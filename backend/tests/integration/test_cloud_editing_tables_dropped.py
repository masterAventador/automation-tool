"""The 0035 migration must actually remove the cloud-editing tables.

Dropping the table declarations from schema.py only changes what new
databases get created with. A database that already ran 0032 and 0034 keeps
all three tables until a migration removes them, which is why the migration
files stay on disk and 0035 exists.
"""

from __future__ import annotations

import pytest
from conftest import AlembicRunner
from sqlalchemy import text

from automation_tool.control_plane.infrastructure.database.session import Database

_REMOVED_TABLES = (
    "aliyun_editing_intents",
    "editing_output_lineages",
    "editing_output_artifacts",
)


@pytest.mark.asyncio
async def test_migrations_leave_no_cloud_editing_tables(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        async with database.session() as session:
            result = await session.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name = ANY(:names)"
                ),
                {"names": list(_REMOVED_TABLES)},
            )
            assert result.scalars().all() == []
    finally:
        await database.close()
