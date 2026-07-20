from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDenialResponse
from starlette.websockets import WebSocketDisconnect

from automation_tool.control_plane import create_app
from automation_tool.control_plane.api.executor_websocket import (
    EXECUTOR_CLOSE_AUTHENTICATION_REJECTED,
    EXECUTOR_CLOSE_CONNECTION_REPLACED,
    EXECUTOR_CLOSE_HELLO_TIMEOUT,
    EXECUTOR_CLOSE_IDENTITY_REJECTED,
    EXECUTOR_CLOSE_INTERNAL_ERROR,
    EXECUTOR_CLOSE_PROTOCOL_REJECTED,
)
from automation_tool.control_plane.application.device_credentials import ParsedDeviceCredential
from automation_tool.control_plane.application.device_sessions import (
    AuthenticatedDeviceSession,
    DeviceSessionCapability,
    DeviceSessionFactory,
    DeviceSessionService,
    IssuedDeviceSession,
    ParsedDeviceSession,
    PendingDeviceSession,
)
from automation_tool.control_plane.application.executor_connection_registry import (
    ExecutorConnectionRegistry,
    ExecutorConnectionRegistryRejected,
    OnlineExecutorConnection,
    StaleExecutorConnection,
)
from automation_tool.control_plane.application.executor_connections import (
    EXECUTOR_WEBSOCKET_SUBPROTOCOL,
    ExecutorConnectionService,
)
from automation_tool.control_plane.application.platform_session_health import (
    PendingPlatformSessionHealth,
    PlatformSessionHealthConvergenceResult,
    PlatformSessionHealthProjection,
    PlatformSessionHealthRejected,
    PlatformSessionHealthService,
    PlatformSessionHealthUnavailable,
    PlatformSessionLogoutGate,
)
from automation_tool.control_plane.application.task_command_delivery import (
    PendingTaskCommand,
    TaskCommandDeliveryRejected,
    TaskCommandDeliveryService,
    TaskCommandRecord,
)
from automation_tool.control_plane.application.task_discovery import (
    TaskDiscoveryBatchAccumulator,
    TaskDiscoveryConvergenceResult,
    TaskDiscoveryConvergenceService,
    TaskDiscoveryRejected,
    TaskDiscoveryUnavailable,
)
from automation_tool.control_plane.application.task_event_convergence import (
    PendingTaskEvent,
    TaskEventConvergenceRejected,
    TaskEventConvergenceResult,
    TaskEventConvergenceService,
    TaskEventConvergenceUnavailable,
)
from automation_tool.control_plane.application.tasks import TaskRecord
from automation_tool.control_plane.domain import (
    ExecutionAttemptId,
    InstallationId,
    TaskCommandResponseType,
    TaskCommandStatus,
    TaskCommandType,
    TaskId,
    TaskSnapshotProjection,
    TaskStatus,
)
from automation_tool.protocol import (
    DouyinCandidate,
    PlatformSessionState,
    TaskCommandResultEnvelope,
    TaskDiscoveryBatchEnvelope,
    TaskDiscoveryCompletedEnvelope,
)

NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
INSTALLATION_ID = UUID("123e4567-e89b-42d3-a456-426614174003")
EXECUTOR_ID = "123e4567-e89b-42d3-a456-426614174004"


@dataclass
class FixedClock:
    def now(self) -> datetime:
        return NOW


@dataclass
class MutableDeliveryClock:
    value: datetime = NOW

    def now(self) -> datetime:
        return self.value


class ContractCommandRepository:
    def __init__(self, command: TaskCommandRecord) -> None:
        self.command = command
        self.fail_dispatch = False
        self.fail_acknowledgement = False

    async def enqueue(self, command: PendingTaskCommand) -> TaskCommandRecord:
        return self.command

    async def expire_due(self, **_values: object) -> int:
        if self.fail_dispatch:
            raise RuntimeError("private dispatch persistence failure")
        return 0

    async def claim_next(
        self,
        *,
        now: datetime,
        lease_expires_at: datetime,
        retry_delivered_before: datetime,
        recover_delivered: bool,
        **_values: object,
    ) -> TaskCommandRecord | None:
        due = self.command.status is TaskCommandStatus.PENDING or (
            self.command.status is TaskCommandStatus.DELIVERED
            and recover_delivered
            and self.command.delivered_at is not None
            and self.command.delivered_at < retry_delivered_before
        )
        if not due:
            return None
        self.command = replace(
            self.command,
            status=TaskCommandStatus.IN_FLIGHT,
            revision=self.command.revision + 1,
            delivery_attempts=self.command.delivery_attempts + 1,
            next_delivery_at=None,
            lease_expires_at=lease_expires_at,
            delivered_at=None,
            updated_at=now,
        )
        return self.command

    async def mark_delivered(
        self,
        *,
        expected_revision: int,
        delivered_at: datetime,
        **_values: object,
    ) -> TaskCommandRecord:
        assert self.command.revision == expected_revision
        self.command = replace(
            self.command,
            status=TaskCommandStatus.DELIVERED,
            revision=self.command.revision + 1,
            lease_expires_at=None,
            delivered_at=delivered_at,
            updated_at=delivered_at,
        )
        return self.command

    async def release_for_retry(self, **_values: object) -> TaskCommandRecord:
        self.command = replace(
            self.command,
            status=TaskCommandStatus.PENDING,
            revision=self.command.revision + 1,
            next_delivery_at=NOW + timedelta(seconds=1),
            lease_expires_at=None,
            delivered_at=None,
        )
        return self.command

    async def acknowledge(
        self,
        *,
        response: TaskCommandResultEnvelope,
        received_at: datetime,
    ) -> TaskCommandRecord:
        if self.fail_acknowledgement:
            raise RuntimeError("private acknowledgement persistence failure")
        if (
            str(response.correlation_id) != str(self.command.correlation_id)
            or str(response.task_id) != str(self.command.task_id)
            or str(response.execution_attempt_id) != str(self.command.execution_attempt_id)
            or response.sequence != self.command.sequence
            or self.command.status
            not in {
                TaskCommandStatus.DELIVERED,
                TaskCommandStatus.ACKNOWLEDGED,
            }
        ):
            raise TaskCommandDeliveryRejected
        if self.command.status is TaskCommandStatus.ACKNOWLEDGED:
            return self.command
        self.command = replace(
            self.command,
            status=TaskCommandStatus.ACKNOWLEDGED,
            revision=self.command.revision + 1,
            acknowledged_at=received_at,
            response_message_id=UUID(str(response.message_id)),
            response_type=TaskCommandResponseType.TASK_ACCEPT,
            updated_at=received_at,
        )
        return self.command


class ContractEventRepository:
    def __init__(self) -> None:
        self.pending: list[PendingTaskEvent] = []
        self.failure: Exception | None = None

    async def converge(self, pending: PendingTaskEvent) -> TaskEventConvergenceResult:
        if self.failure is not None:
            raise self.failure
        self.pending.append(pending)
        return TaskEventConvergenceResult(
            snapshot=TaskSnapshotProjection(
                task_id=TaskId.parse(str(pending.message.task_id)),
                status=pending.target_task_status or TaskStatus.RUNNING,
                revision=2,
                last_event_sequence=pending.message.sequence,
                updated_at=pending.received_at,
            ),
            duplicate=False,
        )


