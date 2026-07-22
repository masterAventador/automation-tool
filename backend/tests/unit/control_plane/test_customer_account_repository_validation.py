from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import pytest
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import SQLAlchemyError

from automation_tool.control_plane.application.customer_accounts import (
    AccountAuditActor,
    AccountAuditContext,
    AccountDataRejected,
    AccountPersistenceUnavailable,
    AccountTransitionRejected,
)
from automation_tool.control_plane.domain import (
    AccountAuditActorKind,
    AccountStatus,
    LoginName,
    PasswordHash,
    UserId,
)
from automation_tool.control_plane.infrastructure.database import Database
from automation_tool.control_plane.infrastructure.database.customer_account_repository import (
    SqlAlchemyCustomerAccountRepository,
    _account_record,
    _password_hash,
    _transition_event,
)

NOW = datetime(2026, 7, 22, 10, 0, tzinfo=UTC)
ACTOR_ID = UUID("123e4567-e89b-42d3-a456-426614174000")
ENCODED_HASH = "$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$aGFzaA"


class FailingContext(AbstractAsyncContextManager[Any]):
    async def __aenter__(self) -> Any:
        raise SQLAlchemyError("private database detail")

    async def __aexit__(self, *_error: object) -> None:
        return None


class FailingDatabase(Database):
    def __init__(self) -> None:
        pass

    def session(self) -> Any:
        return FailingContext()


def audit() -> AccountAuditContext:
    return AccountAuditContext(
        actor=AccountAuditActor(
            kind=AccountAuditActorKind.OPERATIONS,
            actor_id=ACTOR_ID,
        ),
        request_id="request-1",
        occurred_at=NOW,
    )


def test_repository_helpers_reject_corrupt_rows_and_close_every_transition() -> None:
    with pytest.raises(AccountDataRejected):
        _account_record(cast(RowMapping, {}))
    with pytest.raises(AccountDataRejected):
        _password_hash(cast(RowMapping, {}))

    assert _transition_event(AccountStatus.ACTIVE, AccountStatus.LOCKED) == (
        "account.locked",
        "system_locked",
    )
    assert _transition_event(AccountStatus.LOCKED, AccountStatus.DISABLED) == (
        "account.disabled",
        "operations_disabled",
    )
    assert _transition_event(AccountStatus.LOCKED, AccountStatus.ACTIVE) == (
        "account.unlocked",
        "operations_unlocked",
    )
    assert _transition_event(AccountStatus.DISABLED, AccountStatus.ACTIVE) == (
        "account.enabled",
        "operations_restored",
    )
    with pytest.raises(AccountTransitionRejected):
        _transition_event(AccountStatus.ACTIVE, AccountStatus.ACTIVE)


@pytest.mark.asyncio
async def test_repository_normalizes_database_failures_without_private_details() -> None:
    with pytest.raises(AccountPersistenceUnavailable):
        SqlAlchemyCustomerAccountRepository(cast(Database, object()))

    repository = SqlAlchemyCustomerAccountRepository(FailingDatabase())
    user_id = UserId.new()
    login_name = LoginName.parse("alice.ops")
    password_hash = PasswordHash(encoded=ENCODED_HASH, pepper_version=1)

    operations = (
        repository.create(
            user_id=user_id,
            login_name=login_name,
            password_hash=password_hash,
            audit=audit(),
        ),
        repository.find_for_authentication(login_name),
        repository.transition(
            user_id=user_id,
            expected_revision=1,
            target_status=AccountStatus.DISABLED,
            audit=audit(),
        ),
    )
    for operation in operations:
        with pytest.raises(AccountPersistenceUnavailable) as captured:
            await operation
        assert "private" not in repr(captured.value)
