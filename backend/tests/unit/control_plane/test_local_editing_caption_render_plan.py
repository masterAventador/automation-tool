"""LE-10 T5: project caption timeline values onto the path-free render wire."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from automation_tool.control_plane.application.local_editing_visual_render import (
    LocalEditingCaptionPlanRejected,
    create_local_editing_caption_render_plan,
)
from automation_tool.control_plane.domain.editing_project import (
    MAX_CAPTION_FONT_PX,
    MAX_CAPTION_LINE_SPACING,
    MAX_CAPTION_STROKE_PX,
    MIN_CAPTION_FONT_PX,
    MIN_CAPTION_LINE_SPACING,
    CaptionStyle,
    EditingProject,
    EditingProjectId,
    OutputSpec,
)
from automation_tool.control_plane.domain.material import MaterialId
from automation_tool.control_plane.domain.timeline import (
    MAX_CLIP_TEXT_CHARACTERS,
    MAX_CLIPS_PER_TRACK,
    Timeline,
    TimelineClip,
    TimelineId,
    TimelineTrack,
    TimelineTrackKind,
)
from automation_tool.executor.captions import render as caption_render
from automation_tool.protocol.local_rendering import (
    MAX_LOCAL_EDITING_CAPTION_CUES,
    MAX_LOCAL_EDITING_CAPTION_FONT_PX,
    MAX_LOCAL_EDITING_CAPTION_LINE_SPACING,
    MAX_LOCAL_EDITING_CAPTION_STROKE_PX,
    MAX_LOCAL_EDITING_CAPTION_TEXT_CHARACTERS,
    MIN_LOCAL_EDITING_CAPTION_FONT_PX,
    MIN_LOCAL_EDITING_CAPTION_LINE_SPACING,
)

NOW = datetime(2026, 8, 1, tzinfo=UTC)


def _project(project_id: EditingProjectId) -> EditingProject:
    return EditingProject(
        project_id=project_id,
        title="字幕计划",
        output=OutputSpec(width=720, height=1280, fps=30),
        caption_style=CaptionStyle(
            font_key="noto-sans-cjk-sc-bold",
            font_px=48,
            stroke_px=2,
            line_spacing=1.2,
        ),
        created_at=NOW,
    )


def _visual_clip() -> TimelineClip:
    return TimelineClip(
        clip_id="visual-1",
        start_ms=0,
        duration_ms=1000,
        source_material_id=MaterialId.new(),
        source_in_ms=0,
        source_out_ms=1000,
        text=None,
        gain_db=None,
        transition_in=None,
    )


def _timeline(project_id: EditingProjectId, *, with_captions: bool = True) -> Timeline:
    tracks = [
        TimelineTrack(
            track_id="visual",
            kind=TimelineTrackKind.VISUAL,
            clips=(_visual_clip(),),
        )
    ]
    if with_captions:
        tracks.append(
            TimelineTrack(
                track_id="caption",
                kind=TimelineTrackKind.CAPTION,
                clips=(
                    TimelineClip(
                        clip_id="caption-1",
                        start_ms=100,
                        duration_ms=300,
                        source_material_id=None,
                        source_in_ms=None,
                        source_out_ms=None,
                        text="第一条字幕",
                        gain_db=None,
                        transition_in=None,
                    ),
                    TimelineClip(
                        clip_id="caption-2",
                        start_ms=600,
                        duration_ms=300,
                        source_material_id=None,
                        source_in_ms=None,
                        source_out_ms=None,
                        text="第二条字幕",
                        gain_db=None,
                        transition_in=None,
                    ),
                ),
            )
        )
    return Timeline(
        timeline_id=TimelineId.new(),
        project_id=project_id,
        revision=4,
        duration_ms=1000,
        tracks=tuple(tracks),
        created_at=NOW,
    )


def test_project_and_caption_track_project_to_one_path_free_plan() -> None:
    project_id = EditingProjectId.new()
    project = _project(project_id)
    timeline = _timeline(project_id)

    result = create_local_editing_caption_render_plan(project, timeline)

    assert result.project_id == project_id.uuid
    assert result.timeline_id == timeline.timeline_id.uuid
    assert result.timeline_revision == 4
    assert (result.output_width, result.output_height, result.output_fps) == (720, 1280, 30)
    assert result.style.font_key == "noto-sans-cjk-sc-bold"
    assert tuple((cue.sequence, cue.text) for cue in result.cues) == (
        (1, "第一条字幕"),
        (2, "第二条字幕"),
    )
    assert "第一条字幕" not in repr(result)


def test_timeline_without_caption_track_projects_to_an_empty_caption_plan() -> None:
    project_id = EditingProjectId.new()

    result = create_local_editing_caption_render_plan(
        _project(project_id),
        _timeline(project_id, with_captions=False),
    )

    assert result.cues == ()


def test_project_identity_mismatch_is_rejected_without_partial_plan() -> None:
    with pytest.raises(LocalEditingCaptionPlanRejected) as error:
        create_local_editing_caption_render_plan(
            _project(EditingProjectId.new()),
            _timeline(EditingProjectId.new()),
        )

    assert str(error.value) == "local caption render projection rejected"
    assert error.value.__cause__ is None


@pytest.mark.parametrize("target", ["project_id", "timeline_id"])
def test_projection_rejects_semantically_wrong_identifier_types(target: str) -> None:
    project_id = EditingProjectId.new()
    project = _project(project_id)
    timeline = _timeline(project_id)
    if target == "project_id":
        wrong = MaterialId.new()
        object.__setattr__(project, "project_id", wrong)
        object.__setattr__(timeline, "project_id", wrong)
    else:
        object.__setattr__(timeline, "timeline_id", MaterialId.new())

    with pytest.raises(LocalEditingCaptionPlanRejected):
        create_local_editing_caption_render_plan(project, timeline)


def test_projection_rebuilds_style_tracks_and_private_caption_text() -> None:
    project_id = EditingProjectId.new()
    project = _project(project_id)
    timeline = _timeline(project_id)

    object.__setattr__(project.caption_style, "font_px", 0)
    with pytest.raises(LocalEditingCaptionPlanRejected):
        create_local_editing_caption_render_plan(project, timeline)

    project = _project(project_id)
    caption = timeline.track_of(TimelineTrackKind.CAPTION)
    assert caption is not None
    object.__setattr__(caption.clips[0], "text", "/Users/private/\u202efile")
    with pytest.raises(LocalEditingCaptionPlanRejected) as error:
        create_local_editing_caption_render_plan(project, timeline)
    assert "/Users/private" not in str(error.value)
    assert error.value.__cause__ is None


@pytest.mark.parametrize("mutation", ["raw_kind", "list_clips", "list_tracks"])
def test_projection_rejects_bypassed_outer_shapes(mutation: str) -> None:
    project_id = EditingProjectId.new()
    project = _project(project_id)
    timeline = _timeline(project_id)
    caption = timeline.track_of(TimelineTrackKind.CAPTION)
    assert caption is not None
    if mutation == "raw_kind":
        object.__setattr__(caption, "kind", "caption")
    elif mutation == "list_clips":
        object.__setattr__(caption, "clips", list(caption.clips))
    else:
        object.__setattr__(timeline, "tracks", list(timeline.tracks))

    with pytest.raises(LocalEditingCaptionPlanRejected):
        create_local_editing_caption_render_plan(project, timeline)


def test_caption_projection_does_not_import_executor_or_carry_machine_paths() -> None:
    source = (
        Path(__file__).parents[3]
        / "src"
        / "automation_tool"
        / "control_plane"
        / "application"
        / "local_editing_visual_render.py"
    ).read_text(encoding="utf-8")

    assert "automation_tool.executor" not in source


def test_caption_wire_limits_are_guarded_against_domain_and_executor_drift() -> None:
    assert (
        (
            MIN_LOCAL_EDITING_CAPTION_FONT_PX,
            MAX_LOCAL_EDITING_CAPTION_FONT_PX,
            MAX_LOCAL_EDITING_CAPTION_STROKE_PX,
            MIN_LOCAL_EDITING_CAPTION_LINE_SPACING,
            MAX_LOCAL_EDITING_CAPTION_LINE_SPACING,
        )
        == (
            MIN_CAPTION_FONT_PX,
            MAX_CAPTION_FONT_PX,
            MAX_CAPTION_STROKE_PX,
            MIN_CAPTION_LINE_SPACING,
            MAX_CAPTION_LINE_SPACING,
        )
        == (
            caption_render.MIN_CAPTION_FONT_PX,
            caption_render.MAX_CAPTION_FONT_PX,
            caption_render.MAX_CAPTION_STROKE_PX,
            caption_render.MIN_CAPTION_LINE_SPACING,
            caption_render.MAX_CAPTION_LINE_SPACING,
        )
    )
    assert MAX_LOCAL_EDITING_CAPTION_TEXT_CHARACTERS == MAX_CLIP_TEXT_CHARACTERS
    assert MAX_LOCAL_EDITING_CAPTION_CUES == MAX_CLIPS_PER_TRACK