class ContractPlatformSessionRepository:
    def __init__(self) -> None:
        self.pending: list[PendingPlatformSessionHealth] = []
        self.failure: Exception | None = None

    async def converge(
        self,
        pending: PendingPlatformSessionHealth,
    ) -> PlatformSessionHealthConvergenceResult:
        if self.failure is not None:
            raise self.failure
        self.pending.append(pending)
        return PlatformSessionHealthConvergenceResult(
            projection=PlatformSessionHealthProjection(
                installation_id=pending.installation_id,
                platform=pending.platform,
                state=pending.state,
                session_revision=pending.session_revision,
                observed_at=pending.observed_at,
                updated_at=pending.received_at,
            ),
            duplicate=False,
        )

    async def get(
        self,
        installation_id: InstallationId,
        platform: str,
    ) -> PlatformSessionHealthProjection | None:
        return None

    async def begin_logout(
        self,
        installation_id: InstallationId,
        platform: str,
        blocked_at: datetime,
    ) -> PlatformSessionLogoutGate:
        raise AssertionError("not used")


class ContractDiscoveryRepository:
    def __init__(self) -> None:
        self.failure: Exception | None = None
        self.batches: list[TaskDiscoveryBatchEnvelope] = []
        self.completions: list[TaskDiscoveryCompletedEnvelope] = []

    async def authorize_batch(self, message: TaskDiscoveryBatchEnvelope) -> None:
        if self.failure is not None:
            raise self.failure
        self.batches.append(message)

    async def converge(
        self,
        message: TaskDiscoveryCompletedEnvelope,
        *,
        candidates: tuple[DouyinCandidate, ...] | None,
        source_fingerprint: bytes,
        received_at: datetime,
    ) -> TaskDiscoveryConvergenceResult:
        if self.failure is not None:
            raise self.failure
        assert candidates is not None
        assert len(source_fingerprint) == 32
        self.completions.append(message)
        return TaskDiscoveryConvergenceResult(
            task=TaskRecord(
                task_id=TaskId.parse(str(message.task_id)),
                installation_id=InstallationId.parse(str(message.installation_id)),
                status=TaskStatus.AWAITING_CONFIRMATION,
                revision=3,
                last_event_sequence=3,
                created_at=NOW - timedelta(minutes=1),
                updated_at=received_at,
            ),
            duplicate=False,
        )


@dataclass
class SwitchableSessionRepository:
    expected: ParsedDeviceSession
    active: bool = True
    fail: bool = False

    async def issue(
        self,
        *,
        presented_credential: ParsedDeviceCredential,
        pending_session: PendingDeviceSession,
        capability: DeviceSessionCapability,
        issued_at: datetime,
        not_before: datetime,
        expires_at: datetime,
    ) -> IssuedDeviceSession:
        raise AssertionError("not used")

    async def authenticate(self, **values: object) -> AuthenticatedDeviceSession:
        if self.fail:
            raise RuntimeError("private persistence failure")
        presented = cast(ParsedDeviceSession, values["presented_session"])
        capability = cast(DeviceSessionCapability, values["required_capability"])
        if (
            not self.active
            or presented != self.expected
            or capability is not DeviceSessionCapability.EXECUTOR_CONNECT
        ):
            from automation_tool.control_plane.application.device_sessions import (
                DeviceSessionRejected,
            )

            raise DeviceSessionRejected
        return AuthenticatedDeviceSession(
            session_id=presented.session_id,
            installation_id=INSTALLATION_ID,
            credential_id=UUID("123e4567-e89b-42d3-a456-426614174007"),
            credential_version=1,
            capability=capability,
            expires_at=NOW + timedelta(minutes=5),
        )


def app_with_live_session() -> tuple[FastAPI, SwitchableSessionRepository, str]:
    material = DeviceSessionFactory(
        secret_source=secrets.token_bytes,
        id_source=uuid4,
    ).create()
    repository = SwitchableSessionRepository(
        ParsedDeviceSession(
            session_id=material.session_id,
            secret_digest=material.secret_digest,
        )
    )
    sessions = DeviceSessionService(
        repository=repository,
        clock=FixedClock(),
        session_factory=DeviceSessionFactory(
            secret_source=secrets.token_bytes,
            id_source=uuid4,
        ),
    )
    app = create_app(
        database=None,
        executor_connection_service=ExecutorConnectionService(sessions),
        executor_connection_hello_timeout_seconds=0.05,
        executor_connection_recheck_interval_seconds=0.01,
    )
    return app, repository, material.session_token


def app_with_pending_command() -> tuple[
    FastAPI,
    SwitchableSessionRepository,
    str,
    ContractCommandRepository,
    MutableDeliveryClock,
]:
    app, sessions, token = app_with_live_session()
    clock = MutableDeliveryClock()
    pending = PendingTaskCommand(
        message_id=UUID("323e4567-e89b-42d3-a456-426614174001"),
        correlation_id=UUID("323e4567-e89b-42d3-a456-426614174002"),
        installation_id=InstallationId.parse(INSTALLATION_ID),
        task_id=TaskId.parse("123e4567-e89b-42d3-a456-426614174005"),
        execution_attempt_id=ExecutionAttemptId.parse("123e4567-e89b-42d3-a456-426614174006"),
        sequence=1,
        command_type=TaskCommandType.TASK_OFFER,
        idempotency_key="task:offer:attempt:1",
        deadline_at=NOW + timedelta(minutes=5),
        created_at=NOW,
    )
    repository = ContractCommandRepository(TaskCommandRecord.from_pending(pending))
    app.state.task_command_delivery_service = TaskCommandDeliveryService(
        repository=repository,
        registry=app.state.executor_connection_registry,
        clock=clock,
    )
    return app, sessions, token, repository, clock


def app_with_event_service() -> tuple[
    FastAPI,
    SwitchableSessionRepository,
    str,
    ContractEventRepository,
]:
    app, sessions, token = app_with_live_session()
    repository = ContractEventRepository()
    app.state.task_event_convergence_service = TaskEventConvergenceService(
        repository=repository,
        clock=FixedClock(),
    )
    return app, sessions, token, repository


def app_with_platform_session_service() -> tuple[
    FastAPI,
    SwitchableSessionRepository,
    str,
    ContractPlatformSessionRepository,
]:
    app, sessions, token = app_with_live_session()
    repository = ContractPlatformSessionRepository()
    app.state.platform_session_health_service = PlatformSessionHealthService(
        repository=repository,
        clock=FixedClock(),
    )
    return app, sessions, token, repository


def app_with_discovery_service() -> tuple[
    FastAPI,
    SwitchableSessionRepository,
    str,
    ContractDiscoveryRepository,
]:
    app, sessions, token = app_with_live_session()
    repository = ContractDiscoveryRepository()
    app.state.task_discovery_convergence_service = TaskDiscoveryConvergenceService(
        repository=repository,
        accumulator=TaskDiscoveryBatchAccumulator(maximum_attempts=2),
        clock=FixedClock(),
    )
    return app, sessions, token, repository


