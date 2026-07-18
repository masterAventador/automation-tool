"""Versioned Task event and authoritative snapshot projection contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from automation_tool.control_plane.domain.resource_ids import TaskId
from automation_tool.control_plane.domain.task_state_machine import TaskStatus
from automation_tool.protocol.limits import MAX_CROSS_RUNTIME_SEQUENCE
from automation_tool.protocol.safe_text import is_unsafe_text

MAX_TASK_EVENT_SEQUENCE = MAX_CROSS_RUNTIME_SEQUENCE
MAX_SAFE_TASK_EVENT_MESSAGE_CHARACTERS = 1024


class TaskEventVersion(StrEnum):
    """Persisted event schema versions understood by snapshot consumers."""

    V1 = "1.0"


class TaskEventType(StrEnum):
    """Closed event vocabulary consumed by Task timeline projections."""

    TASK_CREATED = "task.created"
    TASK_VALIDATION_STARTED = "task.validation_started"
    TASK_VALIDATION_FAILED = "task.validation_failed"
    TASK_AWAITING_PLATFORM_LOGIN = "task.awaiting_platform_login"
    TASK_AWAITING_CONFIRMATION = "task.awaiting_confirmation"
    TASK_STARTED = "task.started"
    STEP_STARTED = "step.started"
    STEP_PROGRESS = "step.progress"
    STEP_COMPLETED = "step.completed"
    STEP_FAILED = "step.failed"
    TASK_AWAITING_HUMAN = "task.awaiting_human"
    TASK_PAUSED = "task.paused"
    TASK_RESUMED = "task.resumed"
    TASK_CANCELLING = "task.cancelling"
    TASK_CANCELLED = "task.cancelled"
    TASK_COMPLETED = "task.completed"
    TASK_PARTIALLY_COMPLETED = "task.partially_completed"
    TASK_FAILED = "task.failed"
    TASK_OUTCOME_UNCERTAIN = "task.outcome_uncertain"


class InvalidTaskEventModel(ValueError):
    """An event or snapshot value violated the closed persistence contract."""

    def __init__(self) -> None:
        super().__init__("Task event model is invalid")


@dataclass(frozen=True, slots=True)
class SafeTaskEventMessage:
    """A single safe user-facing diagnostic line, never raw Executor text."""

    value: str

    def __post_init__(self) -> None:
        if (
            type(self.value) is not str
            or not self.value
            or is_unsafe_text(
                self.value,
                maximum_characters=MAX_SAFE_TASK_EVENT_MESSAGE_CHARACTERS,
            )
        ):
            raise InvalidTaskEventModel

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class TaskSnapshotProjection:
    """Authoritative Task state paired with its durable event watermark."""

    task_id: TaskId
    status: TaskStatus
    revision: int
    last_event_sequence: int
    updated_at: datetime

    def __post_init__(self) -> None:
        if (
            not isinstance(self.task_id, TaskId)
            or not isinstance(self.status, TaskStatus)
            or type(self.revision) is not int
            or self.revision <= 0
            or type(self.last_event_sequence) is not int
            or not 0 <= self.last_event_sequence <= MAX_TASK_EVENT_SEQUENCE
            or not isinstance(self.updated_at, datetime)
            or self.updated_at.utcoffset() is None
        ):
            raise InvalidTaskEventModel


__all__ = [
    "MAX_SAFE_TASK_EVENT_MESSAGE_CHARACTERS",
    "MAX_TASK_EVENT_SEQUENCE",
    "InvalidTaskEventModel",
    "SafeTaskEventMessage",
    "TaskEventType",
    "TaskEventVersion",
    "TaskSnapshotProjection",
]
