from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import cast
from uuid import UUID

import pytest

from automation_tool.control_plane.application.customer_accounts import (
    AccountAuditActor,
    AccountAuditContext,
    AccountRecord,
    CustomerAccountRepository,
    CustomerAccountService,
    EmergencyRevocationRecord,
)
from automation_tool.control_plane.domain import (
    AccountAuditActorKind,
    AccountStatus,
    InvalidAccountModel,
    LoginName,
    PasswordHash,
    UserId,
)

NOW = datetime(2026, 7, 22, 10, 0, tzinfo=UTC)
ACTOR_ID = UUID("123e4567-e89b-42d3-a456-426614174000")
ENCODED_HASH = "$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$aGFzaA"


@dataclass
class FixedClock:
    current: object = NOW

    def now(self) -> datetime:
        return cast(datetime, self.current)


class FixedHasher:
    def hash(self, _password: object) -> PasswordHash:
        return PasswordHash(encoded=ENCODED_HASH, pepper_version=1)


class UnusedRepository:
    async def create(self, **_values: object) -> None:
        raise AssertionError("repository must not be called")

    async def transition(self, **_values: object) -> None:
        raise AssertionError("repository must not be called")

    async def emergency_revoke(self, **_values: object) -> None:
        raise AssertionError("repository must not be called")


def valid_actor() -> AccountAuditActor:
    return AccountAuditActor(kind=AccountAuditActorKind.OPERATIONS, actor_id=ACTOR_ID)


@pytest.mark.parametrize(
    ("kind", "actor_id", "source_fingerprint"),
    (
        ("operations", ACTOR_ID, None),
        (AccountAuditActorKind.OPERATIONS, str(ACTOR_ID), None),
        (AccountAuditActorKind.OPERATIONS, UUID(int=0), None),
        (AccountAuditActorKind.OPERATIONS, ACTOR_ID, b"short"),
        (AccountAuditActorKind.OPERATIONS, ACTOR_ID, "s" * 32),
    ),
)
def test_audit_actor_rejects_untyped_or_unbounded_identity(
    kind: object,
    actor_id: object,
    source_fingerprint: object,
) -> None:
    with pytest.raises(InvalidAccountModel):
        AccountAuditActor(
            kind=cast(AccountAuditActorKind, kind),
            actor_id=cast(UUID, actor_id),
            source_fingerprint=cast(bytes | None, source_fingerprint),
        )


@pytest.mark.parametrize(
    ("actor", "request_id", "occurred_at"),
    (
        (object(), "request-1", NOW),
        (valid_actor(), 1, NOW),
        (valid_actor(), "", NOW),
        (valid_actor(), "request space", NOW),
        (valid_actor(), "request-1", datetime(2026, 7, 22, 10, 0)),
        (valid_actor(), "request-1", NOW.astimezone(timezone(timedelta(hours=8)))),
    ),
)
def test_audit_context_requires_safe_request_id_and_exact_utc(
    actor: object,
    request_id: object,
    occurred_at: object,
) -> None:
    with pytest.raises(InvalidAccountModel):
        AccountAuditContext(
            actor=cast(AccountAuditActor, actor),
            request_id=cast(str, request_id),
            occurred_at=cast(datetime, occurred_at),
        )


@pytest.mark.asyncio
async def test_service_rejects_invalid_id_request_clock_and_revision_before_persistence() -> None:
    repository = cast(CustomerAccountRepository, UnusedRepository())
    bad_id_service = CustomerAccountService(
        repository=repository,
        password_hasher=FixedHasher(),
        clock=FixedClock(),
        id_source=cast(object, lambda: object()),  # type: ignore[arg-type]
    )
    with pytest.raises(InvalidAccountModel):
        await bad_id_service.create(
            login_name="alice.ops",
            password="valid password",
            actor=valid_actor(),
            request_id="request-1",
        )

    service = CustomerAccountService(
        repository=repository,
        password_hasher=FixedHasher(),
        clock=FixedClock(),
    )
    with pytest.raises(InvalidAccountModel):
        await service.create(
            login_name="alice.ops",
            password="valid password",
            actor=valid_actor(),
            request_id=1,
        )

    bad_clock_service = CustomerAccountService(
        repository=repository,
        password_hasher=FixedHasher(),
        clock=FixedClock(datetime(2026, 7, 22, 10, 0)),
    )
    with pytest.raises(InvalidAccountModel):
        await bad_clock_service.create(
            login_name="alice.ops",
            password="valid password",
            actor=valid_actor(),
            request_id="request-1",
        )

    for user_id, revision in (
        (object(), 1),
        (UserId.new(), True),
        (UserId.new(), 0),
    ):
        with pytest.raises(InvalidAccountModel):
            await service.disable(
                user_id=cast(UserId, user_id),
                expected_revision=revision,
                actor=valid_actor(),
                request_id="request-1",
            )
        with pytest.raises(InvalidAccountModel):
            await service.emergency_revoke(
                user_id=cast(UserId, user_id),
                expected_revision=revision,
                actor=valid_actor(),
                request_id="request-1",
            )


def account_record(*, status: AccountStatus = AccountStatus.DISABLED) -> AccountRecord:
    return AccountRecord(
        user_id=UserId.new(),
        login_name=LoginName.parse("emergency.ops"),
        status=status,
        credential_version=2,
        revision=2,
        created_at=NOW,
        updated_at=NOW,
        locked_at=None,
        lock_expires_at=None,
        disabled_at=NOW if status is AccountStatus.DISABLED else None,
    )


@pytest.mark.parametrize(
    ("account", "count"),
    (
        (object(), 0),
        (account_record(status=AccountStatus.ACTIVE), 0),
        (account_record(), True),
        (account_record(), -1),
    ),
)
def test_emergency_record_requires_disabled_account_and_nonnegative_exact_count(
    account: object,
    count: object,
) -> None:
    with pytest.raises(InvalidAccountModel):
        EmergencyRevocationRecord(
            account=cast(AccountRecord, account),
            revoked_device_count=cast(int, count),
        )
