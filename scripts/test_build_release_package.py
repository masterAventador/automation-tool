#!/usr/bin/env python3
"""Tests for the one command that produces a distributable package.

These run the real `hdiutil` against inputs it must reject. Nothing here
notarises, signs or builds a bundle — those need Apple and forty minutes.
"""

from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
import sys
import tempfile
import tokenize
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_release_package  # noqa: E402
from build_release_package import attach_command  # noqa: E402
from build_release_package import embed_release_identity  # noqa: E402
from release_identity import SourceFacts  # noqa: E402


class SnapshotBuildDependencyTests(unittest.TestCase):
    def test_node_modules_reaches_the_snapshot_without_exposing_the_operators_copy(
        self,
    ) -> None:
        """出包不得让 pnpm 写到操作者那份 node_modules 上。

        2026-08-04 实测：`frontend/node_modules` 是软链进快照的，pnpm 在快照上下文里
        打出 `Recreating /…/frontend/node_modules` 并重建了**真实那份**；快照跑完即删，
        于是 `node_modules/vitest` 这类指向 `.pnpm/…` 的相对软链失效，出包后
        `npx vitest` 报 `Cannot find module …/vitest/vitest.mjs`。

        **代价不在于要重装，而在于报错完全不指向出包**：一个刚跑完发布流程的人，
        看到的是测试框架找不到自己，没有任何线索指回半小时前那条命令。

        判据是隔离而不是形态：快照里那份必须是独立目录，往它里面写不得影响操作者那份。
        """
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            operator = root / "checkout"
            (operator / "frontend/node_modules/.pnpm").mkdir(parents=True)
            (operator / "frontend/node_modules/marker").write_text("原始", encoding="utf-8")
            snapshot = root / "snapshot"
            (snapshot / ".git/info").mkdir(parents=True)
            (snapshot / ".git/info/exclude").write_text("", encoding="utf-8")

            with mock.patch.object(build_release_package, "REPOSITORY_ROOT", operator):
                build_release_package._link_snapshot_build_dependency(
                    snapshot, Path("frontend/node_modules")
                )

            copied = snapshot / "frontend/node_modules"
            self.assertTrue(copied.is_dir())
            self.assertFalse(
                copied.is_symlink(),
                "快照里的 node_modules 不能是指向操作者那份的软链——"
                "pnpm 会穿过它重建真实目录",
            )
            self.assertEqual((copied / "marker").read_text(encoding="utf-8"), "原始")

            # 构建过程写快照那份，操作者那份必须原样不动。
            (copied / "marker").write_text("被构建改过", encoding="utf-8")
            (copied / "新增").write_text("", encoding="utf-8")
            self.assertEqual(
                (operator / "frontend/node_modules/marker").read_text(encoding="utf-8"),
                "原始",
            )
            self.assertFalse((operator / "frontend/node_modules/新增").exists())


