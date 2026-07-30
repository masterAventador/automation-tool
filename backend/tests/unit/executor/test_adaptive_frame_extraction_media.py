"""Real packaged-FFmpeg acceptance for LE-08 scene frame extraction."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, os.fspath(REPOSITORY_ROOT / "scripts"))
from video_runtime_cache import cache_root  # type: ignore[import-not-found]  # noqa: E402

from automation_tool.executor import adaptive_frame_extraction  # noqa: E402
from automation_tool.executor.adaptive_frame_extraction import (  # noqa: E402
    AdaptiveFrameRejection,
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
    return {"hard_cuts": hard_cuts, "single_scene": single_scene}


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
