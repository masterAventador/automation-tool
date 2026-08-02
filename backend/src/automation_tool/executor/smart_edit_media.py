"""Bounded full-picture verification for local smart-editing selection."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from contextlib import suppress
from enum import StrEnum
from pathlib import Path
from typing import NoReturn
from uuid import UUID

from automation_tool.executor.material_probe import (
    MAX_MATERIAL_DURATION_MS,
    MaterialProbeRejected,
    PackagedMediaTools,
    require_source_unchanged,
)
from automation_tool.executor.segment_selection import (
    VerifiedDecodableInterval,
    VerifiedDecodableMaterial,
)
from automation_tool.protocol.local_editing import is_canonical_local_editing_material_id

_DIGEST = re.compile(r"^[0-9a-f]{64}\Z")
_POLL_SECONDS = 0.05
_MIN_TIMEOUT_SECONDS = 30.0
_MAX_TIMEOUT_SECONDS = 15.0 * 60.0
_TIMEOUT_MULTIPLIER = 2.0
_MAX_PROGRESS_BYTES = 1024 * 1024
_FRAME_PROGRESS = re.compile(rb"^frame=([0-9]+)$")
_OUT_TIME_PROGRESS = re.compile(rb"^out_time_us=([0-9]+)$")


class SmartEditMediaFailureCode(StrEnum):
    CANCELLED = "cancelled"
    MATERIAL_UNAVAILABLE = "material_unavailable"
    TOOL_UNAVAILABLE = "tool_unavailable"
    UNDECODABLE = "undecodable"
    TIMED_OUT = "timed_out"
    WORKSPACE_UNUSABLE = "workspace_unusable"


class SmartEditMediaRejected(RuntimeError):
    """Full-picture verification failed without exposing the local source."""

    def __init__(self, code: SmartEditMediaFailureCode) -> None:
        if not isinstance(code, SmartEditMediaFailureCode):
            raise TypeError("smart edit media rejected") from None
        self.code = code
        super().__init__("smart edit media rejected")

    def __repr__(self) -> str:
        return "SmartEditMediaRejected(<redacted>)"


def _reject(code: SmartEditMediaFailureCode) -> NoReturn:
    raise SmartEditMediaRejected(code) from None


def _cancelled(probe: Callable[[], bool]) -> bool | None:
    try:
        result = probe()
    except Exception:
        return None
    return result if type(result) is bool else None


def _timeout(duration_ms: int) -> float:
    return min(
        _MAX_TIMEOUT_SECONDS,
        max(_MIN_TIMEOUT_SECONDS, duration_ms / 1_000 * _TIMEOUT_MULTIPLIER),
    )


def _stop(process: subprocess.Popen[bytes]) -> None:
    with suppress(OSError):
        process.kill()
    with suppress(OSError, subprocess.TimeoutExpired):
        process.wait(timeout=5)


def _decode(
    ffmpeg: Path,
    source: Path,
    *,
    duration_ms: int,
    cancellation_requested: Callable[[], bool],
) -> int | SmartEditMediaFailureCode:
    try:
        workspace = Path(tempfile.mkdtemp(prefix="automation-tool-smart-edit-decode-"))
    except OSError:
        return SmartEditMediaFailureCode.WORKSPACE_UNUSABLE
    try:
        return _decode_with_progress(
            ffmpeg,
            source,
            progress_path=workspace / "progress",
            duration_ms=duration_ms,
            cancellation_requested=cancellation_requested,
        )
    finally:
        with suppress(OSError):
            shutil.rmtree(workspace)


def _decode_with_progress(
    ffmpeg: Path,
    source: Path,
    *,
    progress_path: Path,
    duration_ms: int,
    cancellation_requested: Callable[[], bool],
) -> int | SmartEditMediaFailureCode:
    requested = _cancelled(cancellation_requested)
    if requested is None:
        return SmartEditMediaFailureCode.MATERIAL_UNAVAILABLE
    if requested:
        return SmartEditMediaFailureCode.CANCELLED
    try:
        sink = progress_path.open("xb")
    except OSError:
        return SmartEditMediaFailureCode.WORKSPACE_UNUSABLE
    returncode: int | None = None
    with sink:
        try:
            process = subprocess.Popen(
                [
                    os.fspath(ffmpeg),
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-xerror",
                    "-progress",
                    "pipe:1",
                    "-nostats",
                    "-i",
                    os.fspath(source),
                    "-map",
                    "0:v:0",
                    "-f",
                    "null",
                    os.devnull,
                ],
                stdin=subprocess.DEVNULL,
                stdout=sink,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            return SmartEditMediaFailureCode.TOOL_UNAVAILABLE
        deadline = time.monotonic() + _timeout(duration_ms)
        with process:
            while returncode is None:
                requested = _cancelled(cancellation_requested)
                if requested is None:
                    _stop(process)
                    return SmartEditMediaFailureCode.MATERIAL_UNAVAILABLE
                if requested:
                    _stop(process)
                    return SmartEditMediaFailureCode.CANCELLED
                try:
                    if progress_path.stat().st_size > _MAX_PROGRESS_BYTES:
                        _stop(process)
                        return SmartEditMediaFailureCode.UNDECODABLE
                    returncode = process.poll()
                except OSError:
                    _stop(process)
                    return SmartEditMediaFailureCode.WORKSPACE_UNUSABLE
                if returncode is None and time.monotonic() >= deadline:
                    _stop(process)
                    return SmartEditMediaFailureCode.TIMED_OUT
                if returncode is None:
                    time.sleep(_POLL_SECONDS)
    if returncode != 0:
        return SmartEditMediaFailureCode.UNDECODABLE
    try:
        payload = progress_path.read_bytes()
    except OSError:
        return SmartEditMediaFailureCode.WORKSPACE_UNUSABLE
    if len(payload) > _MAX_PROGRESS_BYTES:
        return SmartEditMediaFailureCode.UNDECODABLE
    frame_count = 0
    decoded_microseconds = 0
    ended = False
    for line in payload.splitlines():
        match = _FRAME_PROGRESS.fullmatch(line)
        if match is not None:
            frame_count = max(frame_count, int(match.group(1)))
        else:
            time_match = _OUT_TIME_PROGRESS.fullmatch(line)
            if time_match is not None:
                decoded_microseconds = max(
                    decoded_microseconds,
                    int(time_match.group(1)),
                )
        if line == b"progress=end":
            ended = True
    decoded_ms = decoded_microseconds // 1_000
    if frame_count <= 0 or decoded_ms <= 0 or not ended:
        return SmartEditMediaFailureCode.UNDECODABLE
    return decoded_ms


def verify_decodable_video(
    tools: PackagedMediaTools,
    source: Path,
    approved: os.stat_result,
    *,
    material_id: UUID,
    content_digest: str,
    duration_ms: int,
    cancellation_requested: Callable[[], bool],
) -> VerifiedDecodableMaterial:
    """Decode every picture frame and return a single proven half-open interval."""

    if (
        not isinstance(tools, PackagedMediaTools)
        or not isinstance(source, Path)
        or not isinstance(approved, os.stat_result)
        or not is_canonical_local_editing_material_id(material_id)
        or type(content_digest) is not str
        or _DIGEST.fullmatch(content_digest) is None
        or type(duration_ms) is not int
        or not 1 <= duration_ms <= MAX_MATERIAL_DURATION_MS
        or not callable(cancellation_requested)
    ):
        _reject(SmartEditMediaFailureCode.MATERIAL_UNAVAILABLE)
    try:
        tools.revalidate()
    except (MaterialProbeRejected, OSError):
        _reject(SmartEditMediaFailureCode.TOOL_UNAVAILABLE)
    try:
        checked_source, checked = require_source_unchanged(source, approved)
    except (MaterialProbeRejected, OSError):
        _reject(SmartEditMediaFailureCode.MATERIAL_UNAVAILABLE)
    decode_outcome = _decode(
        tools.ffmpeg_path,
        checked_source,
        duration_ms=duration_ms,
        cancellation_requested=cancellation_requested,
    )
    if isinstance(decode_outcome, SmartEditMediaFailureCode):
        _reject(decode_outcome)
    try:
        require_source_unchanged(checked_source, checked)
    except (MaterialProbeRejected, OSError):
        _reject(SmartEditMediaFailureCode.MATERIAL_UNAVAILABLE)
    return VerifiedDecodableMaterial(
        material_id=material_id,
        content_digest=content_digest,
        intervals=(
            VerifiedDecodableInterval(
                start_ms=0,
                end_ms=min(duration_ms, decode_outcome),
            ),
        ),
    )


__all__ = [
    "SmartEditMediaFailureCode",
    "SmartEditMediaRejected",
    "verify_decodable_video",
]
