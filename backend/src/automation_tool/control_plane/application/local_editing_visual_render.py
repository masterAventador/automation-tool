"""Project Control Plane visual domain values onto the path-free render wire."""

from __future__ import annotations

from typing import Never

from automation_tool.control_plane.domain.editing_project import (
    EditingProject,
    EditingProjectId,
    OutputSpec,
)
from automation_tool.control_plane.domain.material import MaterialId
from automation_tool.control_plane.domain.timeline import (
    Timeline,
    TimelineClip,
    TimelineId,
    TimelineTrack,
    TimelineTrackKind,
    TimelineTransition,
    TransitionKind,
)
from automation_tool.protocol.local_editing import SegmentSelectionMaterialKind
from automation_tool.protocol.local_rendering import (
    LocalEditingVisualRenderClip,
    LocalEditingVisualRenderPlan,
    LocalEditingVisualTransitionKind,
)


class LocalEditingVisualPlanRejected(ValueError):
    """The domain values cannot form one visual render plan."""

    def __init__(self) -> None:
        super().__init__("local visual render projection rejected")


def _reject() -> Never:
    raise LocalEditingVisualPlanRejected from None


_TRANSITION_KINDS = {
    TransitionKind.FADE: LocalEditingVisualTransitionKind.FADE,
    TransitionKind.DISSOLVE: LocalEditingVisualTransitionKind.DISSOLVE,
    TransitionKind.WIPE: LocalEditingVisualTransitionKind.WIPE,
}


def _visual_clip(
    clip: TimelineClip,
    *,
    sequence: int,
) -> LocalEditingVisualRenderClip:
    if not isinstance(clip, TimelineClip) or not isinstance(clip.source_material_id, MaterialId):
        _reject()
    transition = clip.transition_in
    if transition is not None and not isinstance(transition, TimelineTransition):
        _reject()
    return LocalEditingVisualRenderClip(
        sequence=sequence,
        material_id=clip.source_material_id.uuid,
        kind=(
            SegmentSelectionMaterialKind.IMAGE
            if clip.source_in_ms is None
            else SegmentSelectionMaterialKind.VIDEO
        ),
        start_ms=clip.start_ms,
        duration_ms=clip.duration_ms,
        source_in_ms=clip.source_in_ms,
        source_out_ms=clip.source_out_ms,
        transition_kind=None if transition is None else _TRANSITION_KINDS[transition.kind],
        transition_duration_ms=None if transition is None else transition.duration_ms,
    )


def create_local_editing_visual_render_plan(
    project: EditingProject,
    timeline: Timeline,
) -> LocalEditingVisualRenderPlan:
    """Create the visual-only wire value without carrying captions, audio or paths."""

    if (
        not isinstance(project, EditingProject)
        or not isinstance(timeline, Timeline)
        or not isinstance(project.project_id, EditingProjectId)
        or not isinstance(timeline.project_id, EditingProjectId)
        or not isinstance(timeline.timeline_id, TimelineId)
        or project.project_id != timeline.project_id
        or not isinstance(project.output, OutputSpec)
        or not isinstance(timeline.tracks, tuple)
        or not all(isinstance(track, TimelineTrack) for track in timeline.tracks)
    ):
        _reject()
    try:
        output = OutputSpec(
            width=project.output.width,
            height=project.output.height,
            fps=project.output.fps,
        )
        visual_tracks = tuple(
            track
            for track in timeline.tracks
            if isinstance(track, TimelineTrack) and track.kind is TimelineTrackKind.VISUAL
        )
        if len(visual_tracks) != 1 or not isinstance(visual_tracks[0].clips, tuple):
            _reject()
        clips = tuple(
            _visual_clip(clip, sequence=index)
            for index, clip in enumerate(visual_tracks[0].clips, start=1)
        )
        return LocalEditingVisualRenderPlan(
            project_id=project.project_id.uuid,
            timeline_id=timeline.timeline_id.uuid,
            timeline_revision=timeline.revision,
            output_width=output.width,
            output_height=output.height,
            output_fps=output.fps,
            duration_ms=timeline.duration_ms,
            clips=clips,
        )
    except Exception:
        _reject()


__all__ = [
    "LocalEditingVisualPlanRejected",
    "create_local_editing_visual_render_plan",
]
