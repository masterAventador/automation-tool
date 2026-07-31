"""Versioned, path-free visual and caption values shared across process boundaries."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Never
from uuid import RFC_4122, UUID

from automation_tool.protocol.local_editing import SegmentSelectionMaterialKind

LOCAL_EDITING_VISUAL_RENDER_VERSION: Final = "local-editing.visual-render.v1"
LOCAL_EDITING_CAPTION_RENDER_VERSION: Final = "local-editing.caption-render.v1"
MIN_LOCAL_EDITING_OUTPUT_DIMENSION: Final = 128
MAX_LOCAL_EDITING_OUTPUT_DIMENSION: Final = 4096
MIN_LOCAL_EDITING_OUTPUT_FPS: Final = 12
MAX_LOCAL_EDITING_OUTPUT_FPS: Final = 60
MIN_LOCAL_EDITING_RENDER_DURATION_MS: Final = 100
MAX_LOCAL_EDITING_RENDER_DURATION_MS: Final = 600_000
MAX_LOCAL_EDITING_RENDER_CLIPS: Final = 512
MAX_LOCAL_EDITING_TRANSITION_DURATION_MS: Final = 10_000
MAX_LOCAL_EDITING_SOURCE_DURATION_MS: Final = 4 * 60 * 60 * 1000
MAX_LOCAL_EDITING_CAPTION_CUES: Final = 512
MAX_LOCAL_EDITING_CAPTION_TEXT_CHARACTERS: Final = 2_000
MIN_LOCAL_EDITING_CAPTION_FONT_PX: Final = 12
MAX_LOCAL_EDITING_CAPTION_FONT_PX: Final = 200
MAX_LOCAL_EDITING_CAPTION_STROKE_PX: Final = 20
MIN_LOCAL_EDITING_CAPTION_LINE_SPACING: Final = 1.0
MAX_LOCAL_EDITING_CAPTION_LINE_SPACING: Final = 3.0

_FONT_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,63}\Z")


class LocalEditingVisualRenderRejected(ValueError):
    """A visual render wire value is invalid."""

    def __init__(self) -> None:
        super().__init__("local visual render plan rejected")


class LocalEditingCaptionRenderRejected(ValueError):
    """A caption render wire value is invalid."""

    def __init__(self) -> None:
        super().__init__("local caption render plan rejected")


class LocalEditingVisualTransitionKind(StrEnum):
    FADE = "fade"
    DISSOLVE = "dissolve"
    WIPE = "wipe"


def _reject() -> Never:
    raise LocalEditingVisualRenderRejected from None


def _reject_caption() -> Never:
    raise LocalEditingCaptionRenderRejected from None


def _is_canonical_uuid4(value: object) -> bool:
    return (
        isinstance(value, UUID)
        and value.variant == RFC_4122
        and value.version == 4
        and value.int != 0
    )


@dataclass(frozen=True, slots=True)
class LocalEditingCaptionRenderStyle:
    """Path-free caption appearance, revalidated by the Local Executor."""

    font_key: str
    font_px: int
    stroke_px: int
    line_spacing: float

    def __post_init__(self) -> None:
        if (
            not isinstance(self.font_key, str)
            or _FONT_KEY_PATTERN.fullmatch(self.font_key) is None
            or type(self.font_px) is not int
            or not MIN_LOCAL_EDITING_CAPTION_FONT_PX
            <= self.font_px
            <= MAX_LOCAL_EDITING_CAPTION_FONT_PX
            or type(self.stroke_px) is not int
            or not 0 <= self.stroke_px <= MAX_LOCAL_EDITING_CAPTION_STROKE_PX
            or type(self.line_spacing) is not float
            or not MIN_LOCAL_EDITING_CAPTION_LINE_SPACING
            <= self.line_spacing
            <= MAX_LOCAL_EDITING_CAPTION_LINE_SPACING
            or self.stroke_px * 2 >= self.font_px
        ):
            _reject_caption()


def _valid_caption_text(value: object) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > MAX_LOCAL_EDITING_CAPTION_TEXT_CHARACTERS
    ):
        return False
    return all(
        character in {"\n", "\t"} or not unicodedata.category(character).startswith("C")
        for character in value
    )


@dataclass(frozen=True, slots=True, repr=False)
class LocalEditingCaptionRenderCue:
    """One caption's private text and absolute path-free timeline window."""

    sequence: int
    start_ms: int
    duration_ms: int
    text: str

    def __post_init__(self) -> None:
        if (
            type(self.sequence) is not int
            or not 1 <= self.sequence <= MAX_LOCAL_EDITING_CAPTION_CUES
            or type(self.start_ms) is not int
            or self.start_ms < 0
            or type(self.duration_ms) is not int
            or not 1 <= self.duration_ms <= MAX_LOCAL_EDITING_RENDER_DURATION_MS
            or self.start_ms + self.duration_ms > MAX_LOCAL_EDITING_RENDER_DURATION_MS
            or not _valid_caption_text(self.text)
        ):
            _reject_caption()

    @property
    def end_ms(self) -> int:
        return self.start_ms + self.duration_ms

    def __repr__(self) -> str:
        return "LocalEditingCaptionRenderCue(<redacted>)"


