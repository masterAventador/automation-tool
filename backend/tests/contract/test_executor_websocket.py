from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass
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
from automation_tool.control_plane.domain import InstallationId

NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
INSTALLATION_ID = UUID("123e4567-e89b-42d3-a456-426614174003")
EXECUTOR_ID = "123e4567-e89b-42d3-a456-426614174004"


@dataclass
class FixedClock:
    def now(self) -> datetime:
        return NOW


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
        "validate_lifecycle_message",
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
