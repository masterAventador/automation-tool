"""Short-lived, single-capability sessions exchanged from device credentials."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Final, Protocol
from uuid import UUID

from automation_tool.control_plane.application.device_credentials import (
    ParsedDeviceCredential,
    parse_device_credential,
)
from automation_tool.control_plane.application.opaque_bearers import (
    InvalidOpaqueBearer,
    OpaqueBearerCodec,
)

DEVICE_SESSION_LIFETIME: Final = timedelta(minutes=5)
DEVICE_SESSION_CLOCK_SKEW: Final = timedelta(seconds=30)
_SESSION_CODEC: Final = OpaqueBearerCodec("atds1")


class InvalidDeviceSession(ValueError):
    """A presented short-lived session is malformed."""

    def __init__(self) -> None:
        super().__init__("Device session is invalid")


class InvalidDeviceSessionCapability(ValueError):
    """A requested or required capability is not one exact supported value."""

    def __init__(self) -> None:
        super().__init__("Device session capability is invalid")


class DeviceSessionRejected(PermissionError):
    """A session is unknown, inactive, expired, or lacks the required capability."""

    def __init__(self) -> None:
        super().__init__("Device session is rejected")


class DeviceSessionCapability(StrEnum):
    APP_CONTROL_PLANE = "app.control-plane"
    EXECUTOR_CONNECT = "executor.connect"

    @classmethod
    def parse(cls, value: object) -> "DeviceSessionCapability":
        if isinstance(value, cls):
            return value
        if type(value) is not str:
            raise InvalidDeviceSessionCapability
        try:
            return cls(value)
        except ValueError:
            raise InvalidDeviceSessionCapability from None


@dataclass(frozen=True, slots=True)
class PendingDeviceSession:
    session_id: UUID
    session_token: str
    secret_digest: bytes


@dataclass(frozen=True, slots=True)
class ParsedDeviceSession:
    session_id: UUID
    secret_digest: bytes


@dataclass(frozen=True, slots=True)
class IssuedDeviceSession:
    session_id: UUID
    installation_id: UUID
    credential_id: UUID
    credential_version: int
    session_token: str
    capability: DeviceSessionCapability
    issued_at: datetime
    not_before: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class AuthenticatedDeviceSession:
    session_id: UUID
    installation_id: UUID
    credential_id: UUID
    credential_version: int
    capability: DeviceSessionCapability
    expires_at: datetime


class Clock(Protocol):
    def now(self) -> datetime: ...


class DeviceSessionRepository(Protocol):
    async def issue(
        self,
        *,
        presented_credential: ParsedDeviceCredential,
        pending_session: PendingDeviceSession,
        capability: DeviceSessionCapability,
        issued_at: datetime,
        not_before: datetime,
        expires_at: datetime,
    ) -> IssuedDeviceSession: ...

    async def authenticate(
        self,
        *,
        presented_session: ParsedDeviceSession,
        required_capability: DeviceSessionCapability,
        authenticated_at: datetime,
    ) -> AuthenticatedDeviceSession: ...


class DeviceSessionFactory:
    """Create random short-lived bearer material with one stable prefix."""

    def __init__(
        self,
        *,
        secret_source: Callable[[int], bytes],
        id_source: Callable[[], UUID],
    ) -> None:
        self._secret_source = secret_source
        self._id_source = id_source

    def create(self) -> PendingDeviceSession:
        try:
            created = _SESSION_CODEC.create(
                secret_source=self._secret_source,
                id_source=self._id_source,
            )
        except RuntimeError:
            raise RuntimeError("Device session generation failed") from None
        return PendingDeviceSession(
            session_id=created.bearer_id,
            session_token=created.bearer,
            secret_digest=created.secret_digest,
        )


def parse_device_session(value: object) -> ParsedDeviceSession:
    try:
        parsed = _SESSION_CODEC.parse(value)
    except InvalidOpaqueBearer:
        raise InvalidDeviceSession from None
    return ParsedDeviceSession(
        session_id=parsed.bearer_id,
        secret_digest=parsed.secret_digest,
    )


def _aware_utc(clock: Clock) -> datetime:
    now = clock.now()
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise RuntimeError("Device session clock is invalid")
    return now.astimezone(UTC)


class DeviceSessionService:
    """Exchange a long-lived credential and authenticate its short-lived sessions."""

    def __init__(
        self,
        *,
        repository: DeviceSessionRepository,
        clock: Clock,
        session_factory: DeviceSessionFactory,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._session_factory = session_factory

    async def exchange(
        self,
        *,
        device_credential: object,
        capability: object,
    ) -> IssuedDeviceSession:
        presented_credential = parse_device_credential(device_credential)
        requested_capability = DeviceSessionCapability.parse(capability)
        issued_at = _aware_utc(self._clock)
        pending_session = self._session_factory.create()
        return await self._repository.issue(
            presented_credential=presented_credential,
            pending_session=pending_session,
            capability=requested_capability,
            issued_at=issued_at,
            not_before=issued_at - DEVICE_SESSION_CLOCK_SKEW,
            expires_at=issued_at + DEVICE_SESSION_LIFETIME,
        )

    async def authenticate(
        self,
        *,
        session_token: object,
        required_capability: DeviceSessionCapability,
    ) -> AuthenticatedDeviceSession:
        presented_session = parse_device_session(session_token)
        if not isinstance(required_capability, DeviceSessionCapability):
            raise InvalidDeviceSessionCapability
        authenticated_at = _aware_utc(self._clock)
        return await self._repository.authenticate(
            presented_session=presented_session,
            required_capability=required_capability,
            authenticated_at=authenticated_at,
        )


__all__ = [
    "DEVICE_SESSION_CLOCK_SKEW",
    "DEVICE_SESSION_LIFETIME",
    "AuthenticatedDeviceSession",
    "DeviceSessionCapability",
    "DeviceSessionFactory",
    "DeviceSessionRejected",
    "DeviceSessionRepository",
    "DeviceSessionService",
    "InvalidDeviceSession",
    "InvalidDeviceSessionCapability",
    "IssuedDeviceSession",
    "ParsedDeviceSession",
    "PendingDeviceSession",
    "parse_device_session",
]
