"""Application contracts for Installation-scoped Task persistence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from automation_tool.control_plane.domain import InstallationId, TaskId, TaskStatus


class TaskPersistenceRejected(PermissionError):
    """A Task target, scope, revision, or persistence operation was rejected."""

    def __init__(self) -> None:
        super().__init__("Task persistence operation is rejected")


@dataclass(frozen=True, slots=True)
class TaskRecord:
    task_id: TaskId
    installation_id: InstallationId
    status: TaskStatus
    revision: int
    created_at: datetime
    updated_at: datetime


class TaskRepository(Protocol):
    async def create(
        self,
        *,
        task_id: TaskId,
        installation_id: InstallationId,
        created_at: datetime,
    ) -> TaskRecord: ...

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


__all__ = ["TaskPersistenceRejected", "TaskRecord", "TaskRepository"]
