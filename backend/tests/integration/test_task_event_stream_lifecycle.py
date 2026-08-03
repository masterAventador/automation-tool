from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from conftest import AlembicRunner
from sqlalchemy import delete, insert, update

from automation_tool.control_plane.application.task_event_stream import (
    TaskEventStreamUnavailable,
)
from automation_tool.control_plane.domain import (
    InstallationId,
    TaskEventType,
    TaskId,
    TaskStatus,
)
from automation_tool.control_plane.infrastructure.database import (
    Database,
    installations,
    task_events,
    tasks,
)
from automation_tool.control_plane.infrastructure.database.task_event_stream_repository import (
    SqlAlchemyTaskEventStreamRepository,
)

NOW = datetime(2026, 7, 18, 19, 0, tzinfo=UTC)


async def reset_data(database: Database) -> None:
    async with database.session() as session:
        await session.execute(delete(task_events))
        await session.execute(delete(tasks))
        await session.execute(delete(installations))


async def seed_task(
    database: Database,
    *,
    installation_id: InstallationId | None = None,
    status: TaskStatus = TaskStatus.RUNNING,
    watermark: int = 0,
) -> tuple[InstallationId, TaskId]:
    scoped_installation = installation_id or InstallationId.new()
    task_id = TaskId.new()
    async with database.session() as session:
        if installation_id is None:
            await session.execute(
                insert(installations).values(
                    id=scoped_installation.uuid,
                    device_public_key=secrets.token_bytes(32),
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        await session.execute(
            insert(tasks).values(
                id=task_id.uuid,
                installation_id=scoped_installation.uuid,
                creation_idempotency_key=f"task:stream:{task_id}",
                status=status.value,
                revision=watermark + 1,
                last_event_sequence=watermark,
                created_at=NOW,
                updated_at=NOW + timedelta(seconds=watermark),
            )
        )
    return scoped_installation, task_id


def event_values(
    installation_id: InstallationId,
    task_id: TaskId,
    sequence: int,
    *,
    event_type: TaskEventType,
    task_status: TaskStatus,
    progress_percent: int | None = None,
) -> dict[str, object]:
    return {
        "task_id": task_id.uuid,
        "installation_id": installation_id.uuid,
        "sequence": sequence,
        "event_type": event_type.value,
        "task_revision": sequence + 1,
        "task_status": task_status.value,
        "source_message_id": uuid4(),
        "source_idempotency_key": f"task:stream:event:{task_id}:{sequence}",
        "source_fingerprint": bytes([sequence]) * 32,
        "progress_percent": progress_percent,
        "occurred_at": NOW + timedelta(seconds=sequence),
        "recorded_at": NOW + timedelta(seconds=sequence),
        "safe_message": "公开进度" if progress_percent is not None else None,
    }


@pytest.mark.asyncio
async def test_repository_reads_bounded_ordered_public_events_and_terminal_catchup(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyTaskEventStreamRepository(database)
    try:
        await reset_data(database)
        installation_id, task_id = await seed_task(
            database,
            status=TaskStatus.SUCCEEDED,
            watermark=3,
        )
        async with database.session() as session:
            await session.execute(
                insert(task_events),
                [
                    event_values(
                        installation_id,
                        task_id,
                        1,
                        event_type=TaskEventType.TASK_STARTED,
                        task_status=TaskStatus.RUNNING,
                    ),
                    event_values(
                        installation_id,
                        task_id,
                        2,
                        event_type=TaskEventType.STEP_PROGRESS,
                        task_status=TaskStatus.RUNNING,
                        progress_percent=50,
                    ),
                    event_values(
                        installation_id,
                        task_id,
                        3,
                        event_type=TaskEventType.TASK_COMPLETED,
                        task_status=TaskStatus.SUCCEEDED,
                    ),
                ],
            )

        first = await repository.read_batch(
            installation_id=installation_id,
            task_id=task_id,
            after_sequence=0,
            limit=2,
        )
        assert first is not None
        assert [item.sequence for item in first.events] == [1, 2]
        assert first.events[1].progress_percent == 50
        assert str(first.events[1].safe_message) == "公开进度"
        assert first.events[1].execution_attempt_id is None
        assert first.events[1].action_id is None
        assert first.task_last_event_sequence == 3
        assert first.next_sequence == 2
        assert first.caught_up is False
        assert first.close_after_batch is False

        final = await repository.read_batch(
            installation_id=installation_id,
            task_id=task_id,
            after_sequence=2,
            limit=2,
        )
        assert final is not None
        assert [item.event_type for item in final.events] == [TaskEventType.TASK_COMPLETED]
        assert final.caught_up is True
        assert final.close_after_batch is True
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_repository_hides_cross_scope_and_returns_empty_terminal_watermark(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyTaskEventStreamRepository(database)
    try:
        await reset_data(database)
        installation_id, task_id = await seed_task(database, status=TaskStatus.SUCCEEDED)

        hidden = await repository.read_batch(
            installation_id=InstallationId.new(),
            task_id=task_id,
            after_sequence=0,
            limit=100,
        )
        caught_up = await repository.read_batch(
            installation_id=installation_id,
            task_id=task_id,
            after_sequence=0,
            limit=100,
        )

        assert hidden is None
        assert caught_up is not None
        assert caught_up.events == ()
        assert caught_up.close_after_batch is True
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_repository_never_reads_an_uncommitted_event_or_projection(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyTaskEventStreamRepository(database)
    try:
        await reset_data(database)
        installation_id, task_id = await seed_task(database)
        values = event_values(
            installation_id,
            task_id,
            1,
            event_type=TaskEventType.TASK_COMPLETED,
            task_status=TaskStatus.SUCCEEDED,
        )

        async with database.session() as writer:
            await writer.execute(insert(task_events).values(values))
            await writer.execute(
                update(tasks)
                .where(tasks.c.id == task_id.uuid)
                .values(
                    status=TaskStatus.SUCCEEDED.value,
                    revision=2,
                    last_event_sequence=1,
                    updated_at=NOW + timedelta(seconds=1),
                )
            )
            before_commit = await repository.read_batch(
                installation_id=installation_id,
                task_id=task_id,
                after_sequence=0,
                limit=100,
            )
            assert before_commit is not None
            assert before_commit.events == ()
            assert before_commit.task_last_event_sequence == 0
            assert before_commit.task_status is TaskStatus.RUNNING

        after_commit = await repository.read_batch(
            installation_id=installation_id,
            task_id=task_id,
            after_sequence=0,
            limit=100,
        )
        assert after_commit is not None
        assert [item.sequence for item in after_commit.events] == [1]
        assert after_commit.close_after_batch is True
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_repository_fails_closed_for_invalid_inputs_and_unavailable_database() -> None:
    unavailable = Database.from_url(
        "postgresql+asyncpg://postgres:postgres@127.0.0.1:1/postgres",
        connect_timeout_seconds=0.05,
    )
    repository = SqlAlchemyTaskEventStreamRepository(unavailable)
    try:
        with pytest.raises(TaskEventStreamUnavailable):
            SqlAlchemyTaskEventStreamRepository(object())  # type: ignore[arg-type]

        for values in (
            {
                "installation_id": "invalid",
                "task_id": TaskId.new(),
                "after_sequence": 0,
                "limit": 100,
            },
            {
                "installation_id": InstallationId.new(),
                "task_id": TaskId.new(),
                "after_sequence": True,
                "limit": 100,
            },
            {
                "installation_id": InstallationId.new(),
                "task_id": TaskId.new(),
                "after_sequence": 0,
                "limit": 101,
            },
        ):
            with pytest.raises(TaskEventStreamUnavailable):
                await repository.read_batch(**values)

        with pytest.raises(TaskEventStreamUnavailable) as raised:
            await repository.read_batch(
                installation_id=InstallationId.new(),
                task_id=TaskId.new(),
                after_sequence=0,
                limit=100,
            )
        assert raised.value.__cause__ is None
        assert "127.0.0.1" not in str(raised.value)
    finally:
        await unavailable.close()
