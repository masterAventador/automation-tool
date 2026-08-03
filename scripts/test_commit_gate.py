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

import json
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


def check_windows_build_input_uses_an_unprivileged_directory_junction() -> None:
    """Windows must not require Developer Mode just to run the pre-push gate."""
    link_directory = getattr(commit_gate, "_link_directory", None)
    if link_directory is None:
        _fail("the gate has no cross-platform directory-link helper")

    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        target = root / "source" / "node_modules"
        link = root / "checkout" / "frontend" / "node_modules"
        target.mkdir(parents=True)
        link.parent.mkdir(parents=True)
        commands: list[list[str]] = []

        def create_fake_junction(command: list[str], **kwargs: object):
            commands.append(command)
            link.mkdir()
            return subprocess.CompletedProcess(command, 0, "", "")

        link_directory(link, target, platform="nt", runner=create_fake_junction)
        expected = [
            "cmd.exe",
            "/d",
            "/c",
            "mklink",
            "/J",
            str(link),
            str(target),
        ]
        if commands != [expected]:
            _fail(f"Windows build input did not use mklink /J exactly: {commands}")

        calls: list[tuple[Path, Path]] = []
        original_root = commit_gate.REPOSITORY_ROOT
        original_link_directory = commit_gate._link_directory
        try:
            commit_gate.REPOSITORY_ROOT = root / "source"
            commit_gate._link_directory = lambda actual_link, actual_target: (
                calls.append((actual_link, actual_target))
            )
            checkout = root / "another-checkout"
            (checkout / "frontend").mkdir(parents=True)
            commit_gate._link_build_inputs(checkout)
        finally:
            commit_gate.REPOSITORY_ROOT = original_root
            commit_gate._link_directory = original_link_directory
        expected_call = (
            checkout / "frontend" / "node_modules",
            root / "source" / "frontend" / "node_modules",
        )
        if calls != [expected_call]:
            _fail(f"_link_build_inputs bypassed the portable helper: {calls}")


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


def check_python_check_covers_every_tree_backend_config_omits() -> None:
    """`files = ["src","tests"]` leaves three trees unchecked; cover all three.

    Covering only `scripts/` left `tools/` and `workers/` in the same blind spot
    the gate was built to close.
    """
    for tree in ("scripts", "tools", "workers"):
        if tree not in commit_gate.PYTHON_CHECK_TARGETS:
            _fail(f"{tree}/ is checked by nothing and is not in the gate either")


def check_pre_push_gates_the_tip_of_each_pushed_ref() -> None:
    """git hands pre-push its work on stdin; the gate must read it, not guess."""
    stdin = (
        "refs/heads/main aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa "
        "refs/heads/main bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n"
    )
    selected = commit_gate.commits_to_gate(stdin)
    if selected != ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]:
        _fail(f"pre-push should gate the pushed tip, got {selected}")


def check_pre_push_skips_deletions() -> None:
    """Deleting a remote branch pushes the all-zero sha; there is nothing to check."""
    stdin = (
        "(delete) 0000000000000000000000000000000000000000 "
        "refs/heads/gone bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n"
    )
    if commit_gate.commits_to_gate(stdin) != []:
        _fail("a branch deletion has no commit to gate")


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


def check_slow_tier_runs_the_aggregate_script_suite() -> None:
    """The aggregate runner must be reachable from a local commit gate."""
    checks = getattr(commit_gate, "SLOW_CHECKS", ())
    names = {getattr(check, "__name__", "") for check in checks}
    if "run_script_test_check" not in names:
        _fail("the commit gate has no slow-tier aggregate script test check")

    source = Path(commit_gate.__file__).read_text(encoding="utf-8")
    if "--slow" not in source:
        _fail("the local commit gate exposes no --slow entrypoint")


