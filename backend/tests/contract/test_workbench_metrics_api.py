from __future__ import annotations

from fastapi.testclient import TestClient

from automation_tool.control_plane import create_app
from automation_tool.control_plane.api.installation_access import (
    require_current_installation_access,
)
from automation_tool.control_plane.application.workbench_metrics import (
    WorkbenchMetricsRepositoryRejected,
    WorkbenchMetricsService,
    WorkbenchMetricsSnapshot,
)
from automation_tool.control_plane.domain import InstallationId

INSTALLATION_ID = InstallationId.new()


class StaticRepository:
    def __init__(self, *, rejected: bool = False) -> None:
        self.rejected = rejected

    async def get(self, *, installation_id: InstallationId) -> WorkbenchMetricsSnapshot:
        assert installation_id == INSTALLATION_ID
        if self.rejected:
            raise WorkbenchMetricsRepositoryRejected
        return WorkbenchMetricsSnapshot(
            task_total=9,
            task_succeeded=3,
            task_failed=2,
            task_handoff_required=1,
            task_outcome_uncertain=1,
            action_total=12,
            action_succeeded=7,
            action_failed=2,
            action_outcome_uncertain=1,
        )


def metrics_client(*, rejected: bool = False) -> TestClient:
    app = create_app(
        database=None,
        workbench_metrics_service=WorkbenchMetricsService(
            repository=StaticRepository(rejected=rejected)
        ),
    )
    app.dependency_overrides[require_current_installation_access] = lambda: INSTALLATION_ID
    return TestClient(app)


def test_openapi_exposes_one_app_session_protected_read_only_metrics_endpoint() -> None:
    schema = create_app(database=None).openapi()
    operation = schema["paths"]["/api/v1/workbench/metrics"]["get"]

    assert operation["operationId"] == "getWorkbenchMetrics"
    assert operation["security"] == [{"AppSession": []}]
    assert "post" not in schema["paths"]["/api/v1/workbench/metrics"]


def test_metrics_return_only_bounded_structured_installation_counts() -> None:
    response = metrics_client().get("/api/v1/workbench/metrics")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "version": "workbench.metrics.v1",
        "tasks": {
            "total": 9,
            "succeeded": 3,
            "failed": 2,
            "handoffRequired": 1,
            "outcomeUncertain": 1,
        },
        "actions": {
            "total": 12,
            "succeeded": 7,
            "failed": 2,
            "outcomeUncertain": 1,
        },
    }
    assert str(INSTALLATION_ID) not in response.text
    assert "diagnostic" not in response.text.lower()


def test_metrics_auth_missing_service_and_repository_failure_are_safe() -> None:
    no_auth = TestClient(create_app(database=None)).get("/api/v1/workbench/metrics")
    assert no_auth.status_code == 401
    assert no_auth.json()["error"]["code"] == "installation_access_denied"

    missing_app = create_app(database=None)
    missing_app.dependency_overrides[require_current_installation_access] = lambda: INSTALLATION_ID
    missing = TestClient(missing_app).get("/api/v1/workbench/metrics")
    assert missing.status_code == 503
    assert missing.headers["cache-control"] == "no-store"
    assert missing.json()["error"]["code"] == "workbench_metrics_unavailable"

    rejected = metrics_client(rejected=True).get("/api/v1/workbench/metrics")
    assert rejected.status_code == 503
    assert rejected.headers["cache-control"] == "no-store"
    assert rejected.json()["error"] == {
        "code": "workbench_metrics_unavailable",
        "message": "Workbench metrics are unavailable",
        "requestId": rejected.headers["x-request-id"],
        "retryable": True,
    }
