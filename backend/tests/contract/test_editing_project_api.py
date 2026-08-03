"""LE-06 T2: the editing-project REST surface and opaque total-order cursor."""

from __future__ import annotations

import base64
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from automation_tool.control_plane import create_app
from automation_tool.control_plane.api.installation_access import (
    require_current_installation_access,
)
from automation_tool.control_plane.application.editing_projects import (
    EditingProjectAlreadyRegistered,
    EditingProjectNotFound,
    EditingProjectPersistenceUnavailable,
    EditingProjectService,
    InvalidEditingProjectQuery,
)
from automation_tool.control_plane.domain import (
    CaptionStyle,
    EditingProject,
    EditingProjectId,
    InstallationId,
    OutputSpec,
)
from automation_tool.control_plane.infrastructure.database import Database

NOW = datetime(2026, 7, 30, 7, 8, 9, 123_456, tzinfo=UTC)
INSTALLATION_ID = InstallationId.new()
VALID_OUTPUT: dict[str, object] = {
    "width": 1080,
    "height": 1920,
    "fps": 30,
}
VALID_CAPTION_STYLE: dict[str, object] = {
    "fontKey": "source-han-sans",
    "fontPx": 64,
    "strokePx": 4,
    "lineSpacing": 1.25,
}
VALID_PAYLOAD: dict[str, object] = {
    "title": "夏日露营 第一集",
    "output": VALID_OUTPUT,
    "captionStyle": VALID_CAPTION_STYLE,
}


class MemoryEditingProjectRepository:
    def __init__(self) -> None:
        self.projects: dict[EditingProjectId, tuple[InstallationId, EditingProject]] = {}
        self.failure: Exception | None = None

    async def save(
        self,
        project: EditingProject,
        installation_id: InstallationId,
    ) -> None:
        if self.failure is not None:
            raise self.failure
        if project.project_id in self.projects:
            raise EditingProjectAlreadyRegistered
        self.projects[project.project_id] = (installation_id, project)

    async def get(
        self,
        project_id: EditingProjectId,
        installation_id: InstallationId,
    ) -> EditingProject:
        if self.failure is not None:
            raise self.failure
        try:
            owner, project = self.projects[project_id]
        except KeyError:
            raise EditingProjectNotFound from None
        if owner != installation_id:
            raise EditingProjectNotFound
        return project

    async def list_page(
        self,
        *,
        installation_id: InstallationId,
        before_created_at: datetime | None,
        before_project_id: EditingProjectId | None,
        limit: int,
    ) -> tuple[EditingProject, ...]:
        if self.failure is not None:
            raise self.failure
        projects = sorted(
            (
                project
                for owner, project in self.projects.values()
                if owner == installation_id
                and (
                    before_created_at is None
                    or (
                        before_project_id is not None
                        and (project.created_at, project.project_id.uuid)
                        < (before_created_at, before_project_id.uuid)
                    )
                )
            ),
            key=lambda project: (project.created_at, project.project_id.uuid),
            reverse=True,
        )
        return tuple(projects[:limit])


def project(
    *,
    identifier: str,
    title: str,
    created_at: datetime = NOW,
) -> EditingProject:
    return EditingProject(
        project_id=EditingProjectId.parse(identifier),
        title=title,
        output=OutputSpec(width=1080, height=1920, fps=30),
        caption_style=CaptionStyle(
            font_key="source-han-sans",
            font_px=64,
            stroke_px=4,
            line_spacing=1.25,
        ),
        created_at=created_at,
    )


def project_client(
    repository: MemoryEditingProjectRepository | None = None,
) -> tuple[TestClient, MemoryEditingProjectRepository]:
    resolved = repository or MemoryEditingProjectRepository()
    service = EditingProjectService(repository=resolved, clock=lambda: NOW)
    app = create_app(database=None, editing_project_service=service)
    app.dependency_overrides[require_current_installation_access] = lambda: INSTALLATION_ID
    return TestClient(app), resolved


def expected_snapshot(value: EditingProject) -> dict[str, object]:
    return {
        "projectId": str(value.project_id),
        "title": value.title,
        "output": {
            "width": value.output.width,
            "height": value.output.height,
            "fps": value.output.fps,
        },
        "captionStyle": {
            "fontKey": value.caption_style.font_key,
            "fontPx": value.caption_style.font_px,
            "strokePx": value.caption_style.stroke_px,
            "lineSpacing": value.caption_style.line_spacing,
        },
        "createdAt": "2026-07-30T07:08:09.123456Z",
    }


