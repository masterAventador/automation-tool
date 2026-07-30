"""Fail-closed branches around the PostgreSQL editing job repository.

These cover what a real database cannot be made to produce on demand: a row that
is missing, a row malformed in a way the columns permit, and a session that
fails in each of the ways the driver can fail. Behaviour against a live
PostgreSQL -- including all three cross-aggregate invariants, which are held by
the table's structure rather than by any branch in this module -- is in the
integration suite.

Two things make this table different from the three before it. Its rows are
**mutable**: a job moves through six states, so there is an `update` alongside
`save`, and everything about staleness lives there. And two of its columns store
enumeration members as text, which is where LE-04's recorded failure applies:
`EditingJobStateMachine.is_terminal` answers `False` for a bare string, and
`False` means "still running" -- the unsafe direction. So a stored status has to
come back as a member or not at all.

Every malformed case is built so that fixing the one offending value would leave
a row the domain accepts. A rejection test whose row is illegal for a second
reason passes no matter what the code under test does.
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
    EditingJob,
    EditingJobFailureCode,
    EditingJobId,
    EditingJobStateMachine,
    EditingJobStatus,
    EditingProjectId,
    InvalidEditingJobModel,
    TaskId,
    TimelineId,
)
from automation_tool.control_plane.infrastructure.database import Database, editing_jobs, hydration
from automation_tool.control_plane.infrastructure.database import (
    editing_job_repository as repository_module,
)

# Distinctive enough that a leak into a message or traceback cannot be mistaken
# for anything else, and cannot pass by coincidence. A plain word would also
# match the `File "..."` lines `traceback.format_exception` renders for every
# frame -- on macOS a temporary directory sits under `/private/tmp`, so a
# sentinel like "private" goes red from a clone with nothing leaking at all.
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

CREATED_AT = datetime(2026, 7, 30, 4, 15, 30, 123_456, tzinfo=UTC)
UPDATED_AT = CREATED_AT + timedelta(seconds=90)
SHANGHAI = timezone(timedelta(hours=8))

TIMELINE_REVISION = 3

# The names PostgreSQL reports for the two constraints that share SQLSTATE
# `23505`. Duplicating a job identifier and queueing a second render of one
# revision arrive as the same exception class with the same code, so the name is
# the only thing that tells them apart.
PRIMARY_KEY_NAME = "pk_editing_jobs"
QUEUED_INDEX_NAME = "uq_editing_jobs_queued_timeline_revision"


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
    """Just enough of a `Result` for `.mappings().one_or_none()` and `.rowcount`."""

    def __init__(self, row: RowMapping | None, rowcount: int) -> None:
        self._row = row
        self.rowcount = rowcount

    def mappings(self) -> StubResult:
        return self

    def one_or_none(self) -> RowMapping | None:
        return self._row


class StubSession:
    def __init__(self, row: RowMapping | None, rowcount: int) -> None:
        self._row = row
        self._rowcount = rowcount

    async def execute(self, _statement: object) -> StubResult:
        return StubResult(self._row, self._rowcount)


class StubSessionScope:
    def __init__(self, row: RowMapping | None, rowcount: int) -> None:
        self._row = row
        self._rowcount = rowcount

    async def __aenter__(self) -> StubSession:
        return StubSession(self._row, self._rowcount)

    async def __aexit__(
        self,
        _exception_type: object,
        _exception: object,
        _traceback: object,
    ) -> None:
        return None


class StubSessions:
    def __init__(self, row: RowMapping | None, rowcount: int = 1) -> None:
        self._row = row
        self._rowcount = rowcount

    def begin(self) -> StubSessionScope:
        return StubSessionScope(self._row, self._rowcount)


def unreachable_database() -> Database:
    return Database.from_url(UNREACHABLE_URL, connect_timeout_seconds=0.05)


def make_job(
    job_id: EditingJobId | None = None,
    project_id: EditingProjectId | None = None,
    timeline_id: TimelineId | None = None,
    *,
    status: EditingJobStatus = EditingJobStatus.QUEUED,
    failure_code: EditingJobFailureCode | None = None,
    output_artifact_id: ArtifactId | None = None,
    updated_at: datetime = UPDATED_AT,
) -> EditingJob:
    return EditingJob(
        job_id=job_id or EditingJobId.new(),
        project_id=project_id or EditingProjectId.new(),
        timeline_id=timeline_id or TimelineId.new(),
        timeline_revision=TIMELINE_REVISION,
        status=status,
        failure_code=failure_code,
        output_artifact_id=output_artifact_id,
        created_at=CREATED_AT,
        updated_at=updated_at,
    )


def job_in_state(status: EditingJobStatus, *, updated_at: datetime = UPDATED_AT) -> EditingJob:
    """A job in one state, carrying exactly the facts that state requires.

    `EditingJob` refuses a succeeded job with no artifact and a failed one with
    no code, so a fixture parametrised over states cannot simply vary `status`.
    """
    return make_job(
        status=status,
        failure_code=(
            EditingJobFailureCode.WORKER_LOST if status is EditingJobStatus.FAILED else None
        ),
        output_artifact_id=(ArtifactId.new() if status is EditingJobStatus.SUCCEEDED else None),
        updated_at=updated_at,
    )


def started_pair(job_id: EditingJobId | None = None) -> tuple[EditingJob, EditingJob]:
    """A legal `(previous, changed)` pair: one queued job and its dispatch.

    `update` takes both because it is a compare-and-set -- `previous` is the
    version the caller read, and the statement only touches a row that still
    looks like it.
    """
    previous = make_job(job_id)
    return previous, make_job(
        previous.job_id,
        previous.project_id,
        previous.timeline_id,
        status=EditingJobStatus.RUNNING,
        updated_at=previous.updated_at + timedelta(seconds=1),
    )


def hydration_row(**overrides: object) -> RowMapping:
    """A row shaped the way asyncpg really hands one back.

    Identifiers arrive as `UUID` objects because the columns are `uuid`, and the
    two enumeration columns arrive as plain text -- which is the whole reason
    hydration has to parse them back into members.
    """
    values: dict[str, object] = {
        "job_id": EditingJobId.new().uuid,
        "project_id": EditingProjectId.new().uuid,
        "timeline_id": TimelineId.new().uuid,
        "timeline_revision": TIMELINE_REVISION,
        "status": EditingJobStatus.QUEUED.value,
        "failure_code": None,
        "output_artifact_id": None,
        "created_at": CREATED_AT,
        "updated_at": UPDATED_AT,
    }
    values.update(overrides)
    return cast(RowMapping, values)


def test_repository_refuses_a_database_it_does_not_own() -> None:
    with pytest.raises(EditingJobPersistenceUnavailable):
        repository_module.SqlAlchemyEditingJobRepository(cast(Database, object()))


@pytest.mark.asyncio
async def test_repository_refuses_foreign_argument_types() -> None:
    """Arguments are checked before a statement is built.

    A bare UUID and a sibling identifier both carry exactly the value the column
    would compare against, so leaving the check to the database would silently
    accept either one.
    """
    database = unreachable_database()
    try:
        repository = repository_module.SqlAlchemyEditingJobRepository(database)
        job = make_job()
        with pytest.raises(EditingJobDataRejected):
            await repository.save(cast(EditingJob, object()))
        with pytest.raises(EditingJobDataRejected):
            await repository.update(make_job(), cast(EditingJob, object()))
        with pytest.raises(EditingJobDataRejected):
            await repository.update(cast(EditingJob, object()), make_job())
        with pytest.raises(EditingJobDataRejected):
            await repository.get(cast(EditingJobId, job.job_id.uuid))
        with pytest.raises(EditingJobDataRejected):
            await repository.get(cast(EditingJobId, TaskId.new()))
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_an_unreachable_database_is_refused_without_leaking_the_connection() -> None:
    """A refused connection is refused without the connection string in it.

    Measured against a real PostgreSQL: `asyncpg` raises `ConnectionRefusedError`
    out of asyncio's connect call, and the SQLAlchemy dialect does not wrap it,
    because it is not one of asyncpg's own exceptions -- so it is an `OSError`
    and not a `SQLAlchemyError`.

    That classification is recorded here as a third-party fact, **not** as this
    module's load-bearing guard. Every `try` in the repository ends in an
    `except Exception` tail, so an `OSError` would be answered identically with
    the `_CONNECTION_FAILURES` clause deleted; the clause is kept as the shape
    shared across seven repositories, several of which have no tail and do
    depend on it. What this test pins is the outcome: `Unavailable`, and no host,
    port, user or password anywhere in the rendered traceback.

    All three public methods are exercised, because the tail is written once per
    `try` and one method missing it leaks from that method alone.
    """
    database = unreachable_database()
    try:
        repository = repository_module.SqlAlchemyEditingJobRepository(database)
        job = make_job()
        with pytest.raises(EditingJobPersistenceUnavailable) as saved:
            await repository.save(job)
        with pytest.raises(EditingJobPersistenceUnavailable) as updated:
            await repository.update(*started_pair(job.job_id))
        with pytest.raises(EditingJobPersistenceUnavailable) as loaded:
            await repository.get(job.job_id)
        for captured in (saved, updated, loaded):
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
        repository = repository_module.SqlAlchemyEditingJobRepository(database)
        object.__setattr__(
            database,
            "_sessions",
            FailingSessions(SQLAlchemyError("le05_leaked_database_failure")),
        )
        job = make_job()
        with pytest.raises(EditingJobPersistenceUnavailable) as saved:
            await repository.save(job)
        with pytest.raises(EditingJobPersistenceUnavailable) as updated:
            await repository.update(*started_pair(job.job_id))
        with pytest.raises(EditingJobPersistenceUnavailable) as loaded:
            await repository.get(job.job_id)
        for captured in (saved, updated, loaded):
            rendered = "".join(traceback.format_exception(captured.value))
            assert "le05_leaked_database_failure" not in rendered
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

    Both assertions below record third-party facts rather than this module's
    behaviour: with the catch-all tail present, an exception's position relative
    to `OSError` and `SQLAlchemyError` changes nothing about the answer it gets
    here. What this test pins for this module is the outcome: `Unavailable`,
    with the role absent from the rendered traceback even though the driver's
    message names it.
    """
    database = unreachable_database()
    try:
        repository = repository_module.SqlAlchemyEditingJobRepository(database)
        failure = asyncpg.exceptions.InvalidPasswordError(
            'password authentication failed for user "le05_leaked_user"'
        )
        assert not isinstance(failure, OSError | SQLAlchemyError)
        assert asyncpg.exceptions.PostgresError in type(failure).__mro__
        object.__setattr__(database, "_sessions", FailingSessions(failure))
        job = make_job()
        with pytest.raises(EditingJobPersistenceUnavailable) as saved:
            await repository.save(job)
        with pytest.raises(EditingJobPersistenceUnavailable) as updated:
            await repository.update(*started_pair(job.job_id))
        with pytest.raises(EditingJobPersistenceUnavailable) as loaded:
            await repository.get(job.job_id)
        for captured in (saved, updated, loaded):
            rendered = "".join(traceback.format_exception(captured.value))
            assert "le05_leaked_user" not in rendered
            assert captured.value.__cause__ is None
    finally:
        await database.close()


