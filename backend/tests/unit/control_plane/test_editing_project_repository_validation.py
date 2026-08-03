"""Fail-closed branches around the PostgreSQL editing-project repository.

These cover what a real database cannot be made to produce on demand: a row that
is missing, a row malformed in a way the columns permit, and a session that
fails in each of the three ways the driver can fail. Behaviour against a live
PostgreSQL is in the integration suite.
"""

from __future__ import annotations

import inspect
import traceback
from datetime import UTC, datetime, timedelta, timezone
from typing import cast
from uuid import UUID

import pytest

# `asyncpg` ships no stubs. The import is here rather than behind a local
# equivalent because the point of the test below is what the real driver's
# exception inherits from -- a hand-rolled stand-in would assert nothing.
import asyncpg  # type: ignore[import-untyped]  # isort: skip
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from automation_tool.control_plane.application.editing_projects import (
    EditingProjectAlreadyRegistered,
    EditingProjectDataRejected,
    EditingProjectNotFound,
    EditingProjectPersistenceUnavailable,
)
from automation_tool.control_plane.domain import (
    CaptionStyle,
    EditingProject,
    EditingProjectId,
    InstallationId,
    InvalidEditingProjectModel,
    OutputSpec,
    TaskId,
)
from automation_tool.control_plane.infrastructure.database import Database, editing_projects
from automation_tool.control_plane.infrastructure.database import (
    editing_project_repository as repository_module,
)

# Distinctive enough that a leak into a message or traceback cannot be mistaken
# for anything else, and cannot pass by coincidence.
UNREACHABLE_URL = (
    "postgresql+asyncpg://le05_leaked_user:le05_leaked_password@127.0.0.1:1/le05_leaked_db"
)
LEAKED_TOKENS = (
    "le05_leaked_user",
    "le05_leaked_password",
    "le05_leaked_db",
    # What the underlying socket error says. `raise ... from None` suppresses the
    # context rather than clearing it, so this -- not `__context__ is None` -- is
    # what catches the suppression being dropped.
    "Connect call failed",
    "ConnectionRefusedError",
)

CREATED_AT = datetime(2026, 7, 29, 3, 21, 45, 123_456, tzinfo=UTC)
SHANGHAI = timezone(timedelta(hours=8))


class FailingSessionScope:
    def __init__(self, failure: Exception) -> None:
        self.failure = failure

    async def __aenter__(self) -> object:
        raise self.failure

    async def __aexit__(
        self,
        _exception_type: object,
        _exception: object,
        _traceback: object,
    ) -> None:
        return None


class FailingSessions:
    def __init__(self, failure: Exception) -> None:
        self.failure = failure

    def begin(self) -> FailingSessionScope:
        return FailingSessionScope(self.failure)


class ConstraintFailure(Exception):
    def __init__(self, constraint_name: str) -> None:
        super().__init__("private constraint detail")
        self.constraint_name = constraint_name


def integrity_failure(constraint_name: str) -> IntegrityError:
    original = Exception("private driver detail")
    original.__cause__ = ConstraintFailure(constraint_name)
    return IntegrityError("private statement", None, original)


class StubResult:
    """Just enough of a `Result` for `.mappings().one_or_none()`."""

    def __init__(self, row: RowMapping | None) -> None:
        self._row = row

    def mappings(self) -> StubResult:
        return self

    def one_or_none(self) -> RowMapping | None:
        return self._row

    def all(self) -> list[RowMapping]:
        return [] if self._row is None else [self._row]


class StubSession:
    def __init__(self, row: RowMapping | None) -> None:
        self._row = row

    async def execute(self, _statement: object) -> StubResult:
        return StubResult(self._row)


class StubSessionScope:
    def __init__(self, row: RowMapping | None) -> None:
        self._row = row

    async def __aenter__(self) -> StubSession:
        return StubSession(self._row)

    async def __aexit__(
        self,
        _exception_type: object,
        _exception: object,
        _traceback: object,
    ) -> None:
        return None


