"""One-time proof-of-possession registration for App installations."""

import base64
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from automation_tool.control_plane.application.device_credentials import (
    DeviceCredentialFactory,
    IssuedDeviceCredential,
    PendingDeviceCredential,
)
from automation_tool.control_plane.domain import (
    BootstrapAuthorizationDenied,
    BootstrapPurpose,
    DemoBootstrapGrant,
    DemoEnvironmentId,
    InvalidDemoEnvironmentId,
)

CHALLENGE_LIFETIME = timedelta(minutes=5)
_DEVICE_PUBLIC_KEY_LENGTH = 32
_NONCE_LENGTH = 32


class BootstrapCredentialRejected(ValueError):
    """The presented bootstrap token cannot be authenticated."""

    def __init__(self) -> None:
        super().__init__("Bootstrap credential rejected")


class BootstrapRegistrationDenied(PermissionError):
    """The signed bootstrap cannot authorize this registration scope."""

    def __init__(self) -> None:
        super().__init__("Bootstrap registration denied")


class InvalidRegistrationRequest(ValueError):
    """Registration input is not canonical or safely bounded."""

    def __init__(self) -> None:
        super().__init__("Invalid installation registration request")


class RegistrationProofRejected(PermissionError):
    """Challenge bindings or device proof do not match."""

    def __init__(self) -> None:
        super().__init__("Installation registration proof rejected")


class RegistrationChallengeExpired(PermissionError):
    """A matching challenge reached its exclusive expiry boundary."""

    def __init__(self) -> None:
        super().__init__("Installation registration challenge expired")


class RegistrationChallengeUsed(PermissionError):
    """A matching challenge was already consumed."""

    def __init__(self) -> None:
        super().__init__("Installation registration challenge already used")


class InstallationAlreadyRegistered(PermissionError):
    """The device public key already owns an installation."""

    def __init__(self) -> None:
        super().__init__("Installation already registered")


@dataclass(frozen=True, slots=True)
class VerifiedBootstrapCredential:
    """Typed signed grant plus a non-reversible token binding."""

    grant: DemoBootstrapGrant
    fingerprint: bytes


@dataclass(frozen=True, slots=True)
class RegistrationChallengeRecord:
    challenge_id: UUID
    environment_id: DemoEnvironmentId
    bootstrap_fingerprint: bytes
    device_public_key: bytes
    proof_hash: bytes
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class IssuedRegistrationChallenge:
    challenge_id: UUID
    signing_payload: bytes
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class RegisteredInstallation:
    installation_id: UUID
    status: str
    revision: int
    device_credential: IssuedDeviceCredential


class Clock(Protocol):
    def now(self) -> datetime: ...


class BootstrapTokenVerifier(Protocol):
    def verify(self, token: str) -> VerifiedBootstrapCredential: ...


class InstallationRegistrationRepository(Protocol):
    async def save_challenge(self, challenge: RegistrationChallengeRecord) -> None: ...

    async def complete_challenge(
        self,
        *,
        challenge_id: UUID,
        environment_id: DemoEnvironmentId,
        bootstrap_fingerprint: bytes,
        signing_payload: bytes,
        signature: bytes,
        completed_at: datetime,
        initial_credential: PendingDeviceCredential,
    ) -> RegisteredInstallation: ...


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _utc(value: object) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise InvalidRegistrationRequest
    return value.astimezone(UTC)


def _environment(value: object) -> DemoEnvironmentId:
    try:
        return DemoEnvironmentId.parse(value)
    except InvalidDemoEnvironmentId:
        raise InvalidRegistrationRequest from None


def _authorize(
    verifier: BootstrapTokenVerifier,
    *,
    token: str,
    environment_id: DemoEnvironmentId,
    at: datetime,
) -> VerifiedBootstrapCredential:
    try:
        verified = verifier.verify(token)
    except BootstrapCredentialRejected:
        raise
    except ValueError:
        raise BootstrapCredentialRejected from None
    try:
        verified.grant.authorize(
            purpose=BootstrapPurpose.REGISTER_INSTALLATION,
            environment_id=environment_id,
            at=at,
        )
    except BootstrapAuthorizationDenied:
        raise BootstrapRegistrationDenied from None
    return verified


