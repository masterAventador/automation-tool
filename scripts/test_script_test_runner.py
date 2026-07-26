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
import importlib.util
import io
import os
import re
import subprocess
import sys
import tarfile
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
        matches = _copied_inventory_counts(_documentation_prose(REPOSITORY_ROOT / "scripts" / name))
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
        repository / "tools" / "browser-use-contract" / ".venv" / executable[0] / executable[1]
    )
    for executable in (backend, browser_use):
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.write_text("fixture executable\n", encoding="utf-8")
    return backend, browser_use


def check_interpreter_layout_matches_this_platform() -> None:
    """A venv puts its interpreter in a different place on Windows.

    Pinning `bin/python` unconditionally means the runner aborts on Windows
    before executing a single script, and reports that as one tidy `FATAL`
    line rather than as a suite nobody ran -- the same shape as the orphans
    this file exists to catch.
    """
    interpreter = run_script_tests.interpreter(REPOSITORY_ROOT)
    if not interpreter.is_file():
        _fail(f"pinned interpreter does not exist on this platform: {interpreter}")


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
            _fail(f"an actual browser_use import did not select its environment: {chosen}")


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
            _fail(f"child imported host source instead of checkout source: {result.output}")


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


def _initialize_vendor_fixture(path: Path) -> str:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet"], cwd=path, check=True)
    (path / "tracked.txt").write_text("read only\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Vendor Fixture",
            "-c",
            "user.email=vendor-fixture@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "fixture",
        ],
        cwd=path,
        check=True,
    )
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        text=True,
    ).strip()


def check_vendor_cleanliness_guard_self_proves() -> None:
    """The post-test guard must detect deliberate pollution in a fake vendor."""
    dirty_vendors = getattr(run_script_tests, "dirty_vendors", None)
    if dirty_vendors is None:
        _fail("the aggregate runner has no vendor cleanliness guard")

    with tempfile.TemporaryDirectory() as scratch:
        vendor_root = Path(scratch) / "vendor"
        expected_revisions: dict[str, str] = {}
        for name in ("hyperframes", "moneyprinterturbo"):
            expected_revisions[name] = _initialize_vendor_fixture(vendor_root / name)
        try:
            clean = dirty_vendors(
                vendor_root,
                expected_revisions=expected_revisions,
            )
        except TypeError:
            _fail("vendor cleanliness does not validate locked revisions")
        if clean:
            _fail("clean vendor fixtures were reported dirty")

        pollution = vendor_root / "hyperframes" / "deliberate-pollution.txt"
        pollution.write_text("the guard must catch this\n", encoding="utf-8")
        dirty = dirty_vendors(
            vendor_root,
            expected_revisions=expected_revisions,
        )
        if "hyperframes" not in dirty or "deliberate-pollution.txt" not in dirty["hyperframes"]:
            _fail(f"the deliberate vendor pollution was not detected: {dirty}")
        pollution.unlink()

        switched = vendor_root / "moneyprinterturbo"
        switched.joinpath("tracked.txt").write_text("different clean head\n", encoding="utf-8")
        subprocess.run(["git", "add", "tracked.txt"], cwd=switched, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Vendor Fixture",
                "-c",
                "user.email=vendor-fixture@example.invalid",
                "commit",
                "--quiet",
                "-m",
                "wrong clean revision",
            ],
            cwd=switched,
            check=True,
        )
        dirty = dirty_vendors(
            vendor_root,
            expected_revisions=expected_revisions,
        )
        if "moneyprinterturbo" not in dirty or "locked commit" not in dirty["moneyprinterturbo"]:
            _fail(f"a clean checkout at the wrong revision was not detected: {dirty}")


