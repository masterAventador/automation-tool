"""LE-05 T1: editing projects on a real PostgreSQL.

Every assertion here reads the database back, either through the repository or
through raw Core statements that bypass it. A repository that only ever reads
its own writes proves nothing about what actually landed.
"""

from __future__ import annotations

import secrets
import traceback
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest
from alembic_head import HEAD_REVISION
from conftest import AlembicRunner
from sqlalchemy import delete, insert, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from automation_tool.control_plane.application.editing_projects import (
    EditingProjectAlreadyRegistered,
    EditingProjectDataRejected,
    EditingProjectNotFound,
    EditingProjectPersistenceUnavailable,
)
from automation_tool.control_plane.domain import (
    MAX_PROJECT_TITLE_CHARACTERS,
    CaptionStyle,
    EditingProject,
    EditingProjectId,
    InstallationId,
    InvalidEditingProjectModel,
    OutputSpec,
)
from automation_tool.control_plane.infrastructure.database import (
    Database,
    editing_projects,
    installations,
)
from automation_tool.control_plane.infrastructure.database.editing_project_repository import (
    SqlAlchemyEditingProjectRepository,
)

# Deliberately off every boundary the domain defines, and carrying microseconds
# so a timestamp column that silently truncates would be caught here.
CREATED_AT = datetime(2026, 7, 29, 3, 21, 45, 123_456, tzinfo=UTC)
LATER = datetime(2026, 7, 29, 9, 2, 3, 456_789, tzinfo=UTC)
TITLE = "夏日露营 第一集"
FONT_KEY = "source-han-sans"

PREVIOUS_REVISION = "20260728_0035"

# (data_type, is_nullable, character_maximum_length) straight out of
# `information_schema`. Nullability is asserted because the repository reads
# every one of these columns unconditionally: a column that quietly became
# NULLable would not fail a single round-trip test, it would fail in production
# on the first row that used the new freedom.
EXPECTED_COLUMNS = {
    "project_id": ("uuid", "NO", None),
    "installation_id": ("uuid", "NO", None),
    "title": ("character varying", "NO", 200),
    "output_width": ("integer", "NO", None),
    "output_height": ("integer", "NO", None),
    "output_fps": ("integer", "NO", None),
    "caption_font_key": ("character varying", "NO", 64),
    "caption_font_px": ("integer", "NO", None),
    "caption_stroke_px": ("integer", "NO", None),
    "caption_line_spacing": ("double precision", "NO", None),
    "created_at": ("timestamp with time zone", "NO", None),
}
EXPECTED_CONSTRAINTS = {
    "pk_editing_projects",
    "fk_editing_projects_installation",
    "uq_editing_projects_project_installation",
}
EXPECTED_INDEXES = {"ix_editing_projects_installation_created_project"}
OWNER = InstallationId.parse("00000000-0000-4000-8000-000000000001")


def forged_identifier(value: UUID) -> EditingProjectId:
    """An `EditingProjectId` holding a UUID its constructor would never accept.

    `EditingProjectId(UUID(int=0))` raises, so a stored row whose `project_id`
    is not a v4 UUID cannot be addressed through the normal constructor at all.
    Building the instance without it is what lets the test reach such a row --
    the row itself gets there through a plain INSERT, which the `uuid` column
    accepts happily. Subclassing is not an option: the class is `@final`.
    """
    identifier = object.__new__(EditingProjectId)
    object.__setattr__(identifier, "_value", value)
    return identifier


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


