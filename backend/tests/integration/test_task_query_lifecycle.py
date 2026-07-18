from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

import pytest
from conftest import AlembicRunner
from sqlalchemy import delete, insert

from automation_tool.control_plane.domain import InstallationId, TaskId
from automation_tool.control_plane.infrastructure.database import Database, installations, tasks
from automation_tool.control_plane.infrastructure.database.task_repository import (
    SqlAlchemyTaskRepository,
)

NOW = datetime(2026, 7, 18, 17, 0, tzinfo=UTC)


async def reset_data(database: Database) -> None:
    async with database.session() as session:
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
async def test_repository_lists_stable_keyset_pages_in_scope_and_hides_other_installations(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyTaskRepository(database)
    try:
        await reset_data(database)
        installation_id = await seed_installation(database)
        other_installation = await seed_installation(database)
        created = []
        for offset in range(4):
            result = await repository.create(
                task_id=TaskId.new(),
                installation_id=installation_id,
                idempotency_key=f"task:query:{offset}",
                created_at=NOW + timedelta(minutes=offset),
            )
            created.append(result.task)
        other = (
            await repository.create(
                task_id=TaskId.new(),
                installation_id=other_installation,
                idempotency_key="task:query:other",
                created_at=NOW + timedelta(minutes=10),
            )
        ).task

        first = await repository.list_page(
            installation_id=installation_id,
            before_updated_at=None,
            before_task_id=None,
            limit=3,
        )
        second = await repository.list_page(
            installation_id=installation_id,
            before_updated_at=first[-1].updated_at,
            before_task_id=first[-1].task_id,
            limit=3,
        )

        assert first == tuple(reversed(created[1:]))
        assert second == (created[0],)
        assert (
            await repository.get(
                task_id=other.task_id,
                installation_id=installation_id,
            )
            is None
        )
        assert other not in first + second
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_repository_uses_task_id_as_the_deterministic_timestamp_tiebreaker(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyTaskRepository(database)
    try:
        await reset_data(database)
        installation_id = await seed_installation(database)
        records = []
        for index in range(3):
            records.append(
                (
                    await repository.create(
                        task_id=TaskId.new(),
                        installation_id=installation_id,
                        idempotency_key=f"task:query:tied:{index}",
                        created_at=NOW,
                    )
                ).task
            )

        listed = await repository.list_page(
            installation_id=installation_id,
            before_updated_at=None,
            before_task_id=None,
            limit=3,
        )

        assert listed == tuple(
            sorted(records, key=lambda record: record.task_id.uuid, reverse=True)
        )
    finally:
        await reset_data(database)
        await database.close()
