"""LE-19 T1: compose the shipped LE-13~16 boundaries into one smart draft."""

from __future__ import annotations

from dataclasses import fields, replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

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
    _decodable_evidence,
    _ForbiddenPlanner,
    _material_kind,
    _require_prepared_materials,
    _SilentPlanner,
    _speech_window_is_decodable,
    assemble_smart_edit_timeline_draft,
    generate_smart_edit_timeline_draft,
)
from automation_tool.executor.speech_paragraph_draft import NarrationMaterialBinding
from automation_tool.protocol.local_editing import (
    MAX_LOCAL_EDITING_SEMANTIC_MATERIALS,
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


def test_one_relevant_static_image_covers_a_low_scoring_script_sentence() -> None:
    image = _material(kind=MaterialKind.IMAGE)
    script, voiceovers, _matches, narration = _narrated_inputs(
        image,
        sentences=("彩色测试图用于校准电视。", "它也承载着模拟电视时代的记忆。"),
    )
    matches = SemanticMatchingResult(
        request_ids=("match-request",),
        sentences=(
            SemanticSentenceMatches(
                sequence=1,
                candidates=(
                    SemanticCandidateScore(
                        material_id=image.material_id,
                        score=91,
                        qualified=True,
                    ),
                ),
            ),
            SemanticSentenceMatches(
                sequence=2,
                candidates=(
                    SemanticCandidateScore(
                        material_id=image.material_id,
                        score=59,
                        qualified=False,
                    ),
                ),
            ),
        ),
    )

    outcome = assemble_smart_edit_timeline_draft(
        materials=(image,),
        script=script,
        voiceovers=voiceovers,
        matches=matches,
        decodable_materials=(),
        narration_materials=narration,
    )

    assert isinstance(outcome, SmartEditGenerationResult)
    assert tuple(paragraph.visual_material_id for paragraph in outcome.draft.paragraphs) == (
        image.material_id.uuid,
        image.material_id.uuid,
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
    def __init__(
        self,
        material: Material,
        *,
        score: int = 91,
        expected_thinking: bool = False,
    ) -> None:
        self.material = material
        self.expected_thinking = expected_thinking
        self.script, self.voiceovers, self.matches, self.narration = _narrated_inputs(
            material,
            score=score,
        )
        self.calls: list[str] = []

    def prepare(
        self,
        materials: tuple[Material, ...],
        *,
        enable_thinking: bool,
        cancellation_requested: object,
    ) -> PreparedSmartEditMaterials:
        assert enable_thinking is self.expected_thinking
        assert callable(cancellation_requested)
        self.calls.append("prepare")
        return PreparedSmartEditMaterials(
            materials,
            (_evidence(self.material),),
            (SmartEditMaterialAnalysis.from_material(self.material),),
        )

    def segment(self, prompt: str, *, enable_thinking: bool) -> ScriptSegmentationResult:
        assert prompt == "把发布会剪成一条产品亮点短片。"
        assert enable_thinking is self.expected_thinking
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
        assert enable_thinking is self.expected_thinking
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


def test_one_thinking_choice_reaches_every_model_stage() -> None:
    silent = _material()
    pipeline = _RecordingPipeline(silent, expected_thinking=True)

    outcome = generate_smart_edit_timeline_draft(
        cast(SmartEditGenerationPipeline, pipeline),
        prompt="把发布会剪成一条产品亮点短片。",
        materials=(silent,),
        enable_thinking=True,
        progress=lambda _stage, _per_mille: None,
        cancellation_requested=lambda: False,
    )

    assert isinstance(outcome, SmartEditGenerationResult)
    assert pipeline.calls == ["prepare", "segment", "synthesize", "match", "bind"]


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
            enable_thinking: bool,
            cancellation_requested: object,
        ) -> PreparedSmartEditMaterials:
            assert enable_thinking is False
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
            enable_thinking: bool,
            cancellation_requested: object,
        ) -> PreparedSmartEditMaterials:
            assert enable_thinking is False
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


