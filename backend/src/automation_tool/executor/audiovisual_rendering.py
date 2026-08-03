"""Compose the visual render command with one locally-bound audio graph."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Never
from uuid import UUID

from automation_tool.executor.audio_rendering import (
    AudioRenderBindingRejected,
    AudioRenderSourceBinding,
    bind_audio_render_inputs,
)
from automation_tool.executor.caption_overlay import VisualCaptionOverlaySet
from automation_tool.executor.material_probe import PackagedMediaTools
from automation_tool.executor.visual_rendering import (
    VisualFilterGraphRejected,
    VisualFilterGraphRejection,
    VisualRenderSourceBinding,
    compile_visual_ffmpeg_command,
)
from automation_tool.protocol.local_rendering import (
    LocalEditingAudioRenderPlan,
    LocalEditingVisualRenderPlan,
)


class AudiovisualRenderRejection(StrEnum):
    INVALID_REQUEST = "invalid_request"
    IDENTITY_MISMATCH = "identity_mismatch"
    VISUAL_REJECTED = "visual_rejected"
    AUDIO_REJECTED = "audio_rejected"
    TOOL_UNAVAILABLE = "tool_unavailable"


class AudiovisualRenderRejected(ValueError):
    """The two local render plans cannot form one deterministic command."""

    def __init__(self, code: AudiovisualRenderRejection) -> None:
        self.code = code
        super().__init__("audiovisual render rejected")


def _reject(code: AudiovisualRenderRejection) -> Never:
    raise AudiovisualRenderRejected(code) from None


@dataclass(frozen=True, slots=True, repr=False)
class AudiovisualFfmpegCommand:
    argv: tuple[str, ...] = field(repr=False)
    filter_complex: str
    target_frames: int
    output_width: int
    output_height: int
    output_fps: int
    has_audio: bool
    audio_input_material_ids: tuple[UUID, ...]

    def __repr__(self) -> str:
        return "AudiovisualFfmpegCommand(<redacted>)"


def _matching_identity(
    visual: LocalEditingVisualRenderPlan,
    audio: LocalEditingAudioRenderPlan,
) -> bool:
    return (
        visual.project_id == audio.project_id
        and visual.timeline_id == audio.timeline_id
        and visual.timeline_revision == audio.timeline_revision
        and visual.duration_ms == audio.duration_ms
    )


def _compose_argv(
    visual_argv: tuple[str, ...],
    *,
    audio_input_argv: tuple[str, ...],
    audio_filter_graph: str,
    audio_output_label: str,
) -> tuple[str, ...]:
    argv = list(visual_argv)
    if (
        argv.count("-filter_complex") != 1
        or argv.count("-an") != 1
        or argv.count("-map_metadata") != 1
        or argv.count("-movflags") != 1
    ):
        _reject(AudiovisualRenderRejection.VISUAL_REJECTED)
    filter_index = argv.index("-filter_complex")
    if filter_index + 1 >= len(argv):
        _reject(AudiovisualRenderRejection.VISUAL_REJECTED)
    argv[filter_index:filter_index] = audio_input_argv
    filter_index += len(audio_input_argv)
    argv[filter_index + 1] = f"{argv[filter_index + 1]};{audio_filter_graph}"
    argv.remove("-an")
    metadata_index = argv.index("-map_metadata")
    argv[metadata_index:metadata_index] = ("-map", f"[{audio_output_label}]")
    movflags_index = argv.index("-movflags")
    argv[movflags_index:movflags_index] = (
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        "-ac",
        "2",
    )
    return tuple(argv)


def compile_audiovisual_ffmpeg_command(
    tools: PackagedMediaTools,
    visual_plan: LocalEditingVisualRenderPlan,
    visual_sources: tuple[VisualRenderSourceBinding, ...],
    audio_plan: LocalEditingAudioRenderPlan,
    audio_sources: tuple[AudioRenderSourceBinding, ...],
    output_path: Path,
    *,
    caption_overlays: VisualCaptionOverlaySet | None = None,
) -> AudiovisualFfmpegCommand:
    """Compile one H.264 command with zero or one normalized AAC output."""

    if not isinstance(visual_plan, LocalEditingVisualRenderPlan) or not isinstance(
        audio_plan, LocalEditingAudioRenderPlan
    ):
        _reject(AudiovisualRenderRejection.INVALID_REQUEST)
    if not _matching_identity(visual_plan, audio_plan):
        _reject(AudiovisualRenderRejection.IDENTITY_MISMATCH)
    try:
        visual = compile_visual_ffmpeg_command(
            tools,
            visual_plan,
            visual_sources,
            output_path,
            caption_overlays=caption_overlays,
        )
    except VisualFilterGraphRejected as rejected:
        if rejected.code is VisualFilterGraphRejection.TOOL_UNAVAILABLE:
            _reject(AudiovisualRenderRejection.TOOL_UNAVAILABLE)
        _reject(AudiovisualRenderRejection.VISUAL_REJECTED)
    first_audio_input = sum(argument == "-i" for argument in visual.argv)
    try:
        audio = bind_audio_render_inputs(
            audio_plan,
            audio_sources,
            first_input_index=first_audio_input,
        )
    except AudioRenderBindingRejected:
        _reject(AudiovisualRenderRejection.AUDIO_REJECTED)
    if audio.output_label is None:
        argv = visual.argv
        filter_complex = visual.filter_complex
    else:
        argv = _compose_argv(
            visual.argv,
            audio_input_argv=audio.input_argv,
            audio_filter_graph=audio.filter_graph,
            audio_output_label=audio.output_label,
        )
        filter_complex = f"{visual.filter_complex};{audio.filter_graph}"
    return AudiovisualFfmpegCommand(
        argv=argv,
        filter_complex=filter_complex,
        target_frames=visual.target_frames,
        output_width=visual.output_width,
        output_height=visual.output_height,
        output_fps=visual.output_fps,
        has_audio=audio.output_label is not None,
        audio_input_material_ids=audio.input_material_ids,
    )


__all__ = [
    "AudiovisualFfmpegCommand",
    "AudiovisualRenderRejected",
    "AudiovisualRenderRejection",
    "compile_audiovisual_ffmpeg_command",
]
