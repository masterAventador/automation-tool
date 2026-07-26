#!/usr/bin/env python3
"""Tests for the aggregate standalone and deployment test runner.

Some test scripts in this directory are reachable from no workflow and no
acceptance entrypoint, and `backend/pyproject.toml` sets `testpaths=["tests"]` so
pytest does not collect them either. One of them --
`test_video_studio_acceptance_scope.py` -- was failing for real while nobody was
running it. A guard nobody executes is not a guard.

This file is itself discovered by the runner it tests.
"""

from __future__ import annotations

import ast
import io
import os
import re
import sys
import tempfile
import tokenize
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_script_tests  # noqa: E402

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


def _fail(message: str) -> None:
    raise AssertionError(message)


def check_discovery_finds_every_test_script() -> None:
    """Discovery must be derived, never a hand-maintained list.

    A curated list is how the orphans appeared in the first place: the file gets
    written, nobody remembers the registry, and it is unreachable from birth.
    """
    on_disk = {path.name for path in (REPOSITORY_ROOT / "scripts").glob("test_*.py")}
    discovered = {path.name for path in run_script_tests.discover(REPOSITORY_ROOT)}
    missing = on_disk - discovered
    if missing:
        _fail(f"these test scripts would never run: {sorted(missing)}")


def check_discovery_includes_this_file() -> None:
    """The runner must pick up a newly added test with no registration step."""
    discovered = {path.name for path in run_script_tests.discover(REPOSITORY_ROOT)}
    if "test_script_test_runner.py" not in discovered:
        _fail("a new test script was not auto-discovered")


def check_discovery_includes_deploy_tests() -> None:
    """Deployment tests must not sit outside the only aggregate runner."""
    with tempfile.TemporaryDirectory() as scratch:
        repository = Path(scratch)
        script_test = repository / "scripts" / "test_script.py"
        ingress_test = repository / "deploy" / "ingress" / "test_ingress.py"
        cloud_test = repository / "deploy" / "cloud" / "test_cloud.py"
        for test in (script_test, ingress_test, cloud_test):
            test.parent.mkdir(parents=True, exist_ok=True)
            test.write_text("raise SystemExit(0)\n", encoding="utf-8")

        discovered = {
            path.relative_to(repository).as_posix()
            for path in run_script_tests.discover(repository)
        }
        expected = {
            "scripts/test_script.py",
            "deploy/ingress/test_ingress.py",
            "deploy/cloud/test_cloud.py",
        }
        if discovered != expected:
            _fail(
                "aggregate discovery does not cover script and deployment tests: "
                f"expected {sorted(expected)}, got {sorted(discovered)}"
            )


_COUNT_WORD = (
    r"(?:\d+|zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|"
    r"twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|"
    r"twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand)"
)
_MULTIPLE_COUNT_WORD = (
    r"(?:\d+|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|"
    r"thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand)"
)
_COPIED_INVENTORY_PATTERNS = (
    re.compile(
        rf"\b{_COUNT_WORD}\b(?:\s+[\w-]+){{0,3}}\s+(?:scripts?|test\s+files?)\b",
        flags=re.IGNORECASE,
    ),
    re.compile(rf"\b{_MULTIPLE_COUNT_WORD}\s+of\s+them\b", flags=re.IGNORECASE),
    re.compile(
        rf"\b{_COUNT_WORD}\s+false\s+reds?\s+out\s+of\s+{_COUNT_WORD}\b",
        flags=re.IGNORECASE,
    ),
)


def _documentation_prose(path: Path) -> str:
    """Only inspect prose, never runtime strings or derived result output."""
    source = path.read_text(encoding="utf-8")
    module_document = ast.get_docstring(ast.parse(source), clean=False) or ""
    comments = (
        token.string.removeprefix("#").strip()
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT
    )
    return "\n".join((module_document, *comments))


def _copied_inventory_counts(prose: str) -> list[str]:
    prose = prose.replace("`", "")
    return [
        match.group(0)
        for pattern in _COPIED_INVENTORY_PATTERNS
        for match in pattern.finditer(prose)
    ]


