#!/usr/bin/env python3
"""Every locked static asset ships in this repository, not from the network.

Fonts, the draco decoder and the pinned scripts are immutable files. Unlike
ffmpeg they are not compiled per machine, and unlike the Chromium archive they
are small. Downloading them on every clean machine bought nothing and cost a
real dependency: the Windows box cannot reach `fonts.gstatic.com` at all (TLS
handshake failure), so its build only ever worked because somebody built on the
Mac and copied the tree across by hand.

These tests hold the line in both directions — the bytes are here, and the
build genuinely does not reach for the network to get them.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from subtitle_font_assets import (  # noqa: E402
    ASSET_RIGHTS_PATH,
    bundled_subtitle_fonts,
    ensure_subtitle_fonts,
    packaged_license_notices,
)

OFFLINE_MOTION_LOCK = ROOT / "contracts/video/offline-motion-dependencies.v1.json"


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class NetworkUsed(AssertionError):
    """Raised by the injected fetcher: reaching the network is the failure."""


class OfflineMotionDependencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.lock = json.loads(OFFLINE_MOTION_LOCK.read_text(encoding="utf-8"))
        self.download_root = ROOT / self.lock["layout"]["downloadRoot"]

    def test_download_root_is_inside_the_repository_and_tracked(self) -> None:
        # `.local/` is git-ignored, so a downloadRoot pointing there can never
        # be committed no matter how many bytes sit in it.
        relative = Path(self.lock["layout"]["downloadRoot"])
        self.assertFalse(
            relative.parts[0] == ".local",
            "the download root must be a committed directory, not .local/",
        )

    def test_every_locked_artifact_is_committed_with_its_locked_digest(self) -> None:
        records = list(self.lock["artifacts"]) + [
            font["source"] for font in self.lock.get("builtFonts", [])
        ]
        self.assertGreater(len(records), 50, "the lock lost its artifact inventory")

        dependency_root = self.lock["layout"]["dependencyRoot"]
        missing: list[str] = []
        mismatched: list[str] = []
        for artifact in records:
            local = artifact["localPath"]
            prefix = f"{dependency_root}/"
            relative = local[len(prefix):] if local.startswith(prefix) else local
            path = self.download_root / relative
            if not path.is_file():
                missing.append(relative)
                continue
            if digest(path.read_bytes()) != artifact["sha256"]:
                mismatched.append(relative)

        self.assertEqual(missing, [], "locked artifacts are not committed")
        self.assertEqual(mismatched, [], "committed artifacts drifted from the lock")


class SubtitleFontTests(unittest.TestCase):
    def test_fonts_build_without_touching_the_network(self) -> None:
        def boom(url: str) -> bytes:
            raise NetworkUsed(f"the build tried to download {url}")

        with tempfile.TemporaryDirectory(prefix="committed-fonts-") as temporary:
            cached = ensure_subtitle_fonts(root=Path(temporary), fetch=boom)

            rights = json.loads(ASSET_RIGHTS_PATH.read_text(encoding="utf-8"))
            expected = [font.packaged_name for font in bundled_subtitle_fonts(rights)]
            expected += [
                notice.packaged_name for notice in packaged_license_notices(rights)
            ]
            for name in expected:
                self.assertTrue(
                    (cached / name).is_file(), f"{name} missing from the built bundle"
                )


if __name__ == "__main__":
    unittest.main()
