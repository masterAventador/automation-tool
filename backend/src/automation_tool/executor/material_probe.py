"""Local probing of one source file: what it is, whether it has usable audio, what it hashes to.

The facts produced here fill a `control_plane.domain.material.Material`, but this
module deliberately does not import it: the executor never depends on the
product layer (`CLAUDE.md` §4.3), and `Material` carries no path, which is what
keeps the operator's private paths off the Control Plane. The path-to-id mapping
stays on this side of the boundary.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final, Never

from automation_tool.protocol.safe_text import contains_control_or_bidi

MAX_TOOL_PATH_CHARACTERS: Final = 4096


class MaterialProbeRejection(StrEnum):
    """Why one file cannot become a material.

    A single opaque failure would leave the material library able to say only
    "probe failed", so the one action left to the user is to retry every file in
    turn. Each member names a different next step.
    """

    UNREADABLE = "unreadable"
    UNSAFE_PATH = "unsafe_path"


class MaterialProbeRejected(RuntimeError):
    """Carries a closed reason code; the message stays fixed and path-free.

    `ffprobe` and `ffmpeg` both name the offending file in their diagnostics, so
    neither their output nor the path may reach this message (`CLAUDE.md` §7).
    The reason is an enum rather than free text precisely so it can be surfaced
    without becoming a leak channel.
    """

    def __init__(self, rejection: MaterialProbeRejection) -> None:
        super().__init__("material probe rejected")
        self.rejection = rejection


def _reject(rejection: MaterialProbeRejection) -> Never:
    raise MaterialProbeRejected(rejection)


def _has_symlink_component(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            return True
    return False


def _require_tool(path: object) -> None:
    """Re-check a path the Rust caller already authorized.

    This mirrors `browser_runtime._require_path`, which guards the packaged
    Chromium the same way. It is a re-check, not a search: nothing here looks
    the tool up, so there is no `PATH` lookup to fall back to and no environment
    variable that could redirect a test build somewhere a shipped build would
    never look.
    """
    if not isinstance(path, Path):
        _reject(MaterialProbeRejection.UNSAFE_PATH)
    encoded = os.fspath(path)
    if (
        not path.is_absolute()
        or len(encoded) > MAX_TOOL_PATH_CHARACTERS
        or contains_control_or_bidi(encoded)
        or _has_symlink_component(path)
    ):
        _reject(MaterialProbeRejection.UNSAFE_PATH)
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError:
        _reject(MaterialProbeRejection.UNREADABLE)
    if not stat.S_ISREG(metadata.st_mode) or not os.access(path, os.X_OK):
        _reject(MaterialProbeRejection.UNREADABLE)


@dataclass(frozen=True, slots=True, repr=False)
class PackagedMediaTools:
    """Paths to the packaged FFmpeg pair, already verified by the Rust caller.

    Rust resolves these from the App's resource directory and checks the
    toolchain manifest, sizes and SHA-256 digests before handing them over, the
    same way it hands over the packaged Chromium. Production and tests run this
    one constructor; only the value differs.
    """

    ffprobe_path: Path
    ffmpeg_path: Path

    def __post_init__(self) -> None:
        self.revalidate()

    def revalidate(self) -> None:
        """Re-check both tools immediately before use.

        Validity at construction says nothing about validity now — the packaged
        files can be removed or replaced between the two moments.
        """
        _require_tool(self.ffprobe_path)
        _require_tool(self.ffmpeg_path)

    def __repr__(self) -> str:
        return "PackagedMediaTools(<redacted>)"


__all__ = [
    "MAX_TOOL_PATH_CHARACTERS",
    "MaterialProbeRejected",
    "MaterialProbeRejection",
    "PackagedMediaTools",
]