ABSENT_CAUSE = object()


def integrity_error(sqlstate: object, constraint_name: object, detail: str) -> IntegrityError:
    """An `IntegrityError` shaped the way a real violation arrives.

    Measured against PostgreSQL 18.4 with asyncpg 0.31.0 and SQLAlchemy 2.0.51,
    and the two levels matter:

    * `error.orig` is SQLAlchemy's own `AsyncAdapt_asyncpg_dbapi.IntegrityError`.
      Its entire attribute surface is `args`, `pgcode` and `sqlstate` -- there is
      **no** `constraint_name` on it;
    * `error.orig.__cause__` is asyncpg's `UniqueViolationError`, and that one
      carries `constraint_name` as a structured field, separate from `detail`,
      which is where the offending key values live.

    So the constraint name is reachable without touching any message, and that
    is what the repository reads. `ABSENT_CAUSE` builds the shape a SQLAlchemy
    release that stopped chaining would produce.
    """

    class AdapterError(Exception):
        pass

    class DriverError(Exception):
        pass

    original = AdapterError(detail)
    if sqlstate is not None:
        original.sqlstate = sqlstate  # type: ignore[attr-defined]
    if constraint_name is not ABSENT_CAUSE:
        cause = DriverError(detail)
        cause.constraint_name = constraint_name  # type: ignore[attr-defined]
        original.__cause__ = cause
    return IntegrityError("insert into editing_jobs", None, original)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sqlstate", "constraint_name", "expected"),
    [
        ("23505", PRIMARY_KEY_NAME, EditingJobAlreadyRegistered),
        ("23505", QUEUED_INDEX_NAME, EditingJobRevisionAlreadyQueued),
        # Same code, a name this module has never heard of: answering with
        # either specific outcome would be a statement about which rule was
        # broken, and there is nothing to base it on.
        ("23505", "uq_something_added_later", EditingJobDataRejected),
        ("23505", None, EditingJobDataRejected),
        # The shape a SQLAlchemy release that stopped chaining the driver's
        # exception would produce. It has to degrade to the answer that claims
        # least, not to a guess.
        ("23505", ABSENT_CAUSE, EditingJobDataRejected),
        # Only one foreign key exists on this table, so the code alone is enough
        # -- and the name is deliberately wrong here to prove the code is what
        # decides.
        ("23503", "some_other_name", EditingJobTimelineRevisionMissing),
        ("23503", ABSENT_CAUSE, EditingJobTimelineRevisionMissing),
        # NOT NULL and CHECK: neither can come from this table as it stands, and
        # neither improves on a retry.
        ("23502", None, EditingJobDataRejected),
        ("23514", None, EditingJobDataRejected),
        (None, PRIMARY_KEY_NAME, EditingJobDataRejected),
    ],
    ids=[
        "duplicate-job",
        "second-queued-render",
        "unknown-constraint",
        "unique-without-a-name",
        "unique-without-a-cause",
        "foreign-key",
        "foreign-key-without-a-cause",
        "not-null",
        "check",
        "no-sqlstate",
    ],
)
async def test_each_integrity_violation_gets_its_own_answer(
    sqlstate: object, constraint_name: object, expected: type[Exception]
) -> None:
    """One exception class, four things a caller has to do about it.

    A duplicate job identifier means this job is registered. A second queued
    render of one revision means someone else already asked for it -- a
    different resource, a different message, and LE-06 has to answer 409 about
    different things. A foreign key violation means the timeline revision this
    job names is not stored, which is 404 about *another* resource. None of the
    three improves on a retry, which is what keeps all of them away from
    `Unavailable`.

    The first two share SQLSTATE `23505` and arrive as the same exception class,
    so the constraint name is the only thing that separates them. It is read off
    asyncpg's structured `constraint_name` rather than matched inside the
    driver's message, because that message also carries PostgreSQL's DETAIL
    line, which quotes the offending key values verbatim.
    """
    database = unreachable_database()
    try:
        repository = repository_module.SqlAlchemyEditingJobRepository(database)
        object.__setattr__(
            database,
            "_sessions",
            FailingSessions(
                integrity_error(sqlstate, constraint_name, "Key (le05-private-detail) ...")
            ),
        )
        with pytest.raises(expected) as captured:
            await repository.save(make_job())
        rendered = "".join(traceback.format_exception(captured.value))
        assert "le05-private-detail" not in rendered
        assert captured.value.__cause__ is None
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_an_integrity_error_without_a_driver_exception_is_still_refused() -> None:
    """`IntegrityError.orig` can be `None`, measured.

    `getattr(None, "sqlstate", None)` is `None`, so the same fall-through
    applies -- but only if the lookup tolerates the missing object at all.
    Reaching through `error.orig.sqlstate` would raise `AttributeError` from
    inside the repository, which is neither the domain's error nor one of this
    module's.
    """
    database = unreachable_database()
    try:
        repository = repository_module.SqlAlchemyEditingJobRepository(database)
        object.__setattr__(
            database,
            "_sessions",
            FailingSessions(IntegrityError("insert into editing_jobs", None, None)),  # type: ignore[arg-type]
        )
        with pytest.raises(EditingJobDataRejected):
            await repository.save(make_job())
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_an_update_that_violates_a_constraint_is_refused_the_same_way() -> None:
    """`update` rewrites the columns the foreign key covers, so it can break it.

    `save` is not the only statement a constraint can refuse here, unlike the
    three write-once tables before this one: an update names the timeline, the
    revision and the project again, and PostgreSQL re-checks the composite
    foreign key on every one. Without its own `IntegrityError` clause the
    catch-all tail would report that as an unavailable database and invite a
    retry that can never succeed.
    """
    database = unreachable_database()
    try:
        repository = repository_module.SqlAlchemyEditingJobRepository(database)
        object.__setattr__(
            database,
            "_sessions",
            FailingSessions(integrity_error("23503", None, "Key (le05-private-detail) ...")),
        )
        with pytest.raises(EditingJobTimelineRevisionMissing) as captured:
            await repository.update(*started_pair())
        assert "le05-private-detail" not in "".join(traceback.format_exception(captured.value))
        assert captured.value.__cause__ is None
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_missing_job_is_not_found_and_a_present_one_hydrates() -> None:
    """Both post-query branches of `get`, and `save` returning normally.

    `save` is here because the unit layer's coverage is measured on its own:
    without it the one path through `save` that a caller actually takes would be
    covered only by the integration suite.
    """
    database = unreachable_database()
    try:
        repository = repository_module.SqlAlchemyEditingJobRepository(database)

        object.__setattr__(database, "_sessions", StubSessions(None))
        with pytest.raises(EditingJobNotFound):
            await repository.get(EditingJobId.new())
        await repository.save(make_job())

        job = make_job()
        object.__setattr__(
            database,
            "_sessions",
            StubSessions(
                hydration_row(
                    job_id=job.job_id.uuid,
                    project_id=job.project_id.uuid,
                    timeline_id=job.timeline_id.uuid,
                )
            ),
        )
        assert await repository.get(job.job_id) == job
    finally:
        await database.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["save", "update"], ids=["save", "update"])
