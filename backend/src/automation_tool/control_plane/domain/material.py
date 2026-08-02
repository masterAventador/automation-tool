"""Local editing material library: what one source file is and what we know about it."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from datetime import datetime
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

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}\Z")


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


@dataclass(frozen=True, slots=True)
class Material:
    """One imported source file and everything probing has learned about it."""

    material_id: MaterialId
    kind: MaterialKind
    duration_ms: int | None
    width: int | None
    height: int | None
    content_digest: str
    has_audio: bool
    audio_loudness_lufs: float | None
    has_speech: bool
    speech_segments_ms: tuple[tuple[int, int], ...]
    speech_transcript: str | None
    shot_boundaries_ms: tuple[int, ...]
    ai_description: str | None
    ai_tags: tuple[str, ...]
    description_source: DescriptionSource
    described_at: datetime | None

    @classmethod
    def register(
        cls,
        *,
        material_id: MaterialId,
        kind: MaterialKind,
        duration_ms: int | None,
        width: int | None,
        height: int | None,
        content_digest: str,
        has_audio: bool,
        audio_loudness_lufs: float | None,
        has_speech: bool,
        speech_segments_ms: tuple[tuple[int, int], ...],
        speech_transcript: str | None,
        shot_boundaries_ms: tuple[int, ...],
        ai_description: str | None,
        ai_tags: tuple[str, ...],
        description_source: DescriptionSource,
        described_at: datetime | None,
    ) -> Material:
        """Build the first snapshot accepted at the registration boundary.

        Later description changes must use the two guarded instance methods.
        Keeping initial construction here lets API adapters register probe facts
        without opening a second field-by-field constructor elsewhere.
        """
        return cls(
            material_id=material_id,
            kind=kind,
            duration_ms=duration_ms,
            width=width,
            height=height,
            content_digest=content_digest,
            has_audio=has_audio,
            audio_loudness_lufs=audio_loudness_lufs,
            has_speech=has_speech,
            speech_segments_ms=speech_segments_ms,
            speech_transcript=speech_transcript,
            shot_boundaries_ms=shot_boundaries_ms,
            ai_description=ai_description,
            ai_tags=ai_tags,
            description_source=description_source,
            described_at=described_at,
        )

    def __post_init__(self) -> None:
        if (
            not isinstance(self.material_id, MaterialId)
            or not isinstance(self.kind, MaterialKind)
            or not isinstance(self.content_digest, str)
            or _SHA256_PATTERN.fullmatch(self.content_digest) is None
        ):
            _reject()
        self._validate_frame_size()
        self._validate_duration()
        self._validate_audio()
        self._validate_shot_boundaries()
        self._validate_description()

    def _validate_frame_size(self) -> None:
        """Only material with a picture has a frame size; audio has none to state."""
        if self.kind is MaterialKind.AUDIO:
            if self.width is not None or self.height is not None:
                _reject()
            return
        for value in (self.width, self.height):
            if type(value) is not int or not 1 <= value <= MAX_MATERIAL_DIMENSION:
                _reject()

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

    def _validate_audio(self) -> None:
        if type(self.has_audio) is not bool or type(self.has_speech) is not bool:
            _reject()
        if self.kind is MaterialKind.IMAGE and self.has_audio:
            _reject()
        if self.kind is MaterialKind.AUDIO and not self.has_audio:
            _reject()
        if not self.has_audio:
            if self.audio_loudness_lufs is not None or self.has_speech:
                _reject()
        elif self.audio_loudness_lufs is not None and (
            type(self.audio_loudness_lufs) is not float
            or not -70.0 <= self.audio_loudness_lufs <= 0.0
        ):
            _reject()
        if not isinstance(self.speech_segments_ms, tuple):
            _reject()
        if not self.has_speech:
            if self.speech_segments_ms or self.speech_transcript is not None:
                _reject()
            return
        if not 1 <= len(self.speech_segments_ms) <= MAX_SPEECH_SEGMENTS:
            _reject()
        _validate_text(self.speech_transcript, maximum=MAX_TRANSCRIPT_CHARACTERS)
        previous_end = 0
        for segment in self.speech_segments_ms:
            if (
                not isinstance(segment, tuple)
                or len(segment) != 2
                or any(type(value) is not int for value in segment)
            ):
                _reject()
            start, end = segment
            if start < previous_end or end <= start:
                _reject()
            if self.duration_ms is not None and end > self.duration_ms:
                _reject()
            previous_end = end

    def _validate_shot_boundaries(self) -> None:
        if not isinstance(self.shot_boundaries_ms, tuple):
            _reject()
        if not self.shot_boundaries_ms:
            return
        if self.kind is MaterialKind.AUDIO:
            _reject()
        if self.duration_ms is None or len(self.shot_boundaries_ms) > MAX_SHOT_BOUNDARIES:
            _reject()
        previous = -1
        for boundary in self.shot_boundaries_ms:
            if (
                type(boundary) is not int
                or boundary <= previous
                or not 0 <= boundary < self.duration_ms
            ):
                _reject()
            previous = boundary

    def _validate_description(self) -> None:
        if not isinstance(self.description_source, DescriptionSource):
            _reject()
        _validate_text(self.ai_description, maximum=MAX_DESCRIPTION_CHARACTERS, optional=True)
        if not isinstance(self.ai_tags, tuple) or len(self.ai_tags) > MAX_TAGS:
            _reject()
        for tag in self.ai_tags:
            _validate_text(tag, maximum=MAX_TAG_CHARACTERS)
        if len(set(self.ai_tags)) != len(self.ai_tags):
            _reject()
        if self.described_at is not None and (
            not isinstance(self.described_at, datetime) or self.described_at.tzinfo is None
        ):
            _reject()
        if self.ai_description is None and (self.ai_tags or self.described_at is not None):
            _reject()
        if (
            self.description_source is DescriptionSource.AI
            and self.ai_description is not None
            and self.described_at is None
        ):
            _reject()
        if self.description_source is DescriptionSource.USER and self.described_at is not None:
            _reject()
        if self.description_source is DescriptionSource.USER and self.ai_description is None:
            _reject()
        if self.description_source is DescriptionSource.USER and self.ai_tags:
            _reject()

    def with_ai_understanding(
        self,
        description: str,
        tags: tuple[str, ...],
        shot_boundaries_ms: tuple[int, ...],
        described_at: datetime,
    ) -> Material:
        """Record the model's complete understanding unless a person owns it.

        Returns self unchanged when the description came from the user. The
        check lives here rather than in the caller because every future
        understanding pass would otherwise have to remember it. Description,
        tags, timestamp and shot boundaries move together so callers cannot
        persist a torn model result.
        """
        if self.description_source is DescriptionSource.USER:
            return self
        return replace(
            self,
            ai_description=description,
            ai_tags=tags,
            shot_boundaries_ms=shot_boundaries_ms,
            described_at=described_at,
            description_source=DescriptionSource.AI,
        )

    def with_user_description(self, description: str) -> Material:
        """Record what a person typed, and mark the field theirs from now on.

        This irreversibly drops any existing `ai_tags`: `description_source`
        becomes `USER`, a terminal state, so `with_ai_understanding` will never
        run again to regenerate them. The dropped tags described the text
        that just got replaced, so keeping them would misattribute stale
        classification data to the new, human-written description.
        """
        return replace(
            self,
            ai_description=description,
            ai_tags=(),
            described_at=None,
            description_source=DescriptionSource.USER,
        )

    def with_speech_analysis(
        self,
        *,
        has_speech: bool,
        speech_segments_ms: tuple[tuple[int, int], ...],
        speech_transcript: str | None,
    ) -> Material:
        """Move the complete local-VAD and ASR result as one domain change."""

        return replace(
            self,
            has_speech=has_speech,
            speech_segments_ms=speech_segments_ms,
            speech_transcript=speech_transcript,
        )
