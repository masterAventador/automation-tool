"""Account-authenticated, proof-of-possession Installation ownership binding."""

import base64
import hashlib
import json
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Protocol
from uuid import UUID, uuid4

from automation_tool.control_plane.application.account_sessions import (
    AuthenticatedAccountSession,
)
from automation_tool.control_plane.application.device_credentials import (
    DeviceCredentialFactory,
    PendingDeviceCredential,
)
from automation_tool.control_plane.application.registration import RegisteredInstallation
from automation_tool.control_plane.domain import UserId

BINDING_CHALLENGE_LIFETIME: Final = timedelta(minutes=5)
_DEVICE_KEY_LENGTH: Final = 32
_NONCE_LENGTH: Final = 32
_REQUEST_ID: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", re.ASCII)


class _BindingFailure(RuntimeError):
    message = "Installation binding failed"

    def __init__(self) -> None:
        super().__init__(self.message)


class InvalidBindingRequest(_BindingFailure):
    message = "Installation binding request is invalid"


class BindingProofRejected(_BindingFailure):
    message = "Installation binding proof is invalid"


class BindingChallengeExpired(_BindingFailure):
    message = "Installation binding challenge expired"


class BindingChallengeUsed(_BindingFailure):
    message = "Installation binding challenge was already used"


class CrossAccountBindingRejected(_BindingFailure):
    message = "Installation is owned by another account"


class RevokedInstallationBindingRejected(_BindingFailure):
    message = "Revoked Installation cannot be bound"


class AccountInstallationBindingUnavailable(_BindingFailure):
    message = "Installation binding is unavailable"


@dataclass(frozen=True, slots=True)
class AccountBindingChallengeRecord:
    challenge_id: UUID
    user_id: UserId
    device_public_key: bytes
    proof_hash: bytes
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class IssuedAccountBindingChallenge:
    challenge_id: UUID
    signing_payload: bytes
    expires_at: datetime


class Clock(Protocol):
    def now(self) -> datetime: ...


class AccountAccessAuthenticator(Protocol):
    async def authenticate(self, *, access_token: object) -> AuthenticatedAccountSession: ...


class AccountInstallationBindingRepository(Protocol):
    async def save_challenge(self, challenge: AccountBindingChallengeRecord) -> None: ...

    async def complete_challenge(
        self,
        *,
        challenge_id: UUID,
        user_id: UserId,
        signing_payload: bytes,
        signature: bytes,
        completed_at: datetime,
        pending_credential: PendingDeviceCredential,
        request_id: str,
    ) -> RegisteredInstallation: ...


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _now(clock: Clock) -> datetime:
    value = clock.now()
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise AccountInstallationBindingUnavailable
    return value.astimezone(UTC)


def _signing_payload(
    *,
    challenge_id: UUID,
    device_public_key: bytes,
    nonce: bytes,
    expires_at: datetime,
) -> bytes:
    return json.dumps(
        {
            "challenge": _base64url(nonce),
            "challengeId": str(challenge_id),
            "devicePublicKey": _base64url(device_public_key),
            "expiresAt": int(expires_at.timestamp()),
            "purpose": "account.installation.bind",
            "version": 1,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


class AccountInstallationBindingService:
    """Authenticate the account at both edges and delegate one atomic consumption."""

    def __init__(
        self,
        *,
        repository: AccountInstallationBindingRepository,
        account_sessions: AccountAccessAuthenticator,
        clock: Clock,
        nonce_source: Callable[[int], bytes] = secrets.token_bytes,
        id_source: Callable[[], UUID] = uuid4,
        credential_factory: DeviceCredentialFactory,
    ) -> None:
        self._repository = repository
        self._account_sessions = account_sessions
        self._clock = clock
        self._nonce_source = nonce_source
        self._id_source = id_source
        self._credential_factory = credential_factory

    async def issue_challenge(
        self, *, access_token: object, device_public_key: object
    ) -> IssuedAccountBindingChallenge:
        if type(device_public_key) is not bytes or len(device_public_key) != _DEVICE_KEY_LENGTH:
            raise InvalidBindingRequest
        authenticated = await self._account_sessions.authenticate(access_token=access_token)
        now = _now(self._clock)
        nonce = self._nonce_source(_NONCE_LENGTH)
        challenge_id = self._id_source()
        if (
            type(nonce) is not bytes
            or len(nonce) != _NONCE_LENGTH
            or type(challenge_id) is not UUID
            or challenge_id.version != 4
        ):
            raise AccountInstallationBindingUnavailable
        expires_at = now + BINDING_CHALLENGE_LIFETIME
        payload = _signing_payload(
            challenge_id=challenge_id,
            device_public_key=device_public_key,
            nonce=nonce,
            expires_at=expires_at,
        )
        await self._repository.save_challenge(
            AccountBindingChallengeRecord(
                challenge_id=challenge_id,
                user_id=authenticated.user_id,
                device_public_key=device_public_key,
                proof_hash=hashlib.sha256(payload).digest(),
                created_at=now,
                expires_at=expires_at,
            )
        )
        return IssuedAccountBindingChallenge(challenge_id, payload, expires_at)

    async def complete_binding(
        self,
        *,
        access_token: object,
        challenge_id: object,
        signing_payload: object,
        signature: object,
        request_id: object,
    ) -> RegisteredInstallation:
        if (
            type(challenge_id) is not UUID
            or challenge_id.version != 4
            or type(signing_payload) is not bytes
            or not 1 <= len(signing_payload) <= 2048
            or type(signature) is not bytes
            or len(signature) != 64
            or type(request_id) is not str
            or _REQUEST_ID.fullmatch(request_id) is None
        ):
            raise InvalidBindingRequest
        authenticated = await self._account_sessions.authenticate(access_token=access_token)
        return await self._repository.complete_challenge(
            challenge_id=challenge_id,
            user_id=authenticated.user_id,
            signing_payload=signing_payload,
            signature=signature,
            completed_at=_now(self._clock),
            pending_credential=self._credential_factory.create(),
            request_id=request_id,
        )


__all__ = [
    "BINDING_CHALLENGE_LIFETIME",
    "AccountBindingChallengeRecord",
    "AccountInstallationBindingRepository",
    "AccountInstallationBindingService",
    "AccountInstallationBindingUnavailable",
    "BindingChallengeExpired",
    "BindingChallengeUsed",
    "BindingProofRejected",
    "CrossAccountBindingRejected",
    "InvalidBindingRequest",
    "IssuedAccountBindingChallenge",
    "RevokedInstallationBindingRejected",
]