async def test_a_serialisation_failure_is_not_reported_as_an_unavailable_database(
    monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    """Building the row is not database work and must not borrow its failures.

    `_row` keeps `_hydrate` outside its `try` for exactly this reason, and says
    so: a catch-all that swallows the row-building step turns "this code is
    broken" into "try again later", which is wrong and unfixable by retrying.
    Both writers have the same shape in reverse, so both are pinned -- a
    statement built *inside* the try would report a broken serialiser as an
    unavailable database and be retried forever.

    Nothing can trigger it today: every value the serialiser produces is a
    native the driver takes, and the job it reads from is already validated.
    That is an argument for the guard being cheap, not for leaving the failure
    mode wired the dangerous way -- and a field added to the domain is exactly
    how "nothing can trigger it today" stops being true.
    """
    database = unreachable_database()
    try:
        repository = repository_module.SqlAlchemyEditingJobRepository(database)
        object.__setattr__(database, "_sessions", StubSessions(None))

        class SerialiserFailure(Exception):
            pass

        def explode(_job: EditingJob) -> dict[str, object]:
            raise SerialiserFailure("le05_leaked_serialiser_detail")

        monkeypatch.setattr(repository_module, "_column_values", explode)
        previous, changed = started_pair()
        with pytest.raises(SerialiserFailure):
            if method == "save":
                await repository.save(changed)
            else:
                await repository.update(previous, changed)
    finally:
        await database.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("row", "expected"),
    [(None, EditingJobNotFound), (hydration_row(), EditingJobStale)],
    ids=["row-is-gone", "row-is-still-there"],
)
async def test_an_update_that_matched_nothing_says_which_of_the_two_it_was(
    row: RowMapping | None, expected: type[Exception]
) -> None:
    """`rowcount == 0` has two meanings and they are not the same instruction.

    A job that is not stored is 404 and there is nothing to retry. A job that is
    stored but did not match the predicates is 409: the caller is holding a
    snapshot that has been overtaken, and reloading gives it something to do.
    Collapsing them would tell a caller to stop asking about a job that exists.

    The follow-up read is **not** the second half of the guard. The protection
    decision was made in full by the UPDATE, in one statement, before this read
    runs. Sharing a transaction with it does not make the two answers
    exhaustive: measured on this database, `transaction_isolation` is
    `read committed`, so every statement takes a fresh snapshot and another
    connection can delete the row in between. Both labels are safe inside that
    window -- both tell the caller to stop and reload -- and only the label on a
    concurrently deleted row can come out wrong.
    """
    database = unreachable_database()
    try:
        repository = repository_module.SqlAlchemyEditingJobRepository(database)
        object.__setattr__(database, "_sessions", StubSessions(row, rowcount=0))
        with pytest.raises(expected):
            await repository.update(*started_pair())
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_an_update_that_matched_its_row_returns_quietly() -> None:
    database = unreachable_database()
    try:
        repository = repository_module.SqlAlchemyEditingJobRepository(database)
        object.__setattr__(database, "_sessions", StubSessions(None, rowcount=1))
        await repository.update(*started_pair())
    finally:
        await database.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("previous_status", "changed_status"),
    [
        (EditingJobStatus.RUNNING, EditingJobStatus.QUEUED),
        (EditingJobStatus.QUEUED, EditingJobStatus.SUCCEEDED),
        (EditingJobStatus.QUEUED, EditingJobStatus.CANCELLED),
        (EditingJobStatus.SUCCEEDED, EditingJobStatus.FAILED),
        (EditingJobStatus.FAILED, EditingJobStatus.RUNNING),
        (EditingJobStatus.CANCELLED, EditingJobStatus.SUCCEEDED),
        (EditingJobStatus.RUNNING, EditingJobStatus.RUNNING),
    ],
    ids=[
        "back-into-the-queue",
        "queued-straight-to-succeeded",
        "queued-straight-to-cancelled",
        "out-of-a-terminal-state",
        "out-of-another-terminal-state",
        "out-of-a-third-terminal-state",
        "a-state-to-itself",
    ],
)
async def test_a_pair_that_is_not_a_legal_transition_is_a_caller_error(
    previous_status: EditingJobStatus, changed_status: EditingJobStatus
) -> None:
    """`DataRejected` rather than `Stale`, and the difference is the instruction.

    A `(previous, changed)` pair that is not an edge of the graph means the
    caller built one of them from something other than the other. No reload
    fixes that, so `Stale` -- whose whole meaning is "read it again and decide"
    -- would send the caller round a loop forever.

    Checked in Python rather than as a SQL predicate. Nothing is lost by moving
    it out of the statement, because the compare-and-set already pins the row's
    status to `previous`'s; what is gained is that a caller error stops being
    reported as a concurrency outcome. `a-state-to-itself` is here because the
    graph has no self-loops: a replayed write is not a transition.
    """
    database = unreachable_database()
    try:
        repository = repository_module.SqlAlchemyEditingJobRepository(database)
        # A session that would report success, so a missing guard shows up as a
        # write that went through rather than as an incidental failure.
        object.__setattr__(database, "_sessions", StubSessions(None, rowcount=1))
        previous = job_in_state(previous_status)
        changed = make_job(
            previous.job_id,
            previous.project_id,
            previous.timeline_id,
            status=changed_status,
            failure_code=(
                EditingJobFailureCode.WORKER_LOST
                if changed_status is EditingJobStatus.FAILED
                else None
            ),
            output_artifact_id=(
                ArtifactId.new() if changed_status is EditingJobStatus.SUCCEEDED else None
            ),
            updated_at=previous.updated_at + timedelta(seconds=1),
        )
        assert not EditingJobStateMachine.can_transition(previous_status, changed_status)
        with pytest.raises(EditingJobDataRejected):
            await repository.update(previous, changed)
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_an_update_refuses_a_pair_naming_two_different_jobs() -> None:
    """A compare-and-set needs the version of *this* row, not another one's."""
    database = unreachable_database()
    try:
        repository = repository_module.SqlAlchemyEditingJobRepository(database)
        object.__setattr__(database, "_sessions", StubSessions(None, rowcount=1))
        previous, changed = started_pair()
        stranger = make_job(EditingJobId.new(), status=EditingJobStatus.QUEUED)
        with pytest.raises(EditingJobDataRejected):
            await repository.update(stranger, changed)
        with pytest.raises(EditingJobDataRejected):
            await repository.update(previous, make_job(EditingJobId.new()))
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_an_update_refuses_a_timestamp_that_goes_backwards() -> None:
    """The one thing the old `updated_at <` predicate really did guard.

    The compare-and-set replaced that predicate and re-covered most of what it
    caught: a replay cannot recur, because the version pair it names is gone the
    moment the first write lands. **Clocks running backwards it does not cover.**
    Nothing in the statement compares the incoming timestamp with the stored one
    -- the stored one is only tested for equality with `previous` -- so a
    `changed` carrying an earlier instant writes it, and the row's `updated_at`
    moves back in time.

    `_moved_to` refuses that, so no transition method can produce it; reaching it
    needs a hand-built `EditingJob`. That is the same door the identity-column
    split just closed for re-pointing, and leaving this one open while closing
    that one would be an odd place to stop. It also matters downstream: LE-06 and
    LE-12 order and filter on `updated_at`, and a row that moved backwards
    reorders silently rather than failing.

    `<` and not `<=`. Equality is load-bearing: the domain permits a transition
    whose timestamp equals its predecessor's, and
    `test_the_version_is_the_status_and_the_timestamp_together` in the
    integration suite depends on exactly that being allowed through.
    """
    database = unreachable_database()
    try:
        repository = repository_module.SqlAlchemyEditingJobRepository(database)
        # A session that would report a successful write, so a missing guard
        # shows up as a write that went through rather than as some other error.
        object.__setattr__(database, "_sessions", StubSessions(None, rowcount=1))
        previous = make_job()
        backwards = make_job(
            previous.job_id,
            previous.project_id,
            previous.timeline_id,
            status=EditingJobStatus.RUNNING,
            updated_at=previous.updated_at - timedelta(seconds=80),
        )
        # Still after `created_at`, so the domain builds it without complaint --
        # the row is not malformed, it just goes back in time.
        assert backwards.updated_at > backwards.created_at
        assert EditingJobStateMachine.can_transition(previous.status, backwards.status)
        with pytest.raises(EditingJobDataRejected):
            await repository.update(previous, backwards)

        # The endpoint stays legal, which is what keeps the guard from being one
        # notch too strong.
        same_instant = make_job(
            previous.job_id,
            previous.project_id,
            previous.timeline_id,
            status=EditingJobStatus.RUNNING,
            updated_at=previous.updated_at,
        )
        await repository.update(previous, same_instant)
        # ... and one microsecond earlier is already refused.
        with pytest.raises(EditingJobDataRejected):
            await repository.update(
                previous,
                make_job(
                    previous.job_id,
                    previous.project_id,
                    previous.timeline_id,
                    status=EditingJobStatus.RUNNING,
                    updated_at=previous.updated_at - timedelta(microseconds=1),
                ),
            )
    finally:
        await database.close()


