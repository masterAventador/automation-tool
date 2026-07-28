"""Local editing material library: what one source file is and what we know about it."""

from __future__ import annotations

import re
from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class Material:
    """One imported source file and everything probing has learned about it."""

    material_id: MaterialId
    kind: MaterialKind
    duration_ms: int | None
    width: int
    height: int
    content_digest: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.material_id, MaterialId)
            or not isinstance(self.kind, MaterialKind)
            or type(self.width) is not int
            or not 1 <= self.width <= MAX_MATERIAL_DIMENSION
            or type(self.height) is not int
            or not 1 <= self.height <= MAX_MATERIAL_DIMENSION
            or not isinstance(self.content_digest, str)
            or _SHA256_PATTERN.fullmatch(self.content_digest) is None
        ):
            _reject()
        self._validate_duration()

    def _validate_duration(self) -> None:
        if self.kind is MaterialKind.IMAGE:
            if self.duration_ms is not None:
                _reject()
            return
        if (
            type(self.duration_ms) is not int
            or not 1 <= self.duration_ms <= MAX_MATERIAL_DURATION_MS
        ):
            _reject()