def opaque_cursor(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def test_openapi_exposes_only_create_list_and_detail_as_app_session_operations() -> None:
    schema = create_app(database=None).openapi()
    collection = schema["paths"]["/api/v1/editing-projects"]
    detail = schema["paths"]["/api/v1/editing-projects/{project_id}"]

    assert set(collection) == {"get", "post"}
    assert set(detail) == {"get"}
    assert collection["post"]["operationId"] == "createEditingProject"
    assert collection["get"]["operationId"] == "listEditingProjects"
    assert detail["get"]["operationId"] == "getEditingProject"
    for operation in (*collection.values(), detail["get"]):
        assert operation["security"] == [{"AppSession": []}]


def test_create_detail_and_list_return_the_current_domain_shape_only() -> None:
    client, repository = project_client()

    created = client.post("/api/v1/editing-projects", json=VALID_PAYLOAD)
    assert created.status_code == 201
    assert created.headers["cache-control"] == "no-store"
    owner, stored = next(iter(repository.projects.values()))
    assert owner == INSTALLATION_ID
    assert created.json() == expected_snapshot(stored)
    assert set(created.json()) == {
        "projectId",
        "title",
        "output",
        "captionStyle",
        "createdAt",
    }

    loaded = client.get(f"/api/v1/editing-projects/{stored.project_id}")
    assert loaded.status_code == 200
    assert loaded.headers["cache-control"] == "no-store"
    assert loaded.json() == created.json()

    listed = client.get("/api/v1/editing-projects")
    assert listed.status_code == 200
    assert listed.headers["cache-control"] == "no-store"
    assert listed.json() == {"items": [created.json()], "nextCursor": None}
    assert "sourceArtifactIds" not in listed.text
    assert "updatedAt" not in listed.text


def test_equal_timestamp_projects_page_by_the_identifier_tiebreaker() -> None:
    client, repository = project_client()
    values = tuple(
        project(
            identifier=f"00000000-0000-4000-8000-{suffix:012x}",
            title=f"同一时刻项目 {suffix}",
        )
        for suffix in range(1, 5)
    )
    repository.projects = {value.project_id: (INSTALLATION_ID, value) for value in values}

    first = client.get("/api/v1/editing-projects", params={"limit": 2})
    assert first.status_code == 200
    assert [item["projectId"] for item in first.json()["items"]] == [
        str(values[3].project_id),
        str(values[2].project_id),
    ]
    cursor = first.json()["nextCursor"]
    assert isinstance(cursor, str) and cursor
    assert str(values[2].project_id) not in cursor

    second = client.get(
        "/api/v1/editing-projects",
        params={"limit": 2, "cursor": cursor},
    )
    assert second.status_code == 200
    assert [item["projectId"] for item in second.json()["items"]] == [
        str(values[1].project_id),
        str(values[0].project_id),
    ]
    assert second.json()["nextCursor"] is None


def test_list_and_detail_hide_projects_owned_by_another_installation() -> None:
    client, repository = project_client()
    owned = project(
        identifier="00000000-0000-4000-8000-000000000001",
        title="当前设备项目",
    )
    foreign = project(
        identifier="00000000-0000-4000-8000-000000000002",
        title="其它设备私有项目",
    )
    repository.projects = {
        owned.project_id: (INSTALLATION_ID, owned),
        foreign.project_id: (InstallationId.new(), foreign),
    }

    listed = client.get("/api/v1/editing-projects")
    hidden = client.get(f"/api/v1/editing-projects/{foreign.project_id}")

    assert listed.status_code == 200
    assert listed.json() == {
        "items": [expected_snapshot(owned)],
        "nextCursor": None,
    }
    assert hidden.status_code == 404
    assert hidden.json()["error"]["code"] == "editing_project_not_found"
    assert foreign.title not in hidden.text


def test_invalid_body_cursor_and_limit_fail_closed_as_validation() -> None:
    client, _ = project_client()
    invalid_responses = (
        client.post(
            "/api/v1/editing-projects",
            json={**VALID_PAYLOAD, "privatePath": "/Users/private/movie.mp4"},
        ),
        client.post(
            "/api/v1/editing-projects",
            json={
                **VALID_PAYLOAD,
                "output": {**VALID_OUTPUT, "width": 1079},
            },
        ),
        client.post(
            "/api/v1/editing-projects",
            json={
                **VALID_PAYLOAD,
                "output": {**VALID_OUTPUT, "height": 128},
                "captionStyle": {**VALID_CAPTION_STYLE, "fontPx": 129},
            },
        ),
        client.post(
            "/api/v1/editing-projects",
            json={
                **VALID_PAYLOAD,
                "captionStyle": {
                    **VALID_CAPTION_STYLE,
                    "lineSpacing": "1.25",
                },
            },
        ),
        client.get("/api/v1/editing-projects", params={"limit": 0}),
        client.get("/api/v1/editing-projects", params={"limit": 101}),
        client.get("/api/v1/editing-projects", params={"cursor": "private-invalid"}),
        client.get("/api/v1/editing-projects", params={"cursor": "="}),
    )

    for response in invalid_responses:
        assert response.status_code == 422
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["error"]["code"] == "validation"
        assert "private" not in response.text


@pytest.mark.parametrize(
    "cursor",
    [
        opaque_cursor(b"{}"),
        opaque_cursor(b"[]"),
        opaque_cursor(
            b'{"createdAt":"2026-07-30T07:08:09.123456Z","projectId":"private-invalid-id"}'
        ),
        opaque_cursor(
            b'{"createdAt":"2026-07-30T07:08:09.123456+00:00",'
            b'"projectId":"00000000-0000-4000-8000-000000000001"}'
        ),
        opaque_cursor(
            b'{"createdAt":"2026-07-30T07:08:09Z",'
            b'"projectId":"00000000-0000-4000-8000-000000000001"}'
        ),
        opaque_cursor(
            b'{"createdAt":"2026-07-30T07:08:09.123456Z",'
            b'"createdAt":"2026-07-30T07:08:09.123456Z",'
            b'"projectId":"00000000-0000-4000-8000-000000000001"}'
        ),
        opaque_cursor(
            b'{"createdAt":"2026-07-30T07:08:09.123456Z",'
            b'"future":"private",'
            b'"projectId":"00000000-0000-4000-8000-000000000001"}'
        ),
        opaque_cursor(b"\xff"),
    ],
)
def test_cursor_shape_and_encoding_drift_are_rejected(cursor: str) -> None:
    client, _ = project_client()

    response = client.get("/api/v1/editing-projects", params={"cursor": cursor})

    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["error"]["code"] == "validation"
    assert "private" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [True, 0, 101])