def test_the_two_column_sets_partition_the_row_exactly() -> None:
    """The guarantee that survived narrowing the update.

    `update` writes only `_MUTABLE_COLUMNS`, which is what makes the identity
    columns and `created_at` write-once structurally. The risk in narrowing it is
    the one `materials` recorded: a field added to the domain gets stored on
    insert and silently dropped by every update after. This assertion is what
    replaces "write everything" -- a new column has to be classified, or it
    belongs to neither set and this fails.

    Compared against the table as well as the serialiser, so that a column added
    to `schema.py` and forgotten in `_column_values` is caught too.
    """
    assert repository_module._IDENTITY_COLUMNS.isdisjoint(repository_module._MUTABLE_COLUMNS)
    partition = repository_module._IDENTITY_COLUMNS | repository_module._MUTABLE_COLUMNS
    assert partition == set(repository_module._column_values(make_job()))
    assert partition == {column.name for column in editing_jobs.columns}
    # And the filter really drops the identity half rather than passing
    # everything through.
    assert set(repository_module._mutable_values(make_job())) == (
        repository_module._MUTABLE_COLUMNS
    )


def test_hydration_rebuilds_every_field_as_the_domain_declares_it() -> None:
    """Members, not the text they are stored as.

    `EditingJobStateMachine.is_terminal` answers `False` for a bare string, and
    `False` reads as "still running" -- so a status left as text would not fail,
    it would quietly mean the wrong thing. Identity comparisons rather than
    equality, because a `StrEnum` member compares equal to its own text and
    `== "succeeded"` holds for exactly the value this exists to reject.
    """
    artifact = ArtifactId.new()
    hydrated = repository_module._hydrate(
        hydration_row(
            status=EditingJobStatus.SUCCEEDED.value,
            output_artifact_id=artifact.uuid,
        )
    )
    assert type(hydrated) is EditingJob
    assert hydrated.status is EditingJobStatus.SUCCEEDED
    assert hydrated.output_artifact_id == artifact
    assert type(hydrated.output_artifact_id) is ArtifactId
    assert hydrated.failure_code is None
    assert type(hydrated.job_id) is EditingJobId
    assert type(hydrated.project_id) is EditingProjectId
    assert type(hydrated.timeline_id) is TimelineId
    assert hydrated.timeline_revision == TIMELINE_REVISION
    assert hydrated.created_at == CREATED_AT
    assert hydrated.updated_at == UPDATED_AT

    failed = repository_module._hydrate(
        hydration_row(
            status=EditingJobStatus.FAILED.value,
            failure_code=EditingJobFailureCode.WORKER_LOST.value,
        )
    )
    assert failed.status is EditingJobStatus.FAILED
    assert failed.failure_code is EditingJobFailureCode.WORKER_LOST
    assert failed.output_artifact_id is None


