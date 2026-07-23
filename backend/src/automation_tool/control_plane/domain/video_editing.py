"""Provider-neutral domain contracts for the standalone video editing module.

Video editing is an independent product module with its own lifecycle. It
reuses the shared timeline vocabulary (`TimelineTrack`, `TimelineClip`,
`TimelineTransition`) and stable identifiers (`ArtifactId`, `TimelineId`)
from the creation domain, but never nests into the RenderJob or PublishJob
state machines. Vendor DTOs (Aliyun IMS/ICE or any future provider) must
stay inside their adapters and never appear in this module.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Never, final

from automation_tool.control_plane.domain.resource_ids import ArtifactId, ResourceId
from automation_tool.control_plane.domain.video_creation import (
    MAX_ARTIFACT_REFERENCES,
    MAX_TRACKS,
    MAX_VIDEO_DURATION_MS,
    TimelineId,
    TimelineTrack,
    TimelineTrackKind,
)

MAX_EDITING_PROJECT_TITLE_CHARACTERS: Final = 200
MAX_EDITING_SOURCE_ARTIFACTS: Final = 256


class InvalidVideoEditingModel(ValueError):
    """A provider-neutral video editing domain value is invalid."""

    def __init__(self) -> None:
        super().__init__("Video editing domain model is invalid")


class InvalidEditingJobTransition(ValueError):
    """An editing job transition is not part of the closed lifecycle graph."""

    def __init__(self) -> None:
        super().__init__("Editing job state transition is invalid")


@final
class EditingProjectId(ResourceId):
    """Stable identifier for one standalone editing project."""

    __slots__ = ()
    _resource = "editing project"


@final
class EditingJobId(ResourceId):
    """Stable identifier for one provider-neutral editing job."""

    __slots__ = ()
    _resource = "editing job"


class EditingJobStatus(StrEnum):
    """Closed lifecycle states for one editing job."""

    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    CANCELLING = "cancelling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    OUTCOME_UNCERTAIN = "outcome_uncertain"


class EditingFailureCode(StrEnum):
    """Provider-neutral reasons why an editing job failed."""

    INVALID_INPUT = "invalid_input"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    EDITING_FAILED = "editing_failed"


EDITING_JOB_TERMINAL_STATUSES: Final[frozenset[EditingJobStatus]] = frozenset(
    {
        EditingJobStatus.SUCCEEDED,
        EditingJobStatus.FAILED,
        EditingJobStatus.CANCELLED,
        EditingJobStatus.OUTCOME_UNCERTAIN,
    }
)

_TRANSITIONS: Final[Mapping[EditingJobStatus, frozenset[EditingJobStatus]]] = MappingProxyType(
    {
        EditingJobStatus.QUEUED: frozenset(
            {
                EditingJobStatus.RUNNING,
                EditingJobStatus.CANCELLING,
                EditingJobStatus.FAILED,
            }
        ),
        EditingJobStatus.RUNNING: frozenset(
            {
                EditingJobStatus.PAUSED,
                EditingJobStatus.CANCELLING,
                EditingJobStatus.SUCCEEDED,
                EditingJobStatus.FAILED,
                EditingJobStatus.OUTCOME_UNCERTAIN,
            }
        ),
        EditingJobStatus.PAUSED: frozenset(
            {
                EditingJobStatus.RUNNING,
                EditingJobStatus.CANCELLING,
            }
        ),
        EditingJobStatus.CANCELLING: frozenset(
            {
                EditingJobStatus.SUCCEEDED,
                EditingJobStatus.FAILED,
                EditingJobStatus.CANCELLED,
                EditingJobStatus.OUTCOME_UNCERTAIN,
            }
        ),
        EditingJobStatus.SUCCEEDED: frozenset(),
        EditingJobStatus.FAILED: frozenset(),
        EditingJobStatus.CANCELLED: frozenset(),
        EditingJobStatus.OUTCOME_UNCERTAIN: frozenset(),
    }
)


class EditingJobStateMachine:
    """Stateless transition policy for the standalone editing job lifecycle."""

    @staticmethod
    def terminal_statuses() -> frozenset[EditingJobStatus]:
        return EDITING_JOB_TERMINAL_STATUSES

    @staticmethod
    def is_terminal(status: object) -> bool:
        return isinstance(status, EditingJobStatus) and status in EDITING_JOB_TERMINAL_STATUSES

    @staticmethod
    def allowed_targets(status: object) -> frozenset[EditingJobStatus]:
        if not isinstance(status, EditingJobStatus):
            raise InvalidEditingJobTransition
        return _TRANSITIONS[status]

    @staticmethod
    def can_transition(current: object, target: object) -> bool:
        return (
            isinstance(current, EditingJobStatus)
            and isinstance(target, EditingJobStatus)
            and target in _TRANSITIONS[current]
        )

    @staticmethod
    def transition(current: object, target: object) -> EditingJobStatus:
        if (
            not isinstance(current, EditingJobStatus)
            or not isinstance(target, EditingJobStatus)
            or target not in _TRANSITIONS[current]
        ):
            raise InvalidEditingJobTransition
        return target


def _reject() -> Never:
    raise InvalidVideoEditingModel


def _validate_title(value: object) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > MAX_EDITING_PROJECT_TITLE_CHARACTERS
    ):
        _reject()
    for character in value:
        if unicodedata.category(character).startswith("C"):
            _reject()


def _validate_timestamp(value: object) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != UTC.utcoffset(value)
    ):
        _reject()


def _validate_artifact_tuple(values: object, *, maximum: int) -> None:
    if not isinstance(values, tuple) or len(values) > maximum:
        _reject()
    if any(not isinstance(value, ArtifactId) for value in values) or len(set(values)) != len(
        values
    ):
        _reject()


def _validate_revision(value: object) -> None:
    if type(value) is not int or value < 1:
        _reject()


@dataclass(frozen=True, slots=True)
class EditingProject:
    """One user-owned editing workspace; never a vendor-side resource."""

    project_id: EditingProjectId
    title: str = field(repr=False)
    source_artifact_ids: tuple[ArtifactId, ...]
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.project_id, EditingProjectId):
            _reject()
        _validate_title(self.title)
        _validate_artifact_tuple(self.source_artifact_ids, maximum=MAX_EDITING_SOURCE_ARTIFACTS)
        _validate_timestamp(self.created_at)
        _validate_timestamp(self.updated_at)
        if self.updated_at < self.created_at:
            _reject()


@dataclass(frozen=True, slots=True)
class EditingTimeline:
    """One provider-neutral editing timeline revision anchored to a project.

    Tracks, clips, captions, audio, and transitions reuse the shared timeline
    vocabulary from the creation domain, so finished creation output can be
    handed to editing without translation and without a second vocabulary.
    """

    timeline_id: TimelineId
    project_id: EditingProjectId
    revision: int
    duration_ms: int
    tracks: tuple[TimelineTrack, ...]
    created_at: datetime

    def __post_init__(self) -> None:
        if type(self.timeline_id) is not TimelineId or not isinstance(
            self.project_id, EditingProjectId
        ):
            _reject()
        _validate_revision(self.revision)
        if (
            type(self.duration_ms) is not int
            or not 100 <= self.duration_ms <= MAX_VIDEO_DURATION_MS
            or not isinstance(self.tracks, tuple)
            or not 1 <= len(self.tracks) <= MAX_TRACKS
            or any(not isinstance(track, TimelineTrack) for track in self.tracks)
            or len({track.track_id for track in self.tracks}) != len(self.tracks)
            or not any(track.kind is TimelineTrackKind.VISUAL for track in self.tracks)
            or any(clip.end_ms > self.duration_ms for track in self.tracks for clip in track.clips)
        ):
            _reject()
        _validate_timestamp(self.created_at)


@dataclass(frozen=True, slots=True)
class EditingJob:
    """One editing execution over a frozen timeline revision.

    The job only references shared artifact identifiers; produced videos are
    registered as regular artifacts and may flow into publishing, but this
    lifecycle never nests into RenderJob or PublishJob state machines.
    """

    editing_job_id: EditingJobId
    project_id: EditingProjectId
    timeline_id: TimelineId
    timeline_revision: int
    status: EditingJobStatus
    input_artifact_ids: tuple[ArtifactId, ...]
    output_artifact_ids: tuple[ArtifactId, ...]
    failure_code: EditingFailureCode | None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if (
            not isinstance(self.editing_job_id, EditingJobId)
            or not isinstance(self.project_id, EditingProjectId)
            or type(self.timeline_id) is not TimelineId
            or not isinstance(self.status, EditingJobStatus)
            or (
                self.failure_code is not None
                and not isinstance(self.failure_code, EditingFailureCode)
            )
        ):
            _reject()
        _validate_revision(self.timeline_revision)
        _validate_artifact_tuple(self.input_artifact_ids, maximum=MAX_ARTIFACT_REFERENCES)
        _validate_artifact_tuple(self.output_artifact_ids, maximum=MAX_ARTIFACT_REFERENCES)
        if not self.input_artifact_ids:
            _reject()
        if set(self.input_artifact_ids) & set(self.output_artifact_ids):
            _reject()
        _validate_timestamp(self.created_at)
        _validate_timestamp(self.updated_at)
        if self.updated_at < self.created_at:
            _reject()

        if self.status is EditingJobStatus.SUCCEEDED:
            valid_facts = bool(self.output_artifact_ids) and self.failure_code is None
        elif self.status is EditingJobStatus.FAILED:
            valid_facts = not self.output_artifact_ids and self.failure_code is not None
        else:
            valid_facts = not self.output_artifact_ids and self.failure_code is None
        if not valid_facts:
            _reject()


__all__ = [
    "EDITING_JOB_TERMINAL_STATUSES",
    "MAX_EDITING_PROJECT_TITLE_CHARACTERS",
    "MAX_EDITING_SOURCE_ARTIFACTS",
    "EditingFailureCode",
    "EditingJob",
    "EditingJobId",
    "EditingJobStateMachine",
    "EditingJobStatus",
    "EditingProject",
    "EditingProjectId",
    "EditingTimeline",
    "InvalidEditingJobTransition",
    "InvalidVideoEditingModel",
]
