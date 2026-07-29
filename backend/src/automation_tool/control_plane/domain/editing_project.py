"""One editing project: what it renders to, and how its captions look."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Never, final

from automation_tool.control_plane.domain.resource_ids import ResourceId

MAX_PROJECT_TITLE_CHARACTERS: Final = 200
MIN_OUTPUT_DIMENSION: Final = 128
MAX_OUTPUT_DIMENSION: Final = 4096
MIN_OUTPUT_FPS: Final = 12
MAX_OUTPUT_FPS: Final = 60
MIN_CAPTION_FONT_PX: Final = 12
MAX_CAPTION_FONT_PX: Final = 200
MAX_CAPTION_STROKE_PX: Final = 20
MIN_CAPTION_LINE_SPACING: Final = 1.0
MAX_CAPTION_LINE_SPACING: Final = 3.0

_FONT_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,63}\Z")


class InvalidEditingProjectModel(ValueError):
    """An editing project domain value is invalid."""

    def __init__(self) -> None:
        super().__init__("Editing project model is invalid")


def _reject() -> Never:
    raise InvalidEditingProjectModel


def _validate_text(value: object, *, maximum: int, optional: bool = False) -> None:
    if value is None and optional:
        return
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > maximum:
        _reject()
    for character in value:
        if character in {"\n", "\t"}:
            continue
        if unicodedata.category(character).startswith("C"):
            _reject()


def _validate_timestamp(value: object) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != UTC.utcoffset(value)
    ):
        _reject()


@final
class EditingProjectId(ResourceId):
    """Stable identifier for one editing project."""

    __slots__ = ()
    _resource = "editing project"


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


@dataclass(frozen=True, slots=True)
class EditingProject:
    """One editing project: the render settings every job under it inherits.

    It holds no material list and no timeline — those are separate
    aggregates joined at the repository layer, not object references.
    """

    project_id: EditingProjectId
    title: str
    output: OutputSpec
    caption_style: CaptionStyle
    created_at: datetime

    def __post_init__(self) -> None:
        if (
            not isinstance(self.project_id, EditingProjectId)
            or not isinstance(self.output, OutputSpec)
            or not isinstance(self.caption_style, CaptionStyle)
        ):
            _reject()
        _validate_text(self.title, maximum=MAX_PROJECT_TITLE_CHARACTERS)
        _validate_timestamp(self.created_at)


__all__ = [
    "MAX_CAPTION_FONT_PX",
    "MAX_CAPTION_LINE_SPACING",
    "MAX_CAPTION_STROKE_PX",
    "MAX_OUTPUT_DIMENSION",
    "MAX_OUTPUT_FPS",
    "MAX_PROJECT_TITLE_CHARACTERS",
    "MIN_CAPTION_FONT_PX",
    "MIN_CAPTION_LINE_SPACING",
    "MIN_OUTPUT_DIMENSION",
    "MIN_OUTPUT_FPS",
    "CaptionStyle",
    "EditingProject",
    "EditingProjectId",
    "InvalidEditingProjectModel",
    "OutputSpec",
]
