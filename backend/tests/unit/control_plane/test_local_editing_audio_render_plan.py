"""LE-11 T2: project a complete domain Timeline into the path-free audio wire."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from automation_tool.control_plane.application.local_editing_visual_render import (
    LocalEditingAudioPlanRejected,
    create_local_editing_audio_render_plan,
)
from automation_tool.control_plane.domain import (
    CaptionStyle,
    EditingProject,
    EditingProjectId,
    MaterialId,
    OriginalAudioMode,
    OutputSpec,
    Timeline,
    TimelineClip,
    TimelineId,
    TimelineTrack,
    TimelineTrackKind,
)
from automation_tool.protocol.local_rendering import (
    LocalEditingAudioTrackKind,
    LocalEditingOriginalAudioMode,
)

NOW = datetime(2026, 8, 1, 2, 0, tzinfo=UTC)


def _clip(
    clip_id: str,
    start_ms: int,
    duration_ms: int,
    *,
    gain_db: float,
    mode: OriginalAudioMode | None = None,
) -> TimelineClip:
    return TimelineClip(
        clip_id=clip_id,
        start_ms=start_ms,
        duration_ms=duration_ms,
        source_material_id=MaterialId.new(),
        source_in_ms=100,
        source_out_ms=100 + duration_ms,
        text=None,
        gain_db=gain_db,
        transition_in=None,
        original_audio_mode=mode,
    )


def _values() -> tuple[EditingProject, Timeline]:
    project_id = EditingProjectId.new()
    project = EditingProject(
        project_id=project_id,
        title="音频测试",
        output=OutputSpec(width=720, height=1280, fps=30),
        caption_style=CaptionStyle(
            font_key="noto-sans-cjk-sc-bold",
            font_px=48,
            stroke_px=3,
            line_spacing=1.2,
        ),
        created_at=NOW,
    )
    visual = TimelineTrack(
        "visual",
        TimelineTrackKind.VISUAL,
        (
            TimelineClip(
                clip_id="visual-1",
                start_ms=0,
                duration_ms=1000,
                source_material_id=MaterialId.new(),
                source_in_ms=0,
                source_out_ms=1000,
                text=None,
                gain_db=None,
                transition_in=None,
            ),
        ),
    )
    narration = TimelineTrack(
        "narration",
        TimelineTrackKind.NARRATION,
        (_clip("narration-1", 200, 300, gain_db=0.0),),
    )
    ambient = TimelineTrack(
        "ambient",
        TimelineTrackKind.AMBIENT,
        (
            _clip(
                "ambient-1",
                0,
                250,
                gain_db=-12.0,
                mode=OriginalAudioMode.AUTO_DUCK,
            ),
            _clip(
                "ambient-2",
                250,
                250,
                gain_db=-9.0,
                mode=OriginalAudioMode.FIXED_VOLUME,
            ),
            _clip(
                "ambient-3",
                500,
                250,
                gain_db=-6.0,
                mode=OriginalAudioMode.MUTED,
            ),
        ),
    )
    music = TimelineTrack(
        "music",
        TimelineTrackKind.MUSIC,
        (_clip("music-1", 0, 1000, gain_db=-24.0),),
    )
    return project, Timeline(
        timeline_id=TimelineId.new(),
        project_id=project_id,
        revision=4,
        duration_ms=1000,
        tracks=(visual, music, ambient, narration),
        created_at=NOW,
    )


def test_projection_rebuilds_domain_and_emits_only_three_audio_lanes() -> None:
    project, timeline = _values()

    plan = create_local_editing_audio_render_plan(project, timeline)

    assert plan.project_id == project.project_id.uuid
    assert plan.timeline_id == timeline.timeline_id.uuid
    assert plan.timeline_revision == 4
    assert plan.duration_ms == 1000
    assert tuple(clip.sequence for clip in plan.clips) == (1, 2, 3, 4, 5)
    assert tuple(clip.track_kind for clip in plan.clips) == (
        LocalEditingAudioTrackKind.NARRATION,
        LocalEditingAudioTrackKind.AMBIENT,
        LocalEditingAudioTrackKind.AMBIENT,
        LocalEditingAudioTrackKind.AMBIENT,
        LocalEditingAudioTrackKind.MUSIC,
    )
    assert tuple(clip.original_audio_mode for clip in plan.clips[1:4]) == (
        LocalEditingOriginalAudioMode.AUTO_DUCK,
        LocalEditingOriginalAudioMode.FIXED_VOLUME,
        LocalEditingOriginalAudioMode.MUTED,
    )
    assert all(clip.source_in_ms == 100 for clip in plan.clips)


def test_projection_accepts_a_visual_only_timeline_as_an_empty_audio_plan() -> None:
    project, timeline = _values()
    visual = timeline.track_of(TimelineTrackKind.VISUAL)
    assert visual is not None
    object.__setattr__(timeline, "tracks", (visual,))

    plan = create_local_editing_audio_render_plan(project, timeline)

    assert plan.clips == ()


def test_projection_fails_closed_on_mutated_new_mode_without_leaking_values() -> None:
    project, timeline = _values()
    ambient = timeline.track_of(TimelineTrackKind.AMBIENT)
    assert ambient is not None
    object.__setattr__(ambient.clips[0], "original_audio_mode", "/Users/private/audio.wav")

    with pytest.raises(LocalEditingAudioPlanRejected) as error:
        create_local_editing_audio_render_plan(project, timeline)

    assert str(error.value) == "local audio render projection rejected"
    assert "private" not in str(error.value)
    assert error.value.__cause__ is None


def test_projection_rejects_wrong_outer_values_and_project_identity() -> None:
    project, timeline = _values()
    with pytest.raises(LocalEditingAudioPlanRejected):
        create_local_editing_audio_render_plan(object(), timeline)  # type: ignore[arg-type]

    other_project = EditingProject(
        project_id=EditingProjectId.new(),
        title=project.title,
        output=project.output,
        caption_style=project.caption_style,
        created_at=project.created_at,
    )
    with pytest.raises(LocalEditingAudioPlanRejected):
        create_local_editing_audio_render_plan(other_project, timeline)
