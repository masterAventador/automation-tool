#!/usr/bin/env python3
"""EB-03 deterministic tests for the macOS embedded-Chromium staging builder.

No network: every case uses tiny synthetic archives. The builder must fail
closed on digest mismatch, traversal, escaping symlinks, unexpected or
forbidden entries, and must emit a byte-identical manifest for identical
inputs.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_embedded_chromium_staging import (  # noqa: E402
    StagingRejected,
    build_staging,
    generate_manifest,
    load_staging_contract,
    safe_extract,
    sha256_file,
)

CONTRACT_PATH = ROOT / "contracts/browser/embedded-chromium-staging.v1.json"

_SYMLINK_MODE = 0xA1FF  # lrwxrwxrwx


def _write_zip(path: Path, entries: dict[str, bytes | tuple[str, str]]) -> str:
    """Write a zip; value bytes = file, ("symlink", target) = symlink entry."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in entries.items():
            if isinstance(payload, tuple):
                info = zipfile.ZipInfo(name)
                info.external_attr = _SYMLINK_MODE << 16
                archive.writestr(info, payload[1])
            else:
                info = zipfile.ZipInfo(name)
                executable = name.endswith(("/Chromium", "Google Chrome for Testing"))
                info.external_attr = (0o755 if executable else 0o644) << 16
                archive.writestr(info, payload)
    return sha256_file(path)


def _valid_entries() -> dict[str, bytes | tuple[str, str]]:
    app = "chrome-mac-arm64/Google Chrome for Testing.app/Contents"
    return {
        f"{app}/MacOS/Google Chrome for Testing": b"binary",
        f"{app}/Info.plist": b"<plist/>",
        f"{app}/Frameworks/F.framework/Versions/A/F": b"framework",
        f"{app}/Frameworks/F.framework/Versions/Current": ("symlink", "A"),
    }


class StagingBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory(prefix="eb03-test-")
        self.addCleanup(self._directory.cleanup)
        self.base = Path(self._directory.name)
        self.contract = load_staging_contract(CONTRACT_PATH)
        self.target = self.contract.targets["macos-arm64"]

    def _archive(self, entries: dict[str, bytes | tuple[str, str]]) -> tuple[Path, str]:
        archive = self.base / "archive.zip"
        digest = _write_zip(archive, entries)
        return archive, digest

    def test_contract_locks_version_revision_and_target(self) -> None:
        self.assertEqual(self.contract.browser_version, "149.0.7827.55")
        self.assertEqual(self.contract.revision, "1228")
        self.assertEqual(self.target.root_entry, "chrome-mac-arm64")
        self.assertIn("cdn.playwright.dev", self.target.download_url)
        self.assertTrue(
            self.target.executable.startswith("chrome-mac-arm64/Google Chrome for Testing.app/")
        )

    def test_digest_mismatch_is_rejected(self) -> None:
        archive, _ = self._archive(_valid_entries())
        with self.assertRaises(StagingRejected):
            build_staging(
                contract=self.contract,
                target_id="macos-arm64",
                archive_path=archive,
                archive_sha256="00" * 32,
                output=self.base / "out",
            )

    def test_valid_archive_stages_and_manifest_is_reproducible(self) -> None:
        archive, digest = self._archive(_valid_entries())
        first = build_staging(
            contract=self.contract,
            target_id="macos-arm64",
            archive_path=archive,
            archive_sha256=digest,
            output=self.base / "out-a",
        )
        second = build_staging(
            contract=self.contract,
            target_id="macos-arm64",
            archive_path=archive,
            archive_sha256=digest,
            output=self.base / "out-b",
        )
        manifest_a = json.loads((self.base / "out-a/staging-manifest.json").read_text())
        manifest_b = json.loads((self.base / "out-b/staging-manifest.json").read_text())
        self.assertEqual(manifest_a, manifest_b)
        self.assertEqual(first.file_count, second.file_count)
        self.assertEqual(manifest_a["chromium"]["browser_version"], "149.0.7827.55")
        self.assertEqual(manifest_a["target"], "macos-arm64")
        executable_entry = next(
            entry
            for entry in manifest_a["entries"]
            if entry["path"].endswith("MacOS/Google Chrome for Testing")
        )
        self.assertTrue(executable_entry["executable"])
        symlink_entry = next(
            entry for entry in manifest_a["entries"] if entry["type"] == "symlink"
        )
        self.assertEqual(symlink_entry["targetPath"], "A")
        staged_executable = self.base / "out-a" / self.target.executable
        self.assertTrue(staged_executable.is_file())

    def test_traversal_and_absolute_entries_are_rejected(self) -> None:
        for name in ("chrome-mac-arm64/../escape", "/etc/passwd"):
            archive, digest = self._archive({**_valid_entries(), name: b"x"})
            with self.assertRaises(StagingRejected):
                build_staging(
                    contract=self.contract,
                    target_id="macos-arm64",
                    archive_path=archive,
                    archive_sha256=digest,
                    output=self.base / f"out-{abs(hash(name))}",
                )

    def test_escaping_and_absolute_symlinks_are_rejected(self) -> None:
        cases = {
            "chrome-mac-arm64/evil-abs": ("symlink", "/etc/passwd"),
            "chrome-mac-arm64/evil-up": ("symlink", "../../outside"),
        }
        for name, payload in cases.items():
            archive, digest = self._archive({**_valid_entries(), name: payload})
            with self.assertRaises(StagingRejected):
                build_staging(
                    contract=self.contract,
                    target_id="macos-arm64",
                    archive_path=archive,
                    archive_sha256=digest,
                    output=self.base / f"out-{abs(hash(name))}",
                )

    def test_unexpected_root_entry_is_rejected(self) -> None:
        archive, digest = self._archive({**_valid_entries(), "second-root/file": b"x"})
        with self.assertRaises(StagingRejected):
            build_staging(
                contract=self.contract,
                target_id="macos-arm64",
                archive_path=archive,
                archive_sha256=digest,
                output=self.base / "out-root",
            )

    def test_forbidden_second_browser_entries_are_rejected(self) -> None:
        archive, digest = self._archive(
            {**_valid_entries(), "chrome-mac-arm64/chrome-headless-shell": b"x"}
        )
        with self.assertRaises(StagingRejected):
            build_staging(
                contract=self.contract,
                target_id="macos-arm64",
                archive_path=archive,
                archive_sha256=digest,
                output=self.base / "out-forbidden",
            )

    def test_missing_executable_is_rejected(self) -> None:
        entries = _valid_entries()
        entries.pop(
            "chrome-mac-arm64/Google Chrome for Testing.app"
            "/Contents/MacOS/Google Chrome for Testing"
        )
        archive, digest = self._archive(entries)
        with self.assertRaises(StagingRejected):
            build_staging(
                contract=self.contract,
                target_id="macos-arm64",
                archive_path=archive,
                archive_sha256=digest,
                output=self.base / "out-noexec",
            )

    def test_existing_output_directory_is_rejected(self) -> None:
        archive, digest = self._archive(_valid_entries())
        output = self.base / "out-exists"
        output.mkdir()
        with self.assertRaises(StagingRejected):
            build_staging(
                contract=self.contract,
                target_id="macos-arm64",
                archive_path=archive,
                archive_sha256=digest,
                output=output,
            )

    def test_windows_target_is_declared_but_not_buildable_yet(self) -> None:
        archive, digest = self._archive(_valid_entries())
        with self.assertRaises(StagingRejected):
            build_staging(
                contract=self.contract,
                target_id="windows-x86_64",
                archive_path=archive,
                archive_sha256=digest,
                output=self.base / "out-win",
            )

    def test_safe_extract_rejects_duplicate_entries(self) -> None:
        archive = self.base / "dup.zip"
        with zipfile.ZipFile(archive, "w") as handle:
            handle.writestr("chrome-mac-arm64/a", b"1")
            handle.writestr("chrome-mac-arm64/a", b"2")
        destination = self.base / "dup-out"
        with self.assertRaises(StagingRejected):
            safe_extract(archive, destination, root_entry="chrome-mac-arm64")

    def test_manifest_generation_covers_all_files(self) -> None:
        root = self.base / "tree/chrome-mac-arm64"
        (root / "sub").mkdir(parents=True)
        (root / "sub/file").write_bytes(b"payload")
        manifest = generate_manifest(self.base / "tree", root_entry="chrome-mac-arm64")
        self.assertEqual(len(manifest), 1)
        self.assertEqual(manifest[0]["path"], "chrome-mac-arm64/sub/file")
        self.assertEqual(manifest[0]["size"], 7)


if __name__ == "__main__":
    unittest.main()
