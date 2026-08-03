"""Versioned, path-free values shared by local smart-editing boundaries."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, NoReturn
from uuid import RFC_4122, UUID

LOCAL_EDITING_SEGMENT_SELECTION_VERSION: Final = "local-editing.segment-selection.v1"
LOCAL_EDITING_SPEECH_PARAGRAPH_VERSION: Final = "local-editing.speech-paragraph.v1"
LOCAL_EDITING_TIMELINE_DRAFT_VERSION: Final = "local-editing.timeline-draft.v1"
MAX_LOCAL_EDITING_MATERIAL_DURATION_MS: Final = 4 * 60 * 60 * 1_000
MAX_LOCAL_EDITING_SHOT_BOUNDARIES: Final = 4_096
MAX_LOCAL_EDITING_SPEECH_SEGMENTS: Final = 4_096
MAX_LOCAL_EDITING_TRANSCRIPT_CHARACTERS: Final = 100_000
MIN_LOCAL_EDITING_TIMELINE_DURATION_MS: Final = 100
MAX_LOCAL_EDITING_TIMELINE_DURATION_MS: Final = 600_000
MAX_LOCAL_EDITING_TIMELINE_CAPTION_CHARACTERS: Final = 2_000
MAX_LOCAL_EDITING_SCRIPT_SENTENCES: Final = 128
MAX_LOCAL_EDITING_SEMANTIC_MATERIALS: Final = 32
LOCAL_EDITING_SEMANTIC_SCORE_THRESHOLD: Final = 60

_SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}\Z")


class LocalEditingProtocolRejected(ValueError):
    """A local-editing value is unsafe or not canonical across the boundary."""

    def __init__(self) -> None:
        super().__init__("local editing protocol value is invalid")


def _reject() -> NoReturn:
    raise LocalEditingProtocolRejected from None


def is_canonical_local_editing_material_id(value: object) -> bool:
    """Return whether a value is the exact UUIDv4 identity used on this wire."""

    return (
        isinstance(value, UUID)
        and value.version == 4
        and value.variant == RFC_4122
        and str(UUID(str(value))) == str(value)
    )


class SegmentSelectionMaterialKind(StrEnum):
    VIDEO = "video"
    IMAGE = "image"
    AUDIO = "audio"


class LocalEditingTimelineParagraphKind(StrEnum):
    ORIGINAL_SPEECH = "original_speech"
    NARRATED = "narrated"


def _valid_local_editing_text(value: object, *, maximum: int) -> bool:
    return (
        type(value) is str
        and bool(value)
        and value == value.strip()
        and len(value) <= maximum
        and not any(
            character not in {"\n", "\t"} and unicodedata.category(character).startswith("C")
            for character in value
        )
    )


@dataclass(frozen=True, slots=True)
class LocalEditingTimelineParagraph:
    """One final, path-free paragraph ready for Timeline domain construction."""

    sequence: int
    kind: LocalEditingTimelineParagraphKind
    visual_material_id: UUID
    audio_material_id: UUID
    duration_ms: int
    visual_source_in_ms: int | None
    visual_source_out_ms: int | None
    caption_text: str

    def __post_init__(self) -> None:
        if (
            type(self.sequence) is not int
            or not 1 <= self.sequence <= MAX_LOCAL_EDITING_SEMANTIC_MATERIALS
            or not isinstance(self.kind, LocalEditingTimelineParagraphKind)
            or not is_canonical_local_editing_material_id(self.visual_material_id)
            or not is_canonical_local_editing_material_id(self.audio_material_id)
            or type(self.duration_ms) is not int
            or not 1 <= self.duration_ms <= MAX_LOCAL_EDITING_TIMELINE_DURATION_MS
            or (self.visual_source_in_ms is None) != (self.visual_source_out_ms is None)
            or not _valid_local_editing_text(
                self.caption_text,
                maximum=MAX_LOCAL_EDITING_TIMELINE_CAPTION_CHARACTERS,
            )
            or (
                self.kind is LocalEditingTimelineParagraphKind.ORIGINAL_SPEECH
                and (
                    self.audio_material_id != self.visual_material_id
                    or self.visual_source_in_ms is None
                )
            )
            or (
                self.kind is LocalEditingTimelineParagraphKind.NARRATED
                and self.audio_material_id == self.visual_material_id
            )
        ):
            _reject()
        if self.visual_source_in_ms is None:
            return
        if (
            type(self.visual_source_in_ms) is not int
            or type(self.visual_source_out_ms) is not int
            or self.visual_source_in_ms < 0
            or self.visual_source_out_ms > MAX_LOCAL_EDITING_MATERIAL_DURATION_MS
            or self.visual_source_out_ms - self.visual_source_in_ms != self.duration_ms
        ):
            _reject()


def _validated_timeline_paragraph(
    paragraph: LocalEditingTimelineParagraph,
) -> LocalEditingTimelineParagraph:
    try:
        return LocalEditingTimelineParagraph(
            sequence=paragraph.sequence,
            kind=paragraph.kind,
            visual_material_id=paragraph.visual_material_id,
            audio_material_id=paragraph.audio_material_id,
            duration_ms=paragraph.duration_ms,
            visual_source_in_ms=paragraph.visual_source_in_ms,
            visual_source_out_ms=paragraph.visual_source_out_ms,
            caption_text=paragraph.caption_text,
        )
    except Exception:
        _reject()


@dataclass(frozen=True, slots=True)
class LocalEditingTimelineDraft:
    """A complete all-or-nothing plan crossing into the Control Plane."""

    paragraphs: tuple[LocalEditingTimelineParagraph, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.paragraphs, tuple)
            or not 1 <= len(self.paragraphs) <= MAX_LOCAL_EDITING_SEMANTIC_MATERIALS
            or not all(
                isinstance(paragraph, LocalEditingTimelineParagraph)
                for paragraph in self.paragraphs
            )
        ):
            _reject()
        validated = tuple(_validated_timeline_paragraph(paragraph) for paragraph in self.paragraphs)
        visual_ids = {paragraph.visual_material_id for paragraph in validated}
        narration_audio_ids = tuple(
            paragraph.audio_material_id
            for paragraph in validated
            if paragraph.kind is LocalEditingTimelineParagraphKind.NARRATED
        )
        duration_ms = sum(paragraph.duration_ms for paragraph in validated)
        if (
            tuple(paragraph.sequence for paragraph in validated)
            != tuple(range(1, len(validated) + 1))
            or any(
                sum(paragraph.visual_material_id == material_id for paragraph in validated) > 1
                and any(
                    paragraph.visual_material_id == material_id
                    and paragraph.visual_source_in_ms is not None
                    for paragraph in validated
                )
                for material_id in visual_ids
            )
            or len(set(narration_audio_ids)) != len(narration_audio_ids)
            or any(material_id in visual_ids for material_id in narration_audio_ids)
            or not MIN_LOCAL_EDITING_TIMELINE_DURATION_MS
            <= duration_ms
            <= MAX_LOCAL_EDITING_TIMELINE_DURATION_MS
        ):
            _reject()

    @property
    def duration_ms(self) -> int:
        return sum(paragraph.duration_ms for paragraph in self.paragraphs)

    @property
    def version(self) -> str:
        return LOCAL_EDITING_TIMELINE_DRAFT_VERSION


@dataclass(frozen=True, slots=True)
class SpeechParagraphMaterial:
    """Only path-free speech facts needed to partition paragraph drafting."""

    material_id: UUID
    kind: SegmentSelectionMaterialKind
    duration_ms: int | None
    has_speech: bool
    speech_segments_ms: tuple[tuple[int, int], ...]
    speech_transcript: str | None

    def __post_init__(self) -> None:
        if (
            not is_canonical_local_editing_material_id(self.material_id)
            or not isinstance(self.kind, SegmentSelectionMaterialKind)
            or (self.kind is SegmentSelectionMaterialKind.IMAGE and self.duration_ms is not None)
            or (
                self.kind is not SegmentSelectionMaterialKind.IMAGE
                and (
                    type(self.duration_ms) is not int
                    or not 1 <= self.duration_ms <= MAX_LOCAL_EDITING_MATERIAL_DURATION_MS
                )
            )
            or type(self.has_speech) is not bool
            or not isinstance(self.speech_segments_ms, tuple)
        ):
            _reject()
        if not self.has_speech:
            if self.speech_segments_ms or self.speech_transcript is not None:
                _reject()
            return
        if (
            self.kind is not SegmentSelectionMaterialKind.VIDEO
            or type(self.duration_ms) is not int
            or not 1 <= len(self.speech_segments_ms) <= MAX_LOCAL_EDITING_SPEECH_SEGMENTS
            or not _valid_local_editing_text(
                self.speech_transcript,
                maximum=MAX_LOCAL_EDITING_TRANSCRIPT_CHARACTERS,
            )
        ):
            _reject()
        previous_end = 0
        for segment in self.speech_segments_ms:
            if (
                not isinstance(segment, tuple)
                or len(segment) != 2
                or any(type(value) is not int for value in segment)
            ):
                _reject()
            start, end = segment
            if start < previous_end or end <= start or end > self.duration_ms:
                _reject()
            previous_end = end

    @property
    def version(self) -> str:
        return LOCAL_EDITING_SPEECH_PARAGRAPH_VERSION


@dataclass(frozen=True, slots=True)
class SegmentSelectionMaterial:
    """Only path-free material facts needed to choose a source window."""

    material_id: UUID
    kind: SegmentSelectionMaterialKind
    duration_ms: int | None
    content_digest: str
    shot_boundaries_ms: tuple[int, ...]

    def __post_init__(self) -> None:
        if (
            not is_canonical_local_editing_material_id(self.material_id)
            or not isinstance(self.kind, SegmentSelectionMaterialKind)
            or (self.kind is SegmentSelectionMaterialKind.IMAGE and self.duration_ms is not None)
            or (
                self.kind is not SegmentSelectionMaterialKind.IMAGE
                and (
                    type(self.duration_ms) is not int
                    or not 1 <= self.duration_ms <= MAX_LOCAL_EDITING_MATERIAL_DURATION_MS
                )
            )
            or type(self.content_digest) is not str
            or _SHA256_PATTERN.fullmatch(self.content_digest) is None
            or not isinstance(self.shot_boundaries_ms, tuple)
            or len(self.shot_boundaries_ms) > MAX_LOCAL_EDITING_SHOT_BOUNDARIES
            or (
                self.kind is not SegmentSelectionMaterialKind.VIDEO
                and bool(self.shot_boundaries_ms)
            )
        ):
            _reject()
        previous = -1
        for boundary in self.shot_boundaries_ms:
            if (
                type(boundary) is not int
                or boundary <= previous
                or not 0 <= boundary < MAX_LOCAL_EDITING_MATERIAL_DURATION_MS
            ):
                _reject()
            previous = boundary

    @property
    def version(self) -> str:
        return LOCAL_EDITING_SEGMENT_SELECTION_VERSION


@dataclass(frozen=True, slots=True)
class SegmentSelectionCandidateScore:
    """One T1 score projected without a Control Plane implementation type."""

    material_id: UUID
    score: int
    qualified: bool

    def __post_init__(self) -> None:
        if (
            not is_canonical_local_editing_material_id(self.material_id)
            or type(self.score) is not int
            or not 0 <= self.score <= 100
            or type(self.qualified) is not bool
            or self.qualified is not (self.score >= LOCAL_EDITING_SEMANTIC_SCORE_THRESHOLD)
        ):
            _reject()


@dataclass(frozen=True, slots=True)
class SegmentSelectionSentenceMatches:
    """One complete T1 ranking in its already resolved deterministic order."""

    sequence: int
    candidates: tuple[SegmentSelectionCandidateScore, ...]

    def __post_init__(self) -> None:
        if (
            type(self.sequence) is not int
            or not 1 <= self.sequence <= MAX_LOCAL_EDITING_SCRIPT_SENTENCES
            or not isinstance(self.candidates, tuple)
            or not 1 <= len(self.candidates) <= MAX_LOCAL_EDITING_SEMANTIC_MATERIALS
            or not all(
                isinstance(candidate, SegmentSelectionCandidateScore)
                for candidate in self.candidates
            )
            or len({candidate.material_id for candidate in self.candidates}) != len(self.candidates)
            or any(
                earlier.score < later.score
                for earlier, later in zip(
                    self.candidates,
                    self.candidates[1:],
                    strict=False,
                )
            )
        ):
            _reject()

    @property
    def version(self) -> str:
        return LOCAL_EDITING_SEGMENT_SELECTION_VERSION


__all__ = [
    "LOCAL_EDITING_SEGMENT_SELECTION_VERSION",
    "LOCAL_EDITING_SEMANTIC_SCORE_THRESHOLD",
    "LOCAL_EDITING_SPEECH_PARAGRAPH_VERSION",
    "LOCAL_EDITING_TIMELINE_DRAFT_VERSION",
    "MAX_LOCAL_EDITING_MATERIAL_DURATION_MS",
    "MAX_LOCAL_EDITING_SCRIPT_SENTENCES",
    "MAX_LOCAL_EDITING_SEMANTIC_MATERIALS",
    "MAX_LOCAL_EDITING_SHOT_BOUNDARIES",
    "MAX_LOCAL_EDITING_SPEECH_SEGMENTS",
    "MAX_LOCAL_EDITING_TIMELINE_CAPTION_CHARACTERS",
    "MAX_LOCAL_EDITING_TIMELINE_DURATION_MS",
    "MAX_LOCAL_EDITING_TRANSCRIPT_CHARACTERS",
    "MIN_LOCAL_EDITING_TIMELINE_DURATION_MS",
    "LocalEditingProtocolRejected",
    "LocalEditingTimelineDraft",
    "LocalEditingTimelineParagraph",
    "LocalEditingTimelineParagraphKind",
    "SegmentSelectionCandidateScore",
    "SegmentSelectionMaterial",
    "SegmentSelectionMaterialKind",
    "SegmentSelectionSentenceMatches",
    "SpeechParagraphMaterial",
    "is_canonical_local_editing_material_id",
]
