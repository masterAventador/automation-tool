"""LE-05 T4: editing jobs on a real PostgreSQL, and the three invariants.

Every assertion reads the database back, either through the repository or
through raw Core statements that bypass it. A repository that only ever reads
its own writes proves nothing about what actually landed.

This is the first mutable table in LE-05 -- the three before it are write-once --
and it is the one carrying the rules no single aggregate can hold, because no
domain object here holds a reference to another one:

1. a job's `project_id` is the project its timeline belongs to;
2. the `timeline_revision` a job names really exists;
3. one revision has at most one queued render at a time.

All three are structural. The first two are one composite foreign key, since a
plain single-column key cannot express the triangle; the third is a unique index
restricted to queued rows. **Each is proved by injecting a violating row with a
raw INSERT**, not by observing that the repository does not produce one -- the
latter would pass just as well with the constraint dropped, which is the "green
gate that is not guarding anything" this file exists to avoid.

Rows here reference `timelines`, which reference `editing_projects`, so every
test stores both first through their own repositories. Clean up runs in reverse:
jobs, then timelines, then projects. The other LE-05 integration files delete
`editing_projects` wholesale in their own fixtures, and a leftover job would
block them.
"""

from __future__ import annotations

import traceback
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest
from alembic_head import HEAD_REVISION
from conftest import AlembicRunner
from sqlalchemy import (
    ColumnElement,
    ForeignKeyConstraint,
    delete,
    insert,
    select,
    text,
    update,
)
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql.schema import ColumnCollectionConstraint

from automation_tool.control_plane.application.editing_jobs import (
    EditingJobAlreadyRegistered,
    EditingJobDataRejected,
    EditingJobNotFound,
    EditingJobPersistenceUnavailable,
    EditingJobRevisionAlreadyQueued,
    EditingJobStale,
    EditingJobTimelineRevisionMissing,
)
from automation_tool.control_plane.domain import (
    ArtifactId,
    CaptionStyle,
    EditingJob,
    EditingJobFailureCode,
    EditingJobId,
    EditingJobStatus,
    EditingProject,
    EditingProjectId,
    InvalidEditingJobModel,
    MaterialId,
    OutputSpec,
    Timeline,
    TimelineClip,
    TimelineId,
    TimelineTrack,
    TimelineTrackKind,
)
from automation_tool.control_plane.infrastructure.database import (
    Database,
    editing_jobs,
    editing_projects,
    timelines,
)
from automation_tool.control_plane.infrastructure.database.editing_job_repository import (
    SqlAlchemyEditingJobRepository,
)
from automation_tool.control_plane.infrastructure.database.editing_project_repository import (
    SqlAlchemyEditingProjectRepository,
)
from automation_tool.control_plane.infrastructure.database.timeline_repository import (
    SqlAlchemyTimelineRepository,
)

# Carrying microseconds, so a timestamp column that silently truncates is caught.
CREATED_AT = datetime(2026, 7, 30, 4, 15, 30, 123_456, tzinfo=UTC)
UPDATED_AT = CREATED_AT + timedelta(seconds=90)

TIMELINE_DURATION_MS = 6_000
REVISION = 3

PREVIOUS_REVISION = "20260729_0038"

EXPECTED_COLUMNS = {
    "job_id": ("uuid", "NO", None),
    "project_id": ("uuid", "NO", None),
    "timeline_id": ("uuid", "NO", None),
    "timeline_revision": ("integer", "NO", None),
    "status": ("character varying", "NO", 16),
    "failure_code": ("character varying", "YES", 32),
    "output_artifact_id": ("uuid", "YES", None),
    "created_at": ("timestamp with time zone", "NO", None),
    "updated_at": ("timestamp with time zone", "NO", None),
}
EXPECTED_CONSTRAINTS = {
    "pk_editing_jobs",
    "fk_editing_jobs_timeline_revision",
}

EXPECTED_TABLE_TYPES = {
    "job_id": "UUID",
    "project_id": "UUID",
    "timeline_id": "UUID",
    "timeline_revision": "Integer",
    "status": "String",
    "failure_code": "String",
    "output_artifact_id": "UUID",
    "created_at": "DateTime",
    "updated_at": "DateTime",
}

# What `schema.py` declares, as opposed to what the migration built. The two are
# written separately and drift silently: `migrations/env.py` points
# `target_metadata` at this metadata, so anything the database has and this does
# not is what the next `--autogenerate` offers to drop.
EXPECTED_TABLE_CONSTRAINTS = {
    "pk_editing_jobs": ("PrimaryKeyConstraint", ["job_id"]),
    "fk_editing_jobs_timeline_revision": (
        "ForeignKeyConstraint",
        ["timeline_id", "timeline_revision", "project_id"],
    ),
}

QUEUED_INDEX_NAME = "uq_editing_jobs_queued_timeline_revision"
PRIMARY_KEY_NAME = "pk_editing_jobs"
FOREIGN_KEY_NAME = "fk_editing_jobs_timeline_revision"

# The third invariant lives in an `Index`, which is **not** in
# `Table.constraints` and therefore invisible to the constraint comparison
# above. Its `WHERE` clause is the load-bearing half: without it the index would
# refuse a finished render coexisting with a queued one, and with it dropped
# from `schema.py` alone the next autogenerate proposes removing it.
EXPECTED_TABLE_INDEXES = {
    QUEUED_INDEX_NAME: (
        ["timeline_id", "timeline_revision"],
        True,
        "editing_jobs.status = 'queued'",
    )
}


