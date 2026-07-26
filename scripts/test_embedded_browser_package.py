#!/usr/bin/env python3
"""EB-16 deterministic tests for the first-release package gate.

Synthetic bundles only (no network, no real browser, no Tauri build): one
release bundle must carry exactly one complete Chromium for its own target,
no second browser, no WebDriver, no hidden test window configuration and a
plausible size. Every rejection path fails closed.
"""

from __future__ import annotations

import functools
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_embedded_browser_distribution import (  # noqa: E402
    DistributionRejected,
    build_distribution_manifest,
    install_distribution,
    verify_distribution,
)
from build_embedded_chromium_staging import (  # noqa: E402
    build_staging,
    load_staging_contract,
    sha256_file,
)
from check_embedded_browser_package import (  # noqa: E402
    RELEASE_PAYLOAD_PARTS_MIB,
    RELEASE_SIZE_BOUNDS,
    PackageRejected,
    PackageSizeBounds,
    audit_embedded_browser_package,
    browser_resource_root,
)

STAGING_CONTRACT_PATH = ROOT / "contracts/browser/embedded-chromium-staging.v1.json"
RELEASE_BUNDLE_AUDIT = ROOT / "frontend/scripts/audit-release-bundle.mjs"
PRODUCTION_PACKAGE_AUDIT = ROOT / "frontend/scripts/audit-production-package.mjs"

TARGET_ID = "macos-arm64"
PLATFORM = "macos"
ROOT_ENTRY = "chrome-mac-arm64"
EXECUTABLE = (
    "chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/"
    "Google Chrome for Testing"
)
MUTABLE_FILE = "chrome-mac-arm64/Google Chrome for Testing.app/Contents/Info.plist"

TINY_BOUNDS = PackageSizeBounds(
    min_browser_bytes=1,
    max_browser_bytes=1024 * 1024,
    min_package_bytes=1,
    max_package_bytes=1024 * 1024,
)


_SYMLINK_MODE = 0xA1FF


def _symlinks_creatable() -> bool:
    """Whether this process may create symlinks.

    Windows needs `SeCreateSymbolicLinkPrivilege` (developer mode or an
    elevated token), so the macOS-shaped fixtures below cannot be built there
    without it. The production Windows target declares no symlink at all
    (`allow_symlinks` is macOS-only in EB-03), so skipping these fixtures loses
    no Windows coverage — but silently erroring the whole suite would.
    """
    with tempfile.TemporaryDirectory(prefix="eb16-symlink-probe-") as directory:
        try:
            Path(directory, "link").symlink_to("target")
        except (NotImplementedError, OSError):
            return False
        return True


_REQUIRES_SYMLINKS = unittest.skipUnless(
    _symlinks_creatable(), "creating symlinks requires a privilege this process lacks"
)


def _write_zip(
    path: Path, entries: dict[str, bytes], symlinks: dict[str, str] | None = None
) -> str:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in entries.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = (0o755 if name == EXECUTABLE else 0o644) << 16
            archive.writestr(info, payload)
        for name, target in (symlinks or {}).items():
            info = zipfile.ZipInfo(name)
            info.external_attr = _SYMLINK_MODE << 16
            archive.writestr(info, target)
    return sha256_file(path)


def _synthetic_entries() -> dict[str, bytes]:
    contents = "chrome-mac-arm64/Google Chrome for Testing.app/Contents"
    widevine = (
        f"{contents}/Frameworks/Google Chrome for Testing Framework.framework/"
        "Versions/149.0.7827.55/Libraries/WidevineCdm"
    )
    return {
        EXECUTABLE: b"synthetic browser binary",
        f"{contents}/Info.plist": b"<plist/>",
        f"{widevine}/LICENSE": b"synthetic proprietary license",
        f"{widevine}/manifest.json": b'{"name":"WidevineCdm"}',
        f"{widevine}/_platform_specific/mac_arm64/libwidevinecdm.dylib": (
            b"synthetic proprietary binary"
        ),
    }


class EmbeddedBrowserPackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory(prefix="eb16-test-")
        self.addCleanup(self._directory.cleanup)
        self.base = Path(self._directory.name)
        self.bundle = self.base / "Automation Tool.app"
        self.browser = browser_resource_root(self.bundle, PLATFORM)
        self.browser.parent.mkdir(parents=True)
        archive = self.base / "archive.zip"
        digest = _write_zip(archive, _synthetic_entries())
        build_staging(
            contract=load_staging_contract(STAGING_CONTRACT_PATH),
            target_id=TARGET_ID,
            archive_path=archive,
            archive_sha256=digest,
            output=self.browser,
        )
        build_distribution_manifest(
            staging=self.browser, target_id=TARGET_ID, enforce_archive_lock=False
        )
        binary = self.bundle / "Contents/MacOS/Automation Tool"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"synthetic release binary")
        binary.chmod(0o755)
        (self.bundle / "Contents/Info.plist").write_bytes(b"<plist/>")

    def _audit(self, **overrides: object) -> object:
        arguments: dict[str, object] = {
            "bundle_root": self.bundle,
            "target_id": TARGET_ID,
            "platform": PLATFORM,
            "enforce_archive_lock": False,
            "size_bounds": TINY_BOUNDS,
        }
        arguments.update(overrides)
        return audit_embedded_browser_package(**arguments)  # type: ignore[arg-type]

    def test_release_size_bounds_are_a_real_gate(self) -> None:
        bounds = RELEASE_SIZE_BOUNDS
        self.assertLess(bounds.min_browser_bytes, bounds.max_browser_bytes)
        self.assertLess(bounds.min_package_bytes, bounds.max_package_bytes)
        self.assertGreaterEqual(bounds.min_browser_bytes, 300 * 1024 * 1024)
        self.assertLessEqual(bounds.max_browser_bytes, 500 * 1024 * 1024)
        self.assertGreaterEqual(bounds.min_package_bytes, bounds.min_browser_bytes)

    def test_release_size_bounds_admit_the_declared_production_payload(self) -> None:
        payload = sum(RELEASE_PAYLOAD_PARTS_MIB.values()) * 1024 * 1024
        self.assertEqual(RELEASE_PAYLOAD_PARTS_MIB["embedded-chromium"], 324)
        self.assertEqual(
            RELEASE_SIZE_BOUNDS.max_package_bytes,
            1125 * 1024 * 1024,
        )
        self.assertGreaterEqual(RELEASE_SIZE_BOUNDS.max_package_bytes, payload)
        self.assertLessEqual(
            RELEASE_SIZE_BOUNDS.max_package_bytes, payload + payload // 10
        )

    def test_declared_production_payload_lists_every_shipped_part(self) -> None:
        self.assertEqual(
            set(RELEASE_PAYLOAD_PARTS_MIB),
            {
                "embedded-chromium",
                "local-executor",
                "material-video-worker",
                "motion-video-worker",
                "media-toolchain",
                "app-shell-and-web-assets",
            },
        )
        self.assertTrue(all(value > 0 for value in RELEASE_PAYLOAD_PARTS_MIB.values()))

    def test_one_complete_target_browser_passes_and_is_measured(self) -> None:
        report = self._audit()
        self.assertEqual(report.target_id, TARGET_ID)
        self.assertEqual(report.platform, PLATFORM)
        self.assertEqual(report.browser_files, 2)
        self.assertGreater(report.browser_bytes, 0)
        self.assertGreater(report.package_files, report.browser_files)
        self.assertGreater(report.package_bytes, report.browser_bytes)

    def test_missing_browser_resource_is_rejected(self) -> None:
        manifest = self.browser / "distribution-manifest.v1.json"
        manifest.unlink()
        with self.assertRaises(PackageRejected):
            self._audit()

    def test_unknown_platform_is_rejected(self) -> None:
        with self.assertRaises(PackageRejected):
            self._audit(platform="linux")

    def test_target_and_platform_mismatch_is_rejected(self) -> None:
        with self.assertRaises(PackageRejected):
            self._audit(target_id="windows-x86_64")

    @_REQUIRES_SYMLINKS
    def test_bundle_root_must_be_a_real_directory(self) -> None:
        link = self.base / "linked.app"
        link.symlink_to(self.bundle, target_is_directory=True)
        with self.assertRaises(PackageRejected):
            self._audit(bundle_root=link)
        with self.assertRaises(PackageRejected):
            self._audit(bundle_root=self.bundle / "Contents/Info.plist")

    def test_tampered_packaged_browser_file_is_rejected(self) -> None:
        (self.browser / MUTABLE_FILE).write_bytes(b"<tampered/>")
        with self.assertRaises(PackageRejected):
            self._audit()

    def test_second_target_browser_in_the_package_is_rejected(self) -> None:
        second = self.bundle / "Contents/Resources/chrome-win64"
        second.mkdir(parents=True)
        (second / "resources.pak").write_bytes(b"second target payload")
        with self.assertRaises(PackageRejected):
            self._audit()

    def test_second_browser_executable_in_the_package_is_rejected(self) -> None:
        extra = self.bundle / "Contents/Resources/vendor/chrome.exe"
        extra.parent.mkdir(parents=True)
        extra.write_bytes(b"MZ")
        with self.assertRaises(PackageRejected):
            self._audit()

    def test_headless_shell_outside_the_browser_root_is_rejected(self) -> None:
        extra = self.bundle / "Contents/Resources/chrome-headless-shell"
        extra.write_bytes(b"headless")
        with self.assertRaises(PackageRejected):
            self._audit()

    def test_webdriver_binary_in_the_package_is_rejected(self) -> None:
        for name in ("chromedriver", "tauri-driver", "msedgedriver.exe"):
            extra = self.bundle / "Contents/Resources" / name
            extra.write_bytes(b"driver")
            with self.assertRaises(PackageRejected):
                self._audit()
            extra.unlink()
        self._audit()

    @_REQUIRES_SYMLINKS
    def test_symlink_outside_the_browser_root_is_rejected(self) -> None:
        link = self.bundle / "Contents/Resources/escape"
        link.symlink_to(self.base)
        with self.assertRaises(PackageRejected):
            self._audit()

    def test_a_relative_symlink_that_stays_inside_the_package_is_allowed(self) -> None:
        """PyInstaller trees legitimately carry these and cannot ship without them.

        The material video Worker lays 53 dynamic libraries out this way — a
        top-level `_internal/libarrow.2300.dylib` pointing at
        `pyarrow/libarrow.2300.dylib` inside the same tree — because that is
        where its loader looks. Rejecting every symlink outside the browser was
        free when the only other payload was the executor, which has none. It is
        not free now, and "points somewhere else in this same package" is not
        the thing the rule exists to stop.
        """
        worker = self.bundle / "Contents/Resources/material-video-worker/package"
        (worker / "vendor").mkdir(parents=True)
        (worker / "vendor/libexample.dylib").write_bytes(b"payload")
        (worker / "libexample.dylib").symlink_to("vendor/libexample.dylib")
        self._audit()

    def test_a_relative_symlink_climbing_out_of_the_package_is_rejected(self) -> None:
        # `../` chains resolve outside just as surely as an absolute path does.
        worker = self.bundle / "Contents/Resources/material-video-worker/package"
        worker.mkdir(parents=True)
        (worker / "escape").symlink_to("../../../../..")
        with self.assertRaises(PackageRejected):
            self._audit()

    def test_a_symlink_into_the_browser_distribution_is_rejected(self) -> None:
        # Resolving inside the package is not sufficient: a link into the
        # browser tree lets a second, unverified path reach the browser without
        # passing the manifest check.
        worker = self.bundle / "Contents/Resources/material-video-worker/package"
        worker.mkdir(parents=True)
        (worker / "browser").symlink_to("../../embedded-browser")
        with self.assertRaises(PackageRejected):
            self._audit()

    def test_a_directory_symlink_inside_the_package_is_rejected(self) -> None:
        # Only file links are legitimate. A directory link gives one tree two
        # paths, which would let a payload sit somewhere the "this resource
        # lives here" checks never look. PyInstaller only links libraries.
        worker = self.bundle / "Contents/Resources/material-video-worker/package"
        (worker / "vendor").mkdir(parents=True)
        (worker / "vendor/libexample.dylib").write_bytes(b"payload")
        (worker / "mirror").symlink_to("vendor")
        with self.assertRaises(PackageRejected):
            self._audit()

    def test_a_dangling_symlink_is_rejected(self) -> None:
        # A link with no target cannot be shown to stay inside the package, and
        # at runtime it is an unexplained failure rather than a missing file.
        worker = self.bundle / "Contents/Resources/material-video-worker/package"
        worker.mkdir(parents=True)
        (worker / "missing").symlink_to("nowhere.dylib")
        with self.assertRaises(PackageRejected):
            self._audit()

    def test_oversized_package_is_rejected(self) -> None:
        bounds = PackageSizeBounds(
            min_browser_bytes=1,
            max_browser_bytes=1024 * 1024,
            min_package_bytes=1,
            max_package_bytes=8,
        )
        with self.assertRaises(PackageRejected):
            self._audit(size_bounds=bounds)

    def test_implausibly_small_browser_tree_is_rejected(self) -> None:
        bounds = PackageSizeBounds(
            min_browser_bytes=300 * 1024 * 1024,
            max_browser_bytes=500 * 1024 * 1024,
            min_package_bytes=1,
            max_package_bytes=1024 * 1024,
        )
        with self.assertRaises(PackageRejected):
            self._audit(size_bounds=bounds)


