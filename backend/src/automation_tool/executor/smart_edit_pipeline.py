"""Concrete private-resource pipeline for LE-19 smart editing."""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import NoReturn
from uuid import UUID

from automation_tool.control_plane.domain.material import Material, MaterialKind
from automation_tool.executor.adaptive_frame_extraction import (
    AdaptiveFrameArtifact,
    AdaptiveFrameRejection,
    extract_adaptive_frames,
)
from automation_tool.executor.material_probe import (
    MaterialFacts,
    MaterialPathRegistry,
    MediaStreamFacts,
    PackagedMediaTools,
    ProbedMaterialKind,
    approve_source,
    probe_material,
    read_content_digest,
    read_stream_facts,
    require_source_unchanged,
)
from automation_tool.executor.material_speech_analysis import (
    MaterialSpeechAnalysis,
    analyze_material_speech,
)
from automation_tool.executor.material_understanding import (
    MaterialUnderstandingAdapter,
    MaterialUnderstandingOptions,
    understand_material_artifacts,
)
from automation_tool.executor.motion_authoring.authoring_workspace import (
    AuthoringWorkspace,
)
from automation_tool.executor.motion_authoring.voiceover import VoiceoverConfig
from automation_tool.executor.script_segmentation import (
    ScriptSegmentationAdapter,
    ScriptSegmentationOptions,
    ScriptSegmentationResult,
    segment_script,
)
from automation_tool.executor.script_voiceover import (
    ScriptVoiceoverCancelled,
    ScriptVoiceoverResult,
    synthesize_script_voiceovers,
)
from automation_tool.executor.segment_selection import VerifiedDecodableMaterial
from automation_tool.executor.semantic_matching import (
    SemanticMatchingAdapter,
    SemanticMatchingOptions,
    SemanticMatchingResult,
    match_script_materials,
)
from automation_tool.executor.smart_edit_generation import (
    CancellationProbe,
    PreparedSmartEditMaterials,
    PreparedSmartEditNarration,
    SmartEditGenerationCancelled,
    SmartEditGenerationRejected,
    SmartEditMaterialAnalysis,
    SmartEditNarrationRegistration,
)
from automation_tool.executor.smart_edit_media import (
    SmartEditMediaFailureCode,
    SmartEditMediaRejected,
    verify_decodable_video,
)


def _reject() -> NoReturn:
    raise SmartEditGenerationRejected from None


def _cancel(cancellation_requested: CancellationProbe) -> None:
    requested: object = None
    with suppress(Exception):
        requested = cancellation_requested()
    if type(requested) is not bool:
        _reject()
    if requested:
        raise SmartEditGenerationCancelled from None


