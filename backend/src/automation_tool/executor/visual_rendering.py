"""Deterministic Local Executor frame-grid planning for visual rendering."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Never, cast
from uuid import RFC_4122, UUID

from automation_tool.executor.material_probe import (
    MAX_PATH_CHARACTERS,
    MaterialProbeRejected,
    PackagedMediaTools,
)
from automation_tool.protocol.local_editing import SegmentSelectionMaterialKind
from automation_tool.protocol.local_rendering import (
    LocalEditingVisualRenderClip,
    LocalEditingVisualRenderPlan,
    LocalEditingVisualTransitionKind,
)
from automation_tool.protocol.safe_text import contains_control_or_bidi


class VisualFrameGridRejection(StrEnum):
    INVALID_PLAN = "invalid_plan"
    CLIP_BELOW_ONE_FRAME = "clip_below_one_frame"
    TRANSITION_BELOW_ONE_FRAME = "transition_below_one_frame"
    TRANSITION_SWALLOWS_CLIP = "transition_swallows_clip"


class VisualFrameGridRejected(ValueError):
    """One path-free render plan cannot be represented at its requested fps."""

    def __init__(self, code: VisualFrameGridRejection) -> None:
        self.code = code
        super().__init__("visual frame grid rejected")


def _reject(code: VisualFrameGridRejection) -> Never:
    raise VisualFrameGridRejected(code) from None


class VisualFilterGraphRejection(StrEnum):
    INVALID_PLAN = "invalid_plan"
    INVALID_BINDINGS = "invalid_bindings"
    INVALID_OUTPUT = "invalid_output"
    TRANSITIONS_NOT_SUPPORTED = "transitions_not_supported"
    TOOL_UNAVAILABLE = "tool_unavailable"


class VisualFilterGraphRejected(ValueError):
    """A hard-cut FFmpeg command cannot be compiled from local inputs."""

    def __init__(self, code: VisualFilterGraphRejection) -> None:
        self.code = code
        super().__init__("visual filter graph rejected")


def _reject_filter_graph(code: VisualFilterGraphRejection) -> Never:
    raise VisualFilterGraphRejected(code) from None


def _is_uuid4(value: object) -> bool:
    return (
        isinstance(value, UUID)
        and value.variant == RFC_4122
        and value.version == 4
        and value.int != 0
    )


def _valid_local_path(value: object, *, suffix: str | None = None) -> bool:
    if not isinstance(value, Path) or not value.is_absolute():
        return False
    text = os.fspath(value)
    return (
        1 <= len(text) <= MAX_PATH_CHARACTERS
        and not contains_control_or_bidi(text)
        and (suffix is None or value.suffix == suffix)
    )


@dataclass(frozen=True, slots=True, repr=False)
class VisualRenderSourceBinding:
    """One local-only material path; never sent to the Control Plane."""

    material_id: UUID
    kind: SegmentSelectionMaterialKind
    source_path: Path = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not _is_uuid4(self.material_id)
            or not isinstance(self.kind, SegmentSelectionMaterialKind)
            or self.kind
            not in {SegmentSelectionMaterialKind.VIDEO, SegmentSelectionMaterialKind.IMAGE}
            or not _valid_local_path(self.source_path)
        ):
            _reject_filter_graph(VisualFilterGraphRejection.INVALID_BINDINGS)

    def __repr__(self) -> str:
        return "VisualRenderSourceBinding(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class VisualFfmpegCommand:
    """A local argv and its path-free filter graph, redacted as a whole."""

    argv: tuple[str, ...] = field(repr=False)
    filter_complex: str
    target_frames: int
    output_width: int
    output_height: int
    output_fps: int

    def __repr__(self) -> str:
        return "VisualFfmpegCommand(<redacted>)"


@dataclass(frozen=True, slots=True)
class VisualFrameGridClip:
    sequence: int
    material_id: UUID
    kind: SegmentSelectionMaterialKind
    start_frame: int
    frame_count: int
    source_in_ms: int | None
    source_out_ms: int | None
    transition_kind: LocalEditingVisualTransitionKind | None
    transition_frames: int

    @property
    def end_frame(self) -> int:
        return self.start_frame + self.frame_count


@dataclass(frozen=True, slots=True)
class VisualFrameGridPlan:
    project_id: UUID
    timeline_id: UUID
    timeline_revision: int
    output_width: int
    output_height: int
    output_fps: int
    requested_duration_ms: int
    target_frames: int
    clips: tuple[VisualFrameGridClip, ...]


def _validated_plan(plan: LocalEditingVisualRenderPlan) -> LocalEditingVisualRenderPlan:
    if not isinstance(plan, LocalEditingVisualRenderPlan) or not isinstance(plan.clips, tuple):
        _reject(VisualFrameGridRejection.INVALID_PLAN)
    try:
        return LocalEditingVisualRenderPlan(
            project_id=plan.project_id,
            timeline_id=plan.timeline_id,
            timeline_revision=plan.timeline_revision,
            output_width=plan.output_width,
            output_height=plan.output_height,
            output_fps=plan.output_fps,
            duration_ms=plan.duration_ms,
            clips=tuple(
                LocalEditingVisualRenderClip(
                    sequence=clip.sequence,
                    material_id=clip.material_id,
                    kind=clip.kind,
                    start_ms=clip.start_ms,
                    duration_ms=clip.duration_ms,
                    source_in_ms=clip.source_in_ms,
                    source_out_ms=clip.source_out_ms,
                    transition_kind=clip.transition_kind,
                    transition_duration_ms=clip.transition_duration_ms,
                )
                for clip in plan.clips
            ),
        )
    except Exception:
        _reject(VisualFrameGridRejection.INVALID_PLAN)


def _frame_at(milliseconds: int, fps: int) -> int:
    return (milliseconds * fps + 500) // 1000


def quantize_visual_render_plan(
    plan: LocalEditingVisualRenderPlan,
) -> VisualFrameGridPlan:
    """Map absolute millisecond boundaries to one shared integer frame grid."""

    validated = _validated_plan(plan)
    fps = validated.output_fps
    clips: list[VisualFrameGridClip] = []
    previous_end_frame = 0
    previous_tail_frames = 0
    for clip in validated.clips:
        start_frame = _frame_at(clip.start_ms, fps)
        end_frame = _frame_at(clip.end_ms, fps)
        frame_count = end_frame - start_frame
        if frame_count < 1:
            _reject(VisualFrameGridRejection.CLIP_BELOW_ONE_FRAME)

        transition_frames = 0
        if clip.transition_kind is not None:
            transition_frames = previous_end_frame - start_frame
            if transition_frames < 1:
                _reject(VisualFrameGridRejection.TRANSITION_BELOW_ONE_FRAME)
            if transition_frames >= previous_tail_frames or transition_frames >= frame_count:
                _reject(VisualFrameGridRejection.TRANSITION_SWALLOWS_CLIP)

        clips.append(
            VisualFrameGridClip(
                sequence=clip.sequence,
                material_id=clip.material_id,
                kind=clip.kind,
                start_frame=start_frame,
                frame_count=frame_count,
                source_in_ms=clip.source_in_ms,
                source_out_ms=clip.source_out_ms,
                transition_kind=clip.transition_kind,
                transition_frames=transition_frames,
            )
        )
        previous_tail_frames = frame_count - transition_frames
        previous_end_frame = end_frame

    target_frames = _frame_at(validated.duration_ms, fps)
    return VisualFrameGridPlan(
        project_id=validated.project_id,
        timeline_id=validated.timeline_id,
        timeline_revision=validated.timeline_revision,
        output_width=validated.output_width,
        output_height=validated.output_height,
        output_fps=fps,
        requested_duration_ms=validated.duration_ms,
        target_frames=target_frames,
        clips=tuple(clips),
    )


def _validated_sources(
    sources: tuple[VisualRenderSourceBinding, ...],
) -> tuple[VisualRenderSourceBinding, ...]:
    if (
        not isinstance(sources, tuple)
        or not sources
        or not all(isinstance(source, VisualRenderSourceBinding) for source in sources)
    ):
        _reject_filter_graph(VisualFilterGraphRejection.INVALID_BINDINGS)
    try:
        rebuilt = tuple(
            VisualRenderSourceBinding(
                material_id=source.material_id,
                kind=source.kind,
                source_path=source.source_path,
            )
            for source in sources
        )
    except Exception:
        _reject_filter_graph(VisualFilterGraphRejection.INVALID_BINDINGS)
    if len({source.material_id for source in rebuilt}) != len(rebuilt):
        _reject_filter_graph(VisualFilterGraphRejection.INVALID_BINDINGS)
    return rebuilt


def _seconds(milliseconds: int) -> str:
    return f"{milliseconds // 1000}.{milliseconds % 1000:03d}"


def _clip_filter(
    clip: VisualFrameGridClip,
    *,
    input_index: int,
    width: int,
    height: int,
    fps: int,
) -> str:
    filters: list[str] = []
    if clip.kind is SegmentSelectionMaterialKind.VIDEO:
        source_in_ms = cast(int, clip.source_in_ms)
        source_out_ms = cast(int, clip.source_out_ms)
        filters.append(f"trim=start={_seconds(source_in_ms)}:end={_seconds(source_out_ms)}")
    filters.extend(
        (
            "setpts=PTS-STARTPTS",
            f"scale=w={width}:h={height}:force_original_aspect_ratio=increase",
            f"crop=w={width}:h={height}:x=(in_w-out_w)/2:y=(in_h-out_h)/2",
            f"fps={fps}",
            f"settb=1/{fps}",
            f"trim=end_frame={clip.frame_count}",
            "setpts=N",
            "setsar=1",
            "format=yuv420p",
        )
    )
    return f"[{input_index}:v:0]{','.join(filters)}[v{clip.sequence}]"


def compile_visual_ffmpeg_command(
    tools: PackagedMediaTools,
    plan: LocalEditingVisualRenderPlan,
    sources: tuple[VisualRenderSourceBinding, ...],
    output_path: Path,
) -> VisualFfmpegCommand:
    """Compile one hard-cut visual plan without invoking a shell or FFmpeg."""

    if not isinstance(tools, PackagedMediaTools):
        _reject_filter_graph(VisualFilterGraphRejection.TOOL_UNAVAILABLE)
    try:
        tools.revalidate()
    except MaterialProbeRejected:
        _reject_filter_graph(VisualFilterGraphRejection.TOOL_UNAVAILABLE)
    if not _valid_local_path(output_path, suffix=".mp4"):
        _reject_filter_graph(VisualFilterGraphRejection.INVALID_OUTPUT)
    try:
        frame_plan = quantize_visual_render_plan(plan)
    except VisualFrameGridRejected:
        _reject_filter_graph(VisualFilterGraphRejection.INVALID_PLAN)
    if any(clip.transition_kind is not None for clip in frame_plan.clips):
        _reject_filter_graph(VisualFilterGraphRejection.TRANSITIONS_NOT_SUPPORTED)

    validated_sources = _validated_sources(sources)
    source_by_id = {source.material_id: source for source in validated_sources}
    expected_ids = {clip.material_id for clip in frame_plan.clips}
    if set(source_by_id) != expected_ids or any(
        source_by_id[clip.material_id].kind is not clip.kind for clip in frame_plan.clips
    ):
        _reject_filter_graph(VisualFilterGraphRejection.INVALID_BINDINGS)

    input_argv: list[str] = []
    filter_parts: list[str] = []
    for input_index, clip in enumerate(frame_plan.clips):
        source = source_by_id[clip.material_id]
        if clip.kind is SegmentSelectionMaterialKind.IMAGE:
            input_argv.extend(("-loop", "1", "-framerate", str(frame_plan.output_fps)))
        input_argv.extend(("-i", os.fspath(source.source_path)))
        filter_parts.append(
            _clip_filter(
                clip,
                input_index=input_index,
                width=frame_plan.output_width,
                height=frame_plan.output_height,
                fps=frame_plan.output_fps,
            )
        )

    output_label = f"v{frame_plan.clips[0].sequence}"
    if len(frame_plan.clips) > 1:
        labels = "".join(f"[v{clip.sequence}]" for clip in frame_plan.clips)
        filter_parts.append(f"{labels}concat=n={len(frame_plan.clips)}:v=1:a=0[outv]")
        output_label = "outv"
    filter_complex = ";".join(filter_parts)
    argv = (
        os.fspath(tools.ffmpeg_path),
        "-y",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        *input_argv,
        "-filter_complex",
        filter_complex,
        "-map",
        f"[{output_label}]",
        "-map_metadata",
        "-1",
        "-map_chapters",
        "-1",
        "-an",
        "-frames:v",
        str(frame_plan.target_frames),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-fps_mode",
        "cfr",
        "-r",
        str(frame_plan.output_fps),
        "-movflags",
        "+faststart",
        os.fspath(output_path),
    )
    return VisualFfmpegCommand(
        argv=argv,
        filter_complex=filter_complex,
        target_frames=frame_plan.target_frames,
        output_width=frame_plan.output_width,
        output_height=frame_plan.output_height,
        output_fps=frame_plan.output_fps,
    )


__all__ = [
    "VisualFfmpegCommand",
    "VisualFilterGraphRejected",
    "VisualFilterGraphRejection",
    "VisualFrameGridClip",
    "VisualFrameGridPlan",
    "VisualFrameGridRejected",
    "VisualFrameGridRejection",
    "VisualRenderSourceBinding",
    "compile_visual_ffmpeg_command",
    "quantize_visual_render_plan",
]
