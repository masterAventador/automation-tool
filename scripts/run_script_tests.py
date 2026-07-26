#!/usr/bin/env python3
"""Run every `scripts/test_*.py`, with the interpreter pinned.

Why this exists
---------------
`scripts/` holds self-contained test scripts. Some are referenced by no workflow
and no acceptance entrypoint, and `backend/pyproject.toml` sets
`testpaths = ["tests"]`, so pytest never collects them. They only run if a human
types the filename.

One of them, `test_video_studio_acceptance_scope.py`, was failing for real:
VF-06 had stopped covering `plain-language-comprehension.spec.ts`. The guard
whose entire job was to catch that drift had itself drifted out of reach. A
guard nobody executes is not a guard, and its silence is indistinguishable from
success.

Two properties matter more than convenience here:

* **Discovery is derived, never curated.** A hand-maintained registry is how the
  orphans appeared: the file gets written, the registry does not, and it is
  unreachable from birth.
* **The interpreter is pinned.** These scripts do not declare one, and the
  workflows call a bare `python3`. Under macOS's system 3.9 they produce
  failures (`datetime.UTC`, `zip(strict=)`) that are artefacts of the
  interpreter rather than defects. The same suite giving different answers on
  different machines is worse than not running at all.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final

REPOSITORY_ROOT: Final = Path(__file__).resolve().parent.parent
DEFAULT_TIMEOUT_SECONDS: Final = 600

# Scripts that drive real acceptance runs rather than check a property. They
# start Docker, build Apps and take minutes each, so they are not part of this
# suite; they are entrypoints, invoked deliberately.
EXCLUDED: Final = frozenset()


@dataclass(frozen=True)
class ScriptResult:
    name: str
    ok: bool
    seconds: float
    output: str


def discover(repository_root: Path) -> list[Path]:
    """Every test script in `scripts/`, derived from the directory itself."""
    return sorted(
        path
        for path in (repository_root / "scripts").glob("test_*.py")
        if path.name not in EXCLUDED
    )


def venv_interpreter(project_root: Path) -> Path:
    """A venv's interpreter, whose location inside the venv is platform-specific.

    Windows puts it in `Scripts/python.exe`; POSIX puts it in `bin/python`.
    Pinning one spelling makes the runner abort on the other platform before
    executing anything, which reads as a single `FATAL` line rather than as an
    unrun suite.
    """
    if os.name == "nt":
        return project_root / ".venv" / "Scripts" / "python.exe"
    return project_root / ".venv" / "bin" / "python"


def interpreter(repository_root: Path) -> Path:
    """The project interpreter, never the caller's ambient `python3`."""
    return venv_interpreter(repository_root / "backend")


# A script that reaches into a sub-project needs that sub-project's environment.
# Pinning one interpreter for everything is necessary but not sufficient: the
# `browser_use` scripts import a dependency that lives only in
# `tools/browser-use-contract`, and under the backend venv they fail with
# `ModuleNotFoundError` -- a false red of exactly the same family as running
# them under the system 3.9.
SUB_PROJECT_ENVIRONMENTS: Final = (("tools/browser-use-contract", "browser_use"),)


def interpreter_for(script: Path, repository_root: Path) -> Path:
    """Pick the environment a script's imports actually require."""
    source = script.read_text(encoding="utf-8", errors="replace")
    for directory, marker in SUB_PROJECT_ENVIRONMENTS:
        if directory in source or marker in source:
            candidate = venv_interpreter(repository_root / directory)
            if candidate.is_file():
                return candidate
    return interpreter(repository_root)


def run_one(
    script: Path,
    repository_root: Path,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> ScriptResult:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            [str(interpreter_for(script, repository_root)), str(script)],
            cwd=repository_root,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ScriptResult(
            script.name, False, time.monotonic() - started, "timed out"
        )
    return ScriptResult(
        script.name,
        completed.returncode == 0,
        time.monotonic() - started,
        (completed.stdout + completed.stderr).strip(),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    arguments = parser.parse_args()

    python = interpreter(REPOSITORY_ROOT)
    if not python.is_file():
        print(f"the project interpreter is missing: {python}")
        return 1

    scripts = discover(REPOSITORY_ROOT)
    print(f"running {len(scripts)} script tests with {python}")
    results: list[ScriptResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=arguments.jobs) as pool:
        futures = {
            pool.submit(run_one, script, REPOSITORY_ROOT, arguments.timeout): script
            for script in scripts
        }
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())

    results.sort(key=lambda result: result.name)
    failures = [result for result in results if not result.ok]
    for result in results:
        mark = "ok  " if result.ok else "FAIL"
        print(f"{mark} {result.name} ({result.seconds:.1f}s)")
    for result in failures:
        print(f"\n----- {result.name} -----\n{result.output}")
    if failures:
        print(f"\n{len(failures)} of {len(results)} script tests failed")
        return 1
    print(f"\nall {len(results)} script tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