async def test_service_refuses_non_http_limit_values(limit: object) -> None:
    service = EditingProjectService(
        repository=MemoryEditingProjectRepository(),
        clock=lambda: NOW,
    )

    with pytest.raises(ValueError, match="Editing project query is invalid"):
        await service.list(
            installation_id=INSTALLATION_ID,
            cursor=None,
            limit=limit,  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_service_refuses_a_foreign_installation_type() -> None:
    service = EditingProjectService(
        repository=MemoryEditingProjectRepository(),
        clock=lambda: NOW,
    )
    foreign = object()

    with pytest.raises(ValueError, match="Editing project query is invalid"):
        await service.create(
            installation_id=foreign,
            title="夏日露营 第一集",
            output=OutputSpec(width=1080, height=1920, fps=30),
            caption_style=CaptionStyle(
                font_key="source-han-sans",
                font_px=64,
                stroke_px=4,
                line_spacing=1.25,
            ),
        )
    with pytest.raises(ValueError, match="Editing project query is invalid"):
        await service.get(
            installation_id=foreign,
            project_id=str(EditingProjectId.new()),
        )


def test_not_found_duplicate_unavailable_auth_and_missing_service_are_stable() -> None:
    client, repository = project_client()
    for value in ("private-invalid-id", str(EditingProjectId.new())):
        response = client.get(f"/api/v1/editing-projects/{value}")
        assert response.status_code == 404
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["error"]["code"] == "editing_project_not_found"
        assert "private" not in response.text

    repository.failure = EditingProjectAlreadyRegistered()
    duplicate = client.post("/api/v1/editing-projects", json=VALID_PAYLOAD)
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "editing_project_already_registered"

    repository.failure = EditingProjectPersistenceUnavailable()
    unavailable = client.get("/api/v1/editing-projects")
    assert unavailable.status_code == 503
    assert unavailable.json()["error"] == {
        "code": "editing_project_persistence_unavailable",
        "message": "Editing project persistence is unavailable",
        "retryable": True,
        "requestId": unavailable.headers["x-request-id"],
    }

    repository.failure = InvalidEditingProjectQuery()
    invalid_scope = client.get(f"/api/v1/editing-projects/{EditingProjectId.new()}")
    assert invalid_scope.status_code == 422
    assert invalid_scope.json()["error"]["code"] == "validation"
    assert "private" not in invalid_scope.text

    no_auth = TestClient(create_app(database=None)).get("/api/v1/editing-projects")
    assert no_auth.status_code == 401
    assert no_auth.json()["error"]["code"] == "installation_access_denied"

    app = create_app(database=None)
    app.dependency_overrides[require_current_installation_access] = lambda: INSTALLATION_ID
    missing_service = TestClient(app).get("/api/v1/editing-projects")
    assert missing_service.status_code == 503
    assert missing_service.json()["error"]["code"] == "editing_projects_unavailable"


def test_application_factory_wires_the_real_repository_and_clock() -> None:
    database = Database.from_url(
        "postgresql+asyncpg://private:private@127.0.0.1:1/private",
        connect_timeout_seconds=0.05,
    )
    app = create_app(database=database)
    app.dependency_overrides[require_current_installation_access] = lambda: INSTALLATION_ID

    with TestClient(app) as client:
        response = client.post("/api/v1/editing-projects", json=VALID_PAYLOAD)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "editing_project_persistence_unavailable"
    assert response.json()["error"]["retryable"] is True
    assert "private" not in response.text
