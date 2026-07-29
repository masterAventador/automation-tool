"""One editing job: where a render is in its life, and why it stopped."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Final


class InvalidEditingJobTransition(ValueError):
    """An editing job transition is not part of the closed lifecycle graph."""

    def __init__(self) -> None:
        super().__init__("Editing job state transition is invalid")


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


__all__ = [
    "EditingJobStateMachine",
    "EditingJobStatus",
    "InvalidEditingJobTransition",
]
