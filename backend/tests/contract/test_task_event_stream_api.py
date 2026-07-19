from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from automation_tool.control_plane import create_app
from automation_tool.control_plane.api import task_event_stream as task_event_stream_module
from automation_tool.control_plane.api.installation_access import (
    require_current_installation_access,
)
from automation_tool.control_plane.api.task_event_stream import _event_body
from automation_tool.control_plane.application.task_event_stream import (
    TaskEventRecord,
    TaskEventStreamBatch,
    TaskEventStreamService,
)
from automation_tool.control_plane.domain import (
    ActionId,
    ExecutionAttemptId,
    InstallationId,
    SafeTaskEventMessage,
    TaskEventType,
    TaskEventVersion,
    TaskId,
    TaskStatus,
)

NOW = datetime(2026, 7, 18, 20, 0, tzinfo=UTC)
INSTALLATION_ID = InstallationId.new()
TASK_ID = TaskId.new()
ATTEMPT_ID = ExecutionAttemptId.new()
ACTION_ID = ActionId.new()


def event(
    sequence: int,
    *,
    event_type: TaskEventType,
    status: TaskStatus,
    progress_percent: int | None = None,
) -> TaskEventRecord:
    return TaskEventRecord(
        task_id=TASK_ID,
        sequence=sequence,
        event_version=TaskEventVersion.V1,
        event_type=event_type,
        task_revision=sequence + 1,
        task_status=status,
        execution_attempt_id=ATTEMPT_ID,
        action_id=ACTION_ID if event_type.value.startswith("step.") else None,
        progress_percent=progress_percent,
        occurred_at=NOW + timedelta(seconds=sequence),
        recorded_at=NOW + timedelta(seconds=sequence),
        safe_message=SafeTaskEventMessage("公开进度") if progress_percent is not None else None,
    )


EVENTS = (
    event(1, event_type=TaskEventType.TASK_STARTED, status=TaskStatus.RUNNING),
    event(
        2,
        event_type=TaskEventType.STEP_PROGRESS,
        status=TaskStatus.RUNNING,
        progress_percent=50,
    ),
    event(3, event_type=TaskEventType.TASK_COMPLETED, status=TaskStatus.SUCCEEDED),
)


class MemoryRepository:
    def __init__(self) -> None:
        self.calls: list[int] = []
        self.error_after_calls: int | None = None
        self.visible = True
        self.status = TaskStatus.SUCCEEDED
        self.watermark = 3
        self.events: tuple[TaskEventRecord, ...] = EVENTS

    async def read_batch(
        self,
        *,
        installation_id: InstallationId,
        task_id: TaskId,
        after_sequence: int,
        limit: int,
    ) -> TaskEventStreamBatch | None:
        del limit
        self.calls.append(after_sequence)
        if self.error_after_calls is not None and len(self.calls) > self.error_after_calls:
            raise RuntimeError("password=private")
        if not self.visible or installation_id != INSTALLATION_ID or task_id != TASK_ID:
            return None
        available = tuple(item for item in self.events if item.sequence > after_sequence)
        return TaskEventStreamBatch(
            events=available,
            after_sequence=after_sequence,
            task_last_event_sequence=self.watermark,
            task_status=self.status,
        )


class TestRequest:
    __test__ = False

    def __init__(self, disconnects: list[bool], *, maximum: float = 1.0) -> None:
        self._disconnects = disconnects
        self.app = SimpleNamespace(
            state=SimpleNamespace(
                task_event_stream_poll_interval_seconds=0.001,
                task_event_stream_keepalive_interval_seconds=1.0,
                task_event_stream_max_connection_seconds=maximum,
            )
        )
        self.state = SimpleNamespace(request_id="test-request")

    async def is_disconnected(self) -> bool:
        return self._disconnects.pop(0) if self._disconnects else False


def stream_app(
    repository: MemoryRepository,
    *,
    task_event_stream_poll_interval_seconds: float = 0.25,
    task_event_stream_keepalive_interval_seconds: float = 15.0,
    task_event_stream_max_connection_seconds: float = 55.0,
) -> TestClient:
    app = create_app(
        database=None,
        task_event_stream_service=TaskEventStreamService(repository=repository),
        task_event_stream_poll_interval_seconds=task_event_stream_poll_interval_seconds,
        task_event_stream_keepalive_interval_seconds=(task_event_stream_keepalive_interval_seconds),
        task_event_stream_max_connection_seconds=task_event_stream_max_connection_seconds,
    )
    app.dependency_overrides[require_current_installation_access] = lambda: INSTALLATION_ID
    return TestClient(app)


