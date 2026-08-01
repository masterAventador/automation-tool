"""Compose the path-free LE-13~16 results into one smart-editing draft."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import NoReturn, Protocol, runtime_checkable
from uuid import UUID

from automation_tool.control_plane.domain.material import Material, MaterialKind
from automation_tool.executor.script_segmentation import ScriptSegmentationResult
from automation_tool.executor.script_voiceover import ScriptVoiceoverResult
from automation_tool.executor.segment_selection import (
    SegmentSelectionSlot,
    VerifiedDecodableMaterial,
    select_fitting_segments,
)
from automation_tool.executor.semantic_matching import SemanticMatchingResult
from automation_tool.executor.speech_paragraph_draft import (
    NarratedParagraphDraft,
    NarrationMaterialBinding,
    ParagraphDraftFailure,
    ParagraphDraftFailureCode,
    ResolvedSpeechAwareParagraphDraft,
    build_speech_aware_paragraph_draft,
    project_local_editing_timeline_draft,
    resolve_speech_aware_paragraph_draft,
)
from automation_tool.protocol.local_editing import (
    LocalEditingTimelineDraft,
    SegmentSelectionCandidateScore,
    SegmentSelectionMaterial,
    SegmentSelectionMaterialKind,
    SegmentSelectionSentenceMatches,
    SpeechParagraphMaterial,
)


class SmartEditGenerationRejected(RuntimeError):
    """The deterministic smart-editing assembly boundary rejected its inputs."""

    def __init__(self) -> None:
        super().__init__("smart edit generation rejected")


def _reject() -> NoReturn:
    raise SmartEditGenerationRejected from None


class SmartEditGenerationCancelled(RuntimeError):
    """The operator cooperatively stopped a generation before publication."""

    def __init__(self) -> None:
        super().__init__("smart edit generation cancelled")


class SmartEditGenerationStage(StrEnum):
    PREPARING = "preparing"
    ANALYZING = "analyzing"
    SCRIPTING = "scripting"
    SYNTHESIZING = "synthesizing"
    MATCHING = "matching"
    SELECTING = "selecting"
    PUBLISHING = "publishing"
    COMPLETED = "completed"


class SmartEditGenerationFailureCode(StrEnum):
    INSUFFICIENT_MATERIALS = "insufficient_materials"
    SOURCE_TOO_SHORT = "source_too_short"
    NO_RELEVANT_MATERIAL = "no_relevant_material"


@dataclass(frozen=True, slots=True)
class SmartEditGenerationFailure:
    code: SmartEditGenerationFailureCode

    def __post_init__(self) -> None:
        if not isinstance(self.code, SmartEditGenerationFailureCode):
            _reject()


@dataclass(frozen=True, slots=True)
class SmartEditGenerationResult:
    draft: LocalEditingTimelineDraft

    def __post_init__(self) -> None:
        if not isinstance(self.draft, LocalEditingTimelineDraft):
            _reject()


@dataclass(frozen=True, slots=True)
class PreparedSmartEditMaterials:
    materials: tuple[Material, ...]
    decodable_materials: tuple[VerifiedDecodableMaterial, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.materials, tuple)
            or not self.materials
            or not all(isinstance(material, Material) for material in self.materials)
            or len({material.material_id for material in self.materials}) != len(self.materials)
            or not isinstance(self.decodable_materials, tuple)
            or not all(
                isinstance(evidence, VerifiedDecodableMaterial)
                for evidence in self.decodable_materials
            )
        ):
            _reject()


CancellationProbe = Callable[[], bool]


@runtime_checkable
class SmartEditGenerationPipeline(Protocol):
    def prepare(
        self,
        materials: tuple[Material, ...],
        *,
        cancellation_requested: CancellationProbe,
    ) -> PreparedSmartEditMaterials: ...

    def segment(self, prompt: str, *, enable_thinking: bool) -> ScriptSegmentationResult: ...

    def synthesize(
        self,
        script: ScriptSegmentationResult,
        *,
        cancellation_requested: CancellationProbe,
    ) -> ScriptVoiceoverResult: ...

    def match(
        self,
        script: ScriptSegmentationResult,
        materials: tuple[Material, ...],
        *,
        enable_thinking: bool,
        cancellation_requested: CancellationProbe,
    ) -> SemanticMatchingResult: ...

    def bind_narration(
        self,
        voiceovers: ScriptVoiceoverResult,
        *,
        cancellation_requested: CancellationProbe,
    ) -> tuple[NarrationMaterialBinding, ...]: ...


def _cancel_if_requested(cancellation_requested: CancellationProbe) -> None:
    try:
        requested = cancellation_requested()
    except Exception:
        _reject()
    if type(requested) is not bool:
        _reject()
    if requested:
        raise SmartEditGenerationCancelled from None


def _report_progress(
    progress: Callable[[SmartEditGenerationStage, int], None],
    stage: SmartEditGenerationStage,
    per_mille: int,
) -> None:
    try:
        progress(stage, per_mille)
    except Exception:
        _reject()


def _valid_prompt(value: object) -> bool:
    return (
        type(value) is str
        and bool(value)
        and value == value.strip()
        and len(value) <= 4_000
        and all(character in {"\n", "\t"} or character.isprintable() for character in value)
    )


def generate_smart_edit_timeline_draft(
    pipeline: SmartEditGenerationPipeline,
    *,
    prompt: str,
    materials: tuple[Material, ...],
    enable_thinking: bool,
    progress: Callable[[SmartEditGenerationStage, int], None],
    cancellation_requested: CancellationProbe,
) -> SmartEditGenerationResult | SmartEditGenerationFailure:
    """Run one generation through monotonic stages and cooperative cancellation."""

    if (
        not isinstance(pipeline, SmartEditGenerationPipeline)
        or not _valid_prompt(prompt)
        or not isinstance(materials, tuple)
        or not materials
        or type(enable_thinking) is not bool
        or not callable(progress)
        or not callable(cancellation_requested)
    ):
        _reject()
    try:
        _report_progress(progress, SmartEditGenerationStage.PREPARING, 0)
        _cancel_if_requested(cancellation_requested)
        _report_progress(progress, SmartEditGenerationStage.ANALYZING, 100)
        prepared = pipeline.prepare(
            materials,
            cancellation_requested=cancellation_requested,
        )
        if not isinstance(prepared, PreparedSmartEditMaterials):
            _reject()
        _require_prepared_materials(materials, prepared)
        _cancel_if_requested(cancellation_requested)
        silent = tuple(material for material in prepared.materials if not material.has_speech)
        script: ScriptSegmentationResult | None = None
        voiceovers: ScriptVoiceoverResult | None = None
        matches: SemanticMatchingResult | None = None
        narration: tuple[NarrationMaterialBinding, ...] = ()
        if silent:
            _report_progress(progress, SmartEditGenerationStage.SCRIPTING, 350)
            script = pipeline.segment(prompt, enable_thinking=enable_thinking)
            if not isinstance(script, ScriptSegmentationResult):
                _reject()
            _cancel_if_requested(cancellation_requested)
            _report_progress(progress, SmartEditGenerationStage.SYNTHESIZING, 500)
            voiceovers = pipeline.synthesize(
                script,
                cancellation_requested=cancellation_requested,
            )
            if not isinstance(voiceovers, ScriptVoiceoverResult):
                _reject()
            _cancel_if_requested(cancellation_requested)
            _report_progress(progress, SmartEditGenerationStage.MATCHING, 700)
            matches = pipeline.match(
                script,
                silent,
                enable_thinking=enable_thinking,
                cancellation_requested=cancellation_requested,
            )
            if not isinstance(matches, SemanticMatchingResult):
                _reject()
            _cancel_if_requested(cancellation_requested)
        _report_progress(progress, SmartEditGenerationStage.SELECTING, 850)
        selected = _select_smart_edit_paragraphs(
            materials=prepared.materials,
            script=script,
            voiceovers=voiceovers,
            matches=matches,
            decodable_materials=prepared.decodable_materials,
        )
        if isinstance(selected, SmartEditGenerationFailure):
            return selected
        if voiceovers is not None:
            narration = pipeline.bind_narration(
                voiceovers,
                cancellation_requested=cancellation_requested,
            )
            if not isinstance(narration, tuple):
                _reject()
        _cancel_if_requested(cancellation_requested)
        outcome = SmartEditGenerationResult(
            project_local_editing_timeline_draft(
                selected,
                narration_materials=narration,
            )
        )
        _cancel_if_requested(cancellation_requested)
        _report_progress(progress, SmartEditGenerationStage.PUBLISHING, 950)
        _cancel_if_requested(cancellation_requested)
        _report_progress(progress, SmartEditGenerationStage.COMPLETED, 1_000)
        return outcome
    except (SmartEditGenerationCancelled, SmartEditGenerationRejected):
        raise
    except Exception:
        _reject()


def _material_kind(material: Material) -> SegmentSelectionMaterialKind:
    try:
        return SegmentSelectionMaterialKind(material.kind.value)
    except (TypeError, ValueError):
        _reject()


def _decodable_evidence(
    materials: tuple[Material, ...],
    evidence: tuple[VerifiedDecodableMaterial, ...],
) -> dict[UUID, VerifiedDecodableMaterial]:
    if (
        not isinstance(evidence, tuple)
        or not all(isinstance(item, VerifiedDecodableMaterial) for item in evidence)
        or len({item.material_id for item in evidence}) != len(evidence)
    ):
        _reject()
    by_id = {item.material_id: item for item in evidence}
    expected = {
        material.material_id.uuid for material in materials if material.kind is MaterialKind.VIDEO
    }
    if set(by_id) != expected or any(
        by_id[material_id].content_digest
        != next(
            material.content_digest
            for material in materials
            if material.material_id.uuid == material_id
        )
        for material_id in expected
    ):
        _reject()
    return by_id


def _speech_window_is_decodable(
    material: Material,
    evidence: VerifiedDecodableMaterial,
) -> bool:
    if not material.speech_segments_ms:
        return False
    start = material.speech_segments_ms[0][0]
    end = material.speech_segments_ms[-1][1]
    return any(
        interval.start_ms <= start and end <= interval.end_ms for interval in evidence.intervals
    )


def _immutable_material_facts(material: Material) -> tuple[object, ...]:
    return (
        material.material_id,
        material.kind,
        material.duration_ms,
        material.width,
        material.height,
        material.content_digest,
        material.has_audio,
        material.audio_loudness_lufs,
    )


def _require_prepared_materials(
    requested: tuple[Material, ...],
    prepared: PreparedSmartEditMaterials,
) -> None:
    if (
        len(requested) != len(prepared.materials)
        or any(
            _immutable_material_facts(before) != _immutable_material_facts(after)
            for before, after in zip(requested, prepared.materials, strict=True)
        )
        or any(material.kind is MaterialKind.AUDIO for material in prepared.materials)
    ):
        _reject()
    evidence_by_id = _decodable_evidence(
        prepared.materials,
        prepared.decodable_materials,
    )
    if any(
        material.has_speech
        and not _speech_window_is_decodable(
            material,
            evidence_by_id[material.material_id.uuid],
        )
        for material in prepared.materials
    ):
        _reject()


@dataclass(frozen=True, slots=True)
class _SilentPlanner:
    silent_ids: tuple[UUID, ...]
    script: ScriptSegmentationResult
    voiceovers: ScriptVoiceoverResult
    matches: SemanticMatchingResult
    selection_materials: tuple[SegmentSelectionMaterial, ...]
    decodable_materials: tuple[VerifiedDecodableMaterial, ...]

    def plan(self, material_ids: tuple[UUID, ...]) -> tuple[NarratedParagraphDraft, ...]:
        if material_ids != self.silent_ids:
            _reject()
        paragraphs: list[NarratedParagraphDraft] = []
        for sentence, voiceover, matching in zip(
            self.script.sentences,
            self.voiceovers.clips,
            self.matches.sentences,
            strict=True,
        ):
            projected = SegmentSelectionSentenceMatches(
                sequence=matching.sequence,
                candidates=tuple(
                    SegmentSelectionCandidateScore(
                        material_id=candidate.material_id.uuid,
                        score=candidate.score,
                        qualified=candidate.qualified,
                    )
                    for candidate in matching.candidates
                ),
            )
            selected = select_fitting_segments(
                SegmentSelectionSlot(
                    sequence=sentence.sequence,
                    duration_ms=voiceover.duration_ms,
                ),
                projected,
                self.selection_materials,
                self.decodable_materials,
            )
            paragraphs.append(
                NarratedParagraphDraft(
                    sequence=sentence.sequence,
                    caption_text=sentence.text,
                    narration_relative_path=voiceover.relative_path,
                    duration_ms=voiceover.duration_ms,
                    qualified_material_ids=tuple(
                        candidate.material_id
                        for candidate in projected.candidates
                        if candidate.qualified
                    ),
                    candidates=selected.segments,
                )
            )
        return tuple(paragraphs)


class _ForbiddenPlanner:
    def plan(self, material_ids: tuple[UUID, ...]) -> tuple[NarratedParagraphDraft, ...]:
        del material_ids
        _reject()


def _failure(value: ParagraphDraftFailure) -> SmartEditGenerationFailure:
    mapping = {
        ParagraphDraftFailureCode.INSUFFICIENT_MATERIALS: (
            SmartEditGenerationFailureCode.INSUFFICIENT_MATERIALS
        ),
        ParagraphDraftFailureCode.SOURCE_TOO_SHORT: (
            SmartEditGenerationFailureCode.SOURCE_TOO_SHORT
        ),
        ParagraphDraftFailureCode.NO_RELEVANT_MATERIAL: (
            SmartEditGenerationFailureCode.NO_RELEVANT_MATERIAL
        ),
    }
    return SmartEditGenerationFailure(mapping[value.code])


def assemble_smart_edit_timeline_draft(
    *,
    materials: tuple[Material, ...],
    script: ScriptSegmentationResult | None,
    voiceovers: ScriptVoiceoverResult | None,
    matches: SemanticMatchingResult | None,
    decodable_materials: tuple[VerifiedDecodableMaterial, ...],
    narration_materials: tuple[NarrationMaterialBinding, ...],
) -> SmartEditGenerationResult | SmartEditGenerationFailure:
    """Assemble one all-or-nothing draft from already completed production stages."""

    try:
        return _assemble_smart_edit_timeline_draft(
            materials=materials,
            script=script,
            voiceovers=voiceovers,
            matches=matches,
            decodable_materials=decodable_materials,
            narration_materials=narration_materials,
        )
    except SmartEditGenerationRejected:
        raise
    except Exception:
        _reject()


def _assemble_smart_edit_timeline_draft(
    *,
    materials: tuple[Material, ...],
    script: ScriptSegmentationResult | None,
    voiceovers: ScriptVoiceoverResult | None,
    matches: SemanticMatchingResult | None,
    decodable_materials: tuple[VerifiedDecodableMaterial, ...],
    narration_materials: tuple[NarrationMaterialBinding, ...],
) -> SmartEditGenerationResult | SmartEditGenerationFailure:

    selected = _select_smart_edit_paragraphs(
        materials=materials,
        script=script,
        voiceovers=voiceovers,
        matches=matches,
        decodable_materials=decodable_materials,
    )
    if isinstance(selected, SmartEditGenerationFailure):
        return selected
    try:
        projected = project_local_editing_timeline_draft(
            selected,
            narration_materials=narration_materials,
        )
    except Exception:
        _reject()
    return SmartEditGenerationResult(projected)


def _select_smart_edit_paragraphs(
    *,
    materials: tuple[Material, ...],
    script: ScriptSegmentationResult | None,
    voiceovers: ScriptVoiceoverResult | None,
    matches: SemanticMatchingResult | None,
    decodable_materials: tuple[VerifiedDecodableMaterial, ...],
) -> ResolvedSpeechAwareParagraphDraft | SmartEditGenerationFailure:

    if (
        not isinstance(materials, tuple)
        or not materials
        or not all(isinstance(material, Material) for material in materials)
        or len({material.material_id for material in materials}) != len(materials)
        or any(material.kind is MaterialKind.AUDIO for material in materials)
    ):
        _reject()
    evidence_by_id = _decodable_evidence(materials, decodable_materials)
    if any(
        material.has_speech
        and not _speech_window_is_decodable(
            material,
            evidence_by_id[material.material_id.uuid],
        )
        for material in materials
    ):
        _reject()
    silent = tuple(material for material in materials if not material.has_speech)
    if not silent:
        if any(value is not None for value in (script, voiceovers, matches)):
            _reject()
        planner: _SilentPlanner | _ForbiddenPlanner = _ForbiddenPlanner()
        speech_materials = tuple(
            SpeechParagraphMaterial(
                material_id=material.material_id.uuid,
                kind=_material_kind(material),
                duration_ms=material.duration_ms,
                has_speech=material.has_speech,
                speech_segments_ms=material.speech_segments_ms,
                speech_transcript=material.speech_transcript,
            )
            for material in materials
        )
        resolved = resolve_speech_aware_paragraph_draft(
            build_speech_aware_paragraph_draft(speech_materials, planner=planner)
        )
        if not isinstance(resolved, ResolvedSpeechAwareParagraphDraft):
            _reject()
        return resolved
    if (
        not isinstance(script, ScriptSegmentationResult)
        or not isinstance(voiceovers, ScriptVoiceoverResult)
        or not isinstance(matches, SemanticMatchingResult)
    ):
        _reject()
    if (
        tuple(clip.sentence for clip in voiceovers.clips) != script.sentences
        or voiceovers.script_request_id != script.request_id
        or len(matches.sentences) != len(script.sentences)
        or any(
            {candidate.material_id for candidate in sentence.candidates}
            != {material.material_id for material in silent}
            for sentence in matches.sentences
        )
    ):
        _reject()
    selection_materials = tuple(
        SegmentSelectionMaterial(
            material_id=material.material_id.uuid,
            kind=_material_kind(material),
            duration_ms=material.duration_ms,
            content_digest=material.content_digest,
            shot_boundaries_ms=material.shot_boundaries_ms,
        )
        for material in silent
    )
    planner = _SilentPlanner(
        silent_ids=tuple(material.material_id.uuid for material in silent),
        script=script,
        voiceovers=voiceovers,
        matches=matches,
        selection_materials=selection_materials,
        decodable_materials=tuple(
            evidence_by_id[material.material_id.uuid]
            for material in silent
            if material.kind is MaterialKind.VIDEO
        ),
    )
    speech_materials = tuple(
        SpeechParagraphMaterial(
            material_id=material.material_id.uuid,
            kind=_material_kind(material),
            duration_ms=material.duration_ms,
            has_speech=material.has_speech,
            speech_segments_ms=material.speech_segments_ms,
            speech_transcript=material.speech_transcript,
        )
        for material in materials
    )
    draft = build_speech_aware_paragraph_draft(speech_materials, planner=planner)
    resolved = resolve_speech_aware_paragraph_draft(draft)
    if isinstance(resolved, ParagraphDraftFailure):
        return _failure(resolved)
    if not isinstance(resolved, ResolvedSpeechAwareParagraphDraft):
        _reject()
    return resolved


__all__ = [
    "CancellationProbe",
    "PreparedSmartEditMaterials",
    "SmartEditGenerationCancelled",
    "SmartEditGenerationFailure",
    "SmartEditGenerationFailureCode",
    "SmartEditGenerationPipeline",
    "SmartEditGenerationRejected",
    "SmartEditGenerationResult",
    "SmartEditGenerationStage",
    "assemble_smart_edit_timeline_draft",
    "generate_smart_edit_timeline_draft",
]
