"""Pure Control Plane task lifecycle and explicit transition matrix."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Final


class TaskStatus(StrEnum):
    """Persisted and public lifecycle states for one task."""

    DRAFT = "draft"
    VALIDATING = "validating"
    AWAITING_DEVICE = "awaiting_device"
    AWAITING_PLATFORM_LOGIN = "awaiting_platform_login"
    DISCOVERING_TARGETS = "discovering_targets"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    AWAITING_HUMAN = "awaiting_human"
    CANCELLING = "cancelling"
    SUCCEEDED = "succeeded"
    PARTIALLY_SUCCEEDED = "partially_succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    OUTCOME_UNCERTAIN = "outcome_uncertain"


class InvalidTaskTransition(ValueError):
    """A task transition is not part of the closed lifecycle graph."""

    def __init__(self) -> None:
        super().__init__("Task state transition is invalid")


_TERMINAL_STATUSES: Final[frozenset[TaskStatus]] = frozenset(
    {
        TaskStatus.SUCCEEDED,
        TaskStatus.PARTIALLY_SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.OUTCOME_UNCERTAIN,
    }
)

_TRANSITIONS: Final[Mapping[TaskStatus, frozenset[TaskStatus]]] = MappingProxyType(
    {
        TaskStatus.DRAFT: frozenset({TaskStatus.VALIDATING}),
        TaskStatus.VALIDATING: frozenset(
            {
                TaskStatus.AWAITING_DEVICE,
                TaskStatus.CANCELLING,
                TaskStatus.FAILED,
            }
        ),
        TaskStatus.AWAITING_DEVICE: frozenset(
            {
                TaskStatus.AWAITING_PLATFORM_LOGIN,
                TaskStatus.CANCELLING,
                TaskStatus.FAILED,
            }
        ),
        TaskStatus.AWAITING_PLATFORM_LOGIN: frozenset(
            {
                TaskStatus.DISCOVERING_TARGETS,
                TaskStatus.CANCELLING,
                TaskStatus.FAILED,
            }
        ),
        TaskStatus.DISCOVERING_TARGETS: frozenset(
            {
                TaskStatus.AWAITING_CONFIRMATION,
                TaskStatus.AWAITING_HUMAN,
                TaskStatus.CANCELLING,
                TaskStatus.FAILED,
            }
        ),
        TaskStatus.AWAITING_CONFIRMATION: frozenset(
            {
                TaskStatus.DISCOVERING_TARGETS,
                TaskStatus.QUEUED,
                TaskStatus.CANCELLING,
            }
        ),
        TaskStatus.QUEUED: frozenset(
            {
                TaskStatus.AWAITING_DEVICE,
                TaskStatus.RUNNING,
                TaskStatus.CANCELLING,
                TaskStatus.FAILED,
            }
        ),
        TaskStatus.RUNNING: frozenset(
            {
                TaskStatus.PAUSED,
                TaskStatus.AWAITING_HUMAN,
                TaskStatus.CANCELLING,
                TaskStatus.SUCCEEDED,
                TaskStatus.PARTIALLY_SUCCEEDED,
                TaskStatus.FAILED,
                TaskStatus.OUTCOME_UNCERTAIN,
            }
        ),
        TaskStatus.PAUSED: frozenset(
            {
                TaskStatus.RUNNING,
                TaskStatus.AWAITING_HUMAN,
                TaskStatus.CANCELLING,
            }
        ),
        TaskStatus.AWAITING_HUMAN: frozenset(
            {
                TaskStatus.DISCOVERING_TARGETS,
                TaskStatus.RUNNING,
                TaskStatus.CANCELLING,
                TaskStatus.FAILED,
                TaskStatus.OUTCOME_UNCERTAIN,
            }
        ),
        TaskStatus.CANCELLING: frozenset(
            {
                TaskStatus.SUCCEEDED,
                TaskStatus.PARTIALLY_SUCCEEDED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
                TaskStatus.OUTCOME_UNCERTAIN,
            }
        ),
        TaskStatus.SUCCEEDED: frozenset(),
        TaskStatus.PARTIALLY_SUCCEEDED: frozenset(),
        TaskStatus.FAILED: frozenset(),
        TaskStatus.CANCELLED: frozenset(),
        TaskStatus.OUTCOME_UNCERTAIN: frozenset(),
    }
)


class TaskStateMachine:
    """Stateless transition policy shared by task application use cases."""

    @staticmethod
    def terminal_statuses() -> frozenset[TaskStatus]:
        return _TERMINAL_STATUSES

    @staticmethod
    def is_terminal(status: object) -> bool:
        return isinstance(status, TaskStatus) and status in _TERMINAL_STATUSES

    @staticmethod
    def allowed_targets(status: object) -> frozenset[TaskStatus]:
        if not isinstance(status, TaskStatus):
            raise InvalidTaskTransition
        return _TRANSITIONS[status]

    @staticmethod
    def can_transition(current: object, target: object) -> bool:
        return (
            isinstance(current, TaskStatus)
            and isinstance(target, TaskStatus)
            and target in _TRANSITIONS[current]
        )

    @staticmethod
    def transition(current: object, target: object) -> TaskStatus:
        if (
            not isinstance(current, TaskStatus)
            or not isinstance(target, TaskStatus)
            or target not in _TRANSITIONS[current]
        ):
            raise InvalidTaskTransition
        return target


__all__ = ["InvalidTaskTransition", "TaskStateMachine", "TaskStatus"]
