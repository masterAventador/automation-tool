import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from conftest import AlembicRunner
from sqlalchemy import delete, insert, text
from sqlalchemy.exc import IntegrityError

from automation_tool.control_plane.domain import InstallationId, TaskId, TaskStatus
from automation_tool.control_plane.infrastructure.database import (
    Database,
    device_credentials,
    device_sessions,
    douyin_search_exposure_definitions,
    installation_registration_challenges,
    installations,
    tasks,
)

PREVIOUS_REVISION = "20260718_0005"
HEAD_REVISION = "20260718_0013"
NOW = datetime(2026, 7, 18, 14, 0, tzinfo=UTC)
EXPECTED_COLUMNS = {
    "id",
    "installation_id",
    "creation_idempotency_key",
    "current_attempt_id",
    "last_event_sequence",
    "status",
    "revision",
    "created_at",
    "updated_at",
}
EXPECTED_CONSTRAINTS = {
    "ck_tasks_creation_idempotency_key",
    "pk_tasks",
    "fk_tasks_installation_id",
    "uq_tasks_binding",
    "uq_tasks_creation_idempotency",
    "ck_tasks_id_uuid_v4",
    "ck_tasks_revision_positive",
    "ck_tasks_status",
    "ck_tasks_timestamp_order",
}


async def reset_data(database: Database) -> None:
    async with database.session() as session:
        await session.execute(delete(douyin_search_exposure_definitions))
        await session.execute(delete(tasks))
        await session.execute(delete(installation_registration_challenges))
        await session.execute(delete(device_sessions))
        await session.execute(delete(device_credentials))
        await session.execute(delete(installations))


async def seed_installation(database: Database) -> InstallationId:
    installation_id = InstallationId.new()
    async with database.session() as session:
        await session.execute(
            insert(installations).values(
                id=installation_id.uuid,
                device_public_key=secrets.token_bytes(32),
                created_at=NOW,
                updated_at=NOW,
            )
        )
    return installation_id


def task_values(installation_id: InstallationId) -> dict[str, object]:
    return {
        "id": TaskId.new().uuid,
        "installation_id": installation_id.uuid,
        "creation_idempotency_key": f"task:test:{TaskId.new()}",
        "status": TaskStatus.DRAFT.value,
        "revision": 1,
        "created_at": NOW,
        "updated_at": NOW,
    }


@pytest.mark.asyncio
async def test_task_migration_upgrades_checks_and_downgrades_cleanly(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    alembic_runner(postgresql_url, "check")
    database = Database.from_url(postgresql_url)
    try:
        async with database.session() as session:
            revision = await session.scalar(text("select version_num from alembic_version"))
            table_name = await session.scalar(text("select to_regclass('public.tasks')"))
            columns = set(
                await session.scalars(
                    text(
                        "select column_name from information_schema.columns "
                        "where table_schema = 'public' and table_name = 'tasks'"
                    )
                )
            )
            constraints = set(
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
                        "select indexname from pg_indexes "
                        "where schemaname = 'public' and tablename = 'tasks'"
                    )
                )
            )
        assert revision == HEAD_REVISION
        assert table_name == "tasks"
        assert columns == EXPECTED_COLUMNS
        assert constraints >= EXPECTED_CONSTRAINTS
        assert "ix_tasks_installation_updated" in indexes

        alembic_runner(postgresql_url, "downgrade", PREVIOUS_REVISION)
        async with database.session() as session:
            downgraded_revision = await session.scalar(
                text("select version_num from alembic_version")
            )
            removed = await session.scalar(text("select to_regclass('public.tasks')"))
            installations_remain = await session.scalar(
                text("select to_regclass('public.installations')")
            )
        assert downgraded_revision == PREVIOUS_REVISION
        assert removed is None
        assert installations_remain == "installations"
    finally:
        await database.close()
        alembic_runner(postgresql_url, "upgrade", "head")


@pytest.mark.asyncio
async def test_task_defaults_and_installation_binding_are_real_database_constraints(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        installation_id = await seed_installation(database)
        task_id = TaskId.new()
        async with database.session() as session:
            created = (
                (
                    await session.execute(
                        insert(tasks)
                        .values(
                            id=task_id.uuid,
                            installation_id=installation_id.uuid,
                            creation_idempotency_key="task:test:defaults",
                        )
                        .returning(*tasks.c)
                    )
                )
                .mappings()
                .one()
            )

        assert created["id"] == task_id.uuid
        assert created["installation_id"] == installation_id.uuid
        assert created["creation_idempotency_key"] == "task:test:defaults"
        assert created["status"] == TaskStatus.DRAFT.value
        assert created["revision"] == 1
        assert created["created_at"].tzinfo is not None
        assert created["updated_at"] == created["created_at"]

        with pytest.raises(IntegrityError):
            async with database.session() as session:
                await session.execute(
                    insert(tasks).values(
                        id=TaskId.new().uuid,
                        installation_id=InstallationId.new().uuid,
                        creation_idempotency_key="task:test:unknown-installation",
                    )
                )
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_task_constraints_reject_invalid_identity_state_revision_and_time(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        installation_id = await seed_installation(database)
        invalid_cases: tuple[dict[str, object], ...] = (
            {"id": UUID("123e4567-e89b-12d3-a456-426614174000")},
            {"status": "RUNNING"},
            {"status": "unknown"},
            {"revision": 0},
            {"updated_at": NOW - timedelta(microseconds=1)},
        )
        for overrides in invalid_cases:
            values = task_values(installation_id)
            values.update(overrides)
            with pytest.raises(IntegrityError):
                async with database.session() as session:
                    await session.execute(insert(tasks).values(values))
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_task_id_is_globally_unique_while_binding_is_installation_scoped(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        first_installation = await seed_installation(database)
        second_installation = await seed_installation(database)
        original = task_values(first_installation)
        async with database.session() as session:
            await session.execute(insert(tasks).values(original))

        duplicate = task_values(second_installation)
        duplicate["id"] = original["id"]
        with pytest.raises(IntegrityError):
            async with database.session() as session:
                await session.execute(insert(tasks).values(duplicate))
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_task_creation_idempotency_is_valid_and_installation_scoped(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        first_installation = await seed_installation(database)
        second_installation = await seed_installation(database)
        original = task_values(first_installation)
        original["creation_idempotency_key"] = "task:test:shared"
        other_installation = task_values(second_installation)
        other_installation["creation_idempotency_key"] = "task:test:shared"
        async with database.session() as session:
            await session.execute(insert(tasks).values(original))
            await session.execute(insert(tasks).values(other_installation))

        duplicate = task_values(first_installation)
        duplicate["creation_idempotency_key"] = "task:test:shared"
        with pytest.raises(IntegrityError):
            async with database.session() as session:
                await session.execute(insert(tasks).values(duplicate))

        for invalid in ["", "-leading", "contains space", "a" * 129]:
            values = task_values(first_installation)
            values["creation_idempotency_key"] = invalid
            with pytest.raises(IntegrityError):
                async with database.session() as session:
                    await session.execute(insert(tasks).values(values))
    finally:
        await reset_data(database)
        await database.close()
