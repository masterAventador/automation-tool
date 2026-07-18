import pytest
from conftest import AlembicRunner
from fastapi.testclient import TestClient
from sqlalchemy import text

from automation_tool.control_plane import create_app
from automation_tool.control_plane.infrastructure.database import Database

HEAD_REVISION = "20260718_0009"


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

    with TestClient(create_app(database=database)) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
