"""One editing project: what it renders to, and how its captions look."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Never

MIN_OUTPUT_DIMENSION: Final = 128
MAX_OUTPUT_DIMENSION: Final = 4096
MIN_OUTPUT_FPS: Final = 12
MAX_OUTPUT_FPS: Final = 60
MIN_CAPTION_FONT_PX: Final = 12
MAX_CAPTION_FONT_PX: Final = 200
MAX_CAPTION_STROKE_PX: Final = 20
MIN_CAPTION_LINE_SPACING: Final = 1.0
MAX_CAPTION_LINE_SPACING: Final = 3.0

_FONT_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


class InvalidEditingProjectModel(ValueError):
    """An editing project domain value is invalid."""

    def __init__(self) -> None:
        super().__init__("Editing project model is invalid")


def _reject() -> Never:
    raise InvalidEditingProjectModel


@dataclass(frozen=True, slots=True)
class OutputSpec:
    """The frame every clip is scaled into, and how often frames are written.

    Both sides must be even. The shipped encoder writes h264 in yuv420p,
    whose chroma planes are half resolution on each axis, and ffmpeg
    refuses an odd size outright — so an odd value is rejected here, at
    submit time, rather than halfway through a render.
    """

    width: int
    height: int
    fps: int

    def __post_init__(self) -> None:
        for side in (self.width, self.height):
            if (
                type(side) is not int
                or not MIN_OUTPUT_DIMENSION <= side <= MAX_OUTPUT_DIMENSION
                or side % 2 != 0
            ):
                _reject()
        if type(self.fps) is not int or not MIN_OUTPUT_FPS <= self.fps <= MAX_OUTPUT_FPS:
            _reject()


@dataclass(frozen=True, slots=True)
class CaptionStyle:
    """How captions are drawn. PIL renders them; ffmpeg only overlays.

    `font_key` names an entry in the bundled font registry — never a path.
    It arrives from user settings and the renderer turns it into a
    filename, so a free string could walk out of the font directory. A
    pattern-checked key cannot.
    """

    font_key: str
    font_px: int
    stroke_px: int
    line_spacing: float

    def __post_init__(self) -> None:
        if (
            not isinstance(self.font_key, str)
            or _FONT_KEY_PATTERN.fullmatch(self.font_key) is None
            or type(self.font_px) is not int
            or not MIN_CAPTION_FONT_PX <= self.font_px <= MAX_CAPTION_FONT_PX
            or type(self.stroke_px) is not int
            or not 0 <= self.stroke_px <= MAX_CAPTION_STROKE_PX
            or type(self.line_spacing) is not float
            or not MIN_CAPTION_LINE_SPACING <= self.line_spacing <= MAX_CAPTION_LINE_SPACING
        ):
            _reject()
        # The stroke sits on both sides of the glyph outline, so it eats
        # twice its width out of the letterform.
        if self.stroke_px * 2 >= self.font_px:
            _reject()


__all__ = [
    "MAX_CAPTION_FONT_PX",
    "MAX_CAPTION_LINE_SPACING",
    "MAX_CAPTION_STROKE_PX",
    "MAX_OUTPUT_DIMENSION",
    "MAX_OUTPUT_FPS",
    "MIN_CAPTION_FONT_PX",
    "MIN_CAPTION_LINE_SPACING",
    "MIN_OUTPUT_DIMENSION",
    "MIN_OUTPUT_FPS",
    "CaptionStyle",
    "InvalidEditingProjectModel",
    "OutputSpec",
]