@pytest.mark.parametrize(
    "stored",
    ["render_queued", "Queued", "", None, 7, True],
    ids=["unknown", "wrong-case", "empty", "null", "number", "boolean"],
)
def test_an_unrecognised_enumeration_value_is_refused_by_the_parser_itself(
    stored: object,
) -> None:
    """Pinned at the function, because no row can pin it.

    The obvious test -- store `"render_queued"` as a status and watch the row be
    refused -- passes whatever this function does. Hand the raw string back
    instead of refusing and `EditingJob.__post_init__` rejects it on
    `not isinstance(self.status, EditingJobStatus)`; the row is still refused,
    the reason is no longer this function.
    """
    with pytest.raises(InvalidEditingJobModel):
        hydration.enumeration_member(EditingJobStatus, stored, InvalidEditingJobModel)
    with pytest.raises(InvalidEditingJobModel):
        hydration.enumeration_member(EditingJobFailureCode, stored, InvalidEditingJobModel)


def test_the_enumeration_parser_returns_the_member_not_its_text() -> None:
    """The other side, so the refusals above are not a parser that only fails."""
    parsed = hydration.enumeration_member(EditingJobStatus, "cancelling", InvalidEditingJobModel)
    assert parsed is EditingJobStatus.CANCELLING
    assert (
        hydration.enumeration_member(EditingJobFailureCode, "worker_lost", InvalidEditingJobModel)
        is EditingJobFailureCode.WORKER_LOST
    )


