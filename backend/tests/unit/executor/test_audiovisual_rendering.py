"""LE-11 T4: compose visual and locally-bound audio into one FFmpeg argv."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from automation_tool.executor import audiovisual_rendering as rendering
from automation_tool.executor.audio_rendering import AudioRenderSourceBinding
from automation_tool.executor.audiovisual_rendering import (
    AudiovisualRenderRejected,
    AudiovisualRenderRejection,
    compile_audiovisual_ffmpeg_command,
)
from automation_tool.executor.caption_overlay import (
    VisualCaptionOverlayBinding,
    VisualCaptionOverlaySet,
)
from automation_tool.executor.material_probe import PackagedMediaTools
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


def _visual_plan(
    project_id: UUID,
    timeline_id: UUID,
    material_id: UUID,
) -> LocalEditingVisualRenderPlan:
    return LocalEditingVisualRenderPlan(
        project_id=project_id,
        timeline_id=timeline_id,
        timeline_revision=3,
        output_width=720,
        output_height=1280,
        output_fps=30,
        duration_ms=1000,
        clips=(
            LocalEditingVisualRenderClip(
                sequence=1,
                material_id=material_id,
                kind=SegmentSelectionMaterialKind.IMAGE,
                start_ms=0,
                duration_ms=1000,
                source_in_ms=None,
                source_out_ms=None,
                transition_kind=None,
                transition_duration_ms=None,
            ),
        ),
    )


def _audio_plan(
    project_id: UUID,
    timeline_id: UUID,
    material_id: UUID,
    *,
    kind: LocalEditingAudioTrackKind = LocalEditingAudioTrackKind.NARRATION,
    mode: LocalEditingOriginalAudioMode | None = None,
) -> LocalEditingAudioRenderPlan:
    return LocalEditingAudioRenderPlan(
        project_id=project_id,
        timeline_id=timeline_id,
        timeline_revision=3,
        duration_ms=1000,
        clips=(
            LocalEditingAudioRenderClip(
                sequence=1,
                track_kind=kind,
                material_id=material_id,
                start_ms=0,
                duration_ms=1000,
                source_in_ms=0,
                source_out_ms=1000,
                gain_db=0.0,
                original_audio_mode=mode,
            ),
        ),
    )


def _visual_source(directory: Path, material_id: UUID) -> VisualRenderSourceBinding:
    path = directory / "画面.png"
    path.write_bytes(b"image")
    return VisualRenderSourceBinding(
        material_id=material_id,
        kind=SegmentSelectionMaterialKind.IMAGE,
        source_path=path,
    )


def test_composer_maps_one_video_and_one_normalized_aac_after_caption_inputs(
    tmp_path: Path,
) -> None:
    tools = _tools(tmp_path)
    project_id, timeline_id, visual_id, audio_id = (uuid4() for _ in range(4))
    visual_plan = _visual_plan(project_id, timeline_id, visual_id)
    visual_source = _visual_source(tmp_path, visual_id)
    audio_path = tmp_path / "旁白.wav"
    audio_path.write_bytes(b"audio")
    caption_path = tmp_path / "字幕.png"
    caption_path.write_bytes(b"caption")
    overlays = VisualCaptionOverlaySet(
        project_id=project_id,
        timeline_id=timeline_id,
        timeline_revision=3,
        output_width=720,
        output_height=1280,
        output_fps=30,
        duration_ms=1000,
        target_frames=30,
        captions=(
            VisualCaptionOverlayBinding(
                sequence=1,
                start_frame=0,
                end_frame=30,
                source_path=caption_path,
            ),
        ),
    )

    command = compile_audiovisual_ffmpeg_command(
        tools,
        visual_plan,
        (visual_source,),
        _audio_plan(project_id, timeline_id, audio_id),
        (AudioRenderSourceBinding(audio_id, audio_path, True),),
        tmp_path / "render.mp4",
        caption_overlays=overlays,
    )

    assert command.has_audio is True
    assert command.audio_input_material_ids == (audio_id,)
    assert command.argv.count("-i") == 3
    assert command.argv.index(os.fspath(visual_source.source_path)) < command.argv.index(
        os.fspath(caption_path)
    ) < command.argv.index(os.fspath(audio_path))
    assert "[2:a]atrim=" in command.filter_complex
    assert command.argv.count("-map") == 2
    assert "[audio_out]" in command.argv
    assert "-an" not in command.argv
    assert command.argv[command.argv.index("-c:a") + 1] == "aac"
    assert command.argv[command.argv.index("-ar") + 1] == "48000"
    assert command.argv[command.argv.index("-ac") + 1] == "2"
    assert repr(command) == "AudiovisualFfmpegCommand(<redacted>)"
    assert os.fspath(audio_path) not in repr(command)


def test_all_locally_excluded_audio_keeps_the_explicit_video_only_command(
    tmp_path: Path,
) -> None:
    tools = _tools(tmp_path)
    project_id, timeline_id, visual_id, silent_id = (uuid4() for _ in range(4))
    visual_plan = _visual_plan(project_id, timeline_id, visual_id)
    visual_source = _visual_source(tmp_path, visual_id)
    missing = tmp_path / "not-opened.mp4"

    command = compile_audiovisual_ffmpeg_command(
        tools,
        visual_plan,
        (visual_source,),
        _audio_plan(
            project_id,
            timeline_id,
            silent_id,
            kind=LocalEditingAudioTrackKind.AMBIENT,
            mode=LocalEditingOriginalAudioMode.AUTO_DUCK,
        ),
        (AudioRenderSourceBinding(silent_id, missing, False),),
        tmp_path / "render.mp4",
    )

    assert command.has_audio is False
    assert command.audio_input_material_ids == ()
    assert "-an" in command.argv
    assert os.fspath(missing) not in command.argv
    assert command.argv.count("-map") == 1


def test_composer_rejects_visual_audio_identity_drift(tmp_path: Path) -> None:
    tools = _tools(tmp_path)
    project_id, timeline_id, visual_id, audio_id = (uuid4() for _ in range(4))
    visual_plan = _visual_plan(project_id, timeline_id, visual_id)
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"audio")

    with pytest.raises(AudiovisualRenderRejected) as error:
        compile_audiovisual_ffmpeg_command(
            tools,
            visual_plan,
            (_visual_source(tmp_path, visual_id),),
            _audio_plan(uuid4(), timeline_id, audio_id),
            (AudioRenderSourceBinding(audio_id, audio_path, True),),
            tmp_path / "render.mp4",
        )

    assert error.value.code is AudiovisualRenderRejection.IDENTITY_MISMATCH
    assert str(error.value) == "audiovisual render rejected"
    assert error.value.__cause__ is None


def test_composer_preserves_packaged_tool_unavailable_code(tmp_path: Path) -> None:
    tools = _tools(tmp_path)
    project_id, timeline_id, visual_id, audio_id = (uuid4() for _ in range(4))
    visual_source = _visual_source(tmp_path, visual_id)
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"audio")
    tools.ffmpeg_path.unlink()

    with pytest.raises(AudiovisualRenderRejected) as error:
        compile_audiovisual_ffmpeg_command(
            tools,
            _visual_plan(project_id, timeline_id, visual_id),
            (visual_source,),
            _audio_plan(project_id, timeline_id, audio_id),
            (AudioRenderSourceBinding(audio_id, audio_path, True),),
            tmp_path / "render.mp4",
        )

    assert error.value.code is AudiovisualRenderRejection.TOOL_UNAVAILABLE


def test_composer_rejects_invalid_plan_visual_command_and_audio_binding(
    tmp_path: Path,
) -> None:
    tools = _tools(tmp_path)
    project_id, timeline_id, visual_id, audio_id = (uuid4() for _ in range(4))
    visual_plan = _visual_plan(project_id, timeline_id, visual_id)
    audio_plan = _audio_plan(project_id, timeline_id, audio_id)
    visual_source = _visual_source(tmp_path, visual_id)
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"audio")

    with pytest.raises(AudiovisualRenderRejected) as invalid:
        compile_audiovisual_ffmpeg_command(  # type: ignore[arg-type]
            tools,
            object(),
            (visual_source,),
            audio_plan,
            (AudioRenderSourceBinding(audio_id, audio_path, True),),
            tmp_path / "render.mp4",
        )
    assert invalid.value.code is AudiovisualRenderRejection.INVALID_REQUEST

    with pytest.raises(AudiovisualRenderRejected) as visual_error:
        compile_audiovisual_ffmpeg_command(
            tools,
            visual_plan,
            (),
            audio_plan,
            (AudioRenderSourceBinding(audio_id, audio_path, True),),
            tmp_path / "render.mp4",
        )
    assert visual_error.value.code is AudiovisualRenderRejection.VISUAL_REJECTED

    with pytest.raises(AudiovisualRenderRejected) as audio_error:
        compile_audiovisual_ffmpeg_command(
            tools,
            visual_plan,
            (visual_source,),
            audio_plan,
            (),
            tmp_path / "render.mp4",
        )
    assert audio_error.value.code is AudiovisualRenderRejection.AUDIO_REJECTED


@pytest.mark.parametrize(
    "visual_argv",
    [
        (),
        ("-an", "-map_metadata", "-movflags", "-filter_complex"),
    ],
)
def test_composer_rejects_visual_argv_shape_drift(
    visual_argv: tuple[str, ...],
) -> None:
    with pytest.raises(AudiovisualRenderRejected) as error:
        rendering._compose_argv(
            visual_argv,
            audio_input_argv=("-i", "/redacted/audio.wav"),
            audio_filter_graph="anull[audio_out]",
            audio_output_label="audio_out",
        )

    assert error.value.code is AudiovisualRenderRejection.VISUAL_REJECTED