def check_runner_documentation_does_not_copy_derived_counts() -> None:
    """Inventory prose must not silently drift from glob-derived discovery."""
    copied_counts: dict[str, list[str]] = {}
    for name in ("run_script_tests.py", "test_script_test_runner.py"):
        matches = _copied_inventory_counts(
            _documentation_prose(REPOSITORY_ROOT / "scripts" / name)
        )
        if matches:
            copied_counts[name] = matches
    if copied_counts:
        _fail(
            "runner documentation copies inventory counts that can silently expire: "
            f"{copied_counts}"
        )
    for stale_example in (
        "There are 42 scripts.",
        "42 orphaned test files are not collected.",
        "eleven `browser_use` scripts need a sub-project environment.",
        "7 false reds out of 14 were observed.",
        "14 of them are not referenced by a workflow.",
    ):
        if not _copied_inventory_counts(stale_example):
            _fail(f"inventory count detector can be bypassed by: {stale_example}")


def check_interpreter_is_pinned_not_inherited() -> None:
    """The interpreter must not be whatever `python3` happens to mean.

    Running these under the system `python3` (3.9 on macOS) produces failures
    like `datetime.UTC` and `zip(strict=)` that are artefacts of the interpreter,
    not defects. The same suite then reports different results on different
    machines, which is worse than not running it.
    """
    interpreter = run_script_tests.interpreter(REPOSITORY_ROOT)
    if interpreter.name == "python3" and "backend" not in interpreter.parts:
        _fail(f"runner would inherit an ambient interpreter: {interpreter}")
    if not str(interpreter).startswith(str(REPOSITORY_ROOT / "backend")):
        _fail(f"runner must use the project venv interpreter, got {interpreter}")


def _fake_interpreters(repository: Path) -> tuple[Path, Path]:
    executable = ("Scripts", "python.exe") if os.name == "nt" else ("bin", "python")
    backend = repository / "backend" / ".venv" / executable[0] / executable[1]
    browser_use = (
        repository
        / "tools"
        / "browser-use-contract"
        / ".venv"
        / executable[0]
        / executable[1]
    )
    for executable in (backend, browser_use):
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.write_text("fixture executable\n", encoding="utf-8")
    return backend, browser_use


def check_sub_project_imports_select_their_environment_independently() -> None:
    """Pinning one interpreter is necessary but not sufficient.

    The `browser_use` scripts import a dependency installed only in
    `tools/browser-use-contract/.venv`. Running them under the backend venv
    yields `ModuleNotFoundError` -- a false red of the same family as running
    everything under the system 3.9.

    The fixture deliberately puts the dependency name in a comment first. This
    proves selection is based on a Python import, not tested with the same
    substring heuristic as the implementation.
    """
    with tempfile.TemporaryDirectory() as scratch:
        repository = Path(scratch)
        backend, browser_use = _fake_interpreters(repository)
        scripts = repository / "scripts"
        scripts.mkdir()

        misleading = scripts / "test_comment_only.py"
        misleading.write_text(
            "# browser_use is discussed here but never imported\nprint('ok')\n",
            encoding="utf-8",
        )
        chosen = run_script_tests.interpreter_for(misleading, repository)
        if chosen != backend:
            _fail(f"a comment selected the sub-project environment: {chosen}")

        real_import = scripts / "test_real_import.py"
        real_import.write_text(
            "from browser_use import Agent\nprint(Agent)\n",
            encoding="utf-8",
        )
        chosen = run_script_tests.interpreter_for(real_import, repository)
        if chosen != browser_use:
            _fail(
                f"an actual browser_use import did not select its environment: {chosen}"
            )


def check_windows_venv_layout_is_supported() -> None:
    """A local gate must resolve the standard Windows venv executable."""
    with tempfile.TemporaryDirectory() as scratch:
        repository = Path(scratch)
        expected = repository / "backend" / ".venv" / "Scripts" / "python.exe"
        try:
            chosen = run_script_tests.interpreter(repository, platform_name="nt")
        except TypeError:
            _fail("interpreter selection has no Windows-platform path")
        if chosen != expected:
            _fail(f"Windows should use {expected}, got {chosen}")


def check_success_requires_countable_execution_evidence() -> None:
    """Exit zero without an executed-check count is silence, not evidence."""
    with tempfile.TemporaryDirectory() as scratch:
        repository = Path(scratch)
        script = repository / "scripts" / "test_silent_success.py"
        script.parent.mkdir(parents=True)
        script.write_text("print('everything is fine')\n", encoding="utf-8")

        result = run_script_tests.run_one(
            script,
            repository,
            project_python=Path(sys.executable),
        )
        if result.ok:
            _fail("a zero exit with no executed-check count was accepted")


