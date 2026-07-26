#!/usr/bin/env python3
"""Tests for the commit gate.

The gate exists because two defects reached `main` on 2026-07-26 while every
local check passed: an uncommitted fix in the author's working tree masked each
of them. So the properties under test are not "does it run mypy" but:

1. it judges a *commit*, never the working tree;
2. it looks where `scripts/` actually imports from, not just `backend/src`;
3. it can prove it still detects a defect, rather than being trusted to.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import commit_gate  # noqa: E402

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


def _fail(message: str) -> None:
    raise AssertionError(message)


def check_mypy_path_covers_every_static_sys_path_insert() -> None:
    """Every directory `scripts/` inserts must be on MYPYPATH.

    Pointing mypy at `scripts/` while leaving its import roots off MYPYPATH
    produces `import-not-found` on the very modules whose signatures the gate
    is supposed to check, and the call sites then go unverified while the run
    still looks like it did something.
    """
    declared = set(commit_gate.MYPY_PATH_ROOTS)
    discovered = commit_gate.discover_sys_path_roots(REPOSITORY_ROOT)
    missing = discovered - declared
    if missing:
        _fail(
            "these directories are inserted onto sys.path by scripts/ but are "
            f"absent from MYPY_PATH_ROOTS: {sorted(missing)}"
        )


def check_every_declared_root_exists() -> None:
    for root in commit_gate.MYPY_PATH_ROOTS:
        if not (REPOSITORY_ROOT / root).is_dir():
            _fail(f"declared MYPYPATH root does not exist: {root}")


def check_gate_judges_the_commit_not_the_working_tree() -> None:
    """A dirty working tree must not change the verdict.

    This is the exact failure that let `c0cc760` through: the author's local
    check passed because the repair was sitting unstaged next to the break.
    """
    with tempfile.TemporaryDirectory() as scratch:
        marker = REPOSITORY_ROOT / "scripts" / "_commit_gate_probe.py"
        if marker.exists():
            _fail("probe file already exists; a previous run did not clean up")
        marker.write_text("this is not valid python(", encoding="utf-8")
        try:
            checkout = commit_gate.checkout_commit("HEAD", Path(scratch) / "tree")
            leaked = checkout / "scripts" / "_commit_gate_probe.py"
            if leaked.exists():
                _fail(
                    "the gate's checkout contains an uncommitted working-tree "
                    "file, so it is judging the working tree"
                )
        finally:
            marker.unlink(missing_ok=True)


def check_gate_detects_an_injected_typescript_defect() -> None:
    """The gate must fail on the shape of defect that reached main."""
    with tempfile.TemporaryDirectory() as scratch:
        checkout = commit_gate.checkout_commit("HEAD", Path(scratch) / "tree")
        target = checkout / "frontend/src/platform/tauri/publish-workspace-gateway.ts"
        source = target.read_text(encoding="utf-8")
        # Read a property the port does not declare, exactly as c0cc760 did.
        target.write_text(
            source.replace(
                "async beginPublish(request: PublishRequest)",
                "async beginPublish(request: PublishRequest & { q?: never })",
            ).replace(
                "platform: request.platform,",
                "platform: request.platform, stray: request.nonexistentField,",
                1,
            ),
            encoding="utf-8",
        )
        result = commit_gate.run_typescript_check(checkout)
        if result.ok:
            _fail("gate passed a checkout containing an undeclared-property read")


def check_gate_detects_an_injected_python_defect() -> None:
    """A missing required keyword-only argument must be caught.

    `run_bm_05_acceptance.py` calling `lint_composition` without `entry_path`
    is the second defect of the day, and nothing in the repository could see
    it: mypy's `files` never included `scripts/`.
    """
    with tempfile.TemporaryDirectory() as scratch:
        checkout = commit_gate.checkout_commit("HEAD", Path(scratch) / "tree")
        result = commit_gate.verify_gate_detects_known_defect(checkout)
        if not result.ok:
            _fail(f"gate cannot detect the planted defect: {result.output}")


def check_python_baseline_is_clean_for_blocking_codes() -> None:
    """The blocking codes must be empty today, or the gate is unadoptable.

    A gate that is red on arrival gets switched off. Membership in
    `BLOCKING_ERROR_CODES` is therefore a measured property, not a judgement,
    and this check is what keeps it measured.
    """
    with tempfile.TemporaryDirectory() as scratch:
        checkout = commit_gate.checkout_commit("HEAD", Path(scratch) / "tree")
        result = commit_gate.run_python_check(checkout)
        if not result.ok:
            _fail(
                "HEAD already violates a blocking error code, so the gate "
                f"cannot ship as written:\n{result.output}"
            )


def check_checkout_is_removed_after_use() -> None:
    with tempfile.TemporaryDirectory() as scratch:
        destination = Path(scratch) / "tree"
        checkout = commit_gate.checkout_commit("HEAD", destination)
        commit_gate.discard_checkout(checkout)
        if destination.exists():
            _fail("checkout survived discard_checkout")
        registered = subprocess.run(
            ["git", "worktree", "list"],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        if str(destination) in registered:
            _fail("discarded checkout is still a registered git worktree")


CHECKS = (
    check_mypy_path_covers_every_static_sys_path_insert,
    check_every_declared_root_exists,
    check_gate_judges_the_commit_not_the_working_tree,
    check_gate_detects_an_injected_typescript_defect,
    check_gate_detects_an_injected_python_defect,
    check_python_baseline_is_clean_for_blocking_codes,
    check_checkout_is_removed_after_use,
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
        print(f"{failures} commit gate check(s) failed")
        return 1
    print(f"commit gate checks passed ({len(CHECKS)} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
