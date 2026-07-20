from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from conftest import AlembicRunner
from sqlalchemy import delete, insert, text, update
from sqlalchemy.exc import IntegrityError

from automation_tool.control_plane.domain import (
    ActionId,
    ActionOutcome,
    ActionStatus,
    ExecutionAttemptId,
    ExecutionAttemptStatus,
    InstallationId,
    TaskId,
)
from automation_tool.control_plane.infrastructure.database import (
    Database,
    device_credentials,
    device_sessions,
    execution_attempts,
    installation_registration_challenges,
    installations,
    task_actions,
    tasks,
)

PREVIOUS_REVISION = "20260718_0006"
HEAD_REVISION = "20260720_0022"
NOW = datetime(2026, 7, 18, 16, 0, tzinfo=UTC)
EXPECTED_ATTEMPT_COLUMNS = {
    "id",
    "task_id",
    "installation_id",
    "attempt_number",
    "status",
    "revision",
    "created_at",
    "updated_at",
    "started_at",
    "finished_at",
}
EXPECTED_ACTION_COLUMNS = {
    "id",
    "execution_attempt_id",
    "task_id",
    "installation_id",
    "ordinal",
    "status",
    "outcome",
    "revision",
    "created_at",
    "updated_at",
    "finished_at",
}
EXPECTED_ATTEMPT_CONSTRAINTS = {
    "pk_execution_attempts",
    "fk_execution_attempts_task_binding",
    "uq_execution_attempts_binding",
    "uq_execution_attempts_task_number",
    "ck_execution_attempts_id_uuid_v4",
    "ck_execution_attempts_number_positive",
    "ck_execution_attempts_revision_positive",
    "ck_execution_attempts_status",
    "ck_execution_attempts_time_order",
    "ck_execution_attempts_terminal_time",
}
EXPECTED_ACTION_CONSTRAINTS = {
    "pk_task_actions",
    "fk_task_actions_attempt_binding",
    "uq_task_actions_binding",
    "uq_task_actions_attempt_ordinal",
    "ck_task_actions_id_uuid_v4",
    "ck_task_actions_ordinal_positive",
    "ck_task_actions_revision_positive",
    "ck_task_actions_status",
    "ck_task_actions_outcome",
    "ck_task_actions_time_order",
    "ck_task_actions_result_coherence",
}


async def reset_data(database: Database) -> None:
    async with database.session() as session:
        await session.execute(delete(task_actions))
        await session.execute(update(tasks).values(current_attempt_id=None))
        await session.execute(delete(execution_attempts))
        await session.execute(delete(tasks))
        await session.execute(delete(installation_registration_challenges))
        await session.execute(delete(device_sessions))
        await session.execute(delete(device_credentials))
        await session.execute(delete(installations))


async def seed_task(database: Database) -> tuple[InstallationId, TaskId]:
    installation_id = InstallationId.new()
    task_id = TaskId.new()
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
                created_at=NOW,
                updated_at=NOW,
            )
        )
    return installation_id, task_id


def attempt_values(
    installation_id: InstallationId,
    task_id: TaskId,
    *,
    attempt_number: int = 1,
) -> dict[str, object]:
    return {
        "id": ExecutionAttemptId.new().uuid,
        "task_id": task_id.uuid,
        "installation_id": installation_id.uuid,
        "attempt_number": attempt_number,
        "status": ExecutionAttemptStatus.PENDING.value,
        "revision": 1,
        "created_at": NOW,
        "updated_at": NOW,
    }


def action_values(
    installation_id: InstallationId,
    task_id: TaskId,
    attempt_id: ExecutionAttemptId,
    *,
    ordinal: int = 1,
) -> dict[str, object]:
    return {
        "id": ActionId.new().uuid,
        "execution_attempt_id": attempt_id.uuid,
        "task_id": task_id.uuid,
        "installation_id": installation_id.uuid,
        "ordinal": ordinal,
        "status": ActionStatus.PLANNED.value,
        "outcome": ActionOutcome.PENDING.value,
        "revision": 1,
        "created_at": NOW,
        "updated_at": NOW,
    }


