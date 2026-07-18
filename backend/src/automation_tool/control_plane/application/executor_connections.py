"""Authenticated Executor WebSocket identity and protocol binding."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from automation_tool.control_plane.application.device_sessions import (
    AuthenticatedDeviceSession,
    DeviceSessionCapability,
    DeviceSessionRejected,
    DeviceSessionService,
    InvalidDeviceSession,
    ParsedDeviceSession,
    parse_device_session,
)
from automation_tool.control_plane.domain import (
    ExecutorConnectionId,
    ExecutorId,
    InstallationId,
    InvalidResourceId,
)
from automation_tool.protocol import (
    EXECUTOR_PROTOCOL_VERSION,
    EXECUTOR_WEBSOCKET_SUBPROTOCOL,
    ExecutorLifecycleEnvelope,
    ExecutorProtocolError,
    TaskCommandResultEnvelope,
    TaskEventEnvelope,
    parse_executor_message,
)

_EXECUTOR_VERSION_PATTERN: Final = (
    r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?"
    r"(?:\+[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?$"
)


class ExecutorConnectionRejected(PermissionError):
    """Authentication, identity binding, or lifecycle wire validation failed."""

    def __init__(self) -> None:
        super().__init__("Executor connection is rejected")


class ExecutorPlatform(StrEnum):
    MACOS = "macos"
    WINDOWS = "windows"


class ExecutorArchitecture(StrEnum):
    ARM64 = "arm64"
    X86_64 = "x86_64"


class _ExecutorHelloPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    executor_version: Annotated[
        str,
        Field(min_length=5, max_length=64, pattern=_EXECUTOR_VERSION_PATTERN),
    ]
    platform: ExecutorPlatform
    architecture: ExecutorArchitecture


@dataclass(frozen=True, slots=True)
class AuthorizedExecutorConnection:
    installation_id: InstallationId
    session_id: UUID
    credential_id: UUID
    credential_version: int
    session_expires_at: datetime
    _presented_session: ParsedDeviceSession = field(repr=False)


@dataclass(frozen=True, slots=True)
class BoundExecutorConnection:
    connection_id: ExecutorConnectionId
    installation_id: InstallationId
    executor_id: ExecutorId
    protocol_version: str
    executor_version: str
    platform: ExecutorPlatform
    architecture: ExecutorArchitecture
    hello_sequence: int
    _authorization: AuthorizedExecutorConnection = field(repr=False)

    @property
    def session_id(self) -> UUID:
        return self._authorization.session_id

    @property
    def session_expires_at(self) -> datetime:
        return self._authorization.session_expires_at


class ExecutorConnectionService:
    """Bind one live WebSocket to its authenticated installation and Executor."""

    def __init__(self, device_sessions: DeviceSessionService) -> None:
        self._device_sessions = device_sessions

    async def authorize(self, session_token: object) -> AuthorizedExecutorConnection:
        try:
            presented = parse_device_session(session_token)
            authenticated = await self._device_sessions.authenticate_parsed(
                presented_session=presented,
                required_capability=DeviceSessionCapability.EXECUTOR_CONNECT,
            )
            installation_id = InstallationId.parse(authenticated.installation_id)
            return AuthorizedExecutorConnection(
                installation_id=installation_id,
                session_id=authenticated.session_id,
                credential_id=authenticated.credential_id,
                credential_version=authenticated.credential_version,
                session_expires_at=authenticated.expires_at,
                _presented_session=presented,
            )
        except (InvalidDeviceSession, DeviceSessionRejected, InvalidResourceId):
            pass
        raise ExecutorConnectionRejected

    def bind_hello(
        self,
        authorized: AuthorizedExecutorConnection,
        source: str | bytes,
    ) -> BoundExecutorConnection:
        try:
            message = parse_executor_message(source)
            if (
                not isinstance(message, ExecutorLifecycleEnvelope)
                or message.message_type != "executor.hello"
                or str(message.installation_id) != str(authorized.installation_id)
            ):
                raise ValueError
            payload = _ExecutorHelloPayload.model_validate(message.payload)
            return BoundExecutorConnection(
                connection_id=ExecutorConnectionId.new(),
                installation_id=authorized.installation_id,
                executor_id=ExecutorId.parse(str(message.executor_id)),
                protocol_version=EXECUTOR_PROTOCOL_VERSION,
                executor_version=payload.executor_version,
                platform=payload.platform,
                architecture=payload.architecture,
                hello_sequence=message.sequence,
                _authorization=authorized,
            )
        except (
            ExecutorProtocolError,
            InvalidResourceId,
            TypeError,
            ValidationError,
            ValueError,
        ):
            pass
        raise ExecutorConnectionRejected

    def validate_lifecycle_message(
        self,
        bound: BoundExecutorConnection,
        source: str | bytes,
    ) -> ExecutorLifecycleEnvelope:
        try:
            message = parse_executor_message(source)
            if (
                not isinstance(message, ExecutorLifecycleEnvelope)
                or message.message_type != "executor.heartbeat"
                or str(message.installation_id) != str(bound.installation_id)
                or str(message.executor_id) != str(bound.executor_id)
            ):
                raise ValueError
            return message
        except (ExecutorProtocolError, TypeError, ValueError):
            pass
        raise ExecutorConnectionRejected

    def validate_inbound_message(
        self,
        bound: BoundExecutorConnection,
        source: str | bytes,
    ) -> ExecutorLifecycleEnvelope | TaskCommandResultEnvelope | TaskEventEnvelope:
        """Accept only a bound heartbeat, command result, or Task event after Hello."""

        try:
            message = parse_executor_message(source)
            if (
                not isinstance(
                    message,
                    (ExecutorLifecycleEnvelope, TaskCommandResultEnvelope, TaskEventEnvelope),
                )
                or (
                    isinstance(message, ExecutorLifecycleEnvelope)
                    and message.message_type != "executor.heartbeat"
                )
                or str(message.installation_id) != str(bound.installation_id)
                or str(message.executor_id) != str(bound.executor_id)
            ):
                raise ValueError
            return message
        except (ExecutorProtocolError, TypeError, ValueError):
            pass
        raise ExecutorConnectionRejected

    async def reauthorize(self, bound: BoundExecutorConnection) -> None:
        authorization = bound._authorization
        try:
            authenticated = await self._device_sessions.authenticate_parsed(
                presented_session=authorization._presented_session,
                required_capability=DeviceSessionCapability.EXECUTOR_CONNECT,
            )
            if not _same_authorization(authenticated, authorization):
                raise ValueError
            return
        except (DeviceSessionRejected, ValueError):
            pass
        raise ExecutorConnectionRejected


def _same_authorization(
    authenticated: AuthenticatedDeviceSession,
    expected: AuthorizedExecutorConnection,
) -> bool:
    return (
        authenticated.session_id == expected.session_id
        and authenticated.installation_id == expected.installation_id.uuid
        and authenticated.credential_id == expected.credential_id
        and authenticated.credential_version == expected.credential_version
        and authenticated.capability is DeviceSessionCapability.EXECUTOR_CONNECT
        and authenticated.expires_at == expected.session_expires_at
    )


__all__ = [
    "EXECUTOR_WEBSOCKET_SUBPROTOCOL",
    "AuthorizedExecutorConnection",
    "BoundExecutorConnection",
    "ExecutorArchitecture",
    "ExecutorConnectionRejected",
    "ExecutorConnectionService",
    "ExecutorPlatform",
]
