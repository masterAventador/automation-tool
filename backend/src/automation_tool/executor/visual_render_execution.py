"""Bounded FFmpeg execution, ffprobe validation and atomic visual publication."""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import tempfile
import time
from collections.abc import Callable
from contextlib import ExitStack, suppress
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from fractions import Fraction
from pathlib import Path
from typing import BinaryIO, Final, Never

from automation_tool.executor.caption_overlay import (
    CaptionOverlayRejected,
    VisualCaptionOverlaySet,
    render_caption_overlay_set,
)
from automation_tool.executor.material_probe import (
    MAX_PATH_CHARACTERS,
    MaterialProbeRejected,
    PackagedMediaTools,
    require_source_unchanged,
)
from automation_tool.executor.visual_rendering import (
    VisualFilterGraphRejected,
    VisualFilterGraphRejection,
    VisualRenderSourceBinding,
    compile_visual_ffmpeg_command,
)
from automation_tool.protocol.local_rendering import (
    MAX_LOCAL_EDITING_OUTPUT_DIMENSION,
    MAX_LOCAL_EDITING_OUTPUT_FPS,
    MAX_LOCAL_EDITING_RENDER_DURATION_MS,
    MIN_LOCAL_EDITING_OUTPUT_DIMENSION,
    MIN_LOCAL_EDITING_OUTPUT_FPS,
    LocalEditingCaptionRenderPlan,
    LocalEditingVisualRenderPlan,
)
from automation_tool.protocol.safe_text import contains_control_or_bidi

VISUAL_RENDER_OUTPUT_FILENAME: Final = "render.mp4"
VISUAL_RENDER_MAX_OUTPUT_BYTES: Final = 8 * 1024 * 1024 * 1024

_FFMPEG_TIMEOUT_SECONDS: Final = 15 * 60.0
_FFPROBE_TIMEOUT_SECONDS: Final = 30.0
_PROCESS_STOP_SECONDS: Final = 5.0
_PROCESS_POLL_SECONDS: Final = 0.05
_FFPROBE_STDOUT_LIMIT_BYTES: Final = 1024 * 1024
_CREATE_NEW_PROCESS_GROUP: Final = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}\Z")


class VisualRenderExecutionRejection(StrEnum):
    INVALID_REQUEST = "invalid_request"
    TOOL_UNAVAILABLE = "tool_unavailable"
    SOURCE_CHANGED = "source_changed"
    WORKSPACE_UNUSABLE = "workspace_unusable"
    OUTPUT_EXISTS = "output_exists"
    CAPTION_FAILED = "caption_failed"
    PROCESS_FAILED = "process_failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    OUTPUT_TOO_LARGE = "output_too_large"
    OUTPUT_INVALID = "output_invalid"


class VisualRenderExecutionRejected(RuntimeError):
    """One local render failed with a closed code and no private diagnostics."""

    def __init__(self, code: VisualRenderExecutionRejection) -> None:
        self.code = code
        super().__init__("visual render execution rejected")


def _reject(code: VisualRenderExecutionRejection) -> Never:
    raise VisualRenderExecutionRejected(code) from None


@dataclass(frozen=True, slots=True, repr=False)
class VisualRenderReceipt:
    """Verified final media facts without its local destination path."""

    frame_count: int
    width: int
    height: int
    fps: int
    duration_ms: int
    bytes_written: int
    sha256: str

    def __post_init__(self) -> None:
        if (
            type(self.frame_count) is not int
            or self.frame_count < 1
            or type(self.width) is not int
            or not MIN_LOCAL_EDITING_OUTPUT_DIMENSION
            <= self.width
            <= MAX_LOCAL_EDITING_OUTPUT_DIMENSION
            or self.width % 2 != 0
            or type(self.height) is not int
            or not MIN_LOCAL_EDITING_OUTPUT_DIMENSION
            <= self.height
            <= MAX_LOCAL_EDITING_OUTPUT_DIMENSION
            or self.height % 2 != 0
            or type(self.fps) is not int
            or not MIN_LOCAL_EDITING_OUTPUT_FPS <= self.fps <= MAX_LOCAL_EDITING_OUTPUT_FPS
            or type(self.duration_ms) is not int
            or not 1 <= self.duration_ms <= MAX_LOCAL_EDITING_RENDER_DURATION_MS
            or type(self.bytes_written) is not int
            or not 1 <= self.bytes_written <= VISUAL_RENDER_MAX_OUTPUT_BYTES
            or type(self.sha256) is not str
            or _SHA256_PATTERN.fullmatch(self.sha256) is None
        ):
            _reject(VisualRenderExecutionRejection.OUTPUT_INVALID)

    def __repr__(self) -> str:
        return "VisualRenderReceipt(<redacted>)"


