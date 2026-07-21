from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from automation_tool.control_plane import create_app
from automation_tool.control_plane.api.installation_access import (
    require_current_installation_access,
)
from automation_tool.control_plane.application.task_target_results import (
    InvalidTaskTargetResult,
    TaskTargetResult,
    TaskTargetResultEvidence,
    TaskTargetResultNotFound,
    TaskTargetResultService,
    TaskTargetResultSnapshot,
    TaskTargetResultStatus,
    TaskTargetResultUnavailable,
)
from automation_tool.control_plane.application.tasks import TaskRecord
from automation_tool.control_plane.domain import (
    ActionId,
    InstallationId,
    TargetId,
    TaskId,
    TaskStatus,
)

NOW = datetime(2026, 7, 21, 8, 0, tzinfo=UTC)
INSTALLATION_ID = InstallationId.parse("123e4567-e89b-42d3-a456-426614174003")
TASK_ID = TaskId.parse("123e4567-e89b-42d3-a456-426614174005")


class MemoryTargetResultRepository:
    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure

    async def get(
        self,
        *,
        installation_id: InstallationId,
        task_id: TaskId,
    ) -> TaskTargetResultSnapshot | None:
        if isinstance(self.failure, TaskTargetResultNotFound):
            return None
        if self.failure is not None:
            raise self.failure
        if installation_id != INSTALLATION_ID or task_id != TASK_ID:
            return None
        return TaskTargetResultSnapshot(
            task=TaskRecord(
                task_id=TASK_ID,
                installation_id=INSTALLATION_ID,
                status=TaskStatus.PARTIALLY_SUCCEEDED,
                revision=8,
                last_event_sequence=7,
                created_at=NOW,
                updated_at=NOW,
            ),
            items=(
                TaskTargetResult(
                    target_id=TargetId.parse("123e4567-e89b-42d3-a456-426614174006"),
                    ordinal=1,
                    display_name="已完成目标",
                    public_handle="public_1",
                    status=TaskTargetResultStatus.SUCCEEDED,
                    evidence=TaskTargetResultEvidence.COMMENT_CONFIRMED,
                    action_id=ActionId.parse("123e4567-e89b-42d3-a456-426614174008"),
                    updated_at=NOW,
                ),
                TaskTargetResult(
                    target_id=TargetId.parse("123e4567-e89b-42d3-a456-426614174007"),
                    ordinal=2,
                    display_name="已跳过目标",
                    public_handle=None,
                    status=TaskTargetResultStatus.SKIPPED,
                    evidence=TaskTargetResultEvidence.USER_EXCLUDED,
                    action_id=None,
                    updated_at=NOW,
                ),
            ),
        )


def client() -> TestClient:
    app = create_app(
        database=None,
        task_target_result_service=TaskTargetResultService(
            repository=MemoryTargetResultRepository()
        ),
    )
    app.dependency_overrides[require_current_installation_access] = lambda: INSTALLATION_ID
    return TestClient(app)


def failing_client(failure: Exception) -> TestClient:
    app = create_app(
        database=None,
        task_target_result_service=TaskTargetResultService(
            repository=MemoryTargetResultRepository(failure)
        ),
    )
    app.dependency_overrides[require_current_installation_access] = lambda: INSTALLATION_ID
    return TestClient(app)


def test_openapi_freezes_target_result_operation() -> None:
    operation = create_app(database=None).openapi()["paths"][
        "/api/v1/tasks/{task_id}/target-results"
    ]["get"]

    assert operation["operationId"] == "getTaskTargetResults"
    assert operation["security"] == [{"AppSession": []}]


def test_app_returns_only_scoped_safe_target_result_facts() -> None:
    response = client().get(f"/api/v1/tasks/{TASK_ID}/target-results")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "taskId": str(TASK_ID),
        "taskStatus": "partially_succeeded",
        "taskRevision": 8,
        "lastEventSequence": 7,
        "items": [
            {
                "targetId": "123e4567-e89b-42d3-a456-426614174006",
                "ordinal": 1,
                "displayName": "已完成目标",
                "publicHandle": "public_1",
                "resultStatus": "succeeded",
                "evidence": "comment_confirmed",
                "actionId": "123e4567-e89b-42d3-a456-426614174008",
                "updatedAt": "2026-07-21T08:00:00Z",
            },
            {
                "targetId": "123e4567-e89b-42d3-a456-426614174007",
                "ordinal": 2,
                "displayName": "已跳过目标",
                "publicHandle": None,
                "resultStatus": "skipped",
                "evidence": "user_excluded",
                "actionId": None,
                "updatedAt": "2026-07-21T08:00:00Z",
            },
        ],
    }
    assert "platformTarget" not in response.text
    assert "dedupe" not in response.text.lower()


def test_target_results_require_auth_and_hide_invalid_or_cross_scope_tasks() -> None:
    assert client().get("/api/v1/tasks/private-invalid/target-results").status_code == 404
    assert (
        client()
        .get("/api/v1/tasks/123e4567-e89b-42d3-a456-426614174099/target-results")
        .status_code
        == 404
    )
    assert (
        TestClient(create_app(database=None))
        .get(f"/api/v1/tasks/{TASK_ID}/target-results")
        .status_code
        == 401
    )


@pytest.mark.parametrize(
    ("failure", "status_code", "code"),
    (
        (TaskTargetResultNotFound(), 404, "task_target_results_not_found"),
        (InvalidTaskTargetResult(), 422, "validation"),
        (TaskTargetResultUnavailable(), 503, "task_target_results_unavailable"),
        (RuntimeError("private repository failure"), 503, "task_target_results_unavailable"),
    ),
)
def test_target_result_api_maps_closed_failures_without_disclosure(
    failure: Exception,
    status_code: int,
    code: str,
) -> None:
    response = failing_client(failure).get(f"/api/v1/tasks/{TASK_ID}/target-results")
    assert response.status_code == status_code
    assert response.json()["error"]["code"] == code
    assert "private" not in response.text


def test_target_result_api_fails_closed_when_service_is_not_installed() -> None:
    app = create_app(database=None)
    app.dependency_overrides[require_current_installation_access] = lambda: INSTALLATION_ID
    response = TestClient(app).get(f"/api/v1/tasks/{TASK_ID}/target-results")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "task_target_results_unavailable"


def test_target_result_failure_helper_preserves_unknown_programmer_errors() -> None:
    from automation_tool.control_plane.api.task_target_results import _failure

    error = RuntimeError("private programmer failure")
    with pytest.raises(RuntimeError) as captured:
        _failure(error)
    assert captured.value is error
