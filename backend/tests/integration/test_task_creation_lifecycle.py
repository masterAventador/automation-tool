from __future__ import annotations

import asyncio
import secrets
from datetime import UTC, datetime

import pytest
from conftest import AlembicRunner
from sqlalchemy import delete, insert, select

from automation_tool.control_plane.application.tasks import (
    TaskCreationResult,
    TaskPersistenceRejected,
)
from automation_tool.control_plane.domain import (
    DouyinSearchExposureAction,
    DouyinSearchExposureDefinition,
    InstallationId,
    TaskId,
)
from automation_tool.control_plane.infrastructure.database import (
    Database,
    douyin_search_exposure_definitions,
    installations,
    tasks,
)
from automation_tool.control_plane.infrastructure.database.task_repository import (
    SqlAlchemyTaskRepository,
)

NOW = datetime(2026, 7, 18, 6, 0, tzinfo=UTC)
DEFINITION = DouyinSearchExposureDefinition(
    search_keyword="新能源汽车",
    action=DouyinSearchExposureAction.BROWSE,
    message_template=None,
    target_limit=10,
    minimum_interval_seconds=30,
    maximum_interval_seconds=90,
    preview_required=True,
    final_confirmation_required=True,
)


async def reset_data(database: Database) -> None:
    async with database.session() as session:
        await session.execute(delete(douyin_search_exposure_definitions))
        await session.execute(delete(tasks))
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


@pytest.mark.asyncio
async def test_repository_replays_same_installation_key_and_isolates_other_installations(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyTaskRepository(database)
    try:
        await reset_data(database)
        first_installation = await seed_installation(database)
        second_installation = await seed_installation(database)
        first = await repository.create(
            task_id=TaskId.new(),
            installation_id=first_installation,
            idempotency_key="task:create:shared",
            definition=DEFINITION,
            created_at=NOW,
        )
        replay = await repository.create(
            task_id=TaskId.new(),
            installation_id=first_installation,
            idempotency_key="task:create:shared",
            definition=DEFINITION,
            created_at=NOW,
        )
        other = await repository.create(
            task_id=TaskId.new(),
            installation_id=second_installation,
            idempotency_key="task:create:shared",
            definition=DEFINITION,
            created_at=NOW,
        )

        assert isinstance(first, TaskCreationResult) and first.created is True
        assert replay.created is False and replay.task == first.task
        assert other.created is True and other.task.task_id != first.task.task_id

        changed_definition = DouyinSearchExposureDefinition(
            search_keyword="智能驾驶",
            action=DouyinSearchExposureAction.BROWSE,
            message_template=None,
            target_limit=10,
            minimum_interval_seconds=30,
            maximum_interval_seconds=90,
            preview_required=True,
            final_confirmation_required=True,
        )
        with pytest.raises(TaskPersistenceRejected):
            await repository.create(
                task_id=TaskId.new(),
                installation_id=first_installation,
                idempotency_key="task:create:shared",
                definition=changed_definition,
                created_at=NOW,
            )
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_concurrent_same_key_has_one_created_task_and_one_replay(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyTaskRepository(database)
    try:
        await reset_data(database)
        installation_id = await seed_installation(database)
        results = await asyncio.gather(
            *(
                repository.create(
                    task_id=TaskId.new(),
                    installation_id=installation_id,
                    idempotency_key="task:create:concurrent",
                    definition=DEFINITION,
                    created_at=NOW,
                )
                for _ in range(2)
            )
        )

        assert sum(result.created for result in results) == 1
        assert results[0].task == results[1].task
        async with database.session() as session:
            persisted = list((await session.execute(select(tasks))).mappings())
        assert len(persisted) == 1
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_migration_backfills_existing_tasks_with_stable_protocol_keys(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    await reset_data(database)
    await database.close()

    installation_id = InstallationId.new()
    task_id = TaskId.new()
    try:
        alembic_runner(postgresql_url, "downgrade", "20260718_0009")
        legacy_database = Database.from_url(postgresql_url)
        try:
            async with legacy_database.session() as session:
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
                        status="draft",
                        revision=1,
                        created_at=NOW,
                        updated_at=NOW,
                    )
                )
        finally:
            await legacy_database.close()

        alembic_runner(postgresql_url, "upgrade", "head")
        upgraded_database = Database.from_url(postgresql_url)
        try:
            async with upgraded_database.session() as session:
                key = await session.scalar(
                    select(tasks.c.creation_idempotency_key).where(tasks.c.id == task_id.uuid)
                )
            assert key == f"legacy:{task_id}"
        finally:
            await reset_data(upgraded_database)
            await upgraded_database.close()
    finally:
        alembic_runner(postgresql_url, "upgrade", "head")