@dataclass(frozen=True, slots=True)
class _ProcessResult:
    returncode: int
    stdout: bytes


class _ProcessTimedOut(RuntimeError):
    pass


class _ProcessCancelled(RuntimeError):
    pass


class _ProcessOutputTooLarge(RuntimeError):
    pass


def _valid_path(value: object) -> bool:
    if not isinstance(value, Path) or not value.is_absolute():
        return False
    text = os.fspath(value)
    return 1 <= len(text) <= MAX_PATH_CHARACTERS and not contains_control_or_bidi(text)


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    with suppress(OSError, ProcessLookupError):
        if os.name == "nt":
            process.terminate()
        else:
            os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=_PROCESS_STOP_SECONDS)
        return
    except subprocess.TimeoutExpired:
        pass
    with suppress(OSError, ProcessLookupError):
        if os.name == "nt":
            process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)
    with suppress(subprocess.TimeoutExpired):
        process.wait(timeout=_PROCESS_STOP_SECONDS)


def _run_bounded_process(
    argv: tuple[str, ...],
    *,
    timeout_seconds: float,
    cancel_requested: Callable[[], bool] | None,
    stdout_limit_bytes: int = 0,
    output_path: Path | None = None,
    output_limit_bytes: int = 0,
) -> _ProcessResult:
    """Run one argv without a shell; keep captured output bounded on disk."""

    deadline = time.monotonic() + timeout_seconds
    with ExitStack() as resources:
        captured: BinaryIO | None = None
        if stdout_limit_bytes:
            captured = resources.enter_context(tempfile.TemporaryFile())
        stdout_target: int | BinaryIO = (
            captured if captured is not None else subprocess.DEVNULL
        )
        creationflags = _CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=stdout_target,
            stderr=subprocess.DEVNULL,
            shell=False,
            start_new_session=os.name != "nt",
            creationflags=creationflags,
        )
        try:
            while process.poll() is None:
                if (
                    captured is not None
                    and os.fstat(captured.fileno()).st_size > stdout_limit_bytes
                ):
                    raise _ProcessOutputTooLarge
                output_stat: os.stat_result | None = None
                if output_path is not None and output_limit_bytes:
                    with suppress(OSError):
                        output_stat = output_path.lstat()
                if (
                    output_stat is not None
                    and output_path is not None
                    and stat.S_ISREG(output_stat.st_mode)
                    and not output_path.is_symlink()
                    and output_stat.st_size > output_limit_bytes
                ):
                    raise _ProcessOutputTooLarge
                if cancel_requested is not None and cancel_requested():
                    raise _ProcessCancelled
                if time.monotonic() >= deadline:
                    raise _ProcessTimedOut
                time.sleep(_PROCESS_POLL_SECONDS)
        except BaseException:
            _stop_process(process)
            raise
        returncode = process.wait()
        if not stdout_limit_bytes:
            return _ProcessResult(returncode=returncode, stdout=b"")
        assert captured is not None
        captured.seek(0, os.SEEK_END)
        if captured.tell() > stdout_limit_bytes:
            raise _ProcessOutputTooLarge
        captured.seek(0)
        return _ProcessResult(returncode=returncode, stdout=captured.read())