@pytest.mark.parametrize(
    ("column", "stored"),
    [
        ("job_id", UUID(int=0)),
        ("job_id", "not-a-uuid"),
        ("job_id", None),
        ("project_id", UUID(int=0)),
        ("project_id", "not-a-uuid"),
        ("timeline_id", UUID(int=0)),
        ("timeline_id", "not-a-uuid"),
        ("output_artifact_id", UUID(int=0)),
        ("output_artifact_id", "not-a-uuid"),
    ],
    ids=[
        "job-nil",
        "job-text",
        "job-null",
        "project-nil",
        "project-text",
        "timeline-nil",
        "timeline-text",
        "artifact-nil",
        "artifact-text",
    ],
)
def test_hydration_refuses_an_identifier_the_column_would_accept(
    column: str, stored: object
) -> None:
    """A `uuid` column takes every version; these identifiers take only v4.

    `InvalidResourceId` folds into the domain's own error rather than surfacing
    on its own: a non-v4 identifier is one more way for a stored row to be
    unusable, and no caller should have to catch two exceptions to mean "this
    row is not an editing job".

    The artifact cases run on a succeeded row, where the column is the one thing
    that has to be present -- on a queued row `None` is the only legal value, so
    a parser that dropped the identifier would look correct.
    """
    overrides: dict[str, object] = {column: stored}
    if column == "output_artifact_id":
        overrides["status"] = EditingJobStatus.SUCCEEDED.value
    with pytest.raises(InvalidEditingJobModel):
        repository_module._hydrate(hydration_row(**overrides))


