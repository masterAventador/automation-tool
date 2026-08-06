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
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from run_vendor_tests import extract_archive, materialize_repository

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
        extract_archive(archive_path, destination)
    finally:
        archive_path.unlink(missing_ok=True)
    return destination


def discard_checkout(checkout: Path) -> None:
    shutil.rmtree(checkout, ignore_errors=True)


def _link_directory(
    link: Path,
    target: Path,
    *,
    platform: str = os.name,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> None:
    """Expose one dependency directory without requiring Windows link privilege.

    A directory junction is deliberately used on Windows. Unlike
    ``Path.symlink_to``, ``mklink /J`` works for a normal user without Developer
    Mode, which keeps the pre-push gate runnable on a stock workstation.
    """
    if platform != "nt":
        link.symlink_to(target, target_is_directory=True)
        return

    completed = runner(
        [
            "cmd.exe",
            "/d",
            "/c",
            "mklink",
            "/J",
            os.fspath(link),
            os.fspath(target),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0 or not link.is_dir():
        detail = (completed.stderr or completed.stdout).strip()
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(
            f"could not create the Windows dependency junction "
            f"(exit {completed.returncode}){suffix}"
        )


def _link_build_inputs(checkout: Path) -> None:
    """Borrow installed dependencies so the gate checks source, not installs.

    `node_modules` is a build product reconstructible from the committed
    lockfile; linking it keeps the gate fast without letting any *source* from
    outside the commit into the tree.
    """
    modules = checkout / "frontend" / "node_modules"
    if not modules.exists():
        _link_directory(modules, REPOSITORY_ROOT / "frontend" / "node_modules")


def _run_checkout_command(arguments: list[str], checkout: Path) -> None:
    completed = subprocess.run(
        arguments,
        cwd=checkout,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(
            f"{' '.join(arguments)} failed in the slow checkout"
            + (f": {detail}" if detail else "")
        )


def _initialize_slow_checkout_repository(checkout: Path) -> None:
    """Give archive-only tests disposable Git metadata for the same bytes.

    Several source-transfer tests deliberately ask ``git archive`` or
    ``git ls-files`` what ships. The outer checkout came from ``git archive``
    and therefore has no ``.git`` directory of its own. Initialising a new
    repository *before* adding ignored runtimes and build products preserves
    the exact committed source bytes while making those source-transfer
    questions meaningful inside the disposable tree.
    """
    if (checkout / ".git").exists():
        raise RuntimeError("the slow checkout unexpectedly already contains .git")
    _run_checkout_command(["git", "init", "--quiet"], checkout)
    # The fast tier has already linked ``node_modules`` into this same checkout,
    # and that link must stay usable on disk while staying out of the disposable
    # source snapshot: a snapshot carrying an absolute host link makes the nested
    # checkout self-test (rightly) refuse to extract the archive.
    #
    # Excluded through ``info/exclude`` rather than a ``:(exclude)`` pathspec,
    # and written *before* the add rather than after. The two platforms present
    # the link differently — macOS a symlink, which a directory-shaped
    # ``node_modules/`` rule does not match, and Windows a directory junction,
    # which it does — and naming an already-ignored path in a pathspec is an
    # error rather than a no-op. So the pathspec form worked on one platform and
    # failed the whole slow tier on the other (measured 2026-08-07: `git add`
    # exit 1, "The following paths are ignored ... use -f"). One ignore entry,
    # applied first, is right on both.
    info_exclude = checkout / ".git" / "info" / "exclude"
    existing = info_exclude.read_text(encoding="utf-8")
    marker = "/frontend/node_modules\n"
    if marker not in existing:
        info_exclude.write_text(existing + marker, encoding="utf-8")
    _run_checkout_command(["git", "add", "--all", "--", "."], checkout)
    _run_checkout_command(
        [
            "git",
            "-c",
            "user.name=Commit Gate",
            "-c",
            "user.email=commit-gate@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "isolated committed snapshot",
        ],
        checkout,
    )


def _link_slow_runtime_inputs(checkout: Path, source_root: Path) -> None:
    """Expose installed environments as ignored, reconstructible runtimes."""
    for relative in (
        Path("backend/.venv"),
        Path("tools/browser-use-contract/.venv"),
    ):
        source = source_root / relative
        if not source.is_dir():
            raise RuntimeError(f"slow-tier runtime is missing: {source}")
        destination = checkout / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise RuntimeError(f"slow checkout runtime already exists: {destination}")
        _link_directory(destination, source)


def _require_offline_motion_catalog() -> None:
    """Check the shared build input the slow checkout will read, without copying it.

    This used to copy 46 MiB into the checkout, because the catalog lived in
    `.local` and a link would have exposed the host's copy to writes. It now
    lives in the machine-wide artifact cache, which every checkout resolves to
    identically, and the slow tier only *reads* it: `_build_slow_motion_release`
    runs the release build, whose output goes to `.local` inside the checkout.
    So the copy is gone and the two checks it carried stay — a missing input and
    a link inside the tree are still worth naming here rather than as a failure
    deeper inside the release build.
    """
    from build_offline_motion_catalog import catalog_root

    source = catalog_root()
    if not source.is_dir():
        raise RuntimeError(
            f"slow-tier build input is missing: {source}; "
            "run python3 scripts/build_offline_motion_catalog.py"
        )
    links = [path for path in source.rglob("*") if path.is_symlink()]
    if links:
        raise RuntimeError(
            "slow-tier offline catalog contains a link: "
            f"{links[0].relative_to(source).as_posix()}"
        )


def _build_slow_motion_release(checkout: Path, source_root: Path) -> None:
    """Rebuild the release with the commit's code, never copy the host result."""
    python = _host_interpreter(source_root / "backend" / ".venv")
    completed = subprocess.run(
        [
            os.fspath(python),
            "scripts/build_motion_catalog_release.py",
        ],
        cwd=checkout,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(
            "slow tier could not reconstruct the motion catalog release"
            + (f": {detail}" if detail else "")
        )


def prepare_slow_checkout(
    checkout: Path,
    *,
    source_root: Path = REPOSITORY_ROOT,
    build_release: Callable[[Path], None] | None = None,
) -> None:
    """Prepare only ignored/reconstructible inputs for aggregate script tests."""
    _initialize_slow_checkout_repository(checkout)
    _link_slow_runtime_inputs(checkout, source_root)
    _require_offline_motion_catalog()
    if build_release is None:
        _build_slow_motion_release(checkout, source_root)
    else:
        build_release(checkout)


def _node_tool(name: str) -> str | None:
    """Resolve a Node CLI, which is a `.cmd` shim rather than an `.exe`.

    `CreateProcess` only ever appends `.exe`, so a bare `npx` is not found on
    Windows and the check died with `WinError 2` before running -- a crash
    where a red or green result was expected. `shutil.which` applies PATHEXT
    and returns the shim.
    """
    return shutil.which(name)


def run_typescript_check(checkout: Path) -> CheckResult:
    _link_build_inputs(checkout)
    npx = _node_tool("npx")
    if npx is None:
        return CheckResult(
            name="typescript",
            ok=False,
            output="npx was not found on PATH, so the TypeScript check did not run",
        )
    completed = subprocess.run(
        [npx, "tsc", "-b", "--pretty", "false"],
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
        os.fspath(checkout / root)
        for root in sorted(roots)
        if (checkout / root).is_dir()
    )
    return environment


# The three trees `backend/pyproject.toml`'s `files = ["src", "tests"]` leaves
# unchecked. Measured `call-arg` baselines: scripts 0, tools 0, workers 0 -- so
# extending here keeps the blocking code clean on arrival.
PYTHON_CHECK_TARGETS: Final = ("scripts", "tools", "workers")


def _venv_executable(project_root: Path, name: str) -> Path:
    """A console script inside a venv, whose folder and suffix are per-platform.

    Windows puts these in `Scripts/` with an `.exe` suffix. Deliberately kept
    local rather than shared with `run_script_tests.py`, which resolves the
    same layout: that file is being rewritten on another branch, and four
    duplicated lines cost less than the merge.
    """
    if os.name == "nt":
        return project_root / ".venv" / "Scripts" / f"{name}.exe"
    return project_root / ".venv" / "bin" / name


def _run_mypy(checkout: Path, *targets: str) -> tuple[bool, str]:
    mypy = _venv_executable(REPOSITORY_ROOT / "backend", "mypy")
    if not mypy.is_file():
        return False, f"mypy is not installed at {mypy}"
    completed = subprocess.run(
        [
            os.fspath(mypy),
            "--ignore-missing-imports",
            "--no-error-summary",
            "--follow-imports=silent",
            *targets,
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
    available, report = _run_mypy(checkout, *PYTHON_CHECK_TARGETS)
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


def _host_interpreter(environment: Path) -> Path:
    if os.name == "nt":
        return environment / "Scripts" / "python.exe"
    return environment / "bin" / "python"


VENDOR_NAMES: Final = ("hyperframes", "moneyprinterturbo")
_SCRIPT_TEST_SUMMARY: Final = re.compile(
    r"^all \d+ script tests passed \((?P<count>\d+) checks\)$",
    flags=re.MULTILINE,
)


def script_test_check_count(output: str) -> int | None:
    matches = [
        int(match.group("count")) for match in _SCRIPT_TEST_SUMMARY.finditer(output)
    ]
    if len(matches) != 1 or matches[0] <= 0:
        return None
    return matches[0]


def _locked_vendor_revisions(checkout: Path) -> dict[str, str]:
    lock_path = checkout / "contracts" / "quality" / "third-party-sources.v1.json"
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(
            f"cannot read the commit's vendor source lock: {error}"
        ) from error
    sources = lock.get("sources") if isinstance(lock, dict) else None
    if not isinstance(sources, list):
        raise RuntimeError("the commit's vendor source lock has no sources list")

    revisions: dict[str, str] = {}
    for source in sources:
        if not isinstance(source, dict):
            continue
        path = source.get("path")
        revision = source.get("commit")
        if not isinstance(path, str) or not isinstance(revision, str):
            continue
        prefix = "vendor/"
        name = path.removeprefix(prefix)
        if path != f"{prefix}{name}" or name not in VENDOR_NAMES:
            continue
        revisions[name] = revision
    if set(revisions) != set(VENDOR_NAMES):
        raise RuntimeError(
            "the commit's vendor source lock does not pin every required vendor"
        )
    return revisions


def _vendor_tree_snapshot(vendor_root: Path) -> dict[str, tuple[object, ...]]:
    """Hash materialized vendor files so post-test drift is independently visible."""
    snapshot: dict[str, tuple[object, ...]] = {}
    for path in sorted(vendor_root.rglob("*")):
        relative_path = path.relative_to(vendor_root)
        if ".git" in relative_path.parts:
            continue
        relative = relative_path.as_posix()
        if path.is_symlink():
            snapshot[relative] = ("symlink", os.readlink(path))
            continue
        if not path.is_file():
            continue
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        metadata = path.stat()
        snapshot[relative] = (
            "file",
            metadata.st_size,
            metadata.st_mode & 0o777,
            digest.hexdigest(),
        )
    return snapshot


def _changed_vendor_files(
    vendor_root: Path,
    baseline: dict[str, tuple[object, ...]],
) -> list[str]:
    current = _vendor_tree_snapshot(vendor_root)
    return sorted(
        path
        for path in baseline.keys() | current.keys()
        if baseline.get(path) != current.get(path)
    )


def _vendor_git_drift(
    vendor_root: Path,
    revisions: dict[str, str],
) -> dict[str, str]:
    drift: dict[str, str] = {}
    for name in VENDOR_NAMES:
        source = vendor_root / name
        problems: list[str] = []
        status = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=source,
            capture_output=True,
            text=True,
            check=False,
        )
        if status.returncode != 0:
            problems.append(
                status.stderr.strip() or status.stdout.strip() or "git status failed"
            )
        elif status.stdout.strip():
            problems.append(status.stdout.strip())
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=source,
            capture_output=True,
            text=True,
            check=False,
        )
        if head.returncode != 0:
            problems.append(
                head.stderr.strip() or head.stdout.strip() or "cannot read vendor HEAD"
            )
        elif head.stdout.strip() != revisions[name]:
            problems.append(
                f"HEAD {head.stdout.strip()} does not match locked commit {revisions[name]}"
            )
        if problems:
            drift[name] = "\n".join(problems)
    return drift


def _materialize_vendor_sources(
    checkout: Path,
    *,
    vendor_root: Path = REPOSITORY_ROOT / "vendor",
) -> dict[str, tuple[object, ...]]:
    """Clone locked vendor revisions into the disposable commit checkout."""
    revisions = _locked_vendor_revisions(checkout)
    for name in VENDOR_NAMES:
        source = vendor_root / name
        status = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=source,
            capture_output=True,
            text=True,
            check=False,
        )
        if status.returncode != 0:
            detail = status.stderr.strip() or status.stdout.strip()
            raise RuntimeError(f"cannot inspect vendor/{name}: {detail}")
        if status.stdout.strip():
            raise RuntimeError(
                f"vendor/{name} is dirty before slow-tier tests:\n"
                f"{status.stdout.strip()}"
            )

        materialize_repository(
            source,
            checkout / "vendor" / name,
            revisions[name],
        )
    return _vendor_tree_snapshot(checkout / "vendor")


def run_script_test_check(checkout: Path) -> CheckResult:
    """Run every standalone Python test from the commit under inspection."""
    try:
        prepare_slow_checkout(checkout)
        vendor_baseline = _materialize_vendor_sources(checkout)
        vendor_revisions = _locked_vendor_revisions(checkout)
    except (OSError, RuntimeError) as error:
        return CheckResult("script-tests", False, str(error))
    python = _host_interpreter(REPOSITORY_ROOT / "backend" / ".venv")
    browser_use_python = _host_interpreter(
        REPOSITORY_ROOT / "tools" / "browser-use-contract" / ".venv"
    )
    command = [
        os.fspath(python),
        os.fspath(checkout / "scripts" / "run_script_tests.py"),
        "--project-python",
        os.fspath(python),
        "--vendor-root",
        os.fspath(REPOSITORY_ROOT / "vendor"),
        "--vendor-lock",
        os.fspath(checkout / "contracts" / "quality" / "third-party-sources.v1.json"),
    ]
    if browser_use_python.is_file():
        command.extend(("--browser-use-python", os.fspath(browser_use_python)))
    completed = subprocess.run(
        command,
        cwd=checkout,
        capture_output=True,
        text=True,
        check=False,
    )
    output = (completed.stdout + completed.stderr).strip()
    executed_checks = script_test_check_count(output)
    try:
        vendor_changes = _changed_vendor_files(
            checkout / "vendor",
            vendor_baseline,
        )
        vendor_git_drift = _vendor_git_drift(
            checkout / "vendor",
            vendor_revisions,
        )
        vendor_changes.extend(
            f"{name}: {detail}" for name, detail in sorted(vendor_git_drift.items())
        )
    except OSError as error:
        vendor_changes = [f"vendor snapshot failed: {error}"]
    if completed.returncode == 0 and executed_checks is None:
        output = "\n".join(
            (
                output,
                "aggregate script tests did not report one positive check-count summary",
            )
        ).strip()
    if vendor_changes:
        detail = "\n".join(f"  {path}" for path in vendor_changes[:20])
        output = "\n".join(
            (
                output,
                "isolated vendor source changed during script tests:",
                detail,
            )
        ).strip()
    return CheckResult(
        name=(
            f"script-tests ({executed_checks} checks)"
            if executed_checks is not None
            else "script-tests"
        ),
        ok=(
            completed.returncode == 0
            and executed_checks is not None
            and not vendor_changes
        ),
        output=output,
    )


FAST_CHECKS: Final = (
    verify_gate_detects_known_defect,
    run_typescript_check,
    run_python_check,
)

SLOW_CHECKS: Final = (run_script_test_check,)


def run_tier(commit: str, checks: tuple) -> list[CheckResult]:
    with tempfile.TemporaryDirectory(prefix="automation-tool-commit-gate-") as scratch:
        checkout = checkout_commit(commit, Path(scratch) / "tree")
        try:
            return [check(checkout) for check in checks]
        finally:
            discard_checkout(checkout)


def run_fast_tier(commit: str) -> list[CheckResult]:
    return run_tier(commit, FAST_CHECKS)


def run_slow_tier(commit: str) -> list[CheckResult]:
    return run_tier(commit, FAST_CHECKS + SLOW_CHECKS)


_NULL_SHA: Final = "0" * 40


def commits_to_gate(stdin_text: str) -> list[str]:
    """Pick the commits to check out of git's pre-push protocol.

    git feeds `<local ref> <local sha> <remote ref> <remote sha>` per ref. The
    local sha is the tip about to exist on the remote, which is exactly what
    must be buildable. A deletion carries the all-zero sha and has nothing to
    check.
    """
    commits: list[str] = []
    for line in stdin_text.splitlines():
        fields = line.split()
        if len(fields) != 4:
            continue
        local_sha = fields[1]
        if local_sha == _NULL_SHA:
            continue
        if local_sha not in commits:
            commits.append(local_sha)
    return commits


def _report(commit: str, results: list[CheckResult]) -> int:
    failures = 0
    for result in results:
        if result.ok:
            print(f"ok   {result.name}")
        else:
            failures += 1
            print(f"FAIL {result.name}")
            print(result.output)
    if failures:
        print(f"{failures} check(s) failed on {commit}")
    else:
        print(f"commit gate passed on {commit}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("commit", nargs="?", default="HEAD")
    parser.add_argument(
        "--pre-push",
        action="store_true",
        help="read git's pre-push protocol on stdin and gate each pushed tip",
    )
    parser.add_argument(
        "--slow",
        action="store_true",
        help="also run the aggregate standalone and deployment test suite",
    )
    arguments = parser.parse_args()

    if arguments.pre_push:
        commits = commits_to_gate(sys.stdin.read())
        if not commits:
            print("commit gate: nothing to check")
            return 0
        failures = 0
        for commit in commits:
            print(f"commit gate: fast tier on {commit[:7]}")
            failures += _report(commit[:7], run_fast_tier(commit))
        return 1 if failures else 0

    resolved = subprocess.run(
        ["git", "rev-parse", "--short", arguments.commit],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    tier = "slow" if arguments.slow else "fast"
    run = run_slow_tier if arguments.slow else run_fast_tier
    print(f"commit gate: {tier} tier on {resolved or arguments.commit}")
    return 1 if _report(resolved, run(arguments.commit)) else 0


if __name__ == "__main__":
    raise SystemExit(main())
