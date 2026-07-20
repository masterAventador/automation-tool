from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from automation_tool.control_plane import create_app
from automation_tool.control_plane.api.installation_access import (
    require_current_installation_access,
)
from automation_tool.control_plane.application.task_command_delivery import TaskCommandRecord
from automation_tool.control_plane.application.task_discovery import (
    PendingTaskDiscovery,
    TaskDiscoveryRejected,
    TaskDiscoveryStartResult,
    TaskDiscoveryStartService,
)
from automation_tool.control_plane.application.tasks import TaskRecord
from automation_tool.control_plane.domain import (
    InstallationId,
    TaskCommandStatus,
    TaskId,
    TaskStatus,
)

NOW = datetime(2026, 7, 19, 18, 0, tzinfo=UTC)
INSTALLATION_ID = InstallationId.parse("123e4567-e89b-42d3-a456-426614174003")
TASK_ID = TaskId.parse("123e4567-e89b-42d3-a456-426614174005")


class FixedClock:
    def now(self) -> datetime:
        return NOW


class MemoryTaskDiscoveryRepository:
    def __init__(self) -> None:
        self.records: dict[str, TaskDiscoveryStartResult] = {}
        self.failure: Exception | None = None

    async def start(self, pending: PendingTaskDiscovery) -> TaskDiscoveryStartResult:
        if self.failure is not None:
            raise self.failure
        replay = self.records.get(pending.idempotency_key)
        if replay is not None:
            return TaskDiscoveryStartResult(
                task=replay.task,
                command=replay.command,
                created=False,
            )
        task = TaskRecord(
            task_id=pending.task_id,
            installation_id=pending.installation_id,
            status=TaskStatus.DISCOVERING_TARGETS,
            revision=2,
            last_event_sequence=2,
            created_at=NOW - timedelta(minutes=1),
            updated_at=NOW,
        )
        command = TaskCommandRecord(
            message_id=pending.message_id,
            correlation_id=pending.correlation_id,
            installation_id=pending.installation_id,
            task_id=pending.task_id,
            execution_attempt_id=pending.execution_attempt_id,
            sequence=pending.command_sequence,
            command_type=pending.command_type,
            status=TaskCommandStatus.PENDING,
            idempotency_key=pending.idempotency_key,
            revision=1,
            delivery_attempts=0,
            next_delivery_at=pending.created_at,
            lease_expires_at=None,
            delivered_at=None,
            acknowledged_at=None,
            response_message_id=None,
            response_type=None,
            deadline_at=pending.deadline_at,
            created_at=pending.created_at,
            updated_at=pending.created_at,
        )
        result = TaskDiscoveryStartResult(task=task, command=command, created=True)
        self.records[pending.idempotency_key] = result
        return result


def discovery_app(
    repository: MemoryTaskDiscoveryRepository | None = None,
) -> tuple[TestClient, MemoryTaskDiscoveryRepository]:
    resolved = repository or MemoryTaskDiscoveryRepository()
    service = TaskDiscoveryStartService(repository=resolved, clock=FixedClock())
    app = create_app(database=None, task_discovery_start_service=service)
    app.dependency_overrides[require_current_installation_access] = lambda: INSTALLATION_ID
    return TestClient(app), resolved


def test_openapi_exposes_app_session_protected_discovery_start() -> None:
    schema = create_app(database=None).openapi()
    operation = schema["paths"]["/api/v1/tasks/{task_id}/discoveries"]["post"]

    assert operation["operationId"] == "startTaskDiscovery"
    assert operation["security"] == [{"AppSession": []}]
    assert [parameter["name"] for parameter in operation["parameters"]] == [
        "task_id",
        "Idempotency-Key",
    ]


def test_start_and_replay_return_only_public_discovery_state() -> None:
    client, _ = discovery_app()
    headers = {"Idempotency-Key": "task:discover:demo-1"}

    started = client.post(f"/api/v1/tasks/{TASK_ID}/discoveries", headers=headers)
    replayed = client.post(f"/api/v1/tasks/{TASK_ID}/discoveries", headers=headers)

    assert started.status_code == 202
    assert replayed.status_code == 200
    assert started.json() == replayed.json()
    assert started.json() == {
        "taskId": str(TASK_ID),
        "taskStatus": "discovering_targets",
        "taskRevision": 2,
        "lastEventSequence": 2,
        "commandId": started.json()["commandId"],
        "executionAttemptId": started.json()["executionAttemptId"],
        "commandStatus": TaskCommandStatus.PENDING.value,
        "createdAt": "2026-07-19T18:00:00Z",
        "deadlineAt": "2026-07-19T18:03:00Z",
    }
    assert started.headers["cache-control"] == "no-store"
    assert "task:discover:demo-1" not in started.text


def test_discovery_input_auth_rejection_and_unavailable_fail_closed() -> None:
    client, repository = discovery_app()
    for response in (
        client.post(f"/api/v1/tasks/{TASK_ID}/discoveries"),
        client.post(
            f"/api/v1/tasks/{TASK_ID}/discoveries",
            headers={"Idempotency-Key": "contains space"},
        ),
        client.post(
            "/api/v1/tasks/not-a-task/discoveries",
            headers={"Idempotency-Key": "task:discover:invalid-task"},
        ),
    ):
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation"

    unauthorized = TestClient(create_app(database=None)).post(
        f"/api/v1/tasks/{TASK_ID}/discoveries",
        headers={"Idempotency-Key": "task:discover:no-auth"},
    )
    assert unauthorized.status_code == 401

    repository.failure = TaskDiscoveryRejected()
    rejected = client.post(
        f"/api/v1/tasks/{TASK_ID}/discoveries",
        headers={"Idempotency-Key": "task:discover:rejected"},
    )
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "task_discovery_rejected"

    repository.failure = RuntimeError("private database location")
    unavailable = client.post(
        f"/api/v1/tasks/{TASK_ID}/discoveries",
        headers={"Idempotency-Key": "task:discover:unavailable"},
    )
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "task_discovery_unavailable"
    assert "private" not in unavailable.text

    missing_service_app = create_app(database=None)
    missing_service_app.dependency_overrides[require_current_installation_access] = lambda: (
        INSTALLATION_ID
    )
    missing_service = TestClient(missing_service_app, raise_server_exceptions=False)
    unavailable_service = missing_service.post(
        f"/api/v1/tasks/{TASK_ID}/discoveries",
        headers={"Idempotency-Key": "task:discover:missing-service"},
    )
    assert unavailable_service.status_code == 503
    assert unavailable_service.json()["error"]["code"] == "task_discovery_unavailable"
