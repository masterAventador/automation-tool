"""Deterministic Local Executor frame-grid planning for visual rendering."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Never
from uuid import UUID

from automation_tool.protocol.local_editing import SegmentSelectionMaterialKind
from automation_tool.protocol.local_rendering import (
    LocalEditingVisualRenderClip,
    LocalEditingVisualRenderPlan,
    LocalEditingVisualTransitionKind,
)


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


__all__ = [
    "VisualFrameGridClip",
    "VisualFrameGridPlan",
    "VisualFrameGridRejected",
    "VisualFrameGridRejection",
    "quantize_visual_render_plan",
]
