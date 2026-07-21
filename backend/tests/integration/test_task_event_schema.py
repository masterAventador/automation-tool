from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from conftest import AlembicRunner
from sqlalchemy import delete, insert, select, text, update
from sqlalchemy.exc import IntegrityError

from automation_tool.control_plane.domain import (
    MAX_TASK_EVENT_SEQUENCE,
    ActionId,
    ActionOutcome,
    ActionStatus,
    ExecutionAttemptId,
    ExecutionAttemptStatus,
    InstallationId,
    TaskEventType,
    TaskEventVersion,
    TaskId,
    TaskStatus,
)
from automation_tool.control_plane.infrastructure.database import (
    Database,
    device_credentials,
    device_sessions,
    execution_attempts,
    installation_registration_challenges,
    installations,
    task_actions,
    task_events,
    tasks,
)

PREVIOUS_REVISION = "20260718_0007"
HEAD_REVISION = "20260721_0026"
NOW = datetime(2026, 7, 18, 5, 0, tzinfo=UTC)
EXPECTED_EVENT_COLUMNS = {
    "task_id",
    "installation_id",
    "sequence",
    "event_version",
    "event_type",
    "task_revision",
    "task_status",
    "execution_attempt_id",
    "action_id",
    "source_message_id",
    "source_idempotency_key",
    "source_fingerprint",
    "progress_percent",
    "occurred_at",
    "recorded_at",
    "safe_message",
}
EXPECTED_EVENT_CONSTRAINTS = {
    "pk_task_events",
    "fk_task_events_task_binding",
    "fk_task_events_attempt_binding",
    "fk_task_events_action_binding",
    "uq_task_events_source_message",
    "uq_task_events_source_idempotency",
    "ck_task_events_sequence_range",
    "ck_task_events_version",
    "ck_task_events_type",
    "ck_task_events_task_revision_positive",
    "ck_task_events_task_status",
    "ck_task_events_source_message_uuid_v4",
    "ck_task_events_source_idempotency_key",
    "ck_task_events_source_fingerprint_length",
    "ck_task_events_progress_percent",
    "ck_task_events_action_requires_attempt",
    "ck_task_events_time_order",
    "ck_task_events_safe_message",
}


async def reset_data(database: Database) -> None:
    async with database.session() as session:
        await session.execute(delete(task_events))
        await session.execute(delete(task_actions))
        await session.execute(update(tasks).values(current_attempt_id=None))
        await session.execute(delete(execution_attempts))
        await session.execute(delete(tasks))
        await session.execute(delete(installation_registration_challenges))
        await session.execute(delete(device_sessions))
        await session.execute(delete(device_credentials))
        await session.execute(delete(installations))