class SignedReleaseIdentityTests(unittest.TestCase):
    def test_pre_set_snapshot_environment_cannot_bypass_materialization(self) -> None:
        source_facts = SourceFacts(git_commit="a" * 40, tree_sha256="b" * 64)
        environment = {
            build_release_package.SOURCE_SNAPSHOT_ENVIRONMENT: os.fspath(ROOT),
            build_release_package.SOURCE_SNAPSHOT_IDENTITY_ENVIRONMENT: source_facts.tree_sha256,
        }
        with (
            mock.patch.dict(os.environ, environment, clear=False),
            mock.patch.object(
                build_release_package,
                "repository_source_facts",
                return_value=source_facts,
            ),
        ):
            os.environ.pop(
                "AUTOMATION_TOOL_RELEASE_SOURCE_CAPABILITY_FD",
                None,
            )
            with self.assertRaisesRegex(
                build_release_package.ReleaseFailed,
                "capability",
            ):
                build_release_package.require_materialized_source_snapshot(
                    ROOT / ".local" / "release-work"
                )

    def test_snapshot_capability_is_parent_bound_and_consumed_once(self) -> None:
        source_facts = SourceFacts(git_commit="a" * 40, tree_sha256="b" * 64)
        capability_name = "AUTOMATION_TOOL_RELEASE_SOURCE_CAPABILITY_FD"
        read_descriptor, write_descriptor = os.pipe()
        payload = b"\0".join(
            (
                b"automation-tool.release-source-snapshot.v1",
                str(os.getppid()).encode("ascii"),
                os.fsencode(ROOT),
                source_facts.tree_sha256.encode("ascii") + b"\n",
            )
        )
        os.write(write_descriptor, payload)
        os.close(write_descriptor)
        try:
            with (
                mock.patch.dict(
                    os.environ,
                    {capability_name: str(read_descriptor)},
                    clear=False,
                ),
                mock.patch.object(
                    build_release_package,
                    "repository_source_facts",
                    return_value=source_facts,
                ),
            ):
                os.environ.pop(build_release_package.SOURCE_SNAPSHOT_ENVIRONMENT, None)
                os.environ.pop(
                    build_release_package.SOURCE_SNAPSHOT_IDENTITY_ENVIRONMENT,
                    None,
                )
                with mock.patch.object(
                    build_release_package,
                    "require_snapshot_repository_layout",
                ):
                    build_release_package.require_materialized_source_snapshot(
                        ROOT / ".local" / "release-work"
                    )
                self.assertNotIn(capability_name, os.environ)
            with self.assertRaises(OSError):
                os.fstat(read_descriptor)
        finally:
            try:
                os.close(read_descriptor)
            except OSError:
                pass

    def test_snapshot_capability_cannot_bless_the_ordinary_checkout(self) -> None:
        source_facts = build_release_package.repository_source_facts(ROOT)
        capability_name = build_release_package.SOURCE_SNAPSHOT_CAPABILITY_ENVIRONMENT
        read_descriptor, write_descriptor = os.pipe()
        payload = b"\0".join(
            (
                build_release_package.SOURCE_SNAPSHOT_CAPABILITY_MAGIC,
                str(os.getppid()).encode("ascii"),
                os.fsencode(ROOT),
                source_facts.tree_sha256.encode("ascii") + b"\n",
            )
        )
        os.write(write_descriptor, payload)
        os.close(write_descriptor)
        try:
            with mock.patch.dict(
                os.environ,
                {capability_name: str(read_descriptor)},
                clear=False,
            ):
                with self.assertRaisesRegex(
                    build_release_package.ReleaseFailed,
                    "snapshot layout",
                ):
                    build_release_package.require_materialized_source_snapshot(
                        ROOT / ".local" / "release-work"
                    )
        finally:
            try:
                os.close(read_descriptor)
            except OSError:
                pass

    def test_snapshot_restart_preserves_the_callers_relative_path_base(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            caller = base / "frontend"
            caller.mkdir()
            work_directory = base / "release-work"
            source_facts = SourceFacts(git_commit="a" * 40, tree_sha256="b" * 64)
            child: subprocess.CompletedProcess[str] = subprocess.CompletedProcess(
                args=[],
                returncode=0,
            )

            with (
                mock.patch.object(Path, "cwd", return_value=caller),
                mock.patch.object(
                    build_release_package,
                    "require_source_stable_work_directory",
                    return_value=work_directory,
                ),
                mock.patch.object(
                    build_release_package,
                    "repository_source_facts",
                    return_value=source_facts,
                ),
                mock.patch.object(
                    build_release_package,
                    "materialize_repository_snapshot",
                ),
                mock.patch.object(
                    build_release_package,
                    "_link_snapshot_build_dependency",
                ),
                mock.patch.object(
                    build_release_package.subprocess,
                    "run",
                    return_value=child,
                ) as run,
                mock.patch.object(sys, "argv", ["build_release_package.py", "--work-dir", "../out"]),
            ):
                result = build_release_package.run_from_materialized_source_snapshot(
                    argparse.Namespace(work_dir=work_directory)
                )

            self.assertEqual(result, 0)
            self.assertEqual(run.call_args.kwargs["cwd"], caller.resolve())

    def test_linked_build_dependency_does_not_change_snapshot_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "root"
            snapshot = base / "snapshot"
            root.mkdir()
            subprocess.run(["git", "init", "-q", root], check=True)
            (root / ".gitignore").write_text(".local/\n", encoding="utf-8")
            (root / "tracked.txt").write_text("reviewed\n", encoding="utf-8")
            subprocess.run(["git", "-C", root, "add", "."], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    root,
                    "-c",
                    "user.name=Release Test",
                    "-c",
                    "user.email=release@example.invalid",
                    "commit",
                    "-qm",
                    "snapshot fixture",
                ],
                check=True,
            )
            dependency = root / ".local"
            dependency.mkdir()
            (dependency / "cache.bin").write_bytes(b"build-only")
            subprocess.run(
                ["git", "clone", "--quiet", os.fspath(root), os.fspath(snapshot)],
                check=True,
            )
            expected = build_release_package.repository_source_facts(root)

            with mock.patch.object(build_release_package, "REPOSITORY_ROOT", root):
                build_release_package._link_snapshot_build_dependency(
                    snapshot,
                    Path(".local"),
                )

            self.assertEqual(
                build_release_package.repository_source_facts(snapshot),
                expected,
            )

    def test_release_rejects_a_repository_work_directory_that_changes_the_source_snapshot(
        self,
    ) -> None:
        unsafe = ROOT / "frontend" / "release-work"
        safe = ROOT / ".local" / "release-work"

        with self.assertRaises(build_release_package.ReleaseFailed):
            build_release_package.require_source_stable_work_directory(unsafe)
        self.assertEqual(
            build_release_package.require_source_stable_work_directory(safe),
            safe.resolve(),
        )

        source = Path(build_release_package.__file__).read_text(encoding="utf-8")
        start = source.index("def build_macos_release")
        work_directory_gate = source.index("require_source_stable_work_directory(", start)
        source_snapshot = source.index("repository_source_facts(REPOSITORY_ROOT)", start)
        self.assertLess(work_directory_gate, source_snapshot)

    def test_release_reuses_the_locked_third_party_source_gate_before_identity(self) -> None:
        source = Path(build_release_package.__file__).read_text(encoding="utf-8")
        start = source.index("def build_macos_release")
        gate = source.index("check_third_party_sources.py", start)
        identity = source.index("repository_source_facts(REPOSITORY_ROOT)", start)
        self.assertLess(gate, identity)

    def test_release_entry_restarts_the_build_from_the_materialized_snapshot(self) -> None:
        source = Path(build_release_package.__file__).read_text(encoding="utf-8")
        main = source.index("def main()")
        snapshot = source.index("run_from_materialized_source_snapshot(", main)
        build = source.index("build_macos_release(", main)
        self.assertLess(snapshot, build)

    def test_release_identity_is_embedded_before_the_outer_app_seal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / "Product.app"
            contents = app / "Contents"
            contents.mkdir(parents=True)
            plist_path = contents / "Info.plist"
            with plist_path.open("wb") as target:
                plistlib.dump(
                    {
                        "CFBundleIdentifier": "com.aventador.automationtool",
                        "CFBundleShortVersionString": "0.1.0",
                    },
                    target,
                )
            plist_path.chmod(0o644)

            real_chmod = os.chmod

            def windows_312_chmod(
                path: str | os.PathLike[str],
                mode: int,
                **options: object,
            ) -> None:
                if "follow_symlinks" in options:
                    raise NotImplementedError(
                        "follow_symlinks is unavailable on Windows 3.12"
                    )
                real_chmod(path, mode)

            with mock.patch.object(os, "chmod", side_effect=windows_312_chmod):
                embed_release_identity(
                    application=app,
                    source=SourceFacts(git_commit="a" * 40, tree_sha256="b" * 64),
                    build_id="eb11-current",
                    target_id="macos-arm64",
                    architecture="aarch64",
                    deployment_profile_id="demo-xuanbai",
                )

            with plist_path.open("rb") as plist_source:
                identity = plistlib.load(plist_source)["AutomationToolReleaseIdentity"]
            self.assertEqual(plist_path.stat().st_mode & 0o777, 0o644)
            self.assertEqual(
                identity,
                {
                    "architecture": "aarch64",
                    "buildId": "eb11-current",
                    "deploymentProfileId": "demo-xuanbai",
                    "schema": "automation-tool.release-identity.v1",
                    "sourceGitCommit": "a" * 40,
                    "sourceTreeSha256": "b" * 64,
                    "target": "macos-arm64",
                },
            )

        source_code = Path(build_release_package.__file__).read_text(encoding="utf-8")
        embedded = source_code.index(
            "embed_release_identity(",
            source_code.index("def build_macos_release"),
        )
        sealed = source_code.index("install_runtime_resources_and_sign(", embedded)
        self.assertLess(embedded, sealed)


