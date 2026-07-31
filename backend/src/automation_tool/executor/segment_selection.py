"""Choose source windows only from path-free, verified decodable intervals."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, NoReturn

from automation_tool.control_plane.domain.material import (
    MAX_MATERIAL_DURATION_MS,
    MAX_SHOT_BOUNDARIES,
    Material,
    MaterialId,
    MaterialKind,
)
from automation_tool.control_plane.domain.timeline import MAX_TIMELINE_DURATION_MS
from automation_tool.executor.script_segmentation import MAX_SCRIPT_SENTENCES
from automation_tool.executor.semantic_matching import (
    MAX_SEMANTIC_MATERIALS,
    SEMANTIC_MATCH_SCORE_THRESHOLD,
    SemanticSentenceMatches,
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
            or self.end_ms > MAX_MATERIAL_DURATION_MS
        ):
            _reject()


@dataclass(frozen=True, slots=True)
class VerifiedDecodableMaterial:
    """Path-free decode evidence bound to one exact registered material."""

    material_id: MaterialId
    content_digest: str
    intervals: tuple[VerifiedDecodableInterval, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.material_id, MaterialId)
            or type(self.content_digest) is not str
            or _SHA256_PATTERN.fullmatch(self.content_digest) is None
            or not isinstance(self.intervals, tuple)
            or not 1 <= len(self.intervals) <= MAX_SHOT_BOUNDARIES + 1
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
            or not 1 <= self.sequence <= MAX_SCRIPT_SENTENCES
            or type(self.duration_ms) is not int
            or not 1 <= self.duration_ms <= MAX_TIMELINE_DURATION_MS
        ):
            _reject()


@dataclass(frozen=True, slots=True)
class FittingMaterialSegment:
    """The earliest fitting source window for one semantically ranked material."""

    material_id: MaterialId
    score: int
    duration_ms: int
    source_in_ms: int | None
    source_out_ms: int | None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.material_id, MaterialId)
            or type(self.score) is not int
            or not SEMANTIC_MATCH_SCORE_THRESHOLD <= self.score <= 100
            or type(self.duration_ms) is not int
            or not 1 <= self.duration_ms <= MAX_TIMELINE_DURATION_MS
            or (self.source_in_ms is None) != (self.source_out_ms is None)
        ):
            _reject()
        if self.source_in_ms is None:
            return
        if (
            type(self.source_in_ms) is not int
            or type(self.source_out_ms) is not int
            or self.source_in_ms < 0
            or self.source_out_ms > MAX_MATERIAL_DURATION_MS
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
            or not 1 <= self.sequence <= MAX_SCRIPT_SENTENCES
            or type(self.duration_ms) is not int
            or not 1 <= self.duration_ms <= MAX_TIMELINE_DURATION_MS
            or not isinstance(self.segments, tuple)
            or len(self.segments) > MAX_SEMANTIC_MATERIALS
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
    material: Material,
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
    candidates: list[tuple[int, int]] = []
    for interval in evidence.intervals:
        for index, shot_start in enumerate(shot_starts):
            shot_end = (
                shot_starts[index + 1] if index + 1 < len(shot_starts) else MAX_MATERIAL_DURATION_MS
            )
            start = max(interval.start_ms, shot_start)
            end = min(interval.end_ms, shot_end)
            if end - start >= duration_ms:
                candidates.append((start, start + duration_ms))
    return min(candidates) if candidates else None


def select_fitting_segments(
    slot: SegmentSelectionSlot,
    matches: SemanticSentenceMatches,
    materials: tuple[Material, ...],
    decodable_materials: tuple[VerifiedDecodableMaterial, ...],
) -> SegmentSelectionCandidates:
    """Return one earliest equal-length window per qualified fitting material."""

    if (
        not isinstance(slot, SegmentSelectionSlot)
        or not isinstance(matches, SemanticSentenceMatches)
        or slot.sequence != matches.sequence
        or not isinstance(materials, tuple)
        or not 1 <= len(materials) <= MAX_SEMANTIC_MATERIALS
        or not all(isinstance(material, Material) for material in materials)
        or len({material.material_id for material in materials}) != len(materials)
        or any(
            material.kind not in {MaterialKind.VIDEO, MaterialKind.IMAGE} for material in materials
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
        material.material_id for material in materials if material.kind is MaterialKind.VIDEO
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
        if material.kind is MaterialKind.IMAGE:
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