def check_slow_tier_requires_a_positive_visible_count() -> None:
    """The outer gate must preserve the aggregate runner's evidence."""
    parse_count = getattr(commit_gate, "script_test_check_count", None)
    if parse_count is None:
        _fail("the slow tier does not expose the aggregate check count")
    summary = "all 45 script tests passed (412 checks)"
    if parse_count(summary) != 412:
        _fail("the slow tier cannot parse the aggregate runner's count")
    for untrustworthy in (
        "all script tests passed",
        "all 0 script tests passed (0 checks)",
    ):
        if parse_count(untrustworthy) is not None:
            _fail(f"the slow tier accepted uncounted evidence: {untrustworthy}")


def check_slow_tier_materializes_vendor_without_writing_source() -> None:
    """Commit extraction needs read-only vendor content, never source symlinks."""
    materialize = getattr(commit_gate, "_materialize_vendor_sources", None)
    if materialize is None:
        _fail("the slow tier does not materialize vendor sources")

    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        vendor_root = root / "vendor-source"
        checkout = root / "checkout"
        locked_revisions: dict[str, str] = {}
        for name in ("hyperframes", "moneyprinterturbo"):
            source = vendor_root / name
            source.mkdir(parents=True)
            subprocess.run(["git", "init", "--quiet"], cwd=source, check=True)
            (source / "tracked.txt").write_text("read only\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=source, check=True)
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
                cwd=source,
                check=True,
            )
            locked_revisions[name] = subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=source,
                text=True,
            ).strip()
            (source / "tracked.txt").write_text("newer source head\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=source, check=True)
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
                    "newer fixture",
                ],
                cwd=source,
                check=True,
            )

        lock = checkout / "contracts" / "quality" / "third-party-sources.v1.json"
        lock.parent.mkdir(parents=True)
        lock.write_text(
            json.dumps(
                {
                    "sources": [
                        {
                            "path": f"vendor/{name}",
                            "commit": locked_revisions[name],
                        }
                        for name in ("hyperframes", "moneyprinterturbo")
                    ]
                }
            ),
            encoding="utf-8",
        )

        baseline = materialize(checkout, vendor_root=vendor_root)
        changed_vendor_files = getattr(commit_gate, "_changed_vendor_files", None)
        vendor_git_drift = getattr(commit_gate, "_vendor_git_drift", None)
        if not baseline or changed_vendor_files is None or vendor_git_drift is None:
            _fail("slow tier has no post-test snapshot for its isolated vendor")
        isolated_repository = checkout / "vendor" / "moneyprinterturbo"
        if not (isolated_repository / ".git").is_dir():
            _fail(
                "slow-tier vendor copy cannot support tests that require Git metadata"
            )
        isolated_revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=isolated_repository,
            text=True,
        ).strip()
        if isolated_revision != locked_revisions["moneyprinterturbo"]:
            _fail("isolated vendor Git checkout is not at the commit's lock")
        isolated = checkout / "vendor" / "hyperframes" / "tracked.txt"
        if isolated.read_text(encoding="utf-8") != "read only\n":
            _fail("slow tier materialized vendor HEAD instead of the commit's lock")
        isolated.write_text("generated output\n", encoding="utf-8")
        source = vendor_root / "hyperframes" / "tracked.txt"
        if source.read_text(encoding="utf-8") != "newer source head\n":
            _fail("slow-tier vendor materialization writes through to the submodule")
        if isolated.is_symlink():
            _fail("slow-tier vendor materialization must not symlink source files")
        changed = changed_vendor_files(checkout / "vendor", baseline)
        if "hyperframes/tracked.txt" not in changed:
            _fail(f"slow tier missed deliberate isolated-vendor pollution: {changed}")
        git_drift = vendor_git_drift(checkout / "vendor", locked_revisions)
        if "hyperframes" not in git_drift:
            _fail(
                f"slow tier Git guard missed deliberate vendor pollution: {git_drift}"
            )


