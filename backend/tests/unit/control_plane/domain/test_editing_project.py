"""Editing project invariants: what a render targets, and how captions look."""

from __future__ import annotations

import pytest

from automation_tool.control_plane.domain.editing_project import (
    MAX_CAPTION_FONT_PX,
    MAX_CAPTION_LINE_SPACING,
    MAX_CAPTION_STROKE_PX,
    MAX_OUTPUT_DIMENSION,
    MAX_OUTPUT_FPS,
    MIN_CAPTION_FONT_PX,
    MIN_CAPTION_LINE_SPACING,
    MIN_OUTPUT_DIMENSION,
    MIN_OUTPUT_FPS,
    CaptionStyle,
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
    ],
)
def test_a_font_key_names_a_registry_entry_never_a_path(font_key: object) -> None:
    """It reaches the renderer as a filename; a free string could walk out."""
    with pytest.raises(InvalidEditingProjectModel):
        _caption(font_key=font_key)


@pytest.mark.parametrize(
    "font_px", [MIN_CAPTION_FONT_PX - 1, MAX_CAPTION_FONT_PX + 1, 48.0, True, None]
)
def test_caption_size_fails_closed(font_px: object) -> None:
    with pytest.raises(InvalidEditingProjectModel):
        _caption(font_px=font_px)


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
