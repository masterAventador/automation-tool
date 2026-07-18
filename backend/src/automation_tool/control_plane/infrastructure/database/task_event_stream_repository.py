"""PostgreSQL reads for Installation-scoped durable Task event streams."""

from __future__ import annotations

from datetime import datetime
from typing import cast

from sqlalchemy import select, true
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import SQLAlchemyError

from automation_tool.control_plane.application.task_event_stream import (
    InvalidTaskEventStream,
    TaskEventRecord,
    TaskEventStreamBatch,
    TaskEventStreamUnavailable,
)
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
    TaskStatus,
)
from automation_tool.control_plane.infrastructure.database.schema import task_events, tasks
from automation_tool.control_plane.infrastructure.database.session import Database


def _event(row: RowMapping) -> TaskEventRecord:
    attempt_value = row["event_execution_attempt_id"]
    action_value = row["event_action_id"]
    message_value = row["event_safe_message"]
    return TaskEventRecord(
        task_id=TaskId.parse(row["event_task_id"]),
        sequence=cast(int, row["event_sequence"]),
        event_version=TaskEventVersion(cast(str, row["event_version"])),
        event_type=TaskEventType(cast(str, row["event_type"])),
        task_revision=cast(int, row["event_task_revision"]),
        task_status=TaskStatus(cast(str, row["event_task_status"])),
        execution_attempt_id=(
            None if attempt_value is None else ExecutionAttemptId.parse(attempt_value)
        ),
        action_id=None if action_value is None else ActionId.parse(action_value),
        progress_percent=cast(int | None, row["event_progress_percent"]),
        occurred_at=cast(datetime, row["event_occurred_at"]),
        recorded_at=cast(datetime, row["event_recorded_at"]),
        safe_message=(
            None if message_value is None else SafeTaskEventMessage(cast(str, message_value))
        ),
    )


class SqlAlchemyTaskEventStreamRepository:
    """Read event facts and the Task watermark from one MVCC statement snapshot."""

    def __init__(self, database: Database) -> None:
        if not isinstance(database, Database):
            raise TaskEventStreamUnavailable
        self._database = database

    async def read_batch(
        self,
        *,
        installation_id: InstallationId,
        task_id: TaskId,
        after_sequence: int,
        limit: int,
    ) -> TaskEventStreamBatch | None:
        if (
            not isinstance(installation_id, InstallationId)
            or not isinstance(task_id, TaskId)
            or type(after_sequence) is not int
            or not 0 <= after_sequence <= MAX_TASK_EVENT_SEQUENCE
            or type(limit) is not int
            or not 1 <= limit <= 100
        ):
            raise TaskEventStreamUnavailable
        event_window = (
            select(
                task_events.c.task_id.label("event_task_id"),
                task_events.c.sequence.label("event_sequence"),
                task_events.c.event_version.label("event_version"),
                task_events.c.event_type.label("event_type"),
                task_events.c.task_revision.label("event_task_revision"),
                task_events.c.task_status.label("event_task_status"),
                task_events.c.execution_attempt_id.label("event_execution_attempt_id"),
                task_events.c.action_id.label("event_action_id"),
                task_events.c.progress_percent.label("event_progress_percent"),
                task_events.c.occurred_at.label("event_occurred_at"),
                task_events.c.recorded_at.label("event_recorded_at"),
                task_events.c.safe_message.label("event_safe_message"),
            )
            .where(
                task_events.c.installation_id == installation_id.uuid,
                task_events.c.task_id == task_id.uuid,
                task_events.c.sequence > after_sequence,
            )
            .order_by(task_events.c.sequence)
            .limit(limit)
            .subquery("event_window")
        )
        statement = (
            select(
                tasks.c.last_event_sequence.label("task_last_event_sequence"),
                tasks.c.status.label("current_task_status"),
                *event_window.c,
            )
            .select_from(tasks.outerjoin(event_window, true()))
            .where(
                tasks.c.id == task_id.uuid,
                tasks.c.installation_id == installation_id.uuid,
            )
            .order_by(event_window.c.event_sequence.asc().nulls_last())
        )
        try:
            async with self._database.session() as session:
                rows = (await session.execute(statement)).mappings().all()
            if not rows:
                return None
            events = tuple(_event(row) for row in rows if row["event_sequence"] is not None)
            return TaskEventStreamBatch(
                events=events,
                after_sequence=after_sequence,
                task_last_event_sequence=cast(int, rows[0]["task_last_event_sequence"]),
                task_status=TaskStatus(cast(str, rows[0]["current_task_status"])),
            )
        except (InvalidResourceId, InvalidTaskEventStream, OSError, SQLAlchemyError, ValueError):
            raise TaskEventStreamUnavailable from None


__all__ = ["SqlAlchemyTaskEventStreamRepository"]
