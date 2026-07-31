"""LE-10 T2: quantize absolute millisecond boundaries onto one frame grid."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import fields
from pathlib import Path
from uuid import uuid4

import pytest

from automation_tool.executor.visual_rendering import (
    VisualFrameGridPlan,
    VisualFrameGridRejected,
    VisualFrameGridRejection,
    quantize_visual_render_plan,
)
from automation_tool.protocol.local_editing import SegmentSelectionMaterialKind
from automation_tool.protocol.local_rendering import (
    LocalEditingVisualRenderClip,
    LocalEditingVisualRenderPlan,
    LocalEditingVisualTransitionKind,
)


def _image(
    sequence: int,
    *,
    start_ms: int,
    duration_ms: int,
    transition_ms: int | None = None,
) -> LocalEditingVisualRenderClip:
    return LocalEditingVisualRenderClip(
        sequence=sequence,
        material_id=uuid4(),
        kind=SegmentSelectionMaterialKind.IMAGE,
        start_ms=start_ms,
        duration_ms=duration_ms,
        source_in_ms=None,
        source_out_ms=None,
        transition_kind=(None if transition_ms is None else LocalEditingVisualTransitionKind.FADE),
        transition_duration_ms=transition_ms,
    )


def _video(
    sequence: int,
    *,
    start_ms: int,
    duration_ms: int,
    transition_ms: int | None = None,
) -> LocalEditingVisualRenderClip:
    return LocalEditingVisualRenderClip(
        sequence=sequence,
        material_id=uuid4(),
        kind=SegmentSelectionMaterialKind.VIDEO,
        start_ms=start_ms,
        duration_ms=duration_ms,
        source_in_ms=700,
        source_out_ms=700 + duration_ms,
        transition_kind=(
            None if transition_ms is None else LocalEditingVisualTransitionKind.DISSOLVE
        ),
        transition_duration_ms=transition_ms,
    )


def _plan(
    clips: tuple[LocalEditingVisualRenderClip, ...],
    *,
    duration_ms: int,
    fps: int = 30,
) -> LocalEditingVisualRenderPlan:
    return LocalEditingVisualRenderPlan(
        project_id=uuid4(),
        timeline_id=uuid4(),
        timeline_revision=2,
        output_width=720,
        output_height=1280,
        output_fps=fps,
        duration_ms=duration_ms,
        clips=clips,
    )


def test_absolute_boundaries_do_not_accumulate_per_clip_rounding() -> None:
    plan = _plan(
        tuple(_image(index, start_ms=(index - 1) * 50, duration_ms=50) for index in range(1, 5)),
        duration_ms=200,
    )

    result = quantize_visual_render_plan(plan)

    assert result.target_frames == 6
    assert tuple((clip.start_frame, clip.frame_count) for clip in result.clips) == (
        (0, 2),
        (2, 1),
        (3, 2),
        (5, 1),
    )
    assert result.clips[-1].end_frame == result.target_frames


def test_half_frame_boundaries_round_up_and_preserve_video_window() -> None:
    material_id = uuid4()
    clip = _video(1, start_ms=0, duration_ms=150)
    object.__setattr__(clip, "material_id", material_id)

    result = quantize_visual_render_plan(_plan((clip,), duration_ms=150))

    assert result.target_frames == 5
    assert result.clips[0].material_id == material_id
    assert result.clips[0].kind is SegmentSelectionMaterialKind.VIDEO
    assert (result.clips[0].source_in_ms, result.clips[0].source_out_ms) == (700, 850)


def test_transition_overlap_uses_quantized_absolute_boundaries() -> None:
    plan = _plan(
        (
            _video(1, start_ms=0, duration_ms=500),
            _image(2, start_ms=400, duration_ms=600, transition_ms=100),
        ),
        duration_ms=1000,
    )

    result = quantize_visual_render_plan(plan)

    assert result.target_frames == 30
    assert tuple((clip.start_frame, clip.frame_count) for clip in result.clips) == (
        (0, 15),
        (12, 18),
    )
    assert result.clips[1].transition_frames == 3
    assert result.clips[1].transition_kind is LocalEditingVisualTransitionKind.FADE


def test_a_clip_that_quantizes_to_zero_frames_is_rejected() -> None:
    plan = _plan(
        (
            _image(1, start_ms=0, duration_ms=1),
            _image(2, start_ms=1, duration_ms=99),
        ),
        duration_ms=100,
    )

    with pytest.raises(VisualFrameGridRejected) as error:
        quantize_visual_render_plan(plan)

    assert error.value.code is VisualFrameGridRejection.CLIP_BELOW_ONE_FRAME


def test_a_real_transition_that_quantizes_to_zero_frames_is_not_a_hard_cut() -> None:
    plan = _plan(
        (
            _image(1, start_ms=0, duration_ms=100),
            _image(2, start_ms=99, duration_ms=100, transition_ms=1),
        ),
        duration_ms=199,
    )

    with pytest.raises(VisualFrameGridRejected) as error:
        quantize_visual_render_plan(plan)

    assert error.value.code is VisualFrameGridRejection.TRANSITION_BELOW_ONE_FRAME


@pytest.mark.parametrize(
    "plan",
    [
        lambda: _plan(
            (
                _image(1, start_ms=0, duration_ms=20),
                _image(2, start_ms=3, duration_ms=100, transition_ms=17),
            ),
            duration_ms=103,
        ),
        lambda: _plan(
            (
                _image(1, start_ms=0, duration_ms=100),
                _image(2, start_ms=83, duration_ms=20, transition_ms=17),
            ),
            duration_ms=103,
        ),
    ],
)
def test_quantized_transition_may_not_swallow_outgoing_or_incoming_clip(
    plan: Callable[[], LocalEditingVisualRenderPlan],
) -> None:
    with pytest.raises(VisualFrameGridRejected) as error:
        quantize_visual_render_plan(plan())

    assert error.value.code is VisualFrameGridRejection.TRANSITION_SWALLOWS_CLIP


def test_public_boundary_rejects_mutated_plan_before_iterating_it() -> None:
    plan = _plan((_image(1, start_ms=0, duration_ms=100),), duration_ms=100)
    object.__setattr__(plan, "clips", list(plan.clips))

    with pytest.raises(VisualFrameGridRejected) as error:
        quantize_visual_render_plan(plan)

    assert error.value.code is VisualFrameGridRejection.INVALID_PLAN
    assert str(error.value) == "visual frame grid rejected"
    assert error.value.__cause__ is None

    plan = _plan((_image(1, start_ms=0, duration_ms=100),), duration_ms=100)
    object.__setattr__(plan.clips[0], "kind", "image")
    with pytest.raises(VisualFrameGridRejected) as nested_error:
        quantize_visual_render_plan(plan)
    assert nested_error.value.code is VisualFrameGridRejection.INVALID_PLAN


def test_frame_grid_result_and_executor_source_remain_path_free() -> None:
    result = quantize_visual_render_plan(
        _plan((_image(1, start_ms=0, duration_ms=100),), duration_ms=100)
    )
    names = {
        field.name
        for value_type in (type(result.clips[0]), VisualFrameGridPlan)
        for field in fields(value_type)
    }
    source = (
        Path(__file__).parents[3] / "src" / "automation_tool" / "executor" / "visual_rendering.py"
    ).read_text(encoding="utf-8")

    assert not any(token in name for name in names for token in ("path", "argv", "codec"))
    assert "automation_tool.control_plane" not in source