def _checked_sources(
    sources: tuple[VisualRenderSourceBinding, ...],
    approvals: tuple[os.stat_result, ...],
) -> tuple[tuple[VisualRenderSourceBinding, ...], tuple[os.stat_result, ...]]:
    if (
        not isinstance(sources, tuple)
        or not isinstance(approvals, tuple)
        or len(sources) != len(approvals)
        or not sources
        or not all(isinstance(source, VisualRenderSourceBinding) for source in sources)
        or not all(isinstance(approved, os.stat_result) for approved in approvals)
    ):
        _reject(VisualRenderExecutionRejection.INVALID_REQUEST)
    checked_sources: list[VisualRenderSourceBinding] = []
    checked_stats: list[os.stat_result] = []
    source_changed = False
    try:
        for source, approved in zip(sources, approvals, strict=True):
            path, checked = require_source_unchanged(source.source_path, approved)
            checked_sources.append(
                VisualRenderSourceBinding(
                    material_id=source.material_id,
                    kind=source.kind,
                    source_path=path,
                )
            )
            checked_stats.append(checked)
    except MaterialProbeRejected:
        source_changed = True
    if source_changed:
        _reject(VisualRenderExecutionRejection.SOURCE_CHANGED)
    return tuple(checked_sources), tuple(checked_stats)


def _stat_file(path: Path) -> os.stat_result:
    metadata: os.stat_result | None = None
    with suppress(OSError):
        metadata = path.lstat()
    if metadata is None or not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
        _reject(VisualRenderExecutionRejection.OUTPUT_INVALID)
    return metadata


def _same_file(before: os.stat_result, after: os.stat_result) -> bool:
    return (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    ) == (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )


def _caption_unchanged(path: Path, before: os.stat_result) -> bool:
    try:
        after = _stat_file(path)
    except VisualRenderExecutionRejected:
        return False
    return _same_file(before, after)


def _ffprobe_argv(tools: PackagedMediaTools, output: Path) -> tuple[str, ...]:
    return (
        os.fspath(tools.ffprobe_path),
        "-v",
        "error",
        "-count_frames",
        "-show_entries",
        (
            "stream=codec_type,codec_name,width,height,avg_frame_rate,nb_read_frames:"
            "format=duration,size"
        ),
        "-of",
        "json",
        os.fspath(output),
    )


def _parse_positive_int(value: object) -> int:
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
        _reject(VisualRenderExecutionRejection.OUTPUT_INVALID)
    parsed = int(value)
    if parsed < 1:
        _reject(VisualRenderExecutionRejection.OUTPUT_INVALID)
    return parsed