@pytest.mark.asyncio
async def test_execution_migration_upgrades_checks_and_downgrades_cleanly(
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
            attempt_columns = set(
                await session.scalars(
                    text(
                        "select column_name from information_schema.columns "
                        "where table_schema = 'public' and table_name = 'execution_attempts'"
                    )
                )
            )
            action_columns = set(
                await session.scalars(
                    text(
                        "select column_name from information_schema.columns "
                        "where table_schema = 'public' and table_name = 'task_actions'"
                    )
                )
            )
            attempt_constraints = set(
                await session.scalars(
                    text(
                        "select conname from pg_constraint "
                        "where conrelid = 'public.execution_attempts'::regclass"
                    )
                )
            )
            action_constraints = set(
                await session.scalars(
                    text(
                        "select conname from pg_constraint "
                        "where conrelid = 'public.task_actions'::regclass"
                    )
                )
            )
            task_constraints = set(
                await session.scalars(
                    text(
                        "select conname from pg_constraint "
                        "where conrelid = 'public.tasks'::regclass"
                    )
                )
            )
            indexes = set(
                await session.scalars(
                    text(
                        "select indexname from pg_indexes where schemaname = 'public' "
                        "and tablename in ('execution_attempts', 'task_actions')"
                    )
                )
            )
        assert revision == HEAD_REVISION
        assert "current_attempt_id" in task_columns
        assert attempt_columns == EXPECTED_ATTEMPT_COLUMNS
        assert action_columns == EXPECTED_ACTION_COLUMNS
        assert attempt_constraints >= EXPECTED_ATTEMPT_CONSTRAINTS
        assert action_constraints >= EXPECTED_ACTION_CONSTRAINTS
        assert "fk_tasks_current_attempt_binding" in task_constraints
        assert {
            "uq_execution_attempts_one_active_task",
            "ix_execution_attempts_installation_updated",
            "ix_task_actions_installation_task",
        } <= indexes

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
            attempts_removed = await session.scalar(
                text("select to_regclass('public.execution_attempts')")
            )
            actions_removed = await session.scalar(
                text("select to_regclass('public.task_actions')")
            )
        assert downgraded_revision == PREVIOUS_REVISION
        assert "current_attempt_id" not in task_columns_after
        assert attempts_removed is None
        assert actions_removed is None
    finally:
        await database.close()
        alembic_runner(postgresql_url, "upgrade", "head")


@pytest.mark.asyncio
async def test_attempt_defaults_current_binding_and_retry_uniqueness_are_database_enforced(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        installation_id, task_id = await seed_task(database)
        other_installation, other_task = await seed_task(database)
        attempt_id = ExecutionAttemptId.new()
        async with database.session() as session:
            created = (
                (
                    await session.execute(
                        insert(execution_attempts)
                        .values(
                            id=attempt_id.uuid,
                            task_id=task_id.uuid,
                            installation_id=installation_id.uuid,
                            attempt_number=1,
                        )
                        .returning(*execution_attempts.c)
                    )
                )
                .mappings()
                .one()
            )
            await session.execute(
                update(tasks)
                .where(tasks.c.id == task_id.uuid)
                .values(current_attempt_id=attempt_id.uuid)
            )
        assert created["status"] == ExecutionAttemptStatus.PENDING.value
        assert created["revision"] == 1
        assert created["started_at"] is None
        assert created["finished_at"] is None
        assert created["created_at"].tzinfo is not None

        with pytest.raises(IntegrityError):
            async with database.session() as session:
                await session.execute(
                    insert(execution_attempts).values(
                        attempt_values(installation_id, task_id, attempt_number=2)
                    )
                )

        with pytest.raises(IntegrityError):
            async with database.session() as session:
                await session.execute(
                    update(tasks)
                    .where(tasks.c.id == other_task.uuid)
                    .values(current_attempt_id=attempt_id.uuid)
                )

        async with database.session() as session:
            await session.execute(
                update(execution_attempts)
                .where(execution_attempts.c.id == attempt_id.uuid)
                .values(
                    status=ExecutionAttemptStatus.FAILED.value,
                    updated_at=created["created_at"] + timedelta(seconds=1),
                    finished_at=created["created_at"] + timedelta(seconds=1),
                )
            )

        with pytest.raises(IntegrityError):
            async with database.session() as session:
                await session.execute(
                    insert(execution_attempts).values(
                        attempt_values(installation_id, task_id, attempt_number=1)
                    )
                )

        async with database.session() as session:
            retry = attempt_values(installation_id, task_id, attempt_number=2)
            await session.execute(insert(execution_attempts).values(retry))

        assert other_installation != installation_id
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_attempt_constraints_reject_invalid_identity_scope_state_revision_and_time(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        installation_id, task_id = await seed_task(database)
        other_installation, other_task = await seed_task(database)
        invalid_cases: tuple[dict[str, object], ...] = (
            {"id": UUID("123e4567-e89b-12d3-a456-426614174000")},
            {"task_id": other_task.uuid},
            {"installation_id": other_installation.uuid},
            {"attempt_number": 0},
            {"revision": 0},
            {"status": "unknown"},
            {"updated_at": NOW - timedelta(microseconds=1)},
            {"started_at": NOW - timedelta(microseconds=1)},
            {"status": ExecutionAttemptStatus.FAILED.value},
            {"finished_at": NOW},
            {
                "status": ExecutionAttemptStatus.FAILED.value,
                "finished_at": NOW + timedelta(seconds=2),
                "updated_at": NOW + timedelta(seconds=1),
            },
        )
        for overrides in invalid_cases:
            values = attempt_values(installation_id, task_id)
            values.update(overrides)
            with pytest.raises(IntegrityError):
                async with database.session() as session:
                    await session.execute(insert(execution_attempts).values(values))
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_action_binding_ordinal_phase_and_outcome_are_database_enforced(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        installation_id, task_id = await seed_task(database)
        other_installation, other_task = await seed_task(database)
        attempt_id = ExecutionAttemptId.new()
        async with database.session() as session:
            await session.execute(
                insert(execution_attempts).values(
                    attempt_values(installation_id, task_id) | {"id": attempt_id.uuid}
                )
            )
            created = (
                (
                    await session.execute(
                        insert(task_actions)
                        .values(
                            id=ActionId.new().uuid,
                            execution_attempt_id=attempt_id.uuid,
                            task_id=task_id.uuid,
                            installation_id=installation_id.uuid,
                            ordinal=1,
                        )
                        .returning(*task_actions.c)
                    )
                )
                .mappings()
                .one()
            )
        assert created["status"] == ActionStatus.PLANNED.value
        assert created["outcome"] == ActionOutcome.PENDING.value
        assert created["revision"] == 1
        assert created["finished_at"] is None

        invalid_cases: tuple[dict[str, object], ...] = (
            {"id": UUID("123e4567-e89b-12d3-a456-426614174000"), "ordinal": 2},
            {"task_id": other_task.uuid, "ordinal": 2},
            {"installation_id": other_installation.uuid, "ordinal": 2},
            {"ordinal": 1},
            {"ordinal": 0},
            {"ordinal": 2, "revision": 0},
            {"ordinal": 2, "status": "unknown"},
            {"ordinal": 2, "outcome": "unknown"},
            {
                "ordinal": 2,
                "status": ActionStatus.VERIFIED.value,
                "outcome": ActionOutcome.PENDING.value,
                "finished_at": NOW,
            },
            {
                "ordinal": 2,
                "status": ActionStatus.DISPATCHED.value,
                "outcome": ActionOutcome.SUCCEEDED.value,
                "finished_at": NOW,
            },
            {
                "ordinal": 2,
                "status": ActionStatus.OUTCOME_UNCERTAIN.value,
                "outcome": ActionOutcome.OUTCOME_UNCERTAIN.value,
            },
            {"ordinal": 2, "updated_at": NOW - timedelta(microseconds=1)},
        )
        for overrides in invalid_cases:
            values = action_values(installation_id, task_id, attempt_id, ordinal=2)
            values.update(overrides)
            with pytest.raises(IntegrityError):
                async with database.session() as session:
                    await session.execute(insert(task_actions).values(values))

        async with database.session() as session:
            verified = action_values(installation_id, task_id, attempt_id, ordinal=2)
            verified.update(
                {
                    "status": ActionStatus.VERIFIED.value,
                    "outcome": ActionOutcome.SUCCEEDED.value,
                    "updated_at": NOW + timedelta(seconds=1),
                    "finished_at": NOW + timedelta(seconds=1),
                }
            )
            await session.execute(insert(task_actions).values(verified))
    finally:
        await reset_data(database)
        await database.close()