def hello(
    *,
    installation_id: str = str(INSTALLATION_ID),
    executor_id: str = EXECUTOR_ID,
    sequence: int = 1,
) -> str:
    return json.dumps(
        {
            "protocol_version": "1.0",
            "message_id": "123e4567-e89b-42d3-a456-426614174001",
            "message_type": "executor.hello",
            "sent_at": "2026-07-18T12:00:00Z",
            "deadline_at": "2026-07-18T12:00:30Z",
            "installation_id": installation_id,
            "executor_id": executor_id,
            "correlation_id": "123e4567-e89b-42d3-a456-426614174002",
            "idempotency_key": f"executor:hello:{sequence}",
            "sequence": sequence,
            "payload": {
                "architecture": "arm64",
                "executor_version": "0.1.0",
                "platform": "macos",
            },
        },
        separators=(",", ":"),
    )


def heartbeat(*, executor_id: str = EXECUTOR_ID, sequence: int) -> str:
    return json.dumps(
        {
            "protocol_version": "1.0",
            "message_id": "123e4567-e89b-42d3-a456-426614174009",
            "message_type": "executor.heartbeat",
            "sent_at": "2026-07-18T12:00:01Z",
            "deadline_at": "2026-07-18T12:00:31Z",
            "installation_id": str(INSTALLATION_ID),
            "executor_id": executor_id,
            "correlation_id": "123e4567-e89b-42d3-a456-426614174008",
            "idempotency_key": f"executor:heartbeat:{sequence}",
            "sequence": sequence,
            "payload": {"status": "healthy"},
        },
        separators=(",", ":"),
    )


