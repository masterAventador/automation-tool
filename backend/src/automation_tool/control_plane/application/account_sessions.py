"""Opaque customer account sessions and password lifecycle application boundary."""

import hashlib
import hmac
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Protocol
from uuid import UUID, uuid4

from automation_tool.control_plane.application.customer_accounts import AccountAuditActor
from automation_tool.control_plane.application.opaque_bearers import (
    InvalidOpaqueBearer,
    OpaqueBearerCodec,
)
from automation_tool.control_plane.domain import (
    AccountStatus,
    InvalidAccountModel,
    LoginName,
    PasswordHash,
    UserId,
)

ACCOUNT_ACCESS_LIFETIME: Final = timedelta(minutes=10)
ACCOUNT_REFRESH_LIFETIME: Final = timedelta(days=30)
ACCOUNT_RECOVERY_LIFETIME: Final = timedelta(minutes=15)
LOGIN_FAILURE_WINDOW: Final = timedelta(minutes=15)
LOGIN_LOCK_LIFETIME: Final = timedelta(minutes=15)
LOGIN_IDENTIFIER_FAILURE_LIMIT: Final = 5
LOGIN_SOURCE_FAILURE_LIMIT: Final = 20

_ACCESS_CODEC: Final = OpaqueBearerCodec("atas1")
_REFRESH_CODEC: Final = OpaqueBearerCodec("atrs1")
_RECOVERY_CODEC: Final = OpaqueBearerCodec("atrp1")
_REQUEST_ID: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", re.ASCII)
_SOURCE_ADDRESS: Final = re.compile(r"^[A-Za-z0-9.:[\]_-]{1,255}$", re.ASCII)


class _AccountSessionFailure(RuntimeError):
    message = "Account session operation failed"

    def __init__(self) -> None:
        super().__init__(self.message)


class AccountAuthenticationRejected(_AccountSessionFailure):
    message = "Account authentication is invalid"


class AccountSessionRejected(_AccountSessionFailure):
    message = "Account session is invalid"


class AccountRecoveryRejected(_AccountSessionFailure):
    message = "Account recovery is invalid"


class AccountSessionUnavailable(_AccountSessionFailure):
    message = "Account sessions are unavailable"


@dataclass(frozen=True, slots=True)
class AccountProjection:
    user_id: UserId
    login_name: LoginName
    status: AccountStatus


@dataclass(frozen=True, slots=True)
class PendingAccountToken:
    token_id: UUID
    token: str
    secret_digest: bytes


@dataclass(frozen=True, slots=True)
class ParsedAccountToken:
    token_id: UUID
    secret_digest: bytes


@dataclass(frozen=True, slots=True)
class PendingAccountSession:
    family_id: UUID
    access: PendingAccountToken
    refresh: PendingAccountToken


@dataclass(frozen=True, slots=True)
class IssuedAccountSession:
    access_token: str
    refresh_token: str
    access_expires_at: datetime
    refresh_expires_at: datetime
    account: AccountProjection


@dataclass(frozen=True, slots=True)
class AuthenticatedAccountSession:
    token_id: UUID
    family_id: UUID
    user_id: UserId
    credential_version: int
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class PendingRecoveryToken:
    token_id: UUID
    token: str
    secret_digest: bytes


@dataclass(frozen=True, slots=True)
class IssuedRecoveryToken:
    recovery_token: str
    expires_at: datetime
    account: AccountProjection


PasswordVerifier = Callable[[PasswordHash], bool]


class AccountSessionRepository(Protocol):
    async def login(
        self,
        *,
        login_name: LoginName,
        identifier_fingerprint: bytes,
        source_fingerprint: bytes,
        verify_password: PasswordVerifier,
        dummy_password_hash: PasswordHash,
        pending: PendingAccountSession,
        authenticated_at: datetime,
        request_id: str,
    ) -> IssuedAccountSession: ...

    async def refresh(
        self,
        *,
        presented: ParsedAccountToken,
        pending: PendingAccountSession,
        refreshed_at: datetime,
        source_fingerprint: bytes,
        request_id: str,
    ) -> IssuedAccountSession: ...

    async def logout(
        self,
        *,
        presented: ParsedAccountToken,
        logged_out_at: datetime,
        request_id: str,
    ) -> None: ...

    async def authenticate_access(
        self,
        *,
        presented: ParsedAccountToken,
        authenticated_at: datetime,
    ) -> AuthenticatedAccountSession: ...

    async def change_password(
        self,
        *,
        presented: ParsedAccountToken,
        verify_current_password: PasswordVerifier,
        replacement: PasswordHash,
        changed_at: datetime,
        request_id: str,
    ) -> None: ...

    async def issue_recovery(
        self,
        *,
        login_name: LoginName,
        pending: PendingRecoveryToken,
        actor: AccountAuditActor,
        issued_at: datetime,
        request_id: str,
    ) -> IssuedRecoveryToken: ...

    async def recover_password(
        self,
        *,
        presented: ParsedAccountToken,
        replacement: PasswordHash,
        recovered_at: datetime,
        request_id: str,
    ) -> None: ...


