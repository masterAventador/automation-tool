"""Bring rendered segments onto one canvas and say whether they can be joined.

Route A renders each catalog part on the stage that part declares, then joins
the results. The parts do not agree on a stage — the frozen catalog declares
1920x1080 for 105 of them, 1080x1920 for three and 1440x2560 for one — so a
film has a canvas of its own and a segment rendered elsewhere has to be brought
onto it first.

Why the decision is made here rather than read off an exit code
---------------------------------------------------------------
Measured 2026-07-28 with the packaged ffmpeg: concatenating a 1920x1080 segment
with a 1080x1920 one through the concat demuxer and `-c copy` exits 0, and the
result reports 279 frames and 9.300000 seconds — both exactly the sums. The
file is broken anyway: the second half is portrait content in a container
claiming landscape, visible only in the pixels. So this module refuses the join
before it happens, and the caller measures the product afterwards. Neither the
exit code nor the frame count is treated as evidence.

(English docstring for the reason `part_document.py` gives:
`check_user_facing_branding.py` reads Chinese-bearing literals in a `.py` source
as operator copy.)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Sequence


@dataclass(frozen=True, slots=True)
class FilmCanvas:
    """What the finished film is, in pixels and frames per second."""

    width: int
    height: int
    frames_per_second: int


@dataclass(frozen=True, slots=True)
class SegmentStream:
    """One rendered segment as `ffprobe` reports it.

    Read from the encoded file rather than carried forward from the render
    request: what a segment *is* and what it was *asked to be* are two claims,
    and only the first one gets joined.
    """

    width: int
    height: int
    frames_per_second: int
    pixel_format: str
    frames: int


# What the joined film is encoded as. One value, because the concat demuxer
# reconciles nothing: two segments in different pixel formats produce a file
# whose second half decodes wrong.
FILM_PIXEL_FORMAT: Final = "yuv420p"


class SegmentMismatch(RuntimeError):
    """A segment cannot be joined as it stands.

    Raised before ffmpeg is asked to join anything. Not a repairable condition
    at this level: the caller either normalises the segment first or has a
    defect upstream, and joining anyway produces the plausible-looking file this
    module's docstring measures.
    """


def normalisation_filter(stream: SegmentStream, canvas: FilmCanvas) -> str | None:
    """The filter chain that brings one segment onto the canvas, or None.

    `None` means the segment is already there and can be stream-copied, which is
    what keeps a film of same-stage parts from being re-encoded for nothing.

    The scale keeps the part's own proportions and pads the remainder rather
    than stretching: the part was laid out for the stage it declares, and
    stretching it is a design change nobody asked for. `setsar=1` is required
    rather than tidy — without it the padded stream carries the source's sample
    aspect ratio and a player stretches the result back.
    """
    if (
        stream.width == canvas.width
        and stream.height == canvas.height
        and stream.frames_per_second == canvas.frames_per_second
        and stream.pixel_format == FILM_PIXEL_FORMAT
    ):
        return None
    return (
        f"scale={canvas.width}:{canvas.height}:force_original_aspect_ratio=decrease,"
        f"pad={canvas.width}:{canvas.height}:(ow-iw)/2:(oh-ih)/2:color=black,"
        "setsar=1"
    )


def require_joinable(
    streams: Sequence[SegmentStream], canvas: FilmCanvas
) -> int:
    """The film's total frame count, or a refusal naming what is off-canvas."""
    if not streams:
        raise SegmentMismatch("a film needs at least one segment")
    total = 0
    for position, stream in enumerate(streams):
        if stream.frames <= 0:
            raise SegmentMismatch(f"segment {position} carries no frames")
        if normalisation_filter(stream, canvas) is not None:
            raise SegmentMismatch(
                f"segment {position} is {stream.width}x{stream.height} at "
                f"{stream.frames_per_second}fps {stream.pixel_format}, not the "
                f"film's {canvas.width}x{canvas.height} at "
                f"{canvas.frames_per_second}fps {FILM_PIXEL_FORMAT}"
            )
        total += stream.frames
    return total


__all__ = [
    "FILM_PIXEL_FORMAT",
    "FilmCanvas",
    "SegmentMismatch",
    "SegmentStream",
    "normalisation_filter",
    "require_joinable",
]
