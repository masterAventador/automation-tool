"""LE-10 T1: path-free visual render values shared across processes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import fields
from typing import cast
from uuid import UUID, uuid4

import pytest

from automation_tool.protocol.local_editing import SegmentSelectionMaterialKind
from automation_tool.protocol.local_rendering import (
    LOCAL_EDITING_VISUAL_RENDER_VERSION,
    MAX_LOCAL_EDITING_SOURCE_DURATION_MS,
    MAX_LOCAL_EDITING_TRANSITION_DURATION_MS,
    LocalEditingVisualRenderClip,
    LocalEditingVisualRenderPlan,
    LocalEditingVisualRenderRejected,
    LocalEditingVisualTransitionKind,
)


def _video(
    sequence: int,
    *,
    start_ms: int = 0,
    duration_ms: int = 600,
    transition_kind: LocalEditingVisualTransitionKind | None = None,
    transition_duration_ms: int | None = None,
) -> LocalEditingVisualRenderClip:
    return LocalEditingVisualRenderClip(
        sequence=sequence,
        material_id=uuid4(),
        kind=SegmentSelectionMaterialKind.VIDEO,
        start_ms=start_ms,
        duration_ms=duration_ms,
        source_in_ms=100,
        source_out_ms=100 + duration_ms,
        transition_kind=transition_kind,
        transition_duration_ms=transition_duration_ms,
    )


def _image(
    sequence: int,
    *,
    start_ms: int,
    duration_ms: int,
    transition_kind: LocalEditingVisualTransitionKind | None = None,
    transition_duration_ms: int | None = None,
) -> LocalEditingVisualRenderClip:
    return LocalEditingVisualRenderClip(
        sequence=sequence,
        material_id=uuid4(),
        kind=SegmentSelectionMaterialKind.IMAGE,
        start_ms=start_ms,
        duration_ms=duration_ms,
        source_in_ms=None,
        source_out_ms=None,
        transition_kind=transition_kind,
        transition_duration_ms=transition_duration_ms,
    )


def _plan(
    clips: tuple[LocalEditingVisualRenderClip, ...] | None = None,
) -> LocalEditingVisualRenderPlan:
    return LocalEditingVisualRenderPlan(
        project_id=uuid4(),
        timeline_id=uuid4(),
        timeline_revision=3,
        output_width=720,
        output_height=1280,
        output_fps=30,
        duration_ms=1000,
        clips=clips
        or (
            _video(1),
            _image(
                2,
                start_ms=500,
                duration_ms=500,
                transition_kind=LocalEditingVisualTransitionKind.FADE,
                transition_duration_ms=100,
            ),
        ),
    )


def test_visual_render_plan_is_versioned_path_free_and_preserves_layout() -> None:
    result = _plan()

    assert result.version == LOCAL_EDITING_VISUAL_RENDER_VERSION
    assert result.output_width == 720
    assert result.output_height == 1280
    assert result.output_fps == 30
    assert tuple(clip.sequence for clip in result.clips) == (1, 2)
    assert result.clips[0].source_in_ms == 100
    assert result.clips[1].kind is SegmentSelectionMaterialKind.IMAGE
    assert result.clips[1].transition_kind is LocalEditingVisualTransitionKind.FADE
    field_names = {
        field.name
        for value_type in (LocalEditingVisualRenderClip, LocalEditingVisualRenderPlan)
        for field in fields(value_type)
    }
    assert not any(token in name for name in field_names for token in ("path", "argv", "codec"))


@pytest.mark.parametrize(
    "construct",
    [
        lambda: LocalEditingVisualRenderClip(
            sequence=1,
            material_id=uuid4(),
            kind=SegmentSelectionMaterialKind.AUDIO,
            start_ms=0,
            duration_ms=100,
            source_in_ms=0,
            source_out_ms=100,
            transition_kind=None,
            transition_duration_ms=None,
        ),
        lambda: LocalEditingVisualRenderClip(
            sequence=1,
            material_id=uuid4(),
            kind=SegmentSelectionMaterialKind.VIDEO,
            start_ms=0,
            duration_ms=100,
            source_in_ms=None,
            source_out_ms=None,
            transition_kind=None,
            transition_duration_ms=None,
        ),
        lambda: LocalEditingVisualRenderClip(
            sequence=1,
            material_id=uuid4(),
            kind=SegmentSelectionMaterialKind.IMAGE,
            start_ms=0,
            duration_ms=100,
            source_in_ms=0,
            source_out_ms=100,
            transition_kind=None,
            transition_duration_ms=None,
        ),
        lambda: LocalEditingVisualRenderClip(
            sequence=1,
            material_id=UUID(int=0),
            kind=SegmentSelectionMaterialKind.VIDEO,
            start_ms=0,
            duration_ms=100,
            source_in_ms=0,
            source_out_ms=100,
            transition_kind=None,
            transition_duration_ms=None,
        ),
    ],
)
def test_visual_clip_shape_fails_closed(construct: Callable[[], object]) -> None:
    with pytest.raises(LocalEditingVisualRenderRejected):
        construct()


def test_visual_kind_requires_the_enum_not_an_equal_raw_string() -> None:
    with pytest.raises(LocalEditingVisualRenderRejected):
        LocalEditingVisualRenderClip(
            sequence=1,
            material_id=uuid4(),
            kind=cast(SegmentSelectionMaterialKind, "video"),
            start_ms=0,
            duration_ms=100,
            source_in_ms=0,
            source_out_ms=100,
            transition_kind=None,
            transition_duration_ms=None,
        )


@pytest.mark.parametrize(
    ("source_in_ms", "source_out_ms"),
    [
        (-1, 99),
        (MAX_LOCAL_EDITING_SOURCE_DURATION_MS - 99, MAX_LOCAL_EDITING_SOURCE_DURATION_MS + 1),
        (0, 99),
    ],
)
def test_video_source_window_bounds_fail_closed(
    source_in_ms: int,
    source_out_ms: int,
) -> None:
    with pytest.raises(LocalEditingVisualRenderRejected):
        LocalEditingVisualRenderClip(
            sequence=1,
            material_id=uuid4(),
            kind=SegmentSelectionMaterialKind.VIDEO,
            start_ms=0,
            duration_ms=100,
            source_in_ms=source_in_ms,
            source_out_ms=source_out_ms,
            transition_kind=None,
            transition_duration_ms=None,
        )


@pytest.mark.parametrize(
    ("kind", "duration"),
    [
        (LocalEditingVisualTransitionKind.FADE, None),
        (None, 1),
        (cast(LocalEditingVisualTransitionKind, "fade"), 1),
        (LocalEditingVisualTransitionKind.FADE, 1.0),
        (LocalEditingVisualTransitionKind.FADE, 0),
        (
            LocalEditingVisualTransitionKind.FADE,
            MAX_LOCAL_EDITING_TRANSITION_DURATION_MS + 1,
        ),
        (LocalEditingVisualTransitionKind.FADE, 100),
    ],
)
def test_transition_shape_and_bounds_fail_closed(
    kind: LocalEditingVisualTransitionKind | None,
    duration: int | None,
) -> None:
    with pytest.raises(LocalEditingVisualRenderRejected):
        LocalEditingVisualRenderClip(
            sequence=2,
            material_id=uuid4(),
            kind=SegmentSelectionMaterialKind.IMAGE,
            start_ms=99,
            duration_ms=100,
            source_in_ms=None,
            source_out_ms=None,
            transition_kind=kind,
            transition_duration_ms=duration,
        )


def test_plan_rejects_wrong_container_sequence_layout_and_first_transition() -> None:
    constructors: tuple[Callable[[], object], ...] = (
        lambda: LocalEditingVisualRenderPlan(
            project_id=uuid4(),
            timeline_id=uuid4(),
            timeline_revision=1,
            output_width=720,
            output_height=1280,
            output_fps=30,
            duration_ms=600,
            clips=cast(tuple[LocalEditingVisualRenderClip, ...], [_video(1)]),
        ),
        lambda: _plan((_video(2), _image(3, start_ms=600, duration_ms=400))),
        lambda: _plan((_video(1), _image(2, start_ms=601, duration_ms=399))),
        lambda: _plan(
            (
                _video(
                    1,
                    transition_kind=LocalEditingVisualTransitionKind.FADE,
                    transition_duration_ms=100,
                ),
                _image(2, start_ms=600, duration_ms=400),
            )
        ),
    )

    for construct in constructors:
        with pytest.raises(LocalEditingVisualRenderRejected):
            construct()


def test_plan_revalidates_mutated_nested_values_without_leaking_them() -> None:
    clip = _video(1, duration_ms=1000)
    object.__setattr__(clip, "source_in_ms", "/Users/private/source.mp4")

    with pytest.raises(LocalEditingVisualRenderRejected) as error:
        _plan((clip,))

    assert str(error.value) == "local visual render plan rejected"
    assert error.value.__cause__ is None
    assert "private" not in str(error.value)


def test_plan_rejects_a_valid_clip_tuple_whose_last_end_misses_duration() -> None:
    with pytest.raises(LocalEditingVisualRenderRejected):
        LocalEditingVisualRenderPlan(
            project_id=uuid4(),
            timeline_id=uuid4(),
            timeline_revision=1,
            output_width=720,
            output_height=1280,
            output_fps=30,
            duration_ms=1001,
            clips=(_video(1, duration_ms=1000),),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("output_width", 719),
        ("output_height", 127),
        ("output_fps", 11),
        ("output_fps", 60.0),
        ("timeline_revision", 0),
        ("duration_ms", 99),
    ],
)
def test_plan_output_and_identity_bounds_fail_closed(field: str, value: object) -> None:
    plan = _plan()
    payload = {item.name: getattr(plan, item.name) for item in fields(plan)}
    payload[field] = value

    with pytest.raises(LocalEditingVisualRenderRejected):
        LocalEditingVisualRenderPlan(**payload)


def test_protocol_import_graph_has_no_control_plane_or_executor() -> None:
    import automation_tool.protocol.local_rendering as module

    source = module.__file__
    assert source is not None
    content = __import__("pathlib").Path(source).read_text(encoding="utf-8")
    assert "automation_tool.control_plane" not in content
    assert "automation_tool.executor" not in content
