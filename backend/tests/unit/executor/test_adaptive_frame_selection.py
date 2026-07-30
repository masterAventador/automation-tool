from __future__ import annotations

import tracemalloc
from pathlib import Path
from typing import Any

import pytest

from automation_tool.executor import adaptive_frame_extraction
from automation_tool.executor.adaptive_frame_extraction import (
    BoundedFfmpegOutput,
    extract_adaptive_frame_candidates,
)
from automation_tool.executor.material_probe import PackagedMediaTools, approve_source


@pytest.mark.parametrize(
    ("duration_ms", "expected"),
    [
        (1, 6),
        (15_000, 6),
        (15_001, 12),
        (60_000, 12),
        (60_001, 24),
        (300_000, 24),
        (300_001, 40),
        (1_200_000, 40),
        (1_200_001, 60),
        (14_400_000, 60),
    ],
)
def test_frame_limit_uses_the_shorter_tier_at_each_duration_boundary(
    duration_ms: int,
    expected: int,
) -> None:
    assert adaptive_frame_extraction._frame_limit(duration_ms) == expected


def test_selection_keeps_all_scene_cuts_then_uniformly_fills_the_remaining_slots() -> None:
    selected_scenes, selected_supplements = adaptive_frame_extraction._select_candidate_timestamps(
        (0, 1_000, 2_000, 3_000),
        (4_000, 5_000, 6_000, 7_000, 8_000),
        duration_ms=15_000,
    )

    assert selected_scenes == (0, 1_000, 2_000, 3_000)
    assert selected_supplements == (4_000, 8_000)


def test_one_remaining_supplement_slot_selects_nearest_to_the_time_midpoint() -> None:
    selected_scenes, selected_supplements = adaptive_frame_extraction._select_candidate_timestamps(
        (0, 3_707, 5_563, 6_003, 11_082, 23_663, 48_233, 53_017, 54_756, 55_644, 56_565),
        (19_082, 31_663, 39_663, 47_663),
        duration_ms=60_000,
    )

    assert len(selected_scenes) == 11
    assert selected_supplements == (31_663,)


def test_too_many_scene_cuts_are_uniformly_sampled_with_first_and_tail_retained() -> None:
    selected_scenes, selected_supplements = adaptive_frame_extraction._select_candidate_timestamps(
        tuple(range(0, 10_000, 1_000)),
        (10_000, 11_000),
        duration_ms=15_000,
    )

    assert selected_scenes == (0, 2_000, 4_000, 5_000, 7_000, 9_000)
    assert selected_supplements == ()


def test_scene_cut_sampling_follows_time_not_dense_candidate_indices() -> None:
    selected_scenes, selected_supplements = adaptive_frame_extraction._select_candidate_timestamps(
        (
            0,
            100,
            200,
            300,
            400,
            500,
            600,
            700,
            800,
            900,
            1_000,
            3_000,
            6_000,
            9_000,
            12_000,
            14_999,
        ),
        (),
        duration_ms=15_000,
    )

    assert selected_scenes == (0, 3_000, 6_000, 9_000, 12_000, 14_999)
    assert selected_supplements == ()


def test_scene_cut_sampling_assigns_all_time_targets_globally() -> None:
    selected_scenes, selected_supplements = adaptive_frame_extraction._select_candidate_timestamps(
        (0, 232, 5_721, 8_892, 11_752, 14_845, 14_999),
        (),
        duration_ms=15_000,
    )

    assert selected_scenes == (0, 232, 5_721, 8_892, 11_752, 14_999)
    assert selected_supplements == ()


def test_global_scene_cut_matching_keeps_tracking_memory_bounded() -> None:
    timestamps = tuple(range(10_000))

    tracemalloc.start()
    try:
        selected_indices = adaptive_frame_extraction._globally_uniform_indices(timestamps, 60)
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert len(selected_indices) == 60
    assert selected_indices[0] == 0
    assert selected_indices[-1] == len(timestamps) - 1
    assert peak_bytes < 5 * 1024 * 1024


def test_four_hour_single_scene_is_capped_before_supplement_seek(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ffprobe = tmp_path / "ffprobe"
    ffmpeg = tmp_path / "ffmpeg"
    for tool in (ffprobe, ffmpeg):
        tool.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
        tool.chmod(0o700)
    tools = PackagedMediaTools(ffprobe_path=ffprobe, ffmpeg_path=ffmpeg)
    source = tmp_path / "four-hours.mp4"
    source.write_bytes(b"media")
    source, approved = approve_source(source)
    supplement_seeks: list[int] = []

    def record_selected_seek(build_argv: Any, **_kwargs: Any) -> BoundedFfmpegOutput:
        argv = build_argv(tmp_path)
        if "-ss" not in argv:
            return BoundedFfmpegOutput(files=(("scene-000000000000.jpg", b"scene"),))
        timestamp = argv[argv.index("-ss") + 1]
        seconds, milliseconds = timestamp.split(".")
        timestamp_ms = int(seconds) * 1_000 + int(milliseconds)
        supplement_seeks.append(timestamp_ms)
        return BoundedFfmpegOutput(files=((f"supplement-{timestamp_ms:012d}.jpg", b"supplement"),))

    monkeypatch.setattr(
        adaptive_frame_extraction,
        "_run_bounded_ffmpeg",
        record_selected_seek,
    )

    result = extract_adaptive_frame_candidates(
        tools,
        source,
        approved,
        duration_ms=14_400_000,
    )

    assert isinstance(result, tuple)
    assert len(result) == 60
    assert len(supplement_seeks) == 59
    assert supplement_seeks[0] == 8_000
    assert supplement_seeks[-1] == 14_392_000
