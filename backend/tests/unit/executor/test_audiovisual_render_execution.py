"""LE-11 T4: bounded audiovisual execution and audio-shape verification."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest

from automation_tool.executor import visual_render_execution as execution
from automation_tool.executor.audio_rendering import (
    AudioRenderBindingRejected,
    AudioRenderBindingRejection,
    AudioRenderSourceBinding,
)
from automation_tool.executor.audiovisual_rendering import (
    AudiovisualRenderRejected,
    AudiovisualRenderRejection,
)
from automation_tool.executor.material_probe import PackagedMediaTools, approve_source
from automation_tool.executor.visual_render_execution import (
    VISUAL_RENDER_OUTPUT_FILENAME,
    VisualRenderExecutionRejected,
    VisualRenderExecutionRejection,
    VisualRenderReceipt,
    execute_audiovisual_render,
)
from automation_tool.executor.visual_rendering import VisualRenderSourceBinding
from automation_tool.protocol.local_editing import SegmentSelectionMaterialKind
from automation_tool.protocol.local_rendering import (
    LocalEditingAudioRenderClip,
    LocalEditingAudioRenderPlan,
    LocalEditingAudioTrackKind,
    LocalEditingOriginalAudioMode,
    LocalEditingVisualRenderClip,
    LocalEditingVisualRenderPlan,
)


def _executable(directory: Path, name: str) -> Path:
    path = directory / name
    path.write_bytes(b"#!/bin/sh\nexit 0\n")
    path.chmod(0o700)
    return path


def _tools(directory: Path) -> PackagedMediaTools:
    return PackagedMediaTools(
        ffprobe_path=_executable(directory, "ffprobe"),
        ffmpeg_path=_executable(directory, "ffmpeg"),
    )


def _request(
    tmp_path: Path,
    *,
    audio_kind: LocalEditingAudioTrackKind = LocalEditingAudioTrackKind.NARRATION,
    audio_mode: LocalEditingOriginalAudioMode | None = None,
    has_audio: bool = True,
    create_audio: bool = True,
) -> tuple[
    LocalEditingVisualRenderPlan,
    tuple[VisualRenderSourceBinding, ...],
    tuple[os.stat_result, ...],
    LocalEditingAudioRenderPlan,
    tuple[AudioRenderSourceBinding, ...],
    tuple[os.stat_result | None, ...],
]:
    project_id, timeline_id, visual_id, audio_id = (uuid4() for _ in range(4))
    visual_path = tmp_path / "visual.png"
    visual_path.write_bytes(b"visual")
    _, visual_approved = approve_source(visual_path)
    audio_path = tmp_path / "audio.wav"
    audio_approved: os.stat_result | None = None
    if create_audio:
        audio_path.write_bytes(b"audio")
        _, audio_approved = approve_source(audio_path)
    visual_plan = LocalEditingVisualRenderPlan(
        project_id,
        timeline_id,
        1,
        720,
        1280,
        30,
        1000,
        (
            LocalEditingVisualRenderClip(
                1,
                visual_id,
                SegmentSelectionMaterialKind.IMAGE,
                0,
                1000,
                None,
                None,
                None,
                None,
            ),
        ),
    )
    audio_plan = LocalEditingAudioRenderPlan(
        project_id,
        timeline_id,
        1,
        1000,
        (
            LocalEditingAudioRenderClip(
                1,
                audio_kind,
                audio_id,
                0,
                1000,
                0,
                1000,
                0.0,
                audio_mode,
            ),
        ),
    )
    return (
        visual_plan,
        (
            VisualRenderSourceBinding(
                visual_id,
                SegmentSelectionMaterialKind.IMAGE,
                visual_path,
            ),
        ),
        (visual_approved,),
        audio_plan,
        (AudioRenderSourceBinding(audio_id, audio_path, has_audio),),
        (audio_approved,),
    )


def _probe_payload(
    plan: LocalEditingVisualRenderPlan,
    *,
    size: int = 100,
    audio: dict[str, object] | None = None,
) -> bytes:
    streams: list[dict[str, object]] = [
        {
            "codec_name": "h264",
            "codec_type": "video",
            "width": plan.output_width,
            "height": plan.output_height,
            "avg_frame_rate": f"{plan.output_fps}/1",
            "nb_read_frames": "30",
            "duration": "1.000000",
        }
    ]
    if audio is not None:
        streams.append(audio)
    return json.dumps(
        {
            "streams": streams,
            "format": {"duration": "1.000000", "size": str(size)},
        }
    ).encode()


def _valid_audio_stream() -> dict[str, object]:
    return {
        "codec_name": "aac",
        "codec_type": "audio",
        "sample_rate": "48000",
        "channels": 2,
        "duration": "1.000000",
    }


def test_audiovisual_execution_publishes_one_verified_h264_aac(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = _tools(tmp_path)
    task = tmp_path / "task"
    task.mkdir()
    request = _request(tmp_path)
    captured_ffmpeg: tuple[str, ...] | None = None

    def run_process(argv: tuple[str, ...], **kwargs: object) -> execution._ProcessResult:
        nonlocal captured_ffmpeg
        if argv[0] == os.fspath(tools.ffmpeg_path):
            captured_ffmpeg = argv
            Path(argv[-1]).write_bytes(b"x" * 100)
            return execution._ProcessResult(0, b"")
        return execution._ProcessResult(
            0,
            _probe_payload(request[0], audio=_valid_audio_stream()),
        )

    monkeypatch.setattr(execution, "_run_bounded_process", run_process)

    receipt = execute_audiovisual_render(tools, *request, task)

    assert captured_ffmpeg is not None
    assert "-c:a" in captured_ffmpeg
    assert receipt.audio_codec == "aac"
    assert receipt.audio_sample_rate == 48000
    assert receipt.audio_channels == 2
    assert receipt.duration_ms == 1000
    assert (task / VISUAL_RENDER_OUTPUT_FILENAME).read_bytes() == b"x" * 100


def test_excluded_audio_needs_no_stat_or_file_and_publishes_video_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = _tools(tmp_path)
    task = tmp_path / "task"
    task.mkdir()
    request = _request(
        tmp_path,
        audio_kind=LocalEditingAudioTrackKind.AMBIENT,
        audio_mode=LocalEditingOriginalAudioMode.AUTO_DUCK,
        has_audio=False,
        create_audio=False,
    )
    captured_ffmpeg: tuple[str, ...] | None = None

    def run_process(argv: tuple[str, ...], **kwargs: object) -> execution._ProcessResult:
        nonlocal captured_ffmpeg
        if argv[0] == os.fspath(tools.ffmpeg_path):
            captured_ffmpeg = argv
            Path(argv[-1]).write_bytes(b"x" * 100)
            return execution._ProcessResult(0, b"")
        return execution._ProcessResult(0, _probe_payload(request[0]))

    monkeypatch.setattr(execution, "_run_bounded_process", run_process)

    receipt = execute_audiovisual_render(tools, *request, task)

    assert captured_ffmpeg is not None
    assert os.fspath(request[4][0].source_path) not in captured_ffmpeg
    assert "-an" in captured_ffmpeg
    assert receipt.audio_codec is None
    assert receipt.audio_sample_rate is None
    assert receipt.audio_channels is None


def test_audio_source_change_after_process_prevents_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = _tools(tmp_path)
    task = tmp_path / "task"
    task.mkdir()
    request = _request(tmp_path)

    def run_process(argv: tuple[str, ...], **kwargs: object) -> execution._ProcessResult:
        assert argv[0] == os.fspath(tools.ffmpeg_path)
        Path(argv[-1]).write_bytes(b"x" * 100)
        request[4][0].source_path.write_bytes(b"changed")
        return execution._ProcessResult(0, b"")

    monkeypatch.setattr(execution, "_run_bounded_process", run_process)

    with pytest.raises(VisualRenderExecutionRejected) as error:
        execute_audiovisual_render(tools, *request, task)

    assert error.value.code is VisualRenderExecutionRejection.SOURCE_CHANGED
    assert list(task.iterdir()) == []


def test_receipt_rejects_unhashable_audio_shape_with_the_closed_error() -> None:
    with pytest.raises(VisualRenderExecutionRejected) as error:
        VisualRenderReceipt(
            frame_count=30,
            width=720,
            height=1280,
            fps=30,
            duration_ms=1000,
            bytes_written=100,
            sha256="a" * 64,
            audio_codec=cast(str, []),
            audio_sample_rate=48_000,
            audio_channels=2,
        )

    assert error.value.code is VisualRenderExecutionRejection.OUTPUT_INVALID


@pytest.mark.parametrize(
    "mutate",
    [
        lambda streams: streams.pop(),
        lambda streams: streams.append(_valid_audio_stream()),
        lambda streams: streams[1].update(codec_type="video"),
        lambda streams: streams[1].update(codec_name="mp3"),
        lambda streams: streams[1].update(sample_rate="44100"),
        lambda streams: streams[1].update(channels=1),
        lambda streams: streams[1].update(duration="0.900000"),
        lambda streams: streams[1].update(duration="0.000000"),
    ],
)
def test_audio_probe_shape_fails_closed(
    tmp_path: Path,
    mutate: object,
) -> None:
    plan = _request(tmp_path)[0]
    output = tmp_path / "output.mp4"
    output.write_bytes(b"x" * 100)
    document = json.loads(_probe_payload(plan, audio=_valid_audio_stream()))
    mutate(document["streams"])  # type: ignore[operator]

    with pytest.raises(VisualRenderExecutionRejected) as error:
        execution._verified_receipt(
            json.dumps(document).encode(),
            plan=plan,
            target_frames=30,
            output_stat=output.stat(),
            digest="a" * 64,
            expect_audio=True,
        )

    assert error.value.code is VisualRenderExecutionRejection.OUTPUT_INVALID


def test_audio_source_check_rejects_container_binding_and_approval_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path)
    plan, sources, approvals = request[3], request[4], request[5]

    with pytest.raises(VisualRenderExecutionRejected) as length_error:
        execution._checked_audio_sources(plan, sources, ())
    assert length_error.value.code is VisualRenderExecutionRejection.INVALID_REQUEST

    with pytest.raises(VisualRenderExecutionRejected) as approval_error:
        execution._checked_audio_sources(plan, sources, (None,))
    assert approval_error.value.code is VisualRenderExecutionRejection.INVALID_REQUEST

    silent_root = tmp_path / "silent"
    silent_root.mkdir()
    silent_required = _request(silent_root, has_audio=False)
    with pytest.raises(VisualRenderExecutionRejected) as binding_error:
        execution._checked_audio_sources(
            silent_required[3],
            silent_required[4],
            silent_required[5],
        )
    assert binding_error.value.code is VisualRenderExecutionRejection.INVALID_REQUEST

    def reject_rebuild(**kwargs: object) -> AudioRenderSourceBinding:
        raise AudioRenderBindingRejected(AudioRenderBindingRejection.INVALID_BINDINGS)

    monkeypatch.setattr(execution, "AudioRenderSourceBinding", reject_rebuild)
    with pytest.raises(VisualRenderExecutionRejected) as rebuilt_error:
        execution._checked_audio_sources(plan, sources, approvals)
    assert rebuilt_error.value.code is VisualRenderExecutionRejection.INVALID_REQUEST


def test_execution_maps_audiovisual_compiler_rejection_to_invalid_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = _tools(tmp_path)
    task = tmp_path / "task"
    task.mkdir()
    request = _request(tmp_path)

    def reject_compile(*args: object, **kwargs: object) -> None:
        raise AudiovisualRenderRejected(AudiovisualRenderRejection.AUDIO_REJECTED)

    monkeypatch.setattr(execution, "compile_audiovisual_ffmpeg_command", reject_compile)

    with pytest.raises(VisualRenderExecutionRejected) as error:
        execute_audiovisual_render(tools, *request, task)

    assert error.value.code is VisualRenderExecutionRejection.INVALID_REQUEST
    assert list(task.iterdir()) == []


def test_execution_preserves_audiovisual_tool_unavailable_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = _tools(tmp_path)
    task = tmp_path / "task"
    task.mkdir()
    request = _request(tmp_path)

    def reject_compile(*args: object, **kwargs: object) -> None:
        raise AudiovisualRenderRejected(AudiovisualRenderRejection.TOOL_UNAVAILABLE)

    monkeypatch.setattr(execution, "compile_audiovisual_ffmpeg_command", reject_compile)

    with pytest.raises(VisualRenderExecutionRejected) as error:
        execute_audiovisual_render(tools, *request, task)

    assert error.value.code is VisualRenderExecutionRejection.TOOL_UNAVAILABLE
    assert list(task.iterdir()) == []