def check_slow_checkout_preparation_is_isolated_and_reconstructible() -> None:
    """Slow tests need metadata and build products, never host source bytes.

    ``git archive`` deliberately omits ``.git``, ignored virtual environments
    and ``.local`` build products. The slow tier must reconstruct those three
    classes explicitly. In particular, the offline catalog is copied rather
    than linked: a test writing into its disposable input must not mutate the
    developer's cache.
    """
    prepare = getattr(commit_gate, "prepare_slow_checkout", None)
    if prepare is None:
        _fail("the slow tier has no isolated checkout preparation")

    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        source = root / "source"
        checkout = root / "checkout"
        checkout.mkdir()
        (checkout / ".gitignore").write_text(
            ".local/\n**/.venv/\n",
            encoding="utf-8",
        )
        (checkout / "tracked.txt").write_text("commit bytes\n", encoding="utf-8")

        executable = (
            ("Scripts", "python.exe") if sys.platform == "win32" else ("bin", "python")
        )
        for environment in (
            source / "backend" / ".venv",
            source / "tools" / "browser-use-contract" / ".venv",
        ):
            interpreter = environment / executable[0] / executable[1]
            interpreter.parent.mkdir(parents=True)
            interpreter.write_text("runtime only\n", encoding="utf-8")
        cached = source / ".local/offline-motion-deps/catalog"
        cached.mkdir(parents=True)
        (cached / "locked.txt").write_text("digest-pinned\n", encoding="utf-8")
        modules = source / "frontend/node_modules"
        modules.mkdir(parents=True)
        (modules / "runtime.txt").write_text("dependency only\n", encoding="utf-8")
        checkout_modules = checkout / "frontend/node_modules"
        checkout_modules.parent.mkdir(parents=True)
        commit_gate._link_directory(checkout_modules, modules)

        def build_release(tree: Path) -> None:
            generated = tree / ".local/motion-catalog-release/1.0.0"
            generated.mkdir(parents=True)
            (generated / "manifest.json").write_text("{}\n", encoding="utf-8")

        prepare(checkout, source_root=source, build_release=build_release)

        if not (checkout / ".git").is_dir():
            _fail("slow checkout has no disposable Git metadata")
        tracked = subprocess.check_output(
            ["git", "ls-files"],
            cwd=checkout,
            text=True,
        ).splitlines()
        if tracked != [".gitignore", "tracked.txt"]:
            _fail(f"slow checkout snapshot tracked build inputs: {tracked}")
        untracked = subprocess.check_output(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=checkout,
            text=True,
        ).splitlines()
        if "frontend/node_modules" in untracked:
            _fail("slow checkout exposed its Node runtime as release source")
        if not (checkout_modules / "runtime.txt").is_file():
            _fail("slow checkout lost the Node dependency linked by the fast tier")
        if not (checkout / "backend/.venv" / executable[0] / executable[1]).is_file():
            _fail("slow checkout cannot resolve the project interpreter layout")
        copied = checkout / ".local/offline-motion-deps/catalog/locked.txt"
        if not copied.is_file() or copied.is_symlink():
            _fail("slow checkout did not copy its offline catalog input")
        copied.write_text("test mutation\n", encoding="utf-8")
        if (cached / "locked.txt").read_text(encoding="utf-8") != "digest-pinned\n":
            _fail("slow checkout writes through into the developer's build cache")
        if not (
            checkout / ".local/motion-catalog-release/1.0.0/manifest.json"
        ).is_file():
            _fail("slow checkout did not reconstruct the release from committed code")


CHECKS = (
    check_mypy_path_covers_every_static_sys_path_insert,
    check_every_declared_root_exists,
    check_gate_judges_the_commit_not_the_working_tree,
    check_gate_detects_an_injected_typescript_defect,
    check_windows_build_input_uses_an_unprivileged_directory_junction,
    check_gate_detects_an_injected_python_defect,
    check_python_baseline_is_clean_for_blocking_codes,
    check_python_check_covers_every_tree_backend_config_omits,
    check_pre_push_gates_the_tip_of_each_pushed_ref,
    check_pre_push_skips_deletions,
    check_checkout_is_removed_after_use,
    check_slow_tier_runs_the_aggregate_script_suite,
    check_slow_tier_requires_a_positive_visible_count,
    check_slow_tier_materializes_vendor_without_writing_source,
    check_slow_checkout_preparation_is_isolated_and_reconstructible,
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
    print(f"executed checks: {len(CHECKS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
