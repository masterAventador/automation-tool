#!/usr/bin/env python3
"""Tests for the aggregate runner over `scripts/test_*.py`.

14 of the 37 test scripts in this directory are reachable from no workflow and
no acceptance entrypoint, and `backend/pyproject.toml` sets `testpaths=["tests"]`
so pytest does not collect them either. One of them --
`test_video_studio_acceptance_scope.py` -- was failing for real while nobody was
running it. A guard nobody executes is not a guard.

This file is itself discovered by the runner it tests.
"""

from __future__ import annotations

import sys
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


def check_sub_project_scripts_get_their_own_environment() -> None:
    """Pinning one interpreter is necessary but not sufficient.

    The `browser_use` scripts import a dependency installed only in
    `tools/browser-use-contract/.venv`. Running them under the backend venv
    yields `ModuleNotFoundError` -- a false red of the same family as running
    everything under the system 3.9.
    """
    for script in run_script_tests.discover(REPOSITORY_ROOT):
        if "browser_use" not in script.read_text(encoding="utf-8", errors="replace"):
            continue
        chosen = run_script_tests.interpreter_for(script, REPOSITORY_ROOT)
        if "browser-use-contract" not in str(chosen):
            _fail(
                f"{script.name} imports browser_use but would run under "
                f"{chosen}, which does not have it"
            )


CHECKS = (
    check_discovery_finds_every_test_script,
    check_discovery_includes_this_file,
    check_interpreter_is_pinned_not_inherited,
    check_sub_project_scripts_get_their_own_environment,
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