class _BrokenPipeline(_RecordingPipeline):
    """A pipeline whose one named step answers with something of the wrong type."""

    def __init__(self, material: Material, *, broken_step: str) -> None:
        super().__init__(material)
        self.broken_step = broken_step

    def _answer(self, step: str, real: Any) -> Any:
        return object() if step == self.broken_step else real

    def prepare(self, materials: Any, **options: Any) -> Any:
        return self._answer("prepare", super().prepare(materials, **options))

    def segment(self, prompt: str, **options: Any) -> Any:
        return self._answer("segment", super().segment(prompt, **options))

    def synthesize(self, script: Any, **options: Any) -> Any:
        return self._answer("synthesize", super().synthesize(script, **options))

    def match(self, script: Any, materials: Any, **options: Any) -> Any:
        return self._answer("match", super().match(script, materials, **options))

    def bind_narration(self, voiceovers: Any, **options: Any) -> Any:
        return self._answer("bind", super().bind_narration(voiceovers, **options))


def _generate(pipeline: Any, material: Material, **overrides: Any) -> Any:
    arguments: dict[str, Any] = {
        "prompt": "把发布会剪成一条产品亮点短片。",
        "materials": (material,),
        "enable_thinking": False,
        "progress": lambda _stage, _per_mille: None,
        "cancellation_requested": lambda: False,
    }
    arguments.update(overrides)
    return generate_smart_edit_timeline_draft(pipeline, **arguments)


def test_a_pipeline_step_that_answers_with_the_wrong_type_is_refused() -> None:
    """Every model step is a boundary: what comes back is checked, not assumed."""
    for step in ("prepare", "segment", "synthesize", "match", "bind"):
        material = _material()
        with pytest.raises(SmartEditGenerationRejected):
            _generate(_BrokenPipeline(material, broken_step=step), material)


def test_a_pipeline_that_raises_anything_else_still_fails_closed() -> None:
    """Nothing from a model adapter may surface as its own exception type."""
    material = _material()

    class _ExplodingPipeline(_RecordingPipeline):
        def prepare(self, materials: Any, **options: Any) -> Any:
            raise RuntimeError("adapter defect")

    with pytest.raises(SmartEditGenerationRejected):
        _generate(_ExplodingPipeline(material), material)


def test_generation_refuses_a_request_it_cannot_trust() -> None:
    material = _material()
    pipeline = _RecordingPipeline(material)

    cases: list[tuple[str, dict[str, Any]]] = [
        ("a prompt that is empty", {"prompt": ""}),
        ("a prompt with untrimmed space", {"prompt": " 剪一条片子。"}),
        ("a prompt past the ceiling", {"prompt": "字" * 4_001}),
        ("a prompt with a control character", {"prompt": "剪\x00一条"}),
        ("a prompt that is not text", {"prompt": cast(Any, 1)}),
        ("materials that are not a tuple", {"materials": cast(Any, [material])}),
        ("no materials at all", {"materials": ()}),
        ("something that is not a material", {"materials": (object(),)}),
        ("a thinking flag that is not a bool", {"enable_thinking": cast(Any, 1)}),
        ("a progress sink that cannot be called", {"progress": cast(Any, None)}),
        ("a cancellation probe that cannot be called", {"cancellation_requested": cast(Any, None)}),
    ]
    for label, overrides in cases:
        with pytest.raises(SmartEditGenerationRejected):
            _generate(pipeline, material, **overrides)
        assert label

    with pytest.raises(SmartEditGenerationRejected):
        _generate(cast(Any, object()), material)


def test_a_cancellation_probe_that_cannot_be_trusted_is_not_read_as_carry_on() -> None:
    material = _material()

    for label, probe in [
        ("the probe raises", lambda: (_ for _ in ()).throw(RuntimeError("defect"))),
        ("the probe answers with an int", lambda: cast(bool, 1)),
        ("the probe answers with nothing", lambda: cast(bool, None)),
    ]:
        with pytest.raises(SmartEditGenerationRejected):
            _generate(_RecordingPipeline(material), material, cancellation_requested=probe)
        assert label


def test_a_progress_sink_that_raises_stops_the_generation() -> None:
    """Progress is what the caller bills and shows; a sink that fails is not ignored."""
    material = _material()

    def refuse(_stage: SmartEditGenerationStage, _per_mille: int) -> None:
        raise RuntimeError("sink defect")

    with pytest.raises(SmartEditGenerationRejected):
        _generate(_RecordingPipeline(material), material, progress=refuse)