class StubSessions:
    def __init__(self, row: RowMapping | None) -> None:
        self._row = row

    def begin(self) -> StubSessionScope:
        return StubSessionScope(self._row)


def unreachable_database() -> Database:
    return Database.from_url(UNREACHABLE_URL, connect_timeout_seconds=0.05)


def make_project(project_id: EditingProjectId | None = None) -> EditingProject:
    return EditingProject(
        project_id=project_id or EditingProjectId.new(),
        title="夏日露营 第一集",
        output=OutputSpec(width=1280, height=720, fps=30),
        caption_style=CaptionStyle(
            font_key="source-han-sans",
            font_px=48,
            stroke_px=3,
            line_spacing=1.25,
        ),
        created_at=CREATED_AT,
    )


def hydration_row(**overrides: object) -> RowMapping:
    values: dict[str, object] = {
        "project_id": EditingProjectId.new().uuid,
        "title": "夏日露营 第一集",
        "output_width": 1280,
        "output_height": 720,
        "output_fps": 30,
        "caption_font_key": "source-han-sans",
        "caption_font_px": 48,
        "caption_stroke_px": 3,
        "caption_line_spacing": 1.25,
        "created_at": CREATED_AT,
    }
    values.update(overrides)
    return cast(RowMapping, values)


def test_repository_refuses_a_database_it_does_not_own() -> None:
    with pytest.raises(EditingProjectPersistenceUnavailable):
        repository_module.SqlAlchemyEditingProjectRepository(cast(Database, object()))


def test_repository_has_one_required_installation_scoped_api() -> None:
    repository_type = repository_module.SqlAlchemyEditingProjectRepository
    assert list(inspect.signature(repository_type.save).parameters) == [
        "self",
        "project",
        "installation_id",
    ]
    assert list(inspect.signature(repository_type.get).parameters) == [
        "self",
        "project_id",
        "installation_id",
    ]
    assert list(inspect.signature(repository_type.list_page).parameters) == [
        "self",
        "installation_id",
        "before_created_at",
        "before_project_id",
        "limit",
    ]
    assert not hasattr(repository_type, "save_for_installation")
    assert not hasattr(repository_type, "get_for_installation")
    assert not hasattr(repository_type, "list_page_for_installation")
    assert editing_projects.c.installation_id.nullable is False