def compiled_predicate(clause: ColumnElement[bool]) -> str:
    """A `WHERE` clause with its literals rendered rather than parameterised.

    `literal_binds` is the whole point. Without it the comparison renders as
    `status = :status_1`, the value disappears behind a bind parameter, and a
    predicate restricted to any *other* state would compare equal to this one --
    which is exactly the drift worth catching.

    No dialect is passed. This compares what `schema.py` declares against this
    file's own constant; what PostgreSQL made of it is asserted separately from
    `pg_indexes.indexdef`, which is the real server's rendering rather than a
    reconstruction. Measured: the default compiler and `PGDialect` produce the
    same text for this predicate, and asking for the latter costs an untyped
    constructor call for nothing.
    """
    return str(clause.compile(compile_kwargs={"literal_binds": True}))


def make_project(project_id: EditingProjectId) -> EditingProject:
    return EditingProject(
        project_id=project_id,
        title="夏日露营 第一集",
        output=OutputSpec(width=1280, height=720, fps=30),
        caption_style=CaptionStyle(
            font_key="source-han-sans", font_px=48, stroke_px=3, line_spacing=1.25
        ),
        created_at=CREATED_AT,
    )


def make_timeline(timeline_id: TimelineId, project_id: EditingProjectId, revision: int) -> Timeline:
    """The smallest cut the domain accepts: one picture lane, one clip."""
    return Timeline(
        timeline_id=timeline_id,
        project_id=project_id,
        revision=revision,
        duration_ms=TIMELINE_DURATION_MS,
        tracks=(
            TimelineTrack(
                track_id="visual",
                kind=TimelineTrackKind.VISUAL,
                clips=(
                    TimelineClip(
                        clip_id="v-one",
                        start_ms=0,
                        duration_ms=TIMELINE_DURATION_MS,
                        source_material_id=MaterialId.new(),
                        source_in_ms=None,
                        source_out_ms=None,
                        text=None,
                        gain_db=None,
                        transition_in=None,
                    ),
                ),
            ),
        ),
        created_at=CREATED_AT,
    )


def make_job(
    job_id: EditingJobId,
    project_id: EditingProjectId,
    timeline_id: TimelineId,
    *,
    revision: int = REVISION,
    status: EditingJobStatus = EditingJobStatus.QUEUED,
    failure_code: EditingJobFailureCode | None = None,
    output_artifact_id: ArtifactId | None = None,
    updated_at: datetime = UPDATED_AT,
) -> EditingJob:
    return EditingJob(
        job_id=job_id,
        project_id=project_id,
        timeline_id=timeline_id,
        timeline_revision=revision,
        status=status,
        failure_code=failure_code,
        output_artifact_id=output_artifact_id,
        created_at=CREATED_AT,
        updated_at=updated_at,
    )


async def reset_data(database: Database) -> None:
    """Jobs, then timelines, then projects: neither key has an ON DELETE action."""
    async with database.session() as session:
        await session.execute(delete(editing_jobs))
        await session.execute(delete(timelines))
        await session.execute(delete(editing_projects))


async def store_project(database: Database, project_id: EditingProjectId) -> None:
    """Through T1's repository, not a raw INSERT -- the production path."""
    await SqlAlchemyEditingProjectRepository(database).save(make_project(project_id))


async def store_timeline(
    database: Database,
    timeline_id: TimelineId,
    project_id: EditingProjectId,
    revision: int = REVISION,
) -> None:
    """Through T3's repository, for the same reason."""
    await SqlAlchemyTimelineRepository(database).save(
        make_timeline(timeline_id, project_id, revision)
    )


async def store_scene(
    database: Database, project_id: EditingProjectId, timeline_id: TimelineId
) -> None:
    await store_project(database, project_id)
    await store_timeline(database, timeline_id, project_id)


def row_values(
    job_id: UUID, project_id: UUID, timeline_id: UUID, **overrides: object
) -> dict[str, object]:
    """The exact column payload `save` is expected to write."""
    values: dict[str, object] = {
        "job_id": job_id,
        "project_id": project_id,
        "timeline_id": timeline_id,
        "timeline_revision": REVISION,
        "status": EditingJobStatus.QUEUED.value,
        "failure_code": None,
        "output_artifact_id": None,
        "created_at": CREATED_AT,
        "updated_at": UPDATED_AT,
    }
    values.update(overrides)
    return values


async def insert_row(
    database: Database, job_id: UUID, project_id: UUID, timeline_id: UUID, **overrides: object
) -> None:
    async with database.session() as session:
        await session.execute(
            insert(editing_jobs).values(**row_values(job_id, project_id, timeline_id, **overrides))
        )


async def stored_row(database: Database, job_id: UUID) -> dict[str, object]:
    async with database.session() as session:
        row = (
            (await session.execute(select(editing_jobs).where(editing_jobs.c.job_id == job_id)))
            .mappings()
            .one()
        )
    return dict(row)


async def stored_job_ids(database: Database) -> set[UUID]:
    async with database.session() as session:
        rows = (await session.execute(select(editing_jobs.c.job_id))).all()
    return {cast(UUID, row[0]) for row in rows}