class TheAppIsNotCopiedByHdiutil(unittest.TestCase):
    """`hdiutil create -srcfolder` cannot carry this application bundle.

    Measured 2026-07-27 on the release machine, with the real signed and
    notarised bundle, four ways:

    | staged payload                       | result |
    |--------------------------------------|--------|
    | `自动化运营工具.app` (staging dir)   | EPERM  |
    | the same bundle, renamed `payload`   | ok     |
    | a synthetic `Probe.app` stub         | ok     |
    | the bundle handed to `-srcfolder`    | EPERM  |
    |   directly (the pre-T84 form)        |        |

    So it is neither the name nor the size: it is a *genuine* application
    bundle, and it fails for the old form too — this is not a regression T84
    introduced. `ditto` in this process copies the same bundle onto the same
    mounted volume without complaint, so the refusal belongs to the helper
    hdiutil delegates its copying to, not to us.

    The consequence is blunt: as long as the payload goes through
    `-srcfolder`, this machine cannot produce a package at all.
    """

    def test_the_disk_image_step_does_not_delegate_the_copy_to_hdiutil(self) -> None:
        # Code only. The comments and docstrings around the replacement say
        # `-srcfolder` on purpose — that is the record of why it is gone, and
        # a check that forbids naming the mistake would delete its own reason.
        path = Path(build_release_package.__file__)
        offenders = []
        with path.open("rb") as handle:
            for token in tokenize.tokenize(handle.readline):
                if token.type in (tokenize.COMMENT, tokenize.STRING):
                    continue
                if "-srcfolder" in token.string:
                    offenders.append(f"line {token.start[0]}: {token.string}")

        self.assertEqual(
            offenders,
            [],
            "the release disk image must be filled by ditto into an attached "
            "image, not by hdiutil's own copier, which refuses this bundle",
        )


