#!/usr/bin/env python3
"""One unpacked Chromium per machine, shared by dev builds and releases.

Both sides already unpacked the same locked archive with the same
`build_staging()` and the same contract — but into two different places: the
desktop-E2E cache under `.local/desktop-e2e/`, and a fresh directory on every
release run. Same inputs, same code, two copies, and only the digest manifest
standing between them and drifting apart unnoticed.

Unpacking is not cheap (a 171 MB archive into 328 files, each digested), and
"two copies that should be identical" is exactly the shape of the problems this
repository keeps paying for. So it becomes one cache, keyed on the staging
contract, which pins `archive_sha256` per target — swap the archive and the key
changes with it.

The release still signs every Mach-O and re-takes the inventory afterwards, so
what it ships genuinely differs from the cache. That work happens on its own
copy: the cache holds the pre-signature tree only, and nothing signs in place.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from embedded_browser_staging_cache import (  # noqa: E402
    STAGING_CONTRACT_PATH,
    cache_name,
    ensure_staged_browser,
)
from video_runtime_cache import cache_root  # noqa: E402


class CacheLocationTests(unittest.TestCase):
    def test_cache_lives_beside_the_other_pinned_artifacts(self) -> None:
        staged = ensure_staged_browser(target_id="macos-arm64")

        self.assertTrue(
            staged.is_relative_to(cache_root()),
            f"{staged} must sit in the shared machine artifact cache",
        )

    def test_each_target_gets_its_own_cache_entry(self) -> None:
        self.assertNotEqual(
            cache_name("macos-arm64"),
            cache_name("windows-x86_64"),
            "two targets sharing one cache entry would overwrite each other",
        )

    def test_the_staging_contract_is_the_cache_key(self) -> None:
        # It pins `archive_sha256` per target, so a swapped archive changes the
        # key. A cache keyed on anything less would serve the previous browser.
        self.assertTrue(STAGING_CONTRACT_PATH.is_file())
        text = STAGING_CONTRACT_PATH.read_text(encoding="utf-8")
        self.assertIn("archive_sha256", text)


class CacheContentTests(unittest.TestCase):
    def test_the_cached_tree_is_a_verified_distribution(self) -> None:
        staged = ensure_staged_browser(target_id="macos-arm64")

        manifest = staged / "distribution-manifest.v1.json"
        self.assertTrue(manifest.is_file(), "the cache has no distribution manifest")
        files = sum(1 for path in staged.rglob("*") if path.is_file())
        self.assertGreater(files, 300, f"implausibly small Chromium tree: {files} files")

    def test_second_call_reuses_the_tree_rather_than_unpacking_again(self) -> None:
        first = ensure_staged_browser(target_id="macos-arm64")
        marker = first / "distribution-manifest.v1.json"
        before = marker.stat().st_mtime_ns

        second = ensure_staged_browser(target_id="macos-arm64")

        self.assertEqual(first, second)
        self.assertEqual(
            before,
            marker.stat().st_mtime_ns,
            "the cache was rebuilt even though its key had not changed",
        )


class CopyTests(unittest.TestCase):
    def test_a_copy_preserves_symlinks_and_still_verifies(self) -> None:
        """Chrome's framework is a symlink tree; dereferencing it breaks it.

        `shutil.copytree` follows symlinks by default, which turns the 330-file
        distribution into 848 real files and makes `verify_distribution` report
        "symlink entry drifted". The manifest declares those links, so a copy
        that flattens them is not the distribution any more — and the release
        signs and ships whatever it copied.
        """
        import tempfile

        from build_embedded_browser_distribution import verify_distribution
        from embedded_browser_staging_cache import copy_staged_browser

        cached = ensure_staged_browser(target_id="macos-arm64")
        cached_files = sum(1 for path in cached.rglob("*") if path.is_file())

        with tempfile.TemporaryDirectory(prefix="staged-copy-") as temporary:
            output = Path(temporary) / "browser-staging"
            copy_staged_browser(target_id="macos-arm64", output=output)

            copied = sum(1 for path in output.rglob("*") if path.is_file())
            self.assertEqual(
                cached_files, copied, "the copy did not preserve the tree shape"
            )
            report = verify_distribution(staging=output, target_id="macos-arm64")
            self.assertGreater(report.verified_files, 300)


if __name__ == "__main__":
    unittest.main()
