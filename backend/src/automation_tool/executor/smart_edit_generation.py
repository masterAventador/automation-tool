"""Compose the path-free LE-13~16 results into one smart-editing draft."""

from __future__ import annotations

import re
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import NoReturn, Protocol, runtime_checkable
from uuid import UUID

from automation_tool.control_plane.domain.material import (
    DescriptionSource,
    Material,
    MaterialId,
    MaterialKind,
)
from automation_tool.executor.motion_authoring.voiceover import MAX_VOICEOVER_BYTES
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
    LOCAL_EDITING_SEMANTIC_SCORE_THRESHOLD,
    MAX_LOCAL_EDITING_SEMANTIC_MATERIALS,
    LocalEditingTimelineDraft,
    LocalEditingTimelineParagraphKind,
    SegmentSelectionCandidateScore,
    SegmentSelectionMaterial,
    SegmentSelectionMaterialKind,
    SegmentSelectionSentenceMatches,
    SpeechParagraphMaterial,
    is_canonical_local_editing_material_id,
)

_DIGEST = re.compile(r"^[0-9a-f]{64}\Z")


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
class SmartEditMaterialAnalysis:
    """One path-free, digest-bound material analysis snapshot to persist."""

    material_id: UUID
    content_digest: str
    has_speech: bool
    speech_segments_ms: tuple[tuple[int, int], ...]
    speech_transcript: str | None
    shot_boundaries_ms: tuple[int, ...]
    ai_description: str | None
    ai_tags: tuple[str, ...]
    description_source: DescriptionSource
    described_at: datetime | None

    def __post_init__(self) -> None:
        if (
            not is_canonical_local_editing_material_id(self.material_id)
            or type(self.content_digest) is not str
            or _DIGEST.fullmatch(self.content_digest) is None
        ):
            _reject()
        material: Material | None = None
        with suppress(Exception):
            material = Material.register(
                material_id=_material_id(self.material_id),
                kind=MaterialKind.VIDEO,
                duration_ms=max(
                    1,
                    max((end for _start, end in self.speech_segments_ms), default=0),
                    max(self.shot_boundaries_ms, default=0) + 1,
                ),
                width=1,
                height=1,
                content_digest=self.content_digest,
                has_audio=self.has_speech,
                audio_loudness_lufs=None,
                has_speech=self.has_speech,
                speech_segments_ms=self.speech_segments_ms,
                speech_transcript=self.speech_transcript,
                shot_boundaries_ms=self.shot_boundaries_ms,
                ai_description=self.ai_description,
                ai_tags=self.ai_tags,
                description_source=self.description_source,
                described_at=self.described_at,
            )
        if material is None or material.duration_ms is None or material.duration_ms < 1:
            _reject()

    @classmethod
    def from_material(cls, material: Material) -> SmartEditMaterialAnalysis:
        validated = _validated_material(material)
        return cls(
            material_id=validated.material_id.uuid,
            content_digest=validated.content_digest,
            has_speech=validated.has_speech,
            speech_segments_ms=validated.speech_segments_ms,
            speech_transcript=validated.speech_transcript,
            shot_boundaries_ms=validated.shot_boundaries_ms,
            ai_description=validated.ai_description,
            ai_tags=validated.ai_tags,
            description_source=validated.description_source,
            described_at=validated.described_at,
        )


def _material_id(value: UUID) -> MaterialId:
    return MaterialId(value)


@dataclass(frozen=True, slots=True)
class SmartEditNarrationRegistration:
    """Path-free facts needed to register one generated narration file."""

    sequence: int
    material_id: UUID
    relative_path: str
    duration_ms: int
    content_digest: str
    bytes_written: int

    def __post_init__(self) -> None:
        binding: NarrationMaterialBinding | None = None
        with suppress(Exception):
            binding = NarrationMaterialBinding(
                sequence=self.sequence,
                narration_relative_path=self.relative_path,
                material_id=self.material_id,
                kind=SegmentSelectionMaterialKind.AUDIO,
                duration_ms=self.duration_ms,
            )
        if (
            binding is None
            or binding.material_id != self.material_id
            or type(self.content_digest) is not str
            or _DIGEST.fullmatch(self.content_digest) is None
            or type(self.bytes_written) is not int
            or not 1 <= self.bytes_written <= MAX_VOICEOVER_BYTES
        ):
            _reject()

    @property
    def binding(self) -> NarrationMaterialBinding:
        return NarrationMaterialBinding(
            sequence=self.sequence,
            narration_relative_path=self.relative_path,
            material_id=self.material_id,
            kind=SegmentSelectionMaterialKind.AUDIO,
            duration_ms=self.duration_ms,
        )


