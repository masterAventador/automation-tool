"""Application boundary for operations-managed customer accounts."""

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from automation_tool.control_plane.domain import (
    AccountAuditActorKind,
    AccountStatus,
    InvalidAccountModel,
    LoginName,
    PasswordHash,
    UserId,
)

_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", re.ASCII)


class _AccountFailure(RuntimeError):
    message = "Account operation failed"

    def __init__(self) -> None:
        super().__init__(self.message)


class AccountAlreadyExists(_AccountFailure):
    message = "Account already exists"


class AccountNotFound(_AccountFailure):
    message = "Account was not found"


class AccountRevisionConflict(_AccountFailure):
    message = "Account revision conflict"


class AccountTransitionRejected(_AccountFailure):
    message = "Account transition is rejected"


class AccountPersistenceUnavailable(_AccountFailure):
    message = "Account persistence is unavailable"


class AccountDataRejected(_AccountFailure):
    message = "Account data is rejected"


@dataclass(frozen=True, slots=True)
class AccountAuditActor:
    kind: AccountAuditActorKind
    actor_id: UUID
    source_fingerprint: bytes | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.kind, AccountAuditActorKind)
            or type(self.actor_id) is not UUID
            or self.actor_id.version != 4
            or (
                self.source_fingerprint is not None
                and (
                    type(self.source_fingerprint) is not bytes or len(self.source_fingerprint) != 32
                )
            )
        ):
            raise InvalidAccountModel


@dataclass(frozen=True, slots=True)
class AccountAuditContext:
    actor: AccountAuditActor
    request_id: str
    occurred_at: datetime

    def __post_init__(self) -> None:
        if (
            not isinstance(self.actor, AccountAuditActor)
            or type(self.request_id) is not str
            or _REQUEST_ID.fullmatch(self.request_id) is None
            or type(self.occurred_at) is not datetime
            or self.occurred_at.tzinfo is None
            or self.occurred_at.utcoffset() != timedelta(0)
        ):
            raise InvalidAccountModel


@dataclass(frozen=True, slots=True)
class AccountRecord:
    user_id: UserId
    login_name: LoginName
    status: AccountStatus
    credential_version: int
    revision: int
    created_at: datetime
    updated_at: datetime
    locked_at: datetime | None
    lock_expires_at: datetime | None
    disabled_at: datetime | None


@dataclass(frozen=True, slots=True)
class AccountAuthenticationRecord:
    account: AccountRecord
    password_hash: PasswordHash


class CustomerAccountRepository(Protocol):
    async def create(
        self,
        *,
        user_id: UserId,
        login_name: LoginName,
        password_hash: PasswordHash,
        audit: AccountAuditContext,
    ) -> AccountRecord: ...

    async def transition(
        self,
        *,
        user_id: UserId,
        expected_revision: int,
        target_status: AccountStatus,
        audit: AccountAuditContext,
    ) -> AccountRecord: ...


class AccountPasswordHasher(Protocol):
    def hash(self, password: object) -> PasswordHash: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


class CustomerAccountService:
    """Validate inputs before atomic PostgreSQL account operations."""

    def __init__(
        self,
        *,
        repository: CustomerAccountRepository,
        password_hasher: AccountPasswordHasher,
        clock: Clock,
        id_source: Callable[[], UserId] = UserId.new,
    ) -> None:
        self._repository = repository
        self._password_hasher = password_hasher
        self._clock = clock
        self._id_source = id_source

    def _audit(self, *, actor: AccountAuditActor, request_id: object) -> AccountAuditContext:
        now = self._clock.now()
        if type(request_id) is not str:
            raise InvalidAccountModel
        return AccountAuditContext(actor=actor, request_id=request_id, occurred_at=now)

    async def create(
        self,
        *,
        login_name: object,
        password: object,
        actor: AccountAuditActor,
        request_id: object,
    ) -> AccountRecord:
        user_id = self._id_source()
        if not isinstance(user_id, UserId):
            raise InvalidAccountModel
        return await self._repository.create(
            user_id=user_id,
            login_name=LoginName.parse(login_name),
            password_hash=self._password_hasher.hash(password),
            audit=self._audit(actor=actor, request_id=request_id),
        )

    async def disable(
        self,
        *,
        user_id: UserId,
        expected_revision: object,
        actor: AccountAuditActor,
        request_id: object,
    ) -> AccountRecord:
        return await self._transition(
            user_id=user_id,
            expected_revision=expected_revision,
            target_status=AccountStatus.DISABLED,
            actor=actor,
            request_id=request_id,
        )

    async def restore(
        self,
        *,
        user_id: UserId,
        expected_revision: object,
        actor: AccountAuditActor,
        request_id: object,
    ) -> AccountRecord:
        return await self._transition(
            user_id=user_id,
            expected_revision=expected_revision,
            target_status=AccountStatus.ACTIVE,
            actor=actor,
            request_id=request_id,
        )

    async def _transition(
        self,
        *,
        user_id: UserId,
        expected_revision: object,
        target_status: AccountStatus,
        actor: AccountAuditActor,
        request_id: object,
    ) -> AccountRecord:
        if (
            not isinstance(user_id, UserId)
            or type(expected_revision) is not int
            or expected_revision <= 0
        ):
            raise InvalidAccountModel
        return await self._repository.transition(
            user_id=user_id,
            expected_revision=expected_revision,
            target_status=target_status,
            audit=self._audit(actor=actor, request_id=request_id),
        )


__all__ = [
    "AccountAlreadyExists",
    "AccountAuditActor",
    "AccountAuditContext",
    "AccountAuthenticationRecord",
    "AccountDataRejected",
    "AccountNotFound",
    "AccountPersistenceUnavailable",
    "AccountRecord",
    "AccountRevisionConflict",
    "AccountTransitionRejected",
    "CustomerAccountRepository",
    "CustomerAccountService",
]
