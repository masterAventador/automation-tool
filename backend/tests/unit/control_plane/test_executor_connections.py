from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

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
from automation_tool.control_plane.application.executor_connections import (
    EXECUTOR_WEBSOCKET_SUBPROTOCOL,
    ExecutorArchitecture,
    ExecutorConnectionRejected,
    ExecutorConnectionService,
    ExecutorPlatform,
)
from automation_tool.control_plane.domain import ExecutorConnectionId

NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
INSTALLATION_ID = UUID("123e4567-e89b-42d3-a456-426614174003")
EXECUTOR_ID = UUID("123e4567-e89b-42d3-a456-426614174004")
CREDENTIAL_ID = UUID("123e4567-e89b-42d3-a456-426614174007")
MESSAGE_ID = "123e4567-e89b-42d3-a456-426614174001"
CORRELATION_ID = "123e4567-e89b-42d3-a456-426614174002"


@dataclass
class FixedClock:
    def now(self) -> datetime:
        return NOW


class SessionRepository:
    def __init__(self, expected: ParsedDeviceSession) -> None:
        self.expected = expected
        self.active = True
        self.authentication_count = 0
        self.credential_version = 3

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

    async def authenticate(
        self,
        *,
        presented_session: ParsedDeviceSession,
        required_capability: DeviceSessionCapability,
        authenticated_at: datetime,
    ) -> AuthenticatedDeviceSession:
        self.authentication_count += 1
        if (
            not self.active
            or presented_session != self.expected
            or required_capability is not DeviceSessionCapability.EXECUTOR_CONNECT
            or authenticated_at != NOW
        ):
            from automation_tool.control_plane.application.device_sessions import (
                DeviceSessionRejected,
            )

            raise DeviceSessionRejected
        return AuthenticatedDeviceSession(
            session_id=self.expected.session_id,
            installation_id=INSTALLATION_ID,
            credential_id=CREDENTIAL_ID,
            credential_version=self.credential_version,
            capability=required_capability,
            expires_at=NOW + timedelta(minutes=5),
        )


def session_material() -> PendingDeviceSession:
    return DeviceSessionFactory(
        secret_source=secrets.token_bytes,
        id_source=uuid4,
    ).create()


def connection_service() -> tuple[ExecutorConnectionService, SessionRepository, str]:
    material = session_material()
    parsed = ParsedDeviceSession(
        session_id=material.session_id,
        secret_digest=material.secret_digest,
    )
    repository = SessionRepository(parsed)
    sessions = DeviceSessionService(
        repository=repository,
        clock=FixedClock(),
        session_factory=DeviceSessionFactory(
            secret_source=secrets.token_bytes,
            id_source=uuid4,
        ),
    )
    return ExecutorConnectionService(sessions), repository, material.session_token


def lifecycle_message(
    *,
    message_type: str = "executor.hello",
    installation_id: str = str(INSTALLATION_ID),
    executor_id: str = str(EXECUTOR_ID),
    payload: object | None = None,
    sequence: int = 1,
) -> str:
    return json.dumps(
        {
            "protocol_version": "1.0",
            "message_id": MESSAGE_ID,
            "message_type": message_type,
            "sent_at": "2026-07-18T12:00:00Z",
            "deadline_at": "2026-07-18T12:00:30Z",
            "installation_id": installation_id,
            "executor_id": executor_id,
            "correlation_id": CORRELATION_ID,
            "idempotency_key": f"executor:{message_type}:{sequence}",
            "sequence": sequence,
            "payload": payload
            if payload is not None
            else {
                "architecture": "arm64",
                "executor_version": "0.1.0",
                "platform": "macos",
            },
        },
        separators=(",", ":"),
    )


@pytest.mark.asyncio
async def test_authorize_uses_only_executor_capability_and_retains_no_raw_bearer() -> None:
    service, repository, token = connection_service()

    authorized = await service.authorize(token)

    assert authorized.installation_id.uuid == INSTALLATION_ID
    assert authorized.session_id == repository.expected.session_id
    assert authorized.session_expires_at == NOW + timedelta(minutes=5)
    assert token not in repr(authorized)
    assert repository.expected.secret_digest.hex() not in repr(authorized)
    assert EXECUTOR_WEBSOCKET_SUBPROTOCOL == "automation-tool.executor.v1"


