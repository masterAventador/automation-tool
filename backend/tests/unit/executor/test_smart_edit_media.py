"""LE-19 T1: prove a registered video's picture is decodable, not just declared."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from uuid import uuid4

import pytest

from automation_tool.executor.material_probe import PackagedMediaTools, approve_source
from automation_tool.executor.smart_edit_media import (
    SmartEditMediaFailureCode,
    SmartEditMediaRejected,
    verify_decodable_video,
)


def _executable(path: Path, body: str) -> Path:
    path.write_text(f"#!/bin/sh\nset -eu\n{body}\n", encoding="utf-8")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    return path


def _tools(tmp_path: Path, *, ffmpeg_body: str) -> PackagedMediaTools:
    return PackagedMediaTools(
        ffprobe_path=_executable(tmp_path / "ffprobe", "exit 0"),
        ffmpeg_path=_executable(tmp_path / "ffmpeg", ffmpeg_body),
    )


def _source(tmp_path: Path) -> tuple[Path, os.stat_result]:
    source = tmp_path / "私密素材.mp4"
    source.write_bytes(b"registered-video")
    return approve_source(source)


def test_successful_full_decode_returns_one_digest_bound_interval(tmp_path: Path) -> None:
    source, approved = _source(tmp_path)
    log = tmp_path / "argv"
    tools = _tools(
        tmp_path,
        ffmpeg_body=(
            f"printf '%s\\n' \"$@\" > '{log}'\n"
            "printf 'frame=25\\nout_time_us=4321000\\nprogress=end\\n'\nexit 0"
        ),
    )
    material_id = uuid4()
    digest = "a" * 64

    result = verify_decodable_video(
        tools,
        source,
        approved,
        material_id=material_id,
        content_digest=digest,
        duration_ms=4_321,
        cancellation_requested=lambda: False,
    )

    assert result.material_id == material_id
    assert result.content_digest == digest
    assert tuple((interval.start_ms, interval.end_ms) for interval in result.intervals) == (
        (0, 4_321),
    )
    arguments = log.read_text(encoding="utf-8").splitlines()
    assert "-xerror" in arguments
    assert "0:v:0" in arguments
    assert arguments[arguments.index("-i") + 1] == os.fspath(source)
    assert os.fspath(source) not in repr(result)


def test_zero_exit_without_any_decoded_picture_is_rejected(tmp_path: Path) -> None:
    source, approved = _source(tmp_path)
    tools = _tools(tmp_path, ffmpeg_body="exit 0")

    with pytest.raises(SmartEditMediaRejected) as captured:
        verify_decodable_video(
            tools,
            source,
            approved,
            material_id=uuid4(),
            content_digest="e" * 64,
            duration_ms=1_000,
            cancellation_requested=lambda: False,
        )

    assert captured.value.code is SmartEditMediaFailureCode.UNDECODABLE


def test_declared_duration_is_clamped_to_the_real_decoded_picture_end(
    tmp_path: Path,
) -> None:
    source, approved = _source(tmp_path)
    tools = _tools(
        tmp_path,
        ffmpeg_body="printf 'frame=10\\nout_time_us=400000\\nprogress=end\\n'",
    )

    result = verify_decodable_video(
        tools,
        source,
        approved,
        material_id=uuid4(),
        content_digest="9" * 64,
        duration_ms=4_000,
        cancellation_requested=lambda: False,
    )

    assert tuple((interval.start_ms, interval.end_ms) for interval in result.intervals) == (
        (0, 400),
    )


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("exit 7", SmartEditMediaFailureCode.UNDECODABLE),
        ("kill -9 $$", SmartEditMediaFailureCode.UNDECODABLE),
    ],
)
def test_failed_or_crashed_decoder_returns_one_closed_reason_without_path(
    tmp_path: Path,
    body: str,
    expected: SmartEditMediaFailureCode,
) -> None:
    source, approved = _source(tmp_path)
    tools = _tools(tmp_path, ffmpeg_body=body)

    with pytest.raises(SmartEditMediaRejected) as captured:
        verify_decodable_video(
            tools,
            source,
            approved,
            material_id=uuid4(),
            content_digest="b" * 64,
            duration_ms=1_000,
            cancellation_requested=lambda: False,
        )

    assert captured.value.code is expected
    assert str(captured.value) == "smart edit media rejected"
    assert captured.value.__cause__ is None
    assert os.fspath(source) not in repr(captured.value)


def test_cooperative_cancel_kills_decoder_and_returns_cancelled(tmp_path: Path) -> None:
    source, approved = _source(tmp_path)
    tools = _tools(tmp_path, ffmpeg_body="exec sleep 10")
    polls = 0

    def cancelled() -> bool:
        nonlocal polls
        polls += 1
        return polls >= 2

    with pytest.raises(SmartEditMediaRejected) as captured:
        verify_decodable_video(
            tools,
            source,
            approved,
            material_id=uuid4(),
            content_digest="c" * 64,
            duration_ms=1_000,
            cancellation_requested=cancelled,
        )

    assert captured.value.code is SmartEditMediaFailureCode.CANCELLED
    assert polls == 2


def test_source_identity_change_during_decode_is_not_reported_as_success(tmp_path: Path) -> None:
    source, approved = _source(tmp_path)
    tools = _tools(
        tmp_path,
        ffmpeg_body=(
            'previous=""\nfor value in "$@"; do\n'
            '  if [ "$previous" = "-i" ]; then target="$value"; fi\n'
            '  previous="$value"\ndone\nprintf x >> "$target"\n'
            "printf 'frame=1\\nout_time_us=1000000\\nprogress=end\\n'"
        ),
    )

    with pytest.raises(SmartEditMediaRejected) as captured:
        verify_decodable_video(
            tools,
            source,
            approved,
            material_id=uuid4(),
            content_digest="d" * 64,
            duration_ms=1_000,
            cancellation_requested=lambda: False,
        )

    assert captured.value.code is SmartEditMediaFailureCode.MATERIAL_UNAVAILABLE
    assert os.fspath(source) not in repr(captured.value)
