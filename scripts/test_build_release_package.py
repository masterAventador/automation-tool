#!/usr/bin/env python3
"""Tests for the one command that produces a distributable package.

These run the real `hdiutil` against inputs it must reject. Nothing here
notarises, signs or builds a bundle — those need Apple and forty minutes.
"""

from __future__ import annotations

import argparse
import contextlib
import json
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
from build_release_package import (  # noqa: E402
    attach_command,
    embed_release_identity,
)
from release_identity import SourceFacts  # noqa: E402


def rendered_capability_reference(read_descriptor: int) -> str:
    """What this platform's spawner would actually put in the environment.

    POSIX hands over the file descriptor; Windows hands over an inheritable OS
    handle, because `pass_fds` does not exist there. Spelling the descriptor
    into the environment on both would test the reader against a shape nothing
    in the product ever produces.
    """
    if os.name == "nt":
        import msvcrt

        return str(msvcrt.get_osfhandle(read_descriptor))
    return str(read_descriptor)


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
                    {capability_name: rendered_capability_reference(read_descriptor)},
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
            with contextlib.suppress(OSError):
                os.close(read_descriptor)

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
            with (
                mock.patch.dict(
                    os.environ,
                    {capability_name: rendered_capability_reference(read_descriptor)},
                    clear=False,
                ),
                self.assertRaisesRegex(
                    build_release_package.ReleaseFailed,
                    "snapshot layout",
                ),
            ):
                build_release_package.require_materialized_source_snapshot(
                    ROOT / ".local" / "release-work"
                )
        finally:
            with contextlib.suppress(OSError):
                os.close(read_descriptor)

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
                mock.patch.object(
                    sys, "argv", ["build_release_package.py", "--work-dir", "../out"]
                ),
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
        """Whichever platform is being built, the restart comes first.

        This used to look for `build_macos_release(` because that was the only
        builder `main()` could reach. `main()` now picks between two, so the
        anchor is the dispatch — searching for one platform's builder would
        stop covering the other, silently.
        """
        source = Path(build_release_package.__file__).read_text(encoding="utf-8")
        main = source.index("def main()")
        snapshot = source.index("run_from_materialized_source_snapshot(", main)
        dispatch = source.index("build_windows_release if", main)
        invocation = source.index("result = build(", main)
        self.assertLess(snapshot, dispatch)
        self.assertLess(dispatch, invocation)

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
            # Skipped rather than relaxed on Windows. `chmod` there only toggles
            # the read-only bit, so every mode reads back as 0o666: asserting
            # equality against 0o644 fails for an unrelated reason, and
            # asserting "unchanged" instead passes even when the code sets a
            # different mode — measured, by setting 0o600 and watching this stay
            # green. A vacuous assertion is worse than an absent one, because it
            # reads like coverage.
            if os.name != "nt":
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

