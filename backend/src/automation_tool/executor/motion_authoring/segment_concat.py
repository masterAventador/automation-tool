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

import json
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final


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
    return canvas_filter(canvas)


def canvas_filter(canvas: FilmCanvas) -> str:
    """The filter chain that puts any source onto this canvas."""
    return (
        f"scale={canvas.width}:{canvas.height}:force_original_aspect_ratio=decrease,"
        f"pad={canvas.width}:{canvas.height}:(ow-iw)/2:(oh-ih)/2:color=black,"
        "setsar=1"
    )


def require_joinable(streams: Sequence[SegmentStream], canvas: FilmCanvas) -> int:
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


def probe_segment(path: Path, *, ffprobe: Path) -> SegmentStream:
    """What this file actually is, asked of the same toolchain that made it.

    `-count_frames` rather than the container's frame count: the container is a
    claim and the decoded stream is the fact, and PC-06's whole reason for
    existing is that the two disagree without saying so.
    """
    completed = subprocess.run(
        [
            str(ffprobe),
            "-v",
            "error",
            "-count_frames",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,avg_frame_rate,pix_fmt,nb_read_frames",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise SegmentMismatch(f"ffprobe could not read a segment: {path.name}")
    try:
        stream = json.loads(completed.stdout)["streams"][0]
        numerator, denominator = str(stream["avg_frame_rate"]).split("/")
        rate = int(numerator) // int(denominator) if int(denominator) else 0
        return SegmentStream(
            width=int(stream["width"]),
            height=int(stream["height"]),
            frames_per_second=rate,
            pixel_format=str(stream["pix_fmt"]),
            frames=int(stream["nb_read_frames"]),
        )
    except (KeyError, IndexError, ValueError, ZeroDivisionError) as error:
        raise SegmentMismatch(f"ffprobe answered something unreadable about {path.name}") from error


def concat_listing(segments: Sequence[Path]) -> str:
    """The demuxer's list file.

    `-safe 0` lets the list carry absolute paths, which means a path is data the
    demuxer parses: a single quote inside one would close the entry early. The
    demuxer's escape for that is `'\''`, the same one a POSIX shell uses.
    """
    lines = []
    for segment in segments:
        escaped = str(segment).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    return "\n".join(lines) + "\n"


def normalise_segment(source: Path, destination: Path, *, canvas: FilmCanvas, ffmpeg: Path) -> None:
    """Re-encode one segment onto the canvas."""
    completed = subprocess.run(
        [
            str(ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-i",
            str(source),
            "-vf",
            canvas_filter(canvas),
            "-r",
            str(canvas.frames_per_second),
            "-c:v",
            "libx264",
            "-pix_fmt",
            FILM_PIXEL_FORMAT,
            str(destination),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise SegmentMismatch(f"ffmpeg could not bring {source.name} onto the canvas")


def join_segments(
    segments: Sequence[Path],
    output: Path,
    *,
    canvas: FilmCanvas,
    ffmpeg: Path,
    ffprobe: Path,
    expected_frames: int,
) -> SegmentStream:
    """Join segments already on the canvas, then measure what came out.

    The measurement is the point. Concatenating a 1920x1080 segment with a
    1080x1920 one exits 0 and reports both the right frame count and the right
    duration while being broken; the only thing that catches it is asking the
    finished file what it is and comparing that to what was asked for.
    """
    if not segments:
        raise SegmentMismatch("a film needs at least one segment")
    listing = output.parent / f"{output.stem}.concat.txt"
    listing.write_text(concat_listing(segments), encoding="utf-8")
    completed = subprocess.run(
        [
            str(ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(listing),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise SegmentMismatch("ffmpeg could not join the segments")
    joined = probe_segment(output, ffprobe=ffprobe)
    if joined.frames != expected_frames:
        raise SegmentMismatch(
            f"the joined film carries {joined.frames} frames, not the "
            f"{expected_frames} its segments account for"
        )
    if normalisation_filter(joined, canvas) is not None:
        raise SegmentMismatch(
            f"the joined film is {joined.width}x{joined.height} at "
            f"{joined.frames_per_second}fps {joined.pixel_format}, not the film's canvas"
        )
    return joined


__all__ = [
    "FILM_PIXEL_FORMAT",
    "FilmCanvas",
    "SegmentMismatch",
    "SegmentStream",
    "canvas_filter",
    "concat_listing",
    "join_segments",
    "normalisation_filter",
    "normalise_segment",
    "probe_segment",
    "require_joinable",
]
