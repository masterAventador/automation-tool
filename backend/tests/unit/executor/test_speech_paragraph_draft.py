"""LE-16 T3: arrange voiced materials as self-narrated paragraph drafts."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest

from automation_tool.executor.segment_selection import FittingMaterialSegment
from automation_tool.executor.speech_paragraph_draft import (
    NarratedParagraphDraft,
    OriginalSpeechParagraphDraft,
    SpeechAwareParagraphDraft,
    SpeechParagraphDraftRejected,
    build_speech_aware_paragraph_draft,
)
from automation_tool.protocol.local_editing import (
    LOCAL_EDITING_SPEECH_PARAGRAPH_VERSION,
    LocalEditingProtocolRejected,
    SegmentSelectionMaterialKind,
    SpeechParagraphMaterial,
)


def _material(
    *,
    material_id: UUID | None = None,
    kind: SegmentSelectionMaterialKind = SegmentSelectionMaterialKind.VIDEO,
    duration_ms: int = 10_000,
    has_speech: bool = False,
    speech_segments_ms: tuple[tuple[int, int], ...] | None = None,
    transcript: str | None = None,
) -> SpeechParagraphMaterial:
    if speech_segments_ms is None:
        speech_segments_ms = ((1_000, 3_000),) if has_speech else ()
    if transcript is None and has_speech:
        transcript = "完整原声转写"
    return SpeechParagraphMaterial(
        material_id=material_id or uuid4(),
        kind=kind,
        duration_ms=None if kind is SegmentSelectionMaterialKind.IMAGE else duration_ms,
        has_speech=has_speech,
        speech_segments_ms=speech_segments_ms,
        speech_transcript=transcript,
    )


def _narrated(
    material: SpeechParagraphMaterial,
    *,
    sequence: int = 1,
    text: str = "无人声句子",
    duration_ms: int = 1_200,
) -> NarratedParagraphDraft:
    return NarratedParagraphDraft(
        sequence=sequence,
        caption_text=text,
        narration_relative_path=f"voiceover/sentence-{sequence:04d}.wav",
        duration_ms=duration_ms,
        candidates=(
            FittingMaterialSegment(
                material_id=material.material_id,
                score=90,
                duration_ms=duration_ms,
                source_in_ms=(None if material.kind is SegmentSelectionMaterialKind.IMAGE else 100),
                source_out_ms=(
                    None
                    if material.kind is SegmentSelectionMaterialKind.IMAGE
                    else 100 + duration_ms
                ),
            ),
        ),
    )


class _RecordingPlanner:
    def __init__(self, result: tuple[NarratedParagraphDraft, ...]) -> None:
        self.result = result
        self.calls: list[tuple[UUID, ...]] = []

    def plan(self, material_ids: tuple[UUID, ...]) -> tuple[NarratedParagraphDraft, ...]:
        self.calls.append(material_ids)
        return self.result


class _ForbiddenPlanner:
    def plan(self, material_ids: tuple[UUID, ...]) -> tuple[NarratedParagraphDraft, ...]:
        raise AssertionError(f"silent planner must not run: {material_ids!r}")


def test_mixed_input_only_sends_silent_materials_to_sentence_planner() -> None:
    voiced = _material(has_speech=True)
    silent = _material()
    narrated = _narrated(silent)
    planner = _RecordingPlanner((narrated,))

    result = build_speech_aware_paragraph_draft((voiced, silent), planner=planner)

    assert planner.calls == [(silent.material_id,)]
    assert result.narrated_paragraphs == (narrated,)
    assert {
        candidate.material_id
        for paragraph in result.narrated_paragraphs
        for candidate in paragraph.candidates
    } == {silent.material_id}
    assert tuple(paragraph.material_id for paragraph in result.original_speech_paragraphs) == (
        voiced.material_id,
    )


def test_voiced_paragraphs_follow_material_input_order_not_external_scores() -> None:
    first = _material(
        has_speech=True,
        speech_segments_ms=((100, 800),),
        transcript="第一条",
    )
    second = _material(
        has_speech=True,
        speech_segments_ms=((50, 500),),
        transcript="第二条",
    )

    result = build_speech_aware_paragraph_draft(
        (first, second),
        planner=_ForbiddenPlanner(),
    )

    assert tuple(paragraph.material_id for paragraph in result.original_speech_paragraphs) == (
        first.material_id,
        second.material_id,
    )


def test_original_speech_window_and_all_three_clip_timings_are_identical() -> None:
    voiced = _material(
        has_speech=True,
        speech_segments_ms=((500, 1_200), (1_500, 2_700), (3_000, 4_100)),
        transcript="第一段\n第二段\t第三段",
    )

    result = build_speech_aware_paragraph_draft((voiced,), planner=_ForbiddenPlanner())

    paragraph = result.original_speech_paragraphs[0]
    assert paragraph == OriginalSpeechParagraphDraft(
        material_id=voiced.material_id,
        source_in_ms=500,
        source_out_ms=4_100,
        visual_start_ms=0,
        visual_duration_ms=3_600,
        ambient_start_ms=0,
        ambient_duration_ms=3_600,
        caption_start_ms=0,
        caption_duration_ms=3_600,
        caption_text="第一段\n第二段\t第三段",
    )
    assert paragraph.narration is None


def test_all_voiced_materials_skip_sentence_tts_and_matching_planner() -> None:
    first = _material(has_speech=True, transcript="原声一")
    second = _material(has_speech=True, transcript="原声二")

    result = build_speech_aware_paragraph_draft(
        (first, second),
        planner=_ForbiddenPlanner(),
    )

    assert result.narrated_paragraphs == ()
    assert tuple(paragraph.caption_text for paragraph in result.original_speech_paragraphs) == (
        "原声一",
        "原声二",
    )


def test_all_silent_materials_preserve_existing_narration_and_candidates() -> None:
    video = _material()
    image = _material(kind=SegmentSelectionMaterialKind.IMAGE)
    narrated = (
        _narrated(video, sequence=1),
        _narrated(image, sequence=2, text="第二句"),
    )
    planner = _RecordingPlanner(narrated)

    result = build_speech_aware_paragraph_draft((video, image), planner=planner)

    assert result == SpeechAwareParagraphDraft(
        original_speech_paragraphs=(),
        narrated_paragraphs=narrated,
    )
    assert planner.calls == [(video.material_id, image.material_id)]


def test_speech_paragraph_protocol_is_versioned_and_tracks_domain_limits() -> None:
    from automation_tool.control_plane.domain.material import (
        MAX_MATERIAL_DURATION_MS,
        MAX_SPEECH_SEGMENTS,
        MAX_TRANSCRIPT_CHARACTERS,
    )
    from automation_tool.protocol.local_editing import (
        MAX_LOCAL_EDITING_SPEECH_SEGMENTS,
        MAX_LOCAL_EDITING_TRANSCRIPT_CHARACTERS,
    )

    material = _material(has_speech=True)

    assert material.version == LOCAL_EDITING_SPEECH_PARAGRAPH_VERSION
    assert material.duration_ms is not None
    assert material.duration_ms <= MAX_MATERIAL_DURATION_MS
    assert MAX_LOCAL_EDITING_SPEECH_SEGMENTS == MAX_SPEECH_SEGMENTS
    assert MAX_LOCAL_EDITING_TRANSCRIPT_CHARACTERS == MAX_TRANSCRIPT_CHARACTERS


@pytest.mark.parametrize(
    "construct",
    [
        lambda: _material(material_id=UUID(int=0)),
        lambda: _material(
            kind=SegmentSelectionMaterialKind.IMAGE,
            has_speech=True,
        ),
        lambda: _material(
            kind=SegmentSelectionMaterialKind.AUDIO,
            has_speech=True,
        ),
        lambda: _material(
            has_speech=True,
            speech_segments_ms=((1_000, 2_000), (1_999, 3_000)),
        ),
        lambda: _material(
            has_speech=True,
            speech_segments_ms=(cast(tuple[int, int], (1_000, "2_000")),),
        ),
        lambda: _material(
            has_speech=True,
            speech_segments_ms=((1_000, 11_000),),
        ),
        lambda: _material(
            has_speech=True,
            speech_segments_ms=(),
        ),
        lambda: _material(
            has_speech=True,
            transcript="",
        ),
        lambda: _material(
            has_speech=True,
            transcript="不可见控制符\0路径 /Users/private/movie.mp4",
        ),
        lambda: _material(
            has_speech=False,
            transcript="不应存在的转写 /Users/private/movie.mp4",
        ),
        lambda: SpeechParagraphMaterial(
            material_id=uuid4(),
            kind=cast(SegmentSelectionMaterialKind, "video"),
            duration_ms=1_000,
            has_speech=False,
            speech_segments_ms=(),
            speech_transcript=None,
        ),
    ],
)
def test_invalid_public_speech_material_values_fail_closed(
    construct: Callable[[], object],
) -> None:
    with pytest.raises(LocalEditingProtocolRejected) as caught:
        construct()

    assert str(caught.value) == "local editing protocol value is invalid"
    assert caught.value.__cause__ is None


def test_duplicate_material_identity_is_rejected_without_leaking_identity() -> None:
    material_id = uuid4()
    first = _material(material_id=material_id)
    second = _material(material_id=material_id)

    with pytest.raises(SpeechParagraphDraftRejected) as caught:
        build_speech_aware_paragraph_draft(
            (first, second),
            planner=_RecordingPlanner(()),
        )

    assert str(caught.value) == "speech paragraph draft rejected"
    assert str(material_id) not in str(caught.value)
    assert caught.value.__cause__ is None


@pytest.mark.parametrize(
    "relative_path",
    [
        None,
        "",
        " voiceover/sentence.wav",
        "voiceover\\sentence.wav",
        "voiceover/\0sentence.wav",
        "/voiceover/sentence.wav",
        "voiceover/../sentence.wav",
        "voiceover/sentence-0002.wav",
    ],
)
def test_narration_path_is_a_canonical_safe_relative_path(relative_path: object) -> None:
    material = _material()
    candidate = _narrated(material).candidates

    with pytest.raises(SpeechParagraphDraftRejected):
        NarratedParagraphDraft(
            sequence=1,
            caption_text="句子",
            narration_relative_path=cast(str, relative_path),
            duration_ms=1_200,
            candidates=candidate,
        )


def test_public_draft_values_fail_closed_instead_of_leaking_type_errors() -> None:
    material = _material()
    candidate = _narrated(material).candidates[0]
    valid_original = OriginalSpeechParagraphDraft(
        material_id=uuid4(),
        source_in_ms=100,
        source_out_ms=500,
        visual_start_ms=0,
        visual_duration_ms=400,
        ambient_start_ms=0,
        ambient_duration_ms=400,
        caption_start_ms=0,
        caption_duration_ms=400,
        caption_text="原声",
    )
    constructors: tuple[Callable[[], object], ...] = (
        lambda: NarratedParagraphDraft(
            sequence=1,
            caption_text="句子",
            narration_relative_path="voiceover/sentence.wav",
            duration_ms=1_201,
            candidates=(candidate,),
        ),
        lambda: NarratedParagraphDraft(
            sequence=1,
            caption_text="句子",
            narration_relative_path="voiceover/sentence.wav",
            duration_ms=1_200,
            candidates=(candidate, candidate),
        ),
        lambda: OriginalSpeechParagraphDraft(
            material_id=uuid4(),
            source_in_ms=cast(int, "private/source.mp4"),
            source_out_ms=500,
            visual_start_ms=0,
            visual_duration_ms=400,
            ambient_start_ms=0,
            ambient_duration_ms=400,
            caption_start_ms=0,
            caption_duration_ms=400,
            caption_text="原声",
        ),
        lambda: OriginalSpeechParagraphDraft(
            material_id=uuid4(),
            source_in_ms=100,
            source_out_ms=500,
            visual_start_ms=0,
            visual_duration_ms=400,
            ambient_start_ms=1,
            ambient_duration_ms=400,
            caption_start_ms=0,
            caption_duration_ms=400,
            caption_text="原声",
        ),
        lambda: OriginalSpeechParagraphDraft(
            material_id=uuid4(),
            source_in_ms=100,
            source_out_ms=500,
            visual_start_ms=0,
            visual_duration_ms=400,
            ambient_start_ms=0,
            ambient_duration_ms=399,
            caption_start_ms=0,
            caption_duration_ms=400,
            caption_text="原声",
        ),
        lambda: SpeechAwareParagraphDraft(
            original_speech_paragraphs=(),
            narrated_paragraphs=(),
        ),
        lambda: SpeechAwareParagraphDraft(
            original_speech_paragraphs=(valid_original,),
            narrated_paragraphs=(
                NarratedParagraphDraft(
                    sequence=2,
                    caption_text="第二句",
                    narration_relative_path="voiceover/sentence-0002.wav",
                    duration_ms=1_200,
                    candidates=(candidate,),
                ),
            ),
        ),
    )

    for construct in constructors:
        with pytest.raises(SpeechParagraphDraftRejected) as caught:
            construct()
        assert str(caught.value) == "speech paragraph draft rejected"
        assert caught.value.__cause__ is None


def test_speech_aware_draft_exposes_its_protocol_version() -> None:
    voiced = _material(has_speech=True)

    result = build_speech_aware_paragraph_draft((voiced,), planner=_ForbiddenPlanner())

    assert result.version == LOCAL_EDITING_SPEECH_PARAGRAPH_VERSION


def test_planner_cannot_reintroduce_a_voiced_material_as_sentence_candidate() -> None:
    voiced = _material(has_speech=True, transcript="秘密转写")
    silent = _material()
    leaked = _narrated(silent)
    object.__setattr__(
        leaked,
        "candidates",
        (
            FittingMaterialSegment(
                material_id=voiced.material_id,
                score=90,
                duration_ms=leaked.duration_ms,
                source_in_ms=0,
                source_out_ms=leaked.duration_ms,
            ),
        ),
    )

    with pytest.raises(SpeechParagraphDraftRejected) as caught:
        build_speech_aware_paragraph_draft(
            (voiced, silent),
            planner=_RecordingPlanner((leaked,)),
        )

    assert str(caught.value) == "speech paragraph draft rejected"
    assert "秘密转写" not in str(caught.value)
    assert str(voiced.material_id) not in str(caught.value)


def test_planner_exception_is_collapsed_without_sensitive_exception_chain() -> None:
    silent = _material()

    class _LeakingPlanner:
        def plan(self, material_ids: tuple[UUID, ...]) -> tuple[NarratedParagraphDraft, ...]:
            raise RuntimeError(f"/Users/private/movie.mp4 {material_ids[0]}")

    with pytest.raises(SpeechParagraphDraftRejected) as caught:
        build_speech_aware_paragraph_draft((silent,), planner=_LeakingPlanner())

    assert str(caught.value) == "speech paragraph draft rejected"
    assert caught.value.__cause__ is None


def test_mutated_voiced_protocol_value_is_rejected_at_orchestration_boundary() -> None:
    voiced = _material(has_speech=True, transcript="不应泄漏的转写")
    object.__setattr__(voiced, "speech_transcript", None)

    with pytest.raises(SpeechParagraphDraftRejected) as caught:
        build_speech_aware_paragraph_draft((voiced,), planner=_ForbiddenPlanner())

    assert str(caught.value) == "speech paragraph draft rejected"
    assert caught.value.__cause__ is None


def test_executor_draft_has_no_control_plane_implementation_dependency() -> None:
    source = (
        Path(__file__).parents[3]
        / "src"
        / "automation_tool"
        / "executor"
        / "speech_paragraph_draft.py"
    ).read_text(encoding="utf-8")

    assert "automation_tool.control_plane" not in source
