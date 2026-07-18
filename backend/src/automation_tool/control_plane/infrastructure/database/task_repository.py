"""Atomic PostgreSQL repository for Installation-scoped Task state."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from sqlalchemy import insert, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError

from automation_tool.control_plane.application.tasks import (
    TaskPersistenceRejected,
    TaskRecord,
)
from automation_tool.control_plane.domain import (
    InstallationId,
    InstallationStatus,
    TaskId,
    TaskStateMachine,
    TaskStatus,
)
from automation_tool.control_plane.infrastructure.database.schema import installations, tasks
from automation_tool.control_plane.infrastructure.database.session import Database


def _aware_utc(value: object) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise TaskPersistenceRejected
    return value.astimezone(UTC)


def _require_identity(task_id: object, installation_id: object) -> tuple[TaskId, InstallationId]:
    if not isinstance(task_id, TaskId) or not isinstance(installation_id, InstallationId):
        raise TaskPersistenceRejected
    return task_id, installation_id


def _record(row: RowMapping) -> TaskRecord:
    return TaskRecord(
        task_id=TaskId.parse(row["id"]),
        installation_id=InstallationId.parse(row["installation_id"]),
        status=TaskStatus(cast(str, row["status"])),
        revision=cast(int, row["revision"]),
        created_at=cast(datetime, row["created_at"]),
        updated_at=cast(datetime, row["updated_at"]),
    )


class SqlAlchemyTaskRepository:
    """Serialize creation and state CAS without exposing cross-Installation rows."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def create(
        self,
        *,
        task_id: TaskId,
        installation_id: InstallationId,
        created_at: datetime,
    ) -> TaskRecord:
        target_task, target_installation = _require_identity(task_id, installation_id)
        timestamp = _aware_utc(created_at)
        try:
            async with self._database.session() as session:
                installation_status = await session.scalar(
                    select(installations.c.status)
                    .where(installations.c.id == target_installation.uuid)
                    .with_for_update()
                )
                if installation_status != InstallationStatus.ACTIVE.value:
                    raise TaskPersistenceRejected
                created = (
                    (
                        await session.execute(
                            insert(tasks)
                            .values(
                                id=target_task.uuid,
                                installation_id=target_installation.uuid,
                                status=TaskStatus.DRAFT.value,
                                revision=1,
                                created_at=timestamp,
                                updated_at=timestamp,
                            )
                            .returning(*tasks.c)
                        )
                    )
                    .mappings()
                    .one()
                )
                return _record(created)
        except IntegrityError:
            raise TaskPersistenceRejected from None

    async def get(
        self,
        *,
        task_id: TaskId,
        installation_id: InstallationId,
    ) -> TaskRecord | None:
        target_task, target_installation = _require_identity(task_id, installation_id)
        async with self._database.session() as session:
            row = (
                (
                    await session.execute(
                        select(tasks).where(
                            tasks.c.id == target_task.uuid,
                            tasks.c.installation_id == target_installation.uuid,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        return None if row is None else _record(row)

    async def transition(
        self,
        *,
        task_id: TaskId,
        installation_id: InstallationId,
        expected_revision: int,
        target_status: TaskStatus,
        updated_at: datetime,
    ) -> TaskRecord:
        target_task, target_installation = _require_identity(task_id, installation_id)
        if type(expected_revision) is not int or expected_revision <= 0:
            raise TaskPersistenceRejected
        if not isinstance(target_status, TaskStatus):
            raise TaskPersistenceRejected
        timestamp = _aware_utc(updated_at)
        async with self._database.session() as session:
            current = (
                (
                    await session.execute(
                        select(tasks)
                        .where(
                            tasks.c.id == target_task.uuid,
                            tasks.c.installation_id == target_installation.uuid,
                            tasks.c.revision == expected_revision,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if current is None or timestamp < current["updated_at"]:
                raise TaskPersistenceRejected
            next_status = TaskStateMachine.transition(
                TaskStatus(cast(str, current["status"])),
                target_status,
            )
            transitioned = (
                (
                    await session.execute(
                        update(tasks)
                        .where(
                            tasks.c.id == target_task.uuid,
                            tasks.c.installation_id == target_installation.uuid,
                            tasks.c.revision == expected_revision,
                        )
                        .values(
                            status=next_status.value,
                            revision=expected_revision + 1,
                            updated_at=timestamp,
                        )
                        .returning(*tasks.c)
                    )
                )
                .mappings()
                .one()
            )
            return _record(transitioned)


__all__ = ["SqlAlchemyTaskRepository"]
