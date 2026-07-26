#!/usr/bin/env python3
"""Verify a commit, on a tree extracted from that commit.

Why this exists
---------------
On 2026-07-26 two defects reached `main` while every local check passed:

* `c0cc760` changed the publishing port type and left two consumers reading
  removed properties. `main` did not compile for 40 minutes.
* `run_bm_05_acceptance.py` called `lint_composition` without its required
  keyword-only `entry_path`.

Both were invisible locally for the same reason: the author's *working tree*
contained the repair, unstaged, sitting next to the break. Anything that checks
the working tree -- including a pre-commit hook -- checks the masked version.
So this gate never reads the working tree. It extracts the commit with
`git archive` and checks that.

`git archive` rather than `git worktree add` is deliberate: several agents share
this repository, and `git worktree add` mutates shared state under `.git/`.
Extraction only reads.

The second defect also needed `MYPYPATH`. `scripts/` is not a package; its
modules import each other and reach into `tools/` and `workers/` through
`sys.path.insert`. Point mypy at `scripts/` without those roots and every
interesting call resolves to `Any`, the run reports `import-not-found` on the
modules whose signatures matter, and it still looks like it checked something.
`discover_sys_path_roots` re-derives the list from the source so the declared
roots cannot silently fall behind.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final

REPOSITORY_ROOT: Final = Path(__file__).resolve().parent.parent
SCRIPTS_DIRECTORY: Final = REPOSITORY_ROOT / "scripts"

# Every directory `scripts/` puts on sys.path with a statically known value.
# Guarded by `test_commit_gate.check_mypy_path_covers_every_static_sys_path_insert`.
MYPY_PATH_ROOTS: Final = (
    "backend/src",
    "scripts",
    "tools/browser-use-contract",
    "tools/motion-authoring",
    "workers/material_montage",
)

# Roots that only exist while a driver is running, named through the
# environment. A static checker cannot follow them; naming them here keeps the
# hole visible instead of silent.
DYNAMIC_SYS_PATH_MARKERS: Final = (
    "os.environ",
    "harness_dir",
)

_SYS_PATH_LINE = re.compile(r"sys\.path\.(?:insert|append)\(")
_STRING_LITERAL = re.compile(r"""["']([^"']+)["']""")

_BASE_VARIABLES: Final = {
    "REPOSITORY_ROOT": "",
    "ROOT": "",
    "BACKEND_ROOT": "backend",
    "SCRIPTS_ROOT": "scripts",
}


@dataclass(frozen=True)
class CheckResult:
    """One check's verdict plus the output a human needs to act on it."""

    name: str
    ok: bool
    output: str


def discover_sys_path_roots(repository_root: Path) -> set[str]:
    """Re-derive the statically knowable sys.path roots used by `scripts/`."""
    discovered: set[str] = set()
    for source in sorted((repository_root / "scripts").glob("*.py")):
        for line in source.read_text(encoding="utf-8", errors="replace").splitlines():
            if not _SYS_PATH_LINE.search(line):
                continue
            if any(marker in line for marker in DYNAMIC_SYS_PATH_MARKERS):
                continue
            if "Path(__file__)" in line:
                # Every such insert in this directory adds `scripts/` itself.
                discovered.add("scripts")
                continue
            literals = _STRING_LITERAL.findall(line)
            base = next(
                (name for name in _BASE_VARIABLES if re.search(rf"\b{name}\b", line)),
                None,
            )
            if not literals:
                if base is not None and _BASE_VARIABLES[base]:
                    discovered.add(_BASE_VARIABLES[base])
                continue
            for literal in literals:
                prefix = _BASE_VARIABLES.get(base or "", "")
                candidate = f"{prefix}/{literal}" if prefix else literal
                candidate = candidate.strip("/")
                if (repository_root / candidate).is_dir():
                    discovered.add(candidate)
    return discovered


def checkout_commit(commit: str, destination: Path) -> Path:
    """Extract exactly what `commit` contains into `destination`.

    Nothing from the working tree or the index can reach the result: the bytes
    come from the object database.
    """
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".tar", delete=False) as archive:
        archive_path = Path(archive.name)
    try:
        completed = subprocess.run(
            ["git", "archive", "--format=tar", "-o", os.fspath(archive_path), commit],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"git archive failed for {commit}: {completed.stderr}")
        with tarfile.open(archive_path) as bundle:
            bundle.extractall(destination, filter="data")
    finally:
        archive_path.unlink(missing_ok=True)
    return destination


def discard_checkout(checkout: Path) -> None:
    shutil.rmtree(checkout, ignore_errors=True)


def _link_build_inputs(checkout: Path) -> None:
    """Borrow installed dependencies so the gate checks source, not installs.

    `node_modules` is a build product reconstructible from the committed
    lockfile; linking it keeps the gate fast without letting any *source* from
    outside the commit into the tree.
    """
    modules = checkout / "frontend" / "node_modules"
    if not modules.exists():
        modules.symlink_to(REPOSITORY_ROOT / "frontend" / "node_modules")


def run_typescript_check(checkout: Path) -> CheckResult:
    _link_build_inputs(checkout)
    completed = subprocess.run(
        ["npx", "tsc", "-b", "--pretty", "false"],
        cwd=checkout / "frontend",
        capture_output=True,
        text=True,
        check=False,
    )
    return CheckResult(
        name="typescript",
        ok=completed.returncode == 0,
        output=(completed.stdout + completed.stderr).strip(),
    )



