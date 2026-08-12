from __future__ import annotations

import json
import secrets
from uuid import UUID, uuid4

import pytest

from automation_tool.control_plane.application.executor_connections import (
    EXECUTOR_WEBSOCKET_SUBPROTOCOL,
    ExecutorArchitecture,
    ExecutorConnectionRejected,
    ExecutorConnectionService,
    ExecutorPlatform,
)
from automation_tool.control_plane.domain import ExecutorConnectionId
from automation_tool.protocol import PlatformSessionHealthEnvelope, TaskEventEnvelope

INSTALLATION_ID = UUID("aa11aa11-aa11-4a11-8a11-aa11aa11aa11")
EXECUTOR_ID = UUID("123e4567-e89b-42d3-a456-426614174004")
CREDENTIAL_ID = UUID("123e4567-e89b-42d3-a456-426614174007")
MESSAGE_ID = "123e4567-e89b-42d3-a456-426614174001"
CORRELATION_ID = "123e4567-e89b-42d3-a456-426614174002"


def connection_service() -> tuple[ExecutorConnectionService, str]:
    return ExecutorConnectionService(), secrets.token_urlsafe(24)


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


def task_result_message(
    *,
    installation_id: str = str(INSTALLATION_ID),
    executor_id: str = str(EXECUTOR_ID),
    message_type: str = "task.accept",
) -> str:
    return json.dumps(
        {
            "protocol_version": "1.0",
            "message_id": "423e4567-e89b-42d3-a456-426614174001",
            "message_type": message_type,
            "sent_at": "2026-07-18T12:00:01Z",
            "deadline_at": "2026-07-18T12:00:31Z",
            "installation_id": installation_id,
            "executor_id": executor_id,
            "correlation_id": "323e4567-e89b-42d3-a456-426614174002",
            "idempotency_key": "task:accept:attempt:1",
            "sequence": 1,
            "payload": {"accepted": True},
            "task_id": "123e4567-e89b-42d3-a456-426614174005",
            "execution_attempt_id": "123e4567-e89b-42d3-a456-426614174006",
        },
        separators=(",", ":"),
    )


def task_event_message(
    *,
    installation_id: str = str(INSTALLATION_ID),
    executor_id: str = str(EXECUTOR_ID),
) -> str:
    return json.dumps(
        {
            "protocol_version": "1.0",
            "message_id": "523e4567-e89b-42d3-a456-426614174001",
            "message_type": "task.started",
            "sent_at": "2026-07-18T12:00:01Z",
            "deadline_at": "2026-07-18T12:00:31Z",
            "installation_id": installation_id,
            "executor_id": executor_id,
            "correlation_id": "323e4567-e89b-42d3-a456-426614174002",
            "idempotency_key": "task:event:attempt:1",
            "sequence": 1,
            "payload": {},
            "task_id": "123e4567-e89b-42d3-a456-426614174005",
            "execution_attempt_id": "123e4567-e89b-42d3-a456-426614174006",
        },
        separators=(",", ":"),
    )


def platform_session_health_message(
    *,
    installation_id: str = str(INSTALLATION_ID),
    executor_id: str = str(EXECUTOR_ID),
) -> str:
    return json.dumps(
        {
            "protocol_version": "1.0",
            "message_id": "723e4567-e89b-42d3-a456-426614174001",
            "message_type": "platform.session_health",
            "sent_at": "2026-07-18T12:00:01Z",
            "deadline_at": "2026-07-18T12:00:31Z",
            "installation_id": installation_id,
            "executor_id": executor_id,
            "correlation_id": "723e4567-e89b-42d3-a456-426614174002",
            "idempotency_key": "platform:douyin:session:7:healthy",
            "sequence": 7,
            "payload": {
                "platform": "douyin",
                "state": "healthy",
                "session_revision": 7,
                "observed_at": "2026-07-18T12:00:00Z",
            },
        },
        separators=(",", ":"),
    )


@pytest.mark.asyncio
async def test_authorize_accepts_a_well_formed_local_token() -> None:
    service, token = connection_service()

    authorized = await service.authorize(token)

    assert authorized.installation_id.uuid == INSTALLATION_ID
    assert authorized.session_id.version == 4
    assert token not in repr(authorized)
    assert EXECUTOR_WEBSOCKET_SUBPROTOCOL == "automation-tool.executor.v1"


@pytest.mark.asyncio
async def test_missing_and_malformed_tokens_share_one_safe_error() -> None:
    service, _ = connection_service()
    rejected_values: tuple[object, ...] = (None, "", "short", "has space in it" + "x" * 16, b"bytes" * 8)

    for value in rejected_values:
        with pytest.raises(ExecutorConnectionRejected) as captured:
            await service.authorize(value)
        assert str(captured.value) == "Executor connection is rejected"


@pytest.mark.asyncio
async def test_hello_rejects_impersonation_wrong_type_and_invalid_runtime_metadata() -> None:
    service, token = connection_service()
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
        lifecycle_message(
            payload={
                "architecture": "arm64",
                "executor_version": "0.0.9",
                "platform": "macos",
            }
        ),
        lifecycle_message(
            payload={
                "architecture": "arm64",
                "executor_version": "0.1.1",
                "platform": "macos",
            }
        ),
        lifecycle_message(
            payload={
                "architecture": "arm64",
                "executor_version": "0.1.0-rc.1",
                "platform": "macos",
            }
        ),
    )

    for source in invalid_messages:
        with pytest.raises(ExecutorConnectionRejected):
            service.bind_hello(authorized, source)


@pytest.mark.asyncio
async def test_bound_lifecycle_rejects_identity_switch_repeated_hello_and_task_traffic() -> None:
    service, token = connection_service()
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
async def test_bound_inbound_accepts_platform_health_and_rejects_impersonation() -> None:
    service, token = connection_service()
    bound = service.bind_hello(await service.authorize(token), lifecycle_message())

    heartbeat = service.validate_inbound_message(
        bound,
        lifecycle_message(
            message_type="executor.heartbeat",
            payload={"status": "healthy"},
            sequence=2,
        ),
    )
    accepted = service.validate_inbound_message(bound, task_result_message())
    task_event = service.validate_inbound_message(bound, task_event_message())
    platform_health = service.validate_inbound_message(bound, platform_session_health_message())

    assert heartbeat.message_type == "executor.heartbeat"
    assert accepted.message_type == "task.accept"
    assert isinstance(task_event, TaskEventEnvelope)
    assert task_event.message_type == "task.started"
    assert isinstance(platform_health, PlatformSessionHealthEnvelope)
    assert platform_health.message_type == "platform.session_health"
    for source in (
        task_result_message(installation_id=str(uuid4())),
        task_result_message(executor_id=str(uuid4())),
        task_event_message(installation_id=str(uuid4())),
        task_event_message(executor_id=str(uuid4())),
        platform_session_health_message(installation_id=str(uuid4())),
        platform_session_health_message(executor_id=str(uuid4())),
        lifecycle_message(message_type="task.offer"),
        lifecycle_message(),
    ):
        with pytest.raises(ExecutorConnectionRejected):
            service.validate_inbound_message(bound, source)


@pytest.mark.asyncio
async def test_reauthorize_keeps_a_live_connection_authorized() -> None:
    service, token = connection_service()
    bound = service.bind_hello(await service.authorize(token), lifecycle_message())

    await service.reauthorize(bound)
    await service.reauthorize(bound)
