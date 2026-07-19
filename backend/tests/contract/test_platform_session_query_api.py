from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from automation_tool.control_plane import create_app
from automation_tool.control_plane.api.installation_access import (
    require_current_installation_access,
)
from automation_tool.control_plane.application.platform_session_health import (
    PlatformSessionHealthProjection,
    PlatformSessionHealthService,
)
from automation_tool.control_plane.domain import InstallationId
from automation_tool.protocol import PlatformSessionState

INSTALLATION_ID = InstallationId.new()
OBSERVED_AT = datetime(2026, 7, 19, 14, 30, tzinfo=UTC)
UPDATED_AT = datetime(2026, 7, 19, 14, 30, 1, tzinfo=UTC)


class QueryRepository:
    def __init__(self, projection: PlatformSessionHealthProjection | None) -> None:
        self.projection = projection

    async def converge(self, pending: object) -> object:
        raise AssertionError("query endpoint must not mutate the projection")

    async def get(
        self,
        installation_id: InstallationId,
        platform: str,
    ) -> PlatformSessionHealthProjection | None:
        assert installation_id == INSTALLATION_ID
        assert platform == "douyin"
        return self.projection


def client(projection: PlatformSessionHealthProjection | None) -> TestClient:
    app = create_app(
        database=None,
        platform_session_health_service=PlatformSessionHealthService(
            repository=QueryRepository(projection)
        ),
    )
    app.dependency_overrides[require_current_installation_access] = lambda: INSTALLATION_ID
    return TestClient(app)


def test_openapi_exposes_one_app_session_protected_platform_status_query() -> None:
    operation = create_app(database=None).openapi()["paths"]["/api/v1/platform-sessions/douyin"][
        "get"
    ]

    assert operation["operationId"] == "getDouyinPlatformSession"
    assert operation["security"] == [{"AppSession": []}]


def test_query_returns_the_current_installation_non_sensitive_health_fact() -> None:
    projection = PlatformSessionHealthProjection(
        installation_id=INSTALLATION_ID,
        platform="douyin",
        state=PlatformSessionState.HEALTHY,
        session_revision=7,
        observed_at=OBSERVED_AT,
        updated_at=UPDATED_AT,
    )

    response = client(projection).get("/api/v1/platform-sessions/douyin")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "platform": "douyin",
        "state": "healthy",
        "observedAt": "2026-07-19T14:30:00Z",
    }
    assert str(INSTALLATION_ID) not in response.text
    assert "sessionRevision" not in response.text
    assert "updatedAt" not in response.text


def test_query_maps_absent_projection_to_unknown_without_inventing_a_timestamp() -> None:
    response = client(None).get("/api/v1/platform-sessions/douyin")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "platform": "douyin",
        "state": "unknown",
        "observedAt": None,
    }


def test_query_requires_the_real_app_session_and_fails_closed_without_a_service() -> None:
    no_auth = TestClient(create_app(database=None)).get("/api/v1/platform-sessions/douyin")
    assert no_auth.status_code == 401
    assert no_auth.json()["error"]["code"] == "installation_access_denied"

    app = create_app(database=None)
    app.dependency_overrides[require_current_installation_access] = lambda: INSTALLATION_ID
    unavailable = TestClient(app).get("/api/v1/platform-sessions/douyin")
    assert unavailable.status_code == 503
    assert unavailable.headers["cache-control"] == "no-store"
    assert unavailable.json()["error"]["code"] == "platform_session_unavailable"
