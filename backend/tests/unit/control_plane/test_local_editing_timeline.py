"""LE-16 T5: build the smart-edit draft through Timeline domain constructors."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest

from automation_tool.control_plane.application.local_editing_timeline import (
    create_local_editing_timeline,
)
from automation_tool.control_plane.domain.editing_project import EditingProjectId
from automation_tool.control_plane.domain.material import MaterialId
from automation_tool.control_plane.domain.timeline import (
    MAX_CLIP_TEXT_CHARACTERS,
    MAX_TIMELINE_DURATION_MS,
    MIN_TIMELINE_DURATION_MS,
    InvalidTimelineModel,
    Timeline,
    TimelineId,
    TimelineTrackKind,
)
from automation_tool.protocol.local_editing import (
    MAX_LOCAL_EDITING_TIMELINE_CAPTION_CHARACTERS,
    MAX_LOCAL_EDITING_TIMELINE_DURATION_MS,
    MIN_LOCAL_EDITING_TIMELINE_DURATION_MS,
    LocalEditingTimelineDraft,
    LocalEditingTimelineParagraph,
    LocalEditingTimelineParagraphKind,
)

CREATED_AT = datetime(2026, 8, 1, 1, 0, tzinfo=UTC)


def _paragraph(
    sequence: int,
    kind: LocalEditingTimelineParagraphKind,
    visual_id: UUID,
    audio_id: UUID,
    *,
    duration_ms: int,
    source_in_ms: int | None,
    caption_text: str,
) -> LocalEditingTimelineParagraph:
    return LocalEditingTimelineParagraph(
        sequence=sequence,
        kind=kind,
        visual_material_id=visual_id,
        audio_material_id=audio_id,
        duration_ms=duration_ms,
        visual_source_in_ms=source_in_ms,
        visual_source_out_ms=(None if source_in_ms is None else source_in_ms + duration_ms),
        caption_text=caption_text,
    )


def _create(draft: LocalEditingTimelineDraft) -> Timeline:
    return create_local_editing_timeline(
        draft,
        timeline_id=TimelineId.new(),
        project_id=EditingProjectId.new(),
        created_at=CREATED_AT,
    )


def test_mixed_plan_builds_aligned_domain_tracks_without_transitions() -> None:
    original_video = uuid4()
    narrated_video = uuid4()
    narrated_image = uuid4()
    first_voiceover = uuid4()
    second_voiceover = uuid4()
    timeline_id = TimelineId.new()
    project_id = EditingProjectId.new()
    draft = LocalEditingTimelineDraft(
        paragraphs=(
            _paragraph(
                1,
                LocalEditingTimelineParagraphKind.ORIGINAL_SPEECH,
                original_video,
                original_video,
                duration_ms=300,
                source_in_ms=100,
                caption_text="完整原声",
            ),
            _paragraph(
                2,
                LocalEditingTimelineParagraphKind.NARRATED,
                narrated_video,
                first_voiceover,
                duration_ms=400,
                source_in_ms=200,
                caption_text="第一句旁白",
            ),
            _paragraph(
                3,
                LocalEditingTimelineParagraphKind.NARRATED,
                narrated_image,
                second_voiceover,
                duration_ms=500,
                source_in_ms=None,
                caption_text="第二句旁白",
            ),
        )
    )

    result = create_local_editing_timeline(
        draft,
        timeline_id=timeline_id,
        project_id=project_id,
        created_at=CREATED_AT,
    )

    assert isinstance(result, Timeline)
    assert result.timeline_id is timeline_id
    assert result.project_id is project_id
    assert result.revision == 1
    assert result.created_at is CREATED_AT
    assert result.duration_ms == 1_200
    assert tuple(track.kind for track in result.tracks) == (
        TimelineTrackKind.VISUAL,
        TimelineTrackKind.NARRATION,
        TimelineTrackKind.AMBIENT,
        TimelineTrackKind.CAPTION,
    )

    visual = result.track_of(TimelineTrackKind.VISUAL)
    assert visual is not None
    assert tuple((clip.start_ms, clip.duration_ms) for clip in visual.clips) == (
        (0, 300),
        (300, 400),
        (700, 500),
    )
    assert tuple(clip.source_material_id for clip in visual.clips) == tuple(
        MaterialId.parse(value) for value in (original_video, narrated_video, narrated_image)
    )
    assert tuple((clip.source_in_ms, clip.source_out_ms) for clip in visual.clips) == (
        (100, 400),
        (200, 600),
        (None, None),
    )
    assert all(clip.transition_in is None and clip.gain_db is None for clip in visual.clips)
    assert visual.end_ms == result.duration_ms

    narration = result.track_of(TimelineTrackKind.NARRATION)
    assert narration is not None
    assert tuple((clip.start_ms, clip.duration_ms) for clip in narration.clips) == (
        (300, 400),
        (700, 500),
    )
    assert tuple(clip.source_material_id for clip in narration.clips) == (
        MaterialId.parse(first_voiceover),
        MaterialId.parse(second_voiceover),
    )
    assert tuple((clip.source_in_ms, clip.source_out_ms) for clip in narration.clips) == (
        (0, 400),
        (0, 500),
    )
    assert all(clip.gain_db == 0.0 for clip in narration.clips)

    ambient = result.track_of(TimelineTrackKind.AMBIENT)
    assert ambient is not None
    assert len(ambient.clips) == 1
    assert (
        ambient.clips[0].start_ms,
        ambient.clips[0].duration_ms,
        ambient.clips[0].source_material_id,
        ambient.clips[0].source_in_ms,
        ambient.clips[0].source_out_ms,
        ambient.clips[0].gain_db,
    ) == (0, 300, MaterialId.parse(original_video), 100, 400, 0.0)

    caption = result.track_of(TimelineTrackKind.CAPTION)
    assert caption is not None
    assert tuple((clip.start_ms, clip.duration_ms, clip.text) for clip in caption.clips) == (
        (0, 300, "完整原声"),
        (300, 400, "第一句旁白"),
        (700, 500, "第二句旁白"),
    )
    assert result.track_of(TimelineTrackKind.MUSIC) is None


def test_pure_original_plan_omits_narration_track() -> None:
    material_id = uuid4()
    result = _create(
        LocalEditingTimelineDraft(
            paragraphs=(
                _paragraph(
                    1,
                    LocalEditingTimelineParagraphKind.ORIGINAL_SPEECH,
                    material_id,
                    material_id,
                    duration_ms=100,
                    source_in_ms=0,
                    caption_text="纯原声",
                ),
            )
        )
    )

    assert result.track_of(TimelineTrackKind.NARRATION) is None
    assert result.track_of(TimelineTrackKind.AMBIENT) is not None


def test_pure_narrated_plan_omits_ambient_track() -> None:
    result = _create(
        LocalEditingTimelineDraft(
            paragraphs=(
                _paragraph(
                    1,
                    LocalEditingTimelineParagraphKind.NARRATED,
                    uuid4(),
                    uuid4(),
                    duration_ms=100,
                    source_in_ms=None,
                    caption_text="纯旁白",
                ),
            )
        )
    )

    assert result.track_of(TimelineTrackKind.AMBIENT) is None
    assert result.track_of(TimelineTrackKind.NARRATION) is not None


def test_protocol_limits_are_guarded_against_timeline_domain_drift() -> None:
    assert MIN_LOCAL_EDITING_TIMELINE_DURATION_MS == MIN_TIMELINE_DURATION_MS
    assert MAX_LOCAL_EDITING_TIMELINE_DURATION_MS == MAX_TIMELINE_DURATION_MS
    assert MAX_LOCAL_EDITING_TIMELINE_CAPTION_CHARACTERS == MAX_CLIP_TEXT_CHARACTERS


def test_factory_revalidates_mutated_protocol_values_without_leaking_them() -> None:
    visual_id = uuid4()
    paragraph = _paragraph(
        1,
        LocalEditingTimelineParagraphKind.ORIGINAL_SPEECH,
        visual_id,
        visual_id,
        duration_ms=100,
        source_in_ms=0,
        caption_text="原声",
    )
    draft = LocalEditingTimelineDraft(paragraphs=(paragraph,))
    object.__setattr__(paragraph, "caption_text", "/Users/private/movie.mp4\0")

    with pytest.raises(InvalidTimelineModel) as error:
        _create(draft)

    assert str(error.value) == "Timeline model is invalid"
    assert "private" not in str(error.value)
    assert error.value.__cause__ is None


def test_factory_rejects_wrong_public_argument_types_through_domain_error() -> None:
    with pytest.raises(InvalidTimelineModel):
        create_local_editing_timeline(
            cast(LocalEditingTimelineDraft, object()),
            timeline_id=TimelineId.new(),
            project_id=EditingProjectId.new(),
            created_at=CREATED_AT,
        )


def test_control_plane_factory_depends_on_protocol_not_executor_implementation() -> None:
    source_path = (
        Path(__file__).parents[3]
        / "src"
        / "automation_tool"
        / "control_plane"
        / "application"
        / "local_editing_timeline.py"
    )
    source = source_path.read_text(encoding="utf-8")

    assert "automation_tool.executor" not in source
    assert "TimelineClip(" in source
    assert "TimelineTrack(" in source
    assert "Timeline(" in source
