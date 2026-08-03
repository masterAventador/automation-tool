"""LE-10 T5: path-free caption cues shared across process boundaries."""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from automation_tool.protocol.local_rendering import (
    LOCAL_EDITING_CAPTION_RENDER_VERSION,
    MAX_LOCAL_EDITING_CAPTION_CUES,
    MAX_LOCAL_EDITING_CAPTION_TEXT_CHARACTERS,
    LocalEditingCaptionRenderCue,
    LocalEditingCaptionRenderPlan,
    LocalEditingCaptionRenderRejected,
    LocalEditingCaptionRenderStyle,
)


def _style(**overrides: Any) -> LocalEditingCaptionRenderStyle:
    return LocalEditingCaptionRenderStyle(
        **{
            "font_key": "noto-sans-cjk-sc-bold",
            "font_px": 48,
            "stroke_px": 2,
            "line_spacing": 1.2,
            **overrides,
        }
    )


def _cue(sequence: int = 1, **overrides: Any) -> LocalEditingCaptionRenderCue:
    return LocalEditingCaptionRenderCue(
        **{
            "sequence": sequence,
            "start_ms": 100,
            "duration_ms": 300,
            "text": "字幕内容",
            **overrides,
        }
    )


def _plan(
    cues: tuple[LocalEditingCaptionRenderCue, ...] | None = None,
    **overrides: Any,
) -> LocalEditingCaptionRenderPlan:
    return LocalEditingCaptionRenderPlan(
        **{
            "project_id": uuid4(),
            "timeline_id": uuid4(),
            "timeline_revision": 3,
            "output_width": 720,
            "output_height": 1280,
            "output_fps": 30,
            "duration_ms": 1000,
            "style": _style(),
            "cues": (_cue(),) if cues is None else cues,
            **overrides,
        }
    )


def test_caption_plan_carries_style_and_absolute_cues_without_local_paths() -> None:
    plan = LocalEditingCaptionRenderPlan(
        project_id=uuid4(),
        timeline_id=uuid4(),
        timeline_revision=3,
        output_width=720,
        output_height=1280,
        output_fps=30,
        duration_ms=1000,
        style=LocalEditingCaptionRenderStyle(
            font_key="noto-sans-cjk-sc-bold",
            font_px=48,
            stroke_px=2,
            line_spacing=1.2,
        ),
        cues=(
            LocalEditingCaptionRenderCue(
                sequence=1,
                start_ms=100,
                duration_ms=300,
                text="第一行\n第二行",
            ),
            LocalEditingCaptionRenderCue(
                sequence=2,
                start_ms=600,
                duration_ms=300,
                text="带\t制表符",
            ),
        ),
    )

    assert plan.version == LOCAL_EDITING_CAPTION_RENDER_VERSION
    assert tuple((cue.start_ms, cue.end_ms) for cue in plan.cues) == ((100, 400), (600, 900))
    assert "path" not in repr(plan).lower()


def test_a_captionless_timeline_has_one_valid_empty_plan() -> None:
    plan = _plan(())

    assert plan.cues == ()
    assert plan.version == "local-editing.caption-render.v1"


def test_caption_plan_accepts_exactly_the_limit_and_rejects_one_more() -> None:
    cues = tuple(
        LocalEditingCaptionRenderCue(index, index - 1, 1, "字")
        for index in range(1, MAX_LOCAL_EDITING_CAPTION_CUES + 1)
    )

    assert len(_plan(cues).cues) == MAX_LOCAL_EDITING_CAPTION_CUES
    with pytest.raises(LocalEditingCaptionRenderRejected):
        _plan((*cues, cues[-1]))


