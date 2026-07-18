from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from automation_tool.control_plane import create_app
from automation_tool.control_plane.api.installation_access import (
    require_current_installation_access,
)
from automation_tool.control_plane.application.task_queries import TaskQueryService
from automation_tool.control_plane.application.tasks import TaskRecord
from automation_tool.control_plane.domain import InstallationId, TaskId, TaskStatus

NOW = datetime(2026, 7, 18, 17, 0, tzinfo=UTC)
INSTALLATION_ID = InstallationId.new()


class MemoryTaskQueryRepository:
    def __init__(self, records: tuple[TaskRecord, ...]) -> None:
        self.records = records

    async def get(
        self,
        *,
        task_id: TaskId,
        installation_id: InstallationId,
    ) -> TaskRecord | None:
        return next(
            (
                record
                for record in self.records
                if record.task_id == task_id and record.installation_id == installation_id
            ),
            None,
        )

    async def list_page(
        self,
        *,
        installation_id: InstallationId,
        before_updated_at: datetime | None,
        before_task_id: TaskId | None,
        limit: int,
    ) -> tuple[TaskRecord, ...]:
        records = sorted(
            (
                record
                for record in self.records
                if record.installation_id == installation_id
                and (
                    before_updated_at is None
                    or (
                        before_task_id is not None
                        and (record.updated_at, record.task_id.uuid)
                        < (before_updated_at, before_task_id.uuid)
                    )
                )
            ),
            key=lambda record: (record.updated_at, record.task_id.uuid),
            reverse=True,
        )
        return tuple(records[:limit])


def record(*, offset: int, installation_id: InstallationId = INSTALLATION_ID) -> TaskRecord:
    timestamp = NOW + timedelta(minutes=offset)
    return TaskRecord(
        task_id=TaskId.new(),
        installation_id=installation_id,
        status=TaskStatus.DRAFT,
        revision=1,
        created_at=timestamp,
        updated_at=timestamp,
        last_event_sequence=offset,
    )


def query_app(records: tuple[TaskRecord, ...]) -> TestClient:
    service = TaskQueryService(repository=MemoryTaskQueryRepository(records))
    app = create_app(database=None, task_query_service=service)
    app.dependency_overrides[require_current_installation_access] = lambda: INSTALLATION_ID
    return TestClient(app)


def test_openapi_exposes_app_session_protected_task_list_and_detail_operations() -> None:
    schema = create_app(database=None).openapi()
    list_operation = schema["paths"]["/api/v1/tasks"]["get"]
    detail_operation = schema["paths"]["/api/v1/tasks/{task_id}"]["get"]

    assert list_operation["operationId"] == "listTasks"
    assert detail_operation["operationId"] == "getTask"
    assert list_operation["security"] == [{"AppSession": []}]
    assert detail_operation["security"] == [{"AppSession": []}]
    assert [parameter["name"] for parameter in list_operation["parameters"]] == [
        "cursor",
        "limit",
    ]
    task_schema = schema["components"]["schemas"]["TaskResponse"]
    assert task_schema["required"] == [
        "taskId",
        "status",
        "revision",
        "lastEventSequence",
        "createdAt",
        "updatedAt",
    ]
    assert task_schema["properties"]["revision"] == {
        "maximum": 9007199254740991,
        "minimum": 1,
        "title": "Revision",
        "type": "integer",
    }
    assert task_schema["properties"]["lastEventSequence"] == {
        "maximum": 9007199254740991,
        "minimum": 0,
        "title": "Lasteventsequence",
        "type": "integer",
    }


def test_list_uses_opaque_keyset_pagination_and_returns_public_snapshots_only() -> None:
    older, middle, newer = (record(offset=offset) for offset in range(3))
    client = query_app((older, newer, middle))

    first = client.get("/api/v1/tasks", params={"limit": 2})
    assert first.status_code == 200
    assert first.headers["cache-control"] == "no-store"
    assert [item["taskId"] for item in first.json()["items"]] == [
        str(newer.task_id),
        str(middle.task_id),
    ]
    cursor = first.json()["nextCursor"]
    assert isinstance(cursor, str) and cursor
    assert str(middle.task_id) not in cursor

    second = client.get("/api/v1/tasks", params={"limit": 2, "cursor": cursor})
    assert second.status_code == 200
    assert second.json() == {
        "items": [
            {
                "taskId": str(older.task_id),
                "status": "draft",
                "revision": 1,
                "lastEventSequence": 0,
                "createdAt": "2026-07-18T17:00:00Z",
                "updatedAt": "2026-07-18T17:00:00Z",
            }
        ],
        "nextCursor": None,
    }
    assert "idempotency" not in first.text.lower()
    assert "credential" not in first.text.lower()


def test_detail_and_not_found_responses_do_not_reveal_other_installations() -> None:
    owned = record(offset=0)
    other = record(offset=1, installation_id=InstallationId.new())
    client = query_app((owned, other))

    found = client.get(f"/api/v1/tasks/{owned.task_id}")
    assert found.status_code == 200
    assert found.json()["taskId"] == str(owned.task_id)
    assert found.headers["cache-control"] == "no-store"

    responses = (
        client.get(f"/api/v1/tasks/{other.task_id}"),
        client.get(f"/api/v1/tasks/{TaskId.new()}"),
        client.get("/api/v1/tasks/private-invalid-task"),
    )
    for response in responses:
        assert response.status_code == 404
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["error"]["code"] == "task_not_found"
        assert "private" not in response.text


def test_invalid_pagination_auth_and_unavailable_service_fail_closed() -> None:
    client = query_app(())
    for response in (
        client.get("/api/v1/tasks", params={"limit": 0}),
        client.get("/api/v1/tasks", params={"limit": 101}),
        client.get("/api/v1/tasks", params={"cursor": "private-invalid"}),
    ):
        assert response.status_code == 422
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["error"]["code"] == "validation"
        assert "private" not in response.text

    no_auth = TestClient(create_app(database=None)).get("/api/v1/tasks")
    assert no_auth.status_code == 401
    assert no_auth.json()["error"]["code"] == "installation_access_denied"

    app = create_app(database=None)
    app.dependency_overrides[require_current_installation_access] = lambda: INSTALLATION_ID
    unavailable = TestClient(app).get("/api/v1/tasks")
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "tasks_unavailable"


def test_detail_maps_invalid_authenticated_scope_to_generic_validation_error() -> None:
    owned = record(offset=0)
    app = create_app(
        database=None,
        task_query_service=TaskQueryService(repository=MemoryTaskQueryRepository((owned,))),
    )
    app.dependency_overrides[require_current_installation_access] = lambda: "invalid-scope"

    response = TestClient(app).get(f"/api/v1/tasks/{owned.task_id}")

    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["error"]["code"] == "validation"
    assert "scope" not in response.text
