"""LE-10 T1: project real domain values onto the Executor render wire."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from automation_tool.control_plane.application.local_editing_visual_render import (
    LocalEditingVisualPlanRejected,
    create_local_editing_visual_render_plan,
)
from automation_tool.control_plane.domain.editing_project import (
    MAX_OUTPUT_DIMENSION,
    MAX_OUTPUT_FPS,
    MIN_OUTPUT_DIMENSION,
    MIN_OUTPUT_FPS,
    CaptionStyle,
    EditingProject,
    EditingProjectId,
    OutputSpec,
)
from automation_tool.control_plane.domain.material import MAX_MATERIAL_DURATION_MS, MaterialId
from automation_tool.control_plane.domain.timeline import (
    MAX_CLIPS_PER_TRACK,
    MAX_TIMELINE_DURATION_MS,
    MAX_TRANSITION_DURATION_MS,
    MIN_TIMELINE_DURATION_MS,
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
    MAX_LOCAL_EDITING_OUTPUT_DIMENSION,
    MAX_LOCAL_EDITING_OUTPUT_FPS,
    MAX_LOCAL_EDITING_RENDER_CLIPS,
    MAX_LOCAL_EDITING_RENDER_DURATION_MS,
    MAX_LOCAL_EDITING_SOURCE_DURATION_MS,
    MAX_LOCAL_EDITING_TRANSITION_DURATION_MS,
    MIN_LOCAL_EDITING_OUTPUT_DIMENSION,
    MIN_LOCAL_EDITING_OUTPUT_FPS,
    MIN_LOCAL_EDITING_RENDER_DURATION_MS,
    LocalEditingVisualTransitionKind,
)

NOW = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)


def _project(project_id: EditingProjectId) -> EditingProject:
    return EditingProject(
        project_id=project_id,
        title="本地画面管线",
        output=OutputSpec(width=720, height=1280, fps=30),
        caption_style=CaptionStyle(
            font_key="noto-sans-cjk-sc",
            font_px=48,
            stroke_px=2,
            line_spacing=1.2,
        ),
        created_at=NOW,
    )


def _timeline(
    project_id: EditingProjectId,
    *,
    transition_kind: TransitionKind = TransitionKind.DISSOLVE,
) -> Timeline:
    first_visual = MaterialId.new()
    second_visual = MaterialId.new()
    narration = MaterialId.new()
    return Timeline(
        timeline_id=TimelineId.new(),
        project_id=project_id,
        revision=4,
        duration_ms=1000,
        tracks=(
            TimelineTrack(
                track_id="visual",
                kind=TimelineTrackKind.VISUAL,
                clips=(
                    TimelineClip(
                        clip_id="visual-1",
                        start_ms=0,
                        duration_ms=600,
                        source_material_id=first_visual,
                        source_in_ms=100,
                        source_out_ms=700,
                        text=None,
                        gain_db=None,
                        transition_in=None,
                    ),
                    TimelineClip(
                        clip_id="visual-2",
                        start_ms=500,
                        duration_ms=500,
                        source_material_id=second_visual,
                        source_in_ms=None,
                        source_out_ms=None,
                        text=None,
                        gain_db=None,
                        transition_in=TimelineTransition(
                            kind=transition_kind,
                            duration_ms=100,
                        ),
                    ),
                ),
            ),
            TimelineTrack(
                track_id="narration",
                kind=TimelineTrackKind.NARRATION,
                clips=(
                    TimelineClip(
                        clip_id="narration-1",
                        start_ms=0,
                        duration_ms=1000,
                        source_material_id=narration,
                        source_in_ms=0,
                        source_out_ms=1000,
                        text=None,
                        gain_db=0.0,
                        transition_in=None,
                    ),
                ),
            ),
            TimelineTrack(
                track_id="caption",
                kind=TimelineTrackKind.CAPTION,
                clips=(
                    TimelineClip(
                        clip_id="caption-1",
                        start_ms=0,
                        duration_ms=1000,
                        source_material_id=None,
                        source_in_ms=None,
                        source_out_ms=None,
                        text="不会进入视觉协议",
                        gain_db=None,
                        transition_in=None,
                    ),
                ),
            ),
        ),
        created_at=NOW,
    )


def test_real_project_and_timeline_project_to_path_free_visual_plan() -> None:
    project_id = EditingProjectId.new()
    project = _project(project_id)
    timeline = _timeline(project_id)

    result = create_local_editing_visual_render_plan(project, timeline)

    assert result.project_id == project_id.uuid
    assert result.timeline_id == timeline.timeline_id.uuid
    assert result.timeline_revision == 4
    assert (result.output_width, result.output_height, result.output_fps) == (720, 1280, 30)
    assert tuple(clip.kind for clip in result.clips) == (
        SegmentSelectionMaterialKind.VIDEO,
        SegmentSelectionMaterialKind.IMAGE,
    )
    assert result.clips[0].source_in_ms == 100
    assert result.clips[1].transition_kind is LocalEditingVisualTransitionKind.DISSOLVE
    assert "不会进入视觉协议" not in repr(result)
    assert "narration" not in repr(result)


@pytest.mark.parametrize(
    ("domain_kind", "wire_kind"),
    [
        (TransitionKind.FADE, LocalEditingVisualTransitionKind.FADE),
        (TransitionKind.DISSOLVE, LocalEditingVisualTransitionKind.DISSOLVE),
        (TransitionKind.WIPE, LocalEditingVisualTransitionKind.WIPE),
    ],
)
def test_each_domain_transition_has_one_stable_wire_mapping(
    domain_kind: TransitionKind,
    wire_kind: LocalEditingVisualTransitionKind,
) -> None:
    project_id = EditingProjectId.new()

    result = create_local_editing_visual_render_plan(
        _project(project_id),
        _timeline(project_id, transition_kind=domain_kind),
    )

    assert result.clips[1].transition_kind is wire_kind


def test_project_identity_mismatch_is_rejected_without_partial_plan() -> None:
    project = _project(EditingProjectId.new())
    timeline = _timeline(EditingProjectId.new())

    with pytest.raises(LocalEditingVisualPlanRejected) as error:
        create_local_editing_visual_render_plan(project, timeline)

    assert str(error.value) == "local visual render projection rejected"
    assert error.value.__cause__ is None


def test_projection_rejects_semantically_wrong_domain_identifier_types() -> None:
    project_id = EditingProjectId.new()
    project = _project(project_id)
    timeline = _timeline(project_id)
    wrong_project_id = MaterialId.new()
    object.__setattr__(project, "project_id", wrong_project_id)
    object.__setattr__(timeline, "project_id", wrong_project_id)

    with pytest.raises(LocalEditingVisualPlanRejected):
        create_local_editing_visual_render_plan(project, timeline)

    project = _project(project_id)
    timeline = _timeline(project_id)
    object.__setattr__(timeline, "timeline_id", MaterialId.new())

    with pytest.raises(LocalEditingVisualPlanRejected):
        create_local_editing_visual_render_plan(project, timeline)


def test_projection_revalidates_mutated_domain_containers_and_nested_values() -> None:
    project_id = EditingProjectId.new()
    project = _project(project_id)
    timeline = _timeline(project_id)
    object.__setattr__(timeline, "tracks", list(timeline.tracks))

    with pytest.raises(LocalEditingVisualPlanRejected):
        create_local_editing_visual_render_plan(project, timeline)

    timeline = _timeline(project_id)
    visual = timeline.track_of(TimelineTrackKind.VISUAL)
    assert visual is not None
    object.__setattr__(visual.clips[0], "source_in_ms", "/Users/private/source.mp4")

    with pytest.raises(LocalEditingVisualPlanRejected) as error:
        create_local_editing_visual_render_plan(project, timeline)

    assert "private" not in str(error.value)
    assert error.value.__cause__ is None


def test_projection_rejects_wrong_elements_in_the_track_tuple_before_filtering() -> None:
    project_id = EditingProjectId.new()
    project = _project(project_id)
    timeline = _timeline(project_id)
    object.__setattr__(timeline, "tracks", (*timeline.tracks, "private/track"))

    with pytest.raises(LocalEditingVisualPlanRejected) as error:
        create_local_editing_visual_render_plan(project, timeline)

    assert "private" not in str(error.value)
    assert error.value.__cause__ is None


def test_projection_rejects_mutated_nonvisual_track_shapes_before_filtering() -> None:
    project_id = EditingProjectId.new()
    project = _project(project_id)
    for field, value in (("kind", "caption"), ("clips", [])):
        timeline = _timeline(project_id)
        nonvisual = timeline.tracks[-1]
        object.__setattr__(nonvisual, field, value)

        with pytest.raises(LocalEditingVisualPlanRejected):
            create_local_editing_visual_render_plan(project, timeline)


def test_projection_rebuilds_the_complete_domain_before_selecting_visual_data() -> None:
    project_id = EditingProjectId.new()

    project = _project(project_id)
    timeline = _timeline(project_id)
    object.__setattr__(project.caption_style, "font_px", 0)
    with pytest.raises(LocalEditingVisualPlanRejected):
        create_local_editing_visual_render_plan(project, timeline)

    project = _project(project_id)
    timeline = _timeline(project_id)
    caption = timeline.tracks[-1]
    duplicate_caption = TimelineTrack(
        track_id="caption-copy",
        kind=TimelineTrackKind.CAPTION,
        clips=caption.clips,
    )
    object.__setattr__(timeline, "tracks", (*timeline.tracks, duplicate_caption))
    with pytest.raises(LocalEditingVisualPlanRejected):
        create_local_editing_visual_render_plan(project, timeline)

    timeline = _timeline(project_id)
    caption = timeline.tracks[-1]
    object.__setattr__(caption.clips[0], "text", "/Users/private/caption")
    object.__setattr__(caption.clips[0], "source_in_ms", 0)
    with pytest.raises(LocalEditingVisualPlanRejected) as error:
        create_local_editing_visual_render_plan(project, timeline)
    assert "private" not in str(error.value)
    assert error.value.__cause__ is None


def test_render_wire_limits_are_guarded_against_domain_drift() -> None:
    assert (
        MIN_LOCAL_EDITING_OUTPUT_DIMENSION,
        MAX_LOCAL_EDITING_OUTPUT_DIMENSION,
        MIN_LOCAL_EDITING_OUTPUT_FPS,
        MAX_LOCAL_EDITING_OUTPUT_FPS,
    ) == (MIN_OUTPUT_DIMENSION, MAX_OUTPUT_DIMENSION, MIN_OUTPUT_FPS, MAX_OUTPUT_FPS)
    assert (
        MIN_LOCAL_EDITING_RENDER_DURATION_MS,
        MAX_LOCAL_EDITING_RENDER_DURATION_MS,
        MAX_LOCAL_EDITING_RENDER_CLIPS,
        MAX_LOCAL_EDITING_TRANSITION_DURATION_MS,
        MAX_LOCAL_EDITING_SOURCE_DURATION_MS,
    ) == (
        MIN_TIMELINE_DURATION_MS,
        MAX_TIMELINE_DURATION_MS,
        MAX_CLIPS_PER_TRACK,
        MAX_TRANSITION_DURATION_MS,
        MAX_MATERIAL_DURATION_MS,
    )


def test_projection_fails_closed_when_visual_track_is_removed_after_construction() -> None:
    project_id = EditingProjectId.new()
    project = _project(project_id)
    timeline = _timeline(project_id)
    object.__setattr__(
        timeline,
        "tracks",
        tuple(track for track in timeline.tracks if track.kind is not TimelineTrackKind.VISUAL),
    )

    with pytest.raises(LocalEditingVisualPlanRejected):
        create_local_editing_visual_render_plan(project, timeline)


def test_projection_rejects_mutated_visual_material_and_transition_objects() -> None:
    project_id = EditingProjectId.new()
    project = _project(project_id)
    timeline = _timeline(project_id)
    visual = timeline.track_of(TimelineTrackKind.VISUAL)
    assert visual is not None
    object.__setattr__(visual.clips[0], "source_material_id", uuid4())

    with pytest.raises(LocalEditingVisualPlanRejected):
        create_local_editing_visual_render_plan(project, timeline)

    timeline = _timeline(project_id)
    visual = timeline.track_of(TimelineTrackKind.VISUAL)
    assert visual is not None
    object.__setattr__(visual.clips[1], "transition_in", "fade")

    with pytest.raises(LocalEditingVisualPlanRejected):
        create_local_editing_visual_render_plan(project, timeline)


def test_control_plane_projection_does_not_import_executor() -> None:
    source = (
        Path(__file__).parents[3]
        / "src"
        / "automation_tool"
        / "control_plane"
        / "application"
        / "local_editing_visual_render.py"
    ).read_text(encoding="utf-8")

    assert "automation_tool.executor" not in source
