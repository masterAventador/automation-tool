"""Joining rendered segments into one film, and why the join is measured.

Route A renders each part on its own stage and joins the results. The parts do
not agree on a stage: the frozen catalog declares 1920x1080 for 105 of them,
1080x1920 for three and 1440x2560 for one. So a film has a canvas, and a
segment that was not rendered on it has to be brought onto it before the join.

Why the join is verified by measuring the product
-------------------------------------------------
Measured 2026-07-28 with the packaged ffmpeg, concatenating a 1920x1080 segment
with a 1080x1920 one through the concat demuxer and `-c copy`:

    exit code            0
    nb_read_frames       279      = 144 + 135, exactly right
    format duration      9.300000 = 4.8 + 4.5, exactly right
    stream width/height  1920x1080

Every cheap check passes. The file is broken anyway — the second half is
portrait content in a container that claims landscape, and only looking at the
pixels shows it. So nothing here treats an exit code or a frame count as
evidence that a join succeeded; the segments are checked before, and the result
is checked after.
"""

from __future__ import annotations

import pytest

from automation_tool.executor.motion_authoring.segment_concat import (
    FilmCanvas,
    SegmentMismatch,
    SegmentStream,
    concat_listing,
    join_segments,
    normalisation_filter,
    require_joinable,
)

CANVAS = FilmCanvas(width=1920, height=1080, frames_per_second=30)


def stream(**overrides: object) -> SegmentStream:
    fields: dict[str, object] = {
        "width": 1920,
        "height": 1080,
        "frames_per_second": 30,
        "pixel_format": "yuv420p",
        "frames": 144,
    }
    fields.update(overrides)
    return SegmentStream(**fields)  # type: ignore[arg-type]


def test_a_segment_already_on_the_canvas_needs_no_filter() -> None:
    assert normalisation_filter(stream(), CANVAS) is None


def test_a_portrait_segment_is_letterboxed_rather_than_stretched() -> None:
    """The part was laid out for its own stage; stretching it is a design change.

    `force_original_aspect_ratio=decrease` then a centred pad keeps every
    proportion the part was designed with and fills the rest. `setsar=1` is not
    decoration: without it the padded stream carries the source's sample aspect
    ratio and a player stretches the result back.
    """
    filter_chain = normalisation_filter(stream(width=1080, height=1920), CANVAS)

    assert filter_chain is not None
    assert "force_original_aspect_ratio=decrease" in filter_chain
    assert "pad=1920:1080" in filter_chain
    assert "setsar=1" in filter_chain


def test_a_segment_at_another_frame_rate_is_normalised_too() -> None:
    """A joined film has one frame rate; the demuxer will not reconcile two."""
    assert normalisation_filter(stream(frames_per_second=24), CANVAS) is not None


def test_a_segment_in_another_pixel_format_is_normalised_too() -> None:
    assert normalisation_filter(stream(pixel_format="yuv444p"), CANVAS) is not None


def test_segments_that_all_match_are_joinable() -> None:
    total = require_joinable([stream(), stream(frames=180)], CANVAS)

    assert total == 324


def test_a_segment_off_the_canvas_is_refused_rather_than_copied() -> None:
    """The measured silent failure: `-c copy` would produce a plausible file.

    This is the assertion that stands between a rendered film and the one
    measured above — right frame count, right duration, wrong pixels.
    """
    with pytest.raises(SegmentMismatch):
        require_joinable([stream(), stream(width=1080, height=1920)], CANVAS)


def test_a_segment_with_no_frames_is_refused() -> None:
    with pytest.raises(SegmentMismatch):
        require_joinable([stream(frames=0)], CANVAS)


def test_an_empty_film_is_refused() -> None:
    with pytest.raises(SegmentMismatch):
        require_joinable([], CANVAS)


# --- 执行层：真正调 ffmpeg，并且不相信它的退出码 -----------------------------


def _stub(path, body: str):
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_the_join_is_measured_afterwards_not_trusted(tmp_path) -> None:
    """The silent failure this module exists for, expressed as a test.

    A stub ffmpeg that exits 0 and writes something, with a probe that reports a
    frame count other than the sum, is exactly the shape measured on 2026-07-28
    with the real toolchain: exit code 0, plausible file, wrong pixels. If the
    product is not re-measured, that file ships.
    """
    segments = [tmp_path / "a.mp4", tmp_path / "b.mp4"]
    for segment in segments:
        segment.write_bytes(b"segment")
    ffmpeg = _stub(
        tmp_path / "ffmpeg",
        'for value do output="$value"; done; touch "$output"; exit 0',
    )
    ffprobe = _stub(
        tmp_path / "ffprobe",
        'echo \'{"streams":[{"width":1920,"height":1080,"avg_frame_rate":"30/1",'
        '"pix_fmt":"yuv420p","nb_read_frames":"1"}]}\'',
    )
    output = tmp_path / "film.mp4"

    with pytest.raises(SegmentMismatch) as failure:
        join_segments(
            segments,
            output,
            canvas=CANVAS,
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
            expected_frames=324,
        )

    assert "324" in str(failure.value)
    assert output.is_file(), "the fake ffmpeg must write the requested output"


def test_a_concat_list_quotes_every_path_the_demuxer_reads(tmp_path) -> None:
    """`-safe 0` accepts absolute paths; a single quote in one would end the entry."""
    listing = concat_listing([tmp_path / "a b.mp4", tmp_path / "c'd.mp4"])

    assert listing.splitlines()[0] == f"file '{tmp_path / 'a b.mp4'}'"
    assert "'\\''" in listing.splitlines()[1]
