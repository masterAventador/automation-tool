"""Project Control Plane visual domain values onto the path-free render wire."""

from __future__ import annotations

from typing import Never, cast

from automation_tool.control_plane.domain.editing_project import (
    CaptionStyle,
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
    LocalEditingCaptionRenderCue,
    LocalEditingCaptionRenderPlan,
    LocalEditingCaptionRenderStyle,
    LocalEditingVisualRenderClip,
    LocalEditingVisualRenderPlan,
    LocalEditingVisualTransitionKind,
)


class LocalEditingVisualPlanRejected(ValueError):
    """The domain values cannot form one visual render plan."""

    def __init__(self) -> None:
        super().__init__("local visual render projection rejected")


class LocalEditingCaptionPlanRejected(ValueError):
    """The domain values cannot form one caption render plan."""

    def __init__(self) -> None:
        super().__init__("local caption render projection rejected")


def _reject() -> Never:
    raise LocalEditingVisualPlanRejected from None


_TRANSITION_KINDS = {
    TransitionKind.FADE: LocalEditingVisualTransitionKind.FADE,
    TransitionKind.DISSOLVE: LocalEditingVisualTransitionKind.DISSOLVE,
    TransitionKind.WIPE: LocalEditingVisualTransitionKind.WIPE,
}


def _rebuilt_project(project: EditingProject) -> EditingProject:
    return EditingProject(
        project_id=project.project_id,
        title=project.title,
        output=OutputSpec(
            width=project.output.width,
            height=project.output.height,
            fps=project.output.fps,
        ),
        caption_style=CaptionStyle(
            font_key=project.caption_style.font_key,
            font_px=project.caption_style.font_px,
            stroke_px=project.caption_style.stroke_px,
            line_spacing=project.caption_style.line_spacing,
        ),
        created_at=project.created_at,
    )


def _rebuilt_timeline_clip(clip: TimelineClip) -> TimelineClip:
    transition = clip.transition_in
    rebuilt_transition = (
        None
        if transition is None
        else TimelineTransition(
            kind=transition.kind,
            duration_ms=transition.duration_ms,
        )
    )
    return TimelineClip(
        clip_id=clip.clip_id,
        start_ms=clip.start_ms,
        duration_ms=clip.duration_ms,
        source_material_id=clip.source_material_id,
        source_in_ms=clip.source_in_ms,
        source_out_ms=clip.source_out_ms,
        text=clip.text,
        gain_db=clip.gain_db,
        transition_in=rebuilt_transition,
        original_audio_mode=clip.original_audio_mode,
    )


def _rebuilt_timeline(timeline: Timeline) -> Timeline:
    return Timeline(
        timeline_id=timeline.timeline_id,
        project_id=timeline.project_id,
        revision=timeline.revision,
        duration_ms=timeline.duration_ms,
        tracks=tuple(
            TimelineTrack(
                track_id=track.track_id,
                kind=track.kind,
                clips=tuple(_rebuilt_timeline_clip(clip) for clip in track.clips),
            )
            for track in timeline.tracks
        ),
        created_at=timeline.created_at,
    )


def _visual_clip(
    clip: TimelineClip,
    *,
    sequence: int,
) -> LocalEditingVisualRenderClip:
    material_id = cast(MaterialId, clip.source_material_id)
    transition = clip.transition_in
    return LocalEditingVisualRenderClip(
        sequence=sequence,
        material_id=material_id.uuid,
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
        or not isinstance(project.output, OutputSpec)
        or not isinstance(project.caption_style, CaptionStyle)
        or not isinstance(timeline.tracks, tuple)
        or not all(
            isinstance(track, TimelineTrack)
            and isinstance(track.kind, TimelineTrackKind)
            and isinstance(track.clips, tuple)
            and all(isinstance(clip, TimelineClip) for clip in track.clips)
            for track in timeline.tracks
        )
    ):
        _reject()
    try:
        validated_project = _rebuilt_project(project)
        validated_timeline = _rebuilt_timeline(timeline)
        if validated_project.project_id != validated_timeline.project_id:
            _reject()
        output = validated_project.output
        visual_track = cast(
            TimelineTrack,
            validated_timeline.track_of(TimelineTrackKind.VISUAL),
        )
        clips = tuple(
            _visual_clip(clip, sequence=index)
            for index, clip in enumerate(visual_track.clips, start=1)
        )
        return LocalEditingVisualRenderPlan(
            project_id=validated_project.project_id.uuid,
            timeline_id=validated_timeline.timeline_id.uuid,
            timeline_revision=validated_timeline.revision,
            output_width=output.width,
            output_height=output.height,
            output_fps=output.fps,
            duration_ms=validated_timeline.duration_ms,
            clips=clips,
        )
    except Exception:
        _reject()


def create_local_editing_caption_render_plan(
    project: EditingProject,
    timeline: Timeline,
) -> LocalEditingCaptionRenderPlan:
    """Create the caption-only wire value without carrying local paths or audio."""

    if (
        not isinstance(project, EditingProject)
        or not isinstance(timeline, Timeline)
        or not isinstance(project.project_id, EditingProjectId)
        or not isinstance(timeline.project_id, EditingProjectId)
        or not isinstance(timeline.timeline_id, TimelineId)
        or not isinstance(project.output, OutputSpec)
        or not isinstance(project.caption_style, CaptionStyle)
        or not isinstance(timeline.tracks, tuple)
        or not all(
            isinstance(track, TimelineTrack)
            and isinstance(track.kind, TimelineTrackKind)
            and isinstance(track.clips, tuple)
            and all(isinstance(clip, TimelineClip) for clip in track.clips)
            for track in timeline.tracks
        )
    ):
        raise LocalEditingCaptionPlanRejected from None
    try:
        validated_project = _rebuilt_project(project)
        validated_timeline = _rebuilt_timeline(timeline)
        if validated_project.project_id != validated_timeline.project_id:
            raise LocalEditingCaptionPlanRejected
        output = validated_project.output
        style = validated_project.caption_style
        caption_track = validated_timeline.track_of(TimelineTrackKind.CAPTION)
        cues = (
            ()
            if caption_track is None
            else tuple(
                LocalEditingCaptionRenderCue(
                    sequence=index,
                    start_ms=clip.start_ms,
                    duration_ms=clip.duration_ms,
                    text=cast(str, clip.text),
                )
                for index, clip in enumerate(caption_track.clips, start=1)
            )
        )
        return LocalEditingCaptionRenderPlan(
            project_id=validated_project.project_id.uuid,
            timeline_id=validated_timeline.timeline_id.uuid,
            timeline_revision=validated_timeline.revision,
            output_width=output.width,
            output_height=output.height,
            output_fps=output.fps,
            duration_ms=validated_timeline.duration_ms,
            style=LocalEditingCaptionRenderStyle(
                font_key=style.font_key,
                font_px=style.font_px,
                stroke_px=style.stroke_px,
                line_spacing=style.line_spacing,
            ),
            cues=cues,
        )
    except Exception:
        raise LocalEditingCaptionPlanRejected from None


__all__ = [
    "LocalEditingCaptionPlanRejected",
    "LocalEditingVisualPlanRejected",
    "create_local_editing_caption_render_plan",
    "create_local_editing_visual_render_plan",
]
