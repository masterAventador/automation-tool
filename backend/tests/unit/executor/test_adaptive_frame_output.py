"""Final JPEG artifact acceptance for LE-08."""

from __future__ import annotations

import inspect
import os
import stat
import subprocess
import sys
from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, os.fspath(REPOSITORY_ROOT / "scripts"))
from video_runtime_cache import cache_root  # type: ignore[import-not-found]  # noqa: E402

from automation_tool.executor import adaptive_frame_extraction  # noqa: E402
from automation_tool.executor.adaptive_frame_extraction import (  # noqa: E402
    AdaptiveFrameArtifact,
    AdaptiveFrameRejection,
    ExtractedFrame,
    extract_adaptive_frames,
)
from automation_tool.executor.material_probe import (  # noqa: E402
    PackagedMediaTools,
    approve_source,
)

_PACKAGED_TOOL_SUBDIRECTORY = "media-toolchain/bin"


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


def _fake_tools(tmp_path: Path) -> PackagedMediaTools:
    ffprobe = tmp_path / "ffprobe"
    ffmpeg = tmp_path / "ffmpeg"
    for tool in (ffprobe, ffmpeg):
        tool.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
        tool.chmod(0o700)
    return PackagedMediaTools(ffprobe_path=ffprobe, ffmpeg_path=ffmpeg)


def _encode(ffmpeg: Path, *arguments: str) -> None:
    subprocess.run(
        [os.fspath(ffmpeg), "-y", "-hide_banner", "-loglevel", "error", *arguments],
        check=True,
        capture_output=True,
    )