@pytest.mark.asyncio
async def test_missing_malformed_and_rejected_sessions_share_one_safe_error() -> None:
    service, repository, token = connection_service()
    rejected_values: tuple[object, ...] = (None, "", "private-invalid-session")
    repository.active = False
    rejected_values += (token,)

    for value in rejected_values:
        with pytest.raises(ExecutorConnectionRejected) as captured:
            await service.authorize(value)
        assert str(captured.value) == "Executor connection is rejected"
        assert captured.value.__cause__ is None
        assert captured.value.__context__ is None
        assert "private-invalid-session" not in str(captured.value)


@pytest.mark.asyncio
async def test_hello_binds_installation_executor_protocol_and_runtime_versions() -> None:
    service, _, token = connection_service()
    authorized = await service.authorize(token)

    bound = service.bind_hello(authorized, lifecycle_message())
    another = service.bind_hello(authorized, lifecycle_message())

    assert type(bound.connection_id) is ExecutorConnectionId
    assert bound.connection_id.uuid.version == 4
    assert bound.connection_id != another.connection_id
    assert bound.installation_id == authorized.installation_id
    assert bound.session_id == authorized.session_id
    assert bound.session_expires_at == authorized.session_expires_at
    assert bound.executor_id.uuid == EXECUTOR_ID
    assert bound.protocol_version == "1.0"
    assert bound.executor_version == "0.1.0"
    assert bound.platform is ExecutorPlatform.MACOS
    assert bound.architecture is ExecutorArchitecture.ARM64
    assert token not in repr(bound)


@pytest.mark.asyncio
async def test_hello_rejects_impersonation_wrong_type_and_invalid_runtime_metadata() -> None:
    service, _, token = connection_service()
    authorized = await service.authorize(token)
    invalid_messages = (
        lifecycle_message(installation_id=str(uuid4())),
        lifecycle_message(executor_id=str(uuid4()), message_type="executor.heartbeat"),
        lifecycle_message(payload={"architecture": "arm64", "platform": "macos"}),
        lifecycle_message(
            payload={
                "architecture": "universal",
                "executor_version": "latest",
                "platform": "linux",
            }
        ),
    )

    for source in invalid_messages:
        with pytest.raises(ExecutorConnectionRejected):
            service.bind_hello(authorized, source)


@pytest.mark.asyncio
async def test_bound_lifecycle_rejects_identity_switch_repeated_hello_and_task_traffic() -> None:
    service, _, token = connection_service()
    bound = service.bind_hello(await service.authorize(token), lifecycle_message())
    heartbeat = service.validate_lifecycle_message(
        bound,
        lifecycle_message(
            message_type="executor.heartbeat",
            payload={"status": "healthy"},
            sequence=2,
        ),
    )

    assert heartbeat.message_type == "executor.heartbeat"
    rejected = (
        lifecycle_message(),
        lifecycle_message(
            message_type="executor.heartbeat",
            executor_id=str(uuid4()),
            payload={"status": "healthy"},
        ),
        lifecycle_message(message_type="task.offer"),
    )
    for source in rejected:
        with pytest.raises(ExecutorConnectionRejected):
            service.validate_lifecycle_message(bound, source)


@pytest.mark.asyncio
async def test_reauthorize_rechecks_database_and_closes_revoked_live_session() -> None:
    service, repository, token = connection_service()
    bound = service.bind_hello(await service.authorize(token), lifecycle_message())

    await service.reauthorize(bound)
    assert repository.authentication_count == 2
    repository.active = False

    with pytest.raises(ExecutorConnectionRejected):
        await service.reauthorize(bound)


@pytest.mark.asyncio
async def test_reauthorize_rejects_changed_authenticated_binding() -> None:
    service, repository, token = connection_service()
    bound = service.bind_hello(await service.authorize(token), lifecycle_message())
    repository.credential_version = 4

    with pytest.raises(ExecutorConnectionRejected):
        await service.reauthorize(bound)
