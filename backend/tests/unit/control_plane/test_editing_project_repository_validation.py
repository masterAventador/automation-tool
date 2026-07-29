"""Fail-closed branches around the PostgreSQL editing-project repository."""

from __future__ import annotations

import traceback
from datetime import UTC, datetime, timedelta, timezone
from typing import cast

import pytest
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import SQLAlchemyError

from automation_tool.control_plane.application.editing_projects import (
    EditingProjectRepositoryRejected,
)
from automation_tool.control_plane.domain import (
    CaptionStyle,
    EditingProject,
    EditingProjectId,
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


def unreachable_database() -> Database:
    return Database.from_url(UNREACHABLE_URL, connect_timeout_seconds=0.05)


def make_project() -> EditingProject:
    return EditingProject(
        project_id=EditingProjectId.new(),
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


def hydration_row(created_at: datetime) -> RowMapping:
    return cast(
        RowMapping,
        {
            "project_id": EditingProjectId.new().uuid,
            "title": "夏日露营 第一集",
            "output_width": 1280,
            "output_height": 720,
            "output_fps": 30,
            "caption_font_key": "source-han-sans",
            "caption_font_px": 48,
            "caption_stroke_px": 3,
            "caption_line_spacing": 1.25,
            "created_at": created_at,
        },
    )


def test_repository_refuses_a_database_it_does_not_own() -> None:
    with pytest.raises(EditingProjectRepositoryRejected):
        repository_module.SqlAlchemyEditingProjectRepository(cast(Database, object()))


@pytest.mark.asyncio
async def test_repository_refuses_foreign_argument_types() -> None:
    database = unreachable_database()
    try:
        repository = repository_module.SqlAlchemyEditingProjectRepository(database)
        with pytest.raises(EditingProjectRepositoryRejected):
            await repository.save(cast(EditingProject, object()))
        # A bare UUID and a sibling identifier carry exactly the value the column
        # would accept, so the type has to be checked before the statement is
        # built rather than left to the database.
        with pytest.raises(EditingProjectRepositoryRejected):
            await repository.get(cast(EditingProjectId, EditingProjectId.new().uuid))
        with pytest.raises(EditingProjectRepositoryRejected):
            await repository.get(cast(EditingProjectId, TaskId.new()))
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_an_unreachable_database_is_refused_without_leaking_the_connection() -> None:
    """A refused connection is an `OSError`, not a `SQLAlchemyError`.

    Measured against a real PostgreSQL: `asyncpg` raises `ConnectionRefusedError`
    out of asyncio's connect call, and the SQLAlchemy dialect does not wrap it,
    because it is not one of asyncpg's own exceptions. `session.py` already
    catches `OSError` for exactly this reason. A repository catching only
    `SQLAlchemyError` would let the raw socket error escape to the caller.
    """
    database = unreachable_database()
    try:
        repository = repository_module.SqlAlchemyEditingProjectRepository(database)
        with pytest.raises(EditingProjectRepositoryRejected) as loaded:
            await repository.get(EditingProjectId.new())
        with pytest.raises(EditingProjectRepositoryRejected) as saved:
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
        with pytest.raises(EditingProjectRepositoryRejected) as loaded:
            await repository.get(EditingProjectId.new())
        with pytest.raises(EditingProjectRepositoryRejected) as saved:
            await repository.save(make_project())
        for captured in (loaded, saved):
            assert "private" not in "".join(traceback.format_exception(captured.value))
            assert captured.value.__cause__ is None
    finally:
        await database.close()


def test_hydration_normalises_a_stored_timestamp_to_utc() -> None:
    """A zero offset is not the same thing as UTC.

    The domain accepts any timezone whose offset is zero, so it cannot be the
    thing that guarantees `tzinfo is UTC`; and a row carrying a non-zero offset
    -- as here -- the domain would refuse outright. Normalising at hydration is
    what makes the loaded object's timezone a fact rather than a driver detail.
    """
    shanghai = datetime(2026, 7, 29, 11, 21, 45, 123_456, tzinfo=timezone(timedelta(hours=8)))
    hydrated = repository_module._hydrate(hydration_row(shanghai))
    assert hydrated.created_at.tzinfo is UTC
    assert hydrated.created_at == CREATED_AT