def _verified_receipt(
    payload: bytes,
    *,
    plan: LocalEditingVisualRenderPlan,
    target_frames: int,
    output_stat: os.stat_result,
    digest: str,
) -> VisualRenderReceipt:
    frame_count = 0
    container_size = 0
    duration = Decimal(0)
    payload_invalid = False
    try:
        document = json.loads(payload)
        streams = document["streams"]
        container = document["format"]
        if not isinstance(streams, list) or len(streams) != 1 or not isinstance(container, dict):
            _reject(VisualRenderExecutionRejection.OUTPUT_INVALID)
        stream = streams[0]
        if not isinstance(stream, dict):
            _reject(VisualRenderExecutionRejection.OUTPUT_INVALID)
        if (
            stream.get("codec_type") != "video"
            or stream.get("codec_name") != "h264"
            or type(stream.get("width")) is not int
            or stream["width"] != plan.output_width
            or type(stream.get("height")) is not int
            or stream["height"] != plan.output_height
            or not isinstance(stream.get("avg_frame_rate"), str)
            or Fraction(stream["avg_frame_rate"]) != plan.output_fps
        ):
            _reject(VisualRenderExecutionRejection.OUTPUT_INVALID)
        frame_count = _parse_positive_int(stream.get("nb_read_frames"))
        container_size = _parse_positive_int(container.get("size"))
        if not isinstance(container.get("duration"), str):
            _reject(VisualRenderExecutionRejection.OUTPUT_INVALID)
        duration = Decimal(container["duration"])
        if not duration.is_finite() or duration <= 0:
            _reject(VisualRenderExecutionRejection.OUTPUT_INVALID)
    except VisualRenderExecutionRejected:
        raise
    except (
        KeyError,
        TypeError,
        ValueError,
        ZeroDivisionError,
        InvalidOperation,
        json.JSONDecodeError,
    ):
        payload_invalid = True
    if payload_invalid:
        _reject(VisualRenderExecutionRejection.OUTPUT_INVALID)
    expected_duration = Decimal(target_frames) / Decimal(plan.output_fps)
    if (
        frame_count != target_frames
        or container_size != output_stat.st_size
        or abs(duration - expected_duration) > Decimal("0.001")
    ):
        _reject(VisualRenderExecutionRejection.OUTPUT_INVALID)
    duration_ms = (target_frames * 1000 + plan.output_fps // 2) // plan.output_fps
    return VisualRenderReceipt(
        frame_count=frame_count,
        width=plan.output_width,
        height=plan.output_height,
        fps=plan.output_fps,
        duration_ms=duration_ms,
        bytes_written=container_size,
        sha256=digest,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    read_failed = False
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    except OSError:
        read_failed = True
    if read_failed:
        _reject(VisualRenderExecutionRejection.OUTPUT_INVALID)
    return digest.hexdigest()


def _remove(path: Path | None) -> None:
    if path is not None:
        with suppress(OSError):
            path.unlink(missing_ok=True)


def execute_visual_render(
    tools: PackagedMediaTools,
    plan: LocalEditingVisualRenderPlan,
    sources: tuple[VisualRenderSourceBinding, ...],
    approved_sources: tuple[os.stat_result, ...],
    task_directory: Path,
    *,
    caption_plan: LocalEditingCaptionRenderPlan | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> VisualRenderReceipt:
    """Render, verify and publish one fixed-name MP4, or leave no new media."""

    if (
        not isinstance(tools, PackagedMediaTools)
        or not isinstance(plan, LocalEditingVisualRenderPlan)
        or (
            caption_plan is not None and not isinstance(caption_plan, LocalEditingCaptionRenderPlan)
        )
        or (cancel_requested is not None and not callable(cancel_requested))
        or not _valid_path(task_directory)
    ):
        _reject(VisualRenderExecutionRejection.INVALID_REQUEST)
    resolved_directory: Path | None = None
    with suppress(OSError):
        resolved_directory = task_directory.resolve(strict=True)
    if (
        resolved_directory is None
        or task_directory.is_symlink()
        or not task_directory.is_dir()
        or resolved_directory != task_directory
    ):
        _reject(VisualRenderExecutionRejection.WORKSPACE_UNUSABLE)

    final_output = task_directory / VISUAL_RENDER_OUTPUT_FILENAME
    if final_output.exists() or final_output.is_symlink():
        _reject(VisualRenderExecutionRejection.OUTPUT_EXISTS)

    working_output: Path | None = None
    overlays: VisualCaptionOverlaySet | None = None
    caption_stats: tuple[os.stat_result, ...] = ()
    deferred_rejection: VisualRenderExecutionRejection | None = None
    try:
        checked_sources, checked_stats = _checked_sources(sources, approved_sources)
        if cancel_requested is not None and cancel_requested():
            _reject(VisualRenderExecutionRejection.CANCELLED)
        if caption_plan is not None:
            overlays = render_caption_overlay_set(caption_plan, task_directory)
            caption_stats = tuple(_stat_file(item.source_path) for item in overlays.captions)
        checked_sources, checked_stats = _checked_sources(checked_sources, checked_stats)
        descriptor: int | None = None
        working_name: str | None = None
        allocation_failed = False
        try:
            descriptor, working_name = tempfile.mkstemp(
                dir=task_directory,
                prefix=".visual-render-",
                suffix=".mp4",
            )
            os.close(descriptor)
            working_output = Path(working_name)
        except OSError:
            allocation_failed = True
        if allocation_failed or working_output is None:
            if descriptor is not None:
                with suppress(OSError):
                    os.close(descriptor)
            if working_name is not None:
                _remove(Path(working_name))
            _reject(VisualRenderExecutionRejection.WORKSPACE_UNUSABLE)

        command = compile_visual_ffmpeg_command(
            tools,
            plan,
            checked_sources,
            working_output,
            caption_overlays=overlays,
        )
        rendered = _run_bounded_process(
            command.argv,
            timeout_seconds=_FFMPEG_TIMEOUT_SECONDS,
            cancel_requested=cancel_requested,
            output_path=working_output,
            output_limit_bytes=VISUAL_RENDER_MAX_OUTPUT_BYTES,
        )
        _checked_sources(checked_sources, checked_stats)
        if overlays is not None and any(
            not _caption_unchanged(item.source_path, before)
            for item, before in zip(overlays.captions, caption_stats, strict=True)
        ):
            _reject(VisualRenderExecutionRejection.SOURCE_CHANGED)
        if rendered.returncode != 0:
            _reject(VisualRenderExecutionRejection.PROCESS_FAILED)

        output_stat = _stat_file(working_output)
        if output_stat.st_size < 1:
            _reject(VisualRenderExecutionRejection.OUTPUT_INVALID)
        if output_stat.st_size > VISUAL_RENDER_MAX_OUTPUT_BYTES:
            _reject(VisualRenderExecutionRejection.OUTPUT_TOO_LARGE)
        tool_unavailable = False
        try:
            tools.revalidate()
        except MaterialProbeRejected:
            tool_unavailable = True
        if tool_unavailable:
            _reject(VisualRenderExecutionRejection.TOOL_UNAVAILABLE)
        probe = _run_bounded_process(
            _ffprobe_argv(tools, working_output),
            timeout_seconds=_FFPROBE_TIMEOUT_SECONDS,
            cancel_requested=cancel_requested,
            stdout_limit_bytes=_FFPROBE_STDOUT_LIMIT_BYTES,
        )
        if probe.returncode != 0:
            _reject(VisualRenderExecutionRejection.PROCESS_FAILED)
        after_probe = _stat_file(working_output)
        if not _same_file(output_stat, after_probe):
            _reject(VisualRenderExecutionRejection.OUTPUT_INVALID)
        digest = _sha256(working_output)
        after_digest = _stat_file(working_output)
        if not _same_file(after_probe, after_digest):
            _reject(VisualRenderExecutionRejection.OUTPUT_INVALID)
        receipt = _verified_receipt(
            probe.stdout,
            plan=plan,
            target_frames=command.target_frames,
            output_stat=after_digest,
            digest=digest,
        )
        publication_rejection: VisualRenderExecutionRejection | None = None
        try:
            os.link(working_output, final_output)
        except FileExistsError:
            publication_rejection = VisualRenderExecutionRejection.OUTPUT_EXISTS
        except OSError:
            publication_rejection = VisualRenderExecutionRejection.WORKSPACE_UNUSABLE
        if publication_rejection is not None:
            _reject(publication_rejection)
        _remove(working_output)
        working_output = None
        return receipt
    except _ProcessCancelled:
        deferred_rejection = VisualRenderExecutionRejection.CANCELLED
    except _ProcessTimedOut:
        deferred_rejection = VisualRenderExecutionRejection.TIMED_OUT
    except _ProcessOutputTooLarge:
        deferred_rejection = VisualRenderExecutionRejection.OUTPUT_TOO_LARGE
    except MaterialProbeRejected:
        deferred_rejection = VisualRenderExecutionRejection.SOURCE_CHANGED
    except CaptionOverlayRejected:
        deferred_rejection = VisualRenderExecutionRejection.CAPTION_FAILED
    except VisualFilterGraphRejected as rejected:
        if rejected.code is VisualFilterGraphRejection.TOOL_UNAVAILABLE:
            deferred_rejection = VisualRenderExecutionRejection.TOOL_UNAVAILABLE
        else:
            deferred_rejection = VisualRenderExecutionRejection.INVALID_REQUEST
    except VisualRenderExecutionRejected:
        raise
    except OSError:
        deferred_rejection = VisualRenderExecutionRejection.WORKSPACE_UNUSABLE
    except Exception:
        deferred_rejection = VisualRenderExecutionRejection.PROCESS_FAILED
    finally:
        _remove(working_output)
        if overlays is not None:
            for caption in overlays.captions:
                _remove(caption.source_path)
    assert deferred_rejection is not None
    if deferred_rejection is VisualRenderExecutionRejection.WORKSPACE_UNUSABLE:
        tool_unavailable = False
        try:
            tools.revalidate()
        except MaterialProbeRejected:
            tool_unavailable = True
        if tool_unavailable:
            deferred_rejection = VisualRenderExecutionRejection.TOOL_UNAVAILABLE
    _reject(deferred_rejection)


__all__ = [
    "VISUAL_RENDER_MAX_OUTPUT_BYTES",
    "VISUAL_RENDER_OUTPUT_FILENAME",
    "VisualRenderExecutionRejected",
    "VisualRenderExecutionRejection",
    "VisualRenderReceipt",
    "execute_visual_render",
]
