"""Installation-scoped reads over the durable Task event timeline."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from automation_tool.control_plane.domain import (
    MAX_TASK_EVENT_SEQUENCE,
    ActionId,
    ExecutionAttemptId,
    InstallationId,
    InvalidResourceId,
    SafeTaskEventMessage,
    TaskEventType,
    TaskEventVersion,
    TaskId,
    TaskStateMachine,
    TaskStatus,
)

_LAST_EVENT_ID_PATTERN = re.compile(r"^(?:0|[1-9][0-9]{0,15})$")


class InvalidTaskEventStream(ValueError):
    """The requested stream position or returned event shape is invalid."""

    def __init__(self) -> None:
        super().__init__("Task event stream is invalid")


class TaskEventStreamNotFound(LookupError):
    """The Task is not visible inside the authenticated Installation scope."""

    def __init__(self) -> None:
        super().__init__("Task event stream is unavailable")


class TaskEventStreamUnavailable(RuntimeError):
    """The durable event timeline cannot currently be read safely."""

    def __init__(self) -> None:
        super().__init__("Task event stream is unavailable")


@dataclass(frozen=True, slots=True)
class TaskEventRecord:
    """One public-safe event fact read from the committed PostgreSQL timeline."""

    task_id: TaskId
    sequence: int
    event_version: TaskEventVersion
    event_type: TaskEventType
    task_revision: int
    task_status: TaskStatus
    execution_attempt_id: ExecutionAttemptId | None
    action_id: ActionId | None
    progress_percent: int | None
    occurred_at: datetime
    recorded_at: datetime
    safe_message: SafeTaskEventMessage | None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.task_id, TaskId)
            or type(self.sequence) is not int
            or not 1 <= self.sequence <= MAX_TASK_EVENT_SEQUENCE
            or not isinstance(self.event_version, TaskEventVersion)
            or not isinstance(self.event_type, TaskEventType)
            or type(self.task_revision) is not int
            or self.task_revision <= 0
            or not isinstance(self.task_status, TaskStatus)
            or (
                self.execution_attempt_id is not None
                and not isinstance(self.execution_attempt_id, ExecutionAttemptId)
            )
            or (self.action_id is not None and not isinstance(self.action_id, ActionId))
            or (self.action_id is not None and self.execution_attempt_id is None)
            or (
                self.progress_percent is not None
                and (
                    type(self.progress_percent) is not int
                    or not 0 <= self.progress_percent <= 100
                    or self.event_type is not TaskEventType.STEP_PROGRESS
                )
            )
            or not isinstance(self.occurred_at, datetime)
            or self.occurred_at.utcoffset() is None
            or not isinstance(self.recorded_at, datetime)
            or self.recorded_at.utcoffset() is None
            or self.recorded_at.astimezone(UTC) < self.occurred_at.astimezone(UTC)
            or (
                self.safe_message is not None
                and not isinstance(self.safe_message, SafeTaskEventMessage)
            )
        ):
            raise InvalidTaskEventStream


@dataclass(frozen=True, slots=True)
class TaskEventStreamBatch:
    """A bounded read paired with the Task watermark from one database snapshot."""

    events: tuple[TaskEventRecord, ...]
    after_sequence: int
    task_last_event_sequence: int
    task_status: TaskStatus

    def __post_init__(self) -> None:
        if (
            type(self.events) is not tuple
            or type(self.after_sequence) is not int
            or not 0 <= self.after_sequence <= MAX_TASK_EVENT_SEQUENCE
            or type(self.task_last_event_sequence) is not int
            or not 0 <= self.task_last_event_sequence <= MAX_TASK_EVENT_SEQUENCE
            or not isinstance(self.task_status, TaskStatus)
            or any(not isinstance(event, TaskEventRecord) for event in self.events)
            or any(
                current.sequence >= following.sequence
                for current, following in zip(self.events, self.events[1:], strict=False)
            )
            or any(event.sequence > self.task_last_event_sequence for event in self.events)
        ):
            raise InvalidTaskEventStream

    @property
    def next_sequence(self) -> int:
        return self.events[-1].sequence if self.events else self.after_sequence

    @property
    def caught_up(self) -> bool:
        return self.next_sequence == self.task_last_event_sequence

    @property
    def close_after_batch(self) -> bool:
        return self.caught_up and TaskStateMachine.is_terminal(self.task_status)


@runtime_checkable
class TaskEventStreamRepository(Protocol):
    async def read_batch(
        self,
        *,
        installation_id: InstallationId,
        task_id: TaskId,
        after_sequence: int,
        limit: int,
    ) -> TaskEventStreamBatch | None: ...


def _last_event_sequence(value: object) -> int:
    if value is None:
        return 0
    if not isinstance(value, str) or _LAST_EVENT_ID_PATTERN.fullmatch(value) is None:
        raise InvalidTaskEventStream
    sequence = int(value)
    if sequence > MAX_TASK_EVENT_SEQUENCE:
        raise InvalidTaskEventStream
    return sequence


class TaskEventStreamService:
    def __init__(self, *, repository: TaskEventStreamRepository) -> None:
        if not isinstance(repository, TaskEventStreamRepository):
            raise InvalidTaskEventStream
        self._repository = repository

    async def read(
        self,
        *,
        installation_id: InstallationId,
        task_id: str,
        last_event_id: str | None,
        limit: int = 100,
    ) -> TaskEventStreamBatch:
        if (
            not isinstance(installation_id, InstallationId)
            or type(limit) is not int
            or not 1 <= limit <= 100
        ):
            raise InvalidTaskEventStream
        try:
            parsed_task_id = TaskId.parse(task_id)
        except InvalidResourceId:
            raise TaskEventStreamNotFound from None
        after_sequence = _last_event_sequence(last_event_id)
        try:
            result = await self._repository.read_batch(
                installation_id=installation_id,
                task_id=parsed_task_id,
                after_sequence=after_sequence,
                limit=limit,
            )
        except TaskEventStreamUnavailable:
            raise
        except Exception:
            raise TaskEventStreamUnavailable from None
        if result is None:
            raise TaskEventStreamNotFound
        if after_sequence > result.task_last_event_sequence:
            raise InvalidTaskEventStream
        expected = after_sequence + 1
        if (
            result.after_sequence != after_sequence
            or (result.events and result.events[0].sequence != expected)
            or any(event.task_id != parsed_task_id for event in result.events)
            or any(event.sequence != expected + index for index, event in enumerate(result.events))
            or (not result.events and after_sequence < result.task_last_event_sequence)
        ):
            raise TaskEventStreamUnavailable
        return result


__all__ = [
    "InvalidTaskEventStream",
    "TaskEventRecord",
    "TaskEventStreamBatch",
    "TaskEventStreamNotFound",
    "TaskEventStreamRepository",
    "TaskEventStreamService",
    "TaskEventStreamUnavailable",
]
