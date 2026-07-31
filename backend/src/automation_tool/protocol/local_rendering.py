"""Versioned, path-free visual rendering values shared across process boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Never
from uuid import RFC_4122, UUID

from automation_tool.protocol.local_editing import SegmentSelectionMaterialKind

LOCAL_EDITING_VISUAL_RENDER_VERSION: Final = "local-editing.visual-render.v1"
MIN_LOCAL_EDITING_OUTPUT_DIMENSION: Final = 128
MAX_LOCAL_EDITING_OUTPUT_DIMENSION: Final = 4096
MIN_LOCAL_EDITING_OUTPUT_FPS: Final = 12
MAX_LOCAL_EDITING_OUTPUT_FPS: Final = 60
MIN_LOCAL_EDITING_RENDER_DURATION_MS: Final = 100
MAX_LOCAL_EDITING_RENDER_DURATION_MS: Final = 600_000
MAX_LOCAL_EDITING_RENDER_CLIPS: Final = 512
MAX_LOCAL_EDITING_TRANSITION_DURATION_MS: Final = 10_000
MAX_LOCAL_EDITING_SOURCE_DURATION_MS: Final = 4 * 60 * 60 * 1000


class LocalEditingVisualRenderRejected(ValueError):
    """A visual render wire value is invalid."""

    def __init__(self) -> None:
        super().__init__("local visual render plan rejected")


class LocalEditingVisualTransitionKind(StrEnum):
    FADE = "fade"
    DISSOLVE = "dissolve"
    WIPE = "wipe"


def _reject() -> Never:
    raise LocalEditingVisualRenderRejected from None


def _is_canonical_uuid4(value: object) -> bool:
    return (
        isinstance(value, UUID)
        and value.variant == RFC_4122
        and value.version == 4
        and value.int != 0
    )


@dataclass(frozen=True, slots=True)
class LocalEditingVisualRenderClip:
    """One path-free visual clip ready for Local Executor frame projection."""

    sequence: int
    material_id: UUID
    kind: SegmentSelectionMaterialKind
    start_ms: int
    duration_ms: int
    source_in_ms: int | None
    source_out_ms: int | None
    transition_kind: LocalEditingVisualTransitionKind | None
    transition_duration_ms: int | None

    def __post_init__(self) -> None:
        if (
            type(self.sequence) is not int
            or not 1 <= self.sequence <= MAX_LOCAL_EDITING_RENDER_CLIPS
            or not _is_canonical_uuid4(self.material_id)
            or self.kind
            not in {SegmentSelectionMaterialKind.VIDEO, SegmentSelectionMaterialKind.IMAGE}
            or type(self.start_ms) is not int
            or self.start_ms < 0
            or type(self.duration_ms) is not int
            or not 1 <= self.duration_ms <= MAX_LOCAL_EDITING_RENDER_DURATION_MS
            or self.start_ms + self.duration_ms > MAX_LOCAL_EDITING_RENDER_DURATION_MS
        ):
            _reject()
        self._validate_source_window()
        self._validate_transition()

    def _validate_source_window(self) -> None:
        if self.kind is SegmentSelectionMaterialKind.IMAGE:
            if self.source_in_ms is not None or self.source_out_ms is not None:
                _reject()
            return
        if type(self.source_in_ms) is not int or type(self.source_out_ms) is not int:
            _reject()
        if (
            self.source_in_ms < 0
            or self.source_out_ms > MAX_LOCAL_EDITING_SOURCE_DURATION_MS
            or self.source_out_ms - self.source_in_ms != self.duration_ms
        ):
            _reject()

    def _validate_transition(self) -> None:
        if (self.transition_kind is None) != (self.transition_duration_ms is None):
            _reject()
        if self.transition_kind is None:
            return
        if (
            not isinstance(self.transition_kind, LocalEditingVisualTransitionKind)
            or type(self.transition_duration_ms) is not int
            or not 1 <= self.transition_duration_ms <= MAX_LOCAL_EDITING_TRANSITION_DURATION_MS
            or self.transition_duration_ms >= self.duration_ms
        ):
            _reject()

    @property
    def end_ms(self) -> int:
        return self.start_ms + self.duration_ms


def _validated_clip(clip: LocalEditingVisualRenderClip) -> LocalEditingVisualRenderClip:
    try:
        return LocalEditingVisualRenderClip(
            sequence=clip.sequence,
            material_id=clip.material_id,
            kind=clip.kind,
            start_ms=clip.start_ms,
            duration_ms=clip.duration_ms,
            source_in_ms=clip.source_in_ms,
            source_out_ms=clip.source_out_ms,
            transition_kind=clip.transition_kind,
            transition_duration_ms=clip.transition_duration_ms,
        )
    except Exception:
        _reject()


@dataclass(frozen=True, slots=True)
class LocalEditingVisualRenderPlan:
    """One complete visual render request with no machine-local metadata."""

    project_id: UUID
    timeline_id: UUID
    timeline_revision: int
    output_width: int
    output_height: int
    output_fps: int
    duration_ms: int
    clips: tuple[LocalEditingVisualRenderClip, ...]

    def __post_init__(self) -> None:
        if (
            not _is_canonical_uuid4(self.project_id)
            or not _is_canonical_uuid4(self.timeline_id)
            or type(self.timeline_revision) is not int
            or self.timeline_revision < 1
            or not self._valid_output_side(self.output_width)
            or not self._valid_output_side(self.output_height)
            or type(self.output_fps) is not int
            or not MIN_LOCAL_EDITING_OUTPUT_FPS <= self.output_fps <= MAX_LOCAL_EDITING_OUTPUT_FPS
            or type(self.duration_ms) is not int
            or not MIN_LOCAL_EDITING_RENDER_DURATION_MS
            <= self.duration_ms
            <= MAX_LOCAL_EDITING_RENDER_DURATION_MS
            or not isinstance(self.clips, tuple)
            or not 1 <= len(self.clips) <= MAX_LOCAL_EDITING_RENDER_CLIPS
            or not all(isinstance(clip, LocalEditingVisualRenderClip) for clip in self.clips)
        ):
            _reject()
        validated = tuple(_validated_clip(clip) for clip in self.clips)
        if tuple(clip.sequence for clip in validated) != tuple(range(1, len(validated) + 1)):
            _reject()
        self._validate_layout(validated)
        object.__setattr__(self, "clips", validated)

    @staticmethod
    def _valid_output_side(value: object) -> bool:
        return (
            type(value) is int
            and MIN_LOCAL_EDITING_OUTPUT_DIMENSION <= value <= MAX_LOCAL_EDITING_OUTPUT_DIMENSION
            and value % 2 == 0
        )

    def _validate_layout(self, clips: tuple[LocalEditingVisualRenderClip, ...]) -> None:
        previous_end = 0
        previous_tail = 0
        for clip in clips:
            overlap = 0 if clip.transition_duration_ms is None else clip.transition_duration_ms
            if clip.transition_kind is not None and overlap >= previous_tail:
                _reject()
            if clip.start_ms != previous_end - overlap:
                _reject()
            previous_tail = clip.duration_ms - overlap
            previous_end = clip.end_ms
        if previous_end != self.duration_ms:
            _reject()

    @property
    def version(self) -> str:
        return LOCAL_EDITING_VISUAL_RENDER_VERSION


__all__ = [
    "LOCAL_EDITING_VISUAL_RENDER_VERSION",
    "MAX_LOCAL_EDITING_OUTPUT_DIMENSION",
    "MAX_LOCAL_EDITING_OUTPUT_FPS",
    "MAX_LOCAL_EDITING_RENDER_CLIPS",
    "MAX_LOCAL_EDITING_RENDER_DURATION_MS",
    "MAX_LOCAL_EDITING_SOURCE_DURATION_MS",
    "MAX_LOCAL_EDITING_TRANSITION_DURATION_MS",
    "MIN_LOCAL_EDITING_OUTPUT_DIMENSION",
    "MIN_LOCAL_EDITING_OUTPUT_FPS",
    "MIN_LOCAL_EDITING_RENDER_DURATION_MS",
    "LocalEditingVisualRenderClip",
    "LocalEditingVisualRenderPlan",
    "LocalEditingVisualRenderRejected",
    "LocalEditingVisualTransitionKind",
]
