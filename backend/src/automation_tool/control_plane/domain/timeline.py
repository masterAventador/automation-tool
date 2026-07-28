"""Local editing timeline: what plays when, taken from where, at what level."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Never, final

from automation_tool.control_plane.domain.material import (
    MAX_MATERIAL_DURATION_MS,
    MaterialId,
)
from automation_tool.control_plane.domain.resource_ids import ResourceId

MAX_TIMELINE_DURATION_MS: Final = 600_000
MIN_TIMELINE_DURATION_MS: Final = 100
MAX_CLIPS_PER_TRACK: Final = 512
MAX_TRANSITION_DURATION_MS: Final = 10_000
MAX_CLIP_TEXT_CHARACTERS: Final = 2_000
MIN_GAIN_DB: Final = -60.0
MAX_GAIN_DB: Final = 12.0

_LOCAL_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


class InvalidTimelineModel(ValueError):
    """A timeline domain value is invalid."""

    def __init__(self) -> None:
        super().__init__("Timeline model is invalid")


@final
class TimelineId(ResourceId):
    """Stable identifier for one timeline lineage."""

    __slots__ = ()
    _resource = "timeline"


class TimelineTrackKind(StrEnum):
    """One lane of the film. The three audio lanes mix differently.

    `NARRATION` drives the ducking sidechain, `AMBIENT` and `MUSIC` get
    ducked by it — one `AUDIO` lane could not say which was which.
    """

    VISUAL = "visual"
    NARRATION = "narration"
    AMBIENT = "ambient"
    MUSIC = "music"
    CAPTION = "caption"


AUDIBLE_TRACK_KINDS: Final = frozenset(
    {TimelineTrackKind.NARRATION, TimelineTrackKind.AMBIENT, TimelineTrackKind.MUSIC}
)


class TransitionKind(StrEnum):
    """How one visual clip gives way to the next.

    A hard cut is the absence of a transition — `transition_in=None` — so
    there is deliberately no `CUT` member: two spellings of one state is
    how they drift apart.
    """

    FADE = "fade"
    DISSOLVE = "dissolve"
    WIPE = "wipe"


def _reject() -> Never:
    raise InvalidTimelineModel


def _validate_text(value: object, *, maximum: int, optional: bool = False) -> None:
    if value is None and optional:
        return
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > maximum:
        _reject()
    for character in value:
        if character in {"\n", "\t"}:
            continue
        if unicodedata.category(character).startswith("C"):
            _reject()


@dataclass(frozen=True, slots=True)
class TimelineTransition:
    kind: TransitionKind
    duration_ms: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.kind, TransitionKind)
            or type(self.duration_ms) is not int
            or not 1 <= self.duration_ms <= MAX_TRANSITION_DURATION_MS
        ):
            _reject()


@dataclass(frozen=True, slots=True)
class TimelineClip:
    """One thing happening on one lane, for one stretch of the film."""

    clip_id: str
    start_ms: int
    duration_ms: int
    source_material_id: MaterialId | None
    source_in_ms: int | None
    source_out_ms: int | None
    text: str | None
    gain_db: float | None
    transition_in: TimelineTransition | None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.clip_id, str)
            or _LOCAL_ID_PATTERN.fullmatch(self.clip_id) is None
            or type(self.start_ms) is not int
            or self.start_ms < 0
            or type(self.duration_ms) is not int
            or not 1 <= self.duration_ms <= MAX_TIMELINE_DURATION_MS
            or (
                self.source_material_id is not None
                and not isinstance(self.source_material_id, MaterialId)
            )
            or (
                self.transition_in is not None
                and not isinstance(self.transition_in, TimelineTransition)
            )
        ):
            _reject()
        _validate_text(self.text, maximum=MAX_CLIP_TEXT_CHARACTERS, optional=True)
        if (self.source_material_id is None) == (self.text is None):
            _reject()
        self._validate_source_window()
        self._validate_gain()
        self._validate_transition()

    def _validate_source_window(self) -> None:
        """Where in the source this slice comes from — at both ends or neither.

        Omitting it means the source has no time axis of its own: a still
        image occupies `duration_ms` without any stretch of it being taken.
        """
        if (self.source_in_ms is None) != (self.source_out_ms is None):
            _reject()
        if self.source_in_ms is None:
            return
        if self.source_material_id is None:
            _reject()
        source_in = self.source_in_ms
        source_out = self.source_out_ms
        if type(source_in) is not int or type(source_out) is not int:
            _reject()
        if source_in < 0 or source_out > MAX_MATERIAL_DURATION_MS:
            _reject()
        if source_out - source_in != self.duration_ms:
            _reject()

    def _validate_gain(self) -> None:
        """Gain adjusts a clip's own audio — nothing to adjust without one.

        A caption plays no audio at all, and an omitted source window means
        the source has no time axis (a still image) to carry sound either.
        Both cases must state `gain_db=None`.
        """
        if self.gain_db is None:
            return
        if self.source_in_ms is None:
            _reject()
        if type(self.gain_db) is not float or not MIN_GAIN_DB <= self.gain_db <= MAX_GAIN_DB:
            _reject()

    def _validate_transition(self) -> None:
        """A transition may not cover the whole clip — it would never play on its own.

        This only guards the half the clip itself has enough information to
        judge. Not swallowing the *previous* clip is the track's problem
        (`TimelineTrack`, T3), since that needs a neighbour to compare against.
        """
        if self.transition_in is None:
            return
        if self.transition_in.duration_ms >= self.duration_ms:
            _reject()

    @property
    def end_ms(self) -> int:
        return self.start_ms + self.duration_ms


__all__ = [
    "AUDIBLE_TRACK_KINDS",
    "MAX_CLIPS_PER_TRACK",
    "MAX_CLIP_TEXT_CHARACTERS",
    "MAX_GAIN_DB",
    "MAX_TIMELINE_DURATION_MS",
    "MAX_TRANSITION_DURATION_MS",
    "MIN_GAIN_DB",
    "MIN_TIMELINE_DURATION_MS",
    "InvalidTimelineModel",
    "TimelineClip",
    "TimelineId",
    "TimelineTrackKind",
    "TimelineTransition",
    "TransitionKind",
]
