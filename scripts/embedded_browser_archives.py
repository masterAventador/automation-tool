#!/usr/bin/env python3
"""Locate the digest-locked Chromium archives the acceptance scripts stage from.

The archives are large and pinned by digest, so they are downloaded once per
machine into the project-scoped artifact cache — the same directory that
already holds `media-toolchain`, the two video Workers, the subtitle fonts and
the VAD model. Every checkout on the machine reads that one copy.

They used to live inside the checkout's `.local/`, which forced every consumer
to know whether it was running in the primary checkout or in a `wt/<task>`
worktree whose own `.local/` is empty. Early scripts (BU-02, EB-06, EB-07) only
ever looked two levels up and reported "locked archive not downloaded yet"
while the archive sat right there; later ones each grew a private
two-candidate lookup. `FIX-embedded-browser-archive-lookup.md` records the
cost. A per-machine cache removes the question rather than answering it once
more: there is no second candidate to get wrong.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Final

SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from video_runtime_cache import cache_root  # noqa: E402

ARCHIVE_DIRECTORY_NAME: Final = "embedded-browser-archives"
MACOS_ARM64_ARCHIVE: Final = "chrome-mac-arm64.zip"
WINDOWS_X86_64_ARCHIVE: Final = "chrome-win64.zip"


def archive_root() -> Path:
    """Return the one directory on this machine that holds browser archives."""
    return cache_root() / ARCHIVE_DIRECTORY_NAME


def archive_path(name: str) -> Path:
    """Return where the named archive lives, whether or not it is downloaded.

    A missing file still resolves, so callers report the location an operator
    should download into rather than a path that depends on where they ran.
    """
    return archive_root() / name


def default_archives() -> dict[str, Path]:
    """Return the locked archive path for every staging target."""
    return {
        "macos-arm64": archive_path(MACOS_ARM64_ARCHIVE),
        "windows-x86_64": archive_path(WINDOWS_X86_64_ARCHIVE),
    }


__all__ = [
    "ARCHIVE_DIRECTORY_NAME",
    "MACOS_ARM64_ARCHIVE",
    "WINDOWS_X86_64_ARCHIVE",
    "archive_path",
    "archive_root",
    "default_archives",
]
