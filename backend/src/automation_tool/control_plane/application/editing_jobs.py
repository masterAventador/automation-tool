"""The editing job persistence boundary's failure vocabulary.

Seven outcomes, because a caller has seven different things to do about them,
and this is the first table in LE-05 whose rows change after they are written --
which is where the extra ones come from.

Three say a write was refused and name which rule refused it. A job identifier
that is already registered means this job exists. A revision that already has a
queued render means somebody else asked for the same thing first -- a different
resource and a different message, even though PostgreSQL reports both as the
same code. A timeline revision that is not stored means the job names something
that is not there, which is 404 about *another* resource.

Two say an update did not land, and they are not the same instruction. A job
that is not stored will not appear on a reload. A stale one will: something has
overtaken the caller's snapshot, and reloading gives it a next move.

The last two are the pair every repository here has: an unavailable database is
worth retrying, and a rejected argument is a bug upstairs.

Every message is a fixed string and no constructor takes an argument, so nothing
reaching a log through one of these can carry a connection string, an identifier
or a private path -- and `raise ... ("detail")` is a `TypeError` at the call
site rather than a leak. That matters more here than it looks: PostgreSQL's
DETAIL line on all three integrity failures quotes the offending key values
verbatim, and for the composite foreign key that is three of them at once, so
the tempting thing to attach is exactly the thing that must not travel.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from automation_tool.control_plane.domain import (
    EditingJob,
    EditingJobId,
    EditingJobStatus,
    EditingProjectId,
    InstallationId,
    InvalidResourceId,
    Timeline,
)

_CURSOR_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,256}$")


class _EditingJobPersistenceFailure(RuntimeError):
    message = "Editing job persistence failed"

    def __init__(self) -> None:
        super().__init__(self.message)


class EditingJobAlreadyRegistered(_EditingJobPersistenceFailure):
    """This `job_id` is taken, and `save` creates rather than merges."""

    message = "Editing job is already registered"


class EditingJobRevisionAlreadyQueued(_EditingJobPersistenceFailure):
    """This timeline revision already has a render waiting to start.

    Deliberately separate from `EditingJobAlreadyRegistered`, even though
    PostgreSQL reports both as SQLSTATE `23505`: the caller's mistake is a
    different one. A duplicate identifier means "you have used this name
    already"; this one means "the work you are asking for is already queued",
    and the useful next move is to wait for or look up the existing render
    rather than to pick a new identifier.

    Only *queued* rows are exclusive. A revision that has been rendered, failed
    or cancelled can be queued again, which is what makes a retry possible at
    all -- see the index's predicate in `schema.py`.
    """

    message = "Editing job revision already has a queued render"


class EditingJobTimelineRevisionMissing(_EditingJobPersistenceFailure):
    """No stored timeline revision matches this job's three reference columns.

    One answer covers two rules, because one composite foreign key enforces
    both and PostgreSQL cannot say which half failed: the revision may not exist
    at all, or it may exist under a *different* project than the one the job
    claims. Splitting the answer would mean guessing, and the honest statement
    -- "there is no revision of that timeline, under that project, with that
    number" -- is true either way and points at the same fix.

    A caller error rather than a corrupt row, and no retry improves on it. It
    can only be caught here: no domain object holds a reference to another
    aggregate, so `EditingJob` cannot ask which project a timeline belongs to,
    and an application-layer check on that is one two concurrent callers both
    pass.
    """

    message = "Editing job names a timeline revision that is not stored"


class EditingJobNotFound(_EditingJobPersistenceFailure):
    message = "Editing job was not found"


class EditingJobStale(_EditingJobPersistenceFailure):
    """The stored row is no longer the version this update was computed from.

    `update` is a compare-and-set: it names the `status` and `updated_at` the
    caller read and touches only a row that still carries both. This is the
    answer when the row has moved on -- someone else wrote it, or the caller is
    replaying a write that already landed. The stored row is left untouched.

    Note what this is *not*. It does not mean the requested transition was
    illegal: an inconsistent `(previous, changed)` pair is
    `EditingJobDataRejected`, because no reload can fix it and this exception's
    whole instruction is "read it again". And it is not a promise that the
    transition was legal from where the row actually is -- the caller does not
    get to know what the row became, only that it is not what they read.

    **For the layers above:** this is 409 rather than 404, and it must not be
    collapsed into `EditingJobNotFound`. A caller told "not found" about a job
    that exists will stop asking about a render that is still running, and a
    reconciliation pass told the same thing has no way to learn that the worker
    beat it to the finish.
    """

    message = "Editing job was modified by someone else"


class EditingJobDataRejected(_EditingJobPersistenceFailure):
    """The argument, or the row it would have written, was refused.

    Covers both a caller handing over something that is not an `EditingJob` and
    a constraint violation this module has no specific answer for -- including
    a unique violation whose constraint this module does not recognise, where
    naming either specific rule would be a guess about which one broke. The two
    share what a caller must do: fix the input. Neither improves on a retry,
    which is what separates them from `EditingJobPersistenceUnavailable`.
    """

    message = "Editing job data is rejected"


class EditingJobPersistenceUnavailable(_EditingJobPersistenceFailure):
    message = "Editing job persistence is unavailable"


class InvalidEditingJobQuery(ValueError):
    def __init__(self) -> None:
        super().__init__("Editing job query is invalid")


@dataclass(frozen=True, slots=True)
class EditingJobListBoundary:
    updated_at: datetime
    job_id: EditingJobId


@dataclass(frozen=True, slots=True)
class EditingJobListPage:
    items: tuple[EditingJob, ...]
    next_cursor: str | None


class EditingJobRepository(Protocol):
    async def save(
        self,
        job: EditingJob,
        installation_id: InstallationId,
    ) -> None: ...

    async def get(
        self,
        job_id: EditingJobId,
        installation_id: InstallationId,
    ) -> EditingJob: ...

    async def list_page_by_project(
        self,
        *,
        installation_id: InstallationId,
        project_id: EditingProjectId,
        before_updated_at: datetime | None,
        before_job_id: EditingJobId | None,
        limit: int,
    ) -> tuple[EditingJob, ...]: ...


class EditingTimelineLookup(Protocol):
    async def latest_revision(
        self,
        project_id: EditingProjectId,
        installation_id: InstallationId,
    ) -> Timeline | None: ...


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value


def _utc_text(value: datetime) -> str:
    normalized = value.astimezone(UTC)
    return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _encode_cursor(boundary: EditingJobListBoundary) -> str:
    payload = json.dumps(
        {
            "jobId": str(boundary.job_id),
            "updatedAt": _utc_text(boundary.updated_at),
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def _decode_cursor(value: object) -> EditingJobListBoundary:
    if not isinstance(value, str) or _CURSOR_PATTERN.fullmatch(value) is None:
        raise InvalidEditingJobQuery
    boundary: EditingJobListBoundary | None = None
    try:
        decoded = base64.urlsafe_b64decode(value + ("=" * (-len(value) % 4)))
        if base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii") != value:
            raise ValueError("noncanonical cursor")
        payload = json.loads(decoded.decode("ascii"), object_pairs_hook=_unique_object)
        if not isinstance(payload, dict) or set(payload) != {"jobId", "updatedAt"}:
            raise ValueError("invalid cursor object")
        job_id = EditingJobId.parse(payload["jobId"])
        updated_text = payload["updatedAt"]
        if not isinstance(updated_text, str) or not updated_text.endswith("Z"):
            raise ValueError("invalid cursor timestamp")
        updated_at = datetime.fromisoformat(updated_text.replace("Z", "+00:00"))
        boundary = EditingJobListBoundary(updated_at=updated_at, job_id=job_id)
        if _encode_cursor(boundary) != value:
            raise ValueError("noncanonical cursor payload")
    except (binascii.Error, InvalidResourceId, UnicodeDecodeError, ValueError):
        boundary = None
    if boundary is None:
        raise InvalidEditingJobQuery
    return boundary


def _parse_project_id(value: object) -> EditingProjectId:
    try:
        return EditingProjectId.parse(value)
    except InvalidResourceId:
        raise EditingJobNotFound from None


def _parse_job_id(value: object) -> EditingJobId:
    try:
        return EditingJobId.parse(value)
    except InvalidResourceId:
        raise EditingJobNotFound from None


class EditingJobService:
    def __init__(
        self,
        *,
        repository: EditingJobRepository,
        timelines: EditingTimelineLookup,
        clock: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._timelines = timelines
        self._clock = clock

    async def submit(
        self,
        *,
        installation_id: InstallationId,
        project_id: str,
    ) -> EditingJob:
        if not isinstance(installation_id, InstallationId):
            raise InvalidEditingJobQuery
        parsed_project_id = _parse_project_id(project_id)
        current = await self._timelines.latest_revision(
            parsed_project_id,
            installation_id,
        )
        if current is None:
            raise EditingJobTimelineRevisionMissing
        now = self._clock()
        job = EditingJob(
            job_id=EditingJobId.new(),
            project_id=parsed_project_id,
            timeline_id=current.timeline_id,
            timeline_revision=current.revision,
            status=EditingJobStatus.QUEUED,
            failure_code=None,
            output_artifact_id=None,
            created_at=now,
            updated_at=now,
        )
        await self._repository.save(job, installation_id)
        return job

    async def get(
        self,
        *,
        installation_id: InstallationId,
        job_id: str,
    ) -> EditingJob:
        if not isinstance(installation_id, InstallationId):
            raise InvalidEditingJobQuery
        return await self._repository.get(
            _parse_job_id(job_id),
            installation_id,
        )

    async def list(
        self,
        *,
        installation_id: InstallationId,
        project_id: str,
        cursor: str | None,
        limit: int,
    ) -> EditingJobListPage:
        if (
            not isinstance(installation_id, InstallationId)
            or type(limit) is not int
            or not 1 <= limit <= 100
        ):
            raise InvalidEditingJobQuery
        parsed_project_id = _parse_project_id(project_id)
        boundary = None if cursor is None else _decode_cursor(cursor)
        jobs = await self._repository.list_page_by_project(
            installation_id=installation_id,
            project_id=parsed_project_id,
            before_updated_at=None if boundary is None else boundary.updated_at,
            before_job_id=None if boundary is None else boundary.job_id,
            limit=limit + 1,
        )
        items = jobs[:limit]
        next_cursor = (
            _encode_cursor(
                EditingJobListBoundary(
                    updated_at=items[-1].updated_at,
                    job_id=items[-1].job_id,
                )
            )
            if len(jobs) > limit
            else None
        )
        return EditingJobListPage(items=items, next_cursor=next_cursor)


__all__ = [
    "EditingJobAlreadyRegistered",
    "EditingJobDataRejected",
    "EditingJobListBoundary",
    "EditingJobListPage",
    "EditingJobNotFound",
    "EditingJobPersistenceUnavailable",
    "EditingJobRepository",
    "EditingJobRevisionAlreadyQueued",
    "EditingJobService",
    "EditingJobStale",
    "EditingJobTimelineRevisionMissing",
    "EditingTimelineLookup",
    "InvalidEditingJobQuery",
]
