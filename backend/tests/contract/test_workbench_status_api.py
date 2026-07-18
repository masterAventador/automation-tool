from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from automation_tool.control_plane import create_app
from automation_tool.control_plane.api.installation_access import (
    require_current_installation_access,
)
from automation_tool.control_plane.application.executor_connection_registry import (
    ExecutorConnectionRegistry,
    OnlineExecutorConnection,
)
from automation_tool.control_plane.application.executor_connections import (
    ExecutorArchitecture,
    ExecutorPlatform,
)
from automation_tool.control_plane.domain import (
    ExecutorConnectionId,
    ExecutorId,
    InstallationId,
)

INSTALLATION_ID = InstallationId.new()
NOW = datetime(2026, 7, 18, 22, 30, tzinfo=UTC)


class StaticExecutorRegistry(ExecutorConnectionRegistry):
    def __init__(self, online: OnlineExecutorConnection | None) -> None:
        super().__init__()
        self.online = online

    async def snapshot(
        self,
        installation_id: InstallationId,
    ) -> OnlineExecutorConnection | None:
        assert installation_id == INSTALLATION_ID
        return self.online


def online_executor() -> OnlineExecutorConnection:
    return OnlineExecutorConnection(
        connection_id=ExecutorConnectionId.new(),
        installation_id=INSTALLATION_ID,
        executor_id=ExecutorId.new(),
        protocol_version="1.0",
        executor_version="0.1.0",
        platform=ExecutorPlatform.MACOS,
        architecture=ExecutorArchitecture.ARM64,
        connected_at=NOW,
        last_heartbeat_at=NOW,
        last_sequence=7,
    )


def workbench_client(online: OnlineExecutorConnection | None) -> TestClient:
    app = create_app(
        database=None,
        executor_connection_registry=StaticExecutorRegistry(online),
    )
    app.dependency_overrides[require_current_installation_access] = lambda: INSTALLATION_ID
    return TestClient(app)


def test_openapi_exposes_one_app_session_protected_workbench_status() -> None:
    schema = create_app(database=None).openapi()
    operation = schema["paths"]["/api/v1/workbench/status"]["get"]

    assert operation["operationId"] == "getWorkbenchStatus"
    assert operation["security"] == [{"AppSession": []}]


def test_status_projects_control_plane_and_current_executor_without_private_ids() -> None:
    response = workbench_client(online_executor()).get("/api/v1/workbench/status")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "controlPlaneStatus": "ready",
        "executorStatus": "online",
        "executorLastHeartbeatAt": "2026-07-18T22:30:00Z",
    }
    assert str(INSTALLATION_ID) not in response.text
    assert "connectionId" not in response.text
    assert "executorId" not in response.text


def test_offline_auth_and_registry_failure_are_safe_and_non_cacheable() -> None:
    offline = workbench_client(None).get("/api/v1/workbench/status")
    assert offline.status_code == 200
    assert offline.headers["cache-control"] == "no-store"
    assert offline.json() == {
        "controlPlaneStatus": "ready",
        "executorStatus": "offline",
        "executorLastHeartbeatAt": None,
    }

    no_auth = TestClient(create_app(database=None)).get("/api/v1/workbench/status")
    assert no_auth.status_code == 401
    assert no_auth.json()["error"]["code"] == "installation_access_denied"

    app = create_app(database=None)
    app.dependency_overrides[require_current_installation_access] = lambda: INSTALLATION_ID
    app.state.executor_connection_registry = object()
    unavailable = TestClient(app).get("/api/v1/workbench/status")
    assert unavailable.status_code == 503
    assert unavailable.headers["cache-control"] == "no-store"
    assert unavailable.json()["error"] == {
        "code": "workbench_status_unavailable",
        "message": "Workbench status is unavailable",
        "requestId": unavailable.headers["x-request-id"],
        "retryable": True,
    }
