#!/usr/bin/env python3
"""Run the repository's standalone Python tests, with interpreters pinned.

Why this exists
---------------
`scripts/` holds self-contained test scripts. Some are referenced by no workflow
and no acceptance entrypoint, and `backend/pyproject.toml` sets
`testpaths = ["tests"]`, so pytest never collects them. They only run if a human
types the filename.

The deployment assertions under `deploy/` had the same blind spot, so discovery
also derives every `deploy/**/test_*.py` instead of maintaining another list.

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
import ast
import concurrent.futures
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Mapping, Sequence

REPOSITORY_ROOT: Final = Path(__file__).resolve().parent.parent
DEFAULT_TIMEOUT_SECONDS: Final = 600
VENDOR_NAMES: Final = ("hyperframes", "moneyprinterturbo")

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
    checks: int


def discover(repository_root: Path) -> list[Path]:
    """Every standalone script and deployment test, derived from the tree."""
    script_tests = (
        path
        for path in (repository_root / "scripts").glob("test_*.py")
        if path.name not in EXCLUDED
    )
    deployment_tests = (repository_root / "deploy").rglob("test_*.py")
    return sorted((*script_tests, *deployment_tests))


def _venv_interpreter(environment: Path, platform_name: str) -> Path:
    if platform_name == "nt":
        return environment / "Scripts" / "python.exe"
    return environment / "bin" / "python"


def interpreter(repository_root: Path, platform_name: str | None = None) -> Path:
    """The project interpreter, never the caller's ambient `python3`."""
    return _venv_interpreter(
        repository_root / "backend" / ".venv",
        platform_name or os.name,
    )


# A script that reaches into a sub-project needs that sub-project's environment.
# Pinning one interpreter for everything is necessary but not sufficient: the
# `browser_use` scripts import a dependency that lives only in
# `tools/browser-use-contract`, and under the backend venv they fail with
# `ModuleNotFoundError` -- a false red of exactly the same family as running
# them under the system 3.9.
SUB_PROJECT_ENVIRONMENTS: Final = (("tools/browser-use-contract", "browser_use"),)