# Error codes the gate refuses a commit for. The rule for membership is not
# "serious sounding" but measured: each must be *empty* across `scripts/` today,
# so the gate can be adopted as "must be clean" without a preceding cleanup, and
# each must describe a call site that disagrees with a definition -- the shape
# both 2026-07-26 defects took.
#
# Measured on `scripts/` with MYPYPATH configured: 282 errors total, of which
# `call-arg` is 0. `attr-defined` (79) and `arg-type` (74) are the same *family*
# of defect but are not empty today, so blocking on them would make the gate red
# on arrival and turn adoption into a 150-error refactor that collides with
# every other work line. They are reported, not enforced.
BLOCKING_ERROR_CODES: Final = ("call-arg",)

_ERROR_CODE = re.compile(r"\[(?P<code>[a-z][a-z-]*)\]\s*$")

# The exact defect shape that reached `main`: a required keyword-only argument
# omitted at a call site. Used to prove the gate still works on every run.
_SELF_CHECK_SOURCE: Final = (
    "from automation_tool.executor.motion_authoring.agent import lint_composition\n"
    "\n"
    "def probe() -> None:\n"
    "    lint_composition('<html></html>', allowed_assets=frozenset(),\n"
    "                     max_bytes=1)\n"
)


def _mypy_environment(checkout: Path) -> dict[str, str]:
    """MYPYPATH derived from the checkout, not from this working tree.

    Deriving it from the commit under test is the whole point: if a commit
    moves an import root, the gate must follow the commit rather than whatever
    this machine happens to have on disk.
    """
    environment = dict(os.environ)
    roots = discover_sys_path_roots(checkout) | set(MYPY_PATH_ROOTS)
    environment["MYPYPATH"] = os.pathsep.join(
        os.fspath(checkout / root) for root in sorted(roots) if (checkout / root).is_dir()
    )
    return environment


def _run_mypy(checkout: Path, target: str) -> tuple[bool, str]:
    mypy = REPOSITORY_ROOT / "backend" / ".venv" / "bin" / "mypy"
    if not mypy.is_file():
        return False, f"mypy is not installed at {mypy}"
    completed = subprocess.run(
        [
            os.fspath(mypy),
            "--ignore-missing-imports",
            "--no-error-summary",
            "--follow-imports=silent",
            target,
        ],
        cwd=checkout,
        env=_mypy_environment(checkout),
        capture_output=True,
        text=True,
        check=False,
    )
    return True, (completed.stdout + completed.stderr)


def blocking_errors(report: str) -> list[str]:
    lines = []
    for line in report.splitlines():
        match = _ERROR_CODE.search(line)
        if match and match.group("code") in BLOCKING_ERROR_CODES:
            lines.append(line)
    return lines


def verify_gate_detects_known_defect(checkout: Path) -> CheckResult:
    """Prove the Python check still works, on every run.

    `--ignore-missing-imports` converts an unresolved import into `Any`, and a
    call against `Any` is never wrong, so a broken MYPYPATH does not produce an
    error -- it produces silence that reads exactly like success. The only
    defence is to plant a defect the gate must find and fail loudly if it does
    not.
    """
    probe = checkout / "scripts" / "_commit_gate_self_check.py"
    probe.write_text(_SELF_CHECK_SOURCE, encoding="utf-8")
    try:
        available, report = _run_mypy(checkout, "scripts/_commit_gate_self_check.py")
        if not available:
            return CheckResult("self-check", False, report)
        found = blocking_errors(report)
        if not found:
            return CheckResult(
                "self-check",
                False,
                "the planted missing-argument defect was NOT detected, so the "
                "Python check cannot be trusted on this run (most likely "
                "MYPYPATH no longer resolves the first-party modules).\n"
                f"mypy said:\n{report.strip() or '(no output)'}",
            )
        return CheckResult("self-check", True, found[0])
    finally:
        probe.unlink(missing_ok=True)


def run_python_check(checkout: Path) -> CheckResult:
    """Type-check the halves of the repository mypy's config does not cover.

    `backend/pyproject.toml` sets `files = ["src", "tests"]`, so `scripts/`,
    `tools/` and `workers/` -- where the second defect lived -- are checked by
    nothing at all.
    """
    available, report = _run_mypy(checkout, "scripts")
    if not available:
        return CheckResult("python", False, report)
    blocking = blocking_errors(report)
    total = len([line for line in report.splitlines() if " error: " in line])
    summary = (
        f"{len(blocking)} blocking ({', '.join(BLOCKING_ERROR_CODES)}), "
        f"{total} reported in total"
    )
    return CheckResult(
        name="python",
        ok=not blocking,
        output="\n".join([summary, *blocking]),
    )


FAST_CHECKS: Final = (
    verify_gate_detects_known_defect,
    run_typescript_check,
    run_python_check,
)


def run_fast_tier(commit: str) -> list[CheckResult]:
    with tempfile.TemporaryDirectory(prefix="automation-tool-commit-gate-") as scratch:
        checkout = checkout_commit(commit, Path(scratch) / "tree")
        try:
            return [check(checkout) for check in FAST_CHECKS]
        finally:
            discard_checkout(checkout)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("commit", nargs="?", default="HEAD")
    arguments = parser.parse_args()
    resolved = subprocess.run(
        ["git", "rev-parse", "--short", arguments.commit],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    print(f"commit gate: fast tier on {resolved or arguments.commit}")
    failures = 0
    for result in run_fast_tier(arguments.commit):
        if result.ok:
            print(f"ok   {result.name}")
        else:
            failures += 1
            print(f"FAIL {result.name}")
            print(result.output)
    if failures:
        print(f"{failures} check(s) failed on {resolved}")
        return 1
    print(f"commit gate passed on {resolved}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
