#!/usr/bin/env python3
"""Deterministic tests for the production release assembler.

Synthetic bundles only (no network, no real browser, no Tauri build).

The gap these cover: until now the only non-test caller of
`install_distribution` was `scripts/run_eb_16_acceptance.py`, and
`tauri.conf.json` deliberately does not declare the browser under
`bundle.resources` — EB-16 measured that the bundler follows symlinks and
destroys the Chrome for Testing framework. The consequence was that a package
built by the ordinary candidate path (P9-03/P9-04) carried no browser at all,
and nothing refused to ship it; the failure only appeared on the user's
machine as a startup gate. So the assembly step has to live on a reusable
path, and reaching a distributable artifact has to require a verified browser.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_embedded_browser_distribution import (  # noqa: E402
    build_distribution_manifest,
)
from build_embedded_chromium_staging import (  # noqa: E402
    build_staging,
    load_staging_contract,
    sha256_file,
)
from check_embedded_browser_package import browser_resource_root  # noqa: E402
from release_assembly import (  # noqa: E402
    ReleaseAssemblyRejected,
    install_and_seal,
    install_video_runtime,
    require_packaged_browser,
    require_packaged_video_runtime,
)

STAGING_CONTRACT_PATH = ROOT / "contracts/browser/embedded-chromium-staging.v1.json"
TARGET_ID = "macos-arm64"
PLATFORM = "macos"
EXECUTABLE = (
    "chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/"
    "Google Chrome for Testing"
)
INFO_PLIST = "chrome-mac-arm64/Google Chrome for Testing.app/Contents/Info.plist"


def _write_zip(path: Path, entries: dict[str, bytes]) -> str:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in entries.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = (0o755 if name == EXECUTABLE else 0o644) << 16
            archive.writestr(info, payload)
    return sha256_file(path)


class ReleaseAssemblyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory(prefix="release-assembly-test-")
        self.addCleanup(self._directory.cleanup)
        self.base = Path(self._directory.name)

        self.application = self.base / "自动化运营工具.app"
        binary = self.application / "Contents/MacOS/自动化运营工具"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"synthetic release binary")
        binary.chmod(0o755)
        (self.application / "Contents/Info.plist").write_bytes(b"<plist/>")

        self.staging = self.base / "staging"
        archive = self.base / "archive.zip"
        digest = _write_zip(
            archive,
            {EXECUTABLE: b"synthetic browser binary", INFO_PLIST: b"<plist/>"},
        )
        build_staging(
            contract=load_staging_contract(STAGING_CONTRACT_PATH),
            target_id=TARGET_ID,
            archive_path=archive,
            archive_sha256=digest,
            output=self.staging,
        )
        build_distribution_manifest(
            staging=self.staging, target_id=TARGET_ID, enforce_archive_lock=False
        )
        self.sealed: list[Path] = []

    def seal(self, application: Path) -> None:
        self.sealed.append(application)

    def assemble(self) -> None:
        install_and_seal(
            application=self.application,
            staging=self.staging,
            target_id=TARGET_ID,
            platform=PLATFORM,
            enforce_archive_lock=False,
            seal=self.seal,
        )

    def test_a_bundle_without_a_browser_is_not_distributable(self) -> None:
        # This is the shipped defect: the ordinary candidate build produces
        # exactly this bundle, and nothing used to refuse it.
        with self.assertRaises(ReleaseAssemblyRejected):
            require_packaged_browser(
                application=self.application, target_id=TARGET_ID, platform=PLATFORM
            )

    def test_assembly_installs_the_browser_and_then_seals_the_bundle(self) -> None:
        self.assemble()
        installed = browser_resource_root(self.application, PLATFORM)
        self.assertTrue((installed / EXECUTABLE).is_file())
        # Sealing must happen after the install, never before: a signature
        # taken before the browser lands does not cover it.
        self.assertEqual(self.sealed, [self.application])
        require_packaged_browser(
            application=self.application,
            target_id=TARGET_ID,
            platform=PLATFORM,
            enforce_archive_lock=False,
        )

    def test_a_tampered_browser_stops_assembly_before_sealing(self) -> None:
        (self.staging / INFO_PLIST).write_bytes(b"<plist>tampered</plist>")
        with self.assertRaises(ReleaseAssemblyRejected):
            self.assemble()
        # Nothing was signed, and no half-installed tree is left to be picked
        # up by a retry or, worse, by the packaging step.
        self.assertEqual(self.sealed, [])
        self.assertFalse(browser_resource_root(self.application, PLATFORM).exists())

    def test_assembly_refuses_to_install_over_an_existing_tree(self) -> None:
        self.assemble()
        second = self.base / "second-staging"
        shutil.copytree(self.staging, second, symlinks=True)
        with self.assertRaises(ReleaseAssemblyRejected):
            install_and_seal(
                application=self.application,
                staging=second,
                target_id=TARGET_ID,
                platform=PLATFORM,
                enforce_archive_lock=False,
                seal=self.seal,
            )


class AssemblerIsTheOnlyPathTests(unittest.TestCase):
    """The acceptance script must not keep a private copy of the assembly."""

    def test_the_acceptance_script_delegates_to_the_shared_assembler(self) -> None:
        source = (ROOT / "scripts/run_eb_16_acceptance.py").read_text(encoding="utf-8")
        self.assertIn("from release_assembly import", source)
        # If the acceptance script still called `install_distribution` itself,
        # the verified path and the shipped path could drift apart again.
        self.assertNotIn("install_distribution(", source)

    def test_the_release_path_installs_and_gates_the_video_runtime(self) -> None:
        # Writing the assembler without wiring it into the one path that
        # produces a shipped package is exactly how the browser gap survived
        # its first fix, and how `TauriPublishWorkspaceGateway` shipped
        # unreachable. The gate has to be on the path, not merely available.
        for script in ("run_eb_16_acceptance.py", "run_eb_16_windows_acceptance.py"):
            with self.subTest(script=script):
                source = (ROOT / "scripts" / script).read_text(encoding="utf-8")
                self.assertIn("install_video_runtime(", source)
                self.assertIn("require_packaged_video_runtime(", source)


def _write_video_runtime(root: Path) -> None:
    """Lay down a minimally shaped, non-empty video runtime staging tree."""
    toolchain = root / "media-toolchain"
    (toolchain / "bin").mkdir(parents=True)
    (toolchain / "bin/ffmpeg").write_bytes(b"ffmpeg")
    (toolchain / "bin/ffprobe").write_bytes(b"ffprobe")
    (toolchain / "manifest.json").write_text("{}", encoding="utf-8")
    motion = root / "motion-video-worker"
    (motion / "runtime").mkdir(parents=True)
    (motion / "app").mkdir()
    (motion / "runtime/node").write_bytes(b"node")
    (motion / "app/worker.mjs").write_text("export {};", encoding="utf-8")
    material = root / "material-video-worker"
    material.mkdir()
    (material / "automation-tool-material-video-worker").write_bytes(b"worker")


class VideoRuntimeReleaseGateTests(unittest.TestCase):
    """The three video runtime resources need the same release gate as the browser.

    Every BM/IM acceptance script builds ffmpeg and the two Workers itself and
    hands their paths to a `video-studio-e2e` test build through environment
    variables. The production build reads `Contents/Resources/` instead, and
    nothing ever installed them there — so the shipped package showed
    "本机视频制作服务暂时无法启动" and "本机渲染组件暂时不可用" on a user's
    machine while every acceptance run stayed green.
    """

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="release-video-runtime-"))
        self.addCleanup(shutil.rmtree, self.base, True)
        self.application = self.base / "Example.app"
        (self.application / "Contents/Resources").mkdir(parents=True)
        self.staging = self.base / "video-runtime"
        _write_video_runtime(self.staging)

    def test_a_bundle_without_the_video_runtime_is_rejected(self) -> None:
        with self.assertRaises(ReleaseAssemblyRejected):
            require_packaged_video_runtime(
                application=self.application, platform=PLATFORM
            )

    def test_installing_the_video_runtime_satisfies_the_gate(self) -> None:
        install_video_runtime(
            application=self.application, staging=self.staging, platform=PLATFORM
        )
        installed = require_packaged_video_runtime(
            application=self.application, platform=PLATFORM
        )
        self.assertEqual(
            sorted(installed),
            ["material-video-worker", "media-toolchain", "motion-video-worker"],
        )

    def test_each_missing_resource_is_named(self) -> None:
        for resource in (
            "media-toolchain",
            "motion-video-worker",
            "material-video-worker",
        ):
            with self.subTest(resource=resource):
                application = self.base / f"Missing-{resource}.app"
                (application / "Contents/Resources").mkdir(parents=True)
                partial = self.base / f"staging-{resource}"
                _write_video_runtime(partial)
                shutil.rmtree(partial / resource)
                with self.assertRaises(ReleaseAssemblyRejected) as caught:
                    install_video_runtime(
                        application=application, staging=partial, platform=PLATFORM
                    )
                self.assertIn(resource, str(caught.exception))

    def test_an_empty_worker_payload_is_rejected(self) -> None:
        # A directory that merely exists is exactly what the production
        # resolver trips over: it finds the path and then fails to launch.
        (self.staging / "motion-video-worker/runtime/node").unlink()
        with self.assertRaises(ReleaseAssemblyRejected):
            install_video_runtime(
                application=self.application, staging=self.staging, platform=PLATFORM
            )

    def test_a_rejected_install_leaves_nothing_behind(self) -> None:
        (self.staging / "material-video-worker/automation-tool-material-video-worker").unlink()
        with self.assertRaises(ReleaseAssemblyRejected):
            install_video_runtime(
                application=self.application, staging=self.staging, platform=PLATFORM
            )
        resources = self.application / "Contents/Resources"
        self.assertEqual(sorted(path.name for path in resources.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