class AccountPasswordHasher(Protocol):
    def hash(self, password: object) -> PasswordHash: ...

    def verify(self, password: object, stored: object) -> bool: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


def _create_token(
    codec: OpaqueBearerCodec,
    *,
    secret_source: Callable[[int], bytes],
    id_source: Callable[[], UUID],
) -> PendingAccountToken:
    try:
        material = codec.create(secret_source=secret_source, id_source=id_source)
    except RuntimeError:
        raise RuntimeError("Account token generation failed") from None
    return PendingAccountToken(
        token_id=material.bearer_id,
        token=material.bearer,
        secret_digest=material.secret_digest,
    )


class AccountSessionFactory:
    def __init__(
        self,
        *,
        secret_source: Callable[[int], bytes] = secrets.token_bytes,
        id_source: Callable[[], UUID] = uuid4,
    ) -> None:
        self._secret_source = secret_source
        self._id_source = id_source

    def create(self) -> PendingAccountSession:
        family_id = self._id_source()
        if not isinstance(family_id, UUID) or family_id.version != 4:
            raise RuntimeError("Account token generation failed")
        return PendingAccountSession(
            family_id=family_id,
            access=_create_token(
                _ACCESS_CODEC,
                secret_source=self._secret_source,
                id_source=self._id_source,
            ),
            refresh=_create_token(
                _REFRESH_CODEC,
                secret_source=self._secret_source,
                id_source=self._id_source,
            ),
        )


class AccountRecoveryFactory:
    def __init__(
        self,
        *,
        secret_source: Callable[[int], bytes] = secrets.token_bytes,
        id_source: Callable[[], UUID] = uuid4,
    ) -> None:
        self._secret_source = secret_source
        self._id_source = id_source

    def create(self) -> PendingRecoveryToken:
        token = _create_token(
            _RECOVERY_CODEC,
            secret_source=self._secret_source,
            id_source=self._id_source,
        )
        return PendingRecoveryToken(
            token_id=token.token_id,
            token=token.token,
            secret_digest=token.secret_digest,
        )


def _parse_token(codec: OpaqueBearerCodec, value: object) -> ParsedAccountToken:
    try:
        parsed = codec.parse(value)
    except InvalidOpaqueBearer:
        raise AccountSessionRejected from None
    return ParsedAccountToken(token_id=parsed.bearer_id, secret_digest=parsed.secret_digest)


def parse_access_token(value: object) -> ParsedAccountToken:
    return _parse_token(_ACCESS_CODEC, value)


def parse_refresh_token(value: object) -> ParsedAccountToken:
    return _parse_token(_REFRESH_CODEC, value)


def parse_recovery_token(value: object) -> ParsedAccountToken:
    try:
        return _parse_token(_RECOVERY_CODEC, value)
    except AccountSessionRejected:
        raise AccountRecoveryRejected from None


def _utc(clock: Clock) -> datetime:
    now = clock.now()
    if type(now) is not datetime or now.tzinfo is None or now.utcoffset() != timedelta(0):
        raise AccountSessionUnavailable
    return now.astimezone(UTC)


def _request_id(value: object) -> str:
    if type(value) is not str or _REQUEST_ID.fullmatch(value) is None:
        raise InvalidAccountModel
    return value