@unittest.skipUnless(sys.platform == "darwin", "hdiutil is macOS-only")
class DiskImageFailureIsExplained(unittest.TestCase):
    """A failed release build has to say why it failed.

    On 2026-07-27 this step failed during a release build and the log carried
    the exit code and nothing else, because of `-quiet`. Recovering the reason
    took re-running the command by hand — possible on a laptop, impossible on
    a build machine. `hdiutil -quiet` prints zero bytes on failure; measured.
    """

    def test_hdiutil_reports_a_reason_when_it_cannot_attach(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            command = attach_command(
                # No such image, so hdiutil must refuse.
                image=Path(directory) / "absent.dmg",
                mountpoint=Path(directory),
            )
            completed = subprocess.run(
                command, capture_output=True, text=True, timeout=120
            )

        self.assertNotEqual(
            completed.returncode, 0, "hdiutil attached an image that does not exist"
        )
        self.assertTrue(
            (completed.stdout + completed.stderr).strip(),
            "hdiutil failed without printing a reason, so a release build that "
            "fails here leaves the operator with an exit code and nothing else",
        )


@unittest.skipUnless(sys.platform == "darwin", "hdiutil is macOS-only")
class TheImageCarriesWhatWasStaged(unittest.TestCase):
    """What is staged is what the customer sees when they open the file.

    The payload here is synthetic on purpose. The bundle that provoked the
    rewrite cannot be stood in for: a synthetic `Probe.app` copies fine, only
    a genuine signed application is refused, and building one costs a full
    release run. So this covers the part a fixture can cover — that the
    assembly moves every staged entry, symlinks included, and leaves no mount
    behind — and the signed bundle is verified on the real artifact by
    `require_distributable_release`.
    """

    def test_every_staged_entry_reaches_the_volume(self) -> None:
        volume_name = "automation-tool-fixture"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging = root / "image"
            (staging / "Payload").mkdir(parents=True)
            (staging / "Payload" / "file.txt").write_text("content", encoding="utf-8")
            (staging / "Applications").symlink_to("/Applications")
            output = root / "out.dmg"

            build_release_package.fill_disk_image(
                source=staging, volume_name=volume_name, output=output
            )
            self.assertTrue(output.is_file(), "no disk image was produced")

            mountpoint = root / "mounted"
            mountpoint.mkdir()
            subprocess.run(
                [*attach_command(image=output, mountpoint=mountpoint), "-readonly"],
                check=True,
                capture_output=True,
                text=True,
                timeout=300,
            )
            try:
                carried = sorted(path.name for path in mountpoint.iterdir())
                drag_target = (mountpoint / "Applications").readlink()
                payload = (mountpoint / "Payload" / "file.txt").read_text(
                    encoding="utf-8"
                )
            finally:
                subprocess.run(
                    ["hdiutil", "detach", os.fspath(mountpoint)],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=300,
                )

        self.assertEqual(carried, ["Applications", "Payload"])
        self.assertEqual(drag_target, Path("/Applications"))
        self.assertEqual(payload, "content")
        # Nothing may be left mounted under /Volumes: parallel release lines
        # used to collide there, and a stale mount outlives the build.
        self.assertFalse(Path("/Volumes", volume_name).exists())


class TheOutputDirectoryIsCreatedBeforeItIsUsed(unittest.TestCase):
    """`create_disk_image` stages under `output.parent`, so it must create it.

    Measured 2026-07-27, three release runs, two of them lost to this: the
    build died at `tempfile.TemporaryDirectory(dir=output.parent)` with
    `FileNotFoundError: .../bundle/dmg/tmpXXXX`. `tauri build --bundles app`
    produces only `bundle/macos/` — Tauri's DMG bundler never runs, so
    `bundle/dmg/` is never created by the build. The pre-refactor code created
    it here; when the imaging moved into `fill_disk_image` the `mkdir` moved
    with it, and `fill_disk_image` runs *after* this staging directory.

    Creating it by hand does not work: `tauri build` rebuilds `bundle/`
    wholesale on every run (measured — a directory made at 08:31 was gone by
    08:36:41), so the next run is back where it started.

    Asserted on the source rather than by calling the function, because
    reaching that line requires notarising a real bundle: forty minutes and a
    round trip to Apple per assertion.
    """

    def test_create_disk_image_makes_the_output_directory_first(self) -> None:
        source = Path(build_release_package.__file__).read_text(encoding="utf-8")
        start = source.index("def create_disk_image")
        body = source[start : source.index("\ndef ", start + 1)]

        staging = body.index("TemporaryDirectory(dir=output.parent)")
        made = body.find("output.parent.mkdir")

        self.assertNotEqual(made, -1, "create_disk_image never creates output.parent")
        self.assertLess(
            made,
            staging,
            "output.parent is used as a temporary-directory location before "
            "anything creates it",
        )

if __name__ == "__main__":
    unittest.main()
