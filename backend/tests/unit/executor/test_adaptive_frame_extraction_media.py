"""Real packaged-FFmpeg acceptance for LE-08 scene frame extraction."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, os.fspath(REPOSITORY_ROOT / "scripts"))
from video_runtime_cache import cache_root  # type: ignore[import-not-found]  # noqa: E402

from automation_tool.executor import adaptive_frame_extraction  # noqa: E402
from automation_tool.executor.adaptive_frame_extraction import (  # noqa: E402
    AdaptiveFrameRejection,
    BoundedFfmpegOutput,
    extract_adaptive_frame_candidates,
    extract_scene_frames,
)
from automation_tool.executor.material_probe import (  # noqa: E402
    PackagedMediaTools,
    approve_source,
    require_source_unchanged,
)

_PACKAGED_TOOL_SUBDIRECTORY = "media-toolchain/bin"
_SCENE_FILTER = "settb=1/1000,select='eq(n,0)+gt(scene,0.1)'"


def _packaged_tools() -> PackagedMediaTools:
    suffix = ".exe" if os.name == "nt" else ""
    root = cache_root() / _PACKAGED_TOOL_SUBDIRECTORY
    ffprobe = root / f"ffprobe{suffix}"
    ffmpeg = root / f"ffmpeg{suffix}"
    if not (ffprobe.exists() and ffmpeg.exists()):
        raise AssertionError(
            "packaged media toolchain missing; run scripts/prepare_video_runtime.py"
        )
    return PackagedMediaTools(ffprobe_path=ffprobe, ffmpeg_path=ffmpeg)


def _encode(ffmpeg: Path, *arguments: str) -> None:
    subprocess.run(
        [os.fspath(ffmpeg), "-y", "-hide_banner", "-loglevel", "error", *arguments],
        check=True,
        capture_output=True,
    )


@pytest.fixture(scope="module")
def scene_media(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    tools = _packaged_tools()
    root = tmp_path_factory.mktemp("le08-scene-场景 &$ '")
    hard_cuts = root / "three hard cuts.mp4"
    single_scene = root / "single scene.mp4"
    long_scene = root / "sixty second gradual scene.mp4"
    nonzero_start = root / "nonzero start.mp4"
    ntsc_boundary = root / "ntsc just over eight seconds.mp4"
    _encode(
        tools.ffmpeg_path,
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=320x180:r=10:d=1",
        "-f",
        "lavfi",
        "-i",
        "color=c=white:s=320x180:r=10:d=1",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=s=320x180:r=10:d=1",
        "-filter_complex",
        "[0:v][1:v][2:v]concat=n=3:v=1:a=0[video]",
        "-map",
        "[video]",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        os.fspath(hard_cuts),
    )
    _encode(
        tools.ffmpeg_path,
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=320x180:r=10:d=3",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        os.fspath(single_scene),
    )
    _encode(
        tools.ffmpeg_path,
        "-f",
        "lavfi",
        "-i",
        "color=c=white:s=160x90:r=10:d=60,fade=t=in:st=0:d=60",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        os.fspath(long_scene),
    )
    _encode(
        tools.ffmpeg_path,
        "-f",
        "lavfi",
        "-i",
        "color=c=white:s=160x90:r=10:d=12,fade=t=in:st=0:d=12",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-output_ts_offset",
        "1.6",
        os.fspath(nonzero_start),
    )
    _encode(
        tools.ffmpeg_path,
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=160x90:r=30000/1001",
        "-frames:v",
        "240",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        os.fspath(ntsc_boundary),
    )
    return {
        "hard_cuts": hard_cuts,
        "single_scene": single_scene,
        "long_scene": long_scene,
        "nonzero_start": nonzero_start,
        "ntsc_boundary": ntsc_boundary,
    }


def test_packaged_ffmpeg_extracts_each_hard_cut_with_millisecond_timestamps(
    scene_media: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, approved = approve_source(scene_media["hard_cuts"])
    spawned_argv: list[list[str]] = []
    stability_checks: list[os.stat_result] = []
    real_popen = subprocess.Popen
    real_stability_check = require_source_unchanged

    def recording_popen(argv: list[str], **kwargs: Any) -> subprocess.Popen[bytes]:
        spawned_argv.append(argv)
        return real_popen(argv, **kwargs)

    def recording_stability_check(path: Path, prior: os.stat_result) -> tuple[Path, os.stat_result]:
        stability_checks.append(prior)
        return real_stability_check(path, prior)

    monkeypatch.setattr(
        "automation_tool.executor.adaptive_frame_extraction.subprocess.Popen",
        recording_popen,
    )
    monkeypatch.setattr(
        adaptive_frame_extraction,
        "require_source_unchanged",
        recording_stability_check,
    )

    result = extract_scene_frames(_packaged_tools(), source, approved)

    assert isinstance(result, tuple)
    assert tuple(frame.timestamp_ms for frame in result) == (0, 1000, 2000)
    assert all(frame.is_scene_cut for frame in result)
    assert all(
        frame.jpeg_bytes.startswith(b"\xff\xd8") and frame.jpeg_bytes.endswith(b"\xff\xd9")
        for frame in result
    )
    assert "jpeg_bytes" not in repr(result[0])
    assert len(spawned_argv) == 1
    argv = spawned_argv[0]
    assert argv[argv.index("-vf") + 1] == _SCENE_FILTER
    assert argv[argv.index("-enc_time_base") + 1] == "filter"
    assert argv[argv.index("-frame_pts") + 1] == "1"
    assert len(stability_checks) == 2


def test_packaged_ffmpeg_extracts_only_the_first_frame_from_one_scene(
    scene_media: dict[str, Path],
) -> None:
    source, approved = approve_source(scene_media["single_scene"])

    result = extract_scene_frames(_packaged_tools(), source, approved)

    assert not isinstance(result, AdaptiveFrameRejection)
    assert tuple(frame.timestamp_ms for frame in result) == (0,)
    assert result[0].is_scene_cut is True


def test_scene_extraction_revalidates_packaged_tools_before_spawning(
    tmp_path: Path,
) -> None:
    ffprobe = tmp_path / "ffprobe"
    ffmpeg = tmp_path / "ffmpeg"
    for tool in (ffprobe, ffmpeg):
        tool.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
        tool.chmod(0o700)
    tools = PackagedMediaTools(ffprobe_path=ffprobe, ffmpeg_path=ffmpeg)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")
    source, approved = approve_source(source)
    ffmpeg.unlink()

    result = extract_scene_frames(tools, source, approved)

    assert result is AdaptiveFrameRejection.TOOL_FAILED


def test_scene_extraction_returns_a_closed_reason_when_the_source_changed(
    scene_media: dict[str, Path],
    tmp_path: Path,
) -> None:
    source = tmp_path / "changed source.mp4"
    shutil.copyfile(scene_media["single_scene"], source)
    source, approved = approve_source(source)
    source.write_bytes(b"replaced")

    result = extract_scene_frames(_packaged_tools(), source, approved)

    assert result is AdaptiveFrameRejection.SOURCE_UNAVAILABLE


@pytest.mark.parametrize(
    ("scene_timestamps", "duration_ms", "expected"),
    [
        ((0,), 8_000, ()),
        ((0,), 8_001, (8_000,)),
        ((0, 10_000), 19_001, (8_000, 18_000)),
        ((0, 8_000, 16_001), 24_001, (16_000,)),
    ],
)
def test_long_scene_supplement_plan_resets_at_each_cut(
    scene_timestamps: tuple[int, ...],
    duration_ms: int,
    expected: tuple[int, ...],
) -> None:
    assert (
        adaptive_frame_extraction._supplement_timestamps(
            scene_timestamps,
            duration_ms=duration_ms,
        )
        == expected
    )


def test_packaged_ffmpeg_supplements_a_sixty_second_single_scene(
    scene_media: dict[str, Path],
) -> None:
    source, approved = approve_source(scene_media["long_scene"])

    result = extract_adaptive_frame_candidates(
        _packaged_tools(),
        source,
        approved,
        duration_ms=60_000,
    )

    assert isinstance(result, tuple)
    assert tuple(frame.timestamp_ms for frame in result) == tuple(range(0, 60_000, 8_000))
    assert tuple(frame.is_scene_cut for frame in result) == (True,) + (False,) * 7
    brightness: list[int] = []
    for frame in result:
        with Image.open(BytesIO(frame.jpeg_bytes)) as image:
            center = image.convert("L").getpixel((80, 45))
        assert isinstance(center, int)
        brightness.append(center)
    assert brightness == sorted(set(brightness))


def test_supplement_timestamps_are_relative_to_material_with_nonzero_start_pts(
    scene_media: dict[str, Path],
) -> None:
    source, approved = approve_source(scene_media["nonzero_start"])

    result = extract_adaptive_frame_candidates(
        _packaged_tools(),
        source,
        approved,
        duration_ms=12_000,
    )

    assert isinstance(result, tuple)
    assert tuple(frame.timestamp_ms for frame in result) == (0, 8_000)


def test_missing_tail_frame_at_an_ntsc_duration_boundary_is_not_bad_media(
    scene_media: dict[str, Path],
) -> None:
    source, approved = approve_source(scene_media["ntsc_boundary"])

    result = extract_adaptive_frame_candidates(
        _packaged_tools(),
        source,
        approved,
        duration_ms=8_008,
    )

    assert isinstance(result, tuple)
    assert tuple(frame.timestamp_ms for frame in result) == (0,)


def test_non_tail_supplement_failure_still_rejects_the_material(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ffprobe = tmp_path / "ffprobe"
    ffmpeg = tmp_path / "ffmpeg"
    for tool in (ffprobe, ffmpeg):
        tool.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
        tool.chmod(0o700)
    tools = PackagedMediaTools(ffprobe_path=ffprobe, ffmpeg_path=ffmpeg)
    source = tmp_path / "damaged.mp4"
    source.write_bytes(b"media")
    source, approved = approve_source(source)
    outputs: Iterator[BoundedFfmpegOutput | AdaptiveFrameRejection] = iter(
        (
            BoundedFfmpegOutput(files=(("scene-000000000000.jpg", b"scene"),)),
            AdaptiveFrameRejection.UNDECODABLE,
        )
    )

    def fail_before_the_tail(
        *_args: Any, **_kwargs: Any
    ) -> BoundedFfmpegOutput | AdaptiveFrameRejection:
        return next(outputs)

    monkeypatch.setattr(
        adaptive_frame_extraction,
        "_run_bounded_ffmpeg",
        fail_before_the_tail,
    )

    result = extract_adaptive_frame_candidates(
        tools,
        source,
        approved,
        duration_ms=16_001,
    )

    assert result is AdaptiveFrameRejection.UNDECODABLE


def test_two_seek_targets_that_land_on_one_actual_frame_are_deduplicated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ffprobe = tmp_path / "ffprobe"
    ffmpeg = tmp_path / "ffmpeg"
    for tool in (ffprobe, ffmpeg):
        tool.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
        tool.chmod(0o700)
    tools = PackagedMediaTools(ffprobe_path=ffprobe, ffmpeg_path=ffmpeg)
    source = tmp_path / "sparse.mp4"
    source.write_bytes(b"media")
    source, approved = approve_source(source)
    outputs = iter(
        (
            BoundedFfmpegOutput(files=(("scene-000000000000.jpg", b"scene"),)),
            BoundedFfmpegOutput(files=(("supplement-000000020000.jpg", b"first"),)),
            BoundedFfmpegOutput(files=(("supplement-000000020000.jpg", b"second"),)),
        )
    )

    def same_actual_frame(*_args: Any, **_kwargs: Any) -> BoundedFfmpegOutput:
        return next(outputs)

    monkeypatch.setattr(
        adaptive_frame_extraction,
        "_run_bounded_ffmpeg",
        same_actual_frame,
    )

    result = extract_adaptive_frame_candidates(
        tools,
        source,
        approved,
        duration_ms=20_001,
    )

    assert isinstance(result, tuple)
    assert tuple(frame.timestamp_ms for frame in result) == (0, 20_000)