class WindowsReleaseTests(unittest.TestCase):
    """EB-18. `--platform windows` must build, not refuse.

    Until now the Windows release existed only inside
    `scripts/run_eb_16_windows_acceptance.py`, and two further Windows
    acceptance runners each carried their own `build_installer()`. Three copies
    of the shipped path, none of them reachable as a command, and the one
    command that *is* the shipped path refused the platform outright.

    That is the exact shape the macOS half was created to end: when the
    verified path and the shipped path are different code, they drift, and on
    2026-07-26 the shipped one went out with three resources missing.
    """

    def test_the_build_id_names_the_platform_being_built(self) -> None:
        """A real Windows release shipped `"buildId": "macos-release"`.

        The default was the literal string `macos-release`, and nothing changed
        it when `--platform windows` was passed. The field is not decoration:
        the EB-11 runner matches it against the packaged executor's build id, so
        a package that misnames itself either fails there for a confusing reason
        or agrees because both sides carry the same wrong name.
        """
        for platform_name in ("macos", "windows"):
            with self.subTest(platform=platform_name):
                arguments = build_release_package.parse_arguments(
                    ["--platform", platform_name]
                )

                self.assertEqual(f"{platform_name}-release", arguments.build_id)

    def test_an_explicit_build_id_still_wins(self) -> None:
        arguments = build_release_package.parse_arguments(
            ["--platform", "windows", "--build-id", "customer-demo-xuanbai"]
        )

        self.assertEqual("customer-demo-xuanbai", arguments.build_id)

    def test_a_configuration_that_would_ship_no_release_identity_is_refused(
        self,
    ) -> None:
        """Writing the identity and shipping it are two different things.

        The 2026-08-05 Windows release wrote `release-identity.v1.json` into the
        payload, announced it, passed every gate and produced an installer whose
        install root did not contain it — because `bundle.resources` is derived
        from the resource contract and the identity is not a resource. The EB-11
        runner reads that file out of the *installed* package, so the whole
        provenance claim was unreachable while every step reported success.

        This is the cheapest place to catch it: the configuration is written
        minutes before `makensis` runs, and it is the last artifact that still
        says what the installer will contain.
        """
        from release_identity import PACKAGED_IDENTITY_NAME

        with tempfile.TemporaryDirectory() as temporary:
            configuration = Path(temporary) / "tauri.json"
            resources = {"../../build/payload/embedded-browser/": "embedded-browser/"}
            configuration.write_text(
                json.dumps({"bundle": {"resources": dict(resources)}}),
                encoding="utf-8",
            )

            with self.assertRaises(build_release_package.ReleaseFailed):
                build_release_package.require_declared_release_identity(configuration)

            resources["../../build/payload/release-identity.v1.json"] = (
                PACKAGED_IDENTITY_NAME
            )
            configuration.write_text(
                json.dumps({"bundle": {"resources": resources}}), encoding="utf-8"
            )

            build_release_package.require_declared_release_identity(configuration)

    def test_the_packaged_executor_carries_the_build_id_the_identity_claims(
        self,
    ) -> None:
        """Two names for one build is the same defect as `buildId: macos-release`.

        The Windows executor builder lives in the EB-16 acceptance script and
        hardcoded `eb-16-windows-release` into the manifest it signs, while the
        release identity beside it carried `--build-id`. Measured on the package
        installed 2026-08-05: the identity said `windows-release` and the
        packaged executor manifest said `eb-16-windows-release`.

        That is not cosmetic. `require_release_identity` compares the two, so
        EB-11 refuses the package with "signed release does not match the
        packaged Executor" — an accurate message pointing at a build that was
        internally inconsistent from the start.
        """
        with tempfile.TemporaryDirectory() as temporary:
            executor = Path(temporary)
            manifest = executor / "executor-manifest.v1.json"
            manifest.write_text(
                json.dumps({"build_id": "eb-16-windows-release"}), encoding="utf-8"
            )

            with self.assertRaises(build_release_package.ReleaseFailed):
                build_release_package.require_executor_build_id(executor, "windows-release")

            manifest.write_text(json.dumps({"build_id": "windows-release"}), encoding="utf-8")

            build_release_package.require_executor_build_id(executor, "windows-release")

    def test_a_missing_executor_manifest_is_refused_rather_than_assumed(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temporary,
            self.assertRaises(build_release_package.ReleaseFailed),
        ):
            build_release_package.require_executor_build_id(
                Path(temporary), "windows-release"
            )

    def test_the_default_work_directory_passes_the_gate_that_guards_it(self) -> None:
        """A default the tool itself rejects is not a default.

        `.local/release` is 33 characters against this checkout and the NSIS
        path budget allows 31, so `--platform windows` with no `--work-dir`
        could never run — measured 2026-08-05, and the refusal even suggested
        `C:\\atrel`, sending the operator outside the project for no reason. A
        shorter name under `.local` fits, so the release output stays where
        every other build artefact in this project lives.
        """
        arguments = build_release_package.parse_arguments(["--platform", "windows"])

        build_release_package.require_windows_path_budget(arguments.work_dir)

        self.assertEqual(
            arguments.work_dir.parent, build_release_package.REPOSITORY_ROOT / ".local"
        )

    def test_both_platforms_share_one_default_work_directory(self) -> None:
        """One name, because the reason for a second one was removed.

        A shorter Windows default existed only to survive the 67 characters the
        bundler used to walk through: the resource sources were written relative
        to the Tauri root, so `makensis` saw
        `…\source-snapshot-XXXXXXXX\repository\frontend\src-tauri\..\..\..\..\build\payload\…`
        — a detour that resolves straight back to the work directory. Absolute
        sources, which macOS has always used, removed it.
        """
        macos = build_release_package.parse_arguments(["--platform", "macos"]).work_dir
        windows = build_release_package.parse_arguments(["--platform", "windows"]).work_dir

        self.assertEqual(macos, windows)
        self.assertEqual(macos, build_release_package.REPOSITORY_ROOT / ".local/release")

    def test_the_refusal_points_inside_the_project(self) -> None:
        """The message is the only guidance an operator gets at that moment."""
        deep = build_release_package.REPOSITORY_ROOT / ".local" / ("d" * 200)

        with self.assertRaises(build_release_package.ReleaseFailed) as raised:
            build_release_package.require_windows_path_budget(deep)

        self.assertNotIn("C:\\atrel", str(raised.exception))
        self.assertIn(".local", str(raised.exception))

    def test_the_windows_platform_has_a_release_builder(self) -> None:
        self.assertTrue(
            hasattr(build_release_package, "build_windows_release"),
            "--platform windows has no builder, so main() can only refuse it",
        )

    def test_a_private_dependency_copy_does_not_depend_on_bin_cp(self) -> None:
        """The snapshot's private copy must exist on the host doing the build.

        `_clone_snapshot_dependency` shells out to `/bin/cp -c` for APFS
        `clonefile`. There is no `/bin/cp` on Windows and no `cp` on PATH
        (measured), so on the host that builds the Windows package this raises
        `release source snapshot dependency could not be copied` before a
        single byte is copied.

        The property under test is not the technique but the outcome the
        docstring already claims: the build gets its own copy, and writing to
        it does not reach the operator's. A filesystem without clone support
        is allowed to be slower; it is not allowed to be absent.
        """
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "node_modules"
            (source / "nested").mkdir(parents=True)
            (source / "nested" / "marker.txt").write_text("原始", encoding="utf-8")
            target = root / "snapshot-node_modules"

            build_release_package._clone_snapshot_dependency(source, target)

            copied = target / "nested" / "marker.txt"
            self.assertEqual(copied.read_text(encoding="utf-8"), "原始")
            copied.write_text("快照改过", encoding="utf-8")
            self.assertEqual(
                (source / "nested" / "marker.txt").read_text(encoding="utf-8"),
                "原始",
                "the snapshot copy is not independent of the operator's tree",
            )

    def test_the_snapshot_capability_reaches_a_child_on_this_host(self) -> None:
        """The capability handoff must work on the host that builds the package.

        Round 14 of the EB-11 review replaced two public environment variables
        with a one-shot anonymous pipe precisely because the public pair let a
        caller dress an ordinary writable checkout up as a materialized
        snapshot. The pipe is handed over with `subprocess.run(pass_fds=...)`,
        and `pass_fds` raises `AssertionError: pass_fds not supported on
        Windows` (measured on this host).

        So on Windows the release identity has no delivery mechanism at all —
        not a weaker one, none. This asserts the round trip rather than the
        mechanism: the parent hands a payload to exactly one child, and the
        child reads back the same bytes.
        """
        self.assertTrue(
            hasattr(build_release_package, "spawn_with_source_snapshot_capability"),
            "the capability handoff is inlined and POSIX-only, so no Windows "
            "release can carry a trustworthy source identity",
        )
        payload = b"automation-tool.release-source-snapshot.v1\x00probe\n"
        with tempfile.TemporaryDirectory() as temporary:
            echoed = Path(temporary) / "echoed.bin"
            reader = Path(temporary) / "reader.py"
            reader.write_text(
                "import os, pathlib, sys\n"
                f"sys.path.insert(0, {os.fspath(ROOT / 'scripts')!r})\n"
                "import build_release_package as release\n"
                "pathlib.Path(sys.argv[1]).write_bytes("
                "release.read_source_snapshot_capability_bytes())\n",
                encoding="utf-8",
            )
            returncode = build_release_package.spawn_with_source_snapshot_capability(
                [sys.executable, os.fspath(reader), os.fspath(echoed)],
                capability=payload,
                environment=os.environ.copy(),
                cwd=Path.cwd(),
            )
            # Asserted inside the context manager: `echoed` lives in the
            # temporary directory, so reading it after the block only ever
            # reports that the directory was cleaned up.
            self.assertEqual(returncode, 0)
            self.assertEqual(echoed.read_bytes(), payload)


if __name__ == "__main__":
    unittest.main()