@pytest.mark.asyncio
async def test_repository_refuses_foreign_argument_types() -> None:
    database = unreachable_database()
    try:
        repository = repository_module.SqlAlchemyEditingProjectRepository(database)
        installation_id = InstallationId.new()
        with pytest.raises(EditingProjectDataRejected):
            await repository.save(cast(EditingProject, object()), installation_id)
        # A bare UUID and a sibling identifier carry exactly the value the column
        # would accept, so the type has to be checked before the statement is
        # built rather than left to the database.
        with pytest.raises(EditingProjectDataRejected):
            await repository.get(
                cast(EditingProjectId, EditingProjectId.new().uuid),
                installation_id,
            )
        with pytest.raises(EditingProjectDataRejected):
            await repository.get(
                cast(EditingProjectId, TaskId.new()),
                InstallationId.new(),
            )
        with pytest.raises(EditingProjectDataRejected):
            await repository.save(
                make_project(),
                cast(InstallationId, object()),
            )
        with pytest.raises(EditingProjectDataRejected):
            await repository.get(
                cast(EditingProjectId, TaskId.new()),
                InstallationId.new(),
            )
        with pytest.raises(EditingProjectDataRejected):
            await repository.get(
                EditingProjectId.new(),
                cast(InstallationId, object()),
            )
        with pytest.raises(EditingProjectDataRejected):
            await repository.list_page(
                installation_id=installation_id,
                before_created_at=None,
                before_project_id=None,
                limit=cast(int, True),
            )
        with pytest.raises(EditingProjectDataRejected):
            await repository.list_page(
                installation_id=installation_id,
                before_created_at=SHANGHAI.fromutc(CREATED_AT.replace(tzinfo=SHANGHAI)),
                before_project_id=EditingProjectId.new(),
                limit=20,
            )
        with pytest.raises(EditingProjectDataRejected):
            await repository.list_page(
                installation_id=installation_id,
                before_created_at=CREATED_AT,
                before_project_id=cast(EditingProjectId, TaskId.new()),
                limit=20,
            )
        with pytest.raises(EditingProjectDataRejected):
            await repository.list_page(
                installation_id=cast(InstallationId, object()),
                before_created_at=None,
                before_project_id=None,
                limit=20,
            )
        with pytest.raises(EditingProjectDataRejected):
            await repository.list_page(
                installation_id=InstallationId.new(),
                before_created_at=CREATED_AT,
                before_project_id=None,
                limit=20,
            )
        with pytest.raises(EditingProjectDataRejected):
            await repository.list_page(
                installation_id=InstallationId.new(),
                before_created_at=SHANGHAI.fromutc(CREATED_AT.replace(tzinfo=SHANGHAI)),
                before_project_id=EditingProjectId.new(),
                limit=20,
            )
        with pytest.raises(EditingProjectDataRejected):
            await repository.list_page(
                installation_id=InstallationId.new(),
                before_created_at=CREATED_AT,
                before_project_id=cast(EditingProjectId, TaskId.new()),
                limit=20,
            )
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_an_unreachable_database_is_refused_without_leaking_the_connection() -> None:
    """A refused connection is an `OSError`, not a `SQLAlchemyError`.

    Measured against a real PostgreSQL: `asyncpg` raises `ConnectionRefusedError`
    out of asyncio's connect call, and the SQLAlchemy dialect does not wrap it,
    because it is not one of asyncpg's own exceptions. `session.py` catches the
    same pair for the same reason. A repository catching only `SQLAlchemyError`
    would let the raw socket error escape to the caller.
    """
    database = unreachable_database()
    try:
        repository = repository_module.SqlAlchemyEditingProjectRepository(database)
        installation_id = InstallationId.new()
        with pytest.raises(EditingProjectPersistenceUnavailable) as loaded:
            await repository.get(EditingProjectId.new(), installation_id)
        with pytest.raises(EditingProjectPersistenceUnavailable) as saved:
            await repository.save(make_project(), installation_id)
        with pytest.raises(EditingProjectPersistenceUnavailable) as listed:
            await repository.list_page(
                installation_id=installation_id,
                before_created_at=None,
                before_project_id=None,
                limit=20,
            )
        for captured in (loaded, saved, listed):
            rendered = "".join(traceback.format_exception(captured.value))
            for token in LEAKED_TOKENS:
                assert token not in rendered
            assert captured.value.__cause__ is None
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_database_error_is_refused_without_leaking_its_message() -> None:
    database = unreachable_database()
    try:
        repository = repository_module.SqlAlchemyEditingProjectRepository(database)
        object.__setattr__(
            database,
            "_sessions",
            FailingSessions(SQLAlchemyError("private database failure")),
        )
        installation_id = InstallationId.new()
        with pytest.raises(EditingProjectPersistenceUnavailable) as loaded:
            await repository.get(EditingProjectId.new(), installation_id)
        with pytest.raises(EditingProjectPersistenceUnavailable) as saved:
            await repository.save(make_project(), installation_id)
        with pytest.raises(EditingProjectPersistenceUnavailable) as listed:
            await repository.list_page(
                installation_id=installation_id,
                before_created_at=None,
                before_project_id=None,
                limit=20,
            )
        for captured in (loaded, saved, listed):
            assert "private" not in "".join(traceback.format_exception(captured.value))
            assert captured.value.__cause__ is None
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_an_authentication_failure_is_refused_without_leaking_the_role() -> None:
    """The catch-all tail, on the exception class a live server really raises.

    Measured on asyncpg 0.31.0, `InvalidPasswordError`'s MRO is

        InvalidPasswordError -> InvalidAuthorizationSpecificationError
        -> PostgresError -> PostgresMessage -> Exception -> BaseException

    -- note the intermediate class, and note that `TooManyConnectionsError`
    arrives via `InsufficientResourcesError` instead, so the four classes in
    this family do not share a single direct base. What they do share is the
    `PostgresError` spine and the absence of `OSError` and `SQLAlchemyError`.

    The absence is what the repository's clause ordering rests on, and it is
    asserted first below. The `PostgresError` assertion that follows carries no
    weight for the catch-all -- it records a third-party fact, so that an
    asyncpg release which restructures this hierarchy fails here and sends
    someone back to re-read the reasoning. The message names the role.
    """
    database = unreachable_database()
    try:
        repository = repository_module.SqlAlchemyEditingProjectRepository(database)
        failure = asyncpg.exceptions.InvalidPasswordError(
            'password authentication failed for user "le05_leaked_user"'
        )
        assert not isinstance(failure, OSError | SQLAlchemyError)
        assert asyncpg.exceptions.PostgresError in type(failure).__mro__
        object.__setattr__(database, "_sessions", FailingSessions(failure))
        installation_id = InstallationId.new()
        with pytest.raises(EditingProjectPersistenceUnavailable) as loaded:
            await repository.get(EditingProjectId.new(), installation_id)
        with pytest.raises(EditingProjectPersistenceUnavailable) as saved:
            await repository.save(make_project(), installation_id)
        with pytest.raises(EditingProjectPersistenceUnavailable) as listed:
            await repository.list_page(
                installation_id=installation_id,
                before_created_at=None,
                before_project_id=None,
                limit=20,
            )
        for captured in (loaded, saved, listed):
            rendered = "".join(traceback.format_exception(captured.value))
            assert "le05_leaked_user" not in rendered
            assert captured.value.__cause__ is None
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_conflicting_insert_is_already_registered_and_says_no_more() -> None:
    """`IntegrityError` carries the offending key in its `DETAIL` line.

    PostgreSQL answers a duplicate with `Key (project_id)=(...) already exists`,
    and SQLAlchemy keeps the driver's message on `.orig`. Translating without
    `from None` would put the stored identifier into every caller's traceback.
    """
    database = unreachable_database()
    try:
        repository = repository_module.SqlAlchemyEditingProjectRepository(database)
        object.__setattr__(
            database,
            "_sessions",
            FailingSessions(integrity_failure("pk_editing_projects")),
        )
        with pytest.raises(EditingProjectAlreadyRegistered) as captured:
            await repository.save(make_project(), InstallationId.new())
        rendered = "".join(traceback.format_exception(captured.value))
        assert "private" not in rendered
        assert captured.value.__cause__ is None
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_save_maps_known_and_foreign_constraint_failures() -> None:
    database = unreachable_database()
    repository = repository_module.SqlAlchemyEditingProjectRepository(database)
    try:
        object.__setattr__(
            database,
            "_sessions",
            FailingSessions(integrity_failure("pk_editing_projects")),
        )
        with pytest.raises(EditingProjectAlreadyRegistered) as duplicate:
            await repository.save(
                make_project(),
                InstallationId.new(),
            )
        assert "private" not in "".join(traceback.format_exception(duplicate.value))

        object.__setattr__(
            database,
            "_sessions",
            FailingSessions(integrity_failure("fk_editing_projects_installation")),
        )
        with pytest.raises(EditingProjectDataRejected) as rejected:
            await repository.save(
                make_project(),
                InstallationId.new(),
            )
        assert "private" not in "".join(traceback.format_exception(rejected.value))
    finally:
        await database.close()


