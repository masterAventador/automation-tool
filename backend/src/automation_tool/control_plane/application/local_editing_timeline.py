"""Construct a Timeline from the path-free local smart-editing protocol."""

from __future__ import annotations

from datetime import datetime
from typing import NoReturn

from automation_tool.control_plane.domain.editing_project import EditingProjectId
from automation_tool.control_plane.domain.material import MaterialId
from automation_tool.control_plane.domain.timeline import (
    InvalidTimelineModel,
    Timeline,
    TimelineClip,
    TimelineId,
    TimelineTrack,
    TimelineTrackKind,
)
from automation_tool.protocol.local_editing import (
    LocalEditingTimelineDraft,
    LocalEditingTimelineParagraph,
    LocalEditingTimelineParagraphKind,
)

_DEFAULT_AUDIO_GAIN_DB = 0.0


def _reject() -> NoReturn:
    raise InvalidTimelineModel from None


def _validated_draft(draft: LocalEditingTimelineDraft) -> LocalEditingTimelineDraft:
    if (
        not isinstance(draft, LocalEditingTimelineDraft)
        or not isinstance(draft.paragraphs, tuple)
        or not all(
            isinstance(paragraph, LocalEditingTimelineParagraph) for paragraph in draft.paragraphs
        )
    ):
        _reject()
    try:
        return LocalEditingTimelineDraft(
            paragraphs=tuple(
                LocalEditingTimelineParagraph(
                    sequence=paragraph.sequence,
                    kind=paragraph.kind,
                    visual_material_id=paragraph.visual_material_id,
                    audio_material_id=paragraph.audio_material_id,
                    duration_ms=paragraph.duration_ms,
                    visual_source_in_ms=paragraph.visual_source_in_ms,
                    visual_source_out_ms=paragraph.visual_source_out_ms,
                    caption_text=paragraph.caption_text,
                )
                for paragraph in draft.paragraphs
            )
        )
    except Exception:
        _reject()


def create_local_editing_timeline(
    draft: LocalEditingTimelineDraft,
    *,
    timeline_id: TimelineId,
    project_id: EditingProjectId,
    created_at: datetime,
) -> Timeline:
    """Build the initial editable cut through every Timeline constructor."""

    validated = _validated_draft(draft)
    positioned: list[tuple[LocalEditingTimelineParagraph, int]] = []
    start_ms = 0
    for paragraph in validated.paragraphs:
        positioned.append((paragraph, start_ms))
        start_ms += paragraph.duration_ms

    visual_clips = tuple(
        TimelineClip(
            clip_id=f"visual-{paragraph.sequence:04d}",
            start_ms=paragraph_start_ms,
            duration_ms=paragraph.duration_ms,
            source_material_id=MaterialId.parse(paragraph.visual_material_id),
            source_in_ms=paragraph.visual_source_in_ms,
            source_out_ms=paragraph.visual_source_out_ms,
            text=None,
            gain_db=None,
            transition_in=None,
        )
        for paragraph, paragraph_start_ms in positioned
    )
    narration_clips = tuple(
        TimelineClip(
            clip_id=f"narration-{paragraph.sequence:04d}",
            start_ms=paragraph_start_ms,
            duration_ms=paragraph.duration_ms,
            source_material_id=MaterialId.parse(paragraph.audio_material_id),
            source_in_ms=0,
            source_out_ms=paragraph.duration_ms,
            text=None,
            gain_db=_DEFAULT_AUDIO_GAIN_DB,
            transition_in=None,
        )
        for paragraph, paragraph_start_ms in positioned
        if paragraph.kind is LocalEditingTimelineParagraphKind.NARRATED
    )
    ambient_clips = tuple(
        TimelineClip(
            clip_id=f"ambient-{paragraph.sequence:04d}",
            start_ms=paragraph_start_ms,
            duration_ms=paragraph.duration_ms,
            source_material_id=MaterialId.parse(paragraph.audio_material_id),
            source_in_ms=paragraph.visual_source_in_ms,
            source_out_ms=paragraph.visual_source_out_ms,
            text=None,
            gain_db=_DEFAULT_AUDIO_GAIN_DB,
            transition_in=None,
        )
        for paragraph, paragraph_start_ms in positioned
        if paragraph.kind is LocalEditingTimelineParagraphKind.ORIGINAL_SPEECH
    )
    caption_clips = tuple(
        TimelineClip(
            clip_id=f"caption-{paragraph.sequence:04d}",
            start_ms=paragraph_start_ms,
            duration_ms=paragraph.duration_ms,
            source_material_id=None,
            source_in_ms=None,
            source_out_ms=None,
            text=paragraph.caption_text,
            gain_db=None,
            transition_in=None,
        )
        for paragraph, paragraph_start_ms in positioned
    )

    tracks = [
        TimelineTrack(
            track_id="visual",
            kind=TimelineTrackKind.VISUAL,
            clips=visual_clips,
        )
    ]
    if narration_clips:
        tracks.append(
            TimelineTrack(
                track_id="narration",
                kind=TimelineTrackKind.NARRATION,
                clips=narration_clips,
            )
        )
    if ambient_clips:
        tracks.append(
            TimelineTrack(
                track_id="ambient",
                kind=TimelineTrackKind.AMBIENT,
                clips=ambient_clips,
            )
        )
    tracks.append(
        TimelineTrack(
            track_id="caption",
            kind=TimelineTrackKind.CAPTION,
            clips=caption_clips,
        )
    )
    return Timeline(
        timeline_id=timeline_id,
        project_id=project_id,
        revision=1,
        duration_ms=validated.duration_ms,
        tracks=tuple(tracks),
        created_at=created_at,
    )


__all__ = ["create_local_editing_timeline"]