def test_a_failure_must_name_a_reason_from_the_closed_set() -> None:
    with pytest.raises(SmartEditGenerationRejected):
        SmartEditGenerationFailure(cast(Any, "source_too_short"))

    assert (
        SmartEditGenerationFailure(SmartEditGenerationFailureCode.SOURCE_TOO_SHORT).code
        is SmartEditGenerationFailureCode.SOURCE_TOO_SHORT
    )


def _analysis(material: Material, **overrides: Any) -> SmartEditMaterialAnalysis:
    return replace(SmartEditMaterialAnalysis.from_material(material), **overrides)


def test_an_analysis_snapshot_must_describe_a_material_that_could_exist() -> None:
    """It is persisted as fact, so a shape no probe could produce is refused."""
    material = _material(has_speech=True)

    cases: list[tuple[str, dict[str, Any]]] = [
        ("an identifier that is not canonical", {"material_id": UUID(int=0)}),
        ("a digest that is not text", {"content_digest": cast(Any, b"a" * 64)}),
        ("a digest of the wrong shape", {"content_digest": "not a digest"}),
        ("speech windows that run backwards", {"speech_segments_ms": ((1_200, 200),)}),
        ("shot boundaries that are not a tuple", {"shot_boundaries_ms": cast(Any, [0, 1])}),
        ("a transcript that is not text", {"speech_transcript": cast(Any, 1)}),
        ("tags that are not a tuple", {"ai_tags": cast(Any, ["发布会"])}),
        ("a description source outside the set", {"description_source": cast(Any, "ai")}),
    ]
    for label, overrides in cases:
        with pytest.raises(SmartEditGenerationRejected):
            _analysis(material, **overrides)
        assert label


def _narration_batch(material: Material, sentences: tuple[str, ...]) -> PreparedSmartEditNarration:
    pipeline = _RecordingPipeline(material)
    pipeline.script, pipeline.voiceovers, pipeline.matches, pipeline.narration = _narrated_inputs(
        material, sentences=sentences
    )
    return pipeline.bind_narration(pipeline.voiceovers, cancellation_requested=lambda: False)


def test_prepared_narration_must_be_one_batch_the_bindings_agree_with() -> None:
    material = _material()
    prepared = _narration_batch(material, ("展示产品的核心亮点。", "再讲一句收尾。"))
    first_binding, second_binding = prepared.bindings
    first_registration, second_registration = prepared.registrations

    cases: list[tuple[str, tuple[Any, Any]]] = [
        ("bindings that are not a tuple", (list(prepared.bindings), prepared.registrations)),
        ("no bindings at all", ((), prepared.registrations)),
        ("a binding of the wrong type", ((object(),), prepared.registrations)),
        ("registrations that are not a tuple", (prepared.bindings, list(prepared.registrations))),
        ("no registrations at all", (prepared.bindings, ())),
        ("a registration of the wrong type", (prepared.bindings, (object(),))),
        (
            "the same sentence registered twice",
            ((first_binding, first_binding), (first_registration, first_registration)),
        ),
        (
            "two sentences pointing at one audio file",
            (
                (first_binding, replace(second_binding, material_id=first_binding.material_id)),
                (
                    first_registration,
                    replace(second_registration, material_id=first_binding.material_id),
                ),
            ),
        ),
        (
            "registrations that do not describe these bindings",
            ((first_binding,), (second_registration,)),
        ),
    ]
    for label, (bindings, registrations) in cases:
        with pytest.raises(SmartEditGenerationRejected):
            PreparedSmartEditNarration(bindings=bindings, registrations=registrations)
        assert label

    assert PreparedSmartEditNarration(prepared.bindings, prepared.registrations).bindings


def test_prepared_materials_must_be_a_coherent_verified_batch() -> None:
    material = _material()
    evidence = _evidence(material)
    analysis = SmartEditMaterialAnalysis.from_material(material)
    other = _material()

    cases: list[tuple[str, tuple[Any, Any, Any]]] = [
        ("materials that are not a tuple", ([material], (evidence,), (analysis,))),
        ("no materials at all", ((), (evidence,), (analysis,))),
        ("something that is not a material", ((object(),), (evidence,), (analysis,))),
        ("the same material twice", ((material, material), (evidence,), (analysis,))),
        ("evidence that is not a tuple", ((material,), [evidence], (analysis,))),
        ("evidence of the wrong type", ((material,), (object(),), (analysis,))),
        ("analysis that is not a tuple", ((material,), (evidence,), [analysis])),
        ("analysis of the wrong type", ((material,), (evidence,), (object(),))),
        ("the same analysis twice", ((material,), (evidence,), (analysis, analysis))),
        (
            "analysis for a material that is not here",
            (
                (material,),
                (evidence,),
                (SmartEditMaterialAnalysis.from_material(other),),
            ),
        ),
    ]
    for label, arguments in cases:
        with pytest.raises(SmartEditGenerationRejected):
            PreparedSmartEditMaterials(*arguments)
        assert label

    with pytest.raises(SmartEditGenerationRejected):
        PreparedSmartEditMaterials(
            tuple(_material() for _index in range(MAX_LOCAL_EDITING_SEMANTIC_MATERIALS + 1)),
            (),
            (),
        )