def test_hydration_refuses_an_identifier_of_the_wrong_uuid_version() -> None:
    """The nil UUID is a valid `uuid` value and an invalid `EditingProjectId`.

    The integration suite proves such a row really does land in the table; this
    proves the parse failure is translated instead of surfacing as
    `InvalidResourceId`, which nothing about this repository declares.
    """
    with pytest.raises(InvalidEditingProjectModel):
        repository_module._hydrate(hydration_row(project_id=UUID(int=0)))


@pytest.mark.asyncio
async def test_a_missing_row_is_not_found_and_a_present_row_hydrates() -> None:
    """Both post-query branches, without a live server.

    They were previously reachable only from the integration suite, which does
    not count towards coverage -- so the module read as fully covered while two
    of its branches had no unit-level evidence at all.
    """
    database = unreachable_database()
    try:
        repository = repository_module.SqlAlchemyEditingProjectRepository(database)
        installation_id = InstallationId.new()

        object.__setattr__(database, "_sessions", StubSessions(None))
        with pytest.raises(EditingProjectNotFound):
            await repository.get(
                EditingProjectId.new(),
                installation_id,
            )

        project = make_project()
        object.__setattr__(
            database,
            "_sessions",
            StubSessions(hydration_row(project_id=project.project_id.uuid)),
        )
        await repository.save(project, installation_id)
        assert await repository.get(project.project_id, installation_id) == project
        assert await repository.list_page(
            installation_id=installation_id,
            before_created_at=CREATED_AT,
            before_project_id=project.project_id,
            limit=20,
        ) == (project,)
        assert await repository.list_page(
            installation_id=installation_id,
            before_created_at=None,
            before_project_id=None,
            limit=20,
        ) == (project,)

        object.__setattr__(
            database,
            "_sessions",
            StubSessions(hydration_row(title=" private ")),
        )
        with pytest.raises(InvalidEditingProjectModel):
            await repository.list_page(
                installation_id=installation_id,
                before_created_at=None,
                before_project_id=None,
                limit=20,
            )
    finally:
        await database.close()


