"""Fail-closed branches around the PostgreSQL editing-project repository.

These cover what a real database cannot be made to produce on demand: a row that
is missing, a row malformed in a way the columns permit, and a session that
fails in each of the three ways the driver can fail. Behaviour against a live
PostgreSQL is in the integration suite.
"""

from __future__ import annotations

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
    InvalidEditingProjectModel,
    OutputSpec,
    TaskId,
)
from automation_tool.control_plane.infrastructure.database import Database
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


class StubResult:
    """Just enough of a `Result` for `.mappings().one_or_none()`."""

    def __init__(self, row: RowMapping | None) -> None:
        self._row = row

    def mappings(self) -> StubResult:
        return self

    def one_or_none(self) -> RowMapping | None:
        return self._row


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


@pytest.mark.asyncio
async def test_repository_refuses_foreign_argument_types() -> None:
    database = unreachable_database()
    try:
        repository = repository_module.SqlAlchemyEditingProjectRepository(database)
        with pytest.raises(EditingProjectDataRejected):
            await repository.save(cast(EditingProject, object()))
        # A bare UUID and a sibling identifier carry exactly the value the column
        # would accept, so the type has to be checked before the statement is
        # built rather than left to the database.
        with pytest.raises(EditingProjectDataRejected):
            await repository.get(cast(EditingProjectId, EditingProjectId.new().uuid))
        with pytest.raises(EditingProjectDataRejected):
            await repository.get(cast(EditingProjectId, TaskId.new()))
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
        with pytest.raises(EditingProjectPersistenceUnavailable) as loaded:
            await repository.get(EditingProjectId.new())
        with pytest.raises(EditingProjectPersistenceUnavailable) as saved:
            await repository.save(make_project())
        for captured in (loaded, saved):
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
        with pytest.raises(EditingProjectPersistenceUnavailable) as loaded:
            await repository.get(EditingProjectId.new())
        with pytest.raises(EditingProjectPersistenceUnavailable) as saved:
            await repository.save(make_project())
        for captured in (loaded, saved):
            assert "private" not in "".join(traceback.format_exception(captured.value))
            assert captured.value.__cause__ is None
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_an_authentication_failure_is_refused_without_leaking_the_role() -> None:
    """The catch-all tail, on the exception class a live server really raises.

    `asyncpg.exceptions.InvalidPasswordError` inherits from `PostgresError` and
    `Exception` only -- asserted below rather than assumed -- so neither the
    `OSError` nor the `SQLAlchemyError` clause sees it, and it carries the role
    name in its message. `TooManyConnectionsError` reaches the same gap through
    the same bases, and a saturated pool is ordinary production traffic.
    """
    database = unreachable_database()
    try:
        repository = repository_module.SqlAlchemyEditingProjectRepository(database)
        failure = asyncpg.exceptions.InvalidPasswordError(
            'password authentication failed for user "le05_leaked_user"'
        )
        assert not isinstance(failure, OSError | SQLAlchemyError)
        object.__setattr__(database, "_sessions", FailingSessions(failure))
        with pytest.raises(EditingProjectPersistenceUnavailable) as loaded:
            await repository.get(EditingProjectId.new())
        with pytest.raises(EditingProjectPersistenceUnavailable) as saved:
            await repository.save(make_project())
        for captured in (loaded, saved):
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
            FailingSessions(
                IntegrityError(
                    "insert into editing_projects",
                    None,
                    Exception("Key (project_id)=(le05-private-detail) already exists"),
                )
            ),
        )
        with pytest.raises(EditingProjectAlreadyRegistered) as captured:
            await repository.save(make_project())
        rendered = "".join(traceback.format_exception(captured.value))
        assert "le05-private-detail" not in rendered
        assert captured.value.__cause__ is None
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

        object.__setattr__(database, "_sessions", StubSessions(None))
        with pytest.raises(EditingProjectNotFound):
            await repository.get(EditingProjectId.new())

        project = make_project()
        object.__setattr__(
            database,
            "_sessions",
            StubSessions(hydration_row(project_id=project.project_id.uuid)),
        )
        assert await repository.get(project.project_id) == project
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
        None,
        "2026-07-29T03:21:45.123456+00:00",
        123,
    ],
    ids=["naive", "null", "text", "number"],
)
def test_hydration_refuses_a_timestamp_it_cannot_trust(created_at: object) -> None:
    """`None` and text arrive the moment a nullable timestamp column exists.

    LE-05 T2's `described_at` is nullable and will reuse this hydration shape.
    Normalising before validating turns those two into a bare `AttributeError`,
    which is neither the domain's error nor the repository's.
    """
    with pytest.raises(InvalidEditingProjectModel):
        repository_module._hydrate(hydration_row(created_at=created_at))
