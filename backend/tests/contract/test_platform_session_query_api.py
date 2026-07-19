from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from automation_tool.control_plane import create_app
from automation_tool.control_plane.api.installation_access import (
    require_current_installation_access,
)
from automation_tool.control_plane.application.platform_session_health import (
    PendingPlatformSessionHealth,
    PlatformSessionHealthConvergenceResult,
    PlatformSessionHealthProjection,
    PlatformSessionHealthService,
    PlatformSessionHealthUnavailable,
    PlatformSessionLogoutGate,
)
from automation_tool.control_plane.domain import InstallationId
from automation_tool.protocol import PlatformSessionState

INSTALLATION_ID = InstallationId.new()
OBSERVED_AT = datetime(2026, 7, 19, 14, 30, tzinfo=UTC)
UPDATED_AT = datetime(2026, 7, 19, 14, 30, 1, tzinfo=UTC)


class QueryRepository:
    def __init__(self, projection: PlatformSessionHealthProjection | None) -> None:
        self.projection = projection

    async def converge(
        self,
        pending: PendingPlatformSessionHealth,
    ) -> PlatformSessionHealthConvergenceResult:
        raise AssertionError("query endpoint must not mutate the projection")

    async def get(
        self,
        installation_id: InstallationId,
        platform: str,
    ) -> PlatformSessionHealthProjection | None:
        assert installation_id == INSTALLATION_ID
        assert platform == "douyin"
        return self.projection

    async def begin_logout(
        self,
        installation_id: InstallationId,
        platform: str,
        blocked_at: datetime,
    ) -> PlatformSessionLogoutGate:
        assert installation_id == INSTALLATION_ID
        assert platform == "douyin"
        assert blocked_at.utcoffset() == UTC.utcoffset(blocked_at)
        revision = 1 if self.projection is None else self.projection.session_revision + 1
        return PlatformSessionLogoutGate(
            installation_id=installation_id,
            platform=platform,
            state="blocked",
            session_revision=revision,
            updated_at=blocked_at,
        )


def client(projection: PlatformSessionHealthProjection | None) -> TestClient:
    app = create_app(
        database=None,
        platform_session_health_service=PlatformSessionHealthService(
            repository=QueryRepository(projection)
        ),
    )
    app.dependency_overrides[require_current_installation_access] = lambda: INSTALLATION_ID
    return TestClient(app)


def failing_client() -> TestClient:
    repository = QueryRepository(None)

    async def unavailable(*args: object, **kwargs: object) -> object:
        raise PlatformSessionHealthUnavailable

    repository.get = unavailable  # type: ignore[method-assign,assignment]
    repository.begin_logout = unavailable  # type: ignore[method-assign,assignment]
    app = create_app(
        database=None,
        platform_session_health_service=PlatformSessionHealthService(repository=repository),
    )
    app.dependency_overrides[require_current_installation_access] = lambda: INSTALLATION_ID
    return TestClient(app)


def test_openapi_exposes_one_app_session_protected_platform_status_query() -> None:
    operation = create_app(database=None).openapi()["paths"]["/api/v1/platform-sessions/douyin"][
        "get"
    ]

    assert operation["operationId"] == "getDouyinPlatformSession"
    assert operation["security"] == [{"AppSession": []}]


def test_openapi_exposes_one_app_session_protected_logout_prepare_operation() -> None:
    operation = create_app(database=None).openapi()["paths"][
        "/api/v1/platform-sessions/douyin/logout/prepare"
    ]["post"]

    assert operation["operationId"] == "prepareDouyinPlatformSessionLogout"
    assert operation["security"] == [{"AppSession": []}]


def test_logout_prepare_persists_the_gate_before_the_app_stops_local_execution() -> None:
    projection = PlatformSessionHealthProjection(
        installation_id=INSTALLATION_ID,
        platform="douyin",
        state=PlatformSessionState.HEALTHY,
        session_revision=7,
        observed_at=OBSERVED_AT,
        updated_at=UPDATED_AT,
    )

    response = client(projection).post("/api/v1/platform-sessions/douyin/logout/prepare")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "platform": "douyin",
        "state": "blocked",
        "sessionRevision": 8,
    }


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


def test_query_and_logout_prepare_map_service_failures_to_one_safe_error() -> None:
    app_client = failing_client()

    query = app_client.get("/api/v1/platform-sessions/douyin")
    logout = app_client.post("/api/v1/platform-sessions/douyin/logout/prepare")

    assert query.status_code == 503
    assert logout.status_code == 503
    assert query.headers["cache-control"] == "no-store"
    assert logout.headers["cache-control"] == "no-store"
    assert query.json()["error"]["code"] == "platform_session_unavailable"
    assert logout.json()["error"]["code"] == "platform_session_unavailable"
