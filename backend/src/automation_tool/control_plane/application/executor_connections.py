"""Authenticated Executor WebSocket identity and protocol binding."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Final
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from automation_tool.control_plane.application.device_sessions import ParsedDeviceSession
from automation_tool.control_plane.domain.local_installation import local_installation_id
from automation_tool.control_plane.domain import (
    ExecutorConnectionId,
    ExecutorId,
    InstallationId,
    InvalidResourceId,
)
from automation_tool.protocol import (
    CURRENT_EXECUTOR_RUNTIME_VERSION,
    EXECUTOR_PROTOCOL_VERSION,
    EXECUTOR_WEBSOCKET_SUBPROTOCOL,
    ExecutorLifecycleEnvelope,
    ExecutorProtocolError,
    PlatformSessionHealthEnvelope,
    TaskCommandResultEnvelope,
    TaskDiscoveryBatchEnvelope,
    TaskDiscoveryCompletedEnvelope,
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
    _presented_session: ParsedDeviceSession | None = field(repr=False)


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
    """Bind one live WebSocket to the single local installation.

    The device-identity mechanism was removed: the loopback Executor presents
    an opaque token whose only job is to be a well-formed bearer.
    """

    def __init__(self) -> None:
        pass

    async def authorize(self, session_token: object) -> AuthorizedExecutorConnection:
        if (
            not isinstance(session_token, str)
            or not 16 <= len(session_token) <= 512
            or any(character.isspace() for character in session_token)
        ):
            raise ExecutorConnectionRejected
        now = datetime.now(UTC)
        return AuthorizedExecutorConnection(
            installation_id=local_installation_id(),
            session_id=uuid4(),
            credential_id=uuid4(),
            credential_version=1,
            session_expires_at=now + timedelta(days=365),
            _presented_session=None,
        )

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
            if payload.executor_version != CURRENT_EXECUTOR_RUNTIME_VERSION:
                raise ValueError
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
    ) -> (
        ExecutorLifecycleEnvelope
        | PlatformSessionHealthEnvelope
        | TaskCommandResultEnvelope
        | TaskDiscoveryBatchEnvelope
        | TaskDiscoveryCompletedEnvelope
        | TaskEventEnvelope
    ):
        """Accept only bound Executor facts and acknowledgements after Hello."""

        try:
            message = parse_executor_message(source)
            if (
                not isinstance(
                    message,
                    (
                        ExecutorLifecycleEnvelope,
                        PlatformSessionHealthEnvelope,
                        TaskCommandResultEnvelope,
                        TaskDiscoveryBatchEnvelope,
                        TaskDiscoveryCompletedEnvelope,
                        TaskEventEnvelope,
                    ),
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
        """设备身份机制已删除：连接存续期内始终有效。"""
        del bound


__all__ = [
    "EXECUTOR_WEBSOCKET_SUBPROTOCOL",
    "AuthorizedExecutorConnection",
    "BoundExecutorConnection",
    "ExecutorArchitecture",
    "ExecutorConnectionRejected",
    "ExecutorConnectionService",
    "ExecutorPlatform",
]
