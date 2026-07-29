"""LE-05 T1: editing projects on a real PostgreSQL.

Every assertion here reads the database back, either through the repository or
through raw Core statements that bypass it. A repository that only ever reads
its own writes proves nothing about what actually landed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest
from conftest import AlembicRunner
from sqlalchemy import delete, insert, select, text
from sqlalchemy.exc import IntegrityError

from automation_tool.control_plane.application.editing_projects import (
    EditingProjectRepositoryRejected,
)
from automation_tool.control_plane.domain import (
    CaptionStyle,
    EditingProject,
    EditingProjectId,
    InvalidEditingProjectModel,
    OutputSpec,
)
from automation_tool.control_plane.infrastructure.database import Database, editing_projects
from automation_tool.control_plane.infrastructure.database.editing_project_repository import (
    SqlAlchemyEditingProjectRepository,
)

# Deliberately off every boundary the domain defines, and carrying microseconds
# so a timestamp column that silently truncates would be caught here.
CREATED_AT = datetime(2026, 7, 29, 3, 21, 45, 123_456, tzinfo=UTC)
LATER = datetime(2026, 7, 29, 9, 2, 3, 456_789, tzinfo=UTC)
TITLE = "夏日露营 第一集"
FONT_KEY = "source-han-sans"


def make_project(
    project_id: EditingProjectId,
    *,
    title: str = TITLE,
    created_at: datetime = CREATED_AT,
) -> EditingProject:
    return EditingProject(
        project_id=project_id,
        title=title,
        output=OutputSpec(width=1280, height=720, fps=30),
        caption_style=CaptionStyle(
            font_key=FONT_KEY,
            font_px=48,
            stroke_px=3,
            line_spacing=1.25,
        ),
        created_at=created_at,
    )


def row_values(project_id: EditingProjectId, **overrides: object) -> dict[str, object]:
    """The exact column payload `save` is expected to write."""
    values: dict[str, object] = {
        "project_id": project_id.uuid,
        "title": TITLE,
        "output_width": 1280,
        "output_height": 720,
        "output_fps": 30,
        "caption_font_key": FONT_KEY,
        "caption_font_px": 48,
        "caption_stroke_px": 3,
        "caption_line_spacing": 1.25,
        "created_at": CREATED_AT,
    }
    values.update(overrides)
    return values


async def reset_data(database: Database) -> None:
    async with database.session() as session:
        await session.execute(delete(editing_projects))


async def stored_row(database: Database, project_id: EditingProjectId) -> dict[str, object]:
    async with database.session() as session:
        row = (
            (
                await session.execute(
                    select(editing_projects).where(editing_projects.c.project_id == project_id.uuid)
                )
            )
            .mappings()
            .one()
        )
    return dict(row)


@pytest.mark.asyncio
async def test_saved_project_lands_as_typed_columns_and_hydrates_back_equal(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyEditingProjectRepository(database)
    try:
        await reset_data(database)
        project_id = EditingProjectId.new()
        project = make_project(project_id)

        await repository.save(project)

        row = await stored_row(database, project_id)
        assert row == row_values(project_id)
        # Flattening the two value objects into columns is only worth anything if
        # the column types survive the round trip -- the reason the plan rejected
        # a JSONB blob for them.
        assert type(row["output_width"]) is int
        assert type(row["caption_font_px"]) is int
        assert type(row["caption_line_spacing"]) is float
        # asyncpg decodes TIMESTAMPTZ straight to UTC; pinned here so a driver
        # change that starts handing back local time is a test failure, not a
        # silent shift in every stored timestamp.
        created_at = row["created_at"]
        assert isinstance(created_at, datetime)
        assert created_at.tzinfo is UTC

        loaded = await repository.get(project_id)
        assert loaded == project
        assert loaded.created_at.tzinfo is UTC
        assert type(loaded.caption_style.line_spacing) is float
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_missing_project_and_duplicate_save_are_both_refused(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyEditingProjectRepository(database)
    try:
        await reset_data(database)
        project_id = EditingProjectId.new()

        with pytest.raises(EditingProjectRepositoryRejected):
            await repository.get(project_id)

        await repository.save(make_project(project_id))
        # The second project differs in two columns, not one: if only the title
        # moved, a uniqueness guard mistakenly placed on `created_at` would
        # refuse this row too and the test could not tell the two apart.
        with pytest.raises(EditingProjectRepositoryRejected):
            await repository.save(make_project(project_id, title="改名后的项目", created_at=LATER))

        # A rejected duplicate must not be an upsert in disguise.
        assert (await stored_row(database, project_id))["title"] == TITLE
        assert (await repository.get(project_id)).title == TITLE
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_duplicate_project_id_is_refused_by_postgresql_itself(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    """Violation injection: the conflict is structural, not an application check.

    A repository that looked the row up before inserting would pass the
    duplicate-save test above while still racing two concurrent callers. This
    inserts the conflicting row without going near the repository, so what it
    proves is that the primary key -- not any Python branch -- is what refuses.
    """
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        project_id = EditingProjectId.new()
        async with database.session() as session:
            await session.execute(insert(editing_projects).values(**row_values(project_id)))

        with pytest.raises(IntegrityError) as captured:
            async with database.session() as session:
                await session.execute(
                    insert(editing_projects).values(
                        **row_values(project_id, title="另一个项目", created_at=LATER)
                    )
                )

        # 23505 is unique_violation. Pinned because the repository's rejection
        # branch is only correct if this is what the driver really raises.
        assert getattr(captured.value.orig, "sqlstate", None) == "23505"
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_a_row_the_domain_would_refuse_is_refused_at_hydration(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    """The stored row is input, not truth.

    `caption_font_px = 0` is below the domain's floor and the table has no check
    constraint against it -- deliberately, because the bounds belong to LE-04 and
    the domain expresses cross-field ones SQL cannot. So the guard that has to
    hold is hydration going through the real constructor.
    """
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyEditingProjectRepository(database)
    try:
        await reset_data(database)
        project_id = EditingProjectId.new()
        async with database.session() as session:
            await session.execute(
                insert(editing_projects).values(**row_values(project_id, caption_font_px=0))
            )

        with pytest.raises(InvalidEditingProjectModel):
            await repository.get(project_id)
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_migration_creates_and_drops_the_table(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        assert await table_exists(database) is True
        alembic_runner(postgresql_url, "downgrade", "-1")
        assert await table_exists(database) is False
        alembic_runner(postgresql_url, "upgrade", "head")
        assert await table_exists(database) is True
    finally:
        await database.close()


async def table_exists(database: Database) -> bool:
    async with database.session() as session:
        found = cast(
            int,
            await session.scalar(
                text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name = 'editing_projects'"
                )
            ),
        )
    return found == 1