@pytest.mark.parametrize(
    "overrides",
    [
        {"font_key": "../private"},
        {"font_px": True},
        {"font_px": 11},
        {"font_px": 201},
        {"stroke_px": False},
        {"stroke_px": -1},
        {"stroke_px": 21},
        {"stroke_px": 24},
        {"line_spacing": 1},
        {"line_spacing": 0.9},
        {"line_spacing": 3.1},
    ],
)
def test_caption_style_fails_closed(overrides: dict[str, object]) -> None:
    with pytest.raises(LocalEditingCaptionRenderRejected) as error:
        _style(**overrides)

    assert str(error.value) == "local caption render plan rejected"
    assert error.value.__cause__ is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"sequence": True},
        {"sequence": 0},
        {"sequence": MAX_LOCAL_EDITING_CAPTION_CUES + 1},
        {"start_ms": True},
        {"start_ms": -1},
        {"duration_ms": True},
        {"duration_ms": 0},
        {"duration_ms": 600_001},
        {"text": ""},
        {"text": " 字幕"},
        {"text": "字幕 "},
        {"text": "字幕\u0080"},
        {"text": "字" * (MAX_LOCAL_EDITING_CAPTION_TEXT_CHARACTERS + 1)},
    ],
)
def test_caption_cue_fails_closed(overrides: dict[str, object]) -> None:
    with pytest.raises(LocalEditingCaptionRenderRejected) as error:
        _cue(**cast(Any, overrides))

    assert error.value.__cause__ is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"project_id": UUID(int=0)},
        {"timeline_id": UUID(int=0)},
        {"timeline_revision": True},
        {"timeline_revision": 0},
        {"output_width": 721},
        {"output_height": 127},
        {"output_fps": 61},
        {"duration_ms": 99},
        {"style": cast(LocalEditingCaptionRenderStyle, object())},
        {"cues": cast(tuple[LocalEditingCaptionRenderCue, ...], [])},
    ],
)
def test_caption_plan_shape_fails_closed(overrides: dict[str, object]) -> None:
    with pytest.raises(LocalEditingCaptionRenderRejected) as error:
        _plan(**cast(Any, overrides))

    assert error.value.__cause__ is None


@pytest.mark.parametrize(
    "cues",
    [
        (_cue(sequence=2),),
        (_cue(start_ms=100, duration_ms=400), _cue(2, start_ms=499, duration_ms=100)),
        (_cue(start_ms=800, duration_ms=201),),
    ],
)
def test_caption_cues_are_contiguous_nonoverlapping_and_inside_timeline(
    cues: tuple[LocalEditingCaptionRenderCue, ...],
) -> None:
    with pytest.raises(LocalEditingCaptionRenderRejected):
        _plan(cues)


def test_nested_values_are_rebuilt_and_private_text_is_redacted() -> None:
    style = _style()
    cue = _cue(text="/Users/private/字幕")
    plan = _plan((cue,), style=style)

    assert repr(cue) == "LocalEditingCaptionRenderCue(<redacted>)"
    assert repr(plan) == "LocalEditingCaptionRenderPlan(<redacted>)"
    assert cue.text not in repr(cue)
    assert cue.text not in repr(plan)

    object.__setattr__(style, "font_px", 0)
    with pytest.raises(LocalEditingCaptionRenderRejected):
        _plan((cue,), style=style)

    style = _style()
    object.__setattr__(cue, "text", "\u202eprivate")
    with pytest.raises(LocalEditingCaptionRenderRejected):
        _plan((cue,), style=style)


def test_protocol_field_tree_and_imports_have_no_machine_local_metadata() -> None:
    field_names = {
        field.name
        for value_type in (
            LocalEditingCaptionRenderStyle,
            LocalEditingCaptionRenderCue,
            LocalEditingCaptionRenderPlan,
        )
        for field in fields(value_type)
    }
    source = (
        Path(__file__).parents[3] / "src" / "automation_tool" / "protocol" / "local_rendering.py"
    ).read_text(encoding="utf-8")

    assert not any(token in name for name in field_names for token in ("path", "argv", "codec"))
    assert "automation_tool.control_plane" not in source
    assert "automation_tool.executor" not in source