def row_values(project_id: UUID, **overrides: object) -> dict[str, object]:
    """The exact column payload `save` is expected to write."""
    values: dict[str, object] = {
        "project_id": project_id,
        "installation_id": OWNER.uuid,
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
        exists = await session.scalar(
            select(installations.c.id).where(installations.c.id == OWNER.uuid)
        )
        if exists is None:
            await session.execute(
                insert(installations).values(
                    id=OWNER.uuid,
                    device_public_key=secrets.token_bytes(32),
                )
            )


async def seed_installation(database: Database) -> InstallationId:
    installation_id = InstallationId.new()
    async with database.session() as session:
        await session.execute(
            insert(installations).values(
                id=installation_id.uuid,
                device_public_key=secrets.token_bytes(32),
            )
        )
    return installation_id


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


async def insert_row(database: Database, project_id: UUID, **overrides: object) -> None:
    async with database.session() as session:
        await session.execute(
            insert(editing_projects).values(**row_values(project_id, **overrides))
        )


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

        await repository.save(project, OWNER)

        row = await stored_row(database, project_id)
        assert row == row_values(project_id.uuid)
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

        loaded = await repository.get(project_id, OWNER)
        assert loaded == project
        assert loaded.created_at.tzinfo is UTC
        assert type(loaded.caption_style.line_spacing) is float
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_the_longest_values_the_domain_accepts_still_fit_the_columns(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    """Ties the domain's two text limits to the real column widths.

    `caption_font_key`'s limit is written three times with no reference between
    any of them: `varchar(64)` in the migration, `String(length=64)` in
    `schema.py`, and `{0,63}` inside the domain's font-key pattern. `title` is
    better off -- `schema.py` imports `MAX_PROJECT_TITLE_CHARACTERS`, so that
    one is a reference -- but the migration still spells 200 out, because a
    migration is frozen history and must not import a constant that can move
    under it. Either way, widening the domain without widening the column turns
    a clean validation error into a `StringDataRightTruncation` at insert time,
    on the one input a user controls.

    The pair of assertions is what pins it: the longest accepted value must
    survive a real round trip, and one character more must be refused by the
    domain. Together they say the two limits are the same number, without
    needing a fourth copy of it.

    The title is CJK, so it is 200 characters and 600 bytes. PostgreSQL counts
    `varchar` in characters; a column that counted bytes would fail here.
    """
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyEditingProjectRepository(database)
    longest_title = "标" * MAX_PROJECT_TITLE_CHARACTERS
    longest_font_key = "a" + "b" * 63
    try:
        await reset_data(database)
        project_id = EditingProjectId.new()
        project = EditingProject(
            project_id=project_id,
            title=longest_title,
            output=OutputSpec(width=1280, height=720, fps=30),
            caption_style=CaptionStyle(
                font_key=longest_font_key,
                font_px=48,
                stroke_px=3,
                line_spacing=1.25,
            ),
            created_at=CREATED_AT,
        )

        await repository.save(project, OWNER)
        assert await repository.get(project_id, OWNER) == project
        row = await stored_row(database, project_id)
        assert row["title"] == longest_title
        assert row["caption_font_key"] == longest_font_key

        with pytest.raises(InvalidEditingProjectModel):
            make_project(EditingProjectId.new(), title=longest_title + "标")
        with pytest.raises(InvalidEditingProjectModel):
            CaptionStyle(
                font_key=longest_font_key + "b",
                font_px=48,
                stroke_px=3,
                line_spacing=1.25,
            )
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_missing_project_and_duplicate_save_are_refused_differently(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    """The two refusals are distinguishable, and neither is "unavailable".

    A caller that cannot tell "already there" from "not there" from "the
    database is down" cannot decide whether retrying is safe, and the REST layer
    above cannot choose between 409, 404 and 503.
    """
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyEditingProjectRepository(database)
    try:
        await reset_data(database)
        project_id = EditingProjectId.new()

        with pytest.raises(EditingProjectNotFound):
            await repository.get(project_id, OWNER)

        await repository.save(make_project(project_id), OWNER)
        # The second project differs in two columns, not one: if only the title
        # moved, a uniqueness guard mistakenly placed on `created_at` would
        # refuse this row too and the test could not tell the two apart.
        with pytest.raises(EditingProjectAlreadyRegistered):
            await repository.save(
                make_project(project_id, title="改名后的项目", created_at=LATER),
                OWNER,
            )

        # A rejected duplicate must not be an upsert in disguise.
        assert (await stored_row(database, project_id))["title"] == TITLE
        assert (await repository.get(project_id, OWNER)).title == TITLE
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_project_pages_use_created_at_and_project_id_as_one_total_order(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    """Equal timestamps cannot make a project repeat or disappear across pages."""
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyEditingProjectRepository(database)
    identifiers = tuple(
        EditingProjectId.parse(f"00000000-0000-4000-8000-{suffix:012x}") for suffix in range(1, 5)
    )
    try:
        await reset_data(database)
        for index, project_id in enumerate(identifiers):
            await repository.save(
                make_project(
                    project_id,
                    title=f"同一时刻项目 {index}",
                    created_at=CREATED_AT,
                ),
                OWNER,
            )

        first = await repository.list_page(
            installation_id=OWNER,
            before_created_at=None,
            before_project_id=None,
            limit=2,
        )
        second = await repository.list_page(
            installation_id=OWNER,
            before_created_at=first[-1].created_at,
            before_project_id=first[-1].project_id,
            limit=2,
        )

        assert tuple(project.project_id for project in first + second) == tuple(
            reversed(identifiers)
        )
        assert len({project.project_id for project in first + second}) == 4

        with pytest.raises(EditingProjectDataRejected):
            await repository.list_page(
                installation_id=OWNER,
                before_created_at=CREATED_AT,
                before_project_id=None,
                limit=2,
            )
        with pytest.raises(EditingProjectDataRejected):
            await repository.list_page(
                installation_id=OWNER,
                before_created_at=None,
                before_project_id=None,
                limit=102,
            )
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_editing_projects_have_a_database_enforced_installation_owner(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    """Scoped writes and reads enforce one owner in PostgreSQL, not in memory."""
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyEditingProjectRepository(database)
    owner_ids: tuple[InstallationId, ...] = ()
    try:
        await reset_data(database)
        owner = await seed_installation(database)
        other = await seed_installation(database)
        owner_ids = (owner, other)
        owned = make_project(EditingProjectId.new(), title="当前设备项目")
        foreign = make_project(
            EditingProjectId.new(),
            title="其它设备私有项目",
            created_at=LATER,
        )

        await repository.save(owned, owner)
        await repository.save(foreign, other)

        assert await repository.get(owned.project_id, owner) == owned
        with pytest.raises(EditingProjectNotFound):
            await repository.get(foreign.project_id, owner)
        assert await repository.list_page(
            installation_id=owner,
            before_created_at=None,
            before_project_id=None,
            limit=20,
        ) == (owned,)

        async with database.session() as session:
            ownership_rows = (
                (
                    await session.execute(
                        select(
                            editing_projects.c.project_id,
                            editing_projects.c.installation_id,
                        ).order_by(editing_projects.c.project_id)
                    )
                )
                .mappings()
                .all()
            )
        assert {(row["project_id"], row["installation_id"]) for row in ownership_rows} == {
            (owned.project_id.uuid, owner.uuid),
            (foreign.project_id.uuid, other.uuid),
        }

        with pytest.raises(IntegrityError):
            await insert_row(
                database,
                EditingProjectId.new().uuid,
                installation_id=InstallationId.new().uuid,
            )
    finally:
        await reset_data(database)
        if owner_ids:
            async with database.session() as session:
                await session.execute(
                    delete(installations).where(
                        installations.c.id.in_(
                            installation_id.uuid for installation_id in owner_ids
                        )
                    )
                )
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
        await insert_row(database, project_id.uuid)

        with pytest.raises(IntegrityError) as captured:
            await insert_row(database, project_id.uuid, title="另一个项目", created_at=LATER)

        # 23505 is unique_violation. Pinned because the repository's rejection
        # branch is only correct if this is what the driver really raises.
        assert getattr(captured.value.orig, "sqlstate", None) == "23505"
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_rows_the_domain_would_refuse_are_refused_at_hydration(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    """The stored row is input, not truth.

    None of these rows can be refused by the table, and the first three cannot
    be refused by either value object either -- they are exactly the shapes the
    migration's docstring names when it argues that check constraints could only
    ever cover a subset:

    * a caption taller than its own frame is cross-field, and neither
      `OutputSpec` nor `CaptionStyle` can see both halves;
    * an untrimmed title and a title carrying a control character are not
      expressible as a sane check constraint.

    `caption_font_px = 0` is the one a check constraint could have caught, kept
    so the plain out-of-range case stays covered. Only the last one is refused
    below the aggregate root, so a hydration that skipped
    `EditingProject.__post_init__` alone would still pass on it -- and fail the
    other three.
    """
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyEditingProjectRepository(database)
    try:
        await reset_data(database)
        refused_rows = (
            # 150px captions on a 130px frame: legal font size, legal frame size,
            # and only the aggregate root can see that they contradict.
            {"caption_font_px": 150, "output_height": 130},
            {"title": " 前后有空格 "},
            {"title": "标题里\x07有控制字符"},
            {"caption_font_px": 0},
        )
        for overrides in refused_rows:
            project_id = EditingProjectId.new()
            await insert_row(database, project_id.uuid, **overrides)
            with pytest.raises(InvalidEditingProjectModel):
                await repository.get(project_id, OWNER)
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_a_stored_identifier_of_the_wrong_uuid_version_is_refused(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    """A `uuid` column accepts every version; `EditingProjectId` accepts only v4.

    The nil UUID lands in the table without complaint, so parsing it back is a
    third way hydration can fail. It surfaces as the same domain error as every
    other unusable row, rather than as `InvalidResourceId`, which is not part of
    anything this repository documents.
    """
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyEditingProjectRepository(database)
    try:
        await reset_data(database)
        nil_uuid = UUID(int=0)
        await insert_row(database, nil_uuid)
        assert await stored_project_ids(database) == [nil_uuid]

        with pytest.raises(InvalidEditingProjectModel):
            await repository.get(forged_identifier(nil_uuid), OWNER)
    finally:
        await reset_data(database)
        await database.close()


async def stored_project_ids(database: Database) -> list[UUID]:
    async with database.session() as session:
        return [
            cast(UUID, value)
            for value in (await session.scalars(select(editing_projects.c.project_id))).all()
        ]


@pytest.mark.asyncio
async def test_wrong_credentials_are_refused_without_leaking_the_identity(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    """A refused login is neither an `OSError` nor a `SQLAlchemyError`.

    A refused *connection* is an `OSError`. A refused *login* is an
    `asyncpg.exceptions.InvalidPasswordError`, and its message names the role.
    Measured MRO:

        InvalidPasswordError -> InvalidAuthorizationSpecificationError
        -> PostgresError -> PostgresMessage -> Exception -> BaseException

    `SQLAlchemyError` appears nowhere on it, and neither does `OSError`. Three
    more classes reach the same gap along the same spine, only one of which
    derives from `PostgresError` directly: `InsufficientPrivilegeError`
    (via `SyntaxOrAccessError`), `InvalidCatalogNameError` (direct) and
    `TooManyConnectionsError` (via `InsufficientResourcesError`) -- and a
    saturated connection pool is ordinary production traffic, not a
    misconfiguration.
    """
    alembic_runner(postgresql_url, "upgrade", "head")
    url = make_url(postgresql_url)
    role = url.username
    assert role is not None
    database = Database.from_url(
        url.set(password="le05_wrong_password").render_as_string(hide_password=False)
    )
    try:
        repository = SqlAlchemyEditingProjectRepository(database)
        with pytest.raises(EditingProjectPersistenceUnavailable) as loaded:
            await repository.get(EditingProjectId.new(), OWNER)
        with pytest.raises(EditingProjectPersistenceUnavailable) as saved:
            await repository.save(make_project(EditingProjectId.new()), OWNER)
        for captured in (loaded, saved):
            rendered = "".join(traceback.format_exception(captured.value))
            assert role not in rendered
            assert "password authentication failed" not in rendered
            assert captured.value.__cause__ is None
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_migration_creates_the_declared_shape_and_drops_it(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        async with database.session() as session:
            revision = await session.scalar(text("select version_num from alembic_version"))
            columns = {
                cast(str, row["column_name"]): (
                    cast(str, row["data_type"]),
                    cast(str, row["is_nullable"]),
                    row["character_maximum_length"],
                )
                for row in (
                    await session.execute(
                        text(
                            "select column_name, data_type, is_nullable, "
                            "character_maximum_length from information_schema.columns "
                            "where table_schema = 'public' "
                            "and table_name = 'editing_projects'"
                        )
                    )
                )
                .mappings()
                .all()
            }
            constraints = set(
                await session.scalars(
                    text(
                        "select conname from pg_constraint where conrelid = "
                        "'public.editing_projects'::regclass"
                    )
                )
            )
            indexes = set(
                await session.scalars(
                    text(
                        "select indexname from pg_indexes "
                        "where schemaname = 'public' "
                        "and tablename = 'editing_projects'"
                    )
                )
            )
        assert revision == HEAD_REVISION
        assert columns == EXPECTED_COLUMNS
        assert constraints >= EXPECTED_CONSTRAINTS
        assert indexes >= EXPECTED_INDEXES
        # `schema.py` and the migration each declare these widths separately, and
        # until this assertion existed nothing compared them: narrowing the Table
        # to `String(length=32)` left the whole suite green, because every other
        # test reads either the migrated database or this file's own constants.
        declared = {
            column.name: getattr(column.type, "length", None) for column in editing_projects.columns
        }
        assert declared == {name: shape[2] for name, shape in EXPECTED_COLUMNS.items()}

        alembic_runner(postgresql_url, "downgrade", PREVIOUS_REVISION)
        async with database.session() as session:
            removed = await session.scalar(text("select to_regclass('public.editing_projects')"))
        assert removed is None
    finally:
        alembic_runner(postgresql_url, "upgrade", "head")
        await database.close()
