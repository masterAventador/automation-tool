"""Bounded local frame extraction with packaged FFmpeg."""

from __future__ import annotations

import shutil
import stat
import subprocess
import tempfile
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

_OUTPUT_POLL_SECONDS = 0.02
_PROCESS_REAP_SECONDS = 5.0
_SCRATCH_PREFIX = "automation-tool-frame-extraction-"


class AdaptiveFrameRejection(StrEnum):
    """Closed reasons that callers may safely turn into user actions."""

    UNDECODABLE = "undecodable"
    TIMED_OUT = "timed_out"
    OUTPUT_LIMIT_EXCEEDED = "output_limit_exceeded"
    WORKSPACE_UNUSABLE = "workspace_unusable"
    TOOL_FAILED = "tool_failed"


@dataclass(frozen=True, slots=True)
class BoundedFfmpegOutput:
    """Files written by one successful bounded FFmpeg pass."""

    files: tuple[tuple[str, bytes], ...]


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

    try:
        with process:
            ended = _wait_for_bounded_output(
                process,
                workspace,
                seconds=seconds,
                output_limit_bytes=output_limit_bytes,
            )
    except BaseException:
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
        return AdaptiveFrameRejection.UNDECODABLE
    try:
        return BoundedFfmpegOutput(
            files=tuple((path.name, path.read_bytes()) for path in paths),
        )
    except OSError:
        return AdaptiveFrameRejection.WORKSPACE_UNUSABLE


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
