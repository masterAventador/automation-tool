"""Bounded local frame extraction with packaged FFmpeg."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum
from functools import partial
from pathlib import Path

from automation_tool.executor.material_probe import (
    MaterialProbeRejected,
    PackagedMediaTools,
    require_source_unchanged,
)

_OUTPUT_POLL_SECONDS = 0.02
_PROCESS_REAP_SECONDS = 5.0
_SCRATCH_PREFIX = "automation-tool-frame-extraction-"
_WORKSPACE_PROBE_NAME = ".workspace-write-probe"
_SCENE_FILTER = "settb=1/1000,select='eq(n,0)+gt(scene,0.1)'"
_SCENE_OUTPUT_PATTERN = "scene-%012d.jpg"
_SCENE_OUTPUT_PREFIX = "scene-"
_SCENE_OUTPUT_SUFFIX = ".jpg"
_SCENE_TIMESTAMP_DIGITS = 12
_SCENE_TIMEOUT_SECONDS = 15 * 60
_SCENE_OUTPUT_LIMIT_BYTES = 64 * 1024 * 1024
_LONG_SCENE_INTERVAL_MS = 8_000
_SUPPLEMENT_OUTPUT_PREFIX = "supplement-"
_SUPPLEMENT_OUTPUT_SUFFIX = ".jpg"


class AdaptiveFrameRejection(StrEnum):
    """Closed reasons that callers may safely turn into user actions."""

    UNDECODABLE = "undecodable"
    TIMED_OUT = "timed_out"
    OUTPUT_LIMIT_EXCEEDED = "output_limit_exceeded"
    WORKSPACE_UNUSABLE = "workspace_unusable"
    TOOL_FAILED = "tool_failed"
    SOURCE_UNAVAILABLE = "source_unavailable"


@dataclass(frozen=True, slots=True)
class BoundedFfmpegOutput:
    """Files written by one successful bounded FFmpeg pass."""

    files: tuple[tuple[str, bytes], ...] = field(repr=False)


@dataclass(frozen=True, slots=True)
class ExtractedFrame:
    """One scene candidate held only inside the Local Executor."""

    timestamp_ms: int
    is_scene_cut: bool
    jpeg_bytes: bytes = field(repr=False)


def extract_scene_frames(
    tools: PackagedMediaTools,
    source: Path,
    approved: os.stat_result,
) -> tuple[ExtractedFrame, ...] | AdaptiveFrameRejection:
    """Extract the first frame and each hard scene cut with packaged FFmpeg."""
    try:
        tools.revalidate()
    except MaterialProbeRejected:
        return AdaptiveFrameRejection.TOOL_FAILED
    try:
        path, checked = require_source_unchanged(source, approved)
    except MaterialProbeRejected:
        return AdaptiveFrameRejection.SOURCE_UNAVAILABLE

    output = _run_bounded_ffmpeg(
        lambda workspace: _scene_ffmpeg_argv(tools.ffmpeg_path, path, workspace),
        seconds=_SCENE_TIMEOUT_SECONDS,
        output_limit_bytes=_SCENE_OUTPUT_LIMIT_BYTES,
    )
    try:
        require_source_unchanged(path, checked)
    except MaterialProbeRejected:
        return AdaptiveFrameRejection.SOURCE_UNAVAILABLE
    if isinstance(output, AdaptiveFrameRejection):
        return output
    return _parse_scene_frames(output)


def extract_adaptive_frame_candidates(
    tools: PackagedMediaTools,
    source: Path,
    approved: os.stat_result,
    *,
    duration_ms: int,
) -> tuple[ExtractedFrame, ...] | AdaptiveFrameRejection:
    """Add an actual frame every eight seconds inside long scenes."""
    scene_frames = extract_scene_frames(tools, source, approved)
    if isinstance(scene_frames, AdaptiveFrameRejection):
        return scene_frames
    supplement_timestamps = _supplement_timestamps(
        tuple(frame.timestamp_ms for frame in scene_frames),
        duration_ms=duration_ms,
    )
    if not supplement_timestamps:
        return scene_frames

    remaining_bytes = _SCENE_OUTPUT_LIMIT_BYTES - sum(
        len(frame.jpeg_bytes) for frame in scene_frames
    )
    deadline = time.monotonic() + _SCENE_TIMEOUT_SECONDS
    supplements: list[ExtractedFrame] = []
    for timestamp_ms in supplement_timestamps:
        remaining_seconds = deadline - time.monotonic()
        if remaining_seconds <= 0:
            return AdaptiveFrameRejection.TIMED_OUT
        try:
            tools.revalidate()
        except MaterialProbeRejected:
            return AdaptiveFrameRejection.TOOL_FAILED
        try:
            path, checked = require_source_unchanged(source, approved)
        except MaterialProbeRejected:
            return AdaptiveFrameRejection.SOURCE_UNAVAILABLE
        output = _run_bounded_ffmpeg(
            partial(
                _supplement_ffmpeg_argv,
                tools.ffmpeg_path,
                path,
                timestamp_ms=timestamp_ms,
            ),
            seconds=remaining_seconds,
            output_limit_bytes=remaining_bytes,
        )
        try:
            require_source_unchanged(path, checked)
        except MaterialProbeRejected:
            return AdaptiveFrameRejection.SOURCE_UNAVAILABLE
        if isinstance(output, AdaptiveFrameRejection):
            # The scene pass immediately above decoded the unchanged source through
            # EOF. Some containers nevertheless state a duration just beyond their
            # last frame PTS, so only the final planned seek may legitimately find
            # no frame; an earlier seek failure still rejects the material.
            if (
                output is AdaptiveFrameRejection.UNDECODABLE
                and timestamp_ms == supplement_timestamps[-1]
            ):
                break
            return output
        frame = _parse_supplement_frame(output)
        if isinstance(frame, AdaptiveFrameRejection):
            return frame
        if frame is None:
            break
        supplements.append(frame)
        remaining_bytes -= len(frame.jpeg_bytes)

    by_timestamp: dict[int, ExtractedFrame] = {}
    for frame in (*scene_frames, *supplements):
        by_timestamp.setdefault(frame.timestamp_ms, frame)
    return tuple(by_timestamp[timestamp] for timestamp in sorted(by_timestamp))


def _supplement_timestamps(
    scene_timestamps: tuple[int, ...],
    *,
    duration_ms: int,
) -> tuple[int, ...]:
    supplements: list[int] = []
    for index, scene_start in enumerate(scene_timestamps):
        scene_end = (
            scene_timestamps[index + 1] if index + 1 < len(scene_timestamps) else duration_ms
        )
        timestamp = scene_start + _LONG_SCENE_INTERVAL_MS
        while timestamp < scene_end:
            supplements.append(timestamp)
            timestamp += _LONG_SCENE_INTERVAL_MS
    return tuple(supplements)


def _supplement_ffmpeg_argv(
    ffmpeg: Path,
    source: Path,
    workspace: Path,
    *,
    timestamp_ms: int,
) -> list[str]:
    timestamp = f"{timestamp_ms // 1000}.{timestamp_ms % 1000:03d}"
    return [
        os.fspath(ffmpeg),
        "-y",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-copyts",
        "-start_at_zero",
        "-ss",
        timestamp,
        "-i",
        os.fspath(source),
        "-map",
        "0:v:0",
        "-vf",
        "settb=1/1000",
        "-frames:v",
        "1",
        "-fps_mode",
        "passthrough",
        "-enc_time_base",
        "filter",
        "-frame_pts",
        "1",
        "-q:v",
        "2",
        os.fspath(workspace / f"{_SUPPLEMENT_OUTPUT_PREFIX}%012d{_SUPPLEMENT_OUTPUT_SUFFIX}"),
    ]


def _parse_supplement_frame(
    output: BoundedFfmpegOutput,
) -> ExtractedFrame | AdaptiveFrameRejection | None:
    if not output.files:
        return None
    if len(output.files) != 1:
        return AdaptiveFrameRejection.UNDECODABLE
    name, content = output.files[0]
    timestamp = _timestamp_from_name(
        name,
        prefix=_SUPPLEMENT_OUTPUT_PREFIX,
        suffix=_SUPPLEMENT_OUTPUT_SUFFIX,
    )
    if timestamp is None:
        return AdaptiveFrameRejection.TOOL_FAILED
    return ExtractedFrame(
        timestamp_ms=timestamp,
        is_scene_cut=False,
        jpeg_bytes=content,
    )


def _scene_ffmpeg_argv(ffmpeg: Path, source: Path, workspace: Path) -> list[str]:
    return [
        os.fspath(ffmpeg),
        "-y",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        os.fspath(source),
        "-map",
        "0:v:0",
        "-vf",
        _SCENE_FILTER,
        "-fps_mode",
        "passthrough",
        "-enc_time_base",
        "filter",
        "-frame_pts",
        "1",
        "-q:v",
        "2",
        os.fspath(workspace / _SCENE_OUTPUT_PATTERN),
    ]


def _parse_scene_frames(
    output: BoundedFfmpegOutput,
) -> tuple[ExtractedFrame, ...] | AdaptiveFrameRejection:
    frames: list[ExtractedFrame] = []
    for name, content in output.files:
        timestamp = _scene_timestamp(name)
        if timestamp is None:
            return AdaptiveFrameRejection.TOOL_FAILED
        frames.append(
            ExtractedFrame(
                timestamp_ms=timestamp,
                is_scene_cut=True,
                jpeg_bytes=content,
            )
        )
    return tuple(frames) if frames else AdaptiveFrameRejection.TOOL_FAILED


def _scene_timestamp(name: str) -> int | None:
    return _timestamp_from_name(
        name,
        prefix=_SCENE_OUTPUT_PREFIX,
        suffix=_SCENE_OUTPUT_SUFFIX,
    )


def _timestamp_from_name(name: str, *, prefix: str, suffix: str) -> int | None:
    if not name.startswith(prefix) or not name.endswith(suffix):
        return None
    digits = name[len(prefix) : -len(suffix)]
    if len(digits) != _SCENE_TIMESTAMP_DIGITS or not digits.isascii() or not digits.isdigit():
        return None
    return int(digits)


def _run_bounded_ffmpeg(
    build_argv: Callable[[Path], list[str]],
    *,
    seconds: float,
    output_limit_bytes: int,
) -> BoundedFfmpegOutput | AdaptiveFrameRejection:
    """Run a file-producing FFmpeg pass without piping media through memory.

    This deliberately restates the four LE-07 runner properties instead of
    importing its private helper: output is written to files, measured while
    the child runs, killed once it exceeds the bound, and every operational
    rejection is returned before best-effort workspace cleanup.
    """
    if seconds <= 0 or output_limit_bytes < 0:
        raise ValueError("bounded FFmpeg limits must be positive")
    try:
        workspace_text = tempfile.mkdtemp(prefix=_SCRATCH_PREFIX)
    except OSError:
        return AdaptiveFrameRejection.WORKSPACE_UNUSABLE
    workspace = Path(workspace_text)
    try:
        return _collect_bounded_ffmpeg(
            build_argv(workspace),
            workspace,
            seconds=seconds,
            output_limit_bytes=output_limit_bytes,
        )
    finally:
        with suppress(OSError):
            shutil.rmtree(workspace)


def _collect_bounded_ffmpeg(
    argv: list[str],
    workspace: Path,
    *,
    seconds: float,
    output_limit_bytes: int,
) -> BoundedFfmpegOutput | AdaptiveFrameRejection:
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return AdaptiveFrameRejection.TOOL_FAILED

    with process:
        try:
            ended = _wait_for_bounded_output(
                process,
                workspace,
                seconds=seconds,
                output_limit_bytes=output_limit_bytes,
            )
        except BaseException:
            # Popen.__exit__ waits without a deadline for ordinary exceptions,
            # so the child must be stopped before control leaves this context.
            _kill_and_reap(process)
            raise

    if isinstance(ended, AdaptiveFrameRejection):
        return ended
    measured = _measure_output(workspace)
    if isinstance(measured, AdaptiveFrameRejection):
        return measured
    total_bytes, paths = measured
    if total_bytes > output_limit_bytes:
        return AdaptiveFrameRejection.OUTPUT_LIMIT_EXCEEDED
    if ended < 0:
        return AdaptiveFrameRejection.TOOL_FAILED
    if ended > 0:
        if not _workspace_accepts_write(workspace):
            return AdaptiveFrameRejection.WORKSPACE_UNUSABLE
        return AdaptiveFrameRejection.UNDECODABLE
    try:
        return BoundedFfmpegOutput(
            files=tuple((path.name, path.read_bytes()) for path in paths),
        )
    except OSError:
        return AdaptiveFrameRejection.WORKSPACE_UNUSABLE


def _workspace_accepts_write(workspace: Path) -> bool:
    """Distinguish a failed decode from FFmpeg losing its output workspace."""
    probe = workspace / _WORKSPACE_PROBE_NAME
    try:
        with probe.open("xb") as sink:
            sink.write(b"\0")
            sink.flush()
            os.fsync(sink.fileno())
        probe.unlink()
        return True
    except OSError:
        with suppress(OSError):
            probe.unlink()
        return False


def _wait_for_bounded_output(
    process: subprocess.Popen[bytes],
    workspace: Path,
    *,
    seconds: float,
    output_limit_bytes: int,
) -> int | AdaptiveFrameRejection:
    deadline = time.monotonic() + seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _kill_and_reap(process)
            return AdaptiveFrameRejection.TIMED_OUT
        try:
            returncode = process.wait(timeout=min(_OUTPUT_POLL_SECONDS, remaining))
        except subprocess.TimeoutExpired:
            returncode = None

        measured = _measure_output(workspace)
        if isinstance(measured, AdaptiveFrameRejection):
            _kill_and_reap(process)
            return measured
        total_bytes, _ = measured
        if total_bytes > output_limit_bytes:
            _kill_and_reap(process)
            return AdaptiveFrameRejection.OUTPUT_LIMIT_EXCEEDED
        if returncode is not None:
            return returncode


def _measure_output(
    workspace: Path,
) -> tuple[int, tuple[Path, ...]] | AdaptiveFrameRejection:
    try:
        paths = tuple(sorted(workspace.iterdir(), key=lambda path: path.name))
        total_bytes = 0
        for path in paths:
            metadata = path.stat(follow_symlinks=False)
            if not stat.S_ISREG(metadata.st_mode):
                return AdaptiveFrameRejection.TOOL_FAILED
            total_bytes += metadata.st_size
        return total_bytes, paths
    except OSError:
        return AdaptiveFrameRejection.WORKSPACE_UNUSABLE


def _kill_and_reap(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        with suppress(OSError):
            process.kill()
    try:
        process.wait(timeout=_PROCESS_REAP_SECONDS)
    except subprocess.TimeoutExpired:
        with suppress(OSError):
            process.kill()
        with suppress(subprocess.TimeoutExpired):
            process.wait(timeout=_PROCESS_REAP_SECONDS)