@pytest.mark.parametrize(
    ("column", "stored"),
    [
        ("output_artifact_id", UUID(int=0)),
        ("output_artifact_id", "not-a-uuid"),
        ("output_artifact_id", 7),
        ("failure_code", "disk_on_fire"),
        ("failure_code", ""),
        ("failure_code", 7),
    ],
    ids=[
        "artifact-nil",
        "artifact-text",
        "artifact-number",
        "failure-code-unknown",
        "failure-code-empty",
        "failure-code-number",
    ],
)
def test_a_value_that_cannot_be_parsed_is_not_read_as_the_absence_of_one(
    column: str, stored: object
) -> None:
    """The queued row is the whole point, and it took a surviving mutation to see it.

    Both of these columns are nullable and `None` is an ordinary value in them,
    so the dangerous failure is not an error -- it is a parser that answers
    `None` when it cannot read what is stored. That turns "something was written
    here and this code cannot read it" into "nothing was written here", silently.

    The obvious place to assert it is a *succeeded* row with a malformed
    artifact, and that test passes whichever way the parser behaves: drop the
    identifier and the row becomes "succeeded without an artifact", which
    `EditingJob` refuses for a completely different reason. Measured -- a
    mutation swallowing `InvalidResourceId` and returning `None` survived the
    whole suite until this case existed.

    A **queued** row is the one that can tell them apart. Dropping the value
    leaves a queued job with no artifact and no failure code, which is entirely
    valid, so the swallowing version hydrates it happily while the real one
    refuses. That difference is the assertion.
    """
    with pytest.raises(InvalidEditingJobModel):
        repository_module._hydrate(hydration_row(**{column: stored}))


def test_an_absent_artifact_is_an_ordinary_value_rather_than_a_missing_one() -> None:
    """`None` is what a job that has not produced a file carries.

    Without this the identifier parser could refuse `None` outright and every
    rejection case above would still pass -- while no queued job could ever be
    read back.
    """
    hydrated = repository_module._hydrate(hydration_row())
    assert hydrated.output_artifact_id is None
    assert hydrated.failure_code is None


# Rows the columns accept and the domain refuses. Each changes exactly one thing
# about an otherwise valid queued job, so the rejection is attributable to it.
FACTS_THAT_DO_NOT_MATCH_THE_STATUS: dict[str, dict[str, object]] = {
    "queued-carrying-an-artifact": {"output_artifact_id": ArtifactId.new().uuid},
    "queued-carrying-a-failure-code": {"failure_code": EditingJobFailureCode.RENDER_FAILED.value},
    "running-carrying-an-artifact": {
        "status": EditingJobStatus.RUNNING.value,
        "output_artifact_id": ArtifactId.new().uuid,
    },
    "cancelled-carrying-a-failure-code": {
        "status": EditingJobStatus.CANCELLED.value,
        "failure_code": EditingJobFailureCode.WORKER_LOST.value,
    },
    "succeeded-without-an-artifact": {"status": EditingJobStatus.SUCCEEDED.value},
    "succeeded-carrying-a-failure-code": {
        "status": EditingJobStatus.SUCCEEDED.value,
        "output_artifact_id": ArtifactId.new().uuid,
        "failure_code": EditingJobFailureCode.RENDER_FAILED.value,
    },
    "failed-without-a-failure-code": {"status": EditingJobStatus.FAILED.value},
    "failed-carrying-an-artifact": {
        "status": EditingJobStatus.FAILED.value,
        "failure_code": EditingJobFailureCode.RENDER_FAILED.value,
        "output_artifact_id": ArtifactId.new().uuid,
    },
}