class AccountSessionService:
    """Keep plaintext passwords transient while repositories receive verifier closures."""

    def __init__(
        self,
        *,
        repository: AccountSessionRepository,
        password_hasher: AccountPasswordHasher,
        clock: Clock,
        session_factory: AccountSessionFactory,
        recovery_factory: AccountRecoveryFactory,
        fingerprint_key: object,
        dummy_password_hash: PasswordHash,
    ) -> None:
        if type(fingerprint_key) is not bytes or len(fingerprint_key) != 32:
            raise AccountSessionUnavailable
        self._repository = repository
        self._password_hasher = password_hasher
        self._clock = clock
        self._session_factory = session_factory
        self._recovery_factory = recovery_factory
        self._fingerprint_key = fingerprint_key
        self._dummy_password_hash = dummy_password_hash

    def _fingerprint(self, domain: bytes, value: str) -> bytes:
        return hmac.digest(
            self._fingerprint_key,
            b"automation-tool:account:" + domain + b"\x00" + value.encode("ascii"),
            hashlib.sha256,
        )

    def _source_fingerprint(self, value: object) -> bytes:
        if type(value) is not str or _SOURCE_ADDRESS.fullmatch(value) is None:
            raise AccountAuthenticationRejected
        return self._fingerprint(b"source", value)

    async def login(
        self,
        *,
        login_name: object,
        password: object,
        source_address: object,
        request_id: object,
    ) -> IssuedAccountSession:
        canonical = LoginName.parse(login_name)
        return await self._repository.login(
            login_name=canonical,
            identifier_fingerprint=self._fingerprint(b"identifier", canonical.value),
            source_fingerprint=self._source_fingerprint(source_address),
            verify_password=lambda stored: self._password_hasher.verify(password, stored),
            dummy_password_hash=self._dummy_password_hash,
            pending=self._session_factory.create(),
            authenticated_at=_utc(self._clock),
            request_id=_request_id(request_id),
        )

    async def refresh(
        self,
        *,
        refresh_token: object,
        source_address: object,
        request_id: object,
    ) -> IssuedAccountSession:
        return await self._repository.refresh(
            presented=parse_refresh_token(refresh_token),
            pending=self._session_factory.create(),
            refreshed_at=_utc(self._clock),
            source_fingerprint=self._source_fingerprint(source_address),
            request_id=_request_id(request_id),
        )

    async def logout(self, *, refresh_token: object, request_id: object) -> None:
        await self._repository.logout(
            presented=parse_refresh_token(refresh_token),
            logged_out_at=_utc(self._clock),
            request_id=_request_id(request_id),
        )

    async def authenticate(self, *, access_token: object) -> AuthenticatedAccountSession:
        return await self._repository.authenticate_access(
            presented=parse_access_token(access_token),
            authenticated_at=_utc(self._clock),
        )

    async def change_password(
        self,
        *,
        access_token: object,
        current_password: object,
        new_password: object,
        request_id: object,
    ) -> None:
        replacement = self._password_hasher.hash(new_password)
        await self._repository.change_password(
            presented=parse_access_token(access_token),
            verify_current_password=lambda stored: self._password_hasher.verify(
                current_password,
                stored,
            ),
            replacement=replacement,
            changed_at=_utc(self._clock),
            request_id=_request_id(request_id),
        )

    async def issue_recovery(
        self,
        *,
        login_name: object,
        actor: AccountAuditActor,
        request_id: object,
    ) -> IssuedRecoveryToken:
        return await self._repository.issue_recovery(
            login_name=LoginName.parse(login_name),
            pending=self._recovery_factory.create(),
            actor=actor,
            issued_at=_utc(self._clock),
            request_id=_request_id(request_id),
        )

    async def recover_password(
        self,
        *,
        recovery_token: object,
        new_password: object,
        request_id: object,
    ) -> None:
        await self._repository.recover_password(
            presented=parse_recovery_token(recovery_token),
            replacement=self._password_hasher.hash(new_password),
            recovered_at=_utc(self._clock),
            request_id=_request_id(request_id),
        )


__all__ = [
    "ACCOUNT_ACCESS_LIFETIME",
    "ACCOUNT_RECOVERY_LIFETIME",
    "ACCOUNT_REFRESH_LIFETIME",
    "LOGIN_FAILURE_WINDOW",
    "LOGIN_IDENTIFIER_FAILURE_LIMIT",
    "LOGIN_LOCK_LIFETIME",
    "LOGIN_SOURCE_FAILURE_LIMIT",
    "AccountAuthenticationRejected",
    "AccountProjection",
    "AccountRecoveryFactory",
    "AccountRecoveryRejected",
    "AccountSessionFactory",
    "AccountSessionRejected",
    "AccountSessionRepository",
    "AccountSessionService",
    "AccountSessionUnavailable",
    "AuthenticatedAccountSession",
    "IssuedAccountSession",
    "IssuedRecoveryToken",
    "ParsedAccountToken",
    "PasswordVerifier",
    "PendingAccountSession",
    "PendingAccountToken",
    "PendingRecoveryToken",
    "parse_access_token",
    "parse_recovery_token",
    "parse_refresh_token",
]
