"""LE-06 T4: Installation-scoped, write-once timeline revisions over HTTP."""

from __future__ import annotations

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
from automation_tool.control_plane.application.timelines import (
    InvalidTimelineQuery,
    TimelineNotFound,
    TimelineProjectMissing,
    TimelineRepository,
    TimelineRevisionAlreadyStored,
    TimelineRevisionConflict,
    TimelineService,
)
from automation_tool.control_plane.domain import (
    EditingProjectId,
    InstallationId,
    InvalidTimelineModel,
    Timeline,
    TimelineId,
)

NOW = datetime(2026, 7, 30, 8, 9, 10, 123_456, tzinfo=UTC)
INSTALLATION_ID = InstallationId.new()
PROJECT_ID = EditingProjectId.new()
MATERIAL_ID = "00000000-0000-4000-8000-000000000007"

VALID_DRAFT: dict[str, object] = {
    "durationMs": 1_000,
    "tracks": [
        {
            "trackId": "visual",
            "kind": "visual",
            "clips": [
                {
                    "clipId": "visual-one",
                    "startMs": 0,
                    "durationMs": 1_000,
                    "sourceMaterialId": MATERIAL_ID,
                    "sourceInMs": 0,
                    "sourceOutMs": 1_000,
                    "text": None,
                    "gainDb": None,
                    "transitionIn": None,
                }
            ],
        }
    ],
}


class MemoryTimelineRepository(TimelineRepository):
    def __init__(self) -> None:
        self.project_owners: dict[EditingProjectId, InstallationId] = {PROJECT_ID: INSTALLATION_ID}
        self.revisions: dict[EditingProjectId, list[Timeline]] = {}
        self.conflict_with_revision: int | None = None
        self.conflict_without_current = False

    async def save(
        self,
        timeline: Timeline,
        installation_id: InstallationId,
    ) -> None:
        if self.project_owners.get(timeline.project_id) != installation_id:
            raise TimelineProjectMissing
        stored = self.revisions.setdefault(timeline.project_id, [])
        if self.conflict_without_current:
            self.revisions.pop(timeline.project_id)
            raise TimelineRevisionAlreadyStored
        if self.conflict_with_revision is not None:
            conflict_revision = self.conflict_with_revision
            self.conflict_with_revision = None
            current_id = stored[-1].timeline_id
            stored.append(
                Timeline(
                    timeline_id=current_id,
                    project_id=timeline.project_id,
                    revision=conflict_revision,
                    duration_ms=timeline.duration_ms,
                    tracks=timeline.tracks,
                    created_at=NOW,
                )
            )
            raise TimelineRevisionAlreadyStored
        if stored and stored[-1].timeline_id != timeline.timeline_id:
            raise TimelineRevisionAlreadyStored
        if any(item.revision == timeline.revision for item in stored):
            raise TimelineRevisionAlreadyStored
        stored.append(timeline)

    async def get(
        self,
        timeline_id: TimelineId,
        revision: int,
        installation_id: InstallationId,
    ) -> Timeline:
        for project_id, stored in self.revisions.items():
            if self.project_owners.get(project_id) != installation_id:
                continue
            for timeline in stored:
                if timeline.timeline_id == timeline_id and timeline.revision == revision:
                    return timeline
        raise TimelineNotFound

    async def latest_revision(
        self,
        project_id: EditingProjectId,
        installation_id: InstallationId,
    ) -> Timeline | None:
        if self.project_owners.get(project_id) != installation_id:
            return None
        stored = self.revisions.get(project_id, [])
        return max(stored, key=lambda timeline: timeline.revision) if stored else None


def timeline_client(
    repository: MemoryTimelineRepository | None = None,
) -> tuple[TestClient, MemoryTimelineRepository]:
    resolved = repository or MemoryTimelineRepository()
    service = TimelineService(repository=resolved, clock=lambda: NOW)
    app = create_app(database=None, timeline_service=service)
    app.dependency_overrides[require_current_installation_access] = lambda: INSTALLATION_ID
    return TestClient(app), resolved


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


def test_openapi_exposes_latest_get_and_next_revision_put_only() -> None:
    schema = create_app(database=None).openapi()
    timeline = schema["paths"]["/api/v1/editing-projects/{project_id}/timeline"]

    assert set(timeline) == {"get", "put"}
    assert timeline["get"]["operationId"] == "getEditingProjectTimeline"
    assert timeline["put"]["operationId"] == "saveEditingProjectTimeline"
    assert timeline["get"]["security"] == [{"AppSession": []}]
    assert timeline["put"]["security"] == [{"AppSession": []}]
    assert timeline["put"]["responses"]["409"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorEnvelope"
    }
    conflict_details = schema["components"]["schemas"]["PublicErrorDetails"]
    assert conflict_details["additionalProperties"] is False
    assert conflict_details["required"] == ["kind", "currentRevision"]
    assert conflict_details["properties"]["kind"]["const"] == "timeline_revision_conflict.v1"
    assert conflict_details["properties"]["currentRevision"]["minimum"] == 1