def _copy_material(material: Material) -> Material:
    copied: Material | None = None
    with suppress(Exception):
        copied = Material.register(
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
    if copied is None:
        _reject()
    return copied


def _facts_match(material: Material, facts: MaterialFacts) -> bool:
    return (
        facts.kind.value == material.kind.value
        and facts.duration_ms == material.duration_ms
        and facts.width == material.width
        and facts.height == material.height
        and facts.content_digest == material.content_digest
        and facts.has_audio == material.has_audio
        and facts.audio_loudness_lufs == material.audio_loudness_lufs
    )


@dataclass(frozen=True, slots=True, repr=False)
class _LocalMaterial:
    material: Material
    source: Path
    approved: os.stat_result
    facts: MaterialFacts
    decodable: VerifiedDecodableMaterial | None


@dataclass(frozen=True, slots=True, repr=False)
class _UnderstandingInput:
    workspace: Path
    artifacts: tuple[AdaptiveFrameArtifact, ...]
    duration_ms: int


@dataclass(slots=True, repr=False)
class LocalSmartEditGenerationPipeline:
    """Own paths and credentials while exposing only validated path-free facts."""

    tools: PackagedMediaTools
    registry: MaterialPathRegistry
    workspace: AuthoringWorkspace
    understanding_adapter: MaterialUnderstandingAdapter
    audible_speech_analyzer_factory: Callable[[Path, os.stat_result], object]
    script_adapter: ScriptSegmentationAdapter
    semantic_adapter: SemanticMatchingAdapter
    voiceover_config: VoiceoverConfig
    now: Callable[[], datetime]
    material_id_factory: Callable[[], UUID]
    _prepared: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.tools, PackagedMediaTools)
            or not isinstance(self.registry, MaterialPathRegistry)
            or not isinstance(self.workspace, AuthoringWorkspace)
            or not isinstance(self.voiceover_config, VoiceoverConfig)
            or not callable(self.audible_speech_analyzer_factory)
            or not callable(self.now)
            or not callable(self.material_id_factory)
        ):
            _reject()

    def __repr__(self) -> str:
        return "LocalSmartEditGenerationPipeline(<redacted>)"

    def prepare(
        self,
        materials: tuple[Material, ...],
        *,
        enable_thinking: bool,
        cancellation_requested: CancellationProbe,
    ) -> PreparedSmartEditMaterials:
        if (
            self._prepared
            or not isinstance(materials, tuple)
            or not materials
            or not all(isinstance(value, Material) for value in materials)
            or type(enable_thinking) is not bool
            or not callable(cancellation_requested)
        ):
            _reject()
        validated = tuple(_copy_material(material) for material in materials)
        local = self._prove_all_local_sources(
            validated,
            cancellation_requested=cancellation_requested,
        )
        understanding_inputs = self._prepare_understanding_inputs(
            local,
            cancellation_requested=cancellation_requested,
        )
        enriched: list[Material] = []
        updates: list[SmartEditMaterialAnalysis] = []
        try:
            for value, understanding_input in zip(
                local,
                understanding_inputs,
                strict=True,
            ):
                _cancel(cancellation_requested)
                current = self._understand_if_needed(
                    value,
                    understanding_input,
                    enable_thinking=enable_thinking,
                    cancellation_requested=cancellation_requested,
                )
                current = self._analyze_speech_if_needed(
                    value,
                    current,
                    cancellation_requested=cancellation_requested,
                )
                # Unreachable by construction, and asserted rather than skipped:
                # `_needs_understanding` is false only when a description
                # already exists -- the domain refuses a user-owned material
                # without one -- and when it is true the step above either
                # produced one or refused. A change to either rule fails loudly
                # here rather than handing selection a batch it cannot read.
                assert current.ai_description is not None
                enriched.append(current)
                if current != value.material:
                    updates.append(SmartEditMaterialAnalysis.from_material(current))
        finally:
            for understanding_input in understanding_inputs:
                if understanding_input is not None:
                    with suppress(OSError):
                        shutil.rmtree(understanding_input.workspace)
        self._prepared = True
        return PreparedSmartEditMaterials(
            materials=tuple(enriched),
            decodable_materials=tuple(
                value.decodable for value in local if value.decodable is not None
            ),
            analysis_updates=tuple(updates),
        )

    def _prove_all_local_sources(
        self,
        materials: tuple[Material, ...],
        *,
        cancellation_requested: CancellationProbe,
    ) -> tuple[_LocalMaterial, ...]:
        local: list[_LocalMaterial] = []
        for material in materials:
            _cancel(cancellation_requested)
            if material.kind is MaterialKind.AUDIO:
                _reject()
            source: Path | None = None
            checked: os.stat_result | None = None
            facts: MaterialFacts | None = None
            try:
                source, approved = self.registry.resolve(material.material_id.uuid)
                facts = probe_material(self.tools, source)
                source, checked = require_source_unchanged(source, approved)
            except Exception:
                pass
            if source is None or checked is None or facts is None:
                _reject()
            if not _facts_match(material, facts):
                _reject()
            decodable: VerifiedDecodableMaterial | None = None
            if material.kind is MaterialKind.VIDEO:
                assert material.duration_ms is not None
                try:
                    decodable = verify_decodable_video(
                        self.tools,
                        source,
                        checked,
                        material_id=material.material_id.uuid,
                        content_digest=material.content_digest,
                        duration_ms=material.duration_ms,
                        cancellation_requested=cancellation_requested,
                    )
                except SmartEditMediaRejected as error:
                    if error.code is SmartEditMediaFailureCode.CANCELLED:
                        raise SmartEditGenerationCancelled from None
                    _reject()
            local.append(_LocalMaterial(material, source, checked, facts, decodable))
        return tuple(local)

    @staticmethod
    def _needs_understanding(material: Material) -> bool:
        return material.description_source.value != "user" and (
            material.ai_description is None
            or (material.kind is MaterialKind.VIDEO and not material.shot_boundaries_ms)
        )

    def _prepare_understanding_inputs(
        self,
        local_materials: tuple[_LocalMaterial, ...],
        *,
        cancellation_requested: CancellationProbe,
    ) -> tuple[_UnderstandingInput | None, ...]:
        prepared: list[_UnderstandingInput | None] = []
        workspaces: list[Path] = []
        failed = False
        try:
            for local in local_materials:
                _cancel(cancellation_requested)
                if not self._needs_understanding(local.material):
                    prepared.append(None)
                    continue
                workspace = Path(tempfile.mkdtemp(prefix="automation-tool-smart-edit-frames-"))
                workspaces.append(workspace)
                duration_ms = (
                    local.material.duration_ms if local.material.duration_ms is not None else 1
                )
                artifacts = extract_adaptive_frames(
                    self.tools,
                    local.source,
                    local.approved,
                    workspace,
                    duration_ms=duration_ms,
                )
                if isinstance(artifacts, AdaptiveFrameRejection):
                    _reject()
                require_source_unchanged(local.source, local.approved)
                prepared.append(_UnderstandingInput(workspace, artifacts, duration_ms))
        except (SmartEditGenerationCancelled, SmartEditGenerationRejected):
            self._cleanup_understanding_workspaces(workspaces)
            raise
        except Exception:
            failed = True
        if failed:
            self._cleanup_understanding_workspaces(workspaces)
            _reject()
        return tuple(prepared)

    @staticmethod
    def _cleanup_understanding_workspaces(workspaces: list[Path]) -> None:
        for workspace in workspaces:
            with suppress(OSError):
                shutil.rmtree(workspace)

    def _understand_if_needed(
        self,
        local: _LocalMaterial,
        prepared: _UnderstandingInput | None,
        *,
        enable_thinking: bool,
        cancellation_requested: CancellationProbe,
    ) -> Material:
        material = local.material
        needs_understanding = self._needs_understanding(material)
        if not needs_understanding:
            if prepared is not None:
                _reject()
            return material
        if prepared is None:
            _reject()
        _cancel(cancellation_requested)
        candidate: Material | None = None
        failed = False
        try:
            require_source_unchanged(local.source, local.approved)
            result = understand_material_artifacts(
                self.understanding_adapter,
                output_directory=prepared.workspace,
                artifacts=prepared.artifacts,
                duration_ms=prepared.duration_ms,
                options=MaterialUnderstandingOptions(enable_thinking=enable_thinking),
                static_image=material.kind is MaterialKind.IMAGE,
            )
            require_source_unchanged(local.source, local.approved)
            _cancel(cancellation_requested)
            described_at = self.now()
            if not isinstance(described_at, datetime) or described_at.tzinfo is None:
                _reject()
            boundaries = result.shot_boundaries_ms if material.kind is MaterialKind.VIDEO else ()
            candidate = material.with_ai_understanding(
                result.description,
                result.tags,
                boundaries,
                described_at,
            )
        except (SmartEditGenerationCancelled, SmartEditGenerationRejected):
            raise
        except Exception:
            failed = True
        if failed or candidate is None:
            _reject()
        return candidate

    def _analyze_speech_if_needed(
        self,
        local: _LocalMaterial,
        material: Material,
        *,
        cancellation_requested: CancellationProbe,
    ) -> Material:
        if material.kind is not MaterialKind.VIDEO or material.has_speech:
            return material
        _cancel(cancellation_requested)
        analysis: MaterialSpeechAnalysis | None = None
        failed = False

        def build_audible_analyzer() -> object:
            return self.audible_speech_analyzer_factory(
                local.source,
                local.approved,
            )

        try:
            analysis = analyze_material_speech(
                local.facts,
                audible_analyzer_factory=build_audible_analyzer,
            )
            require_source_unchanged(local.source, local.approved)
        except Exception:
            failed = True
        if failed or analysis is None:
            _reject()
        _cancel(cancellation_requested)
        return material.with_speech_analysis(
            has_speech=analysis.has_speech,
            speech_segments_ms=analysis.speech_segments_ms,
            speech_transcript=analysis.speech_transcript,
        )

    def segment(self, prompt: str, *, enable_thinking: bool) -> ScriptSegmentationResult:
        return segment_script(
            self.script_adapter,
            prompt,
            options=ScriptSegmentationOptions(enable_thinking=enable_thinking),
        )

    def synthesize(
        self,
        script: ScriptSegmentationResult,
        *,
        cancellation_requested: CancellationProbe,
    ) -> ScriptVoiceoverResult:
        _cancel(cancellation_requested)
        try:
            result = synthesize_script_voiceovers(
                script,
                config=self.voiceover_config,
                workspace=self.workspace,
                tools=self.tools,
                cancellation_requested=cancellation_requested,
            )
        except ScriptVoiceoverCancelled:
            raise SmartEditGenerationCancelled from None
        _cancel(cancellation_requested)
        return result

    def match(
        self,
        script: ScriptSegmentationResult,
        materials: tuple[Material, ...],
        *,
        enable_thinking: bool,
        cancellation_requested: CancellationProbe,
    ) -> SemanticMatchingResult:
        _cancel(cancellation_requested)
        result = match_script_materials(
            self.semantic_adapter,
            script,
            materials,
            options=SemanticMatchingOptions(enable_thinking=enable_thinking),
        )
        _cancel(cancellation_requested)
        return result

    def bind_narration(
        self,
        voiceovers: ScriptVoiceoverResult,
        *,
        cancellation_requested: CancellationProbe,
    ) -> PreparedSmartEditNarration:
        if not isinstance(voiceovers, ScriptVoiceoverResult):
            _reject()
        registrations: list[SmartEditNarrationRegistration] = []
        for clip in voiceovers.clips:
            _cancel(cancellation_requested)
            facts: MediaStreamFacts | None = None
            digest: str | None = None
            material_id: UUID | None = None
            failed = False
            try:
                source = self.workspace.resolve(clip.relative_path)
                metadata = source.lstat()
                if (
                    source.is_symlink()
                    or not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_size != clip.bytes_written
                ):
                    _reject()
                source, approved = approve_source(source)
                facts = read_stream_facts(self.tools, source)
                digest = read_content_digest(source)
                require_source_unchanged(source, approved)
                material_id = self.material_id_factory()
            except (SmartEditGenerationCancelled, SmartEditGenerationRejected):
                raise
            except Exception:
                failed = True
            if failed:
                _reject()
            if (
                not isinstance(facts, MediaStreamFacts)
                or facts.kind is not ProbedMaterialKind.AUDIO
                or facts.duration_ms != clip.duration_ms
                or not isinstance(material_id, UUID)
                or not isinstance(digest, str)
            ):
                _reject()
            registrations.append(
                SmartEditNarrationRegistration(
                    sequence=clip.sentence.sequence,
                    material_id=material_id,
                    relative_path=clip.relative_path,
                    duration_ms=clip.duration_ms,
                    content_digest=digest,
                    bytes_written=clip.bytes_written,
                )
            )
        return PreparedSmartEditNarration(
            bindings=tuple(value.binding for value in registrations),
            registrations=tuple(registrations),
        )


__all__ = ["LocalSmartEditGenerationPipeline"]