@pytest.fixture(scope="module")
def output_media(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    tools = _packaged_tools()
    root = tmp_path_factory.mktemp("le08-output-产物 &$ '")
    landscape_cuts = root / "landscape non-grid cuts.mp4"
    portrait = root / "portrait.mp4"
    small = root / "small.mp4"

    _encode(
        tools.ffmpeg_path,
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=1280x720:r=30:d=1.233",
        "-f",
        "lavfi",
        "-i",
        "color=c=white:s=1280x720:r=30:d=0.777",
        "-filter_complex",
        "[0:v][1:v]concat=n=2:v=1:a=0[video]",
        "-map",
        "[video]",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        os.fspath(landscape_cuts),
    )
    _encode(
        tools.ffmpeg_path,
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:s=720x1280:r=10:d=1.137",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        os.fspath(portrait),
    )
    _encode(
        tools.ffmpeg_path,
        "-f",
        "lavfi",
        "-i",
        "color=c=red:s=320x180:r=10:d=1.137",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        os.fspath(small),
    )
    return {
        "landscape_cuts": landscape_cuts,
        "portrait": portrait,
        "small": small,
    }


def test_final_artifacts_use_stable_names_and_path_free_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = _fake_tools(tmp_path)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")
    source, approved = approve_source(source)
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    candidates = (
        ExtractedFrame(timestamp_ms=0, is_scene_cut=True, jpeg_bytes=b"first"),
        ExtractedFrame(timestamp_ms=8_000, is_scene_cut=False, jpeg_bytes=b"second"),
    )
    monkeypatch.setattr(
        adaptive_frame_extraction,
        "extract_adaptive_frame_candidates",
        lambda *_args, **_kwargs: candidates,
    )

    result = extract_adaptive_frames(
        tools,
        source,
        approved,
        output,
        duration_ms=8_001,
    )

    assert isinstance(result, tuple)
    assert tuple(field.name for field in fields(AdaptiveFrameArtifact)) == (
        "filename",
        "timestamp_ms",
        "is_scene_cut",
        "byte_size",
    )
    assert result == (
        AdaptiveFrameArtifact(
            filename="frame-000001.jpg",
            timestamp_ms=0,
            is_scene_cut=True,
            byte_size=5,
        ),
        AdaptiveFrameArtifact(
            filename="frame-000002.jpg",
            timestamp_ms=8_000,
            is_scene_cut=False,
            byte_size=6,
        ),
    )
    assert (output / "frame-000001.jpg").read_bytes() == b"first"
    assert (output / "frame-000002.jpg").read_bytes() == b"second"
    assert os.name == "nt" or stat.S_IMODE((output / "frame-000001.jpg").stat().st_mode) == 0o600
    assert os.fspath(source) not in repr(result)
    assert os.fspath(output) not in repr(result)


def test_non_directory_workspace_is_rejected_before_media_extraction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = _fake_tools(tmp_path)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")
    source, approved = approve_source(source)
    output = tmp_path / "not-a-directory"
    output.write_bytes(b"private")

    def must_not_extract(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("an unusable output workspace must fail before decoding")

    monkeypatch.setattr(
        adaptive_frame_extraction,
        "extract_adaptive_frame_candidates",
        must_not_extract,
    )

    result = extract_adaptive_frames(
        tools,
        source,
        approved,
        output,
        duration_ms=1,
    )

    assert result is AdaptiveFrameRejection.WORKSPACE_UNUSABLE
    assert output.read_bytes() == b"private"


def test_existing_stable_name_is_never_overwritten(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = _fake_tools(tmp_path)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")
    source, approved = approve_source(source)
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    planted = output / "frame-000001.jpg"
    planted.write_bytes(b"keep me")
    monkeypatch.setattr(
        adaptive_frame_extraction,
        "extract_adaptive_frame_candidates",
        lambda *_args, **_kwargs: (
            ExtractedFrame(timestamp_ms=0, is_scene_cut=True, jpeg_bytes=b"replacement"),
        ),
    )

    result = extract_adaptive_frames(
        tools,
        source,
        approved,
        output,
        duration_ms=1,
    )

    assert result is AdaptiveFrameRejection.WORKSPACE_UNUSABLE
    assert planted.read_bytes() == b"keep me"


def test_partial_write_rolls_back_only_files_created_by_this_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = _fake_tools(tmp_path)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")
    source, approved = approve_source(source)
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    stranger = output / "keep.txt"
    stranger.write_bytes(b"not mine")
    monkeypatch.setattr(
        adaptive_frame_extraction,
        "extract_adaptive_frame_candidates",
        lambda *_args, **_kwargs: (
            ExtractedFrame(timestamp_ms=0, is_scene_cut=True, jpeg_bytes=b"first"),
            ExtractedFrame(timestamp_ms=8_000, is_scene_cut=False, jpeg_bytes=b"second"),
        ),
    )
    real_write = os.write
    write_calls = 0

    def fail_second_file(descriptor: int, payload: bytes | bytearray | memoryview[int]) -> int:
        nonlocal write_calls
        write_calls += 1
        if write_calls == 2:
            raise OSError("workspace became full")
        return real_write(descriptor, payload)

    monkeypatch.setattr(
        "automation_tool.executor.adaptive_frame_extraction.os.write",
        fail_second_file,
    )

    result = extract_adaptive_frames(
        tools,
        source,
        approved,
        output,
        duration_ms=8_001,
    )

    assert result is AdaptiveFrameRejection.WORKSPACE_UNUSABLE
    assert stranger.read_bytes() == b"not mine"
    assert tuple(output.glob("frame-*.jpg")) == ()


def test_directory_replacement_rolls_back_through_the_original_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = _fake_tools(tmp_path)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")
    source, approved = approve_source(source)
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    moved = tmp_path / "moved-output"
    monkeypatch.setattr(
        adaptive_frame_extraction,
        "extract_adaptive_frame_candidates",
        lambda *_args, **_kwargs: (
            ExtractedFrame(timestamp_ms=0, is_scene_cut=True, jpeg_bytes=b"first"),
            ExtractedFrame(timestamp_ms=8_000, is_scene_cut=False, jpeg_bytes=b"second"),
        ),
    )
    real_write = os.write
    write_calls = 0

    def replace_directory_after_first_write(
        descriptor: int,
        payload: bytes | bytearray | memoryview[int],
    ) -> int:
        nonlocal write_calls
        written = real_write(descriptor, payload)
        write_calls += 1
        if write_calls == 1:
            output.rename(moved)
            output.mkdir(mode=0o700)
        return written

    monkeypatch.setattr(
        "automation_tool.executor.adaptive_frame_extraction.os.write",
        replace_directory_after_first_write,
    )

    result = extract_adaptive_frames(
        tools,
        source,
        approved,
        output,
        duration_ms=8_001,
    )

    assert result is AdaptiveFrameRejection.WORKSPACE_UNUSABLE
    assert tuple(moved.iterdir()) == ()
    assert tuple(output.iterdir()) == ()


def test_close_failure_removes_the_frame_that_was_just_created(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = _fake_tools(tmp_path)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")
    source, approved = approve_source(source)
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    monkeypatch.setattr(
        adaptive_frame_extraction,
        "extract_adaptive_frame_candidates",
        lambda *_args, **_kwargs: (
            ExtractedFrame(timestamp_ms=0, is_scene_cut=True, jpeg_bytes=b"first"),
        ),
    )
    real_close = os.close
    close_calls = 0

    def fail_first_close(descriptor: int) -> None:
        nonlocal close_calls
        close_calls += 1
        real_close(descriptor)
        if close_calls == 1:
            raise OSError("late close failure")

    monkeypatch.setattr(
        "automation_tool.executor.adaptive_frame_extraction.os.close",
        fail_first_close,
    )

    result = extract_adaptive_frames(
        tools,
        source,
        approved,
        output,
        duration_ms=1,
    )

    assert result is AdaptiveFrameRejection.WORKSPACE_UNUSABLE
    assert tuple(output.iterdir()) == ()


def test_memory_failure_after_exclusive_create_removes_the_partial_frame(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = _fake_tools(tmp_path)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")
    source, approved = approve_source(source)
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    monkeypatch.setattr(
        adaptive_frame_extraction,
        "extract_adaptive_frame_candidates",
        lambda *_args, **_kwargs: (
            ExtractedFrame(timestamp_ms=0, is_scene_cut=True, jpeg_bytes=b"first"),
            ExtractedFrame(timestamp_ms=8_000, is_scene_cut=False, jpeg_bytes=b"second"),
        ),
    )
    real_memoryview = memoryview
    memoryview_calls = 0

    def fail_second_memoryview(payload: bytes) -> memoryview:
        nonlocal memoryview_calls
        memoryview_calls += 1
        if memoryview_calls == 2:
            raise MemoryError("injected allocation failure")
        return real_memoryview(payload)

    monkeypatch.setattr(
        adaptive_frame_extraction,
        "memoryview",
        fail_second_memoryview,
        raising=False,
    )

    with pytest.raises(MemoryError, match="injected allocation failure"):
        extract_adaptive_frames(
            tools,
            source,
            approved,
            output,
            duration_ms=8_001,
        )

    assert tuple(output.iterdir()) == ()


def test_registered_frame_is_unlinked_only_once_during_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = _fake_tools(tmp_path)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")
    source, approved = approve_source(source)
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    monkeypatch.setattr(
        adaptive_frame_extraction,
        "extract_adaptive_frame_candidates",
        lambda *_args, **_kwargs: (
            ExtractedFrame(timestamp_ms=0, is_scene_cut=True, jpeg_bytes=b"first"),
        ),
    )

    def fail_memoryview(_payload: bytes) -> memoryview:
        raise MemoryError("injected allocation failure")

    monkeypatch.setattr(
        adaptive_frame_extraction,
        "memoryview",
        fail_memoryview,
        raising=False,
    )
    real_unlink = adaptive_frame_extraction._OutputWorkspace.unlink
    unlink_calls = 0

    def replace_after_first_unlink(
        workspace: adaptive_frame_extraction._OutputWorkspace,
        filename: str,
    ) -> None:
        nonlocal unlink_calls
        real_unlink(workspace, filename)
        unlink_calls += 1
        if unlink_calls == 1:
            (output / filename).write_bytes(b"concurrent writer")

    monkeypatch.setattr(
        adaptive_frame_extraction._OutputWorkspace,
        "unlink",
        replace_after_first_unlink,
    )

    with pytest.raises(MemoryError, match="injected allocation failure"):
        extract_adaptive_frames(
            tools,
            source,
            approved,
            output,
            duration_ms=1,
        )

    assert unlink_calls == 1
    assert (output / "frame-000001.jpg").read_bytes() == b"concurrent writer"


def test_interruption_after_registration_does_not_repeat_the_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = _fake_tools(tmp_path)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")
    source, approved = approve_source(source)
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    monkeypatch.setattr(
        adaptive_frame_extraction,
        "extract_adaptive_frame_candidates",
        lambda *_args, **_kwargs: (
            ExtractedFrame(timestamp_ms=0, is_scene_cut=True, jpeg_bytes=b"first"),
        ),
    )
    real_unlink = adaptive_frame_extraction._OutputWorkspace.unlink
    unlink_calls = 0

    def replace_after_first_unlink(
        workspace: adaptive_frame_extraction._OutputWorkspace,
        filename: str,
    ) -> None:
        nonlocal unlink_calls
        real_unlink(workspace, filename)
        unlink_calls += 1
        if unlink_calls == 1:
            (output / filename).write_bytes(b"concurrent writer")

    monkeypatch.setattr(
        adaptive_frame_extraction._OutputWorkspace,
        "unlink",
        replace_after_first_unlink,
    )
    lines, first_line = inspect.getsourcelines(adaptive_frame_extraction._write_exclusive_frame)
    append_offset = next(
        offset for offset, line in enumerate(lines) if line.strip() == "created.append(filename)"
    )
    interruption_line = first_line + append_offset + 1

    def interrupt_after_append(
        frame: Any,
        event: str,
        _argument: Any,
    ) -> Any:
        if (
            event == "line"
            and frame.f_code is adaptive_frame_extraction._write_exclusive_frame.__code__
            and frame.f_lineno == interruption_line
        ):
            sys.settrace(previous_trace)
            raise KeyboardInterrupt("injected cancellation")
        return interrupt_after_append

    original_trace = sys.gettrace()

    def existing_trace(frame: Any, event: str, argument: Any) -> Any:
        del frame, event, argument
        return existing_trace

    try:
        sys.settrace(existing_trace)
        previous_trace = sys.gettrace()
        try:
            sys.settrace(interrupt_after_append)
            with pytest.raises(KeyboardInterrupt, match="injected cancellation"):
                extract_adaptive_frames(
                    tools,
                    source,
                    approved,
                    output,
                    duration_ms=1,
                )
        finally:
            sys.settrace(previous_trace)

        assert sys.gettrace() is existing_trace
    finally:
        sys.settrace(original_trace)

    assert sys.gettrace() is original_trace
    assert unlink_calls == 1
    assert (output / "frame-000001.jpg").read_bytes() == b"concurrent writer"


@pytest.mark.parametrize(
    ("media_name", "duration_ms", "expected_count", "expected_size"),
    [
        ("landscape_cuts", 2_010, 2, (768, 432)),
        ("portrait", 1_137, 1, (432, 768)),
        ("small", 1_137, 1, (320, 180)),
    ],
)
def test_packaged_ffmpeg_writes_bounded_jpegs_without_upscaling(
    output_media: dict[str, Path],
    tmp_path: Path,
    media_name: str,
    duration_ms: int,
    expected_count: int,
    expected_size: tuple[int, int],
) -> None:
    source, approved = approve_source(output_media[media_name])
    output = tmp_path / media_name
    output.mkdir(mode=0o700)

    result = extract_adaptive_frames(
        _packaged_tools(),
        source,
        approved,
        output,
        duration_ms=duration_ms,
    )

    assert isinstance(result, tuple)
    assert len(result) == expected_count
    assert tuple(frame.filename for frame in result) == tuple(
        f"frame-{index:06d}.jpg" for index in range(1, expected_count + 1)
    )
    assert all(frame.is_scene_cut for frame in result)
    for frame in result:
        path = output / frame.filename
        assert path.is_file()
        assert path.stat().st_size == frame.byte_size
        with Image.open(path) as image:
            assert image.format == "JPEG"
            assert image.size == expected_size
            assert max(image.size) <= 768

    if media_name == "landscape_cuts":
        assert tuple(frame.timestamp_ms for frame in result) == (0, 1_233)


def test_bad_media_is_undecodable_and_creates_no_final_files(tmp_path: Path) -> None:
    source = tmp_path / "bad material.mp4"
    source.write_bytes(b"this is not media")
    source, approved = approve_source(source)
    output = tmp_path / "output"
    output.mkdir(mode=0o700)

    result = extract_adaptive_frames(
        _packaged_tools(),
        source,
        approved,
        output,
        duration_ms=1_137,
    )

    assert result is AdaptiveFrameRejection.UNDECODABLE
    assert tuple(output.iterdir()) == ()