@_REQUIRES_SYMLINKS
class DistributionInstallationTests(unittest.TestCase):
    """Installing the distribution into a bundle must keep declared symlinks.

    The real macOS package build proved the Tauri resource copier follows and
    drops symlinks, which silently breaks the packaged Chromium framework and
    its upstream code signature. The release packager installs the tree itself
    and the gate must keep rejecting a dereferenced copy.
    """

    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory(prefix="eb16-install-")
        self.addCleanup(self._directory.cleanup)
        self.base = Path(self._directory.name)
        self.staging = self.base / "staging"
        archive = self.base / "archive.zip"
        digest = _write_zip(
            archive,
            _synthetic_entries(),
            {
                "chrome-mac-arm64/Google Chrome for Testing.app/Contents/Current": (
                    "Info.plist"
                )
            },
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

    def test_installed_distribution_keeps_declared_symlinks(self) -> None:
        destination = self.base / "Resources/embedded-browser"
        install_distribution(staging=self.staging, destination=destination)
        installed = destination / (
            "chrome-mac-arm64/Google Chrome for Testing.app/Contents/Current"
        )
        self.assertTrue(installed.is_symlink())
        self.assertEqual(str(installed.readlink()), "Info.plist")
        report = verify_distribution(
            staging=destination, target_id=TARGET_ID, enforce_archive_lock=False
        )
        self.assertEqual(report.verified_files, 2)

    def test_dereferenced_copy_is_rejected(self) -> None:
        destination = self.base / "Resources/dereferenced"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(self.staging, destination, symlinks=False)
        with self.assertRaises(DistributionRejected):
            verify_distribution(
                staging=destination, target_id=TARGET_ID, enforce_archive_lock=False
            )

    def test_existing_destination_is_rejected(self) -> None:
        destination = self.base / "Resources/embedded-browser"
        destination.mkdir(parents=True)
        with self.assertRaises(DistributionRejected):
            install_distribution(staging=self.staging, destination=destination)

    def test_failed_install_leaves_no_half_written_destination(self) -> None:
        destination = self.base / "Resources/embedded-browser"
        with unittest.mock.patch(
            "build_embedded_browser_distribution.shutil.copytree",
            side_effect=OSError("disk full"),
        ), self.assertRaises(OSError):
            install_distribution(staging=self.staging, destination=destination)
        self.assertFalse(destination.exists())
        install_distribution(staging=self.staging, destination=destination)
        self.assertTrue((destination / "distribution-manifest.v1.json").is_file())

    def test_declared_symlink_escaping_the_root_is_rejected(self) -> None:
        """A manifest inside the tree cannot authorise an escaping symlink."""
        destination = self.base / "Resources/embedded-browser"
        install_distribution(staging=self.staging, destination=destination)
        manifest_path = destination / "distribution-manifest.v1.json"
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
        escaping = "../../../../etc/passwd"
        for entry in document["entries"]:
            if entry.get("type") == "symlink":
                entry["targetPath"] = escaping
                link = destination / Path(*entry["path"].split("/"))
                link.unlink()
                link.symlink_to(escaping)
        manifest_path.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaises(DistributionRejected):
            verify_distribution(
                staging=destination, target_id=TARGET_ID, enforce_archive_lock=False
            )


class ReleaseBundleAuditTests(unittest.TestCase):
    """The P9-05 bundle audit keeps its contract and binds the exclusion.

    Excluding the embedded browser subtree from the path/content scan is only
    allowed when the stronger per-file digest gate passes. That gate enforces
    the contract archive lock, so it can only ever pass on the real, locked
    Chromium — the accepting path is therefore proved by the real EB-16
    acceptance run, and these deterministic tests prove the refusals and the
    unchanged behaviour for callers that ship no embedded browser.
    """

    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory(prefix="eb16-bundle-")
        self.addCleanup(self._directory.cleanup)
        self.base = Path(self._directory.name)
        self.bundle = self.base / "Automation Tool.app"
        self.executor = self.bundle / "Contents/Resources/local-executor/package"
        self.executor.mkdir(parents=True)
        (self.executor / "automation-tool-executor").write_bytes(b"executor")
        (self.executor / "executor-manifest.v1.json").write_bytes(b"{}")
        (self.executor / "executor-manifest.v1.sig").write_bytes(b"atems1.x")
        self.browser = self.bundle / "Contents/Resources/embedded-browser"
        (self.browser / "chrome-mac-arm64").mkdir(parents=True)
        (self.browser / "chrome-mac-arm64/resources.pak").write_bytes(b"pak")
        # Only the tests that assert on the upstream symlink layout need this
        # link; the digest-gate binding tests must still run where symlinks
        # cannot be created, because that is exactly the Windows situation.
        if _symlinks_creatable():
            (self.browser / "chrome-mac-arm64/Current").symlink_to("resources.pak")
        (self.browser / "distribution-manifest.v1.json").write_text(
            json.dumps({"target": TARGET_ID}), encoding="utf-8"
        )

    def _run(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "node",
                os.fspath(RELEASE_BUNDLE_AUDIT),
                "--bundle-root",
                os.fspath(self.bundle),
                "--executor-package",
                os.fspath(self.executor),
                "--platform",
                "macos",
                *extra,
            ],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_bundle_without_an_embedded_browser_keeps_the_original_contract(
        self,
    ) -> None:
        """Callers that ship no embedded browser must be unaffected."""
        shutil.rmtree(self.browser)
        accepted = self._run()
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        marker = self.bundle / "Contents/Resources/harness.json"
        marker.write_bytes(b'{"TAURI_WEBDRIVER_PORT": 4444}')
        self.assertNotEqual(self._run().returncode, 0)

    @_REQUIRES_SYMLINKS
    def test_browser_subtree_is_rejected_without_the_exclusion(self) -> None:
        """The upstream symlinks make an unexcluded browser subtree fail."""
        self.assertNotEqual(self._run().returncode, 0)

    def test_a_packaged_browser_always_runs_the_digest_gate(self) -> None:
        """A present browser resource must gate itself, declared or not.

        The symlink rejection above is a coincidence of the macOS upstream
        layout, not a mechanism: a bundler that dereferences the tree (RED-3)
        and every Windows target ship no symlink at all, so an audit that only
        gates when the caller passes `--embedded-browser` would accept a
        tampered or incomplete browser with exit code 0.
        """
        (self.browser / "chrome-mac-arm64/Current").unlink(missing_ok=True)
        result = self._run()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("release package rejected", result.stderr)

    def test_exclusion_requires_the_digest_gate_to_pass(self) -> None:
        """The exclusion runs the per-file digest gate and honours its verdict."""
        result = self._run("--embedded-browser", os.fspath(self.browser))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("release package rejected", result.stderr)

    def test_exclusion_requires_a_declared_distribution_target(self) -> None:
        (self.browser / "distribution-manifest.v1.json").write_text(
            json.dumps({"target": "../../etc"}), encoding="utf-8"
        )
        result = self._run("--embedded-browser", os.fspath(self.browser))
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("release package rejected", result.stderr)

    def test_excluded_browser_resource_must_be_the_declared_location(self) -> None:
        other = self.bundle / "Contents/Resources/somewhere-else"
        other.mkdir(parents=True)
        result = self._run("--embedded-browser", os.fspath(other))
        self.assertNotEqual(result.returncode, 0)


RELEASE_ASSET_NAME = "index-RELEASE.js"
EMBEDDED_ASSET_KEYS = f"/assets/{RELEASE_ASSET_NAME}".encode()


@functools.cache
def required_distribution_markers() -> tuple[str, ...]:
    """The capabilities the real audit demands, read from the audit itself.

    Restating them here would let this fixture keep passing after the audit
    gained a requirement — which is exactly how a fixture stops modelling the
    artifact it claims to stand for.
    """
    source = (
        "import { requiredDistributionMarkers } from "
        f"{json.dumps(PRODUCTION_PACKAGE_AUDIT.as_uri())};\n"
        "process.stdout.write(JSON.stringify(requiredDistributionMarkers));\n"
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", source],
        check=True,
        capture_output=True,
        text=True,
    )
    return tuple(json.loads(result.stdout))


class ProductionPackageAuditTests(unittest.TestCase):
    """The E4-15 binary audit must reject hidden test window configuration."""

    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory(prefix="eb16-production-")
        self.addCleanup(self._directory.cleanup)
        self.base = Path(self._directory.name)
        self.distribution = self.base / "dist"
        (self.distribution / "assets").mkdir(parents=True)
        (self.distribution / "index.html").write_text("<html></html>", encoding="utf-8")
        (self.distribution / "assets" / RELEASE_ASSET_NAME).write_text(
            "globalThis.desktop = true;"
            + "".join(
                f"invoke({marker!r});" for marker in required_distribution_markers()
            ),
            encoding="utf-8",
        )
        self.cargo_manifest = self.base / "Cargo.toml"
        self.cargo_manifest.write_text(
            'tauri-plugin-wdio = { version = "1", optional = true }\n'
            'tauri-plugin-wdio-webdriver = { version = "1", optional = true }\n',
            encoding="utf-8",
        )
        self.tauri_config = self.base / "tauri.conf.json"
        self.tauri_config.write_text(
            json.dumps(
                {
                    "identifier": "com.aventador.automationtool",
                    "app": {
                        "withGlobalTauri": False,
                        "windows": [{"label": "main"}],
                        "security": {"capabilities": ["main"], "csp": {}},
                    },
                    "bundle": {"active": True},
                }
            ),
            encoding="utf-8",
        )

    def _audit(self, binary: Path) -> subprocess.CompletedProcess[str]:
        source = (
            "import { auditProductionPackage } from "
            f"{json.dumps(PRODUCTION_PACKAGE_AUDIT.as_uri())};\n"
            "await auditProductionPackage({\n"
            f"  binaryPath: {json.dumps(os.fspath(binary))},\n"
            f"  cargoManifestPath: {json.dumps(os.fspath(self.cargo_manifest))},\n"
            '  dependencyTree: "tauri v2.0.0",\n'
            f"  distributionPath: {json.dumps(os.fspath(self.distribution))},\n"
            '  expectedVerifyingKey: "'
            'ZGVtby1lYjE2LXJlbGVhc2Uta2V5LWJ5dGVzLTMyISE",\n'
            f"  tauriConfigPath: {json.dumps(os.fspath(self.tauri_config))},\n"
            "});\n"
        )
        return subprocess.run(
            ["node", "--input-type=module", "-e", source],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_release_binary_without_test_markers_passes(self) -> None:
        binary = self.base / "release-binary"
        binary.write_bytes(
            b'{"label":"main","visible":true}'
            # A real Tauri binary carries each embedded asset's key as a plain
            # string; the audit requires the distribution it is handed to be the
            # one this binary embedded, so a release fixture has to carry them.
            + EMBEDDED_ASSET_KEYS
            + b"ZGVtby1lYjE2LXJlbGVhc2Uta2V5LWJ5dGVzLTMyISE"
        )
        result = self._audit(binary)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_hidden_test_window_configuration_is_rejected(self) -> None:
        """The effective build configuration, not a binary substring, decides."""
        configuration = json.loads(self.tauri_config.read_text(encoding="utf-8"))
        configuration["app"]["windows"][0]["visible"] = False
        self.tauri_config.write_text(json.dumps(configuration), encoding="utf-8")
        binary = self.base / "release-binary"
        binary.write_bytes(b"ZGVtby1lYjE2LXJlbGVhc2Uta2V5LWJ5dGVzLTMyISE")
        result = self._audit(binary)
        self.assertNotEqual(result.returncode, 0)

    def test_webdriver_binary_marker_is_rejected(self) -> None:
        binary = self.base / "webdriver-binary"
        binary.write_bytes(
            b"tauri-driver --port 4444"
            b"ZGVtby1lYjE2LXJlbGVhc2Uta2V5LWJ5dGVzLTMyISE"
        )
        result = self._audit(binary)
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
