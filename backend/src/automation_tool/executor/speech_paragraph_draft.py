"""Partition local-editing material into original-speech and narrated drafts."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, NoReturn, Protocol, cast, runtime_checkable
from uuid import UUID

from automation_tool.executor.segment_selection import FittingMaterialSegment
from automation_tool.protocol.local_editing import (
    LOCAL_EDITING_SPEECH_PARAGRAPH_VERSION,
    MAX_LOCAL_EDITING_MATERIAL_DURATION_MS,
    MAX_LOCAL_EDITING_SCRIPT_SENTENCES,
    MAX_LOCAL_EDITING_SEMANTIC_MATERIALS,
    MAX_LOCAL_EDITING_TRANSCRIPT_CHARACTERS,
    SegmentSelectionMaterialKind,
    SpeechParagraphMaterial,
    is_canonical_local_editing_material_id,
)

_NARRATION_PATH_PATTERN: Final = re.compile(r"^voiceover/sentence-([0-9]{4})\.wav\Z")


class SpeechParagraphDraftRejected(RuntimeError):
    """The paragraph-draft boundary rejected inconsistent or unsafe values."""

    def __init__(self) -> None:
        super().__init__("speech paragraph draft rejected")


def _reject() -> NoReturn:
    raise SpeechParagraphDraftRejected from None


def _valid_text(value: object) -> bool:
    return (
        type(value) is str
        and bool(value)
        and value == value.strip()
        and len(value) <= MAX_LOCAL_EDITING_TRANSCRIPT_CHARACTERS
        and not any(
            character not in {"\n", "\t"} and unicodedata.category(character).startswith("C")
            for character in value
        )
    )


def _valid_narration_path(value: object, *, sequence: int) -> bool:
    if type(value) is not str:
        return False
    match = _NARRATION_PATH_PATTERN.fullmatch(value)
    return match is not None and int(match.group(1)) == sequence


@dataclass(frozen=True, slots=True)
class NarratedParagraphDraft:
    """Existing sentence, TTS and T2 candidates retained for later T4 choice."""

    sequence: int
    caption_text: str
    narration_relative_path: str
    duration_ms: int
    qualified_material_ids: tuple[UUID, ...]
    candidates: tuple[FittingMaterialSegment, ...]

    def __post_init__(self) -> None:
        if (
            type(self.sequence) is not int
            or not 1 <= self.sequence <= MAX_LOCAL_EDITING_SCRIPT_SENTENCES
            or not _valid_text(self.caption_text)
            or not _valid_narration_path(
                self.narration_relative_path,
                sequence=self.sequence,
            )
            or type(self.duration_ms) is not int
            or not 1 <= self.duration_ms <= MAX_LOCAL_EDITING_MATERIAL_DURATION_MS
            or not isinstance(self.qualified_material_ids, tuple)
            or len(self.qualified_material_ids) > MAX_LOCAL_EDITING_SEMANTIC_MATERIALS
            or not all(
                is_canonical_local_editing_material_id(material_id)
                for material_id in self.qualified_material_ids
            )
            or len(set(self.qualified_material_ids)) != len(self.qualified_material_ids)
            or not isinstance(self.candidates, tuple)
            or len(self.candidates) > MAX_LOCAL_EDITING_SEMANTIC_MATERIALS
            or not all(
                isinstance(candidate, FittingMaterialSegment)
                and candidate.duration_ms == self.duration_ms
                for candidate in self.candidates
            )
            or len({candidate.material_id for candidate in self.candidates}) != len(self.candidates)
            or any(
                candidate.material_id not in self.qualified_material_ids
                for candidate in self.candidates
            )
        ):
            _reject()
        qualified_index = {
            material_id: index for index, material_id in enumerate(self.qualified_material_ids)
        }
        if any(
            qualified_index[earlier.material_id] >= qualified_index[later.material_id]
            for earlier, later in zip(self.candidates, self.candidates[1:], strict=False)
        ):
            _reject()


@dataclass(frozen=True, slots=True)
class OriginalSpeechParagraphDraft:
    """One clip-relative visual/ambient/caption triple from source speech."""

    material_id: UUID
    source_in_ms: int
    source_out_ms: int
    visual_start_ms: int
    visual_duration_ms: int
    ambient_start_ms: int
    ambient_duration_ms: int
    caption_start_ms: int
    caption_duration_ms: int
    caption_text: str

    def __post_init__(self) -> None:
        if type(self.source_in_ms) is not int or type(self.source_out_ms) is not int:
            _reject()
        source_duration = self.source_out_ms - self.source_in_ms
        if (
            not is_canonical_local_editing_material_id(self.material_id)
            or self.source_in_ms < 0
            or self.source_out_ms <= self.source_in_ms
            or self.source_out_ms > MAX_LOCAL_EDITING_MATERIAL_DURATION_MS
            or any(
                type(value) is not int
                for value in (
                    self.visual_start_ms,
                    self.visual_duration_ms,
                    self.ambient_start_ms,
                    self.ambient_duration_ms,
                    self.caption_start_ms,
                    self.caption_duration_ms,
                )
            )
            or (self.visual_start_ms, self.ambient_start_ms, self.caption_start_ms) != (0, 0, 0)
            or (
                self.visual_duration_ms,
                self.ambient_duration_ms,
                self.caption_duration_ms,
            )
            != (source_duration, source_duration, source_duration)
            or not _valid_text(self.caption_text)
        ):
            _reject()

    @property
    def narration(self) -> None:
        """Original-speech paragraphs never create a competing TTS clip."""

        return None


@dataclass(frozen=True, slots=True)
class SpeechAwareParagraphDraft:
    """T3 intermediate value; T4 chooses candidates and T5 builds Timeline."""

    original_speech_paragraphs: tuple[OriginalSpeechParagraphDraft, ...]
    silent_material_ids: tuple[UUID, ...]
    narrated_paragraphs: tuple[NarratedParagraphDraft, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.original_speech_paragraphs, tuple)
            or len(self.original_speech_paragraphs) > MAX_LOCAL_EDITING_SEMANTIC_MATERIALS
            or not all(
                isinstance(paragraph, OriginalSpeechParagraphDraft)
                for paragraph in self.original_speech_paragraphs
            )
            or len({paragraph.material_id for paragraph in self.original_speech_paragraphs})
            != len(self.original_speech_paragraphs)
            or not isinstance(self.silent_material_ids, tuple)
            or len(self.silent_material_ids) > MAX_LOCAL_EDITING_SEMANTIC_MATERIALS
            or not all(
                is_canonical_local_editing_material_id(material_id)
                for material_id in self.silent_material_ids
            )
            or len(set(self.silent_material_ids)) != len(self.silent_material_ids)
            or not isinstance(self.narrated_paragraphs, tuple)
            or len(self.narrated_paragraphs) > MAX_LOCAL_EDITING_SCRIPT_SENTENCES
            or not all(
                isinstance(paragraph, NarratedParagraphDraft)
                for paragraph in self.narrated_paragraphs
            )
            or tuple(paragraph.sequence for paragraph in self.narrated_paragraphs)
            != tuple(range(1, len(self.narrated_paragraphs) + 1))
            or bool(self.silent_material_ids) is not bool(self.narrated_paragraphs)
            or any(
                material_id not in self.silent_material_ids
                for paragraph in self.narrated_paragraphs
                for material_id in paragraph.qualified_material_ids
            )
            or any(
                paragraph.material_id in self.silent_material_ids
                for paragraph in self.original_speech_paragraphs
            )
            or not (self.original_speech_paragraphs or self.narrated_paragraphs)
        ):
            _reject()

    @property
    def version(self) -> str:
        return LOCAL_EDITING_SPEECH_PARAGRAPH_VERSION


class ParagraphDraftFailureCode(StrEnum):
    INSUFFICIENT_MATERIALS = "INSUFFICIENT_MATERIALS"
    SOURCE_TOO_SHORT = "SOURCE_TOO_SHORT"
    NO_RELEVANT_MATERIAL = "NO_RELEVANT_MATERIAL"


@dataclass(frozen=True, slots=True)
class ParagraphDraftFailure:
    """A fixed, path-free product result with no partial draft attached."""

    code: ParagraphDraftFailureCode

    def __post_init__(self) -> None:
        if not isinstance(self.code, ParagraphDraftFailureCode):
            _reject()


@dataclass(frozen=True, slots=True)
class SelectedNarratedParagraphDraft:
    """One sentence resolved to one unique, fitting visual segment."""

    sequence: int
    caption_text: str
    narration_relative_path: str
    duration_ms: int
    segment: FittingMaterialSegment

    def __post_init__(self) -> None:
        if not isinstance(self.segment, FittingMaterialSegment):
            _reject()
        NarratedParagraphDraft(
            sequence=self.sequence,
            caption_text=self.caption_text,
            narration_relative_path=self.narration_relative_path,
            duration_ms=self.duration_ms,
            qualified_material_ids=(self.segment.material_id,),
            candidates=(self.segment,),
        )


@dataclass(frozen=True, slots=True)
class ResolvedSpeechAwareParagraphDraft:
    """An all-or-nothing T4 success ready for T5 Timeline construction."""

    original_speech_paragraphs: tuple[OriginalSpeechParagraphDraft, ...]
    narrated_paragraphs: tuple[SelectedNarratedParagraphDraft, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.original_speech_paragraphs, tuple)
            or not all(
                isinstance(paragraph, OriginalSpeechParagraphDraft)
                for paragraph in self.original_speech_paragraphs
            )
            or len({paragraph.material_id for paragraph in self.original_speech_paragraphs})
            != len(self.original_speech_paragraphs)
            or not isinstance(self.narrated_paragraphs, tuple)
            or not all(
                isinstance(paragraph, SelectedNarratedParagraphDraft)
                for paragraph in self.narrated_paragraphs
            )
            or tuple(paragraph.sequence for paragraph in self.narrated_paragraphs)
            != tuple(range(1, len(self.narrated_paragraphs) + 1))
            or len({paragraph.segment.material_id for paragraph in self.narrated_paragraphs})
            != len(self.narrated_paragraphs)
            or any(
                narrated.segment.material_id == original.material_id
                for narrated in self.narrated_paragraphs
                for original in self.original_speech_paragraphs
            )
            or not (self.original_speech_paragraphs or self.narrated_paragraphs)
        ):
            _reject()


@runtime_checkable
class SilentParagraphPlanner(Protocol):
    """The TTS/T1/T2 surface called only for eligible silent materials."""

    def plan(self, material_ids: tuple[UUID, ...]) -> tuple[NarratedParagraphDraft, ...]: ...


def _validated_material(material: SpeechParagraphMaterial) -> SpeechParagraphMaterial:
    try:
        return SpeechParagraphMaterial(
            material_id=material.material_id,
            kind=material.kind,
            duration_ms=material.duration_ms,
            has_speech=material.has_speech,
            speech_segments_ms=material.speech_segments_ms,
            speech_transcript=material.speech_transcript,
        )
    except Exception:
        _reject()


def _validated_narrated_paragraph(
    paragraph: NarratedParagraphDraft,
) -> NarratedParagraphDraft:
    try:
        candidates = tuple(
            FittingMaterialSegment(
                material_id=candidate.material_id,
                score=candidate.score,
                duration_ms=candidate.duration_ms,
                source_in_ms=candidate.source_in_ms,
                source_out_ms=candidate.source_out_ms,
            )
            for candidate in paragraph.candidates
        )
        return NarratedParagraphDraft(
            sequence=paragraph.sequence,
            caption_text=paragraph.caption_text,
            narration_relative_path=paragraph.narration_relative_path,
            duration_ms=paragraph.duration_ms,
            qualified_material_ids=paragraph.qualified_material_ids,
            candidates=candidates,
        )
    except Exception:
        _reject()


def _validated_original_paragraph(
    paragraph: OriginalSpeechParagraphDraft,
) -> OriginalSpeechParagraphDraft:
    try:
        return OriginalSpeechParagraphDraft(
            material_id=paragraph.material_id,
            source_in_ms=paragraph.source_in_ms,
            source_out_ms=paragraph.source_out_ms,
            visual_start_ms=paragraph.visual_start_ms,
            visual_duration_ms=paragraph.visual_duration_ms,
            ambient_start_ms=paragraph.ambient_start_ms,
            ambient_duration_ms=paragraph.ambient_duration_ms,
            caption_start_ms=paragraph.caption_start_ms,
            caption_duration_ms=paragraph.caption_duration_ms,
            caption_text=paragraph.caption_text,
        )
    except Exception:
        _reject()


def _original_speech_paragraph(
    material: SpeechParagraphMaterial,
) -> OriginalSpeechParagraphDraft:
    source_in_ms = material.speech_segments_ms[0][0]
    source_out_ms = material.speech_segments_ms[-1][1]
    duration_ms = source_out_ms - source_in_ms
    return OriginalSpeechParagraphDraft(
        material_id=material.material_id,
        source_in_ms=source_in_ms,
        source_out_ms=source_out_ms,
        visual_start_ms=0,
        visual_duration_ms=duration_ms,
        ambient_start_ms=0,
        ambient_duration_ms=duration_ms,
        caption_start_ms=0,
        caption_duration_ms=duration_ms,
        caption_text=cast(str, material.speech_transcript),
    )


def build_speech_aware_paragraph_draft(
    materials: tuple[SpeechParagraphMaterial, ...],
    *,
    planner: SilentParagraphPlanner,
) -> SpeechAwareParagraphDraft:
    """Partition materials without exposing voiced sources to sentence planning."""

    if (
        not isinstance(materials, tuple)
        or not 1 <= len(materials) <= MAX_LOCAL_EDITING_SEMANTIC_MATERIALS
        or not all(isinstance(material, SpeechParagraphMaterial) for material in materials)
        or not isinstance(planner, SilentParagraphPlanner)
    ):
        _reject()
    validated_materials = tuple(_validated_material(material) for material in materials)
    if len({material.material_id for material in validated_materials}) != len(
        validated_materials
    ) or any(
        material.kind is SegmentSelectionMaterialKind.AUDIO for material in validated_materials
    ):
        _reject()

    original = tuple(
        _original_speech_paragraph(material)
        for material in validated_materials
        if material.has_speech
    )
    silent_ids = tuple(
        material.material_id for material in validated_materials if not material.has_speech
    )
    narrated: tuple[NarratedParagraphDraft, ...] = ()
    if silent_ids:
        try:
            candidate = planner.plan(silent_ids)
            if (
                not isinstance(candidate, tuple)
                or not candidate
                or not all(isinstance(paragraph, NarratedParagraphDraft) for paragraph in candidate)
            ):
                _reject()
            narrated = tuple(_validated_narrated_paragraph(paragraph) for paragraph in candidate)
        except Exception:
            _reject()

    return SpeechAwareParagraphDraft(
        original_speech_paragraphs=original,
        silent_material_ids=silent_ids,
        narrated_paragraphs=narrated,
    )


def _validated_speech_aware_draft(
    draft: SpeechAwareParagraphDraft,
) -> SpeechAwareParagraphDraft:
    try:
        return SpeechAwareParagraphDraft(
            original_speech_paragraphs=tuple(
                _validated_original_paragraph(paragraph)
                for paragraph in draft.original_speech_paragraphs
            ),
            silent_material_ids=draft.silent_material_ids,
            narrated_paragraphs=tuple(
                _validated_narrated_paragraph(paragraph) for paragraph in draft.narrated_paragraphs
            ),
        )
    except Exception:
        _reject()


def _has_complete_assignment(
    paragraphs: tuple[NarratedParagraphDraft, ...],
    blocked: frozenset[UUID],
) -> bool:
    owner_by_material: dict[UUID, int] = {}

    def assign(paragraph_index: int, seen: set[UUID]) -> bool:
        for candidate in paragraphs[paragraph_index].candidates:
            material_id = candidate.material_id
            if material_id in blocked or material_id in seen:
                continue
            seen.add(material_id)
            owner = owner_by_material.get(material_id)
            if owner is None or assign(owner, seen):
                owner_by_material[material_id] = paragraph_index
                return True
        return False

    return all(assign(index, set()) for index in range(len(paragraphs)))


def resolve_speech_aware_paragraph_draft(
    draft: SpeechAwareParagraphDraft,
) -> ResolvedSpeechAwareParagraphDraft | ParagraphDraftFailure:
    """Resolve every silent sentence uniquely, or return one fixed failure."""

    if not isinstance(draft, SpeechAwareParagraphDraft):
        _reject()
    validated = _validated_speech_aware_draft(draft)
    if not validated.narrated_paragraphs:
        return ResolvedSpeechAwareParagraphDraft(
            original_speech_paragraphs=validated.original_speech_paragraphs,
            narrated_paragraphs=(),
        )
    if len(validated.silent_material_ids) < len(validated.narrated_paragraphs):
        return ParagraphDraftFailure(code=ParagraphDraftFailureCode.INSUFFICIENT_MATERIALS)
    for paragraph in validated.narrated_paragraphs:
        if not paragraph.qualified_material_ids:
            return ParagraphDraftFailure(code=ParagraphDraftFailureCode.NO_RELEVANT_MATERIAL)
        if not paragraph.candidates:
            return ParagraphDraftFailure(code=ParagraphDraftFailureCode.SOURCE_TOO_SHORT)

    selected: list[SelectedNarratedParagraphDraft] = []
    used: set[UUID] = set()
    for index, paragraph in enumerate(validated.narrated_paragraphs):
        chosen: FittingMaterialSegment | None = None
        for candidate in paragraph.candidates:
            if candidate.material_id in used:
                continue
            if _has_complete_assignment(
                validated.narrated_paragraphs[index + 1 :],
                frozenset({*used, candidate.material_id}),
            ):
                chosen = candidate
                break
        if chosen is None:
            return ParagraphDraftFailure(code=ParagraphDraftFailureCode.INSUFFICIENT_MATERIALS)
        used.add(chosen.material_id)
        selected.append(
            SelectedNarratedParagraphDraft(
                sequence=paragraph.sequence,
                caption_text=paragraph.caption_text,
                narration_relative_path=paragraph.narration_relative_path,
                duration_ms=paragraph.duration_ms,
                segment=chosen,
            )
        )
    return ResolvedSpeechAwareParagraphDraft(
        original_speech_paragraphs=validated.original_speech_paragraphs,
        narrated_paragraphs=tuple(selected),
    )


__all__ = [
    "NarratedParagraphDraft",
    "OriginalSpeechParagraphDraft",
    "ParagraphDraftFailure",
    "ParagraphDraftFailureCode",
    "ResolvedSpeechAwareParagraphDraft",
    "SelectedNarratedParagraphDraft",
    "SilentParagraphPlanner",
    "SpeechAwareParagraphDraft",
    "SpeechParagraphDraftRejected",
    "build_speech_aware_paragraph_draft",
    "resolve_speech_aware_paragraph_draft",
]