def check_vendor_tests_run_from_local_isolation() -> None:
    """Upstream tests must mutate an isolated `.local/` clone, never vendor."""
    entrypoint = REPOSITORY_ROOT / "scripts" / "run_vendor_tests.py"
    if not entrypoint.is_file():
        _fail("scripts/run_vendor_tests.py is missing")
    specification = importlib.util.spec_from_file_location("run_vendor_tests", entrypoint)
    if specification is None or specification.loader is None:
        _fail("cannot load the isolated vendor test entrypoint")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)

    with tempfile.TemporaryDirectory() as scratch:
        repository = Path(scratch)
        source = repository / "vendor" / "hyperframes"
        local_root = repository / ".local" / "vendor-tests"
        source_revision = _initialize_vendor_fixture(source)
        try:
            completed = module.run_in_isolation(
                source,
                local_root,
                [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; "
                    "Path('tracked.txt').write_text('changed\\n'); "
                    "Path('output/compiled.html').parent.mkdir(parents=True); "
                    "Path('output/compiled.html').write_text('generated\\n'); "
                    "print(Path.cwd())",
                ],
                expected_revision=source_revision,
            )
        except TypeError:
            _fail("isolated vendor tests do not validate the locked revision")
        if completed.returncode != 0:
            _fail(f"isolated fixture command failed: {completed.stderr}")
        if source.joinpath("tracked.txt").read_text(encoding="utf-8") != "read only\n":
            _fail("isolated test changed the tracked vendor source")
        if source.joinpath("output/compiled.html").exists():
            _fail("isolated test created output in vendor source")
        reported_cwd = Path(completed.stdout.strip())
        if local_root.resolve() not in reported_cwd.resolve().parents:
            _fail(f"vendor test ran outside .local isolation: {reported_cwd}")

        try:
            module.run_in_isolation(
                source,
                local_root,
                [sys.executable, "-c", "raise SystemExit(0)"],
                expected_revision="0" * 40,
            )
        except RuntimeError as error:
            if "does not match locked commit" not in str(error):
                _fail(f"unexpected locked-revision failure: {error}")
        else:
            _fail("isolated entrypoint accepted a clean but unlocked vendor revision")

        pollution = source / "deliberate-pollution.txt"
        try:
            module.run_in_isolation(
                source,
                local_root,
                [
                    sys.executable,
                    "-c",
                    f"from pathlib import Path; Path({str(pollution)!r}).write_text('polluted\\n')",
                ],
                expected_revision=source_revision,
            )
        except RuntimeError as error:
            if "changed during isolated test" not in str(error):
                _fail(f"unexpected post-test vendor failure: {error}")
        else:
            _fail("isolated entrypoint did not detect an absolute-path vendor write")
        finally:
            pollution.unlink(missing_ok=True)


def check_windows_archive_fallback_does_not_create_symlinks() -> None:
    """Windows isolation must work without Developer Mode symlink privileges."""
    entrypoint = REPOSITORY_ROOT / "scripts" / "run_vendor_tests.py"
    specification = importlib.util.spec_from_file_location(
        "run_vendor_tests_windows_archive",
        entrypoint,
    )
    if specification is None or specification.loader is None:
        _fail("cannot load the isolated vendor test entrypoint")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    extract_archive = getattr(module, "extract_archive", None)
    if extract_archive is None:
        _fail("vendor archive has no Windows symlink fallback")

    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        archive = root / "fixture.tar"
        with tarfile.open(archive, "w") as bundle:
            link = tarfile.TarInfo("nested/shared")
            link.type = tarfile.SYMTYPE
            link.linkname = "../shared"
            bundle.addfile(link)
        destination = root / "extracted"
        extract_archive(archive, destination, platform_name="nt")
        materialized = destination / "nested" / "shared"
        if materialized.is_symlink() or not materialized.is_file():
            _fail("Windows fallback attempted to create a real symlink")
        if materialized.read_text(encoding="utf-8") != "../shared":
            _fail("Windows fallback did not preserve the symlink target")


CHECKS = (
    check_discovery_finds_every_test_script,
    check_discovery_includes_this_file,
    check_discovery_includes_deploy_tests,
    check_runner_documentation_does_not_copy_derived_counts,
    check_interpreter_is_pinned_not_inherited,
    check_interpreter_layout_matches_this_platform,
    check_sub_project_imports_select_their_environment_independently,
    check_windows_venv_layout_is_supported,
    check_success_requires_countable_execution_evidence,
    check_child_imports_come_from_the_tree_under_test,
    check_unittest_count_is_reported,
    check_multiple_standard_summaries_are_aggregated,
    check_incidental_numbers_are_not_execution_evidence,
    check_empty_aggregate_fails_closed,
    check_vendor_cleanliness_guard_self_proves,
    check_vendor_tests_run_from_local_isolation,
    check_windows_archive_fallback_does_not_create_symlinks,
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
