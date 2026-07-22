import subprocess

import pytest
from conftest import AlembicRunner
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from automation_tool.control_plane import create_app
from automation_tool.control_plane.application.task_event_convergence import (
    TaskEventConvergenceService,
)
from automation_tool.control_plane.application.task_event_stream import TaskEventStreamService
from automation_tool.control_plane.domain import DependencyUnavailable
from automation_tool.control_plane.infrastructure.database import Database

HEAD_REVISION = "20260721_0027"


@pytest.mark.asyncio
async def test_empty_database_upgrades_and_rolls_back(
    postgresql_url: str, alembic_runner: AlembicRunner
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        async with database.session() as session:
            revision = await session.scalar(text("select version_num from alembic_version"))
            database_name = await session.scalar(text("select current_database()"))
        assert revision == HEAD_REVISION
        assert database_name == "automation_tool_test"

        alembic_runner(postgresql_url, "downgrade", "base")

        async with database.session() as session:
            remaining = await session.scalar(text("select count(*) from alembic_version"))
        assert remaining == 0

        alembic_runner(postgresql_url, "upgrade", "head")
        async with database.session() as session:
            restored_revision = await session.scalar(
                text("select version_num from alembic_version")
            )
        assert restored_revision == HEAD_REVISION
    finally:
        await database.close()


def test_health_checks_a_real_postgresql_connection(postgresql_url: str) -> None:
    database = Database.from_url(postgresql_url)
    app = create_app(database=database)

    assert isinstance(app.state.task_event_convergence_service, TaskEventConvergenceService)
    assert isinstance(app.state.task_event_stream_service, TaskEventStreamService)
    with TestClient(app) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_connection_pool_exhaustion_is_a_safe_dependency_failure(
    postgresql_url: str,
) -> None:
    engine = create_async_engine(
        postgresql_url,
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.01,
    )
    database = Database(engine)
    try:
        async with engine.connect():
            with pytest.raises(DependencyUnavailable) as captured:
                await database.check_connection()
        assert captured.value.dependency == "postgresql"
        assert captured.value.__cause__ is None
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_unknown_migration_target_fails_without_changing_the_current_revision(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")

    with pytest.raises(subprocess.CalledProcessError):
        alembic_runner(postgresql_url, "upgrade", "h815_unknown_revision")

    database = Database.from_url(postgresql_url)
    try:
        async with database.session() as session:
            revision = await session.scalar(text("select version_num from alembic_version"))
        assert revision == HEAD_REVISION
    finally:
        await database.close()