def _signing_payload(
    *,
    challenge_id: UUID,
    environment_id: DemoEnvironmentId,
    device_public_key: bytes,
    nonce: bytes,
    expires_at: datetime,
) -> bytes:
    document = {
        "challenge": _base64url(nonce),
        "challengeId": str(challenge_id),
        "devicePublicKey": _base64url(device_public_key),
        "environmentId": str(environment_id),
        "expiresAt": int(expires_at.timestamp()),
        "purpose": BootstrapPurpose.REGISTER_INSTALLATION.value,
        "version": 1,
    }
    return json.dumps(
        document,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


class InstallationRegistrationService:
    """Authorize bootstrap scope and orchestrate one-time device proof."""

    def __init__(
        self,
        *,
        repository: InstallationRegistrationRepository,
        bootstrap_verifier: BootstrapTokenVerifier,
        expected_environment_id: DemoEnvironmentId,
        clock: Clock,
        nonce_source: Callable[[int], bytes],
        credential_factory: DeviceCredentialFactory,
    ) -> None:
        self._repository = repository
        self._bootstrap_verifier = bootstrap_verifier
        self._expected_environment_id = expected_environment_id
        self._clock = clock
        self._nonce_source = nonce_source
        self._credential_factory = credential_factory

    async def issue_challenge(
        self,
        *,
        bootstrap_token: str,
        environment_id: object,
        device_public_key: bytes,
    ) -> IssuedRegistrationChallenge:
        if (
            not isinstance(device_public_key, bytes)
            or len(device_public_key) != _DEVICE_PUBLIC_KEY_LENGTH
        ):
            raise InvalidRegistrationRequest
        environment = _environment(environment_id)
        if environment != self._expected_environment_id:
            raise BootstrapRegistrationDenied
        now = _utc(self._clock.now())
        verified = _authorize(
            self._bootstrap_verifier,
            token=bootstrap_token,
            environment_id=environment,
            at=now,
        )
        nonce = self._nonce_source(_NONCE_LENGTH)
        if not isinstance(nonce, bytes) or len(nonce) != _NONCE_LENGTH:
            raise RuntimeError("Secure challenge generation failed")
        challenge_id = uuid4()
        expires_at = min(now + CHALLENGE_LIFETIME, verified.grant.expires_at)
        signing_payload = _signing_payload(
            challenge_id=challenge_id,
            environment_id=environment,
            device_public_key=device_public_key,
            nonce=nonce,
            expires_at=expires_at,
        )
        await self._repository.save_challenge(
            RegistrationChallengeRecord(
                challenge_id=challenge_id,
                environment_id=environment,
                bootstrap_fingerprint=verified.fingerprint,
                device_public_key=device_public_key,
                proof_hash=hashlib.sha256(signing_payload).digest(),
                created_at=now,
                expires_at=expires_at,
            )
        )
        return IssuedRegistrationChallenge(
            challenge_id=challenge_id,
            signing_payload=signing_payload,
            expires_at=expires_at,
        )

    async def complete_registration(
        self,
        *,
        bootstrap_token: str,
        environment_id: object,
        challenge_id: UUID,
        signing_payload: bytes,
        signature: bytes,
    ) -> RegisteredInstallation:
        if (
            not isinstance(challenge_id, UUID)
            or not isinstance(signing_payload, bytes)
            or not signing_payload
            or len(signing_payload) > 2048
            or not isinstance(signature, bytes)
            or len(signature) != 64
        ):
            raise InvalidRegistrationRequest
        environment = _environment(environment_id)
        if environment != self._expected_environment_id:
            raise BootstrapRegistrationDenied
        now = _utc(self._clock.now())
        verified = _authorize(
            self._bootstrap_verifier,
            token=bootstrap_token,
            environment_id=environment,
            at=now,
        )
        initial_credential = self._credential_factory.create()
        return await self._repository.complete_challenge(
            challenge_id=challenge_id,
            environment_id=environment,
            bootstrap_fingerprint=verified.fingerprint,
            signing_payload=signing_payload,
            signature=signature,
            completed_at=now,
            initial_credential=initial_credential,
        )


__all__ = [
    "CHALLENGE_LIFETIME",
    "BootstrapCredentialRejected",
    "BootstrapRegistrationDenied",
    "InstallationAlreadyRegistered",
    "InstallationRegistrationService",
    "InvalidRegistrationRequest",
    "IssuedRegistrationChallenge",
    "RegisteredInstallation",
    "RegistrationChallengeExpired",
    "RegistrationChallengeRecord",
    "RegistrationChallengeUsed",
    "RegistrationProofRejected",
    "VerifiedBootstrapCredential",
]
