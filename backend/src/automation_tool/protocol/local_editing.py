"""Versioned, path-free values shared by local smart-editing boundaries."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, NoReturn
from uuid import RFC_4122, UUID

LOCAL_EDITING_SEGMENT_SELECTION_VERSION: Final = "local-editing.segment-selection.v1"
MAX_LOCAL_EDITING_MATERIAL_DURATION_MS: Final = 4 * 60 * 60 * 1_000
MAX_LOCAL_EDITING_SHOT_BOUNDARIES: Final = 4_096
MAX_LOCAL_EDITING_TIMELINE_DURATION_MS: Final = 600_000
MAX_LOCAL_EDITING_SCRIPT_SENTENCES: Final = 128
MAX_LOCAL_EDITING_SEMANTIC_MATERIALS: Final = 32
LOCAL_EDITING_SEMANTIC_SCORE_THRESHOLD: Final = 60

_SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}\Z")


class LocalEditingProtocolRejected(ValueError):
    """A local-editing value is unsafe or not canonical across the boundary."""

    def __init__(self) -> None:
        super().__init__("local editing protocol value is invalid")


def _reject() -> NoReturn:
    raise LocalEditingProtocolRejected from None


def is_canonical_local_editing_material_id(value: object) -> bool:
    """Return whether a value is the exact UUIDv4 identity used on this wire."""

    return (
        isinstance(value, UUID)
        and value.version == 4
        and value.variant == RFC_4122
        and str(UUID(str(value))) == str(value)
    )


class SegmentSelectionMaterialKind(StrEnum):
    VIDEO = "video"
    IMAGE = "image"
    AUDIO = "audio"


@dataclass(frozen=True, slots=True)
class SegmentSelectionMaterial:
    """Only path-free material facts needed to choose a source window."""

    material_id: UUID
    kind: SegmentSelectionMaterialKind
    duration_ms: int | None
    content_digest: str
    shot_boundaries_ms: tuple[int, ...]

    def __post_init__(self) -> None:
        if (
            not is_canonical_local_editing_material_id(self.material_id)
            or not isinstance(self.kind, SegmentSelectionMaterialKind)
            or (self.kind is SegmentSelectionMaterialKind.IMAGE and self.duration_ms is not None)
            or (
                self.kind is not SegmentSelectionMaterialKind.IMAGE
                and (
                    type(self.duration_ms) is not int
                    or not 1 <= self.duration_ms <= MAX_LOCAL_EDITING_MATERIAL_DURATION_MS
                )
            )
            or type(self.content_digest) is not str
            or _SHA256_PATTERN.fullmatch(self.content_digest) is None
            or not isinstance(self.shot_boundaries_ms, tuple)
            or len(self.shot_boundaries_ms) > MAX_LOCAL_EDITING_SHOT_BOUNDARIES
            or (
                self.kind is not SegmentSelectionMaterialKind.VIDEO
                and bool(self.shot_boundaries_ms)
            )
        ):
            _reject()
        previous = -1
        for boundary in self.shot_boundaries_ms:
            if (
                type(boundary) is not int
                or boundary <= previous
                or not 0 <= boundary < MAX_LOCAL_EDITING_MATERIAL_DURATION_MS
            ):
                _reject()
            previous = boundary

    @property
    def version(self) -> str:
        return LOCAL_EDITING_SEGMENT_SELECTION_VERSION


@dataclass(frozen=True, slots=True)
class SegmentSelectionCandidateScore:
    """One T1 score projected without a Control Plane implementation type."""

    material_id: UUID
    score: int
    qualified: bool

    def __post_init__(self) -> None:
        if (
            not is_canonical_local_editing_material_id(self.material_id)
            or type(self.score) is not int
            or not 0 <= self.score <= 100
            or type(self.qualified) is not bool
            or self.qualified is not (self.score >= LOCAL_EDITING_SEMANTIC_SCORE_THRESHOLD)
        ):
            _reject()


@dataclass(frozen=True, slots=True)
class SegmentSelectionSentenceMatches:
    """One complete T1 ranking in its already resolved deterministic order."""

    sequence: int
    candidates: tuple[SegmentSelectionCandidateScore, ...]

    def __post_init__(self) -> None:
        if (
            type(self.sequence) is not int
            or not 1 <= self.sequence <= MAX_LOCAL_EDITING_SCRIPT_SENTENCES
            or not isinstance(self.candidates, tuple)
            or not 1 <= len(self.candidates) <= MAX_LOCAL_EDITING_SEMANTIC_MATERIALS
            or not all(
                isinstance(candidate, SegmentSelectionCandidateScore)
                for candidate in self.candidates
            )
            or len({candidate.material_id for candidate in self.candidates}) != len(self.candidates)
            or any(
                earlier.score < later.score
                for earlier, later in zip(
                    self.candidates,
                    self.candidates[1:],
                    strict=False,
                )
            )
        ):
            _reject()

    @property
    def version(self) -> str:
        return LOCAL_EDITING_SEGMENT_SELECTION_VERSION


__all__ = [
    "LOCAL_EDITING_SEGMENT_SELECTION_VERSION",
    "LOCAL_EDITING_SEMANTIC_SCORE_THRESHOLD",
    "MAX_LOCAL_EDITING_MATERIAL_DURATION_MS",
    "MAX_LOCAL_EDITING_SCRIPT_SENTENCES",
    "MAX_LOCAL_EDITING_SEMANTIC_MATERIALS",
    "MAX_LOCAL_EDITING_SHOT_BOUNDARIES",
    "MAX_LOCAL_EDITING_TIMELINE_DURATION_MS",
    "LocalEditingProtocolRejected",
    "SegmentSelectionCandidateScore",
    "SegmentSelectionMaterial",
    "SegmentSelectionMaterialKind",
    "SegmentSelectionSentenceMatches",
    "is_canonical_local_editing_material_id",
]