async def seed_execution_chain(
    database: Database,
) -> tuple[InstallationId, TaskId, ExecutionAttemptId, ActionId]:
    installation_id = InstallationId.new()
    task_id = TaskId.new()
    attempt_id = ExecutionAttemptId.new()
    action_id = ActionId.new()
    async with database.session() as session:
        await session.execute(
            insert(installations).values(
                id=installation_id.uuid,
                device_public_key=secrets.token_bytes(32),
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.execute(
            insert(tasks).values(
                id=task_id.uuid,
                installation_id=installation_id.uuid,
                creation_idempotency_key=f"task:seed:{task_id}",
                status=TaskStatus.RUNNING.value,
                revision=4,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.execute(
            insert(execution_attempts).values(
                id=attempt_id.uuid,
                task_id=task_id.uuid,
                installation_id=installation_id.uuid,
                attempt_number=1,
                status=ExecutionAttemptStatus.RUNNING.value,
                revision=2,
                created_at=NOW,
                updated_at=NOW,
                started_at=NOW,
            )
        )
        await session.execute(
            insert(task_actions).values(
                id=action_id.uuid,
                execution_attempt_id=attempt_id.uuid,
                task_id=task_id.uuid,
                installation_id=installation_id.uuid,
                ordinal=1,
                status=ActionStatus.DISPATCHED.value,
                outcome=ActionOutcome.PENDING.value,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.execute(
            update(tasks)
            .where(tasks.c.id == task_id.uuid)
            .values(current_attempt_id=attempt_id.uuid)
        )
    return installation_id, task_id, attempt_id, action_id


def event_values(
    installation_id: InstallationId,
    task_id: TaskId,
    *,
    sequence: int = 1,
) -> dict[str, object]:
    return {
        "task_id": task_id.uuid,
        "installation_id": installation_id.uuid,
        "sequence": sequence,
        "event_version": TaskEventVersion.V1.value,
        "event_type": TaskEventType.TASK_STARTED.value,
        "task_revision": 4,
        "task_status": TaskStatus.RUNNING.value,
        "source_idempotency_key": f"task:event:{sequence}",
        "source_fingerprint": bytes([sequence % 256]) * 32,
        "occurred_at": NOW,
        "recorded_at": NOW,
    }


@pytest.mark.asyncio
async def test_task_event_migration_upgrades_checks_and_downgrades_cleanly(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    alembic_runner(postgresql_url, "check")
    database = Database.from_url(postgresql_url)
    try:
        async with database.session() as session:
            revision = await session.scalar(text("select version_num from alembic_version"))
            task_columns = set(
                await session.scalars(
                    text(
                        "select column_name from information_schema.columns "
                        "where table_schema = 'public' and table_name = 'tasks'"
                    )
                )
            )
            event_columns = set(
                await session.scalars(
                    text(
                        "select column_name from information_schema.columns "
                        "where table_schema = 'public' and table_name = 'task_events'"
                    )
                )
            )
            event_constraints = set(
                await session.scalars(
                    text(
                        "select conname from pg_constraint "
                        "where conrelid = 'public.task_events'::regclass"
                    )
                )
            )
            indexes = set(
                await session.scalars(
                    text(
                        "select indexname from pg_indexes "
                        "where schemaname = 'public' and tablename = 'task_events'"
                    )
                )
            )
        assert revision == HEAD_REVISION
        assert "last_event_sequence" in task_columns
        assert event_columns == EXPECTED_EVENT_COLUMNS
        assert event_constraints >= EXPECTED_EVENT_CONSTRAINTS
        assert "ix_task_events_installation_task_sequence" in indexes

        alembic_runner(postgresql_url, "downgrade", PREVIOUS_REVISION)
        async with database.session() as session:
            downgraded_revision = await session.scalar(
                text("select version_num from alembic_version")
            )
            task_columns_after = set(
                await session.scalars(
                    text(
                        "select column_name from information_schema.columns "
                        "where table_schema = 'public' and table_name = 'tasks'"
                    )
                )
            )
            events_removed = await session.scalar(text("select to_regclass('public.task_events')"))
        assert downgraded_revision == PREVIOUS_REVISION
        assert "last_event_sequence" not in task_columns_after
        assert events_removed is None
    finally:
        await database.close()
        alembic_runner(postgresql_url, "upgrade", "head")


@pytest.mark.asyncio
async def test_event_replay_identity_migration_backfills_existing_rows(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "downgrade", "20260718_0010")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        installation_id, task_id, attempt_id, _ = await seed_execution_chain(database)
        source_message_id = uuid4()
        async with database.session() as session:
            await session.execute(
                insert(task_events).values(
                    task_id=task_id.uuid,
                    installation_id=installation_id.uuid,
                    sequence=1,
                    event_type=TaskEventType.TASK_STARTED.value,
                    task_revision=4,
                    task_status=TaskStatus.RUNNING.value,
                    execution_attempt_id=attempt_id.uuid,
                    source_message_id=source_message_id,
                    occurred_at=NOW,
                    recorded_at=NOW,
                )
            )

        alembic_runner(postgresql_url, "upgrade", "head")
        async with database.session() as session:
            backfilled = (
                (
                    await session.execute(
                        select(
                            task_events.c.source_idempotency_key,
                            task_events.c.source_fingerprint,
                        ).where(
                            task_events.c.task_id == task_id.uuid,
                            task_events.c.sequence == 1,
                        )
                    )
                )
                .mappings()
                .one()
            )
        assert backfilled["source_idempotency_key"] == f"legacy:event:{task_id}:1"
        assert len(backfilled["source_fingerprint"]) == 32
    finally:
        alembic_runner(postgresql_url, "upgrade", "head")
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_event_defaults_scope_and_task_snapshot_watermark_are_persisted(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        installation_id, task_id, attempt_id, action_id = await seed_execution_chain(database)
        source_message_id = uuid4()
        async with database.session() as session:
            created = (
                (
                    await session.execute(
                        insert(task_events)
                        .values(
                            task_id=task_id.uuid,
                            installation_id=installation_id.uuid,
                            sequence=1,
                            event_type=TaskEventType.STEP_PROGRESS.value,
                            task_revision=4,
                            task_status=TaskStatus.RUNNING.value,
                            execution_attempt_id=attempt_id.uuid,
                            action_id=action_id.uuid,
                            source_message_id=source_message_id,
                            source_idempotency_key="task:event:progress:1",
                            source_fingerprint=b"p" * 32,
                            progress_percent=50,
                            occurred_at=NOW,
                            safe_message="正在处理第 1 个目标",
                        )
                        .returning(*task_events.c)
                    )
                )
                .mappings()
                .one()
            )
            await session.execute(
                update(tasks).where(tasks.c.id == task_id.uuid).values(last_event_sequence=1)
            )
            discovery_started = (
                await session.execute(
                    insert(task_events)
                    .values(
                        task_id=task_id.uuid,
                        installation_id=installation_id.uuid,
                        sequence=2,
                        event_type=TaskEventType.TASK_DISCOVERY_STARTED.value,
                        task_revision=5,
                        task_status=TaskStatus.DISCOVERING_TARGETS.value,
                        execution_attempt_id=attempt_id.uuid,
                        source_idempotency_key="task:event:discovery:2",
                        source_fingerprint=b"d" * 32,
                        occurred_at=NOW,
                        recorded_at=NOW,
                    )
                    .returning(task_events.c.event_type)
                )
            ).scalar_one()
            projection = (
                (
                    await session.execute(
                        select(
                            tasks.c.status,
                            tasks.c.revision,
                            tasks.c.last_event_sequence,
                        ).where(tasks.c.id == task_id.uuid)
                    )
                )
                .mappings()
                .one()
            )
        assert created["event_version"] == TaskEventVersion.V1.value
        assert created["recorded_at"].tzinfo is not None
        assert created["recorded_at"] >= created["occurred_at"]
        assert created["progress_percent"] == 50
        assert discovery_started == TaskEventType.TASK_DISCOVERY_STARTED.value
        assert projection == {
            "status": TaskStatus.RUNNING.value,
            "revision": 4,
            "last_event_sequence": 1,
        }
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_event_sequence_and_source_message_uniqueness_are_scoped_and_bounded(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        installation_id, task_id, _, _ = await seed_execution_chain(database)
        other_installation, other_task, _, _ = await seed_execution_chain(database)
        source_message_id = uuid4()
        original = event_values(installation_id, task_id)
        original["source_message_id"] = source_message_id
        async with database.session() as session:
            await session.execute(insert(task_events).values(original))

        for duplicate in (
            event_values(installation_id, task_id, sequence=1),
            event_values(installation_id, task_id, sequence=2)
            | {"source_message_id": source_message_id},
        ):
            with pytest.raises(IntegrityError):
                async with database.session() as session:
                    await session.execute(insert(task_events).values(duplicate))

        async with database.session() as session:
            await session.execute(
                insert(task_events).values(
                    event_values(other_installation, other_task, sequence=1)
                    | {"source_message_id": source_message_id}
                )
            )
            await session.execute(
                insert(task_events).values(
                    event_values(installation_id, task_id, sequence=MAX_TASK_EVENT_SEQUENCE)
                )
            )

        for invalid_sequence in (0, MAX_TASK_EVENT_SEQUENCE + 1):
            with pytest.raises(IntegrityError):
                async with database.session() as session:
                    await session.execute(
                        insert(task_events).values(
                            event_values(
                                installation_id,
                                task_id,
                                sequence=invalid_sequence,
                            )
                        )
                    )
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_event_constraints_reject_invalid_scope_version_projection_time_and_message(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        installation_id, task_id, attempt_id, action_id = await seed_execution_chain(database)
        other_installation, other_task, _, _ = await seed_execution_chain(database)
        invalid_cases: tuple[dict[str, object], ...] = (
            {"installation_id": other_installation.uuid},
            {"task_id": other_task.uuid},
            {"event_version": "2.0"},
            {"event_type": "task.future"},
            {"task_revision": 0},
            {"task_status": "unknown"},
            {"source_message_id": UUID("123e4567-e89b-12d3-a456-426614174000")},
            {"progress_percent": -1},
            {"progress_percent": 101},
            {"progress_percent": 50},
            {"recorded_at": NOW - timedelta(microseconds=1)},
            {"safe_message": ""},
            {"safe_message": "x" * 1025},
            {"safe_message": "cookie=private-value"},
            {"execution_attempt_id": ExecutionAttemptId.new().uuid},
            {"action_id": action_id.uuid},
            {
                "execution_attempt_id": attempt_id.uuid,
                "action_id": ActionId.new().uuid,
            },
        )
        for sequence, overrides in enumerate(invalid_cases, start=2):
            values = event_values(installation_id, task_id, sequence=sequence)
            values.update(overrides)
            with pytest.raises(IntegrityError):
                async with database.session() as session:
                    await session.execute(insert(task_events).values(values))

        for invalid_watermark in (-1, MAX_TASK_EVENT_SEQUENCE + 1):
            with pytest.raises(IntegrityError):
                async with database.session() as session:
                    await session.execute(
                        update(tasks)
                        .where(tasks.c.id == task_id.uuid)
                        .values(last_event_sequence=invalid_watermark)
                    )
    finally:
        await reset_data(database)
        await database.close()