def _corrupt(material: Material, **overrides: Any) -> Material:
    """A material the domain factory would refuse, assembled past it on purpose.

    Everything downstream re-validates rather than trusting what it is handed,
    and the only way to test that is to hand it something the factory would not
    have produced -- which means bypassing the constructor, since that is the
    very check being proved insufficient on its own.
    """
    broken = Material.__new__(Material)
    for field in fields(Material):
        object.__setattr__(
            broken,
            field.name,
            overrides.get(field.name, getattr(material, field.name)),
        )
    return broken


def test_a_material_that_no_longer_revalidates_is_refused_where_it_is_used() -> None:
    material = _material()
    broken = _corrupt(material, duration_ms=0)

    with pytest.raises(SmartEditGenerationRejected):
        PreparedSmartEditMaterials((broken,), (), ())

    with pytest.raises(SmartEditGenerationRejected):
        _require_prepared_materials(
            (material,),
            cast(
                PreparedSmartEditMaterials,
                SimpleNamespace(
                    materials=(broken,),
                    decodable_materials=(),
                    analysis_updates=(),
                ),
            ),
        )


def test_a_material_kind_the_selector_has_no_word_for_is_refused() -> None:
    with pytest.raises(SmartEditGenerationRejected):
        _material_kind(_corrupt(_material(), kind=cast(Any, SimpleNamespace(value="hologram"))))


def test_decodable_evidence_must_cover_exactly_the_video_it_claims() -> None:
    material = _material()
    evidence = _evidence(material)
    other = _material()

    cases: list[tuple[str, tuple[Any, Any]]] = [
        ("evidence that is not a tuple", ((material,), [evidence])),
        ("evidence of the wrong type", ((material,), (object(),))),
        ("the same material twice", ((material,), (evidence, evidence))),
        ("no evidence for the video", ((material,), ())),
        ("evidence for something not asked about", ((material,), (evidence, _evidence(other)))),
        (
            "evidence bound to a different digest",
            ((material,), (replace(evidence, content_digest="a" * 64),)),
        ),
    ]
    for label, (materials, values) in cases:
        with pytest.raises(SmartEditGenerationRejected):
            _decodable_evidence(materials, values)
        assert label


def test_a_material_claiming_speech_with_no_window_has_nothing_to_verify() -> None:
    material = _material(has_speech=True)

    without_window = _corrupt(material, speech_segments_ms=())

    assert _speech_window_is_decodable(without_window, _evidence(material)) is False


def test_original_speech_outside_the_verified_picture_is_refused_when_prepared() -> None:
    material = _material(has_speech=True)
    assert material.duration_ms is not None
    short = replace(
        _evidence(material),
        intervals=(VerifiedDecodableInterval(start_ms=0, end_ms=100),),
    )

    with pytest.raises(SmartEditGenerationRejected):
        _require_prepared_materials(
            (material,),
            PreparedSmartEditMaterials((material,), (short,), ()),
        )


def test_the_narrated_planner_only_plans_for_the_silent_material_it_was_built_for() -> None:
    material = _material()
    script, voiceovers, matches, _narration = _narrated_inputs(material)
    planner = _SilentPlanner(
        silent_ids=(material.material_id.uuid,),
        script=script,
        voiceovers=voiceovers,
        matches=matches,
        selection_materials=(),
        decodable_materials=(_evidence(material),),
    )

    with pytest.raises(SmartEditGenerationRejected):
        planner.plan((uuid4(),))


