from __future__ import annotations

import secrets
from datetime import UTC, datetime
from uuid import UUID

import pytest
from conftest import AlembicRunner
from sqlalchemy import delete, insert, text
from sqlalchemy.exc import IntegrityError

from automation_tool.control_plane.domain import InstallationId, TargetId, TaskId
from automation_tool.control_plane.infrastructure.database import (
    Database,
    installations,
    task_targets,
    tasks,
)

PREVIOUS_REVISION = "20260718_0015"
HEAD_REVISION = "20260720_0019"
NOW = datetime(2026, 7, 19, 0, 0, tzinfo=UTC)
EXPECTED_COLUMNS = {
    "id",
    "task_id",
    "installation_id",
    "ordinal",
    "platform_target_id",
    "dedupe_key",
    "display_name",
    "public_handle",
    "source",
    "page_revision",
    "disposition",
    "policy_version",
    "evaluated_at",
    "created_at",
}
EXPECTED_CONSTRAINTS = {
    "ck_task_targets_candidate_key",
    "ck_task_targets_disposition",
    "ck_task_targets_display_name",
    "ck_task_targets_id_uuid_v4",
    "ck_task_targets_ordinal_range",
    "ck_task_targets_page_revision_range",
    "ck_task_targets_platform_target_id",
    "ck_task_targets_policy_version",
    "ck_task_targets_public_handle",
    "ck_task_targets_source",
    "ck_task_targets_time_order",
    "fk_task_targets_task_binding",
    "pk_task_targets",
    "uq_task_targets_binding",
    "uq_task_targets_task_ordinal",
}


async def reset_data(database: Database) -> None:
    async with database.session() as session:
        await session.execute(delete(task_targets))
        await session.execute(delete(tasks))
        await session.execute(delete(installations))


async def seed_task(database: Database) -> tuple[TaskId, InstallationId]:
    task_id = TaskId.new()
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
        await session.execute(
            insert(tasks).values(
                id=task_id.uuid,
                installation_id=installation_id.uuid,
                creation_idempotency_key=f"task:target-schema:{task_id}",
                created_at=NOW,
                updated_at=NOW,
            )
        )
    return task_id, installation_id


def target_values(
    task_id: TaskId,
    installation_id: InstallationId,
    *,
    ordinal: int = 1,
) -> dict[str, object]:
    return {
        "id": TargetId.new().uuid,
        "task_id": task_id.uuid,
        "installation_id": installation_id.uuid,
        "ordinal": ordinal,
        "platform_target_id": f"creator-{ordinal:03d}",
        "dedupe_key": "atdck1_" + ("A" * 43),
        "display_name": f"创作者 {ordinal}",
        "public_handle": f"creator_{ordinal:03d}",
        "source": "general_search_author",
        "page_revision": 7,
        "disposition": "eligible",
        "policy_version": "douyin.candidate-policy.v1",
        "evaluated_at": NOW,
        "created_at": NOW,
    }


@pytest.mark.asyncio
async def test_target_migration_is_explicit_drift_free_and_reversible(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    alembic_runner(postgresql_url, "check")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        task_id, installation_id = await seed_task(database)
        async with database.session() as session:
            await session.execute(
                insert(task_targets).values(target_values(task_id, installation_id))
            )
            revision = await session.scalar(text("select version_num from alembic_version"))
            columns = set(
                await session.scalars(
                    text(
                        "select column_name from information_schema.columns "
                        "where table_schema = 'public' and table_name = 'task_targets'"
                    )
                )
            )
            constraints = set(
                await session.scalars(
                    text(
                        "select conname from pg_constraint "
                        "where conrelid = 'public.task_targets'::regclass"
                    )
                )
            )
            indexes = set(
                await session.scalars(
                    text(
                        "select indexname from pg_indexes "
                        "where schemaname = 'public' and tablename = 'task_targets'"
                    )
                )
            )
        assert revision == HEAD_REVISION
        assert columns == EXPECTED_COLUMNS
        assert constraints >= EXPECTED_CONSTRAINTS
        assert "ix_task_targets_installation_task_page" in indexes
        assert "ix_task_targets_installation_history" in indexes

        alembic_runner(postgresql_url, "downgrade", PREVIOUS_REVISION)
        async with database.session() as session:
            removed = await session.scalar(text("select to_regclass('public.task_targets')"))
            parent = await session.scalar(text("select to_regclass('public.tasks')"))
        assert removed is None
        assert parent == "tasks"
    finally:
        alembic_runner(postgresql_url, "upgrade", "head")
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_database_enforces_target_shape_scope_and_order_uniqueness(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        task_id, installation_id = await seed_task(database)
        _, other_installation = await seed_task(database)
        original = target_values(task_id, installation_id)
        async with database.session() as session:
            await session.execute(insert(task_targets).values(original))

        duplicate_order = target_values(task_id, installation_id)
        duplicate_order["dedupe_key"] = "atdck1_" + ("B" * 43)
        with pytest.raises(IntegrityError):
            async with database.session() as session:
                await session.execute(insert(task_targets).values(duplicate_order))

        duplicate_key = target_values(task_id, installation_id, ordinal=2)
        duplicate_key["dedupe_key"] = original["dedupe_key"]
        duplicate_key["platform_target_id"] = original["platform_target_id"]
        duplicate_key["disposition"] = "duplicate_in_task"
        async with database.session() as session:
            await session.execute(insert(task_targets).values(duplicate_key))

        wrong_scope = target_values(task_id, other_installation, ordinal=3)
        with pytest.raises(IntegrityError):
            async with database.session() as session:
                await session.execute(insert(task_targets).values(wrong_scope))

        invalid_overrides: tuple[dict[str, object], ...] = (
            {"id": UUID("123e4567-e89b-12d3-a456-426614174000"), "ordinal": 3},
            {"ordinal": 0},
            {"ordinal": 101},
            {"platform_target_id": "https://www.douyin.com/user/private"},
            {"dedupe_key": "private-key"},
            {"display_name": "line\nbreak"},
            {"public_handle": "invalid handle"},
            {"source": "profile_page"},
            {"page_revision": 0},
            {"page_revision": 9_007_199_254_740_992},
            {"disposition": "selected"},
            {"policy_version": "candidate-policy.latest"},
            {"created_at": NOW.replace(year=2025)},
        )
        for index, overrides in enumerate(invalid_overrides, start=3):
            values = target_values(task_id, installation_id, ordinal=index)
            values.update(overrides)
            with pytest.raises(IntegrityError):
                async with database.session() as session:
                    await session.execute(insert(task_targets).values(values))
    finally:
        await reset_data(database)
        await database.close()