def _validated_caption_cue(
    cue: LocalEditingCaptionRenderCue,
) -> LocalEditingCaptionRenderCue:
    try:
        return LocalEditingCaptionRenderCue(
            sequence=cue.sequence,
            start_ms=cue.start_ms,
            duration_ms=cue.duration_ms,
            text=cue.text,
        )
    except Exception:
        _reject_caption()


@dataclass(frozen=True, slots=True, repr=False)
class LocalEditingCaptionRenderPlan:
    """One complete path-free caption request for a matching visual plan."""

    project_id: UUID
    timeline_id: UUID
    timeline_revision: int
    output_width: int
    output_height: int
    output_fps: int
    duration_ms: int
    style: LocalEditingCaptionRenderStyle
    cues: tuple[LocalEditingCaptionRenderCue, ...]

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
            or not isinstance(self.style, LocalEditingCaptionRenderStyle)
            or self.style.font_px > self.output_height
            or not isinstance(self.cues, tuple)
            or len(self.cues) > MAX_LOCAL_EDITING_CAPTION_CUES
            or not all(isinstance(cue, LocalEditingCaptionRenderCue) for cue in self.cues)
        ):
            _reject_caption()
        try:
            style = LocalEditingCaptionRenderStyle(
                font_key=self.style.font_key,
                font_px=self.style.font_px,
                stroke_px=self.style.stroke_px,
                line_spacing=self.style.line_spacing,
            )
            cues = tuple(_validated_caption_cue(cue) for cue in self.cues)
        except Exception:
            _reject_caption()
        if tuple(cue.sequence for cue in cues) != tuple(range(1, len(cues) + 1)):
            _reject_caption()
        previous_end = 0
        for cue in cues:
            if cue.start_ms < previous_end or cue.end_ms > self.duration_ms:
                _reject_caption()
            previous_end = cue.end_ms
        object.__setattr__(self, "style", style)
        object.__setattr__(self, "cues", cues)

    @staticmethod
    def _valid_output_side(value: object) -> bool:
        return (
            type(value) is int
            and MIN_LOCAL_EDITING_OUTPUT_DIMENSION <= value <= MAX_LOCAL_EDITING_OUTPUT_DIMENSION
            and value % 2 == 0
        )

    @property
    def version(self) -> str:
        return LOCAL_EDITING_CAPTION_RENDER_VERSION

    def __repr__(self) -> str:
        return "LocalEditingCaptionRenderPlan(<redacted>)"


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
            or not isinstance(self.kind, SegmentSelectionMaterialKind)
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
    "LOCAL_EDITING_CAPTION_RENDER_VERSION",
    "LOCAL_EDITING_VISUAL_RENDER_VERSION",
    "MAX_LOCAL_EDITING_CAPTION_CUES",
    "MAX_LOCAL_EDITING_CAPTION_FONT_PX",
    "MAX_LOCAL_EDITING_CAPTION_LINE_SPACING",
    "MAX_LOCAL_EDITING_CAPTION_STROKE_PX",
    "MAX_LOCAL_EDITING_CAPTION_TEXT_CHARACTERS",
    "MAX_LOCAL_EDITING_OUTPUT_DIMENSION",
    "MAX_LOCAL_EDITING_OUTPUT_FPS",
    "MAX_LOCAL_EDITING_RENDER_CLIPS",
    "MAX_LOCAL_EDITING_RENDER_DURATION_MS",
    "MAX_LOCAL_EDITING_SOURCE_DURATION_MS",
    "MAX_LOCAL_EDITING_TRANSITION_DURATION_MS",
    "MIN_LOCAL_EDITING_CAPTION_FONT_PX",
    "MIN_LOCAL_EDITING_CAPTION_LINE_SPACING",
    "MIN_LOCAL_EDITING_OUTPUT_DIMENSION",
    "MIN_LOCAL_EDITING_OUTPUT_FPS",
    "MIN_LOCAL_EDITING_RENDER_DURATION_MS",
    "LocalEditingCaptionRenderCue",
    "LocalEditingCaptionRenderPlan",
    "LocalEditingCaptionRenderRejected",
    "LocalEditingCaptionRenderStyle",
    "LocalEditingVisualRenderClip",
    "LocalEditingVisualRenderPlan",
    "LocalEditingVisualRenderRejected",
    "LocalEditingVisualTransitionKind",
]
