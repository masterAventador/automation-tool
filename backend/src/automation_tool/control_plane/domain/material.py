"""Local editing material library: what one source file is and what we know about it."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Final, Never, final

from automation_tool.control_plane.domain.resource_ids import ResourceId

MAX_MATERIAL_DURATION_MS: Final = 4 * 60 * 60 * 1000
MAX_MATERIAL_DIMENSION: Final = 8192
MAX_DESCRIPTION_CHARACTERS: Final = 2_000
MAX_TRANSCRIPT_CHARACTERS: Final = 100_000
MAX_TAGS: Final = 32
MAX_TAG_CHARACTERS: Final = 32
MAX_SHOT_BOUNDARIES: Final = 4_096
MAX_SPEECH_SEGMENTS: Final = 4_096

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class InvalidMaterialModel(ValueError):
    """A material domain value is invalid."""

    def __init__(self) -> None:
        super().__init__("Material model is invalid")


@final
class MaterialId(ResourceId):
    """Stable identifier for one imported source file."""

    __slots__ = ()
    _resource = "material"


class MaterialKind(StrEnum):
    VIDEO = "video"
    IMAGE = "image"
    AUDIO = "audio"


class DescriptionSource(StrEnum):
    """Who wrote the description currently held by a material.

    The distinction exists to keep a later AI pass from overwriting what a
    user typed: `USER` is a terminal state for the description field.
    """

    AI = "ai"
    USER = "user"


def _reject() -> Never:
    raise InvalidMaterialModel
