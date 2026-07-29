"""Editing project invariants: what a render targets, and how captions look."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from automation_tool.control_plane.domain.editing_project import (
    MAX_CAPTION_FONT_PX,
    MAX_CAPTION_LINE_SPACING,
    MAX_CAPTION_STROKE_PX,
    MAX_OUTPUT_DIMENSION,
    MAX_OUTPUT_FPS,
    MAX_PROJECT_TITLE_CHARACTERS,
    MIN_CAPTION_FONT_PX,
    MIN_CAPTION_LINE_SPACING,
    MIN_OUTPUT_DIMENSION,
    MIN_OUTPUT_FPS,
    CaptionStyle,
    EditingProject,
    EditingProjectId,
    InvalidEditingProjectModel,
    OutputSpec,
)


def test_invalid_editing_project_model_is_a_value_error() -> None:
    assert issubclass(InvalidEditingProjectModel, ValueError)


def _output(**overrides: object) -> OutputSpec:
    defaults: dict[str, object] = {"width": 1080, "height": 1920, "fps": 30}
    defaults.update(overrides)
    return OutputSpec(**defaults)  # type: ignore[arg-type]


def test_a_portrait_output_is_accepted() -> None:
    spec = _output()
    assert (spec.width, spec.height, spec.fps) == (1080, 1920, 30)


@pytest.mark.parametrize("field", ["width", "height"])
def test_an_odd_frame_side_is_rejected_because_the_encoder_refuses_it(field: str) -> None:
    """h264/yuv420p halves chroma on both axes; ffmpeg rejects an odd size."""
    with pytest.raises(InvalidEditingProjectModel):
        _output(**{field: 1081})


@pytest.mark.parametrize("field", ["width", "height"])
@pytest.mark.parametrize(
    "value",
    [
        MIN_OUTPUT_DIMENSION - 2,
        MAX_OUTPUT_DIMENSION + 2,
        1080.0,
        True,
        "1080",
        None,
    ],
)
def test_frame_sides_fail_closed(field: str, value: object) -> None:
    with pytest.raises(InvalidEditingProjectModel):
        _output(**{field: value})


def test_frame_side_bounds_are_inclusive() -> None:
    assert _output(width=MIN_OUTPUT_DIMENSION, height=MIN_OUTPUT_DIMENSION).width == (
        MIN_OUTPUT_DIMENSION
    )
    assert _output(width=MAX_OUTPUT_DIMENSION, height=MAX_OUTPUT_DIMENSION).height == (
        MAX_OUTPUT_DIMENSION
    )


@pytest.mark.parametrize("fps", [MIN_OUTPUT_FPS - 1, MAX_OUTPUT_FPS + 1, 29.97, True, "30", None])
def test_frame_rate_fails_closed(fps: object) -> None:
    with pytest.raises(InvalidEditingProjectModel):
        _output(fps=fps)


def test_frame_rate_bounds_are_inclusive() -> None:
    assert _output(fps=MIN_OUTPUT_FPS).fps == MIN_OUTPUT_FPS
    assert _output(fps=MAX_OUTPUT_FPS).fps == MAX_OUTPUT_FPS


def _caption(**overrides: object) -> CaptionStyle:
    defaults: dict[str, object] = {
        "font_key": "noto-sans-sc",
        "font_px": 48,
        "stroke_px": 3,
        "line_spacing": 1.4,
    }
    defaults.update(overrides)
    return CaptionStyle(**defaults)  # type: ignore[arg-type]


def test_a_caption_style_is_accepted() -> None:
    assert _caption().font_key == "noto-sans-sc"


@pytest.mark.parametrize(
    "font_key",
    [
        "../../../etc/passwd",
        "/absolute/path.ttf",
        "Noto-Sans-SC",
        "noto sans sc",
        "9-leading-digit",
        "",
        "x" * 65,
        None,
        b"noto",
        # `_FONT_KEY_PATTERN.fullmatch` already rejects this because fullmatch
        # requires the whole string to be consumed — the newline is left over
        # regardless of `$` vs `\Z`. Pinned separately so the guard does not
        # quietly start depending on that call-site choice of verb: `$` (unlike
        # `\Z`) would let a future `match()` rewrite treat a trailing newline as
        # end-of-string and accept it.
        "noto-sans-sc\n",
    ],
)
def test_a_font_key_names_a_registry_entry_never_a_path(font_key: object) -> None:
    """It reaches the renderer as a filename; a free string could walk out."""
    with pytest.raises(InvalidEditingProjectModel):
        _caption(font_key=font_key)


def test_a_font_key_at_the_maximum_length_is_accepted() -> None:
    """Pattern is `^[a-z][a-z0-9-]{0,63}\\Z`: 1 + 63 = 64 characters is the cap."""
    key = "x" * 64
    assert _caption(font_key=key).font_key == key


@pytest.mark.parametrize(
    "font_px", [MIN_CAPTION_FONT_PX - 1, MAX_CAPTION_FONT_PX + 1, 48.0, True, None]
)
def test_caption_size_fails_closed(font_px: object) -> None:
    with pytest.raises(InvalidEditingProjectModel):
        _caption(font_px=font_px)


def test_caption_font_px_bounds_are_inclusive() -> None:
    assert _caption(font_px=MIN_CAPTION_FONT_PX).font_px == MIN_CAPTION_FONT_PX
    assert _caption(font_px=MAX_CAPTION_FONT_PX).font_px == MAX_CAPTION_FONT_PX


@pytest.mark.parametrize("stroke_px", [-1, MAX_CAPTION_STROKE_PX + 1, 3.0, True, None])
def test_caption_stroke_fails_closed(stroke_px: object) -> None:
    with pytest.raises(InvalidEditingProjectModel):
        _caption(stroke_px=stroke_px)


def test_a_caption_may_have_no_stroke_at_all() -> None:
    assert _caption(stroke_px=0).stroke_px == 0


def test_a_stroke_that_would_swallow_the_glyph_is_rejected() -> None:
    """The stroke is drawn on both sides of the outline, so it costs 2x."""
    assert _caption(font_px=20, stroke_px=9).stroke_px == 9
    with pytest.raises(InvalidEditingProjectModel):
        _caption(font_px=20, stroke_px=10)


def test_caption_stroke_upper_bound_is_inclusive() -> None:
    assert _caption(stroke_px=MAX_CAPTION_STROKE_PX).stroke_px == MAX_CAPTION_STROKE_PX


@pytest.mark.parametrize(
    "line_spacing",
    [
        MIN_CAPTION_LINE_SPACING - 0.1,
        MAX_CAPTION_LINE_SPACING + 0.1,
        1,
        True,
        "1.4",
        None,
    ],
)
def test_caption_line_spacing_fails_closed(line_spacing: object) -> None:
    with pytest.raises(InvalidEditingProjectModel):
        _caption(line_spacing=line_spacing)


def test_caption_line_spacing_bounds_are_inclusive() -> None:
    assert _caption(line_spacing=MIN_CAPTION_LINE_SPACING).line_spacing == (
        MIN_CAPTION_LINE_SPACING
    )
    assert _caption(line_spacing=MAX_CAPTION_LINE_SPACING).line_spacing == (
        MAX_CAPTION_LINE_SPACING
    )


def test_editing_project_id_is_a_uuid4_resource_id() -> None:
    identifier = EditingProjectId.new()
    assert EditingProjectId.parse(str(identifier)) == identifier


def test_editing_project_id_rejects_a_foreign_identifier_type() -> None:
    from automation_tool.control_plane.domain.resource_ids import InvalidResourceId
    from automation_tool.control_plane.domain.timeline import TimelineId

    with pytest.raises(InvalidResourceId):
        EditingProjectId.parse(TimelineId.new())


def _project(**overrides: object) -> EditingProject:
    defaults: dict[str, object] = {
        "project_id": EditingProjectId.new(),
        "title": "国庆探店合集",
        "output": _output(),
        "caption_style": _caption(),
        "created_at": datetime(2026, 7, 29, 10, 0, tzinfo=UTC),
    }
    defaults.update(overrides)
    return EditingProject(**defaults)  # type: ignore[arg-type]


def test_a_project_carries_everything_a_render_needs_but_the_timeline() -> None:
    project = _project()
    assert project.output.fps == 30
    assert project.caption_style.font_px == 48


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("project_id", "not-an-id"),
        ("title", ""),
        ("title", "   "),
        ("title", "  前后有空白  "),
        ("title", "带\x00空字符"),
        ("title", "x" * (MAX_PROJECT_TITLE_CHARACTERS + 1)),
        ("title", None),
        ("output", {"width": 1080, "height": 1920, "fps": 30}),
        ("output", None),
        ("caption_style", "noto-sans-sc"),
        ("caption_style", None),
        ("created_at", datetime(2026, 7, 29, 10, 0)),
        ("created_at", "2026-07-29T10:00:00Z"),
        ("created_at", datetime(2026, 7, 29, 10, 0, tzinfo=timezone(timedelta(hours=8)))),
    ],
)
def test_project_structural_bounds_fail_closed(field: str, value: object) -> None:
    with pytest.raises(InvalidEditingProjectModel):
        _project(**{field: value})


def test_a_project_title_may_wrap_but_carries_no_control_characters() -> None:
    assert _project(title="国庆探店\n第二季").title == "国庆探店\n第二季"


def test_project_title_length_bound_is_inclusive() -> None:
    assert len(_project(title="国" * MAX_PROJECT_TITLE_CHARACTERS).title) == (
        MAX_PROJECT_TITLE_CHARACTERS
    )


def test_validate_text_accepts_a_missing_value_when_optional() -> None:
    """`title` is required, so `EditingProject` never passes `optional=True`.

    The branch has no caller in this module yet — a future optional field
    will be its first real user (mirroring how `MAX_PROJECT_TITLE_CHARACTERS`
    moved from T1 to its first real caller here). Exercised directly so it
    is not carried as untested dead code in the meantime.
    """
    from automation_tool.control_plane.domain.editing_project import _validate_text

    _validate_text(None, maximum=10, optional=True)