@dataclass(frozen=True, slots=True)
class PreparedSmartEditNarration:
    bindings: tuple[NarrationMaterialBinding, ...]
    registrations: tuple[SmartEditNarrationRegistration, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.bindings, tuple)
            or not self.bindings
            or not all(isinstance(value, NarrationMaterialBinding) for value in self.bindings)
            or not isinstance(self.registrations, tuple)
            or not self.registrations
            or not all(
                isinstance(value, SmartEditNarrationRegistration) for value in self.registrations
            )
            or len({value.sequence for value in self.registrations}) != len(self.registrations)
            or len({value.material_id for value in self.registrations}) != len(self.registrations)
            or tuple(value.binding for value in self.registrations) != self.bindings
        ):
            _reject()


@dataclass(frozen=True, slots=True)
class SmartEditGenerationResult:
    draft: LocalEditingTimelineDraft
    analysis_updates: tuple[SmartEditMaterialAnalysis, ...] = ()
    narration_registrations: tuple[SmartEditNarrationRegistration, ...] = ()

    def __post_init__(self) -> None:
        if (
            not isinstance(self.draft, LocalEditingTimelineDraft)
            or not isinstance(self.analysis_updates, tuple)
            or not all(
                isinstance(value, SmartEditMaterialAnalysis) for value in self.analysis_updates
            )
            or len({value.material_id for value in self.analysis_updates})
            != len(self.analysis_updates)
            or not isinstance(self.narration_registrations, tuple)
            or not all(
                isinstance(value, SmartEditNarrationRegistration)
                for value in self.narration_registrations
            )
            or len({value.sequence for value in self.narration_registrations})
            != len(self.narration_registrations)
            or len({value.material_id for value in self.narration_registrations})
            != len(self.narration_registrations)
            or (
                bool(self.narration_registrations)
                and tuple(value.material_id for value in self.narration_registrations)
                != tuple(
                    paragraph.audio_material_id
                    for paragraph in self.draft.paragraphs
                    if paragraph.kind is LocalEditingTimelineParagraphKind.NARRATED
                )
            )
        ):
            _reject()


@dataclass(frozen=True, slots=True)
class PreparedSmartEditMaterials:
    materials: tuple[Material, ...]
    decodable_materials: tuple[VerifiedDecodableMaterial, ...]
    analysis_updates: tuple[SmartEditMaterialAnalysis, ...] = ()

    def __post_init__(self) -> None:
        if (
            not isinstance(self.materials, tuple)
            or not self.materials
            or len(self.materials) > MAX_LOCAL_EDITING_SEMANTIC_MATERIALS
            or not all(isinstance(material, Material) for material in self.materials)
            or len({material.material_id for material in self.materials}) != len(self.materials)
            or not isinstance(self.decodable_materials, tuple)
            or not all(
                isinstance(evidence, VerifiedDecodableMaterial)
                for evidence in self.decodable_materials
            )
            or not isinstance(self.analysis_updates, tuple)
            or not all(
                isinstance(update, SmartEditMaterialAnalysis) for update in self.analysis_updates
            )
            or len({update.material_id for update in self.analysis_updates})
            != len(self.analysis_updates)
            or not {update.material_id for update in self.analysis_updates}.issubset(
                {material.material_id.uuid for material in self.materials}
            )
        ):
            _reject()
        try:
            tuple(_validated_material(material) for material in self.materials)
        except Exception:
            _reject()


CancellationProbe = Callable[[], bool]


@runtime_checkable
class SmartEditGenerationPipeline(Protocol):
    def prepare(
        self,
        materials: tuple[Material, ...],
        *,
        enable_thinking: bool,
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
    ) -> PreparedSmartEditNarration: ...


def _cancel_if_requested(cancellation_requested: CancellationProbe) -> None:
    requested: object = None
    with suppress(Exception):
        requested = cancellation_requested()
    if type(requested) is not bool:
        _reject()
    if requested:
        raise SmartEditGenerationCancelled from None