def discovery_message(message_type: str) -> str:
    is_batch = message_type == "task.discovery_batch"
    return json.dumps(
        {
            "protocol_version": "1.0",
            "message_id": (
                "623e4567-e89b-42d3-a456-426614174001"
                if is_batch
                else "623e4567-e89b-42d3-a456-426614174002"
            ),
            "message_type": message_type,
            "sent_at": "2026-07-18T12:00:00Z",
            "deadline_at": "2026-07-18T12:05:00Z",
            "installation_id": str(INSTALLATION_ID),
            "executor_id": EXECUTOR_ID,
            "correlation_id": "623e4567-e89b-42d3-a456-426614174003",
            "idempotency_key": (
                "task:discovery:batch:1" if is_batch else "task:discovery:completed"
            ),
            "sequence": 1 if is_batch else 2,
            "payload": (
                {
                    "discovery_version": "douyin.discovery.v1",
                    "page_revision": 1,
                    "batch_index": 1,
                    "batch_count": 1,
                    "candidates": [
                        {
                            "candidate_version": "douyin.candidate.v1",
                            "platform_target_id": "author-1",
                            "display_name": "目标一",
                            "public_handle": "target_1",
                            "source": "general_search_author",
                            "page_revision": 1,
                        }
                    ],
                }
                if is_batch
                else {
                    "discovery_version": "douyin.discovery.v1",
                    "outcome": "completed",
                    "evidence": "candidates_extracted",
                    "page_revision": 1,
                    "batch_count": 1,
                    "candidate_count": 1,
                }
            ),
            "task_id": "123e4567-e89b-42d3-a456-426614174005",
            "execution_attempt_id": "123e4567-e89b-42d3-a456-426614174006",
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def command_response(
    offer: dict[str, Any],
    *,
    message_id: str = "423e4567-e89b-42d3-a456-426614174001",
    correlation_id: str | None = None,
) -> str:
    return json.dumps(
        {
            "protocol_version": "1.0",
            "message_id": message_id,
            "message_type": "task.accept",
            "sent_at": "2026-07-18T12:00:01Z",
            "deadline_at": "2026-07-18T12:00:31Z",
            "installation_id": offer["installation_id"],
            "executor_id": offer["executor_id"],
            "correlation_id": correlation_id or offer["correlation_id"],
            "idempotency_key": "task:accept:attempt:1",
            "sequence": offer["sequence"],
            "payload": {"accepted": True},
            "task_id": offer["task_id"],
            "execution_attempt_id": offer["execution_attempt_id"],
        },
        separators=(",", ":"),
    )


def task_event(
    *,
    message_id: str = "523e4567-e89b-42d3-a456-426614174001",
    idempotency_key: str = "task:event:started:1",
) -> str:
    return json.dumps(
        {
            "protocol_version": "1.0",
            "message_id": message_id,
            "message_type": "task.started",
            "sent_at": "2026-07-18T12:00:00Z",
            "deadline_at": "2026-07-18T12:00:30Z",
            "installation_id": str(INSTALLATION_ID),
            "executor_id": EXECUTOR_ID,
            "correlation_id": "323e4567-e89b-42d3-a456-426614174002",
            "idempotency_key": idempotency_key,
            "sequence": 1,
            "payload": {},
            "task_id": "123e4567-e89b-42d3-a456-426614174005",
            "execution_attempt_id": "123e4567-e89b-42d3-a456-426614174006",
        },
        separators=(",", ":"),
    )


def platform_session_health() -> str:
    return json.dumps(
        {
            "protocol_version": "1.0",
            "message_id": "623e4567-e89b-42d3-a456-426614174001",
            "message_type": "platform.session_health",
            "sent_at": "2026-07-18T12:00:00Z",
            "deadline_at": "2026-07-18T12:00:30Z",
            "installation_id": str(INSTALLATION_ID),
            "executor_id": EXECUTOR_ID,
            "correlation_id": "623e4567-e89b-42d3-a456-426614174002",
            "idempotency_key": "platform:douyin:session:1:1",
            "sequence": 2,
            "payload": {
                "platform": "douyin",
                "state": PlatformSessionState.MISSING.value,
                "session_revision": 1,
                "observed_at": "2026-07-18T12:00:00Z",
            },
        },
        separators=(",", ":"),
    )


def wait_for_online(
    client: TestClient,
    registry: ExecutorConnectionRegistry,
    predicate: Any,
) -> OnlineExecutorConnection | None:
    portal = client.portal
    assert portal is not None
    installation_id = InstallationId.parse(INSTALLATION_ID)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        snapshot = portal.call(registry.snapshot, installation_id)
        if predicate(snapshot):
            return snapshot
        time.sleep(0.005)
    raise AssertionError("Executor online projection did not reach the expected state")


def test_executor_websocket_route_is_registered_without_polluting_openapi() -> None:
    app = create_app(database=None)
    websocket_paths = {
        nested.path
        for included in app.routes
        for nested in getattr(
            getattr(included, "original_router", None),
            "routes",
            (),
        )
        if nested.__class__.__name__ == "APIWebSocketRoute"
    }

    assert websocket_paths == {"/api/v1/executors/connect"}
    assert "/api/v1/executors/connect" not in app.openapi()["paths"]


def test_command_offer_is_redelivered_on_reconnect_and_only_executor_ack_terminates() -> None:
    app, _, token, repository, clock = app_with_pending_command()
    registry = app.state.executor_connection_registry

    with TestClient(app) as client:
        with client.websocket_connect(
            "/api/v1/executors/connect",
            headers={"authorization": f"Bearer {token}"},
            subprotocols=[EXECUTOR_WEBSOCKET_SUBPROTOCOL],
        ) as first:
            first.send_text(hello())
            first_offer = json.loads(first.receive_text())
            assert first_offer["message_type"] == "task.offer"
            assert repository.command.status is TaskCommandStatus.DELIVERED
            assert repository.command.response_message_id is None

        clock.value = NOW + timedelta(seconds=1)
        with client.websocket_connect(
            "/api/v1/executors/connect",
            headers={"authorization": f"Bearer {token}"},
            subprotocols=[EXECUTOR_WEBSOCKET_SUBPROTOCOL],
        ) as second:
            second.send_text(hello())
            replayed_offer = json.loads(second.receive_text())
            assert replayed_offer["message_id"] == first_offer["message_id"]
            assert replayed_offer["idempotency_key"] == first_offer["idempotency_key"]
            assert repository.command.delivery_attempts == 2
            assert repository.command.status is TaskCommandStatus.DELIVERED

            clock.value = NOW + timedelta(seconds=2)
            second.send_text(command_response(replayed_offer))
            second.send_text(heartbeat(sequence=2))
            wait_for_online(
                client,
                registry,
                lambda value: value is not None and value.last_sequence == 2,
            )
            assert repository.command.status.value == TaskCommandStatus.ACKNOWLEDGED.value
            response_id = repository.command.response_message_id

            clock.value = NOW + timedelta(seconds=3)
            second.send_text(
                command_response(
                    replayed_offer,
                    message_id="523e4567-e89b-42d3-a456-426614174001",
                )
            )
            second.send_text(heartbeat(sequence=3))
            wait_for_online(
                client,
                registry,
                lambda value: value is not None and value.last_sequence == 3,
            )
            assert repository.command.response_message_id == response_id


def test_task_event_uses_the_wired_convergence_service_and_keeps_connection_open() -> None:
    app, _, token, repository = app_with_event_service()
    registry = app.state.executor_connection_registry

    with (
        TestClient(app) as client,
        client.websocket_connect(
            "/api/v1/executors/connect",
            headers={"authorization": f"Bearer {token}"},
            subprotocols=[EXECUTOR_WEBSOCKET_SUBPROTOCOL],
        ) as websocket,
    ):
        websocket.send_text(hello())
        websocket.send_text(task_event())
        websocket.send_text(heartbeat(sequence=2))
        wait_for_online(
            client,
            registry,
            lambda value: value is not None and value.last_sequence == 2,
        )

    assert len(repository.pending) == 1
    assert repository.pending[0].message.message_type == "task.started"


def test_platform_session_health_uses_wired_service_and_keeps_connection_open() -> None:
    app, _, token, repository = app_with_platform_session_service()
    registry = app.state.executor_connection_registry

    with (
        TestClient(app) as client,
        client.websocket_connect(
            "/api/v1/executors/connect",
            headers={"authorization": f"Bearer {token}"},
            subprotocols=[EXECUTOR_WEBSOCKET_SUBPROTOCOL],
        ) as websocket,
    ):
        websocket.send_text(hello())
        websocket.send_text(platform_session_health())
        websocket.send_text(heartbeat(sequence=3))
        wait_for_online(
            client,
            registry,
            lambda value: value is not None and value.last_sequence == 3,
        )

    assert len(repository.pending) == 1
    assert repository.pending[0].state is PlatformSessionState.MISSING


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    (
        (PlatformSessionHealthRejected(), EXECUTOR_CLOSE_PROTOCOL_REJECTED),
        (PlatformSessionHealthUnavailable(), EXECUTOR_CLOSE_INTERNAL_ERROR),
    ),
)
def test_platform_session_health_failures_close_with_safe_reason(
    failure: Exception,
    expected_code: int,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app, _, token, repository = app_with_platform_session_service()
    repository.failure = failure

    with TestClient(app).websocket_connect(
        "/api/v1/executors/connect",
        headers={"authorization": f"Bearer {token}"},
        subprotocols=[EXECUTOR_WEBSOCKET_SUBPROTOCOL],
    ) as websocket:
        websocket.send_text(hello())
        websocket.send_text(platform_session_health())
        with pytest.raises(WebSocketDisconnect) as captured:
            websocket.receive_text()

    assert captured.value.code == expected_code
    assert "private" not in captured.value.reason
    assert "private" not in caplog.text


@pytest.mark.parametrize(
    ("health_service", "expected_code"),
    ((None, EXECUTOR_CLOSE_PROTOCOL_REJECTED), (object(), EXECUTOR_CLOSE_INTERNAL_ERROR)),
)
def test_platform_session_health_requires_a_valid_wired_service(
    health_service: object,
    expected_code: int,
) -> None:
    app, _, token = app_with_live_session()
    app.state.platform_session_health_service = health_service

    with TestClient(app).websocket_connect(
        "/api/v1/executors/connect",
        headers={"authorization": f"Bearer {token}"},
        subprotocols=[EXECUTOR_WEBSOCKET_SUBPROTOCOL],
    ) as websocket:
        websocket.send_text(hello())
        websocket.send_text(platform_session_health())
        with pytest.raises(WebSocketDisconnect) as captured:
            websocket.receive_text()

    assert captured.value.code == expected_code


def test_unexpected_platform_session_health_failure_is_internal_and_safe(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app, _, token, _ = app_with_platform_session_service()

    async def fail(*_values: object, **_kwargs: object) -> None:
        raise RuntimeError("private unexpected convergence failure")

    monkeypatch.setattr(PlatformSessionHealthService, "receive", fail)
    with TestClient(app).websocket_connect(
        "/api/v1/executors/connect",
        headers={"authorization": f"Bearer {token}"},
        subprotocols=[EXECUTOR_WEBSOCKET_SUBPROTOCOL],
    ) as websocket:
        websocket.send_text(hello())
        websocket.send_text(platform_session_health())
        with pytest.raises(WebSocketDisconnect) as captured:
            websocket.receive_text()

    assert captured.value.code == EXECUTOR_CLOSE_INTERNAL_ERROR
    assert "private" not in captured.value.reason
    assert "private unexpected convergence failure" not in caplog.text


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    (
        (TaskEventConvergenceRejected(), EXECUTOR_CLOSE_PROTOCOL_REJECTED),
        (TaskEventConvergenceUnavailable(), EXECUTOR_CLOSE_INTERNAL_ERROR),
        (RuntimeError("private event persistence failure"), EXECUTOR_CLOSE_INTERNAL_ERROR),
    ),
)
def test_task_event_failures_close_with_safe_protocol_or_internal_reason(
    failure: Exception,
    expected_code: int,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app, _, token, repository = app_with_event_service()
    repository.failure = failure

    with TestClient(app).websocket_connect(
        "/api/v1/executors/connect",
        headers={"authorization": f"Bearer {token}"},
        subprotocols=[EXECUTOR_WEBSOCKET_SUBPROTOCOL],
    ) as websocket:
        websocket.send_text(hello())
        websocket.send_text(task_event())
        with pytest.raises(WebSocketDisconnect) as captured:
            websocket.receive_text()

    assert captured.value.code == expected_code
    assert "private" not in captured.value.reason
    assert "private event persistence failure" not in caplog.text


@pytest.mark.parametrize(
    ("event_service", "expected_code"),
    ((None, EXECUTOR_CLOSE_PROTOCOL_REJECTED), (object(), EXECUTOR_CLOSE_INTERNAL_ERROR)),
)
def test_task_event_requires_a_valid_wired_convergence_service(
    event_service: object,
    expected_code: int,
) -> None:
    app, _, token = app_with_live_session()
    app.state.task_event_convergence_service = event_service

    with TestClient(app).websocket_connect(
        "/api/v1/executors/connect",
        headers={"authorization": f"Bearer {token}"},
        subprotocols=[EXECUTOR_WEBSOCKET_SUBPROTOCOL],
    ) as websocket:
        websocket.send_text(hello())
        websocket.send_text(task_event())
        with pytest.raises(WebSocketDisconnect) as captured:
            websocket.receive_text()

    assert captured.value.code == expected_code


def test_unexpected_task_event_service_failure_closes_without_private_details(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app, _, token, _ = app_with_event_service()

    async def fail(*_values: object) -> None:
        raise RuntimeError("private unexpected convergence failure")

    monkeypatch.setattr(TaskEventConvergenceService, "receive", fail)
    with TestClient(app).websocket_connect(
        "/api/v1/executors/connect",
        headers={"authorization": f"Bearer {token}"},
        subprotocols=[EXECUTOR_WEBSOCKET_SUBPROTOCOL],
    ) as websocket:
        websocket.send_text(hello())
        websocket.send_text(task_event())
        with pytest.raises(WebSocketDisconnect) as captured:
            websocket.receive_text()

    assert captured.value.code == EXECUTOR_CLOSE_INTERNAL_ERROR
    assert "private" not in captured.value.reason
    assert "private unexpected convergence failure" not in caplog.text


def test_task_discovery_batches_and_completion_use_the_wired_convergence_service() -> None:
    app, _, token, repository = app_with_discovery_service()

    with (
        TestClient(app) as client,
        client.websocket_connect(
            "/api/v1/executors/connect",
            headers={"authorization": f"Bearer {token}"},
            subprotocols=[EXECUTOR_WEBSOCKET_SUBPROTOCOL],
        ) as websocket,
    ):
        websocket.send_text(hello())
        websocket.send_text(discovery_message("task.discovery_batch"))
        websocket.send_text(discovery_message("task.discovery_completed"))
        websocket.send_text(heartbeat(sequence=2))
        wait_for_online(
            client,
            app.state.executor_connection_registry,
            lambda value: value is not None and value.last_sequence == 2,
        )

    assert len(repository.batches) == 1
    assert len(repository.completions) == 1


@pytest.mark.parametrize(
    ("discovery_service", "expected_code"),
    ((None, EXECUTOR_CLOSE_PROTOCOL_REJECTED), (object(), EXECUTOR_CLOSE_INTERNAL_ERROR)),
)
def test_task_discovery_requires_a_valid_wired_convergence_service(
    discovery_service: object,
    expected_code: int,
) -> None:
    app, _, token = app_with_live_session()
    app.state.task_discovery_convergence_service = discovery_service

    with TestClient(app).websocket_connect(
        "/api/v1/executors/connect",
        headers={"authorization": f"Bearer {token}"},
        subprotocols=[EXECUTOR_WEBSOCKET_SUBPROTOCOL],
    ) as websocket:
        websocket.send_text(hello())
        websocket.send_text(discovery_message("task.discovery_batch"))
        with pytest.raises(WebSocketDisconnect) as captured:
            websocket.receive_text()

    assert captured.value.code == expected_code


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    (
        (TaskDiscoveryRejected(), EXECUTOR_CLOSE_PROTOCOL_REJECTED),
        (TaskDiscoveryUnavailable(), EXECUTOR_CLOSE_INTERNAL_ERROR),
    ),
)
def test_task_discovery_failures_close_with_safe_reason(
    failure: Exception,
    expected_code: int,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app, _, token, repository = app_with_discovery_service()
    repository.failure = failure

    with TestClient(app).websocket_connect(
        "/api/v1/executors/connect",
        headers={"authorization": f"Bearer {token}"},
        subprotocols=[EXECUTOR_WEBSOCKET_SUBPROTOCOL],
    ) as websocket:
        websocket.send_text(hello())
        websocket.send_text(discovery_message("task.discovery_batch"))
        with pytest.raises(WebSocketDisconnect) as captured:
            websocket.receive_text()

    assert captured.value.code == expected_code
    assert "private" not in captured.value.reason
    assert "private" not in caplog.text


def test_unexpected_task_discovery_failure_is_internal_and_safe(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app, _, token, _ = app_with_discovery_service()

    async def fail(*_values: object) -> None:
        raise RuntimeError("private discovery convergence failure")

    monkeypatch.setattr(TaskDiscoveryConvergenceService, "receive_batch", fail)
    with TestClient(app).websocket_connect(
        "/api/v1/executors/connect",
        headers={"authorization": f"Bearer {token}"},
        subprotocols=[EXECUTOR_WEBSOCKET_SUBPROTOCOL],
    ) as websocket:
        websocket.send_text(hello())
        websocket.send_text(discovery_message("task.discovery_batch"))
        with pytest.raises(WebSocketDisconnect) as captured:
            websocket.receive_text()

    assert captured.value.code == EXECUTOR_CLOSE_INTERNAL_ERROR
    assert "private" not in captured.value.reason
    assert "private discovery convergence failure" not in caplog.text


def test_idle_receive_timeout_keeps_the_authenticated_connection_alive() -> None:
    app, _, token = app_with_live_session()
    registry = app.state.executor_connection_registry

    with (
        TestClient(app) as client,
        client.websocket_connect(
            "/api/v1/executors/connect",
            headers={"authorization": f"Bearer {token}"},
            subprotocols=[EXECUTOR_WEBSOCKET_SUBPROTOCOL],
        ) as websocket,
    ):
        websocket.send_text(hello())
        time.sleep(0.03)
        websocket.send_text(heartbeat(sequence=2))
        wait_for_online(
            client,
            registry,
            lambda value: value is not None and value.last_sequence == 2,
        )


def test_unmatched_command_result_is_protocol_rejected() -> None:
    app, _, token, _, _ = app_with_pending_command()

    with TestClient(app).websocket_connect(
        "/api/v1/executors/connect",
        headers={"authorization": f"Bearer {token}"},
        subprotocols=[EXECUTOR_WEBSOCKET_SUBPROTOCOL],
    ) as websocket:
        websocket.send_text(hello())
        offer = json.loads(websocket.receive_text())
        websocket.send_text(
            command_response(
                offer,
                correlation_id="623e4567-e89b-42d3-a456-426614174001",
            )
        )
        with pytest.raises(WebSocketDisconnect) as captured:
            websocket.receive_text()

    assert captured.value.code == EXECUTOR_CLOSE_PROTOCOL_REJECTED
    assert captured.value.reason == "Executor protocol is rejected"


def test_command_result_requires_a_wired_delivery_service() -> None:
    app, _, token = app_with_live_session()
    offer = {
        "installation_id": str(INSTALLATION_ID),
        "executor_id": EXECUTOR_ID,
        "correlation_id": "323e4567-e89b-42d3-a456-426614174002",
        "sequence": 1,
        "task_id": "123e4567-e89b-42d3-a456-426614174005",
        "execution_attempt_id": "123e4567-e89b-42d3-a456-426614174006",
    }

    with TestClient(app).websocket_connect(
        "/api/v1/executors/connect",
        headers={"authorization": f"Bearer {token}"},
        subprotocols=[EXECUTOR_WEBSOCKET_SUBPROTOCOL],
    ) as websocket:
        websocket.send_text(hello())
        websocket.send_text(command_response(offer))
        with pytest.raises(WebSocketDisconnect) as captured:
            websocket.receive_text()

    assert captured.value.code == EXECUTOR_CLOSE_PROTOCOL_REJECTED


def test_invalid_delivery_service_wiring_closes_safely() -> None:
    app, _, token = app_with_live_session()
    app.state.task_command_delivery_service = object()

    with TestClient(app).websocket_connect(
        "/api/v1/executors/connect",
        headers={"authorization": f"Bearer {token}"},
        subprotocols=[EXECUTOR_WEBSOCKET_SUBPROTOCOL],
    ) as websocket:
        websocket.send_text(hello())
        with pytest.raises(WebSocketDisconnect) as captured:
            websocket.receive_text()

    assert captured.value.code == EXECUTOR_CLOSE_INTERNAL_ERROR
    assert captured.value.reason == "Executor connection failed"


@pytest.mark.parametrize("failure_point", ["dispatch", "acknowledgement"])
def test_command_persistence_failures_close_as_safe_internal_errors(
    failure_point: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app, _, token, repository, _ = app_with_pending_command()
    repository.fail_dispatch = failure_point == "dispatch"

    with TestClient(app).websocket_connect(
        "/api/v1/executors/connect",
        headers={"authorization": f"Bearer {token}"},
        subprotocols=[EXECUTOR_WEBSOCKET_SUBPROTOCOL],
    ) as websocket:
        websocket.send_text(hello())
        if failure_point == "acknowledgement":
            offer = json.loads(websocket.receive_text())
            repository.fail_acknowledgement = True
            websocket.send_text(command_response(offer))
        with pytest.raises(WebSocketDisconnect) as captured:
            websocket.receive_text()

    assert captured.value.code == EXECUTOR_CLOSE_INTERNAL_ERROR
    assert captured.value.reason == "Executor connection failed"
    assert "private" not in captured.value.reason
    assert "private dispatch persistence failure" not in caplog.text
    assert "private acknowledgement persistence failure" not in caplog.text


def test_upgrade_requires_one_exact_subprotocol_and_valid_executor_session() -> None:
    app, _, token = app_with_live_session()
    client = TestClient(app)
    rejected: tuple[dict[str, Any], ...] = (
        {},
        {"subprotocols": [EXECUTOR_WEBSOCKET_SUBPROTOCOL]},
        {
            "headers": {"authorization": "Bearer private invalid session"},
            "subprotocols": [EXECUTOR_WEBSOCKET_SUBPROTOCOL],
        },
        {"headers": {"authorization": "Bearer private-invalid-session"}},
        {
            "headers": {"authorization": f"Bearer {token}"},
            "subprotocols": ["automation-tool.executor.future"],
        },
    )

    for options in rejected:
        with (
            pytest.raises(WebSocketDenialResponse) as captured,
            client.websocket_connect("/api/v1/executors/connect", **options),
        ):
            pass
        assert captured.value.status_code == 403
        assert "private-invalid-session" not in captured.value.text
        assert captured.value.headers["cache-control"] == "no-store"


def test_upgrade_maps_explicit_connection_rejection_to_forbidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, token = app_with_live_session()

    async def reject(*_values: object) -> None:
        from automation_tool.control_plane.application.executor_connections import (
            ExecutorConnectionRejected,
        )

        raise ExecutorConnectionRejected

    monkeypatch.setattr(ExecutorConnectionService, "authorize", reject)
    with (
        pytest.raises(WebSocketDenialResponse) as captured,
        TestClient(app).websocket_connect(
            "/api/v1/executors/connect",
            headers={"authorization": f"Bearer {token}"},
            subprotocols=[EXECUTOR_WEBSOCKET_SUBPROTOCOL],
        ),
    ):
        pass

    assert captured.value.status_code == 403
    assert captured.value.headers["cache-control"] == "no-store"


def test_upgrade_is_retryably_denied_when_connection_service_is_unavailable() -> None:
    with (
        pytest.raises(WebSocketDenialResponse) as captured,
        TestClient(create_app(database=None)).websocket_connect(
            "/api/v1/executors/connect",
            headers={"authorization": "Bearer private-session"},
            subprotocols=[EXECUTOR_WEBSOCKET_SUBPROTOCOL],
        ),
    ):
        pass

    assert captured.value.status_code == 503
    assert captured.value.headers["cache-control"] == "no-store"
    assert "private-session" not in captured.value.text


def test_upgrade_is_retryably_denied_when_registry_is_unavailable() -> None:
    app, _, token = app_with_live_session()
    app.state.executor_connection_registry = None
    with (
        pytest.raises(WebSocketDenialResponse) as captured,
        TestClient(app).websocket_connect(
            "/api/v1/executors/connect",
            headers={"authorization": f"Bearer {token}"},
            subprotocols=[EXECUTOR_WEBSOCKET_SUBPROTOCOL],
        ),
    ):
        pass

    assert captured.value.status_code == 503
    assert captured.value.headers["cache-control"] == "no-store"


def test_unexpected_upgrade_authentication_failure_is_retryably_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, token = app_with_live_session()

    async def fail_authorize(*_values: object) -> None:
        raise RuntimeError("private authentication failure")

    monkeypatch.setattr(ExecutorConnectionService, "authorize", fail_authorize)
    with (
        pytest.raises(WebSocketDenialResponse) as captured,
        TestClient(app).websocket_connect(
            "/api/v1/executors/connect",
            headers={"authorization": f"Bearer {token}"},
            subprotocols=[EXECUTOR_WEBSOCKET_SUBPROTOCOL],
        ),
    ):
        pass

    assert captured.value.status_code == 503
    assert captured.value.headers["cache-control"] == "no-store"
    assert token not in captured.value.text


def test_hello_timeout_closes_with_one_fixed_public_reason() -> None:
    app, _, token = app_with_live_session()

    with (
        TestClient(app).websocket_connect(
            "/api/v1/executors/connect",
            headers={"authorization": f"Bearer {token}"},
            subprotocols=[EXECUTOR_WEBSOCKET_SUBPROTOCOL],
        ) as websocket,
        pytest.raises(WebSocketDisconnect) as captured,
    ):
        websocket.receive_text()

    assert captured.value.code == EXECUTOR_CLOSE_HELLO_TIMEOUT
    assert captured.value.reason == "Executor hello timed out"


def test_bearer_header_is_removed_before_long_lived_upgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, token = app_with_live_session()
    original_accept = WebSocket.accept
    authorization_removed = False

    async def inspected_accept(
        websocket: WebSocket,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        nonlocal authorization_removed
        authorization_removed = all(
            name.lower() != b"authorization" for name, _ in websocket.scope["headers"]
        )
        await original_accept(websocket, *args, **kwargs)

    monkeypatch.setattr(WebSocket, "accept", inspected_accept)
    with TestClient(app).websocket_connect(
        "/api/v1/executors/connect",
        headers={"authorization": f"Bearer {token}"},
        subprotocols=[EXECUTOR_WEBSOCKET_SUBPROTOCOL],
    ) as websocket:
        websocket.send_text(hello())

    assert authorization_removed is True


def test_executor_can_disconnect_before_sending_hello() -> None:
    app, _, token = app_with_live_session()

    with TestClient(app).websocket_connect(
        "/api/v1/executors/connect",
        headers={"authorization": f"Bearer {token}"},
        subprotocols=[EXECUTOR_WEBSOCKET_SUBPROTOCOL],
    ):
        pass


def test_first_frame_must_be_text() -> None:
    app, _, token = app_with_live_session()

    with TestClient(app).websocket_connect(
        "/api/v1/executors/connect",
        headers={"authorization": f"Bearer {token}"},
        subprotocols=[EXECUTOR_WEBSOCKET_SUBPROTOCOL],
    ) as websocket:
        websocket.send_bytes(b"private-binary-hello")
        with pytest.raises(WebSocketDisconnect) as captured:
            websocket.receive_text()

    assert captured.value.code == EXECUTOR_CLOSE_PROTOCOL_REJECTED
    assert captured.value.reason == "Executor protocol is rejected"


def test_hello_binds_selected_subprotocol_and_impersonation_closes_safely() -> None:
    app, _, token = app_with_live_session()

    with TestClient(app).websocket_connect(
        "/api/v1/executors/connect",
        headers={"authorization": f"Bearer {token}"},
        subprotocols=[EXECUTOR_WEBSOCKET_SUBPROTOCOL],
    ) as websocket:
        assert websocket.accepted_subprotocol == EXECUTOR_WEBSOCKET_SUBPROTOCOL
        websocket.send_text(hello(installation_id=str(uuid4())))
        with pytest.raises(WebSocketDisconnect) as captured:
            websocket.receive_text()

    assert captured.value.code == EXECUTOR_CLOSE_IDENTITY_REJECTED
    assert captured.value.reason == "Executor identity is rejected"
    assert token not in captured.value.reason


def test_unexpected_hello_binding_failure_closes_without_private_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, token = app_with_live_session()

    def fail_binding(*_values: object) -> None:
        raise RuntimeError("private binding failure")

    monkeypatch.setattr(ExecutorConnectionService, "bind_hello", fail_binding)
    with TestClient(app).websocket_connect(
        "/api/v1/executors/connect",
        headers={"authorization": f"Bearer {token}"},
        subprotocols=[EXECUTOR_WEBSOCKET_SUBPROTOCOL],
    ) as websocket:
        websocket.send_text(hello())
        with pytest.raises(WebSocketDisconnect) as captured:
            websocket.receive_text()

    assert captured.value.code == EXECUTOR_CLOSE_INTERNAL_ERROR
    assert captured.value.reason == "Executor connection failed"
    assert "binding" not in captured.value.reason


def test_live_connection_is_closed_when_periodic_session_revalidation_fails() -> None:
    app, repository, token = app_with_live_session()

    with TestClient(app).websocket_connect(
        "/api/v1/executors/connect",
        headers={"authorization": f"Bearer {token}"},
        subprotocols=[EXECUTOR_WEBSOCKET_SUBPROTOCOL],
    ) as websocket:
        websocket.send_text(hello())
        repository.active = False
        with pytest.raises(WebSocketDisconnect) as captured:
            websocket.receive_text()

    assert captured.value.code == EXECUTOR_CLOSE_AUTHENTICATION_REJECTED
    assert captured.value.reason == "Executor authentication is rejected"
    assert token not in captured.value.reason


def test_bound_executor_can_disconnect_cleanly() -> None:
    app, _, token = app_with_live_session()

    with TestClient(app).websocket_connect(
        "/api/v1/executors/connect",
        headers={"authorization": f"Bearer {token}"},
        subprotocols=[EXECUTOR_WEBSOCKET_SUBPROTOCOL],
    ) as websocket:
        websocket.send_text(hello())


def test_new_hello_replaces_old_installation_connection_and_heartbeat_projects_online() -> None:
    app, _, token = app_with_live_session()
    registry = cast(ExecutorConnectionRegistry, app.state.executor_connection_registry)
    headers = {"authorization": f"Bearer {token}"}
    replacement_executor_id = str(uuid4())

    with TestClient(app) as client:
        with client.websocket_connect(
            "/api/v1/executors/connect",
            headers=headers,
            subprotocols=[EXECUTOR_WEBSOCKET_SUBPROTOCOL],
        ) as first:
            first.send_text(hello())
            first_online = wait_for_online(client, registry, lambda value: value is not None)
            assert first_online is not None

            with client.websocket_connect(
                "/api/v1/executors/connect",
                headers=headers,
                subprotocols=[EXECUTOR_WEBSOCKET_SUBPROTOCOL],
            ) as second:
                second.send_text(hello(executor_id=replacement_executor_id))
                replacement = wait_for_online(
                    client,
                    registry,
                    lambda value: (
                        value is not None and value.connection_id != first_online.connection_id
                    ),
                )
                assert replacement is not None
                assert str(replacement.executor_id) == replacement_executor_id
                with pytest.raises(WebSocketDisconnect) as replaced:
                    first.receive_text()
                assert replaced.value.code == EXECUTOR_CLOSE_CONNECTION_REPLACED
                assert replaced.value.reason == "Executor connection was replaced"

                second.send_text(heartbeat(executor_id=replacement_executor_id, sequence=2))
                heartbeat_projection = wait_for_online(
                    client,
                    registry,
                    lambda value: value is not None and value.last_sequence == 2,
                )
                assert heartbeat_projection is not None
                assert heartbeat_projection.connected_at <= heartbeat_projection.last_heartbeat_at

                second.send_text(heartbeat(executor_id=replacement_executor_id, sequence=2))
                with pytest.raises(WebSocketDisconnect) as duplicate:
                    second.receive_text()
                assert duplicate.value.code == EXECUTOR_CLOSE_PROTOCOL_REJECTED
                assert duplicate.value.reason == "Executor protocol is rejected"

        assert wait_for_online(client, registry, lambda value: value is None) is None


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    (
        (ExecutorConnectionRegistryRejected(), EXECUTOR_CLOSE_INTERNAL_ERROR),
        (RuntimeError("private registration failure"), EXECUTOR_CLOSE_INTERNAL_ERROR),
    ),
)
def test_registry_registration_failures_close_without_private_details(
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    expected_code: int,
) -> None:
    app, _, token = app_with_live_session()

    async def fail_registration(*_values: object) -> None:
        raise failure

    monkeypatch.setattr(ExecutorConnectionRegistry, "register", fail_registration)
    with TestClient(app).websocket_connect(
        "/api/v1/executors/connect",
        headers={"authorization": f"Bearer {token}"},
        subprotocols=[EXECUTOR_WEBSOCKET_SUBPROTOCOL],
    ) as websocket:
        websocket.send_text(hello())
        with pytest.raises(WebSocketDisconnect) as captured:
            websocket.receive_text()

    assert captured.value.code == expected_code
    assert captured.value.reason == "Executor connection failed"
    assert "private" not in captured.value.reason


@pytest.mark.parametrize(
    ("current", "expected_code", "expected_reason"),
    (
        (False, EXECUTOR_CLOSE_CONNECTION_REPLACED, "Executor connection was replaced"),
        (
            RuntimeError("private current failure"),
            EXECUTOR_CLOSE_INTERNAL_ERROR,
            "Executor connection failed",
        ),
    ),
)
def test_registry_current_check_failures_close_safely(
    monkeypatch: pytest.MonkeyPatch,
    current: bool | Exception,
    expected_code: int,
    expected_reason: str,
) -> None:
    app, _, token = app_with_live_session()

    async def check_current(*_values: object) -> bool:
        if isinstance(current, Exception):
            raise current
        return current

    monkeypatch.setattr(ExecutorConnectionRegistry, "is_current", check_current)
    with TestClient(app).websocket_connect(
        "/api/v1/executors/connect",
        headers={"authorization": f"Bearer {token}"},
        subprotocols=[EXECUTOR_WEBSOCKET_SUBPROTOCOL],
    ) as websocket:
        websocket.send_text(hello())
        with pytest.raises(WebSocketDisconnect) as captured:
            websocket.receive_text()

    assert captured.value.code == expected_code
    assert captured.value.reason == expected_reason
    assert "private" not in captured.value.reason


@pytest.mark.parametrize(
    ("failure", "expected_code", "expected_reason"),
    (
        (
            StaleExecutorConnection(),
            EXECUTOR_CLOSE_CONNECTION_REPLACED,
            "Executor connection was replaced",
        ),
        (
            RuntimeError("private heartbeat projection failure"),
            EXECUTOR_CLOSE_INTERNAL_ERROR,
            "Executor connection failed",
        ),
    ),
)
def test_registry_heartbeat_projection_failures_close_safely(
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    expected_code: int,
    expected_reason: str,
) -> None:
    app, _, token = app_with_live_session()

    async def fail_heartbeat(*_values: object, **_named: object) -> None:
        raise failure

    monkeypatch.setattr(ExecutorConnectionRegistry, "record_heartbeat", fail_heartbeat)
    with TestClient(app).websocket_connect(
        "/api/v1/executors/connect",
        headers={"authorization": f"Bearer {token}"},
        subprotocols=[EXECUTOR_WEBSOCKET_SUBPROTOCOL],
    ) as websocket:
        websocket.send_text(hello())
        websocket.send_text(heartbeat(sequence=2))
        with pytest.raises(WebSocketDisconnect) as captured:
            websocket.receive_text()

    assert captured.value.code == expected_code
    assert captured.value.reason == expected_reason
    assert "private" not in captured.value.reason


def test_registry_cleanup_failure_is_logged_without_reaching_the_client(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app, _, token = app_with_live_session()

    async def fail_cleanup(*_values: object) -> None:
        raise RuntimeError("private cleanup failure")

    monkeypatch.setattr(ExecutorConnectionRegistry, "unregister", fail_cleanup)
    with TestClient(app).websocket_connect(
        "/api/v1/executors/connect",
        headers={"authorization": f"Bearer {token}"},
        subprotocols=[EXECUTOR_WEBSOCKET_SUBPROTOCOL],
    ) as websocket:
        websocket.send_text(hello())

    assert "Executor WebSocket registry cleanup failed" in caplog.text
    assert "private cleanup failure" not in caplog.text


@pytest.mark.parametrize("invalid_frame", ["binary", "malformed-heartbeat"])
def test_bound_connection_rejects_non_text_or_invalid_lifecycle_frames(
    invalid_frame: str,
) -> None:
    app, _, token = app_with_live_session()

    with TestClient(app).websocket_connect(
        "/api/v1/executors/connect",
        headers={"authorization": f"Bearer {token}"},
        subprotocols=[EXECUTOR_WEBSOCKET_SUBPROTOCOL],
    ) as websocket:
        websocket.send_text(hello())
        if invalid_frame == "binary":
            websocket.send_bytes(b"private-binary-value")
        else:
            websocket.send_text('{"private":"malformed-heartbeat"}')
        with pytest.raises(WebSocketDisconnect) as captured:
            websocket.receive_text()

    assert captured.value.code == EXECUTOR_CLOSE_PROTOCOL_REJECTED
    assert captured.value.reason == "Executor protocol is rejected"
    assert "private" not in captured.value.reason


def test_unexpected_reauthentication_failure_closes_without_private_details() -> None:
    app, repository, token = app_with_live_session()

    with TestClient(app).websocket_connect(
        "/api/v1/executors/connect",
        headers={"authorization": f"Bearer {token}"},
        subprotocols=[EXECUTOR_WEBSOCKET_SUBPROTOCOL],
    ) as websocket:
        websocket.send_text(hello())
        repository.fail = True
        with pytest.raises(WebSocketDisconnect) as captured:
            websocket.receive_text()

    assert captured.value.code == EXECUTOR_CLOSE_INTERNAL_ERROR
    assert captured.value.reason == "Executor connection failed"
    assert "persistence" not in captured.value.reason


def test_unexpected_lifecycle_validation_failure_closes_without_private_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, token = app_with_live_session()

    def fail_validation(*_values: object) -> None:
        raise RuntimeError("private lifecycle failure")

    monkeypatch.setattr(
        ExecutorConnectionService,
        "validate_inbound_message",
        fail_validation,
    )
    with TestClient(app).websocket_connect(
        "/api/v1/executors/connect",
        headers={"authorization": f"Bearer {token}"},
        subprotocols=[EXECUTOR_WEBSOCKET_SUBPROTOCOL],
    ) as websocket:
        websocket.send_text(hello())
        websocket.send_text(hello())
        with pytest.raises(WebSocketDisconnect) as captured:
            websocket.receive_text()

    assert captured.value.code == EXECUTOR_CLOSE_INTERNAL_ERROR
    assert captured.value.reason == "Executor connection failed"
    assert "lifecycle" not in captured.value.reason


@pytest.mark.parametrize("field", ["hello", "recheck"])
@pytest.mark.parametrize("invalid", (0, -1, float("inf"), float("nan"), True, "1"))
def test_connection_timeouts_must_be_positive_finite_numbers(
    field: str,
    invalid: object,
) -> None:
    arguments = {
        f"executor_connection_{field}_"
        + ("timeout_seconds" if field == "hello" else "interval_seconds"): invalid
    }

    with pytest.raises(ValueError, match="Executor connection timeouts must be positive"):
        cast(Any, create_app)(database=None, **arguments)