def check_child_imports_come_from_the_tree_under_test() -> None:
    """An editable host venv must not mask the checkout's backend source."""
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        repository = root / "checkout"
        package = repository / "backend" / "src" / "gate_fixture"
        package.mkdir(parents=True)
        package.joinpath("__init__.py").write_text(
            "VALUE = 'checkout'\n",
            encoding="utf-8",
        )
        host_package = root / "host-site" / "gate_fixture"
        host_package.mkdir(parents=True)
        host_package.joinpath("__init__.py").write_text(
            "VALUE = 'working-tree'\n",
            encoding="utf-8",
        )
        script = repository / "scripts" / "test_import_origin.py"
        script.parent.mkdir()
        script.write_text(
            "from gate_fixture import VALUE\n"
            "assert VALUE == 'checkout', VALUE\n"
            "print('executed checks: 1')\n",
            encoding="utf-8",
        )

        previous = os.environ.get("PYTHONPATH")
        os.environ["PYTHONPATH"] = os.fspath(root / "host-site")
        try:
            result = run_script_tests.run_one(
                script,
                repository,
                project_python=Path(sys.executable),
            )
        finally:
            if previous is None:
                os.environ.pop("PYTHONPATH", None)
            else:
                os.environ["PYTHONPATH"] = previous
        if not result.ok:
            _fail(
                f"child imported host source instead of checkout source: {result.output}"
            )


def check_unittest_count_is_reported() -> None:
    """The standard unittest summary is countable evidence."""
    with tempfile.TemporaryDirectory() as scratch:
        repository = Path(scratch)
        script = repository / "deploy" / "cloud" / "test_counted.py"
        script.parent.mkdir(parents=True)
        script.write_text(
            "import unittest\n"
            "\n"
            "class TestCount(unittest.TestCase):\n"
            "    def test_one(self):\n"
            "        self.assertTrue(True)\n"
            "\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n",
            encoding="utf-8",
        )

        result = run_script_tests.run_one(
            script,
            repository,
            project_python=Path(sys.executable),
        )
        if not result.ok:
            _fail(f"a counted unittest run failed: {result.output}")
        if getattr(result, "checks", None) != 1:
            _fail(f"expected one executed check, got {getattr(result, 'checks', None)}")


def check_multiple_standard_summaries_are_aggregated() -> None:
    """A script that runs multiple child suites must report their total."""
    output = "Ran 2 tests in 0.01s\nOK\nRan 3 tests in 0.02s\nOK\n"
    count = run_script_tests.reported_check_count(output)
    if count != 5:
        _fail(f"two child suites should report 5 executed tests, got {count}")


def check_incidental_numbers_are_not_execution_evidence() -> None:
    """Only complete summary lines may satisfy the count protocol."""
    output = (
        "debug: fixture text contains (9 checks) but this is not a summary\n"
        "the parser observed 1 passed token in prose\n"
        "debug executed checks: 7\n"
        "executed checks: 5 trailing words\n"
    )
    count = run_script_tests.reported_check_count(output)
    if count != 0:
        _fail(f"incidental prose was accepted as {count} executed checks")


def check_empty_aggregate_fails_closed() -> None:
    """Discovering no test at all must never be reported as success."""
    aggregate_success = getattr(run_script_tests, "aggregate_success", None)
    if aggregate_success is None:
        _fail("the runner has no aggregate-level zero-check guard")
    if aggregate_success([]):
        _fail("an empty test result set was accepted")


CHECKS = (
    check_discovery_finds_every_test_script,
    check_discovery_includes_this_file,
    check_discovery_includes_deploy_tests,
    check_runner_documentation_does_not_copy_derived_counts,
    check_interpreter_is_pinned_not_inherited,
    check_sub_project_imports_select_their_environment_independently,
    check_windows_venv_layout_is_supported,
    check_success_requires_countable_execution_evidence,
    check_child_imports_come_from_the_tree_under_test,
    check_unittest_count_is_reported,
    check_multiple_standard_summaries_are_aggregated,
    check_incidental_numbers_are_not_execution_evidence,
    check_empty_aggregate_fails_closed,
)


def main() -> int:
    failures = 0
    for check in CHECKS:
        try:
            check()
        except AssertionError as error:
            failures += 1
            print(f"FAIL {check.__name__}: {error}")
        else:
            print(f"ok   {check.__name__}")
    if failures:
        return 1
    print(f"script test runner checks passed ({len(CHECKS)} checks)")
    print(f"executed checks: {len(CHECKS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
