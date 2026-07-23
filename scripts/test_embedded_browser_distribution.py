#!/usr/bin/env python3
"""EB-05 deterministic tests for the single distribution manifest and verifier.

Synthetic staging trees only (no network, no real browser): the manifest must
aggregate every locked runtime version, carry per-file digests, and the
verifier must reject tampering, missing files, extra files, extra browsers,
platform mismatches and version drift — all fail closed.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_embedded_browser_distribution import (  # noqa: E402
    DISTRIBUTION_MANIFEST_NAME,
    DistributionRejected,
    build_distribution_manifest,
    verify_distribution,
)
from build_embedded_chromium_staging import (  # noqa: E402
    build_staging,
    load_staging_contract,
    sha256_file,
)

STAGING_CONTRACT_PATH = ROOT / "contracts/browser/embedded-chromium-staging.v1.json"

_SYMLINK_MODE = 0xA1FF
TARGET_ID = "windows-x86_64" if os.name == "nt" else "macos-arm64"
ROOT_ENTRY = "chrome-win64" if os.name == "nt" else "chrome-mac-arm64"
EXECUTABLE = (
    "chrome-win64/chrome.exe"
    if os.name == "nt"
    else (
        "chrome-mac-arm64/Google Chrome for Testing.app/Contents/"
        "MacOS/Google Chrome for Testing"
    )
)
MUTABLE_FILE = (
    "chrome-win64/locales/en-US.pak"
    if os.name == "nt"
    else "chrome-mac-arm64/Google Chrome for Testing.app/Contents/Info.plist"
)


def _write_zip(path: Path, entries: dict[str, bytes]) -> str:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in entries.items():
            info = zipfile.ZipInfo(name)
            executable = name == EXECUTABLE
            info.external_attr = (0o755 if executable else 0o644) << 16
            archive.writestr(info, payload)
    return sha256_file(path)


def _valid_entries() -> dict[str, bytes]:
    if os.name == "nt":
        return {
            EXECUTABLE: b"MZ synthetic browser",
            MUTABLE_FILE: b"synthetic locale",
        }
    app = "chrome-mac-arm64/Google Chrome for Testing.app/Contents"
    return {
        f"{app}/MacOS/Google Chrome for Testing": b"binary",
        f"{app}/Info.plist": b"<plist/>",
    }


class DistributionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory(prefix="eb05-test-")
        self.addCleanup(self._directory.cleanup)
        self.base = Path(self._directory.name)
        self.contract = load_staging_contract(STAGING_CONTRACT_PATH)
        archive = self.base / "archive.zip"
        digest = _write_zip(archive, _valid_entries())
        self.staging = self.base / "staging"
        build_staging(
            contract=self.contract,
            target_id=TARGET_ID,
            archive_path=archive,
            archive_sha256=digest,
            output=self.staging,
        )

    def _manifest(self) -> Path:
        return build_distribution_manifest(
            staging=self.staging, target_id=TARGET_ID, enforce_archive_lock=False
        )

    def test_manifest_aggregates_all_locked_runtime_versions(self) -> None:
        manifest_path = self._manifest()
        self.assertEqual(manifest_path.name, DISTRIBUTION_MANIFEST_NAME)
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
        runtime = document["runtime"]
        self.assertEqual(runtime["playwright_python"], "1.61.0")
        self.assertEqual(runtime["chromium"]["browser_version"], "149.0.7827.55")
        self.assertEqual(runtime["chromium"]["revision"], "1228")
        self.assertEqual(runtime["browser_use"], "0.13.6")
        self.assertEqual(runtime["render_engine"], "0.7.68")
        self.assertEqual(document["target"], TARGET_ID)
        self.assertTrue(document["entries"])
        self.assertTrue(
            any(
                component["name"] == "chrome-for-testing"
                for component in document["sbom"]
            )
        )
        self.assertIn("licenses", document)

    def test_verify_passes_on_untouched_staging(self) -> None:
        self._manifest()
        report = verify_distribution(
            staging=self.staging, target_id=TARGET_ID, enforce_archive_lock=False
        )
        self.assertGreaterEqual(report.verified_files, 2)

    def test_default_mode_rejects_synthetic_archive_digest(self) -> None:
        with self.assertRaises(DistributionRejected):
            build_distribution_manifest(staging=self.staging, target_id=TARGET_ID)
        self._manifest()
        with self.assertRaises(DistributionRejected):
            verify_distribution(staging=self.staging, target_id=TARGET_ID)

    def test_tampered_file_is_rejected(self) -> None:
        self._manifest()
        target = self.staging / MUTABLE_FILE
        target.write_bytes(b"<tampered/>")
        with self.assertRaises(DistributionRejected):
            verify_distribution(
                staging=self.staging, target_id=TARGET_ID, enforce_archive_lock=False
            )

    def test_missing_file_is_rejected(self) -> None:
        self._manifest()
        (self.staging / MUTABLE_FILE).unlink()
        with self.assertRaises(DistributionRejected):
            verify_distribution(
                staging=self.staging, target_id=TARGET_ID, enforce_archive_lock=False
            )

    def test_extra_file_is_rejected(self) -> None:
        self._manifest()
        (self.staging / ROOT_ENTRY / "extra.bin").write_bytes(b"x")
        with self.assertRaises(DistributionRejected):
            verify_distribution(
                staging=self.staging, target_id=TARGET_ID, enforce_archive_lock=False
            )

    def test_second_platform_root_is_rejected(self) -> None:
        other_root = (
            "chrome-mac-arm64" if TARGET_ID == "windows-x86_64" else "chrome-win64"
        )
        extra = self.staging / other_root
        extra.mkdir()
        (extra / "second-platform-browser").write_bytes(b"x")
        with self.assertRaises(DistributionRejected):
            self._manifest()
        (extra / "second-platform-browser").unlink()
        extra.rmdir()
        self._manifest()
        extra.mkdir()
        (extra / "second-platform-browser").write_bytes(b"x")
        with self.assertRaises(DistributionRejected):
            verify_distribution(
                staging=self.staging, target_id=TARGET_ID, enforce_archive_lock=False
            )

    def test_extra_browser_binary_is_rejected_by_name(self) -> None:
        self._manifest()
        extra = self.staging / ROOT_ENTRY / "chrome-headless-shell.exe"
        extra.write_bytes(b"x")
        with self.assertRaises(DistributionRejected):
            verify_distribution(
                staging=self.staging, target_id=TARGET_ID, enforce_archive_lock=False
            )

    def test_platform_mismatch_is_rejected(self) -> None:
        self._manifest()
        with self.assertRaises(DistributionRejected):
            verify_distribution(
                staging=self.staging,
                target_id=(
                    "macos-arm64" if TARGET_ID == "windows-x86_64" else "windows-x86_64"
                ),
                enforce_archive_lock=False,
            )

    def test_manifest_version_drift_is_rejected(self) -> None:
        manifest_path = self._manifest()
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
        document["runtime"]["chromium"]["browser_version"] = "150.0.0.0"
        manifest_path.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaises(DistributionRejected):
            verify_distribution(
                staging=self.staging, target_id=TARGET_ID, enforce_archive_lock=False
            )

    def test_manifest_executable_target_drift_is_rejected(self) -> None:
        manifest_path = self._manifest()
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
        document["executable"] = (
            "chrome-mac-x64/Google Chrome for Testing.app/Contents/MacOS/"
            "Google Chrome for Testing"
        )
        manifest_path.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaises(DistributionRejected):
            verify_distribution(
                staging=self.staging, target_id=TARGET_ID, enforce_archive_lock=False
            )

    def test_manifest_is_deterministic(self) -> None:
        first = self._manifest().read_bytes()
        second = self._manifest().read_bytes()
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
