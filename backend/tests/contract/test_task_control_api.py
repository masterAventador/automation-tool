from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi.testclient import TestClient

from automation_tool.control_plane import create_app
from automation_tool.control_plane.api.installation_access import (
    require_current_installation_access,
)
from automation_tool.control_plane.application.task_command_delivery import TaskCommandRecord
from automation_tool.control_plane.application.task_controls import (
    PendingTaskControl,
    TaskControlConflict,
    TaskControlEnqueueResult,
    TaskControlNotFound,
    TaskControlService,
)
from automation_tool.control_plane.domain import (
    ExecutionAttemptId,
    InstallationId,
    TaskCommandStatus,
    TaskId,
)

NOW = datetime(2026, 7, 18, 18, 30, tzinfo=UTC)
INSTALLATION_ID = InstallationId.parse("123e4567-e89b-42d3-a456-426614174003")
TASK_ID = TaskId.parse("123e4567-e89b-42d3-a456-426614174005")
ATTEMPT_ID = ExecutionAttemptId.parse("123e4567-e89b-42d3-a456-426614174006")


class FixedClock:
    def now(self) -> datetime:
        return NOW


class MemoryTaskControlRepository:
    def __init__(self) -> None:
        self.records: dict[str, TaskCommandRecord] = {}
        self.failure: Exception | None = None

    async def enqueue_control(self, control: PendingTaskControl) -> TaskControlEnqueueResult:
        if self.failure is not None:
            raise self.failure
        existing = self.records.get(control.idempotency_key)
        if existing is not None:
            return TaskControlEnqueueResult(command=existing, created=False)
        command = TaskCommandRecord(
            message_id=control.message_id,
            correlation_id=control.correlation_id,
            installation_id=control.installation_id,
            task_id=control.task_id,
            execution_attempt_id=ATTEMPT_ID,
            sequence=len(self.records) + 2,
            command_type=control.command_type,
            status=TaskCommandStatus.PENDING,
            idempotency_key=control.idempotency_key,
            revision=1,
            delivery_attempts=0,
            next_delivery_at=control.created_at,
            lease_expires_at=None,
            delivered_at=None,
            acknowledged_at=None,
            response_message_id=None,
            response_type=None,
            deadline_at=control.deadline_at,
            created_at=control.created_at,
            updated_at=control.created_at,
        )
        self.records[control.idempotency_key] = command
        return TaskControlEnqueueResult(command=command, created=True)


def control_app(
    repository: MemoryTaskControlRepository | None = None,
) -> tuple[TestClient, MemoryTaskControlRepository]:
    resolved = repository or MemoryTaskControlRepository()
    service = TaskControlService(repository=resolved, clock=FixedClock())
    app = create_app(database=None, task_control_service=service)
    app.dependency_overrides[require_current_installation_access] = lambda: INSTALLATION_ID
    return TestClient(app), resolved


def test_openapi_exposes_two_app_session_protected_control_operations() -> None:
    schema = create_app(database=None).openapi()
    pause = schema["paths"]["/api/v1/tasks/{task_id}/pause"]["post"]
    resume = schema["paths"]["/api/v1/tasks/{task_id}/resume"]["post"]

    assert pause["operationId"] == "pauseTask"
    assert resume["operationId"] == "resumeTask"
    assert pause["security"] == resume["security"] == [{"AppSession": []}]
    assert [parameter["name"] for parameter in pause["parameters"]] == [
        "task_id",
        "Idempotency-Key",
    ]


def test_pause_resume_and_idempotent_replay_return_only_public_command_state() -> None:
    client, _ = control_app()
    pause_headers = {"Idempotency-Key": "task:pause:demo-1"}
    resume_headers = {"Idempotency-Key": "task:resume:demo-1"}

    paused = client.post(f"/api/v1/tasks/{TASK_ID}/pause", headers=pause_headers, json={})
    replayed = client.post(f"/api/v1/tasks/{TASK_ID}/pause", headers=pause_headers, json={})
    resumed = client.post(f"/api/v1/tasks/{TASK_ID}/resume", headers=resume_headers, json={})
    replayed_resume = client.post(
        f"/api/v1/tasks/{TASK_ID}/resume", headers=resume_headers, json={}
    )

    assert paused.status_code == resumed.status_code == 202
    assert replayed.status_code == 200
    assert replayed_resume.status_code == 200
    assert paused.json() == replayed.json()
    assert paused.json() == {
        "commandId": paused.json()["commandId"],
        "taskId": str(TASK_ID),
        "executionAttemptId": str(ATTEMPT_ID),
        "sequence": 2,
        "commandType": "task.pause",
        "status": "pending",
        "revision": 1,
        "createdAt": "2026-07-18T18:30:00Z",
        "deadlineAt": "2026-07-18T18:31:00Z",
    }
    assert resumed.json()["commandType"] == "task.resume"
    assert resumed.json()["sequence"] == 3
    assert resumed.json() == replayed_resume.json()
    for response in (paused, replayed, resumed, replayed_resume):
        assert response.headers["cache-control"] == "no-store"
        assert "idempotency" not in response.text.lower()
        UUID(response.json()["commandId"])


def test_invalid_input_auth_not_found_conflict_and_unavailable_fail_closed() -> None:
    client, repository = control_app()
    invalid = (
        client.post(f"/api/v1/tasks/{TASK_ID}/pause", json={}),
        client.post(
            f"/api/v1/tasks/{TASK_ID}/pause",
            headers={"Idempotency-Key": "private invalid"},
            json={},
        ),
        client.post(
            f"/api/v1/tasks/{TASK_ID}/resume",
            headers={"Idempotency-Key": "task:resume:extra"},
            json={"private": True},
        ),
    )
    for response in invalid:
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation"
        assert "private" not in response.text

    unauthorized = TestClient(create_app(database=None)).post(
        f"/api/v1/tasks/{TASK_ID}/pause",
        headers={"Idempotency-Key": "task:pause:no-auth"},
        json={},
    )
    assert unauthorized.status_code == 401
    assert unauthorized.json()["error"]["code"] == "installation_access_denied"

    cases = (
        (TaskControlNotFound(), 404, "task_not_found"),
        (TaskControlConflict(), 409, "task_control_rejected"),
        (RuntimeError("private database address"), 503, "task_control_unavailable"),
    )
    for failure, expected_status, expected_code in cases:
        repository.failure = failure
        response = client.post(
            f"/api/v1/tasks/{TASK_ID}/pause",
            headers={"Idempotency-Key": "task:pause:failure"},
            json={},
        )
        assert response.status_code == expected_status
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["error"]["code"] == expected_code
        assert "private" not in response.text

    app = create_app(database=None)
    app.dependency_overrides[require_current_installation_access] = lambda: INSTALLATION_ID
    unavailable = TestClient(app).post(
        f"/api/v1/tasks/{TASK_ID}/pause",
        headers={"Idempotency-Key": "task:pause:unavailable"},
        json={},
    )
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "task_control_unavailable"
