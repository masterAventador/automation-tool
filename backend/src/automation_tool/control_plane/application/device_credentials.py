"""Versioned long-lived device credentials with digest-only persistence."""

import base64
import binascii
import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Protocol
from uuid import UUID

DEVICE_CREDENTIAL_SCOPE: Final = "device.session.exchange"
_CREDENTIAL_PREFIX: Final = "atdc1"
_SECRET_LENGTH: Final = 32
_MAX_CREDENTIAL_LENGTH: Final = 256
_BASE64URL_PATTERN: Final = re.compile(r"[A-Za-z0-9_-]+")


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


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def parse_device_credential(value: object) -> ParsedDeviceCredential:
    """Parse a credential without retaining or returning its plaintext secret."""

    if not isinstance(value, str) or not value or len(value) > _MAX_CREDENTIAL_LENGTH:
        raise InvalidDeviceCredential
    try:
        prefix, identifier, encoded_secret = value.split(".")
    except ValueError:
        raise InvalidDeviceCredential from None
    if prefix != _CREDENTIAL_PREFIX or _BASE64URL_PATTERN.fullmatch(encoded_secret) is None:
        raise InvalidDeviceCredential
    try:
        credential_id = UUID(identifier)
        secret = base64.urlsafe_b64decode(encoded_secret + ("=" * (-len(encoded_secret) % 4)))
    except (ValueError, binascii.Error):
        raise InvalidDeviceCredential from None
    if (
        credential_id.version != 4
        or str(credential_id) != identifier
        or len(secret) != _SECRET_LENGTH
        or _base64url(secret) != encoded_secret
    ):
        raise InvalidDeviceCredential
    return ParsedDeviceCredential(
        credential_id=credential_id,
        secret_digest=hashlib.sha256(secret).digest(),
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
        secret = self._secret_source(_SECRET_LENGTH)
        credential_id = self._id_source()
        if (
            not isinstance(secret, bytes)
            or len(secret) != _SECRET_LENGTH
            or not isinstance(credential_id, UUID)
            or credential_id.version != 4
        ):
            raise RuntimeError("Device credential generation failed")
        encoded_secret = _base64url(secret)
        return PendingDeviceCredential(
            credential_id=credential_id,
            credential=f"{_CREDENTIAL_PREFIX}.{credential_id}.{encoded_secret}",
            secret_digest=hashlib.sha256(secret).digest(),
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
