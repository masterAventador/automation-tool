"""PostgreSQL storage for editing jobs."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final, Never, cast

from sqlalchemy import insert, select, update
from sqlalchemy.engine import CursorResult, RowMapping
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
    InvalidResourceId,
    TimelineId,
)

from .hydration import enumeration_member, normalise_timestamp
from .schema import editing_jobs
from .session import Database

# A refused or timed-out connection surfaces as an `OSError`, not a
# `SQLAlchemyError`: it comes out of asyncio's connect call, and the asyncpg
# dialect only wraps asyncpg's own exceptions. `session.py` and six other
# repositories catch the same pair for the same reason.
#
# **In this module that pairing changes no behaviour.** Every `try` here ends in
# an `except Exception` tail answering with the same failure, so deleting this
# clause would be invisible: the tail already covers `OSError` and
# `SQLAlchemyError` alike. It is kept as the shape shared across eight
# repositories -- several of which have no tail, and for those the distinction
# is what stops a raw socket error reaching the caller. Treat the sentence above
# as documenting why the pair exists at all, not as a claim that this file would
# leak without it. Tests naming these classes are pinning the third-party fact,
# not this module's behaviour.
_CONNECTION_FAILURES = (OSError, SQLAlchemyError)

# PostgreSQL's SQLSTATE for the violations this table can produce.
_UNIQUE_VIOLATION: Final = "23505"
_FOREIGN_KEY_VIOLATION: Final = "23503"

# Which unique rule was broken, since both arrive under `23505`. The names have
# to match `schema.py` and the migration; a rename that misses one of the three
# places lands on the fall-through, which claims less rather than claiming
# something wrong.
_PRIMARY_KEY: Final = "pk_editing_jobs"
_QUEUED_INDEX: Final = "uq_editing_jobs_queued_timeline_revision"

# The row split into what an update may rewrite and what it may not. `update`
# writes only the mutable half, which is what makes the identity columns and
# `created_at` write-once as a matter of structure rather than of convention.
#
# Naming both halves rather than just one is the point. The reason `update`
# originally wrote every column was to stop a field added to the domain from
# being stored on insert and then silently dropped by every update after it --
# a real failure, recorded on `materials`. A named split keeps that guarantee
# without the cost: a test asserts these two sets partition both
# `_column_values` and the table's own columns, so a new field cannot be added
# without being classified as one or the other.
_IDENTITY_COLUMNS: Final = frozenset(
    {"job_id", "project_id", "timeline_id", "timeline_revision", "created_at"}
)
_MUTABLE_COLUMNS: Final = frozenset({"status", "failure_code", "output_artifact_id", "updated_at"})


def _refuse_integrity_violation(error: IntegrityError) -> Never:
    """Turn one exception class into the answers a caller can act on.

    A duplicate `job_id` means this job is registered. A second queued render of
    one revision means the work is already asked for -- a different message and
    a different next move, even though PostgreSQL reports both under `23505`, so
    the constraint name is the only thing separating them. A foreign key
    violation means the timeline revision the job names is not stored, under
    that project, with that number; only one foreign key exists on this table,
    so the code alone identifies it.

    None of the three improves on a retry, which is what keeps all of them away
    from `EditingJobPersistenceUnavailable`. Anything else -- a NOT NULL
    violation is `23502`, a CHECK is `23514` -- cannot come from this table as
    it stands, and neither can a unique violation naming something else, but
    "the database refused this row" is still what happened and
    `EditingJobDataRejected` says so without inviting a retry or guessing which
    rule broke.

    Both lookups go through `getattr`, and for two different measured reasons.
    `error.orig` can be `None` outright. And the constraint name is not on
    `error.orig` at all: that object is SQLAlchemy's
    `AsyncAdapt_asyncpg_dbapi.IntegrityError`, whose attribute surface is
    `args`, `pgcode` and `sqlstate`; asyncpg's own exception, the one carrying
    `constraint_name`, is one link down the chain on `__cause__`. Reading it
    there rather than matching the name inside the driver's message is
    deliberate -- the message also carries PostgreSQL's DETAIL line, which
    quotes the offending key values, three of them at once for the composite
    key. A SQLAlchemy release that stopped chaining would leave this `None` and
    fall through to the answer that claims least, which is the right way for it
    to degrade.
    """
    sqlstate = getattr(error.orig, "sqlstate", None)
    if sqlstate == _FOREIGN_KEY_VIOLATION:
        raise EditingJobTimelineRevisionMissing from None
    if sqlstate == _UNIQUE_VIOLATION:
        constraint = getattr(getattr(error.orig, "__cause__", None), "constraint_name", None)
        if constraint == _PRIMARY_KEY:
            raise EditingJobAlreadyRegistered from None
        if constraint == _QUEUED_INDEX:
            raise EditingJobRevisionAlreadyQueued from None
    raise EditingJobDataRejected from None


def _optional_artifact_id(stored: object) -> ArtifactId | None:
    """`None` is an ordinary value: only a succeeded job has produced a file."""
    if stored is None:
        return None
    return ArtifactId.parse(stored)


def _optional_failure_code(stored: object) -> EditingJobFailureCode | None:
    """`None` is an ordinary value: only a failed job has a reason to give."""
    if stored is None:
        return None
    return enumeration_member(EditingJobFailureCode, stored, InvalidEditingJobModel)


def _hydrate(row: RowMapping) -> EditingJob:
    """Rebuild a job by constructing it, so a stored row is re-validated.

    Nothing in the table stops a row the domain would refuse. Two columns hold
    enumeration members as text that PostgreSQL never checks; two more are
    nullable because most states carry neither, which leaves the pairing of a
    state with the facts it may hold unexpressed; and no column can compare
    `updated_at` against `created_at`. Rows arrive from migrations, fixtures and
    hand-run statements as well as from `save`, so going through the constructor
    is what makes every one of them meet the rules a caller meets.
    `InvalidEditingJobModel` then propagates rather than being translated: a row
    the domain rejects is bad data, not a repository failure, and the caller has
    to be able to tell those apart.

    `InvalidResourceId` folds into that same error rather than surfacing on its
    own -- the `uuid` columns accept every version, so a non-v4 identifier is one
    more way for a stored row to be unusable, and no caller should have to catch
    two exceptions to mean "this row is not an editing job".
    """
    try:
        job_id = EditingJobId.parse(row["job_id"])
        project_id = EditingProjectId.parse(row["project_id"])
        timeline_id = TimelineId.parse(row["timeline_id"])
        output_artifact_id = _optional_artifact_id(row["output_artifact_id"])
    except InvalidResourceId:
        raise InvalidEditingJobModel from None
    return EditingJob(
        job_id=job_id,
        project_id=project_id,
        timeline_id=timeline_id,
        timeline_revision=cast(int, row["timeline_revision"]),
        status=enumeration_member(EditingJobStatus, row["status"], InvalidEditingJobModel),
        failure_code=_optional_failure_code(row["failure_code"]),
        output_artifact_id=output_artifact_id,
        created_at=cast(datetime, normalise_timestamp(row["created_at"])),
        updated_at=cast(datetime, normalise_timestamp(row["updated_at"])),
    )


def _column_values(job: EditingJob) -> dict[str, object]:
    """The full row, as `save` writes it and as `_mutable_values` filters it.

    One function for both writers, so that a field added to the domain cannot be
    stored on insert and then silently dropped by every update after it. What
    keeps the update honest is not that it writes everything -- it does not --
    but that `_IDENTITY_COLUMNS` and `_MUTABLE_COLUMNS` are asserted to
    partition exactly these keys.
    """
    return {
        "job_id": job.job_id.uuid,
        "project_id": job.project_id.uuid,
        "timeline_id": job.timeline_id.uuid,
        "timeline_revision": job.timeline_revision,
        "status": job.status.value,
        "failure_code": None if job.failure_code is None else job.failure_code.value,
        "output_artifact_id": (
            None if job.output_artifact_id is None else job.output_artifact_id.uuid
        ),
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


def _mutable_values(job: EditingJob) -> dict[str, object]:
    """Only the columns a transition is allowed to move."""
    return {name: value for name, value in _column_values(job).items() if name in _MUTABLE_COLUMNS}


class SqlAlchemyEditingJobRepository:
    """Editing job rows, which unlike the other three tables change over time."""

    def __init__(self, database: Database) -> None:
        if not isinstance(database, Database):
            raise EditingJobPersistenceUnavailable
        self._database = database

    async def save(self, job: EditingJob) -> None:
        """Insert one job, leaving any existing row untouched.

        There is no lookup before the insert -- not for the identifier, not for
        the timeline revision, and not for an existing queued render. That would
        let two callers both find nothing and both proceed, which is the same
        defect one level down. The primary key, the composite foreign key and
        the partial unique index are what refuse the second one, and they refuse
        it whoever is racing.

        The row is built *before* the `try`, for the same reason `_row` hydrates
        after its own: building it is not database work, and a catch-all that
        covered it would report a broken serialiser as an unavailable database
        -- telling the caller to retry something no retry can fix.
        """
        if not isinstance(job, EditingJob):
            raise EditingJobDataRejected
        values = _column_values(job)
        try:
            async with self._database.session() as session:
                await session.execute(insert(editing_jobs).values(**values))
        except IntegrityError as error:
            _refuse_integrity_violation(error)
        except _CONNECTION_FAILURES:
            raise EditingJobPersistenceUnavailable from None
        except Exception:
            # Authentication and authorisation failures are neither of the
            # above. Measured on asyncpg 0.31.0:
            #
            #   InvalidPasswordError -> InvalidAuthorizationSpecificationError
            #     -> PostgresError -> PostgresMessage -> Exception
            #   InsufficientPrivilegeError -> SyntaxOrAccessError -> PostgresError -> ...
            #   InvalidCatalogNameError -> PostgresError -> ...
            #   TooManyConnectionsError -> InsufficientResourcesError -> PostgresError -> ...
            #
            # Only the third sits directly under `PostgresError`; the others
            # arrive through an intermediate class, which is why matching on any
            # single named base would miss some of them. None of the four has
            # `OSError` or `SQLAlchemyError` anywhere on its MRO, and their
            # messages name the role and the database, so without this tail they
            # reach the caller verbatim. The same tail guards every method here.
            raise EditingJobPersistenceUnavailable from None

    async def update(self, previous: EditingJob, changed: EditingJob) -> None:
        """Move a job on, if its row is still the exact version `previous` is.

        This is a compare-and-set, and it takes two arguments because that is
        what a compare-and-set needs: `previous` is the job as it was read, and
        the statement only touches a row that still looks like it. A caller that
        cannot produce the version it read has no business writing.

        **An earlier version of this method compared `updated_at` with `<` and
        that was wrong.** The mistake is worth recording because it looked like
        an optimistic-concurrency check and was not one: a live caller's new
        timestamp is `now()`, which is always later than whatever is stored, so
        the predicate passed for *every* caller. What it did catch was replays
        and clocks running backwards -- and neither is worthless, so both are
        accounted for below rather than dropped: replays are covered by the
        comparison itself (the version a replay names stops existing once the
        first write lands), and a backwards timestamp is refused by the guard,
        because nothing in the statement compares the *incoming* timestamp with
        anything. Paired with a check that the row's status was a legal source
        for the new one, the old predicate still let this through -- reproduced
        on a real database:

        1. the row is `QUEUED`; a scheduler reads it;
        2. another instance dispatches the job, so the row becomes `RUNNING`;
        3. the scheduler, still holding the `QUEUED` snapshot, calls
           `fail(INVALID_TIMELINE)`. `QUEUED -> FAILED` is a legal edge, so the
           domain builds it; `RUNNING` is in `pred(FAILED)`, so the source check
           passed; the timestamp was later, so that check passed too. **It
           landed.** The worker then finished and was refused as stale, leaving
           an mp4 on disk, a row saying `failed`, and a NULL artifact.

        A source-status check cannot close that: it asks whether the transition
        is legal from where the row is, and "stale but still legal" is exactly
        the case it says yes to. Every target with two or more possible
        predecessors had the same opening. Comparing the version instead of the
        direction closes all of them at once, because `previous`'s `status` and
        `updated_at` together are the version.

        Legality is checked in Python, before the statement, and answers
        `EditingJobDataRejected`. It belongs there: an inconsistent
        `(previous, changed)` pair is a caller that built one from something
        other than the other, which no reload can fix -- so `EditingJobStale`
        would be the wrong instruction. Nothing is lost by moving it out of SQL,
        because the CAS pins the row's status to `previous`'s anyway.

        `rowcount == 0` then has two meanings, and the follow-up read is a
        best-effort attempt to say which. It is **not** the second half of the
        guard: the comparison was applied in full by the UPDATE, in one
        statement, before this read runs at all. Another connection that deletes
        the row and commits in between makes it invisible here, same transaction
        or not -- measured, `transaction_isolation` is `read committed`, so every
        statement takes a fresh snapshot. Both answers are safe inside that
        window: a row that really was deleted is `EditingJobNotFound`, a row
        still present is `EditingJobStale`, and both tell the caller to stop and
        reload. Only the label on a concurrently deleted row can come out wrong.
        Collapsing the two would not be safe -- it would tell a caller a running
        job does not exist.

        The `IntegrityError` clause is kept although **no row can reach it
        today**: this writes only `_MUTABLE_COLUMNS`, and none of those four
        carries a constraint an update could break. The composite foreign key
        covers columns this statement does not touch, and the partial index only
        indexes queued rows, which no update can produce (nothing transitions
        *to* queued). It stays because the alternative is the dangerous failure
        mode -- a constraint violation reported as an unavailable database, which
        invites a retry that can never succeed -- and because that reachability
        argument is one column classification away from changing.
        """
        if (
            not isinstance(previous, EditingJob)
            or not isinstance(changed, EditingJob)
            or previous.job_id != changed.job_id
            or not EditingJobStateMachine.can_transition(previous.status, changed.status)
            # `<`, deliberately: the domain permits a transition whose timestamp
            # equals its predecessor's, and the comparison's own equality case is
            # what makes the version a pair rather than just an instant.
            or changed.updated_at < previous.updated_at
        ):
            raise EditingJobDataRejected
        values = _mutable_values(changed)
        statement = (
            update(editing_jobs)
            .where(
                editing_jobs.c.job_id == changed.job_id.uuid,
                editing_jobs.c.status == previous.status.value,
                editing_jobs.c.updated_at == previous.updated_at,
            )
            .values(**values)
        )
        try:
            async with self._database.session() as session:
                # `AsyncSession.execute` is declared as returning `Result`, and
                # `rowcount` lives on the `CursorResult` a DML statement really
                # hands back. The cast records that rather than reaching through
                # an `Any`, so a SQLAlchemy release that changes it fails here.
                result = cast("CursorResult[Any]", await session.execute(statement))
                matched = result.rowcount != 0
                stored = (
                    None
                    if matched
                    else (
                        await session.execute(
                            select(editing_jobs).where(editing_jobs.c.job_id == changed.job_id.uuid)
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
        except IntegrityError as error:
            _refuse_integrity_violation(error)
        except _CONNECTION_FAILURES:
            raise EditingJobPersistenceUnavailable from None
        except Exception:
            raise EditingJobPersistenceUnavailable from None
        if matched:
            return
        if stored is None:
            raise EditingJobNotFound
        raise EditingJobStale

    async def get(self, job_id: EditingJobId) -> EditingJob:
        if not isinstance(job_id, EditingJobId):
            raise EditingJobDataRejected
        row = await self._row(job_id)
        if row is None:
            raise EditingJobNotFound
        return _hydrate(row)

    async def _row(self, job_id: EditingJobId) -> RowMapping | None:
        """Read at most one row, with hydration deliberately left to the caller.

        Hydrating in here would put `EditingJob.__post_init__` inside the `try`,
        where the catch-all tail would swallow a domain rejection and report it
        as an unavailable database -- turning "this stored row is broken" into
        "try again later", which is both wrong and unfixable by retrying.
        """
        try:
            async with self._database.session() as session:
                return (
                    (
                        await session.execute(
                            select(editing_jobs).where(editing_jobs.c.job_id == job_id.uuid)
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
        except _CONNECTION_FAILURES:
            raise EditingJobPersistenceUnavailable from None
        except Exception:
            raise EditingJobPersistenceUnavailable from None


__all__ = ["SqlAlchemyEditingJobRepository"]
