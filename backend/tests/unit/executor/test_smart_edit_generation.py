"""LE-19 T1: compose the shipped LE-13~16 boundaries into one smart draft."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

import pytest

from automation_tool.control_plane.domain import (
    DescriptionSource,
    Material,
    MaterialId,
    MaterialKind,
)
from automation_tool.executor.motion_authoring.voiceover import MAX_VOICEOVER_BYTES
from automation_tool.executor.script_segmentation import (
    ScriptSegmentationResult,
    ScriptSentence,
)
from automation_tool.executor.script_voiceover import (
    ScriptVoiceoverClip,
    ScriptVoiceoverResult,
)
from automation_tool.executor.segment_selection import (
    VerifiedDecodableInterval,
    VerifiedDecodableMaterial,
)
from automation_tool.executor.semantic_matching import (
    SemanticCandidateScore,
    SemanticMatchingResult,
    SemanticSentenceMatches,
)
from automation_tool.executor.smart_edit_generation import (
    PreparedSmartEditMaterials,
    PreparedSmartEditNarration,
    SmartEditGenerationCancelled,
    SmartEditGenerationFailure,
    SmartEditGenerationFailureCode,
    SmartEditGenerationPipeline,
    SmartEditGenerationRejected,
    SmartEditGenerationResult,
    SmartEditGenerationStage,
    SmartEditMaterialAnalysis,
    SmartEditNarrationRegistration,
    assemble_smart_edit_timeline_draft,
    generate_smart_edit_timeline_draft,
)
from automation_tool.executor.speech_paragraph_draft import NarrationMaterialBinding
from automation_tool.protocol.local_editing import (
    LocalEditingTimelineParagraphKind,
    SegmentSelectionMaterialKind,
)


def _material(
    *,
    kind: MaterialKind = MaterialKind.VIDEO,
    duration_ms: int | None = 4_000,
    has_speech: bool = False,
) -> Material:
    speech_segments = ((200, 1_200),) if has_speech else ()
    return Material.register(
        material_id=MaterialId.new(),
        kind=kind,
        duration_ms=None if kind is MaterialKind.IMAGE else duration_ms,
        width=None if kind is MaterialKind.AUDIO else 720,
        height=None if kind is MaterialKind.AUDIO else 1_280,
        content_digest=uuid4().hex + uuid4().hex,
        has_audio=kind is not MaterialKind.IMAGE,
        audio_loudness_lufs=-18.0 if kind is not MaterialKind.IMAGE else None,
        has_speech=has_speech,
        speech_segments_ms=speech_segments,
        speech_transcript="这是素材中的真实原声。" if has_speech else None,
        shot_boundaries_ms=(0, 2_000) if kind is MaterialKind.VIDEO else (),
        ai_description="发布会现场与产品特写",
        ai_tags=("发布会", "产品"),
        description_source=DescriptionSource.AI,
        described_at=datetime.now(UTC),
    )


def _evidence(material: Material) -> VerifiedDecodableMaterial:
    assert material.duration_ms is not None
    return VerifiedDecodableMaterial(
        material_id=material.material_id.uuid,
        content_digest=material.content_digest,
        intervals=(VerifiedDecodableInterval(start_ms=0, end_ms=material.duration_ms),),
    )


def _narrated_inputs(
    material: Material,
    *,
    sentences: tuple[str, ...] = ("展示产品的核心亮点。",),
    duration_ms: int = 800,
    score: int = 91,
) -> tuple[
    ScriptSegmentationResult,
    ScriptVoiceoverResult,
    SemanticMatchingResult,
    tuple[NarrationMaterialBinding, ...],
]:
    script = ScriptSegmentationResult(
        request_id="script-request",
        sentences=tuple(
            ScriptSentence(sequence=index, text=text)
            for index, text in enumerate(sentences, start=1)
        ),
    )
    voiceovers = ScriptVoiceoverResult(
        script_request_id=script.request_id,
        clips=tuple(
            ScriptVoiceoverClip(
                sentence=sentence,
                relative_path=f"voiceover/sentence-{sentence.sequence:04d}.wav",
                duration_ms=duration_ms,
                bytes_written=1_024,
            )
            for sentence in script.sentences
        ),
    )
    matches = SemanticMatchingResult(
        request_ids=("match-request",),
        sentences=tuple(
            SemanticSentenceMatches(
                sequence=sentence.sequence,
                candidates=(
                    SemanticCandidateScore(
                        material_id=material.material_id,
                        score=score,
                        qualified=score >= 60,
                    ),
                ),
            )
            for sentence in script.sentences
        ),
    )
    narration = tuple(
        NarrationMaterialBinding(
            sequence=sentence.sequence,
            narration_relative_path=f"voiceover/sentence-{sentence.sequence:04d}.wav",
            material_id=uuid4(),
            kind=SegmentSelectionMaterialKind.AUDIO,
            duration_ms=duration_ms,
        )
        for sentence in script.sentences
    )
    return script, voiceovers, matches, narration


def test_mixed_materials_use_original_speech_then_narrated_generator_chain() -> None:
    voiced = _material(has_speech=True)
    silent = _material()
    script, voiceovers, matches, narration = _narrated_inputs(silent)
    narration_material_id = narration[0].material_id

    outcome = assemble_smart_edit_timeline_draft(
        materials=(voiced, silent),
        script=script,
        voiceovers=voiceovers,
        matches=matches,
        decodable_materials=(_evidence(voiced), _evidence(silent)),
        narration_materials=narration,
    )

    assert isinstance(outcome, SmartEditGenerationResult)
    assert outcome.draft.duration_ms == 1_800
    assert tuple(paragraph.kind for paragraph in outcome.draft.paragraphs) == (
        LocalEditingTimelineParagraphKind.ORIGINAL_SPEECH,
        LocalEditingTimelineParagraphKind.NARRATED,
    )
    assert outcome.draft.paragraphs[0].visual_material_id == voiced.material_id.uuid
    assert outcome.draft.paragraphs[0].audio_material_id == voiced.material_id.uuid
    assert outcome.draft.paragraphs[1].visual_material_id == silent.material_id.uuid
    assert outcome.draft.paragraphs[1].audio_material_id == narration_material_id
    assert (
        outcome.draft.paragraphs[1].visual_source_in_ms,
        outcome.draft.paragraphs[1].visual_source_out_ms,
    ) == (0, 800)
    assert "script-request" not in repr(outcome)
    assert "match-request" not in repr(outcome)


def test_all_voiced_materials_skip_script_tts_and_matching() -> None:
    first = _material(has_speech=True)
    second = _material(has_speech=True)

    outcome = assemble_smart_edit_timeline_draft(
        materials=(first, second),
        script=None,
        voiceovers=None,
        matches=None,
        decodable_materials=(_evidence(first), _evidence(second)),
        narration_materials=(),
    )

    assert isinstance(outcome, SmartEditGenerationResult)
    assert outcome.draft.duration_ms == 2_000
    assert tuple(paragraph.visual_material_id for paragraph in outcome.draft.paragraphs) == (
        first.material_id.uuid,
        second.material_id.uuid,
    )
    assert all(
        paragraph.kind is LocalEditingTimelineParagraphKind.ORIGINAL_SPEECH
        for paragraph in outcome.draft.paragraphs
    )


@pytest.mark.parametrize(
    ("sentences", "duration_ms", "score", "expected"),
    [
        (
            ("第一句。", "第二句。"),
            800,
            91,
            SmartEditGenerationFailureCode.INSUFFICIENT_MATERIALS,
        ),
        (
            ("没有相关素材。",),
            800,
            59,
            SmartEditGenerationFailureCode.NO_RELEVANT_MATERIAL,
        ),
        (
            ("素材窗口太短。",),
            4_001,
            91,
            SmartEditGenerationFailureCode.SOURCE_TOO_SHORT,
        ),
    ],
)
def test_product_failures_return_only_one_fixed_code_without_partial_draft(
    sentences: tuple[str, ...],
    duration_ms: int,
    score: int,
    expected: SmartEditGenerationFailureCode,
) -> None:
    silent = _material()
    script, voiceovers, matches, narration = _narrated_inputs(
        silent,
        sentences=sentences,
        duration_ms=duration_ms,
        score=score,
    )

    outcome = assemble_smart_edit_timeline_draft(
        materials=(silent,),
        script=script,
        voiceovers=voiceovers,
        matches=matches,
        decodable_materials=(_evidence(silent),),
        narration_materials=narration,
    )

    assert outcome == SmartEditGenerationFailure(expected)
    assert not hasattr(outcome, "draft")
    assert str(silent.material_id) not in repr(outcome)
    assert script.request_id not in repr(outcome)


def test_original_speech_window_must_be_inside_verified_decodable_picture() -> None:
    voiced = _material(has_speech=True)
    evidence = VerifiedDecodableMaterial(
        material_id=voiced.material_id.uuid,
        content_digest=voiced.content_digest,
        intervals=(VerifiedDecodableInterval(start_ms=1_300, end_ms=4_000),),
    )

    with pytest.raises(SmartEditGenerationRejected, match=r"^smart edit generation rejected$"):
        assemble_smart_edit_timeline_draft(
            materials=(voiced,),
            script=None,
            voiceovers=None,
            matches=None,
            decodable_materials=(evidence,),
            narration_materials=(),
        )


def test_mutated_cross_boundary_values_fail_closed_without_leaking_identity() -> None:
    silent = _material()
    script, voiceovers, matches, narration = _narrated_inputs(silent)
    object.__setattr__(matches.sentences[0].candidates[0], "score", 101)

    with pytest.raises(SmartEditGenerationRejected) as captured:
        assemble_smart_edit_timeline_draft(
            materials=(silent,),
            script=script,
            voiceovers=voiceovers,
            matches=matches,
            decodable_materials=(_evidence(silent),),
            narration_materials=narration,
        )

    assert str(captured.value) == "smart edit generation rejected"
    assert captured.value.__cause__ is None
    assert str(silent.material_id) not in repr(captured.value)


def test_wrong_outer_container_is_rejected_before_it_is_normalized() -> None:
    silent = _material()
    script, voiceovers, matches, narration = _narrated_inputs(silent)

    with pytest.raises(SmartEditGenerationRejected):
        assemble_smart_edit_timeline_draft(
            materials=cast(tuple[Material, ...], [silent]),
            script=script,
            voiceovers=voiceovers,
            matches=matches,
            decodable_materials=(_evidence(silent),),
            narration_materials=narration,
        )


def test_narration_registration_rejects_unbounded_output_size() -> None:
    with pytest.raises(SmartEditGenerationRejected):
        SmartEditNarrationRegistration(
            sequence=1,
            material_id=uuid4(),
            relative_path="voiceover/sentence-0001.wav",
            duration_ms=800,
            content_digest="f" * 64,
            bytes_written=MAX_VOICEOVER_BYTES + 1,
        )


def test_result_rejects_registration_for_audio_not_used_by_the_draft() -> None:
    silent = _material()
    script, voiceovers, matches, narration = _narrated_inputs(silent)
    assembled = assemble_smart_edit_timeline_draft(
        materials=(silent,),
        script=script,
        voiceovers=voiceovers,
        matches=matches,
        decodable_materials=(_evidence(silent),),
        narration_materials=narration,
    )
    assert isinstance(assembled, SmartEditGenerationResult)

    with pytest.raises(SmartEditGenerationRejected):
        SmartEditGenerationResult(
            draft=assembled.draft,
            narration_registrations=(
                SmartEditNarrationRegistration(
                    sequence=1,
                    material_id=uuid4(),
                    relative_path="voiceover/sentence-0001.wav",
                    duration_ms=800,
                    content_digest="f" * 64,
                    bytes_written=1_024,
                ),
            ),
        )


class _RecordingPipeline:
    def __init__(self, material: Material, *, score: int = 91) -> None:
        self.material = material
        self.script, self.voiceovers, self.matches, self.narration = _narrated_inputs(
            material,
            score=score,
        )
        self.calls: list[str] = []

    def prepare(
        self,
        materials: tuple[Material, ...],
        *,
        cancellation_requested: object,
    ) -> PreparedSmartEditMaterials:
        assert callable(cancellation_requested)
        self.calls.append("prepare")
        return PreparedSmartEditMaterials(
            materials,
            (_evidence(self.material),),
            (SmartEditMaterialAnalysis.from_material(self.material),),
        )

    def segment(self, prompt: str, *, enable_thinking: bool) -> ScriptSegmentationResult:
        assert prompt == "把发布会剪成一条产品亮点短片。"
        assert enable_thinking is False
        self.calls.append("segment")
        return self.script

    def synthesize(
        self,
        script: ScriptSegmentationResult,
        *,
        cancellation_requested: object,
    ) -> ScriptVoiceoverResult:
        assert script is self.script
        assert callable(cancellation_requested)
        self.calls.append("synthesize")
        return self.voiceovers

    def match(
        self,
        script: ScriptSegmentationResult,
        materials: tuple[Material, ...],
        *,
        enable_thinking: bool,
        cancellation_requested: object,
    ) -> SemanticMatchingResult:
        assert script is self.script
        assert materials == (self.material,)
        assert enable_thinking is False
        assert callable(cancellation_requested)
        self.calls.append("match")
        return self.matches

    def bind_narration(
        self,
        voiceovers: ScriptVoiceoverResult,
        *,
        cancellation_requested: object,
    ) -> PreparedSmartEditNarration:
        assert voiceovers is self.voiceovers
        assert callable(cancellation_requested)
        self.calls.append("bind")
        return PreparedSmartEditNarration(
            bindings=self.narration,
            registrations=tuple(
                SmartEditNarrationRegistration(
                    sequence=binding.sequence,
                    material_id=binding.material_id,
                    relative_path=binding.narration_relative_path,
                    duration_ms=binding.duration_ms,
                    content_digest="f" * 64,
                    bytes_written=1_024,
                )
                for binding in self.narration
            ),
        )


def test_pipeline_reports_monotonic_real_stages_and_uses_existing_generator() -> None:
    silent = _material()
    pipeline = _RecordingPipeline(silent)
    progress: list[tuple[SmartEditGenerationStage, int]] = []

    outcome = generate_smart_edit_timeline_draft(
        cast(SmartEditGenerationPipeline, pipeline),
        prompt="把发布会剪成一条产品亮点短片。",
        materials=(silent,),
        enable_thinking=False,
        progress=lambda stage, per_mille: progress.append((stage, per_mille)),
        cancellation_requested=lambda: False,
    )

    assert isinstance(outcome, SmartEditGenerationResult)
    assert pipeline.calls == ["prepare", "segment", "synthesize", "match", "bind"]
    assert progress == [
        (SmartEditGenerationStage.PREPARING, 0),
        (SmartEditGenerationStage.ANALYZING, 100),
        (SmartEditGenerationStage.SCRIPTING, 350),
        (SmartEditGenerationStage.SYNTHESIZING, 500),
        (SmartEditGenerationStage.MATCHING, 700),
        (SmartEditGenerationStage.SELECTING, 850),
        (SmartEditGenerationStage.PUBLISHING, 950),
        (SmartEditGenerationStage.COMPLETED, 1_000),
    ]
    assert outcome.analysis_updates == (SmartEditMaterialAnalysis.from_material(silent),)
    assert outcome.narration_registrations[0].material_id == (pipeline.narration[0].material_id)


def test_pipeline_cancellation_stops_before_the_next_billable_stage() -> None:
    silent = _material()
    pipeline = _RecordingPipeline(silent)
    progress: list[int] = []

    with pytest.raises(SmartEditGenerationCancelled, match=r"^smart edit generation cancelled$"):
        generate_smart_edit_timeline_draft(
            cast(SmartEditGenerationPipeline, pipeline),
            prompt="把发布会剪成一条产品亮点短片。",
            materials=(silent,),
            enable_thinking=False,
            progress=lambda _stage, per_mille: progress.append(per_mille),
            cancellation_requested=lambda: bool(progress and progress[-1] >= 500),
        )

    assert pipeline.calls == ["prepare", "segment", "synthesize"]
    assert progress == [0, 100, 350, 500]


def test_product_failure_does_not_bind_or_claim_publishing_completion() -> None:
    silent = _material()
    pipeline = _RecordingPipeline(silent, score=59)
    progress: list[int] = []

    outcome = generate_smart_edit_timeline_draft(
        cast(SmartEditGenerationPipeline, pipeline),
        prompt="把发布会剪成一条产品亮点短片。",
        materials=(silent,),
        enable_thinking=False,
        progress=lambda _stage, per_mille: progress.append(per_mille),
        cancellation_requested=lambda: False,
    )

    assert outcome == SmartEditGenerationFailure(
        SmartEditGenerationFailureCode.NO_RELEVANT_MATERIAL
    )
    assert pipeline.calls == ["prepare", "segment", "synthesize", "match"]
    assert progress == [0, 100, 350, 500, 700, 850]


@pytest.mark.parametrize("mode", ["missing_evidence", "substituted_material"])
def test_prepared_materials_are_bound_to_the_original_request_before_model_calls(
    mode: str,
) -> None:
    silent = _material()

    class _BadPreparePipeline(_RecordingPipeline):
        def prepare(
            self,
            materials: tuple[Material, ...],
            *,
            cancellation_requested: object,
        ) -> PreparedSmartEditMaterials:
            assert callable(cancellation_requested)
            self.calls.append("prepare")
            if mode == "missing_evidence":
                return PreparedSmartEditMaterials(materials, ())
            replacement = _material()
            return PreparedSmartEditMaterials((replacement,), (_evidence(replacement),))

    pipeline = _BadPreparePipeline(silent)

    with pytest.raises(SmartEditGenerationRejected):
        generate_smart_edit_timeline_draft(
            cast(SmartEditGenerationPipeline, pipeline),
            prompt="把发布会剪成一条产品亮点短片。",
            materials=(silent,),
            enable_thinking=False,
            progress=lambda _stage, _per_mille: None,
            cancellation_requested=lambda: False,
        )

    assert pipeline.calls == ["prepare"]


def test_analysis_update_must_exactly_describe_its_prepared_material() -> None:
    silent = _material()

    class _MismatchedUpdatePipeline(_RecordingPipeline):
        def prepare(
            self,
            materials: tuple[Material, ...],
            *,
            cancellation_requested: object,
        ) -> PreparedSmartEditMaterials:
            assert callable(cancellation_requested)
            self.calls.append("prepare")
            mismatched = materials[0].with_user_description("另一份未参与匹配的描述")
            return PreparedSmartEditMaterials(
                materials,
                (_evidence(materials[0]),),
                (SmartEditMaterialAnalysis.from_material(mismatched),),
            )

    pipeline = _MismatchedUpdatePipeline(silent)

    with pytest.raises(SmartEditGenerationRejected):
        generate_smart_edit_timeline_draft(
            cast(SmartEditGenerationPipeline, pipeline),
            prompt="把发布会剪成一条产品亮点短片。",
            materials=(silent,),
            enable_thinking=False,
            progress=lambda _stage, _per_mille: None,
            cancellation_requested=lambda: False,
        )

    assert pipeline.calls == ["prepare"]


def test_all_voiced_pipeline_skips_every_billable_text_and_tts_stage() -> None:
    voiced = _material(has_speech=True)
    pipeline = _RecordingPipeline(voiced)
    progress: list[int] = []

    outcome = generate_smart_edit_timeline_draft(
        cast(SmartEditGenerationPipeline, pipeline),
        prompt="这段原声素材直接按内容成片。",
        materials=(voiced,),
        enable_thinking=False,
        progress=lambda _stage, per_mille: progress.append(per_mille),
        cancellation_requested=lambda: False,
    )

    assert isinstance(outcome, SmartEditGenerationResult)
    assert pipeline.calls == ["prepare"]
    assert progress == [0, 100, 850, 950, 1_000]


def test_request_materials_are_fully_revalidated_before_pipeline_work() -> None:
    silent = _material()
    pipeline = _RecordingPipeline(silent)
    object.__setattr__(silent, "ai_description", "含有\0控制字符")

    with pytest.raises(SmartEditGenerationRejected):
        generate_smart_edit_timeline_draft(
            cast(SmartEditGenerationPipeline, pipeline),
            prompt="把发布会剪成一条产品亮点短片。",
            materials=(silent,),
            enable_thinking=False,
            progress=lambda _stage, _per_mille: None,
            cancellation_requested=lambda: False,
        )

    assert pipeline.calls == []


def test_more_than_32_visual_materials_are_rejected_before_preparation() -> None:
    first = _material()
    pipeline = _RecordingPipeline(first)
    materials = (first, *(_material() for _ in range(32)))

    with pytest.raises(SmartEditGenerationRejected):
        generate_smart_edit_timeline_draft(
            cast(SmartEditGenerationPipeline, pipeline),
            prompt="把发布会剪成一条产品亮点短片。",
            materials=materials,
            enable_thinking=False,
            progress=lambda _stage, _per_mille: None,
            cancellation_requested=lambda: False,
        )

    assert pipeline.calls == []