def _report_progress(
    progress: Callable[[SmartEditGenerationStage, int], None],
    stage: SmartEditGenerationStage,
    per_mille: int,
) -> None:
    failed = False
    try:
        progress(stage, per_mille)
    except Exception:
        failed = True
    if failed:
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
        or not 1 <= len(materials) <= MAX_LOCAL_EDITING_SEMANTIC_MATERIALS
        or not all(isinstance(material, Material) for material in materials)
        or type(enable_thinking) is not bool
        or not callable(progress)
        or not callable(cancellation_requested)
    ):
        _reject()
    try:
        validated_materials = tuple(_validated_material(material) for material in materials)
        _report_progress(progress, SmartEditGenerationStage.PREPARING, 0)
        _cancel_if_requested(cancellation_requested)
        _report_progress(progress, SmartEditGenerationStage.ANALYZING, 100)
        prepared = pipeline.prepare(
            validated_materials,
            enable_thinking=enable_thinking,
            cancellation_requested=cancellation_requested,
        )
        if not isinstance(prepared, PreparedSmartEditMaterials):
            _reject()
        _require_prepared_materials(validated_materials, prepared)
        _cancel_if_requested(cancellation_requested)
        silent = tuple(material for material in prepared.materials if not material.has_speech)
        script: ScriptSegmentationResult | None = None
        voiceovers: ScriptVoiceoverResult | None = None
        matches: SemanticMatchingResult | None = None
        narration: tuple[NarrationMaterialBinding, ...] = ()
        narration_registrations: tuple[SmartEditNarrationRegistration, ...] = ()
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
            prepared_narration = pipeline.bind_narration(
                voiceovers,
                cancellation_requested=cancellation_requested,
            )
            if not isinstance(prepared_narration, PreparedSmartEditNarration):
                _reject()
            narration = prepared_narration.bindings
            narration_registrations = prepared_narration.registrations
        _cancel_if_requested(cancellation_requested)
        outcome = SmartEditGenerationResult(
            draft=project_local_editing_timeline_draft(
                selected,
                narration_materials=narration,
            ),
            analysis_updates=prepared.analysis_updates,
            narration_registrations=narration_registrations,
        )
        _cancel_if_requested(cancellation_requested)
        _report_progress(progress, SmartEditGenerationStage.PUBLISHING, 950)
        _cancel_if_requested(cancellation_requested)
        _report_progress(progress, SmartEditGenerationStage.COMPLETED, 1_000)
        return outcome
    except (SmartEditGenerationCancelled, SmartEditGenerationRejected):
        raise
    except Exception:
        pass
    _reject()


def _material_kind(material: Material) -> SegmentSelectionMaterialKind:
    try:
        return SegmentSelectionMaterialKind(material.kind.value)
    except (TypeError, ValueError):
        _reject()


def _validated_material(material: Material) -> Material:
    validated: Material | None = None
    with suppress(Exception):
        validated = Material.register(
            material_id=material.material_id,
            kind=material.kind,
            duration_ms=material.duration_ms,
            width=material.width,
            height=material.height,
            content_digest=material.content_digest,
            has_audio=material.has_audio,
            audio_loudness_lufs=material.audio_loudness_lufs,
            has_speech=material.has_speech,
            speech_segments_ms=material.speech_segments_ms,
            speech_transcript=material.speech_transcript,
            shot_boundaries_ms=material.shot_boundaries_ms,
            ai_description=material.ai_description,
            ai_tags=material.ai_tags,
            description_source=material.description_source,
            described_at=material.described_at,
        )
    if validated is None:
        _reject()
    return validated


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
    try:
        tuple(_validated_material(material) for material in prepared.materials)
    except Exception:
        _reject()
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
    prepared_by_id = {material.material_id.uuid: material for material in prepared.materials}
    if any(
        update != SmartEditMaterialAnalysis.from_material(prepared_by_id[update.material_id])
        for update in prepared.analysis_updates
    ):
        _reject()
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
        reusable_static_id: UUID | None = None
        if (
            len(self.selection_materials) == 1
            and self.selection_materials[0].kind is SegmentSelectionMaterialKind.IMAGE
            and all(len(sentence.candidates) == 1 for sentence in self.matches.sentences)
            and any(sentence.candidates[0].qualified for sentence in self.matches.sentences)
        ):
            reusable_static_id = self.selection_materials[0].material_id
        paragraphs: list[NarratedParagraphDraft] = []
        for sentence, voiceover, matching in zip(
            self.script.sentences,
            self.voiceovers.clips,
            self.matches.sentences,
            strict=True,
        ):
            projected_scores = tuple(
                max(candidate.score, LOCAL_EDITING_SEMANTIC_SCORE_THRESHOLD)
                if candidate.material_id.uuid == reusable_static_id
                else candidate.score
                for candidate in matching.candidates
            )
            projected = SegmentSelectionSentenceMatches(
                sequence=matching.sequence,
                candidates=tuple(
                    SegmentSelectionCandidateScore(
                        material_id=candidate.material_id.uuid,
                        score=score,
                        qualified=score >= LOCAL_EDITING_SEMANTIC_SCORE_THRESHOLD,
                    )
                    for candidate, score in zip(
                        matching.candidates,
                        projected_scores,
                        strict=True,
                    )
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
        pass
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
    projected: LocalEditingTimelineDraft | None = None
    with suppress(Exception):
        projected = project_local_editing_timeline_draft(
            selected,
            narration_materials=narration_materials,
        )
    if projected is None:
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
    "PreparedSmartEditNarration",
    "SmartEditGenerationCancelled",
    "SmartEditGenerationFailure",
    "SmartEditGenerationFailureCode",
    "SmartEditGenerationPipeline",
    "SmartEditGenerationRejected",
    "SmartEditGenerationResult",
    "SmartEditGenerationStage",
    "SmartEditMaterialAnalysis",
    "SmartEditNarrationRegistration",
    "assemble_smart_edit_timeline_draft",
    "generate_smart_edit_timeline_draft",
]
