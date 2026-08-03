"""COV-02: the reconciliation transition the HTTP layer cannot reach past its schema.

`EditingJobService.reconcile` re-checks everything the request model already
constrains. That looks redundant from the outside and is not: the local
scheduler calls this service directly, without a Pydantic model in front of it,
so these are the only checks standing between a malformed transition and the
repository. None of them can be reached through the API, which is why they are
exercised here against the service itself.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Any, cast

import pytest

from automation_tool.control_plane.application.editing_jobs import (
    EditingJobNotFound,
    EditingJobService,
    EditingJobStale,
    EditingJobTransitionConflict,
    InvalidEditingJobQuery,
)
from automation_tool.control_plane.domain import (
    EditingJob,
    EditingJobFailureCode,
    EditingJobId,
    EditingJobStatus,
    EditingProjectId,
    InstallationId,
    Timeline,
    TimelineId,
)

NOW = datetime(2026, 7, 30, 10, 11, 12, 123_456, tzinfo=UTC)
LATER = NOW + timedelta(seconds=30)
INSTALLATION_ID = InstallationId.parse("00000000-0000-4000-8000-000000000001")
PROJECT_ID = EditingProjectId.parse("00000000-0000-4000-8000-000000000010")
TIMELINE_ID = TimelineId.parse("00000000-0000-4000-8000-000000000020")
JOB_ID = "00000000-0000-4000-8000-000000000031"
ARTIFACT_ID = "00000000-0000-4000-8000-000000000041"


class _Repository:
    def __init__(self, job: EditingJob) -> None:
        self.jobs: dict[EditingJobId, tuple[InstallationId, EditingJob]] = {
            job.job_id: (INSTALLATION_ID, job)
        }
        self.updates: list[tuple[EditingJob, EditingJob]] = []

    async def save(self, job: EditingJob, installation_id: InstallationId) -> None:
        self.jobs[job.job_id] = (installation_id, job)

    async def get(self, job_id: EditingJobId, installation_id: InstallationId) -> EditingJob:
        stored = self.jobs.get(job_id)
        if stored is None or stored[0] != installation_id:
            raise EditingJobNotFound
        return stored[1]

    async def list_page_by_project(self, **_options: object) -> tuple[EditingJob, ...]:
        return ()

    async def update(self, previous: EditingJob, changed: EditingJob) -> None:
        self.updates.append((previous, changed))
        self.jobs[previous.job_id] = (INSTALLATION_ID, changed)


class _Timelines:
    async def latest_revision(self, **_options: object) -> Timeline | None:
        return None


def _job(
    *,
    status: EditingJobStatus = EditingJobStatus.QUEUED,
    updated_at: datetime = NOW,
) -> EditingJob:
    return EditingJob(
        job_id=EditingJobId.parse(JOB_ID),
        project_id=PROJECT_ID,
        timeline_id=TIMELINE_ID,
        timeline_revision=3,
        status=status,
        failure_code=None,
        output_artifact_id=None,
        created_at=NOW,
        updated_at=updated_at,
    )


def _service(job: EditingJob) -> tuple[EditingJobService, _Repository]:
    repository = _Repository(job)
    return (
        EditingJobService(
            repository=cast(Any, repository),
            timelines=cast(Any, _Timelines()),
            clock=lambda: LATER,
        ),
        repository,
    )


async def _reconcile(service: EditingJobService, **overrides: object) -> EditingJob:
    arguments: dict[str, object] = {
        "installation_id": INSTALLATION_ID,
        "job_id": JOB_ID,
        "expected_updated_at": NOW,
        "status": EditingJobStatus.RUNNING,
        "failure_code": None,
        "output_artifact_id": None,
    }
    arguments.update(overrides)
    return await service.reconcile(**arguments)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_a_transition_request_it_cannot_trust_never_reaches_the_repository() -> None:
    service, repository = _service(_job())

    cases: list[tuple[str, dict[str, object]]] = [
        ("an installation that is not one", {"installation_id": str(INSTALLATION_ID)}),
        ("a timestamp that is not one", {"expected_updated_at": "2026-07-30T10:11:12Z"}),
        ("a timestamp with no zone", {"expected_updated_at": NOW.replace(tzinfo=None)}),
        (
            "a timestamp in another zone",
            {"expected_updated_at": NOW.astimezone(timezone(timedelta(hours=8)))},
        ),
        ("a status from outside the closed set", {"status": "running"}),
    ]
    for label, overrides in cases:
        with pytest.raises(InvalidEditingJobQuery):
            await _reconcile(service, **overrides)
        assert label

    assert repository.updates == [], "nothing may be written for a refused transition"


@pytest.mark.asyncio
async def test_a_transition_built_on_a_stale_snapshot_is_refused() -> None:
    """Compare-and-set: the caller must have seen the row it is replacing."""
    service, repository = _service(_job(updated_at=LATER))

    with pytest.raises(EditingJobStale):
        await _reconcile(service)

    assert repository.updates == []


@pytest.mark.asyncio
async def test_each_status_carries_exactly_the_fields_that_belong_to_it() -> None:
    """A terminal state with the wrong fields would record an unreadable outcome."""
    service, repository = _service(_job())

    cases: list[tuple[str, dict[str, object]]] = [
        (
            "a running job with a failure code",
            {"failure_code": EditingJobFailureCode.RENDER_FAILED},
        ),
        ("a running job with an artifact", {"output_artifact_id": ARTIFACT_ID}),
        (
            "a success with a failure code",
            {
                "status": EditingJobStatus.SUCCEEDED,
                "failure_code": EditingJobFailureCode.RENDER_FAILED,
                "output_artifact_id": ARTIFACT_ID,
            },
        ),
        ("a success with no artifact", {"status": EditingJobStatus.SUCCEEDED}),
        ("a failure with no reason", {"status": EditingJobStatus.FAILED}),
        (
            "a failure that also produced an artifact",
            {
                "status": EditingJobStatus.FAILED,
                "failure_code": EditingJobFailureCode.RENDER_FAILED,
                "output_artifact_id": ARTIFACT_ID,
            },
        ),
        ("a transition back to queued", {"status": EditingJobStatus.QUEUED}),
    ]
    for label, overrides in cases:
        with pytest.raises(InvalidEditingJobQuery):
            await _reconcile(service, **overrides)
        assert label

    assert repository.updates == []


@pytest.mark.asyncio
async def test_an_artifact_identifier_that_is_not_one_is_a_query_problem() -> None:
    """Not a storage failure and not a conflict: the caller sent something unusable."""
    service, repository = _service(_job(status=EditingJobStatus.RUNNING))

    with pytest.raises(InvalidEditingJobQuery):
        await _reconcile(
            service,
            status=EditingJobStatus.SUCCEEDED,
            output_artifact_id="not-an-artifact-id",
        )

    assert repository.updates == []


@pytest.mark.asyncio
async def test_a_transition_the_job_cannot_make_is_a_conflict_not_a_rejection() -> None:
    """Well-formed but out of order -- the caller's next step differs, so the code does."""
    service, repository = _service(_job(status=EditingJobStatus.RUNNING))

    with pytest.raises(EditingJobTransitionConflict):
        await _reconcile(service)

    assert repository.updates == []


@pytest.mark.asyncio
async def test_an_accepted_transition_writes_the_pair_the_repository_compares() -> None:
    service, repository = _service(_job())

    changed = await _reconcile(service)

    assert changed.status is EditingJobStatus.RUNNING
    assert changed.updated_at == LATER
    previous, stored = repository.updates[0]
    assert previous.status is EditingJobStatus.QUEUED
    assert stored == changed


@pytest.mark.asyncio
async def test_a_failure_is_recorded_with_its_reason_and_no_artifact() -> None:
    service, repository = _service(_job(status=EditingJobStatus.RUNNING))

    changed = await _reconcile(
        service,
        status=EditingJobStatus.FAILED,
        failure_code=EditingJobFailureCode.RENDER_FAILED,
    )

    assert changed.status is EditingJobStatus.FAILED
    assert changed.failure_code is EditingJobFailureCode.RENDER_FAILED
    assert changed.output_artifact_id is None
    assert repository.updates[0][1] == changed