@pytest.mark.parametrize(
    "overrides",
    list(FACTS_THAT_DO_NOT_MATCH_THE_STATUS.values()),
    ids=list(FACTS_THAT_DO_NOT_MATCH_THE_STATUS),
)
def test_hydration_refuses_a_row_whose_facts_do_not_match_its_status(
    overrides: dict[str, object],
) -> None:
    """Two nullable columns and six states: the column cannot say which go together.

    `failure_code` and `output_artifact_id` are nullable because most states
    carry neither, so SQL can express "sometimes absent" and nothing more. Which
    absence belongs to which state is `EditingJob._validate_facts_match_status`,
    and hydration is where a stored row has to meet it.
    """
    with pytest.raises(InvalidEditingJobModel):
        repository_module._hydrate(hydration_row(**overrides))


ILLEGAL_ENUMERATION_ROWS: dict[str, dict[str, object]] = {
    "unknown-status": {"status": "rendering"},
    "status-in-the-wrong-case": {"status": "QUEUED"},
    "empty-status": {"status": ""},
    "unknown-failure-code": {
        "status": EditingJobStatus.FAILED.value,
        "failure_code": "disk_on_fire",
    },
    "failure-code-in-the-wrong-case": {
        "status": EditingJobStatus.FAILED.value,
        "failure_code": "WORKER_LOST",
    },
}


@pytest.mark.parametrize(
    "overrides",
    list(ILLEGAL_ENUMERATION_ROWS.values()),
    ids=list(ILLEGAL_ENUMERATION_ROWS),
)
def test_hydration_refuses_an_enumeration_value_the_column_would_accept(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(InvalidEditingJobModel):
        repository_module._hydrate(hydration_row(**overrides))


def test_hydration_normalises_both_timestamps_to_utc() -> None:
    """Both columns, because the conversion is written once per column."""
    hydrated = repository_module._hydrate(
        hydration_row(
            created_at=CREATED_AT.astimezone(SHANGHAI),
            updated_at=UPDATED_AT.astimezone(SHANGHAI),
        )
    )
    assert hydrated.created_at == CREATED_AT
    assert hydrated.created_at.tzinfo is UTC
    assert hydrated.updated_at == UPDATED_AT
    assert hydrated.updated_at.tzinfo is UTC


@pytest.mark.parametrize("column", ["created_at", "updated_at"], ids=["created", "updated"])
@pytest.mark.parametrize(
    "stored",
    [None, "2026-07-30T04:15:30Z", CREATED_AT.replace(tzinfo=None), 0],
    ids=["null", "text", "naive", "number"],
)
def test_hydration_refuses_a_timestamp_it_cannot_trust(column: str, stored: object) -> None:
    """A naive timestamp is the dangerous one: `.astimezone` would launder it.

    Converting before validating does not fail on a naive value -- it
    reinterprets it in the host's timezone, moves the instant, and hands back
    something aware that sails through the domain's check. That is why
    `normalise_timestamp` guards first and converts second, and why this asserts
    on both columns.
    """
    with pytest.raises(InvalidEditingJobModel):
        repository_module._hydrate(hydration_row(**{column: stored}))


def test_hydration_refuses_a_row_that_was_updated_before_it_was_created() -> None:
    """The columns cannot compare themselves; the domain does."""
    with pytest.raises(InvalidEditingJobModel):
        repository_module._hydrate(hydration_row(updated_at=CREATED_AT - timedelta(microseconds=1)))


@pytest.mark.parametrize(
    "revision",
    [0, -1, True, "3", None, 3.0],
    ids=["zero", "negative", "boolean", "text", "null", "float"],
)
def test_hydration_refuses_a_revision_the_column_would_accept(revision: object) -> None:
    """`integer` takes zero and negatives; a revision starts at one.

    `True` is here because it is an `int` in Python and would otherwise pass as
    revision 1 -- the domain spells its own check `type(...) is not int` for
    exactly that reason.
    """
    with pytest.raises(InvalidEditingJobModel):
        repository_module._hydrate(hydration_row(timeline_revision=revision))


def test_the_longest_value_each_enumeration_can_produce_fits_its_column() -> None:
    """The domain's members and the column widths have to agree.

    A width narrower than the longest member turns a valid job into a
    `StringDataRightTruncation` at insert time, and it would only show up for
    whichever member happens to be exercised. Reading both sides off their own
    definitions is what keeps this from becoming a third copy of the numbers.
    """
    status_width = cast(int, editing_jobs.c.status.type.length)  # type: ignore[attr-defined]
    failure_width = cast(int, editing_jobs.c.failure_code.type.length)  # type: ignore[attr-defined]
    assert max(len(member.value) for member in EditingJobStatus) <= status_width
    assert max(len(member.value) for member in EditingJobFailureCode) <= failure_width
