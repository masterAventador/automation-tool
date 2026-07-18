from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from automation_tool.control_plane import create_app
from automation_tool.control_plane.api.installation_access import (
    require_current_installation_access,
)
from automation_tool.control_plane.application.tasks import (
    TaskCreationResult,
    TaskCreationService,
    TaskPersistenceRejected,
    TaskRecord,
)
from automation_tool.control_plane.domain import InstallationId, TaskId, TaskStatus

NOW = datetime(2026, 7, 18, 6, 0, tzinfo=UTC)
INSTALLATION_ID = InstallationId.new()


class FixedClock:
    def now(self) -> datetime:
        return NOW


class MemoryTaskRepository:
    def __init__(self) -> None:
        self.records: dict[str, TaskRecord] = {}
        self.reject = False

    async def create(
        self,
        *,
        task_id: TaskId,
        installation_id: InstallationId,
        idempotency_key: str,
        created_at: datetime,
    ) -> TaskCreationResult:
        if self.reject:
            raise TaskPersistenceRejected
        existing = self.records.get(idempotency_key)
        if existing is not None:
            return TaskCreationResult(task=existing, created=False)
        record = TaskRecord(
            task_id=task_id,
            installation_id=installation_id,
            status=TaskStatus.DRAFT,
            revision=1,
            last_event_sequence=0,
            created_at=created_at,
            updated_at=created_at,
        )
        self.records[idempotency_key] = record
        return TaskCreationResult(task=record, created=True)

    async def get(
        self,
        *,
        task_id: TaskId,
        installation_id: InstallationId,
    ) -> TaskRecord | None:
        raise AssertionError("not used")

    async def transition(
        self,
        *,
        task_id: TaskId,
        installation_id: InstallationId,
        expected_revision: int,
        target_status: TaskStatus,
        updated_at: datetime,
    ) -> TaskRecord:
        raise AssertionError("not used")


def task_app(
    repository: MemoryTaskRepository | None = None,
) -> tuple[TestClient, MemoryTaskRepository]:
    resolved = repository or MemoryTaskRepository()
    service = TaskCreationService(repository=resolved, clock=FixedClock())
    app = create_app(database=None, task_creation_service=service)
    app.dependency_overrides[require_current_installation_access] = lambda: INSTALLATION_ID
    return TestClient(app), resolved


def test_openapi_exposes_one_app_session_protected_task_creation_operation() -> None:
    schema = create_app(database=None).openapi()
    operation = schema["paths"]["/api/v1/tasks"]["post"]

    assert operation["operationId"] == "createTask"
    assert operation["security"] == [{"AppSession": []}]
    assert operation["parameters"] == [
        {
            "in": "header",
            "name": "Idempotency-Key",
            "required": True,
            "schema": {"title": "Idempotency-Key", "type": "string"},
        }
    ]


def test_create_and_replay_return_one_public_task_snapshot_without_secrets() -> None:
    client, repository = task_app()
    headers = {"Idempotency-Key": "task:create:demo-1"}

    created = client.post("/api/v1/tasks", headers=headers, json={})
    replayed = client.post("/api/v1/tasks", headers=headers, json={})

    assert created.status_code == 201
    assert replayed.status_code == 200
    assert created.headers["cache-control"] == "no-store"
    assert replayed.headers["cache-control"] == "no-store"
    assert created.json() == replayed.json()
    assert created.json() == {
        "taskId": str(next(iter(repository.records.values())).task_id),
        "status": "draft",
        "revision": 1,
        "lastEventSequence": 0,
        "createdAt": "2026-07-18T06:00:00Z",
        "updatedAt": "2026-07-18T06:00:00Z",
    }
    assert "task:create:demo-1" not in created.text


def test_invalid_key_unknown_body_and_missing_auth_fail_closed() -> None:
    client, _ = task_app()
    invalid = (
        client.post("/api/v1/tasks", json={}),
        client.post(
            "/api/v1/tasks",
            headers={"Idempotency-Key": "contains space"},
            json={},
        ),
        client.post(
            "/api/v1/tasks",
            headers={"Idempotency-Key": "x" * 129},
            json={},
        ),
        client.post(
            "/api/v1/tasks",
            headers={"Idempotency-Key": "task:create:extra"},
            json={"template": "private-future-field"},
        ),
    )
    for response in invalid:
        assert response.status_code == 422
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["error"]["code"] == "validation"
        assert "private" not in response.text

    unauthorized = TestClient(create_app(database=None)).post(
        "/api/v1/tasks",
        headers={"Idempotency-Key": "task:create:no-auth"},
        json={},
    )
    assert unauthorized.status_code == 401
    assert unauthorized.json()["error"]["code"] == "installation_access_denied"


def test_unavailable_and_repository_rejection_are_stable_non_cacheable_errors() -> None:
    app = create_app(database=None)
    app.dependency_overrides[require_current_installation_access] = lambda: INSTALLATION_ID
    unavailable = TestClient(app).post(
        "/api/v1/tasks",
        headers={"Idempotency-Key": "task:create:unavailable"},
        json={},
    )
    assert unavailable.status_code == 503
    assert unavailable.headers["cache-control"] == "no-store"
    assert unavailable.json()["error"]["code"] == "tasks_unavailable"

    repository = MemoryTaskRepository()
    repository.reject = True
    client, _ = task_app(repository)
    rejected = client.post(
        "/api/v1/tasks",
        headers={"Idempotency-Key": "task:create:rejected"},
        json={},
    )
    assert rejected.status_code == 409
    assert rejected.headers["cache-control"] == "no-store"
    assert rejected.json()["error"]["code"] == "task_create_rejected"
    assert "task:create:rejected" not in rejected.text
