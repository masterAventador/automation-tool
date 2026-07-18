"""Versioned long-lived device credentials with digest-only persistence."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Protocol
from uuid import UUID

from automation_tool.control_plane.application.opaque_bearers import (
    InvalidOpaqueBearer,
    OpaqueBearerCodec,
)

DEVICE_CREDENTIAL_SCOPE: Final = "device.session.exchange"
_CREDENTIAL_CODEC: Final = OpaqueBearerCodec("atdc1")


class InvalidDeviceCredential(ValueError):
    """A presented credential is not canonical or safely bounded."""

    def __init__(self) -> None:
        super().__init__("Device credential is invalid")


class DeviceCredentialRejected(PermissionError):
    """A syntactically valid credential is unknown, inactive, or mismatched."""

    def __init__(self) -> None:
        super().__init__("Device credential is rejected")


@dataclass(frozen=True, slots=True)
class PendingDeviceCredential:
    """A fresh secret plus the digest that is safe to persist."""

    credential_id: UUID
    credential: str
    secret_digest: bytes


@dataclass(frozen=True, slots=True)
class ParsedDeviceCredential:
    """Non-secret lookup identifier and proof digest derived from a bearer."""

    credential_id: UUID
    secret_digest: bytes


@dataclass(frozen=True, slots=True)
class IssuedDeviceCredential:
    """A credential returned exactly when it is created."""

    credential_id: UUID
    installation_id: UUID
    credential: str
    version: int
    scope: str


@dataclass(frozen=True, slots=True)
class RevokedDeviceCredential:
    """Public state returned after self-revocation."""

    credential_id: UUID
    installation_id: UUID
    version: int
    status: str


class Clock(Protocol):
    def now(self) -> datetime: ...


class DeviceCredentialRepository(Protocol):
    async def rotate(
        self,
        *,
        presented: ParsedDeviceCredential,
        replacement: PendingDeviceCredential,
        rotated_at: datetime,
    ) -> IssuedDeviceCredential: ...

    async def revoke(
        self,
        *,
        presented: ParsedDeviceCredential,
        revoked_at: datetime,
    ) -> RevokedDeviceCredential: ...


def parse_device_credential(value: object) -> ParsedDeviceCredential:
    """Parse a credential without retaining or returning its plaintext secret."""
    try:
        parsed = _CREDENTIAL_CODEC.parse(value)
    except InvalidOpaqueBearer:
        raise InvalidDeviceCredential from None
    return ParsedDeviceCredential(
        credential_id=parsed.bearer_id,
        secret_digest=parsed.secret_digest,
    )


class DeviceCredentialFactory:
    """Create independently random bearer credentials for one-time return."""

    def __init__(
        self,
        *,
        secret_source: Callable[[int], bytes],
        id_source: Callable[[], UUID],
    ) -> None:
        self._secret_source = secret_source
        self._id_source = id_source

    def create(self) -> PendingDeviceCredential:
        try:
            created = _CREDENTIAL_CODEC.create(
                secret_source=self._secret_source,
                id_source=self._id_source,
            )
        except RuntimeError:
            raise RuntimeError("Device credential generation failed") from None
        return PendingDeviceCredential(
            credential_id=created.bearer_id,
            credential=created.bearer,
            secret_digest=created.secret_digest,
        )


def _aware_utc(clock: Clock) -> datetime:
    now = clock.now()
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise RuntimeError("Device credential clock is invalid")
    return now.astimezone(UTC)


class DeviceCredentialService:
    """Rotate or revoke a long-lived credential without exposing persistence details."""

    def __init__(
        self,
        *,
        repository: DeviceCredentialRepository,
        clock: Clock,
        credential_factory: DeviceCredentialFactory,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._credential_factory = credential_factory

    async def rotate(self, credential: object) -> IssuedDeviceCredential:
        presented = parse_device_credential(credential)
        rotated_at = _aware_utc(self._clock)
        replacement = self._credential_factory.create()
        return await self._repository.rotate(
            presented=presented,
            replacement=replacement,
            rotated_at=rotated_at,
        )

    async def revoke(self, credential: object) -> RevokedDeviceCredential:
        presented = parse_device_credential(credential)
        revoked_at = _aware_utc(self._clock)
        return await self._repository.revoke(
            presented=presented,
            revoked_at=revoked_at,
        )


__all__ = [
    "DEVICE_CREDENTIAL_SCOPE",
    "DeviceCredentialFactory",
    "DeviceCredentialRejected",
    "DeviceCredentialRepository",
    "DeviceCredentialService",
    "InvalidDeviceCredential",
    "IssuedDeviceCredential",
    "ParsedDeviceCredential",
    "PendingDeviceCredential",
    "RevokedDeviceCredential",
    "parse_device_credential",
]
