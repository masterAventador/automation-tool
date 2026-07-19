from __future__ import annotations

import secrets
from datetime import UTC, datetime

import pytest
from conftest import AlembicRunner
from sqlalchemy import delete, insert, select, text
from sqlalchemy.exc import IntegrityError

from automation_tool.control_plane.domain import InstallationId, TaskId
from automation_tool.control_plane.infrastructure.database import (
    Database,
    douyin_search_exposure_definitions,
    installations,
    tasks,
)

PREVIOUS_REVISION = "20260718_0012"
HEAD_REVISION = "20260718_0015"
NOW = datetime(2026, 7, 18, 23, 40, tzinfo=UTC)
EXPECTED_COLUMNS = {
    "task_id",
    "installation_id",
    "template",
    "search_keyword",
    "action",
    "message_template",
    "target_limit",
    "minimum_interval_seconds",
    "maximum_interval_seconds",
    "preview_required",
    "final_confirmation_required",
}
EXPECTED_CONSTRAINTS = {
    "ck_douyin_search_exposure_action",
    "ck_douyin_search_exposure_interval",
    "ck_douyin_search_exposure_keyword",
    "ck_douyin_search_exposure_mandatory_confirmation",
    "ck_douyin_search_exposure_message_presence",
    "ck_douyin_search_exposure_message_safe",
    "ck_douyin_search_exposure_target_limit",
    "ck_douyin_search_exposure_template",
    "fk_douyin_search_exposure_task_binding",
    "pk_douyin_search_exposure_definitions",
    "uq_douyin_search_exposure_binding",
}


async def reset_data(
    database: Database,
    installation_ids: set[InstallationId] | None = None,
) -> None:
    async with database.session() as session:
        await session.execute(delete(douyin_search_exposure_definitions))
        await session.execute(delete(tasks))
        if installation_ids:
            await session.execute(
                delete(installations).where(
                    installations.c.id.in_(
                        installation_id.uuid for installation_id in installation_ids
                    )
                )
            )


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
                creation_idempotency_key=f"task:schema:{task_id}",
                created_at=NOW,
                updated_at=NOW,
            )
        )
    return task_id, installation_id


def definition_values(
    task_id: TaskId,
    installation_id: InstallationId,
) -> dict[str, object]:
    return {
        "task_id": task_id.uuid,
        "installation_id": installation_id.uuid,
        "template": "douyin.search_exposure.v1",
        "search_keyword": "新能源汽车",
        "action": "browse",
        "message_template": None,
        "target_limit": 10,
        "minimum_interval_seconds": 30,
        "maximum_interval_seconds": 90,
        "preview_required": True,
        "final_confirmation_required": True,
    }


@pytest.mark.asyncio
async def test_definition_migration_is_explicit_drift_free_and_reversible(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    alembic_runner(postgresql_url, "check")
    database = Database.from_url(postgresql_url)
    created_installations: set[InstallationId] = set()
    try:
        await reset_data(database)
        task_id, installation_id = await seed_task(database)
        created_installations.add(installation_id)
        async with database.session() as session:
            await session.execute(
                insert(douyin_search_exposure_definitions).values(
                    definition_values(task_id, installation_id)
                )
            )
            revision = await session.scalar(text("select version_num from alembic_version"))
            columns = set(
                await session.scalars(
                    text(
                        "select column_name from information_schema.columns "
                        "where table_schema = 'public' "
                        "and table_name = 'douyin_search_exposure_definitions'"
                    )
                )
            )
            constraints = set(
                await session.scalars(
                    text(
                        "select conname from pg_constraint where conrelid = "
                        "'public.douyin_search_exposure_definitions'::regclass"
                    )
                )
            )
        assert revision == HEAD_REVISION
        assert columns == EXPECTED_COLUMNS
        assert constraints >= EXPECTED_CONSTRAINTS

        alembic_runner(postgresql_url, "downgrade", PREVIOUS_REVISION)
        async with database.session() as session:
            removed = await session.scalar(
                text("select to_regclass('public.douyin_search_exposure_definitions')")
            )
            parent = await session.scalar(select(tasks.c.id).where(tasks.c.id == task_id.uuid))
        assert removed is None
        assert parent == task_id.uuid

    finally:
        alembic_runner(postgresql_url, "upgrade", "head")
        await reset_data(database, created_installations)
        await database.close()


@pytest.mark.asyncio
async def test_database_enforces_closed_definition_shape_and_task_binding(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    created_installations: set[InstallationId] = set()
    try:
        await reset_data(database)
        valid_task, valid_installation = await seed_task(database)
        created_installations.add(valid_installation)
        async with database.session() as session:
            await session.execute(
                insert(douyin_search_exposure_definitions).values(
                    definition_values(valid_task, valid_installation)
                )
            )

        invalid_overrides: tuple[dict[str, object], ...] = (
            {"template": "unknown.template"},
            {"search_keyword": " leading"},
            {"search_keyword": "line\nbreak"},
            {"search_keyword": "control\u0085character"},
            {"search_keyword": "词" * 81},
            {"action": "like"},
            {"action": "browse", "message_template": "not allowed"},
            {"action": "comment", "message_template": None},
            {"action": "comment", "message_template": "password=private-value"},
            {"target_limit": 0},
            {"target_limit": 101},
            {"minimum_interval_seconds": 91, "maximum_interval_seconds": 90},
            {"maximum_interval_seconds": 3601},
            {"preview_required": False},
            {"final_confirmation_required": False},
        )
        for overrides in invalid_overrides:
            task_id, installation_id = await seed_task(database)
            created_installations.add(installation_id)
            values = definition_values(task_id, installation_id)
            values.update(overrides)
            with pytest.raises(IntegrityError):
                async with database.session() as session:
                    await session.execute(insert(douyin_search_exposure_definitions).values(values))

        bound_task, bound_installation = await seed_task(database)
        created_installations.add(bound_installation)
        _, other_installation = await seed_task(database)
        created_installations.add(other_installation)
        wrong_binding = definition_values(bound_task, bound_installation)
        wrong_binding["installation_id"] = other_installation.uuid
        with pytest.raises(IntegrityError):
            async with database.session() as session:
                await session.execute(
                    insert(douyin_search_exposure_definitions).values(wrong_binding)
                )
    finally:
        await reset_data(database, created_installations)
        await database.close()