def _imports(script: Path) -> set[str]:
    """Return imported module names; comments and string literals do not count."""
    try:
        tree = ast.parse(script.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return set()
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def _requires(imports: set[str], marker: str) -> bool:
    return any(
        module == marker
        or module.startswith(f"{marker}.")
        or module.startswith(f"{marker}_")
        for module in imports
    )


def interpreter_for(
    script: Path,
    repository_root: Path,
    *,
    project_python: Path | None = None,
    sub_project_pythons: Mapping[str, Path] | None = None,
    platform_name: str | None = None,
) -> Path:
    """Pick the environment a script's imports actually require."""
    imports = _imports(script)
    for directory, marker in SUB_PROJECT_ENVIRONMENTS:
        if _requires(imports, marker):
            candidate = (
                sub_project_pythons.get(directory)
                if sub_project_pythons and directory in sub_project_pythons
                else _venv_interpreter(
                    repository_root / directory / ".venv",
                    platform_name or os.name,
                )
            )
            if candidate.is_file():
                return candidate
    return project_python or interpreter(repository_root, platform_name)


_UNITTEST_COUNT: Final = re.compile(
    r"^Ran\s+(?P<count>\d+)\s+tests?\s+in\s+[^\r\n]+$",
    flags=re.MULTILINE,
)
_EXPLICIT_COUNT: Final = re.compile(
    r"^executed checks:\s+(?P<count>\d+)$",
    flags=re.MULTILINE,
)
_PYTEST_COUNT: Final = re.compile(
    r"^(?P<count>\d+)\s+passed(?:,\s+[^\r\n]+)?\s+in\s+\d+(?:\.\d+)?s$",
    flags=re.MULTILINE,
)


def reported_check_count(output: str) -> int:
    """Extract only complete standard or script-owned summary lines."""
    explicit = [int(match.group("count")) for match in _EXPLICIT_COUNT.finditer(output)]
    if explicit:
        return max(explicit)
    standard = [
        int(match.group("count"))
        for pattern in (_UNITTEST_COUNT, _PYTEST_COUNT)
        for match in pattern.finditer(output)
    ]
    return sum(standard)


def locked_vendor_revisions(lock_path: Path) -> dict[str, str]:
    """Read the exact submodule revisions required by the tree under test."""
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(
            f"cannot read vendor source lock {lock_path}: {error}"
        ) from error
    sources = lock.get("sources") if isinstance(lock, dict) else None
    if not isinstance(sources, list):
        raise RuntimeError("vendor source lock has no sources list")
    revisions: dict[str, str] = {}
    for source in sources:
        if not isinstance(source, dict):
            continue
        path = source.get("path")
        revision = source.get("commit")
        if (
            isinstance(path, str)
            and isinstance(revision, str)
            and path.startswith("vendor/")
        ):
            name = path.removeprefix("vendor/")
            if name in VENDOR_NAMES and path == f"vendor/{name}":
                revisions[name] = revision
    if set(revisions) != set(VENDOR_NAMES):
        raise RuntimeError("vendor source lock does not pin every required vendor")
    return revisions


def dirty_vendors(
    vendor_root: Path,
    *,
    expected_revisions: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return read-only vendor trees that are absent, invalid, or dirty."""
    dirty: dict[str, str] = {}
    for name in VENDOR_NAMES:
        source = vendor_root / name
        if not (source / ".git").exists():
            dirty[name] = "submodule is not initialized"
            continue
        completed = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=source,
            capture_output=True,
            text=True,
            check=False,
        )
        problems: list[str] = []
        if completed.returncode != 0:
            problems.append(
                completed.stderr.strip()
                or completed.stdout.strip()
                or "git status failed"
            )
        elif completed.stdout.strip():
            problems.append(completed.stdout.strip())
        if expected_revisions is not None:
            expected = expected_revisions.get(name)
            revision = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=source,
                capture_output=True,
                text=True,
                check=False,
            )
            if expected is None:
                problems.append("locked commit is missing")
            elif revision.returncode != 0:
                problems.append(
                    revision.stderr.strip()
                    or revision.stdout.strip()
                    or "cannot read vendor HEAD"
                )
            elif revision.stdout.strip() != expected:
                problems.append(
                    f"HEAD {revision.stdout.strip()} does not match locked commit {expected}"
                )
        if problems:
            dirty[name] = "\n".join(problems)
    return dirty


def _report_dirty_vendors(phase: str, dirty: Mapping[str, str]) -> None:
    print(f"vendor cleanliness check failed {phase}:")
    for name, status in sorted(dirty.items()):
        print(f"  {name}: {status}")


def _child_environment(repository_root: Path) -> dict[str, str]:
    """Put the tree under test ahead of editable working-tree installations."""
    environment = dict(os.environ)
    checkout_source = os.fspath(repository_root / "backend" / "src")
    inherited = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        os.pathsep.join((checkout_source, inherited)) if inherited else checkout_source
    )
    return environment


def run_one(
    script: Path,
    repository_root: Path,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    *,
    project_python: Path | None = None,
    sub_project_pythons: Mapping[str, Path] | None = None,
) -> ScriptResult:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            [
                str(
                    interpreter_for(
                        script,
                        repository_root,
                        project_python=project_python,
                        sub_project_pythons=sub_project_pythons,
                    )
                ),
                str(script),
            ],
            cwd=repository_root,
            env=_child_environment(repository_root),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ScriptResult(
            script.relative_to(repository_root).as_posix(),
            False,
            time.monotonic() - started,
            "timed out",
            0,
        )
    output = (completed.stdout + completed.stderr).strip()
    checks = reported_check_count(output)
    if completed.returncode == 0 and checks == 0:
        output = "\n".join(
            (
                output,
                "runner rejected success: no positive executed-check count was reported",
            )
        ).strip()
    return ScriptResult(
        script.relative_to(repository_root).as_posix(),
        completed.returncode == 0 and checks > 0,
        time.monotonic() - started,
        output,
        checks,
    )


def aggregate_success(results: Sequence[ScriptResult]) -> bool:
    """Require actual positive evidence across a non-empty successful suite."""
    return (
        bool(results)
        and all(result.ok for result in results)
        and sum(result.checks for result in results) > 0
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument(
        "--project-python",
        type=Path,
        help="project interpreter supplied by a commit checkout's host gate",
    )
    parser.add_argument(
        "--browser-use-python",
        type=Path,
        help="browser-use interpreter supplied by a commit checkout's host gate",
    )
    parser.add_argument(
        "--vendor-root",
        type=Path,
        default=REPOSITORY_ROOT / "vendor",
        help="read-only vendor root supplied by a commit checkout's host gate",
    )
    parser.add_argument(
        "--vendor-lock",
        type=Path,
        default=REPOSITORY_ROOT
        / "contracts"
        / "quality"
        / "third-party-sources.v1.json",
        help="source lock belonging to the tree under test",
    )
    arguments = parser.parse_args(argv)

    python = arguments.project_python or interpreter(REPOSITORY_ROOT)
    if not python.is_file():
        print(f"the project interpreter is missing: {python}")
        return 1
    sub_project_pythons = (
        {"tools/browser-use-contract": arguments.browser_use_python}
        if arguments.browser_use_python
        else None
    )
    try:
        expected_revisions = locked_vendor_revisions(arguments.vendor_lock)
    except RuntimeError as error:
        print(error)
        return 1
    dirty_before = dirty_vendors(
        arguments.vendor_root,
        expected_revisions=expected_revisions,
    )
    if dirty_before:
        _report_dirty_vendors("before tests", dirty_before)
        return 1

    scripts = discover(REPOSITORY_ROOT)
    print(f"running {len(scripts)} script tests with {python}")
    results: list[ScriptResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=arguments.jobs) as pool:
        futures = {
            pool.submit(
                run_one,
                script,
                REPOSITORY_ROOT,
                arguments.timeout,
                project_python=python,
                sub_project_pythons=sub_project_pythons,
            ): script
            for script in scripts
        }
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())

    results.sort(key=lambda result: result.name)
    failures = [result for result in results if not result.ok]
    dirty_after = dirty_vendors(
        arguments.vendor_root,
        expected_revisions=expected_revisions,
    )
    for result in results:
        mark = "ok  " if result.ok else "FAIL"
        print(f"{mark} {result.name} ({result.seconds:.1f}s, {result.checks} checks)")
    for result in failures:
        print(f"\n----- {result.name} -----\n{result.output}")
    if dirty_after:
        _report_dirty_vendors("after tests", dirty_after)
    if failures:
        print(f"\n{len(failures)} of {len(results)} script tests failed")
        return 1
    if dirty_after:
        return 1
    if not aggregate_success(results):
        print("\nscript test suite reported zero executed checks")
        return 1
    print(
        f"\nall {len(results)} script tests passed "
        f"({sum(result.checks for result in results)} checks)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
