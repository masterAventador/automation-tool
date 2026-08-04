#!/usr/bin/env python3
"""Locate the digest-locked Chromium archives the acceptance scripts stage from.

The archives are large, so they are downloaded once into the primary
checkout's `.local/` and reused. An acceptance script may run either from that
primary checkout or from a `wt/<task>` worktree, whose own `.local/` is empty —
from a worktree the primary checkout is exactly two levels up.

Early scripts (BU-02, EB-06, EB-07) only ever looked two levels up, so they
failed in the primary checkout itself with "locked archive not downloaded yet"
while the archive sat right there. Later ones each grew their own private copy
of a two-candidate lookup. This is that lookup, once.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

MACOS_ARM64_ARCHIVE: Final = (
    ".local/embedded-browser-video-studio/eb-03-cache/chrome-mac-arm64.zip"
)
WINDOWS_X86_64_ARCHIVE: Final = ".local/eb-04-windows/chrome-win64.zip"


def archive_path(root: Path, relative: str) -> Path:
    """Return the archive location, checking the checkout then its parent.

    When neither exists the primary-checkout path is returned so the caller
    reports the location an operator should download into, rather than the
    worktree fallback.
    """
    primary = root / relative
    for candidate in (primary, root.parent.parent / relative):
        if candidate.is_file():
            return candidate
    return primary


def default_archives(root: Path) -> dict[str, Path]:
    """Return the locked archive path for every staging target."""
    return {
        "macos-arm64": archive_path(root, MACOS_ARM64_ARCHIVE),
        "windows-x86_64": archive_path(root, WINDOWS_X86_64_ARCHIVE),
    }


__all__ = [
    "MACOS_ARM64_ARCHIVE",
    "WINDOWS_X86_64_ARCHIVE",
    "archive_path",
    "default_archives",
]
