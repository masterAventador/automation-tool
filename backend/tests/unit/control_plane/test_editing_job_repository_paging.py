"""LE-06 T5 repository ownership and the job page's compound total order."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from typing import cast

import pytest
from sqlalchemy.engine import RowMapping
from sqlalchemy.sql import ClauseElement
from sqlalchemy.sql.schema import ColumnCollectionConstraint

from automation_tool.control_plane.application.editing_jobs import EditingJobDataRejected
from automation_tool.control_plane.domain import (
    EditingJob,
    EditingJobId,
    EditingJobStatus,
    EditingProjectId,
    InstallationId,
    TimelineId,
)
from automation_tool.control_plane.infrastructure.database import Database, editing_jobs
from automation_tool.control_plane.infrastructure.database import (
    editing_job_repository as repository_module,
)

NOW = datetime(2026, 7, 30, 10, 11, 12, 123_456, tzinfo=UTC)
OWNER = InstallationId.parse("00000000-0000-4000-8000-000000000001")
PROJECT_ID = EditingProjectId.parse("00000000-0000-4000-8000-000000000010")
TIMELINE_ID = TimelineId.parse("00000000-0000-4000-8000-000000000020")


def job(identifier: str, *, revision: int) -> EditingJob:
    return EditingJob(
        job_id=EditingJobId.parse(identifier),
        project_id=PROJECT_ID,
        timeline_id=TIMELINE_ID,
        timeline_revision=revision,
        status=EditingJobStatus.QUEUED,
        failure_code=None,
        output_artifact_id=None,
        created_at=NOW,
        updated_at=NOW,
    )


def row(value: EditingJob) -> RowMapping:
    return cast(
        RowMapping,
        {
            "job_id": value.job_id.uuid,
            "installation_id": OWNER.uuid,
            "project_id": value.project_id.uuid,
            "timeline_id": value.timeline_id.uuid,
            "timeline_revision": value.timeline_revision,
            "status": value.status.value,
            "failure_code": None,
            "output_artifact_id": None,
            "created_at": value.created_at,
            "updated_at": value.updated_at,
        },
    )


class StubResult:
    def __init__(self, rows: tuple[RowMapping, ...]) -> None:
        self._rows = rows

    def mappings(self) -> StubResult:
        return self

    def all(self) -> tuple[RowMapping, ...]:
        return self._rows


class StubSession:
    def __init__(self, rows: tuple[RowMapping, ...]) -> None:
        self.rows = rows
        self.statements: list[object] = []

    async def execute(self, statement: object) -> StubResult:
        self.statements.append(statement)
        return StubResult(self.rows)


class StubSessionScope:
    def __init__(self, session: StubSession) -> None:
        self.session = session

    async def __aenter__(self) -> StubSession:
        return self.session

    async def __aexit__(
        self,
        _exception_type: object,
        _exception: object,
        _traceback: object,
    ) -> None:
        return None


class StubSessions:
    def __init__(self, session: StubSession) -> None:
        self.session = session

    def begin(self) -> StubSessionScope:
        return StubSessionScope(self.session)


def repository_with(
    rows: tuple[RowMapping, ...],
) -> tuple[
    repository_module.SqlAlchemyEditingJobRepository,
    StubSession,
    Database,
]:
    database = Database.from_url(
        "postgresql+asyncpg://unused:unused@127.0.0.1:1/unused",
        connect_timeout_seconds=0.05,
    )
    session = StubSession(rows)
    object.__setattr__(database, "_sessions", StubSessions(session))
    return repository_module.SqlAlchemyEditingJobRepository(database), session, database


def test_job_repository_has_one_owner_scoped_surface_without_compatibility_aliases() -> None:
    repository = repository_module.SqlAlchemyEditingJobRepository

    assert tuple(inspect.signature(repository.save).parameters) == (
        "self",
        "job",
        "installation_id",
    )
    assert tuple(inspect.signature(repository.get).parameters) == (
        "self",
        "job_id",
        "installation_id",
    )
    assert tuple(inspect.signature(repository.list_page_by_project).parameters) == (
        "self",
        "installation_id",
        "project_id",
        "before_updated_at",
        "before_job_id",
        "limit",
    )
    assert not any(
        hasattr(repository, name)
        for name in (
            "save_for_installation",
            "get_for_installation",
            "list_page_by_project_for_installation",
            "list_for_project",
        )
    )


def test_job_schema_carries_owner_foreign_key_and_the_page_index() -> None:
    assert editing_jobs.c.installation_id.nullable is False
    constraints = {
        cast(str, constraint.name): tuple(column.name for column in constraint.columns)
        for constraint in editing_jobs.constraints
        if isinstance(constraint, ColumnCollectionConstraint)
    }
    assert constraints["fk_editing_jobs_project_owner"] == (
        "project_id",
        "installation_id",
    )
    indexes = {
        cast(str, index.name): tuple(column.name for column in index.columns)
        for index in editing_jobs.indexes
    }
    assert indexes["ix_editing_jobs_installation_project_updated_job"] == (
        "installation_id",
        "project_id",
        "updated_at",
        "job_id",
    )


@pytest.mark.asyncio
async def test_list_page_uses_owner_project_and_both_cursor_columns() -> None:
    low = job("00000000-0000-4000-8000-000000000001", revision=1)
    high = job("00000000-0000-4000-8000-000000000002", revision=2)
    repository, session, database = repository_with((row(high), row(low)))
    try:
        listed = await repository.list_page_by_project(
            installation_id=OWNER,
            project_id=PROJECT_ID,
            before_updated_at=NOW,
            before_job_id=EditingJobId.parse("00000000-0000-4000-8000-000000000003"),
            limit=3,
        )
    finally:
        await database.close()

    assert listed == (high, low)
    assert len(session.statements) == 1
    statement = cast(ClauseElement, session.statements[0])
    compiled = statement.compile()
    sql = str(compiled)
    assert "editing_jobs.installation_id =" in sql
    assert "editing_jobs.project_id =" in sql
    assert "editing_jobs.updated_at <" in sql
    assert "editing_jobs.updated_at =" in sql
    assert "editing_jobs.job_id <" in sql
    assert "ORDER BY editing_jobs.updated_at DESC, editing_jobs.job_id DESC" in sql
    assert "LIMIT" in sql
    assert OWNER.uuid in compiled.params.values()
    assert PROJECT_ID.uuid in compiled.params.values()
    assert (
        EditingJobId.parse("00000000-0000-4000-8000-000000000003").uuid in compiled.params.values()
    )
    assert 3 in compiled.params.values()


@pytest.mark.asyncio
async def test_list_page_without_a_cursor_has_no_boundary_predicate() -> None:
    low = job("00000000-0000-4000-8000-000000000001", revision=1)
    repository, session, database = repository_with((row(low),))
    try:
        listed = await repository.list_page_by_project(
            installation_id=OWNER,
            project_id=PROJECT_ID,
            before_updated_at=None,
            before_job_id=None,
            limit=20,
        )
    finally:
        await database.close()

    assert listed == (low,)
    sql = str(cast(ClauseElement, session.statements[0]).compile())
    assert "editing_jobs.updated_at <" not in sql
    assert "editing_jobs.job_id <" not in sql


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("before_updated_at", "before_job_id", "limit"),
    (
        (None, EditingJobId.new(), 20),
        (NOW, None, 20),
        (None, None, 0),
        (None, None, 102),
    ),
)
async def test_list_page_rejects_half_a_cursor_and_invalid_limits(
    before_updated_at: datetime | None,
    before_job_id: EditingJobId | None,
    limit: int,
) -> None:
    repository, _, database = repository_with(())
    try:
        with pytest.raises(EditingJobDataRejected):
            await repository.list_page_by_project(
                installation_id=OWNER,
                project_id=PROJECT_ID,
                before_updated_at=before_updated_at,
                before_job_id=before_job_id,
                limit=limit,
            )
    finally:
        await database.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("before_updated_at", "before_job_id"),
    (
        (
            datetime(2026, 7, 30, 10, 11, 12),
            EditingJobId.new(),
        ),
        (
            datetime.fromisoformat("2026-07-30T18:11:12+08:00"),
            EditingJobId.new(),
        ),
        (
            NOW,
            cast(EditingJobId, object()),
        ),
    ),
)
async def test_list_page_rejects_invalid_compound_boundaries(
    before_updated_at: datetime,
    before_job_id: EditingJobId,
) -> None:
    repository, _, database = repository_with(())
    try:
        with pytest.raises(EditingJobDataRejected):
            await repository.list_page_by_project(
                installation_id=OWNER,
                project_id=PROJECT_ID,
                before_updated_at=before_updated_at,
                before_job_id=before_job_id,
                limit=20,
            )
    finally:
        await database.close()
