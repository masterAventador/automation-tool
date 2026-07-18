"""Application contracts for Installation-scoped Task persistence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from automation_tool.control_plane.domain import InstallationId, TaskId, TaskStatus
from automation_tool.protocol import IdempotencyKey


class TaskPersistenceRejected(PermissionError):
    """A Task target, scope, revision, or persistence operation was rejected."""

    def __init__(self) -> None:
        super().__init__("Task persistence operation is rejected")


class InvalidTaskCreation(ValueError):
    def __init__(self) -> None:
        super().__init__("Task creation request is invalid")


@dataclass(frozen=True, slots=True)
class TaskRecord:
    task_id: TaskId
    installation_id: InstallationId
    status: TaskStatus
    revision: int
    last_event_sequence: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class TaskCreationResult:
    task: TaskRecord
    created: bool


class Clock(Protocol):
    def now(self) -> datetime: ...


class TaskRepository(Protocol):
    async def create(
        self,
        *,
        task_id: TaskId,
        installation_id: InstallationId,
        idempotency_key: str,
        created_at: datetime,
    ) -> TaskCreationResult: ...

    async def get(
        self,
        *,
        task_id: TaskId,
        installation_id: InstallationId,
    ) -> TaskRecord | None: ...

    async def transition(
        self,
        *,
        task_id: TaskId,
        installation_id: InstallationId,
        expected_revision: int,
        target_status: TaskStatus,
        updated_at: datetime,
    ) -> TaskRecord: ...


class TaskCreationService:
    def __init__(self, *, repository: TaskRepository, clock: Clock) -> None:
        self._repository = repository
        self._clock = clock

    async def create(
        self,
        *,
        installation_id: InstallationId,
        idempotency_key: str,
    ) -> TaskCreationResult:
        if not isinstance(installation_id, InstallationId):
            raise InvalidTaskCreation
        try:
            normalized_key = str(IdempotencyKey(idempotency_key))
        except (TypeError, ValueError):
            normalized_key = None
        if normalized_key is None:
            raise InvalidTaskCreation
        return await self._repository.create(
            task_id=TaskId.new(),
            installation_id=installation_id,
            idempotency_key=normalized_key,
            created_at=self._clock.now(),
        )


__all__ = [
    "InvalidTaskCreation",
    "TaskCreationResult",
    "TaskCreationService",
    "TaskPersistenceRejected",
    "TaskRecord",
    "TaskRepository",
]