def test_route_reports_when_the_timeline_service_is_not_wired() -> None:
    app = create_app(database=None)
    app.dependency_overrides[require_current_installation_access] = lambda: INSTALLATION_ID

    response = TestClient(app).get(f"/api/v1/editing-projects/{PROJECT_ID}/timeline")

    error = assert_error(
        response,
        status_code=503,
        code="editing_timelines_unavailable",
    )
    assert error["retryable"] is True


def test_first_and_second_saves_use_one_server_owned_timeline_identity() -> None:
    client, repository = timeline_client()

    first = client.put(
        f"/api/v1/editing-projects/{PROJECT_ID}/timeline",
        json=VALID_DRAFT,
    )
    second = client.put(
        f"/api/v1/editing-projects/{PROJECT_ID}/timeline",
        json=VALID_DRAFT,
    )
    loaded = client.get(f"/api/v1/editing-projects/{PROJECT_ID}/timeline")

    assert first.status_code == 201
    assert second.status_code == 201
    assert loaded.status_code == 200
    assert first.headers["cache-control"] == "no-store"
    assert second.headers["cache-control"] == "no-store"
    assert loaded.headers["cache-control"] == "no-store"
    first_body = first.json()
    second_body = second.json()
    assert first_body["revision"] == 1
    assert second_body["revision"] == 2
    assert loaded.json() == second_body
    assert first_body["timelineId"] == second_body["timelineId"]
    assert first_body["projectId"] == second_body["projectId"] == str(PROJECT_ID)
    assert first_body["createdAt"] == second_body["createdAt"] == "2026-07-30T08:09:10.123456Z"
    assert {revision.timeline_id for revision in repository.revisions[PROJECT_ID]} == {
        TimelineId.parse(first_body["timelineId"])
    }
    assert first_body["durationMs"] == VALID_DRAFT["durationMs"]
    assert first_body["tracks"] == VALID_DRAFT["tracks"]


def test_transition_shape_round_trips_through_the_domain() -> None:
    client, _repository = timeline_client()
    draft = {
        "durationMs": 1_800,
        "tracks": [
            {
                "trackId": "visual",
                "kind": "visual",
                "clips": [
                    {
                        "clipId": "visual-one",
                        "startMs": 0,
                        "durationMs": 1_000,
                        "sourceMaterialId": MATERIAL_ID,
                        "sourceInMs": 0,
                        "sourceOutMs": 1_000,
                        "text": None,
                        "gainDb": None,
                        "transitionIn": None,
                    },
                    {
                        "clipId": "visual-two",
                        "startMs": 800,
                        "durationMs": 1_000,
                        "sourceMaterialId": MATERIAL_ID,
                        "sourceInMs": 1_000,
                        "sourceOutMs": 2_000,
                        "text": None,
                        "gainDb": None,
                        "transitionIn": {
                            "kind": "fade",
                            "durationMs": 200,
                        },
                    },
                ],
            }
        ],
    }

    response = client.put(
        f"/api/v1/editing-projects/{PROJECT_ID}/timeline",
        json=draft,
    )

    assert response.status_code == 201
    assert response.json()["tracks"] == draft["tracks"]


def test_revision_conflict_reloads_and_reports_the_newest_revision() -> None:
    client, repository = timeline_client()
    first = client.put(
        f"/api/v1/editing-projects/{PROJECT_ID}/timeline",
        json=VALID_DRAFT,
    )
    assert first.status_code == 201
    repository.conflict_with_revision = 4

    conflict = client.put(
        f"/api/v1/editing-projects/{PROJECT_ID}/timeline",
        json=VALID_DRAFT,
    )

    error = assert_error(
        conflict,
        status_code=409,
        code="timeline_revision_conflict",
    )
    assert error["message"] == "Timeline revision conflicts"
    assert error["retryable"] is False
    assert error["details"] == {
        "kind": "timeline_revision_conflict.v1",
        "currentRevision": 4,
    }


def test_conflict_without_a_committed_current_revision_fails_closed() -> None:
    client, repository = timeline_client()
    repository.conflict_without_current = True

    response = client.put(
        f"/api/v1/editing-projects/{PROJECT_ID}/timeline",
        json=VALID_DRAFT,
    )

    error = assert_error(
        response,
        status_code=503,
        code="timeline_persistence_unavailable",
    )
    assert error["retryable"] is True
    assert "details" not in error


def test_a_bad_stored_timeline_stays_an_internal_failure_on_put() -> None:
    class BadStoredTimelineRepository(MemoryTimelineRepository):
        async def latest_revision(
            self,
            project_id: EditingProjectId,
            installation_id: InstallationId,
        ) -> Timeline | None:
            raise InvalidTimelineModel

    repository = BadStoredTimelineRepository()
    service = TimelineService(repository=repository, clock=lambda: NOW)
    app = create_app(database=None, timeline_service=service)
    app.dependency_overrides[require_current_installation_access] = lambda: INSTALLATION_ID

    response = TestClient(app, raise_server_exceptions=False).put(
        f"/api/v1/editing-projects/{PROJECT_ID}/timeline",
        json=VALID_DRAFT,
    )

    error = assert_error(response, status_code=500, code="internal")
    assert set(error) == {"code", "message", "retryable", "requestId"}
    assert "Timeline model is invalid" not in response.text


