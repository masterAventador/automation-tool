"""Choose source windows only from path-free, verified decodable intervals."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, NoReturn
from uuid import UUID

from automation_tool.protocol.local_editing import (
    LOCAL_EDITING_SEMANTIC_SCORE_THRESHOLD,
    MAX_LOCAL_EDITING_MATERIAL_DURATION_MS,
    MAX_LOCAL_EDITING_SCRIPT_SENTENCES,
    MAX_LOCAL_EDITING_SEMANTIC_MATERIALS,
    MAX_LOCAL_EDITING_SHOT_BOUNDARIES,
    MAX_LOCAL_EDITING_TIMELINE_DURATION_MS,
    SegmentSelectionMaterial,
    SegmentSelectionMaterialKind,
    SegmentSelectionSentenceMatches,
    is_canonical_local_editing_material_id,
)

_SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}\Z")


class SegmentSelectionRejected(RuntimeError):
    """The segment-selection boundary rejected mismatched or unsafe facts."""

    def __init__(self) -> None:
        super().__init__("segment selection rejected")


def _reject() -> NoReturn:
    raise SegmentSelectionRejected from None


@dataclass(frozen=True, slots=True)
class VerifiedDecodableInterval:
    """One half-open source interval proven to contain decodable picture."""

    start_ms: int
    end_ms: int

    def __post_init__(self) -> None:
        if (
            type(self.start_ms) is not int
            or type(self.end_ms) is not int
            or self.start_ms < 0
            or self.end_ms <= self.start_ms
            or self.end_ms > MAX_LOCAL_EDITING_MATERIAL_DURATION_MS
        ):
            _reject()


@dataclass(frozen=True, slots=True)
class VerifiedDecodableMaterial:
    """Path-free decode evidence bound to one exact registered material."""

    material_id: UUID
    content_digest: str
    intervals: tuple[VerifiedDecodableInterval, ...]

    def __post_init__(self) -> None:
        if (
            not is_canonical_local_editing_material_id(self.material_id)
            or type(self.content_digest) is not str
            or _SHA256_PATTERN.fullmatch(self.content_digest) is None
            or not isinstance(self.intervals, tuple)
            or len(self.intervals) > MAX_LOCAL_EDITING_SHOT_BOUNDARIES + 1
            or not all(
                isinstance(interval, VerifiedDecodableInterval) for interval in self.intervals
            )
        ):
            _reject()
        previous_end = -1
        for interval in self.intervals:
            # Touching ranges must be coalesced by the producer. Keeping one
            # canonical representation prevents a continuous window from
            # depending on how a caller happened to split its evidence.
            if interval.start_ms <= previous_end:
                _reject()
            previous_end = interval.end_ms


@dataclass(frozen=True, slots=True)
class SegmentSelectionSlot:
    """One narration position that needs an equal-length visual segment."""

    sequence: int
    duration_ms: int

    def __post_init__(self) -> None:
        if (
            type(self.sequence) is not int
            or not 1 <= self.sequence <= MAX_LOCAL_EDITING_SCRIPT_SENTENCES
            or type(self.duration_ms) is not int
            or not 1 <= self.duration_ms <= MAX_LOCAL_EDITING_TIMELINE_DURATION_MS
        ):
            _reject()


@dataclass(frozen=True, slots=True)
class FittingMaterialSegment:
    """The earliest fitting source window for one semantically ranked material."""

    material_id: UUID
    score: int
    duration_ms: int
    source_in_ms: int | None
    source_out_ms: int | None

    def __post_init__(self) -> None:
        if (
            not is_canonical_local_editing_material_id(self.material_id)
            or type(self.score) is not int
            or not LOCAL_EDITING_SEMANTIC_SCORE_THRESHOLD <= self.score <= 100
            or type(self.duration_ms) is not int
            or not 1 <= self.duration_ms <= MAX_LOCAL_EDITING_TIMELINE_DURATION_MS
            or (self.source_in_ms is None) != (self.source_out_ms is None)
        ):
            _reject()
        if self.source_in_ms is None:
            return
        if (
            type(self.source_in_ms) is not int
            or type(self.source_out_ms) is not int
            or self.source_in_ms < 0
            or self.source_out_ms > MAX_LOCAL_EDITING_MATERIAL_DURATION_MS
            or self.source_out_ms - self.source_in_ms != self.duration_ms
        ):
            _reject()


@dataclass(frozen=True, slots=True)
class SegmentSelectionCandidates:
    """All fitting materials for one slot in deterministic local rank order."""

    sequence: int
    duration_ms: int
    segments: tuple[FittingMaterialSegment, ...]

    def __post_init__(self) -> None:
        if (
            type(self.sequence) is not int
            or not 1 <= self.sequence <= MAX_LOCAL_EDITING_SCRIPT_SENTENCES
            or type(self.duration_ms) is not int
            or not 1 <= self.duration_ms <= MAX_LOCAL_EDITING_TIMELINE_DURATION_MS
            or not isinstance(self.segments, tuple)
            or len(self.segments) > MAX_LOCAL_EDITING_SEMANTIC_MATERIALS
            or not all(
                isinstance(segment, FittingMaterialSegment)
                and segment.duration_ms == self.duration_ms
                for segment in self.segments
            )
            or len({segment.material_id for segment in self.segments}) != len(self.segments)
            or any(
                earlier.score < later.score
                for earlier, later in zip(self.segments, self.segments[1:], strict=False)
            )
        ):
            _reject()


def _earliest_fitting_window(
    material: SegmentSelectionMaterial,
    evidence: VerifiedDecodableMaterial,
    *,
    duration_ms: int,
) -> tuple[int, int] | None:
    if not material.shot_boundaries_ms:
        for interval in evidence.intervals:
            if interval.end_ms - interval.start_ms >= duration_ms:
                return interval.start_ms, interval.start_ms + duration_ms
        return None

    shot_starts = material.shot_boundaries_ms
    if shot_starts[0] != 0:
        shot_starts = (0, *shot_starts)
    shot_index = 0
    interval_index = 0
    while shot_index < len(shot_starts) and interval_index < len(evidence.intervals):
        shot_start = shot_starts[shot_index]
        shot_end = (
            shot_starts[shot_index + 1]
            if shot_index + 1 < len(shot_starts)
            else MAX_LOCAL_EDITING_MATERIAL_DURATION_MS
        )
        interval = evidence.intervals[interval_index]
        start = max(interval.start_ms, shot_start)
        end = min(interval.end_ms, shot_end)
        if end - start >= duration_ms:
            return start, start + duration_ms
        if shot_end <= interval.end_ms:
            shot_index += 1
        if interval.end_ms <= shot_end:
            interval_index += 1
    return None


def select_fitting_segments(
    slot: SegmentSelectionSlot,
    matches: SegmentSelectionSentenceMatches,
    materials: tuple[SegmentSelectionMaterial, ...],
    decodable_materials: tuple[VerifiedDecodableMaterial, ...],
) -> SegmentSelectionCandidates:
    """Return one earliest equal-length window per qualified fitting material."""

    if (
        not isinstance(slot, SegmentSelectionSlot)
        or not isinstance(matches, SegmentSelectionSentenceMatches)
        or slot.sequence != matches.sequence
        or not isinstance(materials, tuple)
        or not 1 <= len(materials) <= MAX_LOCAL_EDITING_SEMANTIC_MATERIALS
        or not all(isinstance(material, SegmentSelectionMaterial) for material in materials)
        or len({material.material_id for material in materials}) != len(materials)
        or any(
            material.kind
            not in {
                SegmentSelectionMaterialKind.VIDEO,
                SegmentSelectionMaterialKind.IMAGE,
            }
            for material in materials
        )
        or not isinstance(decodable_materials, tuple)
        or not all(
            isinstance(evidence, VerifiedDecodableMaterial) for evidence in decodable_materials
        )
        or len({evidence.material_id for evidence in decodable_materials})
        != len(decodable_materials)
    ):
        _reject()

    material_by_id = {material.material_id: material for material in materials}
    material_index = {material.material_id: index for index, material in enumerate(materials)}
    if {candidate.material_id for candidate in matches.candidates} != set(material_by_id):
        _reject()

    evidence_by_id = {evidence.material_id: evidence for evidence in decodable_materials}
    video_ids = {
        material.material_id
        for material in materials
        if material.kind is SegmentSelectionMaterialKind.VIDEO
    }
    if set(evidence_by_id) != video_ids or any(
        evidence.content_digest != material_by_id[material_id].content_digest
        for material_id, evidence in evidence_by_id.items()
    ):
        _reject()

    ranked = sorted(
        matches.candidates,
        key=lambda candidate: (
            -candidate.score,
            material_index[candidate.material_id],
        ),
    )
    selected: list[FittingMaterialSegment] = []
    for candidate in ranked:
        if not candidate.qualified:
            continue
        material = material_by_id[candidate.material_id]
        if material.kind is SegmentSelectionMaterialKind.IMAGE:
            source_window: tuple[int, int] | None = None
        else:
            source_window = _earliest_fitting_window(
                material,
                evidence_by_id[material.material_id],
                duration_ms=slot.duration_ms,
            )
            if source_window is None:
                continue
        selected.append(
            FittingMaterialSegment(
                material_id=material.material_id,
                score=candidate.score,
                duration_ms=slot.duration_ms,
                source_in_ms=None if source_window is None else source_window[0],
                source_out_ms=None if source_window is None else source_window[1],
            )
        )

    return SegmentSelectionCandidates(
        sequence=slot.sequence,
        duration_ms=slot.duration_ms,
        segments=tuple(selected),
    )


__all__ = [
    "FittingMaterialSegment",
    "SegmentSelectionCandidates",
    "SegmentSelectionRejected",
    "SegmentSelectionSlot",
    "VerifiedDecodableInterval",
    "VerifiedDecodableMaterial",
    "select_fitting_segments",
]