def test_the_all_voiced_path_refuses_to_plan_a_narrated_paragraph_at_all() -> None:
    """Narration exists only for silent material; being asked here is a defect."""
    with pytest.raises(SmartEditGenerationRejected):
        _ForbiddenPlanner().plan(())


def _assemble(materials: tuple[Material, ...], **overrides: Any) -> Any:
    arguments: dict[str, Any] = {
        "materials": materials,
        "script": None,
        "voiceovers": None,
        "matches": None,
        "decodable_materials": tuple(_evidence(material) for material in materials),
        "narration_materials": (),
    }
    arguments.update(overrides)
    return assemble_smart_edit_timeline_draft(**arguments)


def test_narration_offered_to_an_all_voiced_draft_is_refused() -> None:
    """Narration only exists for silent material, so a draft with none cannot take it."""
    voiced = _material(has_speech=True)
    _script, _voiceovers, _matches, narration = _narrated_inputs(voiced)

    with pytest.raises(SmartEditGenerationRejected):
        _assemble((voiced,), narration_materials=narration)


def test_an_all_voiced_draft_refuses_model_output_it_never_asked_for() -> None:
    """Nothing was scripted or synthesised here; being handed some means a defect."""
    voiced = _material(has_speech=True)
    script, voiceovers, matches, _narration = _narrated_inputs(voiced)

    cases: list[tuple[str, dict[str, Any]]] = [
        ("a script", {"script": script}),
        ("voiceovers", {"voiceovers": voiceovers}),
        ("matches", {"matches": matches}),
    ]
    for label, overrides in cases:
        with pytest.raises(SmartEditGenerationRejected):
            _assemble((voiced,), **overrides)
        assert label


def test_a_silent_draft_refuses_missing_or_mistyped_model_output() -> None:
    silent = _material()
    script, voiceovers, matches, _narration = _narrated_inputs(silent)
    complete: dict[str, Any] = {"script": script, "voiceovers": voiceovers, "matches": matches}

    for label, missing in [
        ("no script", "script"),
        ("no voiceovers", "voiceovers"),
        ("no matches", "matches"),
    ]:
        with pytest.raises(SmartEditGenerationRejected):
            _assemble((silent,), **{**complete, missing: None})
        assert label

    for label, wrong in [
        ("a script of the wrong type", "script"),
        ("voiceovers of the wrong type", "voiceovers"),
        ("matches of the wrong type", "matches"),
    ]:
        with pytest.raises(SmartEditGenerationRejected):
            _assemble((silent,), **{**complete, wrong: object()})
        assert label


def test_a_silent_draft_refuses_model_output_that_does_not_agree_with_itself() -> None:
    """Three separate model calls; nothing but this checks they describe one job."""
    silent = _material()
    other = _material()
    script, voiceovers, matches, _narration = _narrated_inputs(silent)
    two_sentence_script, _v, _m, _n = _narrated_inputs(silent, sentences=("一。", "二。"))
    _s, _v2, other_matches, _n2 = _narrated_inputs(other)

    cases: list[tuple[str, dict[str, Any]]] = [
        (
            "voiceovers made from a different script",
            {"voiceovers": replace(voiceovers, script_request_id="another-request")},
        ),
        (
            "voiceovers covering different sentences",
            {"script": two_sentence_script},
        ),
        (
            "matches for a material that is not in this job",
            {"matches": other_matches},
        ),
    ]
    for label, override in cases:
        arguments: dict[str, Any] = {
            "script": script,
            "voiceovers": voiceovers,
            "matches": matches,
        }
        arguments.update(override)
        with pytest.raises(SmartEditGenerationRejected):
            _assemble((silent,), **arguments)
        assert label


def test_a_resolved_draft_of_the_wrong_type_is_refused_on_both_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The resolver is another module; what it returns is checked, not assumed."""
    monkeypatch.setattr(
        "automation_tool.executor.smart_edit_generation.resolve_speech_aware_paragraph_draft",
        lambda _draft: object(),
    )
    voiced = _material(has_speech=True)
    silent = _material()
    script, voiceovers, matches, narration = _narrated_inputs(silent)

    with pytest.raises(SmartEditGenerationRejected):
        _assemble((voiced,))

    with pytest.raises(SmartEditGenerationRejected):
        _assemble(
            (silent,),
            script=script,
            voiceovers=voiceovers,
            matches=matches,
            narration_materials=narration,
        )
