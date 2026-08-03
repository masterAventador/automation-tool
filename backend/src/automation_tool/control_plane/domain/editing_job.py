"""One editing job: where a render is in its life, and why it stopped."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Never, final

from automation_tool.control_plane.domain.editing_project import EditingProjectId
from automation_tool.control_plane.domain.resource_ids import ArtifactId, ResourceId
from automation_tool.control_plane.domain.timeline import TimelineId


class InvalidEditingJobTransition(ValueError):
    """An editing job transition is not part of the closed lifecycle graph."""

    def __init__(self) -> None:
        super().__init__("Editing job state transition is invalid")


class InvalidEditingJobModel(ValueError):
    """An editing job domain value is invalid."""

    def __init__(self) -> None:
        super().__init__("Editing job model is invalid")


def _reject() -> Never:
    raise InvalidEditingJobModel


def _validate_timestamp(value: object) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != UTC.utcoffset(value)
    ):
        _reject()


class EditingJobStatus(StrEnum):
    """Where one render is in its life.

    Six states, deliberately. There is no PAUSED — a 5-55 second local
    render has no pause story. There is no OUTCOME_UNCERTAIN either: that
    state exists for platform side effects nobody can re-read, whereas the
    output file here is ours to inspect, and a half-written mp4 is simply
    a failure to delete.
    """

    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


_TERMINAL_STATUSES: Final[frozenset[EditingJobStatus]] = frozenset(
    {EditingJobStatus.SUCCEEDED, EditingJobStatus.FAILED, EditingJobStatus.CANCELLED}
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
        # No way back to QUEUED: ffmpeg has no checkpoint, so a render that
        # lost its worker cannot resume. Re-running it is a new job.
        EditingJobStatus.RUNNING: frozenset(
            {
                EditingJobStatus.CANCELLING,
                EditingJobStatus.SUCCEEDED,
                EditingJobStatus.FAILED,
            }
        ),
        # Cancellation is cooperative: the request can race a render that
        # already finished or already failed, so both remain reachable.
        EditingJobStatus.CANCELLING: frozenset(
            {
                EditingJobStatus.SUCCEEDED,
                EditingJobStatus.FAILED,
                EditingJobStatus.CANCELLED,
            }
        ),
        EditingJobStatus.SUCCEEDED: frozenset(),
        EditingJobStatus.FAILED: frozenset(),
        EditingJobStatus.CANCELLED: frozenset(),
    }
)


class EditingJobStateMachine:
    """Stateless transition policy for editing jobs."""

    @staticmethod
    def terminal_statuses() -> frozenset[EditingJobStatus]:
        return _TERMINAL_STATUSES

    @staticmethod
    def is_terminal(status: object) -> bool:
        return isinstance(status, EditingJobStatus) and status in _TERMINAL_STATUSES

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


@final
class EditingJobId(ResourceId):
    """Stable identifier for one editing job."""

    __slots__ = ()
    _resource = "editing job"


class EditingJobFailureCode(StrEnum):
    """Why a render stopped, grouped by what the user can do about it."""

    INVALID_TIMELINE = "invalid_timeline"
    MATERIAL_UNAVAILABLE = "material_unavailable"
    MATERIAL_UNSUPPORTED = "material_unsupported"
    FONT_UNAVAILABLE = "font_unavailable"
    RENDER_FAILED = "render_failed"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    PERMISSION_DENIED = "permission_denied"
    WORKER_LOST = "worker_lost"


@dataclass(frozen=True, slots=True)
class EditingJob:
    """One render of one timeline revision, and where it got to.

    Who acks a never-dispatched job out of CANCELLING: a job can enter
    CANCELLING (from QUEUED) before any worker ever claimed it. Nothing in
    this module ever calls `confirm_cancelled` on such a job by itself —
    that responsibility belongs to whichever component would otherwise
    have dispatched it (the scheduler, LE-12), not to this domain layer:

    - If the scheduler notices the race at dispatch time (it is about to
      hand a QUEUED job to a worker and sees it is already CANCELLING),
      it must call `confirm_cancelled` itself instead of dispatching —
      it alone holds the fact "no worker was ever handed this job", so no
      external confirmation is needed.
    - If cancellation was requested and the scheduler crashed or never
      revisited that job (so neither `confirm_cancelled` nor `succeed`/
      `fail` is ever called), the job would otherwise be stuck in
      CANCELLING forever. That is not a deadlock: `CANCELLING -> FAILED`
      is a legal edge (see `EditingJobStateMachine`), so a reconciliation
      pass with a cancellation timeout must resolve an abandoned
      CANCELLING job to FAILED with `EditingJobFailureCode.WORKER_LOST` —
      there is no OUTCOME_UNCERTAIN state here to fall back to (see
      `EditingJobStatus`), and a local render has no partial result worth
      preserving in that case.

    Implementing that scheduler/reconciliation logic is out of scope here
    (LE-12); this docstring only fixes who is responsible so the gap does
    not silently fall between tasks.
    """

    job_id: EditingJobId
    project_id: EditingProjectId
    timeline_id: TimelineId
    timeline_revision: int
    status: EditingJobStatus
    failure_code: EditingJobFailureCode | None
    output_artifact_id: ArtifactId | None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if (
            not isinstance(self.job_id, EditingJobId)
            or not isinstance(self.project_id, EditingProjectId)
            or not isinstance(self.timeline_id, TimelineId)
            or type(self.timeline_revision) is not int
            or self.timeline_revision < 1
            or not isinstance(self.status, EditingJobStatus)
            or (
                self.failure_code is not None
                and not isinstance(self.failure_code, EditingJobFailureCode)
            )
            or (
                self.output_artifact_id is not None
                and not isinstance(self.output_artifact_id, ArtifactId)
            )
        ):
            _reject()
        _validate_timestamp(self.created_at)
        _validate_timestamp(self.updated_at)
        if self.updated_at < self.created_at:
            _reject()
        self._validate_facts_match_status()

    def _validate_facts_match_status(self) -> None:
        """Every state says exactly which facts it must and must not carry."""
        if self.status is EditingJobStatus.SUCCEEDED:
            allowed = self.output_artifact_id is not None and self.failure_code is None
        elif self.status is EditingJobStatus.FAILED:
            allowed = self.output_artifact_id is None and self.failure_code is not None
        else:
            allowed = self.output_artifact_id is None and self.failure_code is None
        if not allowed:
            _reject()

    def _moved_to(
        self, status: EditingJobStatus, updated_at: datetime, **facts: object
    ) -> EditingJob:
        EditingJobStateMachine.transition(self.status, status)
        _validate_timestamp(updated_at)
        if updated_at < self.updated_at:
            _reject()
        return replace(self, status=status, updated_at=updated_at, **facts)  # type: ignore[arg-type]

    def start(self, updated_at: datetime) -> EditingJob:
        return self._moved_to(EditingJobStatus.RUNNING, updated_at)

    def request_cancel(self, updated_at: datetime) -> EditingJob:
        return self._moved_to(EditingJobStatus.CANCELLING, updated_at)

    def succeed(self, output_artifact_id: ArtifactId, updated_at: datetime) -> EditingJob:
        return self._moved_to(
            EditingJobStatus.SUCCEEDED, updated_at, output_artifact_id=output_artifact_id
        )

    def fail(self, failure_code: EditingJobFailureCode, updated_at: datetime) -> EditingJob:
        return self._moved_to(EditingJobStatus.FAILED, updated_at, failure_code=failure_code)

    def confirm_cancelled(self, updated_at: datetime) -> EditingJob:
        return self._moved_to(EditingJobStatus.CANCELLED, updated_at)


__all__ = [
    "EditingJob",
    "EditingJobFailureCode",
    "EditingJobId",
    "EditingJobStateMachine",
    "EditingJobStatus",
    "InvalidEditingJobModel",
    "InvalidEditingJobTransition",
]