@pytest.mark.asyncio
async def test_saved_job_lands_as_typed_columns_and_hydrates_back_equal(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyEditingJobRepository(database)
    try:
        await reset_data(database)
        project_id = EditingProjectId.new()
        timeline_id = TimelineId.new()
        await store_scene(database, project_id, timeline_id)
        job_id = EditingJobId.new()
        job = make_job(job_id, project_id, timeline_id)

        await repository.save(job)

        row = await stored_row(database, job_id.uuid)
        assert row == row_values(job_id.uuid, project_id.uuid, timeline_id.uuid)
        # The enumeration lands as its `.value`, not as `str(member)` or a
        # repr -- the reader looks the member up by value.
        assert row["status"] == "queued"
        assert type(row["status"]) is str
        for column in ("created_at", "updated_at"):
            stored = row[column]
            assert isinstance(stored, datetime)
            assert stored.tzinfo is UTC

        loaded = await repository.get(job_id)
        assert loaded == job
        # Identity, not equality: a `StrEnum` member equals its own text, so
        # `== "queued"` holds for the bare string this has to rule out.
        assert loaded.status is EditingJobStatus.QUEUED
        assert loaded.created_at.tzinfo is UTC
        assert loaded.updated_at.tzinfo is UTC
    finally:
        await reset_data(database)
        await database.close()


# One representative per state, with the facts that state is allowed to carry.
# `EditingJob` refuses every other combination, so this is also the full set of
# shapes a row can legally hold.
ROUND_TRIP_STATES: dict[
    str, tuple[EditingJobStatus, EditingJobFailureCode | None, ArtifactId | None]
] = {
    "queued": (EditingJobStatus.QUEUED, None, None),
    "running": (EditingJobStatus.RUNNING, None, None),
    "cancelling": (EditingJobStatus.CANCELLING, None, None),
    "succeeded": (EditingJobStatus.SUCCEEDED, None, ArtifactId.new()),
    # `MATERIAL_UNSUPPORTED` is the longest value either enumeration can
    # produce, so a column narrower than the domain's members fails here rather
    # than on whichever code a future test happens to pick.
    "failed": (EditingJobStatus.FAILED, EditingJobFailureCode.MATERIAL_UNSUPPORTED, None),
    "cancelled": (EditingJobStatus.CANCELLED, None, None),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("facts", list(ROUND_TRIP_STATES.values()), ids=list(ROUND_TRIP_STATES))
async def test_every_state_and_the_facts_it_carries_round_trip(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
    facts: tuple[EditingJobStatus, EditingJobFailureCode | None, ArtifactId | None],
) -> None:
    """Six states, two nullable columns, and the column cannot pair them.

    Which absence belongs to which state is a domain rule, so a round trip per
    state is what proves the two nullable columns are written and read back the
    right way round rather than both landing as NULL.
    """
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyEditingJobRepository(database)
    try:
        await reset_data(database)
        project_id = EditingProjectId.new()
        timeline_id = TimelineId.new()
        await store_scene(database, project_id, timeline_id)
        job_id = EditingJobId.new()
        status, failure_code, output_artifact_id = facts
        job = make_job(
            job_id,
            project_id,
            timeline_id,
            status=status,
            failure_code=failure_code,
            output_artifact_id=output_artifact_id,
        )

        await repository.save(job)

        loaded = await repository.get(job_id)
        assert loaded == job
        assert loaded.status is job.status
        assert loaded.failure_code is job.failure_code
        assert loaded.output_artifact_id == job.output_artifact_id
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_a_stored_job_cannot_be_registered_twice(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    """`save` creates; it never merges into an existing row.

    The stored row is compared afterwards, because a refusal that had silently
    upserted would still raise nothing and still leave the caller believing the
    original was intact.
    """
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyEditingJobRepository(database)
    try:
        await reset_data(database)
        project_id = EditingProjectId.new()
        timeline_id = TimelineId.new()
        await store_scene(database, project_id, timeline_id)
        job_id = EditingJobId.new()

        with pytest.raises(EditingJobNotFound):
            await repository.get(job_id)

        await repository.save(make_job(job_id, project_id, timeline_id))
        with pytest.raises(EditingJobAlreadyRegistered):
            await repository.save(
                make_job(
                    job_id,
                    project_id,
                    timeline_id,
                    status=EditingJobStatus.RUNNING,
                    updated_at=UPDATED_AT + timedelta(seconds=1),
                )
            )
        assert await stored_row(database, job_id.uuid) == row_values(
            job_id.uuid, project_id.uuid, timeline_id.uuid
        )
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_the_primary_key_is_enforced_by_postgresql_itself(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    """Violation injection: the refusal is the database's, not a Python branch.

    A repository that looked the row up before inserting would pass the test
    above while still letting two concurrent callers both find nothing and both
    proceed. These inserts never touch the repository.

    The constraint name is asserted from asyncpg's structured `constraint_name`
    rather than from the message, because the message carries PostgreSQL's
    DETAIL line and that quotes the key values.
    """
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        project_id = EditingProjectId.new()
        timeline_id = TimelineId.new()
        await store_scene(database, project_id, timeline_id)
        job_id = EditingJobId.new()
        await insert_row(database, job_id.uuid, project_id.uuid, timeline_id.uuid)

        with pytest.raises(IntegrityError) as repeated:
            await insert_row(
                database,
                job_id.uuid,
                project_id.uuid,
                timeline_id.uuid,
                status=EditingJobStatus.RUNNING.value,
            )
        assert getattr(repeated.value.orig, "sqlstate", None) == "23505"
        assert constraint_name_of(repeated.value) == PRIMARY_KEY_NAME

        # A different job on the same revision is an ordinary new row, provided
        # it is not a second queued one -- otherwise this would pass for a
        # constraint that refuses everything.
        await insert_row(
            database,
            EditingJobId.new().uuid,
            project_id.uuid,
            timeline_id.uuid,
            status=EditingJobStatus.RUNNING.value,
        )
        assert len(await stored_job_ids(database)) == 2
    finally:
        await reset_data(database)
        await database.close()


def constraint_name_of(error: IntegrityError) -> object:
    """Where asyncpg really puts the constraint name, measured.

    `error.orig` is SQLAlchemy's `AsyncAdapt_asyncpg_dbapi.IntegrityError`, and
    its whole attribute surface is `args`, `pgcode` and `sqlstate`. The driver's
    own exception -- the one carrying `constraint_name` as a structured field
    beside `detail` -- is one link further down the chain, on `__cause__`. That
    is the fact the repository's dispatch rests on, so this file reaches for it
    the same way rather than through a helper of the repository's.
    """
    return getattr(getattr(error.orig, "__cause__", None), "constraint_name", None)


@pytest.mark.asyncio
async def test_a_job_cannot_claim_a_project_its_timeline_does_not_belong_to(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    """Invariant 1, by injection: the triangle a single-column key cannot hold.

    `project_id` sits on both the job and the timeline, which is deliberate
    redundancy -- and redundancy is exactly what needs an enforced agreement. A
    plain foreign key from `editing_jobs.project_id` to `editing_projects` would
    be satisfied by **any** stored project, including the wrong one, and no
    domain object can catch it either: `EditingJob` holds an `EditingProjectId`
    and has no way to ask what project a timeline belongs to.

    So the second project here is deliberately **stored**. A row naming a
    project that simply does not exist would be refused by a much weaker rule,
    and the test would pass with the composite key replaced by a single-column
    one. What is injected is a job whose project exists, whose timeline exists,
    and whose revision exists -- and where the three do not belong together.
    """
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyEditingJobRepository(database)
    try:
        await reset_data(database)
        project_id = EditingProjectId.new()
        other_project = EditingProjectId.new()
        timeline_id = TimelineId.new()
        await store_scene(database, project_id, timeline_id)
        await store_project(database, other_project)

        with pytest.raises(IntegrityError) as injected:
            await insert_row(
                database, EditingJobId.new().uuid, other_project.uuid, timeline_id.uuid
            )
        assert getattr(injected.value.orig, "sqlstate", None) == "23503"
        assert constraint_name_of(injected.value) == FOREIGN_KEY_NAME

        # Through the repository, the same row gets an answer a caller can act
        # on -- and the DETAIL line quoting all three key values must not travel.
        with pytest.raises(EditingJobTimelineRevisionMissing) as refused:
            await repository.save(make_job(EditingJobId.new(), other_project, timeline_id))
        rendered = "".join(traceback.format_exception(refused.value))
        assert str(other_project) not in rendered
        assert str(timeline_id) not in rendered
        assert refused.value.__cause__ is None
        assert await stored_job_ids(database) == set()

        # Correcting only the project makes the same job land, so the refusal
        # was about the three columns belonging together and nothing else.
        job_id = EditingJobId.new()
        await repository.save(make_job(job_id, project_id, timeline_id))
        assert await stored_job_ids(database) == {job_id.uuid}
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_a_job_cannot_name_a_revision_that_was_never_stored(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    """Invariant 2, by injection, carried by the same composite key.

    The timeline exists and the project is the right one; only the revision has
    never been written. Nothing in the domain can see this either -- a revision
    is an integer on the job, and `EditingJob` cannot ask which revisions of a
    timeline were stored.
    """
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyEditingJobRepository(database)
    try:
        await reset_data(database)
        project_id = EditingProjectId.new()
        timeline_id = TimelineId.new()
        await store_scene(database, project_id, timeline_id)

        with pytest.raises(IntegrityError) as injected:
            await insert_row(
                database,
                EditingJobId.new().uuid,
                project_id.uuid,
                timeline_id.uuid,
                timeline_revision=REVISION + 1,
            )
        assert getattr(injected.value.orig, "sqlstate", None) == "23503"
        assert constraint_name_of(injected.value) == FOREIGN_KEY_NAME

        with pytest.raises(EditingJobTimelineRevisionMissing):
            await repository.save(
                make_job(EditingJobId.new(), project_id, timeline_id, revision=REVISION + 1)
            )
        assert await stored_job_ids(database) == set()

        # Storing that revision is all it takes, so the refusal was the missing
        # revision rather than anything else about the row.
        await store_timeline(database, timeline_id, project_id, REVISION + 1)
        job_id = EditingJobId.new()
        await repository.save(make_job(job_id, project_id, timeline_id, revision=REVISION + 1))
        assert await stored_job_ids(database) == {job_id.uuid}
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_one_revision_may_have_only_one_queued_render(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    """Invariant 3, by injection. This is the half a plain UNIQUE would give.

    Two callers both asking to render the same cut is a duplicate request, not
    two pieces of work, and checking for one in the application is a check two
    concurrent callers both pass.
    """
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyEditingJobRepository(database)
    try:
        await reset_data(database)
        project_id = EditingProjectId.new()
        timeline_id = TimelineId.new()
        await store_scene(database, project_id, timeline_id)
        first = EditingJobId.new()
        await repository.save(make_job(first, project_id, timeline_id))

        with pytest.raises(IntegrityError) as injected:
            await insert_row(database, EditingJobId.new().uuid, project_id.uuid, timeline_id.uuid)
        assert getattr(injected.value.orig, "sqlstate", None) == "23505"
        assert constraint_name_of(injected.value) == QUEUED_INDEX_NAME

        with pytest.raises(EditingJobRevisionAlreadyQueued) as refused:
            await repository.save(make_job(EditingJobId.new(), project_id, timeline_id))
        rendered = "".join(traceback.format_exception(refused.value))
        assert str(timeline_id) not in rendered
        assert refused.value.__cause__ is None
        assert await stored_job_ids(database) == {first.uuid}

        # A queued render of a *different* revision is an ordinary new row, so
        # the index is not simply refusing every second job on a timeline.
        await store_timeline(database, timeline_id, project_id, REVISION + 1)
        second = EditingJobId.new()
        await repository.save(make_job(second, project_id, timeline_id, revision=REVISION + 1))
        assert await stored_job_ids(database) == {first.uuid, second.uuid}
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_finished_renders_of_one_revision_coexist_with_a_queued_one(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    """The other half of invariant 3, and the `WHERE` clause is load-bearing.

    Only queued rows are unique. Every other state may repeat on one revision --
    a cut can be rendered, cancelled, retried and rendered again, and the
    history has to survive. Pinning only "a second queued row is refused" would
    leave a mutation that drops the `WHERE` alive: an unrestricted unique index
    passes that assertion and quietly makes a second render of anything
    impossible.

    Five finished or in-flight rows on one revision, then a queued one on top,
    all accepted.
    """
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyEditingJobRepository(database)
    try:
        await reset_data(database)
        project_id = EditingProjectId.new()
        timeline_id = TimelineId.new()
        await store_scene(database, project_id, timeline_id)

        history = [
            make_job(
                EditingJobId.new(),
                project_id,
                timeline_id,
                status=EditingJobStatus.SUCCEEDED,
                output_artifact_id=ArtifactId.new(),
            ),
            make_job(
                EditingJobId.new(),
                project_id,
                timeline_id,
                status=EditingJobStatus.FAILED,
                failure_code=EditingJobFailureCode.RENDER_FAILED,
            ),
            make_job(
                EditingJobId.new(),
                project_id,
                timeline_id,
                status=EditingJobStatus.CANCELLED,
            ),
            make_job(
                EditingJobId.new(),
                project_id,
                timeline_id,
                status=EditingJobStatus.RUNNING,
            ),
            make_job(
                EditingJobId.new(),
                project_id,
                timeline_id,
                status=EditingJobStatus.CANCELLING,
            ),
        ]
        for job in history:
            await repository.save(job)
        # A second succeeded render of the same revision, because "at most one"
        # must not have leaked into the non-queued states either.
        repeat = make_job(
            EditingJobId.new(),
            project_id,
            timeline_id,
            status=EditingJobStatus.SUCCEEDED,
            output_artifact_id=ArtifactId.new(),
        )
        await repository.save(repeat)
        queued = make_job(EditingJobId.new(), project_id, timeline_id)
        await repository.save(queued)

        assert await stored_job_ids(database) == {
            *(job.job_id.uuid for job in history),
            repeat.job_id.uuid,
            queued.job_id.uuid,
        }
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_leaving_the_queue_frees_the_slot_for_the_next_render(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    """The index is evaluated on every write, not only on insert.

    A partial index that held its entry after the row stopped matching the
    predicate would make one revision renderable exactly once, forever. This is
    the behaviour LE-12 depends on to retry a failed render.
    """
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyEditingJobRepository(database)
    try:
        await reset_data(database)
        project_id = EditingProjectId.new()
        timeline_id = TimelineId.new()
        await store_scene(database, project_id, timeline_id)
        first = make_job(EditingJobId.new(), project_id, timeline_id)
        await repository.save(first)

        started = first.start(UPDATED_AT + timedelta(seconds=1))
        await repository.update(started)
        assert (await repository.get(first.job_id)).status is EditingJobStatus.RUNNING

        second = make_job(EditingJobId.new(), project_id, timeline_id)
        await repository.save(second)
        assert await stored_job_ids(database) == {first.job_id.uuid, second.job_id.uuid}
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_an_update_rewrites_every_column_of_its_row(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    """One column set for insert and update, so neither can drift from the other.

    A narrower update would store a field on insert and silently drop it on
    every write after, which is the failure `materials` recorded when its
    description columns were split out. Here the whole row is written, including
    the identity columns -- writing them to their own values costs nothing and
    means a column added to the domain later cannot be missed by one of the two.

    The neighbouring job is here to catch an update whose `WHERE` lost its
    identifier: without it, a statement touching every row would pass every
    assertion about the row under test.
    """
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyEditingJobRepository(database)
    try:
        await reset_data(database)
        project_id = EditingProjectId.new()
        timeline_id = TimelineId.new()
        await store_scene(database, project_id, timeline_id)
        job = make_job(EditingJobId.new(), project_id, timeline_id)
        await repository.save(job)
        neighbour = make_job(
            EditingJobId.new(),
            project_id,
            timeline_id,
            status=EditingJobStatus.RUNNING,
        )
        await repository.save(neighbour)

        started = job.start(UPDATED_AT + timedelta(seconds=1))
        await repository.update(started)
        artifact = ArtifactId.new()
        succeeded = started.succeed(artifact, UPDATED_AT + timedelta(seconds=2))
        await repository.update(succeeded)

        assert await repository.get(job.job_id) == succeeded
        assert await stored_row(database, job.job_id.uuid) == row_values(
            job.job_id.uuid,
            project_id.uuid,
            timeline_id.uuid,
            status=EditingJobStatus.SUCCEEDED.value,
            output_artifact_id=artifact.uuid,
            updated_at=UPDATED_AT + timedelta(seconds=2),
        )
        assert await repository.get(neighbour.job_id) == neighbour
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("shift", "accepted"),
    [
        (timedelta(microseconds=-1), False),
        (timedelta(0), False),
        (timedelta(microseconds=1), True),
    ],
    ids=["one-tick-earlier", "the-same-instant", "one-tick-later"],
)
async def test_an_update_requires_its_timestamp_to_have_moved_forward(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
    shift: timedelta,
    accepted: bool,
) -> None:
    """Both endpoints and one microsecond either side of them.

    The endpoint itself -- an `updated_at` equal to the stored one -- is
    **refused**, which is a decision rather than an accident. A replayed write
    carrying the same instant is either a duplicate or a caller that never
    reloaded; accepting it would let a repository silently absorb the retry that
    the state machine is supposed to answer for. A microsecond is the smallest
    step `timestamptz` records, so these three cases sit either side of the only
    line there is.

    The comparison is a predicate inside the UPDATE rather than a read followed
    by a decision, because reading first has the identical defect one level
    down: two callers both read the old timestamp and both conclude they may
    proceed. Only the database sees one statement at a time.
    """
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyEditingJobRepository(database)
    try:
        await reset_data(database)
        project_id = EditingProjectId.new()
        timeline_id = TimelineId.new()
        await store_scene(database, project_id, timeline_id)
        job = make_job(EditingJobId.new(), project_id, timeline_id)
        await repository.save(job)

        # Built through the domain and then re-timed, so the only thing under
        # test is the timestamp: the transition itself is legal in all three.
        started = job.start(UPDATED_AT + timedelta(seconds=1))
        candidate = make_job(
            job.job_id,
            project_id,
            timeline_id,
            status=EditingJobStatus.RUNNING,
            updated_at=UPDATED_AT + shift,
        )
        assert candidate.status is started.status

        if accepted:
            await repository.update(candidate)
            assert (await repository.get(job.job_id)) == candidate
        else:
            with pytest.raises(EditingJobStale):
                await repository.update(candidate)
            assert (await repository.get(job.job_id)) == job
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_updating_a_job_that_was_never_stored_is_not_found(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    """The other meaning of `rowcount == 0`, told apart by the follow-up read.

    404 and 409 are different things for LE-06 to answer, and a caller can act
    on only one of them: a job that is not there will not appear on a reload.
    """
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyEditingJobRepository(database)
    try:
        await reset_data(database)
        project_id = EditingProjectId.new()
        timeline_id = TimelineId.new()
        await store_scene(database, project_id, timeline_id)

        with pytest.raises(EditingJobNotFound) as refused:
            await repository.update(
                make_job(
                    EditingJobId.new(),
                    project_id,
                    timeline_id,
                    status=EditingJobStatus.RUNNING,
                )
            )
        assert not isinstance(refused.value, EditingJobStale)
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_a_stale_snapshot_cannot_undo_a_render_that_already_finished(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    """The state machine judges a snapshot; the row is what has to be judged.

    The sequence is the one `EditingJob`'s own docstring describes, and every
    step of it is sanctioned:

    1. a worker takes a cancelling job -- cancellation is cooperative, so it may
       still finish -- and succeeds, writing `SUCCEEDED` with the artifact;
    2. a reconciliation pass holding the *earlier* `CANCELLING` snapshot decides
       the job was abandoned and calls `fail(WORKER_LOST)`;
    3. `CANCELLING -> FAILED` is a legal edge, so `EditingJob` produces the
       object without complaint, and its `updated_at` is *later* than the
       worker's -- so the timestamp predicate alone would let it through.

    The result would be a rendered video marked failed and its artifact
    orphaned. What refuses it is the second predicate: the row's own status has
    to be one the requested state can legally be reached from, and `SUCCEEDED`
    is terminal. The answer is `Stale` rather than a new outcome because it is
    the same instruction -- reload and decide again -- as an overtaken
    timestamp.
    """
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyEditingJobRepository(database)
    try:
        await reset_data(database)
        project_id = EditingProjectId.new()
        timeline_id = TimelineId.new()
        await store_scene(database, project_id, timeline_id)
        queued = make_job(EditingJobId.new(), project_id, timeline_id)
        await repository.save(queued)
        cancelling = queued.request_cancel(UPDATED_AT + timedelta(seconds=1))
        await repository.update(cancelling)

        artifact = ArtifactId.new()
        finished = cancelling.succeed(artifact, UPDATED_AT + timedelta(seconds=2))
        await repository.update(finished)

        # The reconciliation pass, still holding the snapshot from step 1 and
        # carrying a later timestamp than the worker's.
        abandoned = cancelling.fail(
            EditingJobFailureCode.WORKER_LOST, UPDATED_AT + timedelta(seconds=3)
        )
        assert abandoned.updated_at > finished.updated_at
        with pytest.raises(EditingJobStale):
            await repository.update(abandoned)

        reloaded = await repository.get(queued.job_id)
        assert reloaded == finished
        assert reloaded.status is EditingJobStatus.SUCCEEDED
        assert reloaded.output_artifact_id == artifact
        assert reloaded.failure_code is None
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_a_job_cannot_be_pushed_back_into_the_queue_by_an_update(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    """Nothing transitions *to* queued, so no update may write that state.

    A render that lost its worker cannot resume -- ffmpeg has no checkpoint --
    so re-running it is a new job. That is a domain rule, and this is the row
    that would otherwise carry it back: an `update` naming a queued job matches
    no row, whatever the timestamp says. It also keeps the third invariant
    honest, since a job walked back into the queue would occupy a slot the index
    could not have refused at insert time.
    """
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyEditingJobRepository(database)
    try:
        await reset_data(database)
        project_id = EditingProjectId.new()
        timeline_id = TimelineId.new()
        await store_scene(database, project_id, timeline_id)
        job = make_job(EditingJobId.new(), project_id, timeline_id)
        await repository.save(job)
        await repository.update(job.start(UPDATED_AT + timedelta(seconds=1)))

        with pytest.raises(EditingJobStale):
            await repository.update(
                make_job(
                    job.job_id,
                    project_id,
                    timeline_id,
                    updated_at=UPDATED_AT + timedelta(seconds=2),
                )
            )
        assert (await repository.get(job.job_id)).status is EditingJobStatus.RUNNING
    finally:
        await reset_data(database)
        await database.close()


# Rows the columns accept and the domain refuses. Each changes exactly one thing
# about an otherwise valid queued job, so the rejection is attributable to it.
# Revision and identifier shapes are *not* here: every row has to satisfy the
# composite foreign key to exist at all, so a revision of 0 or a stray timeline
# never reaches hydration -- the database refuses it first. What is left is
# everything the columns cannot express.
REFUSED_ROWS: dict[str, dict[str, object]] = {
    "unknown-status": {"status": "rendering"},
    "status-in-the-wrong-case": {"status": "QUEUED"},
    "empty-status": {"status": ""},
    "unknown-failure-code": {
        "status": EditingJobStatus.FAILED.value,
        "failure_code": "disk_on_fire",
    },
    "queued-carrying-an-artifact": {"output_artifact_id": ArtifactId.new().uuid},
    "queued-carrying-a-failure-code": {"failure_code": EditingJobFailureCode.RENDER_FAILED.value},
    "succeeded-without-an-artifact": {"status": EditingJobStatus.SUCCEEDED.value},
    "failed-without-a-failure-code": {"status": EditingJobStatus.FAILED.value},
    "succeeded-carrying-a-failure-code": {
        "status": EditingJobStatus.SUCCEEDED.value,
        "output_artifact_id": ArtifactId.new().uuid,
        "failure_code": EditingJobFailureCode.RENDER_FAILED.value,
    },
    "updated-before-it-was-created": {"updated_at": CREATED_AT - timedelta(microseconds=1)},
    "an-artifact-of-the-wrong-uuid-version": {
        "status": EditingJobStatus.SUCCEEDED.value,
        "output_artifact_id": UUID(int=0),
    },
    "a-job-identifier-of-the-wrong-uuid-version": {"job_id": UUID(int=0)},
    # The two below are queued rows on purpose, and the state is what makes them
    # worth having. Both nullable columns are legally empty on a queued job, so
    # a parser that answered `None` for a value it could not read would hydrate
    # these into perfectly ordinary queued jobs -- losing the fact that anything
    # was stored there at all. On a succeeded or failed row the same mistake is
    # still refused, but by the rule pairing a state with its facts, which is
    # why the cases above cannot pin it. Measured: a mutation swallowing the
    # identifier parse error survived until these two existed.
    "queued-carrying-an-unreadable-artifact": {"output_artifact_id": UUID(int=0)},
    "queued-carrying-an-unreadable-failure-code": {"failure_code": "disk_on_fire"},
}


@pytest.mark.asyncio
@pytest.mark.parametrize("overrides", list(REFUSED_ROWS.values()), ids=list(REFUSED_ROWS))
async def test_rows_the_domain_would_refuse_are_refused_at_hydration(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
    overrides: dict[str, object],
) -> None:
    """The stored row is input, not truth.

    Rows arrive from migrations, fixtures and hand-run statements as well as
    from `save`, and two of these columns hold enumeration members as text that
    PostgreSQL will never check. Hydrating through the constructor is what makes
    every row meet the rules a caller meets -- including the pairing of a state
    with the facts it is allowed to carry, which two independently nullable
    columns cannot express.
    """
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyEditingJobRepository(database)
    try:
        await reset_data(database)
        project_id = EditingProjectId.new()
        timeline_id = TimelineId.new()
        await store_scene(database, project_id, timeline_id)
        columns = dict(overrides)
        job_id = cast(UUID, columns.pop("job_id", EditingJobId.new().uuid))
        await insert_row(database, job_id, project_id.uuid, timeline_id.uuid, **columns)

        with pytest.raises(InvalidEditingJobModel):
            await repository.get(forged_identifier(job_id))
    finally:
        await reset_data(database)
        await database.close()


def forged_identifier(value: UUID) -> EditingJobId:
    """An `EditingJobId` holding a UUID its constructor would never accept.

    `EditingJobId(UUID(int=0))` raises, so a stored row whose `job_id` is not a
    v4 UUID cannot be addressed through the normal constructor at all. Building
    the instance without it is what lets a test reach such a row; the row itself
    gets there through a plain INSERT, which the `uuid` column takes happily.
    Subclassing is not an option -- the class is `@final`.
    """
    identifier = object.__new__(EditingJobId)
    object.__setattr__(identifier, "_value", value)
    return identifier


@pytest.mark.asyncio
async def test_a_timeline_whose_identifier_is_not_v4_is_refused_at_hydration(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    """The identifier columns take every UUID version; these types take one.

    The foreign key means this row can only exist if a timeline with the same
    nil identifier exists too, which is why the timeline is stored the same way.
    """
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyEditingJobRepository(database)
    try:
        await reset_data(database)
        project_id = EditingProjectId.new()
        timeline_id = TimelineId.new()
        await store_scene(database, project_id, timeline_id)
        nil_uuid = UUID(int=0)
        async with database.session() as session:
            await session.execute(
                update(timelines)
                .where(timelines.c.timeline_id == timeline_id.uuid)
                .values(timeline_id=nil_uuid)
            )
        job_id = EditingJobId.new()
        await insert_row(database, job_id.uuid, project_id.uuid, nil_uuid)

        with pytest.raises(InvalidEditingJobModel):
            await repository.get(job_id)
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_wrong_credentials_are_refused_without_leaking_the_identity(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    """A real server refusing a real login, against all three public methods.

    The `except Exception` tail is written once per `try`, so a method missing it
    leaks from that method alone while the other two stay clean. That is what
    makes exercising all three load-bearing rather than repetitive.
    """
    alembic_runner(postgresql_url, "upgrade", "head")
    url = make_url(postgresql_url)
    role = url.username
    assert role is not None
    database = Database.from_url(
        url.set(password="le05_wrong_password").render_as_string(hide_password=False)
    )
    try:
        repository = SqlAlchemyEditingJobRepository(database)
        job = make_job(EditingJobId.new(), EditingProjectId.new(), TimelineId.new())
        with pytest.raises(EditingJobPersistenceUnavailable) as saved:
            await repository.save(job)
        with pytest.raises(EditingJobPersistenceUnavailable) as updated:
            await repository.update(job.start(UPDATED_AT + timedelta(seconds=1)))
        with pytest.raises(EditingJobPersistenceUnavailable) as loaded:
            await repository.get(job.job_id)
        for captured in (saved, updated, loaded):
            rendered = "".join(traceback.format_exception(captured.value))
            assert role not in rendered
            assert "password authentication failed" not in rendered
            assert captured.value.__cause__ is None
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_the_repository_refuses_arguments_before_it_reaches_the_database(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    """A bare UUID carries exactly the value the column would compare against."""
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        repository = SqlAlchemyEditingJobRepository(database)
        with pytest.raises(EditingJobDataRejected):
            await repository.get(cast(EditingJobId, EditingJobId.new().uuid))
        with pytest.raises(EditingJobDataRejected):
            await repository.save(cast(EditingJob, object()))
        with pytest.raises(EditingJobDataRejected):
            await repository.update(cast(EditingJob, object()))
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
                            "where table_schema = 'public' and table_name = 'editing_jobs'"
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
                        "'public.editing_jobs'::regclass"
                    )
                )
            )
            index_definition = await session.scalar(
                text(
                    "select indexdef from pg_indexes where schemaname = 'public' "
                    f"and tablename = 'editing_jobs' and indexname = '{QUEUED_INDEX_NAME}'"
                )
            )
            foreign_key = await session.scalar(
                text(
                    "select pg_get_constraintdef(oid) from pg_constraint "
                    f"where conname = '{FOREIGN_KEY_NAME}'"
                )
            )
        assert revision == HEAD_REVISION
        assert columns == EXPECTED_COLUMNS
        assert constraints >= EXPECTED_CONSTRAINTS
        # The composite key, read back as PostgreSQL stores it. Column order is
        # part of it: the target is `timelines`'s superkey, which spells its
        # three columns in this order, and a reference in any other order is a
        # different constraint that PostgreSQL would refuse to create.
        assert foreign_key == (
            "FOREIGN KEY (timeline_id, timeline_revision, project_id) "
            "REFERENCES timelines(timeline_id, revision, project_id)"
        )
        # The partial index, with its predicate. `pg_indexes` renders the
        # `WHERE`, so an index created without it -- which would refuse a second
        # render of any revision, in any state -- fails here.
        assert index_definition == (
            f"CREATE UNIQUE INDEX {QUEUED_INDEX_NAME} ON public.editing_jobs "
            "USING btree (timeline_id, timeline_revision) "
            "WHERE ((status)::text = 'queued'::text)"
        )

        # `schema.py` and the migration each declare all of this separately, and
        # the three assertions below are the only thing comparing the two: every
        # other test in this file reads either the migrated database or this
        # file's own constants, so narrowing the `Table` alone would leave them
        # all green. `migrations/env.py` sets `target_metadata` to exactly this
        # metadata, which is what makes the drift consequential -- anything the
        # database has and this does not is what the next
        # `alembic revision --autogenerate` proposes dropping.
        declared = {
            column.name: (column.type.__class__.__name__, column.nullable)
            for column in editing_jobs.columns
        }
        assert declared == {
            name: (EXPECTED_TABLE_TYPES[name], shape[1] == "YES")
            for name, shape in EXPECTED_COLUMNS.items()
        }
        # `Table.constraints` holds the `Constraint` base, which declares no
        # `.columns`. Every concrete constraint class in SQLAlchemy 2.0.51 does
        # have one, so this narrowing never actually fires today; it is here so
        # that a constraint kind without one would surface as a failure rather
        # than be dropped by an `isinstance` filter. What does the work below is
        # the dictionary comparison.
        declared_constraints: dict[str, tuple[str, list[str]]] = {}
        for constraint in editing_jobs.constraints:
            assert isinstance(constraint, ColumnCollectionConstraint), constraint
            assert constraint.name is not None
            declared_constraints[str(constraint.name)] = (
                type(constraint).__name__,
                list(constraint.columns.keys()),
            )
        assert declared_constraints == EXPECTED_TABLE_CONSTRAINTS
        assert [
            element.target_fullname
            for constraint in editing_jobs.constraints
            if isinstance(constraint, ForeignKeyConstraint)
            for element in constraint.elements
        ] == [
            "timelines.timeline_id",
            "timelines.revision",
            "timelines.project_id",
        ]
        # And the index, which is **not** a constraint and therefore absent from
        # everything above. Its predicate is compared with the literal bound in,
        # because the default rendering hides `'queued'` behind a parameter and
        # would agree with an index restricted to any other state.
        declared_indexes = {
            str(index.name): (
                list(index.columns.keys()),
                index.unique,
                compiled_predicate(index.dialect_options["postgresql"]["where"]),
            )
            for index in editing_jobs.indexes
        }
        assert declared_indexes == EXPECTED_TABLE_INDEXES

        alembic_runner(postgresql_url, "downgrade", PREVIOUS_REVISION)
        async with database.session() as session:
            removed = await session.scalar(text("select to_regclass('public.editing_jobs')"))
            orphaned = await session.scalar(
                text(
                    "select count(*) from pg_indexes where schemaname = 'public' "
                    f"and indexname = '{QUEUED_INDEX_NAME}'"
                )
            )
        assert removed is None
        assert orphaned == 0
    finally:
        alembic_runner(postgresql_url, "upgrade", "head")
        await database.close()
