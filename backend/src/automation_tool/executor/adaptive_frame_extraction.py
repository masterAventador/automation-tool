"""Bounded local frame extraction with packaged FFmpeg."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import time
from array import array
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum
from functools import partial
from pathlib import Path
from typing import cast

from automation_tool.executor.material_probe import (
    MaterialProbeRejected,
    PackagedMediaTools,
    require_source_unchanged,
)

_OUTPUT_POLL_SECONDS = 0.02
_PROCESS_REAP_SECONDS = 5.0
_SCRATCH_PREFIX = "automation-tool-frame-extraction-"
_WORKSPACE_PROBE_NAME = ".workspace-write-probe"
_JPEG_SCALE_FILTER = "scale=w='min(768,iw)':h='min(768,ih)':force_original_aspect_ratio=decrease"
_SCENE_FILTER = f"settb=1/1000,select='eq(n,0)+gt(scene,0.1)',{_JPEG_SCALE_FILTER}"
_SCENE_OUTPUT_PATTERN = "scene-%012d.jpg"
_SCENE_OUTPUT_PREFIX = "scene-"
_SCENE_OUTPUT_SUFFIX = ".jpg"
_SCENE_TIMESTAMP_DIGITS = 12
_SCENE_TIMEOUT_SECONDS = 15 * 60
_SCENE_OUTPUT_LIMIT_BYTES = 64 * 1024 * 1024
_LONG_SCENE_INTERVAL_MS = 8_000
_SUPPLEMENT_OUTPUT_PREFIX = "supplement-"
_SUPPLEMENT_OUTPUT_SUFFIX = ".jpg"
_SHORT_FRAME_LIMIT_DURATION_MS = 15_000
_MEDIUM_FRAME_LIMIT_DURATION_MS = 60_000
_LONG_FRAME_LIMIT_DURATION_MS = 300_000
_EXTRA_LONG_FRAME_LIMIT_DURATION_MS = 1_200_000
_FINAL_OUTPUT_PREFIX = "frame-"
_FINAL_OUTPUT_SUFFIX = ".jpg"
_FINAL_OUTPUT_DIGITS = 6


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


@dataclass(frozen=True, slots=True)
class AdaptiveFrameArtifact:
    """Path-free metadata for one final JPEG in the caller-owned workspace."""

    filename: str
    timestamp_ms: int
    is_scene_cut: bool
    byte_size: int


@dataclass(slots=True)
class _OutputWorkspace:
    """An owned directory reference that survives path replacement."""

    path: Path
    identity: tuple[int, int]
    directory_descriptor: int | None = None
    windows_handle: int | None = None

    def revalidate_path(self) -> None:
        if _output_workspace_identity(self.path) != self.identity:
            raise OSError("output workspace changed")

    def open_exclusive(self, filename: str) -> int:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= cast(int, getattr(os, "O_BINARY", 0))
        flags |= cast(int, getattr(os, "O_NOFOLLOW", 0))
        if self.directory_descriptor is not None:
            return os.open(
                filename,
                flags,
                0o600,
                dir_fd=self.directory_descriptor,
            )
        return os.open(self._current_path() / filename, flags, 0o600)

    def stat_frame(self, filename: str) -> os.stat_result:
        if self.directory_descriptor is not None:
            return os.stat(
                filename,
                dir_fd=self.directory_descriptor,
                follow_symlinks=False,
            )
        return (self._current_path() / filename).lstat()

    def unlink(self, filename: str) -> None:
        if self.directory_descriptor is not None:
            os.unlink(filename, dir_fd=self.directory_descriptor)
            return
        (self._current_path() / filename).unlink()

    def fsync(self) -> None:
        if self.directory_descriptor is not None:
            os.fsync(self.directory_descriptor)

    def close(self) -> None:
        if self.directory_descriptor is not None:
            descriptor = self.directory_descriptor
            self.directory_descriptor = None
            os.close(descriptor)
        if self.windows_handle is not None:
            handle = self.windows_handle
            self.windows_handle = None
            _close_windows_directory_handle(handle)

    def _current_path(self) -> Path:
        if self.windows_handle is not None:
            return _windows_directory_path(self.windows_handle)
        return self.path


def extract_adaptive_frames(
    tools: PackagedMediaTools,
    source: Path,
    approved: os.stat_result,
    output_directory: Path,
    *,
    duration_ms: int,
) -> tuple[AdaptiveFrameArtifact, ...] | AdaptiveFrameRejection:
    """Extract, resize and persist final JPEGs under controlled names."""
    workspace = _open_output_workspace(output_directory)
    if workspace is None:
        return AdaptiveFrameRejection.WORKSPACE_UNUSABLE
    try:
        candidates = extract_adaptive_frame_candidates(
            tools,
            source,
            approved,
            duration_ms=duration_ms,
        )
        if isinstance(candidates, AdaptiveFrameRejection):
            return candidates
        return _write_final_frames(workspace, candidates)
    finally:
        with suppress(OSError):
            workspace.close()


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
    """Select bounded scene and long-shot candidates before supplemental seeks."""
    extracted_scene_frames = extract_scene_frames(tools, source, approved)
    if isinstance(extracted_scene_frames, AdaptiveFrameRejection):
        return extracted_scene_frames
    scene_by_timestamp: dict[int, ExtractedFrame] = {}
    for scene_frame in extracted_scene_frames:
        scene_by_timestamp.setdefault(scene_frame.timestamp_ms, scene_frame)
    scene_timestamps = tuple(scene_by_timestamp)
    planned_supplements = _supplement_timestamps(
        scene_timestamps,
        duration_ms=duration_ms,
    )
    selected_scenes, supplement_timestamps = _select_candidate_timestamps(
        scene_timestamps,
        planned_supplements,
        duration_ms=duration_ms,
    )
    scene_frames = tuple(scene_by_timestamp[timestamp] for timestamp in selected_scenes)
    if not supplement_timestamps:
        return scene_frames

    remaining_bytes = _SCENE_OUTPUT_LIMIT_BYTES - sum(
        len(frame.jpeg_bytes) for frame in scene_frames
    )
    final_scene_start_ms = scene_timestamps[-1]
    deadline = time.monotonic() + _SCENE_TIMEOUT_SECONDS
    supplements: list[ExtractedFrame] = []
    for timestamp_ms in supplement_timestamps:
        is_in_final_scene = timestamp_ms > final_scene_start_ms
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
            return output
        frame = _parse_supplement_frame(output)
        if isinstance(frame, AdaptiveFrameRejection):
            return frame
        if frame is None:
            if is_in_final_scene:
                break
            return AdaptiveFrameRejection.UNDECODABLE
        supplements.append(frame)
        remaining_bytes -= len(frame.jpeg_bytes)

    by_timestamp: dict[int, ExtractedFrame] = {}
    for frame in (*scene_frames, *supplements):
        by_timestamp.setdefault(frame.timestamp_ms, frame)
    return tuple(by_timestamp[timestamp] for timestamp in sorted(by_timestamp))


def _frame_limit(duration_ms: int) -> int:
    if duration_ms <= _SHORT_FRAME_LIMIT_DURATION_MS:
        return 6
    if duration_ms <= _MEDIUM_FRAME_LIMIT_DURATION_MS:
        return 12
    if duration_ms <= _LONG_FRAME_LIMIT_DURATION_MS:
        return 24
    if duration_ms <= _EXTRA_LONG_FRAME_LIMIT_DURATION_MS:
        return 40
    return 60


def _select_candidate_timestamps(
    scene_timestamps: tuple[int, ...],
    supplement_timestamps: tuple[int, ...],
    *,
    duration_ms: int,
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    limit = _frame_limit(duration_ms)
    if len(scene_timestamps) >= limit:
        return _uniformly_sample(scene_timestamps, limit), ()
    supplement_limit = limit - len(scene_timestamps)
    return scene_timestamps, _uniformly_sample(supplement_timestamps, supplement_limit)


def _uniformly_sample(timestamps: tuple[int, ...], limit: int) -> tuple[int, ...]:
    if limit < 0:
        raise ValueError("sample limit must not be negative")
    if limit == 0:
        return ()
    if len(timestamps) <= limit:
        return timestamps
    if limit == 1:
        midpoint_numerator = timestamps[0] + timestamps[-1]
        midpoint_index = _nearest_timestamp_index(
            timestamps,
            first_index=0,
            last_index=len(timestamps) - 1,
            target_numerator=midpoint_numerator,
            target_denominator=2,
        )
        return (timestamps[midpoint_index],)

    selected_indices = _globally_uniform_indices(timestamps, limit)
    return tuple(timestamps[index] for index in selected_indices)


def _globally_uniform_indices(timestamps: tuple[int, ...], limit: int) -> tuple[int, ...]:
    if limit == 2:
        return (0, len(timestamps) - 1)

    target_denominator = limit - 1
    time_span = timestamps[-1] - timestamps[0]
    previous_costs = array("q", [-1]) * len(timestamps)
    previous_costs[0] = 0
    parent_rows: list[array[int]] = []

    for position in range(1, limit - 1):
        target_numerator = timestamps[0] * target_denominator + time_span * position
        current_costs = array("q", [-1]) * len(timestamps)
        parents = array("i", [-1]) * len(timestamps)
        best_previous_cost = -1
        best_previous_index = -1

        for candidate_index in range(position, len(timestamps) - limit + position + 1):
            previous_index = candidate_index - 1
            previous_cost = previous_costs[previous_index]
            if previous_cost >= 0 and (
                best_previous_cost < 0 or previous_cost < best_previous_cost
            ):
                best_previous_cost = previous_cost
                best_previous_index = previous_index
            if best_previous_cost < 0:
                continue
            distance = abs(timestamps[candidate_index] * target_denominator - target_numerator)
            current_costs[candidate_index] = best_previous_cost + distance
            parents[candidate_index] = best_previous_index

        previous_costs = current_costs
        parent_rows.append(parents)

    final_internal_index = -1
    final_cost = -1
    for candidate_index in range(limit - 2, len(timestamps) - 1):
        candidate_cost = previous_costs[candidate_index]
        if candidate_cost >= 0 and (final_cost < 0 or candidate_cost < final_cost):
            final_cost = candidate_cost
            final_internal_index = candidate_index
    assert final_internal_index >= 0

    selected_indices = [len(timestamps) - 1, final_internal_index]
    for parents in reversed(parent_rows[1:]):
        parent_index = parents[selected_indices[-1]]
        assert parent_index >= 0
        selected_indices.append(parent_index)
    selected_indices.append(0)
    selected_indices.reverse()
    return tuple(selected_indices)


def _nearest_timestamp_index(
    timestamps: tuple[int, ...],
    *,
    first_index: int,
    last_index: int,
    target_numerator: int,
    target_denominator: int,
) -> int:
    return min(
        range(first_index, last_index + 1),
        key=lambda index: abs(timestamps[index] * target_denominator - target_numerator),
    )


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
        f"settb=1/1000,{_JPEG_SCALE_FILTER}",
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
        "-pix_fmt",
        "yuvj420p",
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
        "-pix_fmt",
        "yuvj420p",
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


def _output_workspace_identity(output_directory: Path) -> tuple[int, int] | None:
    try:
        metadata = output_directory.lstat()
    except (OSError, ValueError, TypeError):
        return None
    if not stat.S_ISDIR(metadata.st_mode) or _is_reparse_point(metadata):
        return None
    return metadata.st_dev, metadata.st_ino


def _open_output_workspace(output_directory: Path) -> _OutputWorkspace | None:
    identity = _output_workspace_identity(output_directory)
    if identity is None:
        return None
    if os.name == "nt":
        handle: int | None = None
        try:
            handle = _open_windows_directory_handle(output_directory)
            current = _windows_directory_path(handle)
            if _output_workspace_identity(current) != identity:
                raise OSError("output workspace changed while opening")
            return _OutputWorkspace(
                path=output_directory,
                identity=identity,
                windows_handle=handle,
            )
        except OSError:
            if handle is not None:
                with suppress(OSError):
                    _close_windows_directory_handle(handle)
            return None

    descriptor: int | None = None
    try:
        flags = os.O_RDONLY
        flags |= cast(int, getattr(os, "O_DIRECTORY", 0))
        flags |= cast(int, getattr(os, "O_NOFOLLOW", 0))
        descriptor = os.open(output_directory, flags)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or _is_reparse_point(metadata)
            or (metadata.st_dev, metadata.st_ino) != identity
        ):
            raise OSError("output workspace changed while opening")
        return _OutputWorkspace(
            path=output_directory,
            identity=identity,
            directory_descriptor=descriptor,
        )
    except OSError:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
        return None


def _write_final_frames(
    workspace: _OutputWorkspace,
    candidates: tuple[ExtractedFrame, ...],
) -> tuple[AdaptiveFrameArtifact, ...] | AdaptiveFrameRejection:
    created: list[str] = []
    artifacts: list[AdaptiveFrameArtifact] = []
    try:
        for index, candidate in enumerate(candidates, start=1):
            workspace.revalidate_path()
            filename = (
                f"{_FINAL_OUTPUT_PREFIX}{index:0{_FINAL_OUTPUT_DIGITS}d}{_FINAL_OUTPUT_SUFFIX}"
            )
            _write_exclusive_frame(workspace, filename, candidate.jpeg_bytes, created)
            artifacts.append(
                AdaptiveFrameArtifact(
                    filename=filename,
                    timestamp_ms=candidate.timestamp_ms,
                    is_scene_cut=candidate.is_scene_cut,
                    byte_size=len(candidate.jpeg_bytes),
                )
            )
        workspace.fsync()
        workspace.revalidate_path()
        for filename, artifact in zip(created, artifacts, strict=True):
            _require_written_frame(workspace, filename, artifact.byte_size)
        return tuple(artifacts)
    except BaseException as error:
        for filename in reversed(created):
            with suppress(OSError):
                workspace.unlink(filename)
        with suppress(OSError):
            workspace.fsync()
        if isinstance(error, OSError):
            return AdaptiveFrameRejection.WORKSPACE_UNUSABLE
        raise


def _write_exclusive_frame(
    workspace: _OutputWorkspace,
    filename: str,
    payload: bytes,
    created: list[str],
) -> None:
    descriptor: int | None = None
    opened = False
    try:
        descriptor = workspace.open_exclusive(filename)
        opened = True
        created.append(filename)
        if os.name != "nt":  # pragma: no branch - native platform split
            cast(Callable[[int, int], None], vars(os)["fchmod"])(descriptor, 0o600)
        view = memoryview(payload)
        written = 0
        while written < len(view):
            count = os.write(descriptor, view[written:])
            if count <= 0:
                raise OSError("short frame write")
            written += count
        os.fsync(descriptor)
        _validate_written_frame(os.fstat(descriptor), len(payload))
        closing_descriptor = descriptor
        descriptor = None
        os.close(closing_descriptor)
    except BaseException:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
        if opened and (not created or created[-1] != filename):
            with suppress(OSError):
                workspace.unlink(filename)
        raise


def _require_written_frame(
    workspace: _OutputWorkspace,
    filename: str,
    expected_size: int,
) -> None:
    metadata = workspace.stat_frame(filename)
    _validate_written_frame(metadata, expected_size)


def _validate_written_frame(metadata: os.stat_result, expected_size: int) -> None:
    if (
        not stat.S_ISREG(metadata.st_mode)
        or _is_reparse_point(metadata)
        or metadata.st_nlink != 1
        or metadata.st_size != expected_size
        or (os.name != "nt" and stat.S_IMODE(metadata.st_mode) != 0o600)
    ):
        raise OSError("final frame changed")


def _is_reparse_point(metadata: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    attributes = getattr(metadata, "st_file_attributes", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(reparse_flag and attributes & reparse_flag)


def _open_windows_directory_handle(path: Path) -> int:
    if os.name != "nt":
        raise OSError("Windows directory handles are unavailable")
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.CDLL("kernel32.dll", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        os.fspath(path),
        0x0001,  # FILE_LIST_DIRECTORY
        0x00000001 | 0x00000002,  # FILE_SHARE_READ | FILE_SHARE_WRITE
        None,
        3,  # OPEN_EXISTING
        0x02000000 | 0x00200000,  # BACKUP_SEMANTICS | OPEN_REPARSE_POINT
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if handle in {None, invalid_handle}:
        raise OSError(_windows_last_error(ctypes), "cannot open output workspace")
    return cast(int, handle)


def _windows_directory_path(handle: int) -> Path:
    if os.name != "nt":
        raise OSError("Windows directory handles are unavailable")
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.CDLL("kernel32.dll", use_last_error=True)
    get_final_path = kernel32.GetFinalPathNameByHandleW
    get_final_path.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    get_final_path.restype = wintypes.DWORD
    required = get_final_path(handle, None, 0, 0)
    if required == 0:
        raise OSError(_windows_last_error(ctypes), "cannot resolve output workspace")
    buffer = ctypes.create_unicode_buffer(required + 1)
    written = get_final_path(handle, buffer, len(buffer), 0)
    if written == 0 or written >= len(buffer):
        raise OSError(_windows_last_error(ctypes), "cannot resolve output workspace")
    return Path(buffer.value)


def _close_windows_directory_handle(handle: int) -> None:
    if os.name != "nt":
        raise OSError("Windows directory handles are unavailable")
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.CDLL("kernel32.dll", use_last_error=True)
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    if not close_handle(handle):
        raise OSError(_windows_last_error(ctypes), "cannot close output workspace")


def _windows_last_error(ctypes_module: object) -> int:
    getter = vars(ctypes_module).get("get_last_error")
    if not callable(getter):
        raise OSError("Windows last-error state is unavailable")
    value = getter()
    return value if type(value) is int else 0


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