def test_hydration_normalises_a_stored_timestamp_to_utc() -> None:
    """A zero offset is not the same thing as UTC.

    The domain accepts any timezone whose offset is zero, so it cannot be the
    thing that guarantees `tzinfo is UTC`; and a row carrying a non-zero offset
    -- as here -- the domain would refuse outright. Normalising at hydration is
    what makes the loaded object's timezone a fact rather than a driver detail.
    """
    shanghai_noon = datetime(2026, 7, 29, 11, 21, 45, 123_456, tzinfo=SHANGHAI)
    hydrated = repository_module._hydrate(hydration_row(created_at=shanghai_noon))
    assert hydrated.created_at.tzinfo is UTC
    assert hydrated.created_at == CREATED_AT


@pytest.mark.parametrize(
    "created_at",
    [
        # Naive: the domain refuses these outright. Normalising before the
        # constructor would instead reinterpret it in the host's timezone --
        # moving the instant by the host's offset and handing back a perfectly
        # valid-looking object. The guard has to run first.
        datetime(2026, 7, 29, 3, 21, 45, 123_456),
        # DO NOT COPY THIS PARAMETER INTO T2. See the docstring below.
        None,
        "2026-07-29T03:21:45.123456+00:00",
        123,
    ],
    ids=["naive", "null", "text", "number"],
)
def test_hydration_refuses_a_timestamp_it_cannot_trust(created_at: object) -> None:
    """Every one of these is refused because `created_at` is `NOT NULL`.

    Normalising before validating would turn the last three into a bare
    `AttributeError` or a wrong instant, which is neither the domain's error nor
    the repository's -- that is what this pins.

    **The `null` parameter must not be copied to a nullable column.** T2's
    `materials.described_at` is nullable, so `None` is a legal value there and
    hydrates to `described_at=None` rather than raising. Copied verbatim, this
    case would have demanded that T2 reject a perfectly ordinary row -- and with
    T2's implementation copied from here too, both halves would agree, the test
    would pass, and the bug would ship green. It did not: T2 kept `None` in its
    own parametrisation with the opposite expectation.

    `normalise_timestamp()` needed no change for either column -- it returns
    anything that is not an aware datetime untouched and lets the constructor
    decide. It now lives in `hydration.py` so that both repositories share one
    copy of the order it enforces.
    """
    with pytest.raises(InvalidEditingProjectModel):
        repository_module._hydrate(hydration_row(created_at=created_at))
