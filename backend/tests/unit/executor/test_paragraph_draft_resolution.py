"""LE-16 T4: resolve every narrated paragraph or return one fixed product result."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import cast
from uuid import UUID, uuid4

import pytest

from automation_tool.executor.segment_selection import FittingMaterialSegment
from automation_tool.executor.speech_paragraph_draft import (
    NarratedParagraphDraft,
    OriginalSpeechParagraphDraft,
    ParagraphDraftFailure,
    ParagraphDraftFailureCode,
    ResolvedSpeechAwareParagraphDraft,
    SelectedNarratedParagraphDraft,
    SpeechAwareParagraphDraft,
    SpeechParagraphDraftRejected,
    resolve_speech_aware_paragraph_draft,
)
from automation_tool.protocol.local_editing import MAX_LOCAL_EDITING_SEMANTIC_MATERIALS


def _candidate(
    material_id: UUID,
    *,
    duration_ms: int = 1_000,
    score: int = 90,
) -> FittingMaterialSegment:
    return FittingMaterialSegment(
        material_id=material_id,
        score=score,
        duration_ms=duration_ms,
        source_in_ms=100,
        source_out_ms=100 + duration_ms,
    )


def _paragraph(
    sequence: int,
    *,
    qualified_material_ids: tuple[UUID, ...],
    fitting_material_ids: tuple[UUID, ...],
) -> NarratedParagraphDraft:
    return NarratedParagraphDraft(
        sequence=sequence,
        caption_text=f"第{sequence}句",
        narration_relative_path=f"voiceover/sentence-{sequence:04d}.wav",
        duration_ms=1_000,
        qualified_material_ids=qualified_material_ids,
        candidates=tuple(_candidate(material_id) for material_id in fitting_material_ids),
    )


def _draft(
    silent_material_ids: tuple[UUID, ...],
    paragraphs: tuple[NarratedParagraphDraft, ...],
) -> SpeechAwareParagraphDraft:
    return SpeechAwareParagraphDraft(
        original_speech_paragraphs=(),
        silent_material_ids=silent_material_ids,
        narrated_paragraphs=paragraphs,
    )


def _original(material_id: UUID) -> OriginalSpeechParagraphDraft:
    return OriginalSpeechParagraphDraft(
        material_id=material_id,
        source_in_ms=100,
        source_out_ms=1_100,
        visual_start_ms=0,
        visual_duration_ms=1_000,
        ambient_start_ms=0,
        ambient_duration_ms=1_000,
        caption_start_ms=0,
        caption_duration_ms=1_000,
        caption_text="完整原声",
    )


def test_fewer_silent_materials_than_sentences_is_all_or_nothing_failure() -> None:
    only = uuid4()
    draft = _draft(
        (only,),
        (
            _paragraph(1, qualified_material_ids=(only,), fitting_material_ids=(only,)),
            _paragraph(2, qualified_material_ids=(only,), fitting_material_ids=(only,)),
        ),
    )

    result = resolve_speech_aware_paragraph_draft(draft)

    assert result == ParagraphDraftFailure(code=ParagraphDraftFailureCode.INSUFFICIENT_MATERIALS)
    assert not isinstance(result, ResolvedSpeechAwareParagraphDraft)


def test_one_static_image_can_cover_multiple_narrated_sentences() -> None:
    image = uuid4()

    def static_paragraph(sequence: int) -> NarratedParagraphDraft:
        return NarratedParagraphDraft(
            sequence=sequence,
            caption_text=f"第{sequence}句",
            narration_relative_path=f"voiceover/sentence-{sequence:04d}.wav",
            duration_ms=1_000,
            qualified_material_ids=(image,),
            candidates=(
                FittingMaterialSegment(
                    material_id=image,
                    score=90,
                    duration_ms=1_000,
                    source_in_ms=None,
                    source_out_ms=None,
                ),
            ),
        )

    result = resolve_speech_aware_paragraph_draft(
        _draft((image,), (static_paragraph(1), static_paragraph(2)))
    )

    assert isinstance(result, ResolvedSpeechAwareParagraphDraft)
    assert tuple(paragraph.segment.material_id for paragraph in result.narrated_paragraphs) == (
        image,
        image,
    )


def test_qualified_candidates_with_no_decodable_window_are_source_too_short() -> None:
    material_id = uuid4()
    draft = _draft(
        (material_id,),
        (
            _paragraph(
                1,
                qualified_material_ids=(material_id,),
                fitting_material_ids=(),
            ),
        ),
    )

    result = resolve_speech_aware_paragraph_draft(draft)

    assert result == ParagraphDraftFailure(code=ParagraphDraftFailureCode.SOURCE_TOO_SHORT)


def test_too_short_first_rank_falls_back_to_next_fitting_candidate() -> None:
    too_short = uuid4()
    fitting = uuid4()
    paragraph = _paragraph(
        1,
        qualified_material_ids=(too_short, fitting),
        fitting_material_ids=(fitting,),
    )

    result = resolve_speech_aware_paragraph_draft(_draft((too_short, fitting), (paragraph,)))

    assert result == ResolvedSpeechAwareParagraphDraft(
        original_speech_paragraphs=(),
        narrated_paragraphs=(
            SelectedNarratedParagraphDraft(
                sequence=1,
                caption_text="第1句",
                narration_relative_path="voiceover/sentence-0001.wav",
                duration_ms=1_000,
                segment=_candidate(fitting),
            ),
        ),
    )


def test_all_scores_below_threshold_are_not_forced_into_a_draft() -> None:
    material_id = uuid4()
    draft = _draft(
        (material_id,),
        (
            _paragraph(
                1,
                qualified_material_ids=(),
                fitting_material_ids=(),
            ),
        ),
    )

    result = resolve_speech_aware_paragraph_draft(draft)

    assert result == ParagraphDraftFailure(code=ParagraphDraftFailureCode.NO_RELEVANT_MATERIAL)


def test_all_voiced_draft_remains_a_success_without_narrated_materials() -> None:
    original = _original(uuid4())
    draft = SpeechAwareParagraphDraft(
        original_speech_paragraphs=(original,),
        silent_material_ids=(),
        narrated_paragraphs=(),
    )

    result = resolve_speech_aware_paragraph_draft(draft)

    assert result == ResolvedSpeechAwareParagraphDraft(
        original_speech_paragraphs=(original,),
        narrated_paragraphs=(),
    )


def test_resolution_backtracks_from_greedy_choice_to_complete_unique_assignment() -> None:
    flexible_first = uuid4()
    required_by_second = uuid4()
    draft = _draft(
        (required_by_second, flexible_first),
        (
            _paragraph(
                1,
                qualified_material_ids=(required_by_second, flexible_first),
                fitting_material_ids=(required_by_second, flexible_first),
            ),
            _paragraph(
                2,
                qualified_material_ids=(required_by_second,),
                fitting_material_ids=(required_by_second,),
            ),
        ),
    )

    result = resolve_speech_aware_paragraph_draft(draft)

    assert isinstance(result, ResolvedSpeechAwareParagraphDraft)
    assert tuple(paragraph.segment.material_id for paragraph in result.narrated_paragraphs) == (
        flexible_first,
        required_by_second,
    )


def test_future_assignment_can_rematch_an_earlier_future_paragraph() -> None:
    first = uuid4()
    shared = uuid4()
    alternative = uuid4()
    draft = _draft(
        (first, shared, alternative),
        (
            _paragraph(1, qualified_material_ids=(first,), fitting_material_ids=(first,)),
            _paragraph(
                2,
                qualified_material_ids=(shared, alternative),
                fitting_material_ids=(shared, alternative),
            ),
            _paragraph(3, qualified_material_ids=(shared,), fitting_material_ids=(shared,)),
        ),
    )

    result = resolve_speech_aware_paragraph_draft(draft)

    assert isinstance(result, ResolvedSpeechAwareParagraphDraft)
    assert tuple(paragraph.segment.material_id for paragraph in result.narrated_paragraphs) == (
        first,
        alternative,
        shared,
    )


def test_future_assignment_tries_next_candidate_when_owner_cannot_be_rematched() -> None:
    first = uuid4()
    shared = uuid4()
    alternative = uuid4()
    draft = _draft(
        (first, shared, alternative),
        (
            _paragraph(1, qualified_material_ids=(first,), fitting_material_ids=(first,)),
            _paragraph(2, qualified_material_ids=(shared,), fitting_material_ids=(shared,)),
            _paragraph(
                3,
                qualified_material_ids=(shared, alternative),
                fitting_material_ids=(shared, alternative),
            ),
        ),
    )

    result = resolve_speech_aware_paragraph_draft(draft)

    assert isinstance(result, ResolvedSpeechAwareParagraphDraft)
    assert tuple(paragraph.segment.material_id for paragraph in result.narrated_paragraphs) == (
        first,
        shared,
        alternative,
    )


def test_later_paragraph_skips_a_material_already_selected_by_an_earlier_one() -> None:
    first = uuid4()
    second = uuid4()
    draft = _draft(
        (first, second),
        (
            _paragraph(1, qualified_material_ids=(first,), fitting_material_ids=(first,)),
            _paragraph(
                2,
                qualified_material_ids=(first, second),
                fitting_material_ids=(first, second),
            ),
        ),
    )

    result = resolve_speech_aware_paragraph_draft(draft)

    assert isinstance(result, ResolvedSpeechAwareParagraphDraft)
    assert tuple(paragraph.segment.material_id for paragraph in result.narrated_paragraphs) == (
        first,
        second,
    )


def test_no_complete_unique_assignment_is_insufficient_materials() -> None:
    shared = uuid4()
    unused = uuid4()
    draft = _draft(
        (shared, unused),
        (
            _paragraph(1, qualified_material_ids=(shared,), fitting_material_ids=(shared,)),
            _paragraph(2, qualified_material_ids=(shared,), fitting_material_ids=(shared,)),
        ),
    )

    result = resolve_speech_aware_paragraph_draft(draft)

    assert result == ParagraphDraftFailure(code=ParagraphDraftFailureCode.INSUFFICIENT_MATERIALS)


@pytest.mark.parametrize(
    "code",
    [
        ParagraphDraftFailureCode.INSUFFICIENT_MATERIALS,
        ParagraphDraftFailureCode.SOURCE_TOO_SHORT,
        ParagraphDraftFailureCode.NO_RELEVANT_MATERIAL,
    ],
)
def test_product_failure_contains_only_the_fixed_code(
    code: ParagraphDraftFailureCode,
) -> None:
    failure = ParagraphDraftFailure(code=code)

    assert failure.code is code
    assert repr(failure) == f"ParagraphDraftFailure(code={code!r})"
    assert "句子" not in repr(failure)
    assert "/Users" not in repr(failure)


def test_public_t4_values_fail_closed() -> None:
    first = uuid4()
    second = uuid4()
    segment = _candidate(first)
    selected = SelectedNarratedParagraphDraft(
        sequence=1,
        caption_text="第一句",
        narration_relative_path="voiceover/sentence-0001.wav",
        duration_ms=1_000,
        segment=segment,
    )
    constructors: tuple[Callable[[], object], ...] = (
        lambda: NarratedParagraphDraft(
            sequence=1,
            caption_text="第一句",
            narration_relative_path="voiceover/sentence-0001.wav",
            duration_ms=1_000,
            qualified_material_ids=(first, second),
            candidates=(_candidate(second), _candidate(first)),
        ),
        lambda: ParagraphDraftFailure(code=cast(ParagraphDraftFailureCode, "PRIVATE_PATH")),
        lambda: SelectedNarratedParagraphDraft(
            sequence=1,
            caption_text="第一句",
            narration_relative_path="voiceover/sentence-0001.wav",
            duration_ms=1_000,
            segment=cast(FittingMaterialSegment, object()),
        ),
        lambda: ResolvedSpeechAwareParagraphDraft(
            original_speech_paragraphs=(),
            narrated_paragraphs=(),
        ),
        lambda: resolve_speech_aware_paragraph_draft(
            cast(SpeechAwareParagraphDraft, object()),
        ),
        lambda: ResolvedSpeechAwareParagraphDraft(
            original_speech_paragraphs=(_original(first),),
            narrated_paragraphs=(selected,),
        ),
    )

    for construct in constructors:
        with pytest.raises(SpeechParagraphDraftRejected) as error:
            construct()
        assert str(error.value) == "speech paragraph draft rejected"
        assert error.value.__cause__ is None


def test_selected_paragraph_revalidates_mutated_fitting_segment() -> None:
    segment = _candidate(uuid4())
    object.__setattr__(segment, "score", 0)

    with pytest.raises(SpeechParagraphDraftRejected):
        SelectedNarratedParagraphDraft(
            sequence=1,
            caption_text="第一句",
            narration_relative_path="voiceover/sentence-0001.wav",
            duration_ms=1_000,
            segment=segment,
        )


def test_resolved_draft_revalidates_mutated_original_paragraph() -> None:
    original = _original(uuid4())
    object.__setattr__(original, "source_in_ms", "C:/Users/private/movie.mp4")

    with pytest.raises(SpeechParagraphDraftRejected):
        ResolvedSpeechAwareParagraphDraft(
            original_speech_paragraphs=(original,),
            narrated_paragraphs=(),
        )


def test_resolved_draft_revalidates_mutated_selected_paragraph() -> None:
    selected = SelectedNarratedParagraphDraft(
        sequence=1,
        caption_text="第一句",
        narration_relative_path="voiceover/sentence-0001.wav",
        duration_ms=1_000,
        segment=_candidate(uuid4()),
    )
    object.__setattr__(selected, "narration_relative_path", "C:/Users/private/voice.wav")

    with pytest.raises(SpeechParagraphDraftRejected):
        ResolvedSpeechAwareParagraphDraft(
            original_speech_paragraphs=(),
            narrated_paragraphs=(selected,),
        )


def test_speech_aware_draft_caps_original_and_silent_materials_together() -> None:
    originals = tuple(_original(uuid4()) for _ in range(MAX_LOCAL_EDITING_SEMANTIC_MATERIALS))
    silent = uuid4()

    with pytest.raises(SpeechParagraphDraftRejected):
        SpeechAwareParagraphDraft(
            original_speech_paragraphs=originals,
            silent_material_ids=(silent,),
            narrated_paragraphs=(
                _paragraph(1, qualified_material_ids=(silent,), fitting_material_ids=(silent,)),
            ),
        )


def test_resolved_draft_caps_total_materials() -> None:
    originals = tuple(_original(uuid4()) for _ in range(MAX_LOCAL_EDITING_SEMANTIC_MATERIALS + 1))

    with pytest.raises(SpeechParagraphDraftRejected):
        ResolvedSpeechAwareParagraphDraft(
            original_speech_paragraphs=originals,
            narrated_paragraphs=(),
        )


def test_resolved_draft_rejects_wrong_container_before_iterating_it() -> None:
    class _IterationCountingList(list[OriginalSpeechParagraphDraft]):
        iterations = 0

        def __iter__(self) -> Iterator[OriginalSpeechParagraphDraft]:
            self.iterations += 1
            return super().__iter__()

    originals = _IterationCountingList([_original(uuid4())])

    with pytest.raises(SpeechParagraphDraftRejected):
        ResolvedSpeechAwareParagraphDraft(
            original_speech_paragraphs=cast(
                tuple[OriginalSpeechParagraphDraft, ...],
                originals,
            ),
            narrated_paragraphs=(),
        )

    assert originals.iterations == 0


def test_resolution_rejects_mutated_silent_material_container_at_public_boundary() -> None:
    material_id = uuid4()
    draft = _draft(
        (material_id,),
        (
            _paragraph(
                1,
                qualified_material_ids=(material_id,),
                fitting_material_ids=(material_id,),
            ),
        ),
    )
    object.__setattr__(draft, "silent_material_ids", [material_id])

    with pytest.raises(SpeechParagraphDraftRejected) as error:
        resolve_speech_aware_paragraph_draft(draft)

    assert str(error.value) == "speech paragraph draft rejected"
    assert error.value.__cause__ is None


def test_resolution_revalidates_mutated_original_paragraph_at_public_boundary() -> None:
    original = _original(uuid4())
    draft = SpeechAwareParagraphDraft(
        original_speech_paragraphs=(original,),
        silent_material_ids=(),
        narrated_paragraphs=(),
    )
    object.__setattr__(original, "source_in_ms", "/Users/private/movie.mp4")

    with pytest.raises(SpeechParagraphDraftRejected) as error:
        resolve_speech_aware_paragraph_draft(draft)

    assert str(error.value) == "speech paragraph draft rejected"
    assert "private" not in str(error.value)
    assert error.value.__cause__ is None