def test_openapi_exposes_app_session_protected_sse_and_standard_resume_header() -> None:
    schema = create_app(database=None).openapi()
    operation = schema["paths"]["/api/v1/tasks/{task_id}/events"]["get"]

    assert operation["operationId"] == "streamTaskEvents"
    assert operation["security"] == [{"AppSession": []}]
    assert operation["responses"]["200"]["content"] == {
        "text/event-stream": {"schema": {"type": "string"}}
    }
    assert [parameter["name"] for parameter in operation["parameters"]] == [
        "task_id",
        "Last-Event-ID",
    ]


def test_stream_emits_only_committed_public_event_frames_and_closes_at_terminal() -> None:
    repository = MemoryRepository()
    response = stream_app(repository).get(
        f"/api/v1/tasks/{TASK_ID}/events",
        headers={"Last-Event-ID": "1"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
    assert response.headers["cache-control"] == "no-store, no-transform"
    assert response.headers["x-accel-buffering"] == "no"
    assert repository.calls == [1]
    frames = response.text.strip().split("\n\n")
    assert frames[0].splitlines()[:2] == ["id: 2", "event: step.progress"]
    assert '"progressPercent":50' in frames[0]
    assert '"message":"公开进度"' in frames[0]
    assert frames[1].splitlines()[:2] == ["id: 3", "event: task.completed"]
    assert '"taskStatus":"succeeded"' in frames[1]
    for private_name in (
        "sourceMessageId",
        "sourceIdempotencyKey",
        "sourceFingerprint",
        "credential",
        "password",
    ):
        assert private_name not in response.text


def test_invalid_cursor_hidden_task_auth_and_unavailable_service_fail_closed() -> None:
    repository = MemoryRepository()
    client = stream_app(repository)

    invalid = client.get(
        f"/api/v1/tasks/{TASK_ID}/events",
        headers={"Last-Event-ID": "4"},
    )
    malformed = client.get(
        f"/api/v1/tasks/{TASK_ID}/events",
        headers={"Last-Event-ID": "private"},
    )
    repository.visible = False
    hidden = client.get(f"/api/v1/tasks/{TASK_ID}/events")

    assert invalid.status_code == 422
    assert malformed.status_code == 422
    assert invalid.json()["error"]["code"] == "validation"
    assert "private" not in malformed.text
    assert hidden.status_code == 404
    assert hidden.json()["error"]["code"] == "task_not_found"

    no_auth = TestClient(create_app(database=None)).get(f"/api/v1/tasks/{TASK_ID}/events")
    assert no_auth.status_code == 401
    assert no_auth.json()["error"]["code"] == "installation_access_denied"

    app = create_app(database=None)
    app.dependency_overrides[require_current_installation_access] = lambda: INSTALLATION_ID
    unavailable = TestClient(app).get(f"/api/v1/tasks/{TASK_ID}/events")
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "task_events_unavailable"


def test_preflight_database_failure_returns_retryable_503_without_private_details() -> None:
    repository = MemoryRepository()
    repository.error_after_calls = 0

    response = stream_app(repository).get(f"/api/v1/tasks/{TASK_ID}/events")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "task_events_unavailable"
    assert response.json()["error"]["retryable"] is True
    assert "private" not in response.text


def test_failure_after_stream_start_closes_for_safe_last_event_reconnect(
    caplog: pytest.LogCaptureFixture,
) -> None:
    repository = MemoryRepository()
    repository.events = EVENTS[:1]
    repository.watermark = 2
    repository.status = TaskStatus.RUNNING
    repository.error_after_calls = 1

    with caplog.at_level(logging.WARNING):
        response = stream_app(repository).get(f"/api/v1/tasks/{TASK_ID}/events")

    assert response.status_code == 200
    assert response.text.count("id: 1") == 1
    assert repository.calls == [0, 1]
    assert "password=private" not in caplog.text
    assert "Task event stream stopped after response start" in caplog.text


def test_nonterminal_idle_stream_sends_comments_then_rotates_the_connection() -> None:
    repository = MemoryRepository()
    repository.events = ()
    repository.watermark = 0
    repository.status = TaskStatus.RUNNING

    response = stream_app(
        repository,
        task_event_stream_poll_interval_seconds=0.001,
        task_event_stream_keepalive_interval_seconds=0.01,
        task_event_stream_max_connection_seconds=0.1,
    ).get(f"/api/v1/tasks/{TASK_ID}/events")

    assert response.status_code == 200
    assert ": keep-alive" in response.text
    assert len(repository.calls) >= 2
    assert set(repository.calls) == {0}


def test_stream_service_rejects_invalid_runtime_configuration() -> None:
    with pytest.raises(ValueError, match="Task event stream timing must be positive"):
        create_app(database=None, task_event_stream_poll_interval_seconds=0.0)
    with pytest.raises(ValueError, match="Task event stream timing must be positive"):
        create_app(
            database=None,
            task_event_stream_keepalive_interval_seconds=float("inf"),
        )
    with pytest.raises(ValueError, match="Task event stream timing must be positive"):
        create_app(database=None, task_event_stream_max_connection_seconds=True)


def test_stream_rejects_an_invalid_authenticated_scope_without_leaking_it() -> None:
    app = create_app(
        database=None,
        task_event_stream_service=TaskEventStreamService(repository=MemoryRepository()),
    )
    app.dependency_overrides[require_current_installation_access] = lambda: "private-scope"

    response = TestClient(app).get(f"/api/v1/tasks/{TASK_ID}/events")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation"
    assert "private-scope" not in response.text


@pytest.mark.asyncio
async def test_body_stops_before_or_after_poll_when_the_client_disconnects() -> None:
    repository = MemoryRepository()
    repository.events = ()
    repository.watermark = 0
    repository.status = TaskStatus.RUNNING
    service = TaskEventStreamService(repository=repository)
    initial = await service.read(
        installation_id=INSTALLATION_ID,
        task_id=str(TASK_ID),
        last_event_id=None,
    )

    before = _event_body(
        request=cast(Request, TestRequest([True])),
        service=service,
        installation_id=INSTALLATION_ID,
        task_id=str(TASK_ID),
        initial=initial,
    )
    after = _event_body(
        request=cast(Request, TestRequest([False, True])),
        service=service,
        installation_id=INSTALLATION_ID,
        task_id=str(TASK_ID),
        initial=initial,
    )
    after_one_poll = _event_body(
        request=cast(Request, TestRequest([False, False, True])),
        service=service,
        installation_id=INSTALLATION_ID,
        task_id=str(TASK_ID),
        initial=initial,
    )

    assert [item async for item in before] == []
    assert [item async for item in after] == []
    assert [item async for item in after_one_poll] == []


@pytest.mark.asyncio
async def test_body_rotates_immediately_when_its_bounded_connection_age_is_reached() -> None:
    repository = MemoryRepository()
    repository.events = ()
    repository.watermark = 0
    repository.status = TaskStatus.RUNNING
    service = TaskEventStreamService(repository=repository)
    initial = await service.read(
        installation_id=INSTALLATION_ID,
        task_id=str(TASK_ID),
        last_event_id=None,
    )
    body = _event_body(
        request=cast(Request, TestRequest([False], maximum=1e-12)),
        service=service,
        installation_id=INSTALLATION_ID,
        task_id=str(TASK_ID),
        initial=initial,
    )

    assert [item async for item in body] == []


@pytest.mark.asyncio
async def test_body_rotates_when_the_bounded_age_is_reached_during_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = MemoryRepository()
    repository.events = ()
    repository.watermark = 0
    repository.status = TaskStatus.RUNNING
    service = TaskEventStreamService(repository=repository)
    initial = await service.read(
        installation_id=INSTALLATION_ID,
        task_id=str(TASK_ID),
        last_event_id=None,
    )
    monotonic_times = iter((0.0, 0.0, 1.0))
    monkeypatch.setattr(task_event_stream_module, "monotonic", monotonic_times.__next__)
    body = _event_body(
        request=cast(Request, TestRequest([False, False], maximum=1.0)),
        service=service,
        installation_id=INSTALLATION_ID,
        task_id=str(TASK_ID),
        initial=initial,
    )

    assert [item async for item in body] == []


@pytest.mark.asyncio
async def test_body_propagates_cancellation_instead_of_logging_a_stream_failure() -> None:
    class CancellationRepository(MemoryRepository):
        async def read_batch(
            self,
            *,
            installation_id: InstallationId,
            task_id: TaskId,
            after_sequence: int,
            limit: int,
        ) -> TaskEventStreamBatch | None:
            del installation_id, task_id, after_sequence, limit
            raise asyncio.CancelledError

    initial = TaskEventStreamBatch(
        events=(EVENTS[0],),
        after_sequence=0,
        task_last_event_sequence=2,
        task_status=TaskStatus.RUNNING,
    )
    body = _event_body(
        request=cast(Request, TestRequest([False])),
        service=TaskEventStreamService(repository=CancellationRepository()),
        installation_id=INSTALLATION_ID,
        task_id=str(TASK_ID),
        initial=initial,
    )

    assert (await anext(body)).startswith("id: 1")
    with pytest.raises(asyncio.CancelledError):
        await anext(body)
