"""LE-06 T5: App-session editing-job submission, lookup, and total-order pages."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response

from automation_tool.control_plane import create_app
from automation_tool.control_plane.api.installation_access import (
    require_current_installation_access,
)
from automation_tool.control_plane.application.editing_jobs import (
    EditingJobAlreadyRegistered,
    EditingJobNotFound,
    EditingJobPersistenceUnavailable,
    EditingJobRevisionAlreadyQueued,
    EditingJobService,
    EditingJobStale,
    InvalidEditingJobQuery,
)
from automation_tool.control_plane.bootstrap.editing_jobs import (
    editing_job_service as build_editing_job_service,
)
from automation_tool.control_plane.domain import (
    EditingJob,
    EditingJobFailureCode,
    EditingJobId,
    EditingJobStatus,
    EditingProjectId,
    InstallationId,
    InvalidEditingJobModel,
    MaterialId,
    Timeline,
    TimelineClip,
    TimelineId,
    TimelineTrack,
    TimelineTrackKind,
)
from automation_tool.control_plane.infrastructure.database import (
    Database,
    SqlAlchemyEditingJobRepository,
    SqlAlchemyTimelineRepository,
)

NOW = datetime(2026, 7, 30, 10, 11, 12, 123_456, tzinfo=UTC)
INSTALLATION_ID = InstallationId.parse("00000000-0000-4000-8000-000000000001")
FOREIGN_INSTALLATION_ID = InstallationId.parse("00000000-0000-4000-8000-000000000002")
PROJECT_ID = EditingProjectId.parse("00000000-0000-4000-8000-000000000010")
TIMELINE_ID = TimelineId.parse("00000000-0000-4000-8000-000000000020")


class MemoryEditingJobRepository:
    def __init__(self) -> None:
        self.jobs: dict[EditingJobId, tuple[InstallationId, EditingJob]] = {}
        self.failure: Exception | None = None

    async def save(self, job: EditingJob, installation_id: InstallationId) -> None:
        if self.failure is not None:
            raise self.failure
        if job.job_id in self.jobs:
            raise EditingJobAlreadyRegistered
        if any(
            owner == installation_id
            and stored.timeline_id == job.timeline_id
            and stored.timeline_revision == job.timeline_revision
            and stored.status is EditingJobStatus.QUEUED
            for owner, stored in self.jobs.values()
        ):
            raise EditingJobRevisionAlreadyQueued
        self.jobs[job.job_id] = (installation_id, job)

    async def get(
        self,
        job_id: EditingJobId,
        installation_id: InstallationId,
    ) -> EditingJob:
        if self.failure is not None:
            raise self.failure
        try:
            owner, job = self.jobs[job_id]
        except KeyError:
            raise EditingJobNotFound from None
        if owner != installation_id:
            raise EditingJobNotFound
        return job

    async def list_page_by_project(
        self,
        *,
        installation_id: InstallationId,
        project_id: EditingProjectId,
        before_updated_at: datetime | None,
        before_job_id: EditingJobId | None,
        limit: int,
    ) -> tuple[EditingJob, ...]:
        if self.failure is not None:
            raise self.failure
        jobs = sorted(
            (
                job
                for owner, job in self.jobs.values()
                if owner == installation_id
                and job.project_id == project_id
                and (
                    before_updated_at is None
                    or (
                        before_job_id is not None
                        and (job.updated_at, job.job_id.uuid)
                        < (before_updated_at, before_job_id.uuid)
                    )
                )
            ),
            key=lambda job: (job.updated_at, job.job_id.uuid),
            reverse=True,
        )
        return tuple(jobs[:limit])

    async def update(
        self,
        previous: EditingJob,
        changed: EditingJob,
    ) -> None:
        if self.failure is not None:
            raise self.failure
        stored = self.jobs.get(previous.job_id)
        if stored is None or stored[1] != previous:
            raise EditingJobStale
        self.jobs[previous.job_id] = (stored[0], changed)


class MemoryTimelineLookup:
    def __init__(self) -> None:
        self.latest: dict[tuple[InstallationId, EditingProjectId], Timeline] = {}

    async def latest_revision(
        self,
        project_id: EditingProjectId,
        installation_id: InstallationId,
    ) -> Timeline | None:
        return self.latest.get((installation_id, project_id))


def timeline(
    *,
    project_id: EditingProjectId = PROJECT_ID,
    timeline_id: TimelineId = TIMELINE_ID,
    revision: int = 3,
) -> Timeline:
    return Timeline(
        timeline_id=timeline_id,
        project_id=project_id,
        revision=revision,
        duration_ms=1_000,
        tracks=(
            TimelineTrack(
                track_id="visual",
                kind=TimelineTrackKind.VISUAL,
                clips=(
                    TimelineClip(
                        clip_id="visual-one",
                        start_ms=0,
                        duration_ms=1_000,
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
        created_at=NOW,
    )


def job(
    *,
    identifier: str,
    project_id: EditingProjectId = PROJECT_ID,
    timeline_id: TimelineId = TIMELINE_ID,
    revision: int = 3,
    updated_at: datetime = NOW,
    status: EditingJobStatus = EditingJobStatus.QUEUED,
    failure_code: EditingJobFailureCode | None = None,
) -> EditingJob:
    return EditingJob(
        job_id=EditingJobId.parse(identifier),
        project_id=project_id,
        timeline_id=timeline_id,
        timeline_revision=revision,
        status=status,
        failure_code=failure_code,
        output_artifact_id=None,
        created_at=NOW,
        updated_at=updated_at,
    )


def job_client(
    repository: MemoryEditingJobRepository | None = None,
    timelines: MemoryTimelineLookup | None = None,
    *,
    raise_server_exceptions: bool = True,
) -> tuple[TestClient, MemoryEditingJobRepository, MemoryTimelineLookup]:
    resolved_repository = repository or MemoryEditingJobRepository()
    resolved_timelines = timelines or MemoryTimelineLookup()
    service = EditingJobService(
        repository=resolved_repository,
        timelines=resolved_timelines,
        clock=lambda: NOW,
    )
    app = create_app(database=None, editing_job_service=service)
    app.dependency_overrides[require_current_installation_access] = lambda: INSTALLATION_ID
    return (
        TestClient(app, raise_server_exceptions=raise_server_exceptions),
        resolved_repository,
        resolved_timelines,
    )


def expected_snapshot(value: EditingJob) -> dict[str, object]:
    return {
        "jobId": str(value.job_id),
        "projectId": str(value.project_id),
        "timelineId": str(value.timeline_id),
        "timelineRevision": value.timeline_revision,
        "status": value.status.value,
        "failureCode": None if value.failure_code is None else value.failure_code.value,
        "outputArtifactId": (
            None if value.output_artifact_id is None else str(value.output_artifact_id)
        ),
        "createdAt": "2026-07-30T10:11:12.123456Z",
        "updatedAt": "2026-07-30T10:11:12.123456Z",
    }


def opaque_cursor(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def assert_error(
    response: Response,
    *,
    status_code: int,
    code: str,
) -> dict[str, object]:
    assert response.status_code == status_code
    payload = response.json()
    assert set(payload) == {"error"}
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == code
    return cast("dict[str, object]", error)


def test_openapi_exposes_submit_project_list_job_detail_and_worker_reconciliation() -> None:
    schema = create_app(database=None).openapi()
    collection = schema["paths"]["/api/v1/editing-projects/{project_id}/jobs"]
    detail = schema["paths"]["/api/v1/editing-jobs/{job_id}"]

    assert set(collection) == {"get", "post"}
    assert set(detail) == {"get", "patch"}
    assert collection["post"]["operationId"] == "submitEditingJob"
    assert collection["get"]["operationId"] == "listEditingJobs"
    assert detail["get"]["operationId"] == "getEditingJob"
    assert detail["patch"]["operationId"] == "reconcileEditingJob"
    for operation in (*collection.values(), *detail.values()):
        assert operation["security"] == [{"AppSession": []}]
        assert operation["responses"]["422"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorEnvelope"
        }
    assert "patch" not in collection
    assert "delete" not in collection
    submit_schema = schema["components"]["schemas"]["EditingJobSubmitRequest"]
    assert submit_schema["additionalProperties"] is False


def test_worker_reconciliation_moves_one_owned_job_through_running_to_succeeded() -> None:
    client, repository, _ = job_client()
    queued = job(identifier="00000000-0000-4000-8000-000000000031")
    repository.jobs[queued.job_id] = (INSTALLATION_ID, queued)
    artifact_id = "00000000-0000-4000-8000-000000000041"

    running = client.patch(
        f"/api/v1/editing-jobs/{queued.job_id}",
        json={
            "expectedUpdatedAt": "2026-07-30T10:11:12.123456Z",
            "status": "running",
            "failureCode": None,
            "outputArtifactId": None,
        },
    )
    succeeded = client.patch(
        f"/api/v1/editing-jobs/{queued.job_id}",
        json={
            "expectedUpdatedAt": running.json()["updatedAt"],
            "status": "succeeded",
            "failureCode": None,
            "outputArtifactId": artifact_id,
        },
    )

    assert running.status_code == 200
    assert running.json()["status"] == "running"
    assert succeeded.status_code == 200
    assert succeeded.headers["cache-control"] == "no-store"
    assert succeeded.json()["status"] == "succeeded"
    assert succeeded.json()["outputArtifactId"] == artifact_id
    assert repository.jobs[queued.job_id][1].status is EditingJobStatus.SUCCEEDED


@pytest.mark.parametrize(
    ("payload", "status_code", "code"),
    [
        (
            {
                "expectedUpdatedAt": "2026-07-30T10:11:12.123456Z",
                "status": "succeeded",
                "failureCode": None,
                "outputArtifactId": "00000000-0000-4000-8000-000000000041",
            },
            409,
            "editing_job_conflict",
        ),
        (
            {
                "expectedUpdatedAt": "2026-07-30T10:11:12.123456Z",
                "status": "failed",
                "failureCode": None,
                "outputArtifactId": None,
            },
            422,
            "validation",
        ),
    ],
)
def test_worker_reconciliation_rejects_illegal_or_incomplete_terminal_updates(
    payload: dict[str, object], status_code: int, code: str
) -> None:
    client, repository, _ = job_client()
    queued = job(identifier="00000000-0000-4000-8000-000000000032")
    repository.jobs[queued.job_id] = (INSTALLATION_ID, queued)

    response = client.patch(f"/api/v1/editing-jobs/{queued.job_id}", json=payload)

    assert_error(response, status_code=status_code, code=code)
    assert repository.jobs[queued.job_id][1] == queued


def test_empty_submission_uses_the_latest_revision_and_returns_the_domain_shape() -> None:
    client, repository, timelines = job_client()
    current = timeline()
    timelines.latest[(INSTALLATION_ID, PROJECT_ID)] = current

    submitted = client.post(f"/api/v1/editing-projects/{PROJECT_ID}/jobs", json={})

    assert submitted.status_code == 201
    assert submitted.headers["cache-control"] == "no-store"
    owner, stored = next(iter(repository.jobs.values()))
    assert owner == INSTALLATION_ID
    assert stored.project_id == current.project_id
    assert stored.timeline_id == current.timeline_id
    assert stored.timeline_revision == current.revision
    assert stored.status is EditingJobStatus.QUEUED
    assert submitted.json() == expected_snapshot(stored)
    assert set(submitted.json()) == {
        "jobId",
        "projectId",
        "timelineId",
        "timelineRevision",
        "status",
        "failureCode",
        "outputArtifactId",
        "createdAt",
        "updatedAt",
    }
    assert "editingJobId" not in submitted.text
    assert "inputArtifactIds" not in submitted.text
    assert "outputArtifactIds" not in submitted.text

    loaded = client.get(f"/api/v1/editing-jobs/{stored.job_id}")
    listed = client.get(f"/api/v1/editing-projects/{PROJECT_ID}/jobs")
    assert loaded.status_code == 200
    assert loaded.headers["cache-control"] == "no-store"
    assert loaded.json() == submitted.json()
    assert listed.status_code == 200
    assert listed.headers["cache-control"] == "no-store"
    assert listed.json() == {"items": [submitted.json()], "nextCursor": None}


def test_equal_timestamp_jobs_page_by_the_identifier_tiebreaker() -> None:
    client, repository, _ = job_client()
    values = tuple(
        job(identifier=f"00000000-0000-4000-8000-{suffix:012x}", revision=suffix)
        for suffix in range(1, 5)
    )
    repository.jobs = {value.job_id: (INSTALLATION_ID, value) for value in values}

    first = client.get(f"/api/v1/editing-projects/{PROJECT_ID}/jobs", params={"limit": 2})
    assert first.status_code == 200
    assert [item["jobId"] for item in first.json()["items"]] == [
        str(values[3].job_id),
        str(values[2].job_id),
    ]
    cursor = first.json()["nextCursor"]
    assert isinstance(cursor, str) and cursor
    assert str(values[2].job_id) not in cursor

    second = client.get(
        f"/api/v1/editing-projects/{PROJECT_ID}/jobs",
        params={"limit": 2, "cursor": cursor},
    )
    assert second.status_code == 200
    assert [item["jobId"] for item in second.json()["items"]] == [
        str(values[1].job_id),
        str(values[0].job_id),
    ]
    assert second.json()["nextCursor"] is None


def test_detail_and_list_hide_jobs_owned_by_another_installation() -> None:
    client, repository, _ = job_client()
    owned = job(identifier="00000000-0000-4000-8000-000000000001", revision=1)
    foreign = job(identifier="00000000-0000-4000-8000-000000000002", revision=2)
    repository.jobs = {
        owned.job_id: (INSTALLATION_ID, owned),
        foreign.job_id: (FOREIGN_INSTALLATION_ID, foreign),
    }

    listed = client.get(f"/api/v1/editing-projects/{PROJECT_ID}/jobs")
    hidden = client.get(f"/api/v1/editing-jobs/{foreign.job_id}")

    assert listed.status_code == 200
    assert listed.json() == {
        "items": [expected_snapshot(owned)],
        "nextCursor": None,
    }
    assert_error(hidden, status_code=404, code="editing_job_not_found")
    assert str(foreign.job_id) not in hidden.text


def test_submission_is_strict_and_invalid_resource_ids_do_not_reach_storage() -> None:
    client, repository, timelines = job_client()
    timelines.latest[(INSTALLATION_ID, PROJECT_ID)] = timeline()

    extra = client.post(
        f"/api/v1/editing-projects/{PROJECT_ID}/jobs",
        json={"timelineRevision": 3},
    )
    missing_body = client.post(f"/api/v1/editing-projects/{PROJECT_ID}/jobs")
    bad_project = client.post("/api/v1/editing-projects/not-a-project/jobs", json={})
    bad_job = client.get("/api/v1/editing-jobs/not-a-job")

    assert_error(extra, status_code=422, code="validation")
    assert_error(missing_body, status_code=422, code="validation")
    assert_error(bad_project, status_code=404, code="editing_job_not_found")
    assert_error(bad_job, status_code=404, code="editing_job_not_found")
    assert repository.jobs == {}


@pytest.mark.parametrize(
    "params",
    (
        {"limit": "0"},
        {"limit": "101"},
        {"cursor": "not+base64"},
        {"cursor": opaque_cursor(b"{}")[:-1] + "1"},
        {"cursor": opaque_cursor(b"{}")},
        {
            "cursor": opaque_cursor(
                b'{"jobId":"00000000-0000-4000-8000-000000000001","updatedAt":3}'
            )
        },
        {
            "cursor": opaque_cursor(
                b'{"jobId":"00000000-0000-4000-8000-000000000001",'
                b'"updatedAt":"2026-07-30T10:11:12+00:00"}'
            )
        },
        {
            "cursor": opaque_cursor(
                b'{"jobId":"00000000-0000-4000-8000-000000000001",'
                b'"updatedAt":"2026-07-30T10:11:12Z"}'
            )
        },
        {
            "cursor": opaque_cursor(
                b'{"jobId":"00000000-0000-4000-8000-000000000001",'
                b'"updatedAt":"2026-07-30T10:11:12.123456Z","extra":true}'
            )
        },
        {
            "cursor": opaque_cursor(
                b'{"jobId":"00000000-0000-4000-8000-000000000001",'
                b'"jobId":"00000000-0000-4000-8000-000000000002",'
                b'"updatedAt":"2026-07-30T10:11:12.123456Z"}'
            )
        },
    ),
)
def test_invalid_page_arguments_fail_closed(params: dict[str, str]) -> None:
    client, _, _ = job_client()

    response = client.get(
        f"/api/v1/editing-projects/{PROJECT_ID}/jobs",
        params=params,
    )

    assert_error(response, status_code=422, code="validation")


def test_missing_timeline_and_duplicate_queue_have_distinct_conflicts() -> None:
    client, repository, timelines = job_client()

    missing = client.post(f"/api/v1/editing-projects/{PROJECT_ID}/jobs", json={})
    assert_error(
        missing,
        status_code=409,
        code="editing_job_timeline_revision_missing",
    )

    timelines.latest[(INSTALLATION_ID, PROJECT_ID)] = timeline()
    first = client.post(f"/api/v1/editing-projects/{PROJECT_ID}/jobs", json={})
    assert first.status_code == 201
    before = dict(repository.jobs)

    duplicate = client.post(f"/api/v1/editing-projects/{PROJECT_ID}/jobs", json={})
    assert_error(
        duplicate,
        status_code=409,
        code="editing_job_revision_already_queued",
    )
    assert repository.jobs == before


def test_persistence_unavailable_is_retryable_and_service_absence_is_explicit() -> None:
    repository = MemoryEditingJobRepository()
    repository.failure = EditingJobPersistenceUnavailable()
    client, _, _ = job_client(repository=repository)

    unavailable = client.get(f"/api/v1/editing-jobs/{EditingJobId.new()}")
    error = assert_error(
        unavailable,
        status_code=503,
        code="editing_job_persistence_unavailable",
    )
    assert error["retryable"] is True

    app = create_app(database=None)
    app.dependency_overrides[require_current_installation_access] = lambda: INSTALLATION_ID
    unwired = TestClient(app).get(f"/api/v1/editing-jobs/{EditingJobId.new()}")
    service_error = assert_error(
        unwired,
        status_code=503,
        code="editing_jobs_unavailable",
    )
    assert service_error["retryable"] is True


def test_a_bad_stored_job_stays_an_internal_failure_without_reflecting_data() -> None:
    class BadStoredJobRepository(MemoryEditingJobRepository):
        async def get(
            self,
            job_id: EditingJobId,
            installation_id: InstallationId,
        ) -> EditingJob:
            raise InvalidEditingJobModel

    client, _, _ = job_client(
        repository=BadStoredJobRepository(),
        raise_server_exceptions=False,
    )

    response = client.get(f"/api/v1/editing-jobs/{EditingJobId.new()}")

    error = assert_error(response, status_code=500, code="internal")
    assert set(error) == {"code", "message", "retryable", "requestId"}
    assert "Editing job model is invalid" not in response.text


def test_service_errors_keep_the_existing_public_codes() -> None:
    client, repository, timelines = job_client()
    timelines.latest[(INSTALLATION_ID, PROJECT_ID)] = timeline()
    repository.failure = EditingJobAlreadyRegistered()

    duplicate_id = client.post(f"/api/v1/editing-projects/{PROJECT_ID}/jobs", json={})

    assert_error(
        duplicate_id,
        status_code=409,
        code="editing_job_already_registered",
    )


def test_unknown_project_list_is_empty_but_invalid_project_id_is_not_a_cursor_error() -> None:
    client, _, _ = job_client()

    empty = client.get(f"/api/v1/editing-projects/{EditingProjectId.new()}/jobs")
    invalid = client.get("/api/v1/editing-projects/not-a-project/jobs")

    assert empty.status_code == 200
    assert empty.json() == {"items": [], "nextCursor": None}
    assert_error(invalid, status_code=404, code="editing_job_not_found")


def test_the_app_type_is_real_fastapi_for_dependency_override_coverage() -> None:
    client, _, _ = job_client()

    assert isinstance(client.app, FastAPI)


@pytest.mark.asyncio
async def test_service_refuses_invalid_internal_arguments_before_its_boundaries() -> None:
    repository = MemoryEditingJobRepository()
    timelines = MemoryTimelineLookup()
    service = EditingJobService(
        repository=repository,
        timelines=timelines,
        clock=lambda: NOW,
    )
    invalid_installation = cast(InstallationId, object())

    with pytest.raises(InvalidEditingJobQuery):
        await service.submit(
            installation_id=invalid_installation,
            project_id=str(PROJECT_ID),
        )
    with pytest.raises(InvalidEditingJobQuery):
        await service.get(
            installation_id=invalid_installation,
            job_id=str(EditingJobId.new()),
        )
    with pytest.raises(InvalidEditingJobQuery):
        await service.list(
            installation_id=INSTALLATION_ID,
            project_id=str(PROJECT_ID),
            cursor=None,
            limit=cast(int, True),
        )


def test_routes_translate_a_rejected_installation_boundary_to_validation() -> None:
    repository = MemoryEditingJobRepository()
    timelines = MemoryTimelineLookup()
    service = EditingJobService(
        repository=repository,
        timelines=timelines,
        clock=lambda: NOW,
    )
    app = create_app(database=None, editing_job_service=service)
    app.dependency_overrides[require_current_installation_access] = lambda: cast(
        InstallationId,
        object(),
    )
    client = TestClient(app)

    submitted = client.post(
        f"/api/v1/editing-projects/{PROJECT_ID}/jobs",
        json={},
    )
    loaded = client.get(f"/api/v1/editing-jobs/{EditingJobId.new()}")

    assert_error(submitted, status_code=422, code="validation")
    assert_error(loaded, status_code=422, code="validation")


@pytest.mark.asyncio
async def test_runtime_builder_uses_sql_repositories_and_an_utc_clock() -> None:
    database = Database.from_url(
        "postgresql+asyncpg://unused:unused@127.0.0.1:1/unused",
        connect_timeout_seconds=0.05,
    )
    try:
        service = build_editing_job_service(database)

        assert isinstance(service, EditingJobService)
        assert isinstance(service._repository, SqlAlchemyEditingJobRepository)
        assert isinstance(service._timelines, SqlAlchemyTimelineRepository)
        assert service._clock().tzinfo is UTC
    finally:
        await database.close()