def test_missing_foreign_and_empty_timeline_states_do_not_cross_owners() -> None:
    client, repository = timeline_client()

    empty = client.get(f"/api/v1/editing-projects/{PROJECT_ID}/timeline")
    assert "details" not in assert_error(
        empty,
        status_code=404,
        code="timeline_not_found",
    )

    created = client.put(
        f"/api/v1/editing-projects/{PROJECT_ID}/timeline",
        json=VALID_DRAFT,
    )
    assert created.status_code == 201
    foreign_owner = InstallationId.new()
    cast(FastAPI, client.app).dependency_overrides[require_current_installation_access] = lambda: (
        foreign_owner
    )

    hidden = client.get(f"/api/v1/editing-projects/{PROJECT_ID}/timeline")
    refused = client.put(
        f"/api/v1/editing-projects/{PROJECT_ID}/timeline",
        json=VALID_DRAFT,
    )
    assert "details" not in assert_error(
        hidden,
        status_code=404,
        code="timeline_not_found",
    )
    assert "details" not in assert_error(
        refused,
        status_code=409,
        code="timeline_project_missing",
    )
    assert len(repository.revisions[PROJECT_ID]) == 1


@pytest.mark.parametrize(
    "payload",
    [
        {**VALID_DRAFT, "timelineId": "private-client-id"},
        {**VALID_DRAFT, "projectId": str(PROJECT_ID)},
        {**VALID_DRAFT, "revision": 9},
        {**VALID_DRAFT, "createdAt": "2026-07-30T00:00:00Z"},
        {**VALID_DRAFT, "durationMs": True},
        {"duration_ms": 1_000, "tracks": VALID_DRAFT["tracks"]},
        {**VALID_DRAFT, "private": "must-not-cross-boundary"},
        {
            "durationMs": 1_000,
            "tracks": [
                {
                    "trackId": "captions",
                    "kind": "caption",
                    "clips": [
                        {
                            "clipId": "caption-one",
                            "startMs": 0,
                            "durationMs": 1_000,
                            "sourceMaterialId": None,
                            "sourceInMs": None,
                            "sourceOutMs": None,
                            "text": "没有画面轨道",
                            "gainDb": None,
                            "transitionIn": None,
                        }
                    ],
                }
            ],
        },
        {
            **VALID_DRAFT,
            "tracks": [
                {
                    "trackId": "visual",
                    "kind": "visual",
                    "clips": [
                        {
                            "clipId": "visual-one",
                            "startMs": 0,
                            "durationMs": 1_000,
                            "sourceMaterialId": "private-invalid-material",
                            "sourceInMs": 0,
                            "sourceOutMs": 1_000,
                            "text": None,
                            "gainDb": None,
                            "transitionIn": None,
                        }
                    ],
                }
            ],
        },
    ],
)
def test_timeline_draft_is_a_strict_payload_without_client_owned_identity(
    payload: dict[str, object],
) -> None:
    client, repository = timeline_client()

    response = client.put(
        f"/api/v1/editing-projects/{PROJECT_ID}/timeline",
        json=payload,
    )

    error = assert_error(response, status_code=422, code="validation")
    assert set(error) == {"code", "message", "retryable", "requestId"}
    assert not repository.revisions


def test_invalid_project_identifier_uses_the_fixed_validation_envelope() -> None:
    client, _repository = timeline_client()

    loaded = client.get("/api/v1/editing-projects/private-invalid-project/timeline")
    saved = client.put(
        "/api/v1/editing-projects/private-invalid-project/timeline",
        json=VALID_DRAFT,
    )

    for response in (loaded, saved):
        error = assert_error(response, status_code=422, code="validation")
        assert "private-invalid-project" not in response.text
        assert set(error) == {"code", "message", "retryable", "requestId"}


@pytest.mark.asyncio
async def test_service_refuses_a_non_installation_owner_before_repository_access() -> None:
    repository = MemoryTimelineRepository()
    service = TimelineService(repository=repository, clock=lambda: NOW)
    invalid_owner = cast(InstallationId, object())

    with pytest.raises(InvalidTimelineQuery):
        await service.get(
            project_id=str(PROJECT_ID),
            installation_id=invalid_owner,
        )
    with pytest.raises(InvalidTimelineQuery):
        await service.save(
            project_id=str(PROJECT_ID),
            installation_id=invalid_owner,
            duration_ms=1_000,
            tracks=(),
        )
    assert not repository.revisions


@pytest.mark.parametrize("current_revision", [0, True])
def test_revision_conflict_requires_a_strict_positive_revision(
    current_revision: object,
) -> None:
    with pytest.raises(ValueError):
        TimelineRevisionConflict(cast(int, current_revision))
