"""LE-16 T2: choose fitting source windows from verified decodable ranges."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest

from automation_tool.executor.segment_selection import (
    FittingMaterialSegment,
    SegmentSelectionCandidates,
    SegmentSelectionRejected,
    SegmentSelectionSlot,
    VerifiedDecodableInterval,
    VerifiedDecodableMaterial,
    select_fitting_segments,
)
from automation_tool.protocol.local_editing import (
    LOCAL_EDITING_SEGMENT_SELECTION_VERSION,
    MAX_LOCAL_EDITING_MATERIAL_DURATION_MS,
    LocalEditingProtocolRejected,
    SegmentSelectionCandidateScore,
    SegmentSelectionMaterial,
    SegmentSelectionMaterialKind,
    SegmentSelectionSentenceMatches,
)


def _material(
    *,
    kind: SegmentSelectionMaterialKind = SegmentSelectionMaterialKind.VIDEO,
    duration_ms: int = 60_000,
    shot_boundaries_ms: tuple[int, ...] = (),
    digest_character: str = "a",
) -> SegmentSelectionMaterial:
    return SegmentSelectionMaterial(
        material_id=uuid4(),
        kind=kind,
        duration_ms=None if kind is SegmentSelectionMaterialKind.IMAGE else duration_ms,
        content_digest=digest_character * 64,
        shot_boundaries_ms=(
            shot_boundaries_ms if kind is SegmentSelectionMaterialKind.VIDEO else ()
        ),
    )


def _matches(
    materials_and_scores: tuple[tuple[SegmentSelectionMaterial, int], ...],
    *,
    sequence: int = 1,
) -> SegmentSelectionSentenceMatches:
    ranked = sorted(
        enumerate(materials_and_scores),
        key=lambda item: (-item[1][1], item[0]),
    )
    return SegmentSelectionSentenceMatches(
        sequence=sequence,
        candidates=tuple(
            SegmentSelectionCandidateScore(
                material_id=material.material_id,
                score=score,
                qualified=score >= 60,
            )
            for _, (material, score) in ranked
        ),
    )


def _evidence(
    material: SegmentSelectionMaterial,
    *intervals: tuple[int, int],
) -> VerifiedDecodableMaterial:
    return VerifiedDecodableMaterial(
        material_id=material.material_id,
        content_digest=material.content_digest,
        intervals=tuple(
            VerifiedDecodableInterval(start_ms=start, end_ms=end) for start, end in intervals
        ),
    )


def test_declared_duration_never_substitutes_for_decodable_frames() -> None:
    truncated = _material(
        duration_ms=60_000,
        shot_boundaries_ms=(0, 10_000, 30_000),
    )

    result = select_fitting_segments(
        SegmentSelectionSlot(sequence=1, duration_ms=9_000),
        _matches(((truncated, 95),)),
        (truncated,),
        (_evidence(truncated, (0, 8_000)),),
    )

    assert result == SegmentSelectionCandidates(
        sequence=1,
        duration_ms=9_000,
        segments=(),
    )


def test_zero_decodable_frames_are_a_valid_empty_evidence_result() -> None:
    material = _material()
    evidence = VerifiedDecodableMaterial(
        material_id=material.material_id,
        content_digest=material.content_digest,
        intervals=(),
    )

    result = select_fitting_segments(
        SegmentSelectionSlot(sequence=1, duration_ms=1_000),
        _matches(((material, 90),)),
        (material,),
        (evidence,),
    )

    assert result.segments == ()


class _IterationCountingTuple(tuple[int, ...]):
    iterations: int

    def __new__(cls, values: tuple[int, ...]) -> _IterationCountingTuple:
        instance = super().__new__(cls, values)
        instance.iterations = 0
        return instance

    def __iter__(self) -> Iterator[int]:
        self.iterations += 1
        return super().__iter__()


def test_ordered_shots_and_decode_ranges_are_merged_without_repeated_scans() -> None:
    shot_boundaries = _IterationCountingTuple(tuple(range(0, 1_300, 20)))
    material = _material(
        duration_ms=2_000,
        shot_boundaries_ms=shot_boundaries,
    )
    evidence = _evidence(
        material,
        *((start, start + 1) for start in range(10, 1_310, 20)),
    )
    shot_boundaries.iterations = 0

    result = select_fitting_segments(
        SegmentSelectionSlot(sequence=1, duration_ms=2),
        _matches(((material, 90),)),
        (material,),
        (evidence,),
    )

    assert result.segments == ()
    assert shot_boundaries.iterations <= 2


def test_executor_selection_has_no_control_plane_implementation_dependency() -> None:
    source = (
        Path(__file__).parents[3] / "src" / "automation_tool" / "executor" / "segment_selection.py"
    ).read_text(encoding="utf-8")

    assert "automation_tool.control_plane" not in source


def test_segment_selection_protocol_is_versioned_and_matches_upstream_limits() -> None:
    from automation_tool.control_plane.domain.material import (
        MAX_MATERIAL_DURATION_MS,
        MAX_SHOT_BOUNDARIES,
    )
    from automation_tool.control_plane.domain.timeline import MAX_TIMELINE_DURATION_MS
    from automation_tool.executor.script_segmentation import MAX_SCRIPT_SENTENCES
    from automation_tool.executor.semantic_matching import (
        MAX_SEMANTIC_MATERIALS,
        SEMANTIC_MATCH_SCORE_THRESHOLD,
    )
    from automation_tool.protocol.local_editing import (
        LOCAL_EDITING_SEMANTIC_SCORE_THRESHOLD,
        MAX_LOCAL_EDITING_SCRIPT_SENTENCES,
        MAX_LOCAL_EDITING_SEMANTIC_MATERIALS,
        MAX_LOCAL_EDITING_SHOT_BOUNDARIES,
        MAX_LOCAL_EDITING_TIMELINE_DURATION_MS,
    )

    material = _material()
    matches = _matches(((material, 90),))

    assert material.version == LOCAL_EDITING_SEGMENT_SELECTION_VERSION
    assert matches.version == LOCAL_EDITING_SEGMENT_SELECTION_VERSION
    assert MAX_LOCAL_EDITING_MATERIAL_DURATION_MS == MAX_MATERIAL_DURATION_MS
    assert MAX_LOCAL_EDITING_SHOT_BOUNDARIES == MAX_SHOT_BOUNDARIES
    assert MAX_LOCAL_EDITING_TIMELINE_DURATION_MS == MAX_TIMELINE_DURATION_MS
    assert MAX_LOCAL_EDITING_SCRIPT_SENTENCES == MAX_SCRIPT_SENTENCES
    assert MAX_LOCAL_EDITING_SEMANTIC_MATERIALS == MAX_SEMANTIC_MATERIALS
    assert LOCAL_EDITING_SEMANTIC_SCORE_THRESHOLD == SEMANTIC_MATCH_SCORE_THRESHOLD


@pytest.mark.parametrize(
    "construct",
    [
        lambda: SegmentSelectionMaterial(
            material_id=UUID(int=0),
            kind=SegmentSelectionMaterialKind.VIDEO,
            duration_ms=1,
            content_digest="a" * 64,
            shot_boundaries_ms=(),
        ),
        lambda: SegmentSelectionMaterial(
            material_id=uuid4(),
            kind=cast(SegmentSelectionMaterialKind, "video"),
            duration_ms=1,
            content_digest="a" * 64,
            shot_boundaries_ms=(),
        ),
        lambda: SegmentSelectionMaterial(
            material_id=uuid4(),
            kind=SegmentSelectionMaterialKind.IMAGE,
            duration_ms=1,
            content_digest="a" * 64,
            shot_boundaries_ms=(),
        ),
        lambda: SegmentSelectionMaterial(
            material_id=uuid4(),
            kind=SegmentSelectionMaterialKind.VIDEO,
            duration_ms=0,
            content_digest="a" * 64,
            shot_boundaries_ms=(),
        ),
        lambda: SegmentSelectionMaterial(
            material_id=uuid4(),
            kind=SegmentSelectionMaterialKind.VIDEO,
            duration_ms=1,
            content_digest="not-a-digest",
            shot_boundaries_ms=(),
        ),
        lambda: SegmentSelectionMaterial(
            material_id=uuid4(),
            kind=SegmentSelectionMaterialKind.AUDIO,
            duration_ms=1,
            content_digest="a" * 64,
            shot_boundaries_ms=(0,),
        ),
        lambda: SegmentSelectionMaterial(
            material_id=uuid4(),
            kind=SegmentSelectionMaterialKind.VIDEO,
            duration_ms=1,
            content_digest="a" * 64,
            shot_boundaries_ms=(True,),
        ),
        lambda: SegmentSelectionMaterial(
            material_id=uuid4(),
            kind=SegmentSelectionMaterialKind.VIDEO,
            duration_ms=1,
            content_digest="a" * 64,
            shot_boundaries_ms=(1, 1),
        ),
        lambda: SegmentSelectionMaterial(
            material_id=uuid4(),
            kind=SegmentSelectionMaterialKind.VIDEO,
            duration_ms=1,
            content_digest="a" * 64,
            shot_boundaries_ms=(MAX_LOCAL_EDITING_MATERIAL_DURATION_MS,),
        ),
    ],
)
def test_segment_selection_protocol_materials_fail_closed(
    construct: Callable[[], object],
) -> None:
    with pytest.raises(
        LocalEditingProtocolRejected,
        match="local editing protocol value is invalid",
    ):
        construct()


@pytest.mark.parametrize(
    "construct",
    [
        lambda: SegmentSelectionCandidateScore(
            material_id=UUID(int=0),
            score=90,
            qualified=True,
        ),
        lambda: SegmentSelectionCandidateScore(
            material_id=uuid4(),
            score=True,
            qualified=True,
        ),
        lambda: SegmentSelectionCandidateScore(
            material_id=uuid4(),
            score=101,
            qualified=True,
        ),
        lambda: SegmentSelectionCandidateScore(
            material_id=uuid4(),
            score=60,
            qualified=False,
        ),
        lambda: SegmentSelectionSentenceMatches(sequence=0, candidates=()),
        lambda: SegmentSelectionSentenceMatches(sequence=1, candidates=()),
        lambda: SegmentSelectionSentenceMatches(
            sequence=1,
            candidates=cast(tuple[SegmentSelectionCandidateScore, ...], []),
        ),
        lambda: SegmentSelectionSentenceMatches(
            sequence=1,
            candidates=(
                SegmentSelectionCandidateScore(uuid4(), 80, True),
                SegmentSelectionCandidateScore(uuid4(), 90, True),
            ),
        ),
    ],
)
def test_segment_selection_protocol_rankings_fail_closed(
    construct: Callable[[], object],
) -> None:
    with pytest.raises(LocalEditingProtocolRejected):
        construct()


def test_verified_frames_may_extend_beyond_an_understated_container_duration() -> None:
    understated = _material(duration_ms=1_000)

    result = select_fitting_segments(
        SegmentSelectionSlot(sequence=1, duration_ms=1_500),
        _matches(((understated, 90),)),
        (understated,),
        (_evidence(understated, (0, 2_000)),),
    )

    assert result.segments == (
        FittingMaterialSegment(
            material_id=understated.material_id,
            score=90,
            duration_ms=1_500,
            source_in_ms=0,
            source_out_ms=1_500,
        ),
    )


def test_shot_boundaries_are_intersected_with_decodable_ranges() -> None:
    material = _material(
        shot_boundaries_ms=(0, 5_000, 10_000),
    )

    result = select_fitting_segments(
        SegmentSelectionSlot(sequence=1, duration_ms=3_500),
        _matches(((material, 90),)),
        (material,),
        (_evidence(material, (2_000, 7_000), (9_000, 15_000)),),
    )

    assert result.segments == (
        FittingMaterialSegment(
            material_id=material.material_id,
            score=90,
            duration_ms=3_500,
            source_in_ms=10_000,
            source_out_ms=13_500,
        ),
    )


def test_the_earliest_fitting_shot_intersection_wins() -> None:
    material = _material(
        shot_boundaries_ms=(0, 5_000, 10_000),
    )

    result = select_fitting_segments(
        SegmentSelectionSlot(sequence=1, duration_ms=3_000),
        _matches(((material, 90),)),
        (material,),
        (_evidence(material, (2_000, 7_000), (9_000, 15_000)),),
    )

    assert result.segments[0].source_in_ms == 2_000
    assert result.segments[0].source_out_ms == 5_000


def test_a_nonzero_first_cut_does_not_drop_the_opening_shot() -> None:
    material = _material(shot_boundaries_ms=(5_000,))

    result = select_fitting_segments(
        SegmentSelectionSlot(sequence=1, duration_ms=3_000),
        _matches(((material, 90),)),
        (material,),
        (_evidence(material, (0, 4_000)),),
    )

    assert result.segments[0].source_in_ms == 0
    assert result.segments[0].source_out_ms == 3_000


def test_a_material_without_shot_boundaries_uses_only_verified_ranges() -> None:
    material = _material()

    result = select_fitting_segments(
        SegmentSelectionSlot(sequence=1, duration_ms=4_000),
        _matches(((material, 90),)),
        (material,),
        (_evidence(material, (4_000, 6_000), (8_000, 14_000)),),
    )

    assert result.segments[0].source_in_ms == 8_000
    assert result.segments[0].source_out_ms == 12_000


def test_an_image_needs_no_decodable_evidence_or_source_window() -> None:
    image = _material(kind=SegmentSelectionMaterialKind.IMAGE)

    result = select_fitting_segments(
        SegmentSelectionSlot(sequence=1, duration_ms=12_345),
        _matches(((image, 90),)),
        (image,),
        (),
    )

    assert result.segments == (
        FittingMaterialSegment(
            material_id=image.material_id,
            score=90,
            duration_ms=12_345,
            source_in_ms=None,
            source_out_ms=None,
        ),
    )


def test_qualified_fitting_materials_are_ranked_by_score_then_input_order() -> None:
    first = _material(digest_character="a")
    second = _material(
        kind=SegmentSelectionMaterialKind.IMAGE,
        digest_character="b",
    )
    third = _material(digest_character="c")
    result = select_fitting_segments(
        SegmentSelectionSlot(sequence=1, duration_ms=2_000),
        _matches(((first, 80), (third, 80), (second, 95))),
        (first, second, third),
        (
            _evidence(first, (0, 5_000)),
            _evidence(third, (0, 5_000)),
        ),
    )

    assert tuple(segment.material_id for segment in result.segments) == (
        second.material_id,
        first.material_id,
        third.material_id,
    )


def test_low_scores_and_too_short_ranges_are_not_returned() -> None:
    too_short = _material(digest_character="a")
    low_score = _material(digest_character="b")
    image = _material(
        kind=SegmentSelectionMaterialKind.IMAGE,
        digest_character="c",
    )

    result = select_fitting_segments(
        SegmentSelectionSlot(sequence=1, duration_ms=3_000),
        _matches(((too_short, 95), (low_score, 59), (image, 75))),
        (too_short, low_score, image),
        (
            _evidence(too_short, (0, 2_999)),
            _evidence(low_score, (0, 10_000)),
        ),
    )

    assert tuple(segment.material_id for segment in result.segments) == (image.material_id,)


@pytest.mark.parametrize(
    "evidence_factory",
    [
        lambda first, _second: (),
        lambda first, _second: (_evidence(first, (0, 5_000)),) * 2,
        lambda first, second: (
            _evidence(first, (0, 5_000)),
            _evidence(second, (0, 5_000)),
        ),
    ],
    ids=["missing-video", "duplicate-video", "image-has-evidence"],
)
def test_decodable_evidence_must_exactly_cover_the_video_materials(
    evidence_factory: Callable[
        [SegmentSelectionMaterial, SegmentSelectionMaterial],
        tuple[VerifiedDecodableMaterial, ...],
    ],
) -> None:
    video = _material()
    image = _material(
        kind=SegmentSelectionMaterialKind.IMAGE,
        digest_character="b",
    )

    with pytest.raises(SegmentSelectionRejected):
        select_fitting_segments(
            SegmentSelectionSlot(sequence=1, duration_ms=1_000),
            _matches(((video, 90), (image, 80))),
            (video, image),
            evidence_factory(video, image),
        )


def test_decodable_evidence_is_bound_to_the_material_bytes() -> None:
    material = _material()
    wrong_digest = VerifiedDecodableMaterial(
        material_id=material.material_id,
        content_digest="f" * 64,
        intervals=(VerifiedDecodableInterval(start_ms=0, end_ms=5_000),),
    )

    with pytest.raises(SegmentSelectionRejected):
        select_fitting_segments(
            SegmentSelectionSlot(sequence=1, duration_ms=1_000),
            _matches(((material, 90),)),
            (material,),
            (wrong_digest,),
        )


def test_matching_matrix_must_cover_exactly_the_supplied_materials() -> None:
    supplied = _material()
    unknown = _material(digest_character="b")

    with pytest.raises(SegmentSelectionRejected):
        select_fitting_segments(
            SegmentSelectionSlot(sequence=1, duration_ms=1_000),
            _matches(((unknown, 90),)),
            (supplied,),
            (_evidence(supplied, (0, 5_000)),),
        )


def test_slot_and_matching_sentence_must_name_the_same_sequence() -> None:
    material = _material()

    with pytest.raises(SegmentSelectionRejected):
        select_fitting_segments(
            SegmentSelectionSlot(sequence=2, duration_ms=1_000),
            _matches(((material, 90),), sequence=1),
            (material,),
            (_evidence(material, (0, 5_000)),),
        )


def test_audio_and_duplicate_materials_fail_closed() -> None:
    audio = _material(kind=SegmentSelectionMaterialKind.AUDIO)
    video = _material(digest_character="b")

    for materials, matches, evidence in (
        ((audio,), _matches(((audio, 90),)), ()),
        (
            (video, video),
            _matches(((video, 90),)),
            (_evidence(video, (0, 5_000)),),
        ),
    ):
        with pytest.raises(SegmentSelectionRejected):
            select_fitting_segments(
                SegmentSelectionSlot(sequence=1, duration_ms=1_000),
                matches,
                materials,
                evidence,
            )


@pytest.mark.parametrize(
    "construct",
    [
        lambda: VerifiedDecodableInterval(start_ms=-1, end_ms=1),
        lambda: VerifiedDecodableInterval(start_ms=0, end_ms=0),
        lambda: VerifiedDecodableInterval(start_ms=True, end_ms=1),
        lambda: VerifiedDecodableInterval(
            start_ms=0,
            end_ms=MAX_LOCAL_EDITING_MATERIAL_DURATION_MS + 1,
        ),
        lambda: VerifiedDecodableMaterial(
            material_id=uuid4(),
            content_digest="not-a-digest",
            intervals=(VerifiedDecodableInterval(start_ms=0, end_ms=1),),
        ),
        lambda: VerifiedDecodableMaterial(
            material_id=UUID(int=0),
            content_digest="a" * 64,
            intervals=(),
        ),
        lambda: VerifiedDecodableMaterial(
            material_id=uuid4(),
            content_digest="a" * 64,
            intervals=(
                VerifiedDecodableInterval(start_ms=0, end_ms=2),
                VerifiedDecodableInterval(start_ms=2, end_ms=3),
            ),
        ),
        lambda: SegmentSelectionSlot(sequence=0, duration_ms=1),
        lambda: SegmentSelectionSlot(sequence=1, duration_ms=0),
        lambda: FittingMaterialSegment(
            material_id=uuid4(),
            score=90,
            duration_ms=1,
            source_in_ms=0,
            source_out_ms=None,
        ),
        lambda: FittingMaterialSegment(
            material_id=UUID(int=0),
            score=90,
            duration_ms=1,
            source_in_ms=None,
            source_out_ms=None,
        ),
        lambda: FittingMaterialSegment(
            material_id=uuid4(),
            score=90,
            duration_ms=2,
            source_in_ms=0,
            source_out_ms=1,
        ),
        lambda: SegmentSelectionCandidates(
            sequence=1,
            duration_ms=2,
            segments=(
                FittingMaterialSegment(
                    material_id=uuid4(),
                    score=90,
                    duration_ms=1,
                    source_in_ms=0,
                    source_out_ms=1,
                ),
            ),
        ),
        lambda: SegmentSelectionCandidates(
            sequence=1,
            duration_ms=1,
            segments=(
                FittingMaterialSegment(
                    material_id=uuid4(),
                    score=80,
                    duration_ms=1,
                    source_in_ms=0,
                    source_out_ms=1,
                ),
                FittingMaterialSegment(
                    material_id=uuid4(),
                    score=90,
                    duration_ms=1,
                    source_in_ms=0,
                    source_out_ms=1,
                ),
            ),
        ),
    ],
)
def test_public_selection_values_fail_closed(construct: Callable[[], object]) -> None:
    with pytest.raises(SegmentSelectionRejected):
        construct()
