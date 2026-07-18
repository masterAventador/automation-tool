"""Installation-scoped pause and resume command intents."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable
from uuid import RFC_4122, UUID, uuid4

from automation_tool.control_plane.application.task_command_delivery import TaskCommandRecord
from automation_tool.control_plane.domain import InstallationId, TaskCommandType, TaskId
from automation_tool.protocol import IdempotencyKey


class InvalidTaskControl(ValueError):
    def __init__(self) -> None:
        super().__init__("Task control request is invalid")


class TaskControlNotFound(LookupError):
    def __init__(self) -> None:
        super().__init__("Task is unavailable")


class TaskControlConflict(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Task control was rejected")


class TaskControlUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Task control is unavailable")


class TaskControlClock(Protocol):
    def now(self) -> datetime: ...


class SystemTaskControlClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


def _uuid_v4(value: object) -> UUID:
    if not isinstance(value, UUID) or value.version != 4 or value.variant != RFC_4122:
        raise InvalidTaskControl
    return value


def _aware_utc(value: object) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise InvalidTaskControl
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class PendingTaskControl:
    message_id: UUID
    correlation_id: UUID
    installation_id: InstallationId
    task_id: TaskId
    command_type: TaskCommandType
    idempotency_key: str
    deadline_at: datetime
    created_at: datetime

    def __post_init__(self) -> None:
        _uuid_v4(self.message_id)
        _uuid_v4(self.correlation_id)
        if (
            type(self.installation_id) is not InstallationId
            or type(self.task_id) is not TaskId
            or self.command_type not in {TaskCommandType.TASK_PAUSE, TaskCommandType.TASK_RESUME}
        ):
            raise InvalidTaskControl
        try:
            key = str(IdempotencyKey(self.idempotency_key))
        except (TypeError, ValueError):
            raise InvalidTaskControl from None
        created_at = _aware_utc(self.created_at)
        deadline_at = _aware_utc(self.deadline_at)
        if deadline_at <= created_at:
            raise InvalidTaskControl
        object.__setattr__(self, "idempotency_key", key)
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "deadline_at", deadline_at)


@dataclass(frozen=True, slots=True)
class TaskControlEnqueueResult:
    command: TaskCommandRecord
    created: bool

    def __post_init__(self) -> None:
        if not isinstance(self.command, TaskCommandRecord) or type(self.created) is not bool:
            raise InvalidTaskControl
        if self.command.command_type not in {
            TaskCommandType.TASK_PAUSE,
            TaskCommandType.TASK_RESUME,
        }:
            raise InvalidTaskControl


@runtime_checkable
class TaskControlRepository(Protocol):
    async def enqueue_control(self, control: PendingTaskControl) -> TaskControlEnqueueResult: ...


class TaskControlService:
    """Persist control intent without projecting Task or Attempt state."""

    def __init__(
        self,
        *,
        repository: TaskControlRepository,
        clock: TaskControlClock | None = None,
        id_source: Callable[[], object] = uuid4,
        command_lifetime: timedelta = timedelta(minutes=1),
    ) -> None:
        resolved_clock = clock or SystemTaskControlClock()
        if (
            not isinstance(repository, TaskControlRepository)
            or not callable(getattr(resolved_clock, "now", None))
            or not callable(id_source)
            or not isinstance(command_lifetime, timedelta)
            or command_lifetime <= timedelta(0)
        ):
            raise InvalidTaskControl
        self._repository = repository
        self._clock = resolved_clock
        self._id_source = id_source
        self._command_lifetime = command_lifetime

    async def pause(
        self,
        *,
        installation_id: InstallationId,
        task_id: str,
        idempotency_key: str,
    ) -> TaskControlEnqueueResult:
        return await self._request(
            installation_id=installation_id,
            task_id=task_id,
            idempotency_key=idempotency_key,
            command_type=TaskCommandType.TASK_PAUSE,
        )

    async def resume(
        self,
        *,
        installation_id: InstallationId,
        task_id: str,
        idempotency_key: str,
    ) -> TaskControlEnqueueResult:
        return await self._request(
            installation_id=installation_id,
            task_id=task_id,
            idempotency_key=idempotency_key,
            command_type=TaskCommandType.TASK_RESUME,
        )

    async def _request(
        self,
        *,
        installation_id: InstallationId,
        task_id: str,
        idempotency_key: str,
        command_type: TaskCommandType,
    ) -> TaskControlEnqueueResult:
        if type(installation_id) is not InstallationId:
            raise InvalidTaskControl
        try:
            target_task = TaskId.parse(task_id)
            key = str(IdempotencyKey(idempotency_key))
        except (TypeError, ValueError):
            raise InvalidTaskControl from None
        try:
            now = _aware_utc(self._clock.now())
            control = PendingTaskControl(
                message_id=_uuid_v4(self._id_source()),
                correlation_id=_uuid_v4(self._id_source()),
                installation_id=installation_id,
                task_id=target_task,
                command_type=command_type,
                idempotency_key=key,
                created_at=now,
                deadline_at=now + self._command_lifetime,
            )
        except Exception:
            raise TaskControlUnavailable from None
        try:
            result = await self._repository.enqueue_control(control)
        except (TaskControlNotFound, TaskControlConflict):
            raise
        except Exception:
            raise TaskControlUnavailable from None
        if not isinstance(result, TaskControlEnqueueResult):
            raise TaskControlUnavailable
        return result


__all__ = [
    "InvalidTaskControl",
    "PendingTaskControl",
    "SystemTaskControlClock",
    "TaskControlClock",
    "TaskControlConflict",
    "TaskControlEnqueueResult",
    "TaskControlNotFound",
    "TaskControlRepository",
    "TaskControlService",
    "TaskControlUnavailable",
]
