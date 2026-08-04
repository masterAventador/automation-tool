#!/usr/bin/env python3
"""Tests for locating the digest-locked Chromium archives.

Why this exists: the archives used to live inside the checkout's `.local/`,
which made every consumer responsible for knowing whether it was running in
the primary checkout or in a `wt/<task>` worktree whose own `.local/` is
empty. `FIX-embedded-browser-archive-lookup.md` records what that cost — three
early scripts only ever looked two levels up and reported "not downloaded yet"
while the archive sat right there in the repository.

Every other pinned third-party input on this machine already lives in the
project-scoped artifact cache next to `media-toolchain` and the two video
Workers. Putting the browser archives there removes the worktree question
entirely: one download per machine, shared by every checkout, and no path that
depends on where the caller happens to be running from.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from embedded_browser_archives import (  # noqa: E402
    MACOS_ARM64_ARCHIVE,
    WINDOWS_X86_64_ARCHIVE,
    archive_path,
    default_archives,
)
from video_runtime_cache import cache_root  # noqa: E402


class ArchivePathTests(unittest.TestCase):
    def test_archive_lives_in_the_machine_artifact_cache(self) -> None:
        resolved = archive_path(MACOS_ARM64_ARCHIVE)

        self.assertTrue(
            resolved.is_relative_to(cache_root()),
            f"{resolved} must sit under the shared artifact cache {cache_root()}",
        )

    def test_archive_never_resolves_inside_a_checkout(self) -> None:
        # The whole point of the move: a worktree and the primary checkout must
        # resolve to the same file, so the answer cannot mention either of them.
        resolved = archive_path(WINDOWS_X86_64_ARCHIVE)

        self.assertFalse(
            resolved.is_relative_to(ROOT),
            f"{resolved} must not depend on which checkout the caller runs from",
        )

    def test_each_target_keeps_its_own_archive_name(self) -> None:
        self.assertEqual(archive_path(MACOS_ARM64_ARCHIVE).name, "chrome-mac-arm64.zip")
        self.assertEqual(archive_path(WINDOWS_X86_64_ARCHIVE).name, "chrome-win64.zip")

    def test_cache_override_moves_the_archives_with_it(self) -> None:
        # `AUTOMATION_TOOL_BUILD_CACHE` already relocates every other pinned
        # artifact. An archive that ignored it would split one machine's cache
        # into two halves that disagree about where things are.
        previous = os.environ.get("AUTOMATION_TOOL_BUILD_CACHE")
        os.environ["AUTOMATION_TOOL_BUILD_CACHE"] = "/tmp/automation-tool-cache-probe"
        try:
            resolved = archive_path(MACOS_ARM64_ARCHIVE)
        finally:
            if previous is None:
                del os.environ["AUTOMATION_TOOL_BUILD_CACHE"]
            else:
                os.environ["AUTOMATION_TOOL_BUILD_CACHE"] = previous

        self.assertTrue(
            resolved.is_relative_to(Path("/tmp/automation-tool-cache-probe")),
            f"{resolved} ignored the cache override",
        )


class DefaultArchivesTests(unittest.TestCase):
    def test_every_staging_target_resolves_into_the_cache(self) -> None:
        archives = default_archives()

        self.assertEqual(set(archives), {"macos-arm64", "windows-x86_64"})
        for target, path in archives.items():
            self.assertTrue(
                path.is_relative_to(cache_root()),
                f"{target} resolved to {path}, outside the artifact cache",
            )


if __name__ == "__main__":
    unittest.main()
