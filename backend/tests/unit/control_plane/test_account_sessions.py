from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from automation_tool.control_plane.application.account_sessions import (
    AccountAuthenticationRejected,
    AccountRecoveryFactory,
    AccountRecoveryRejected,
    AccountSessionFactory,
    AccountSessionRejected,
    AccountSessionRepository,
    AccountSessionService,
    AccountSessionUnavailable,
    ParsedAccountToken,
    _request_id,
    _utc,
    parse_access_token,
    parse_recovery_token,
    parse_refresh_token,
)
from automation_tool.control_plane.application.customer_accounts import AccountAuditActor
from automation_tool.control_plane.domain import (
    AccountAuditActorKind,
    InvalidAccountModel,
    LoginName,
    PasswordHash,
)
from automation_tool.control_plane.infrastructure.database import Database
from automation_tool.control_plane.infrastructure.database.account_session_repository import (
    SqlAlchemyAccountSessionRepository,
)

NOW = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)
HASH = PasswordHash(
    encoded="$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$aGFzaA",
    pepper_version=1,
)
IDS = (
    UUID("123e4567-e89b-42d3-a456-426614174000"),
    UUID("123e4567-e89b-42d3-a456-426614174001"),
    UUID("123e4567-e89b-42d3-a456-426614174002"),
)


class FixedClock:
    def __init__(self, current: object = NOW) -> None:
        self.current = current

    def now(self) -> datetime:
        return cast(datetime, self.current)


class FixedHasher:
    def hash(self, _password: object) -> PasswordHash:
        return HASH

    def verify(self, _password: object, _stored: object) -> bool:
        return True


def deterministic_factory() -> AccountSessionFactory:
    identifiers = iter(IDS)
    return AccountSessionFactory(
        secret_source=lambda length: b"s" * length,
        id_source=lambda: next(identifiers),
    )


def test_account_factories_create_parseable_digest_only_tokens() -> None:
    created = deterministic_factory().create()
    recovery = AccountRecoveryFactory(
        secret_source=lambda length: b"r" * length,
        id_source=lambda: IDS[0],
    ).create()

    assert parse_access_token(created.access.token).token_id == IDS[1]
    assert parse_refresh_token(created.refresh.token).token_id == IDS[2]
    assert parse_recovery_token(recovery.token).token_id == IDS[0]
    assert created.access.token.encode() not in created.access.secret_digest
    assert recovery.token.encode() not in recovery.secret_digest


@pytest.mark.parametrize("family_id", (object(), UUID(int=0)))
def test_account_factory_rejects_invalid_family_identifiers(family_id: object) -> None:
    with pytest.raises(RuntimeError, match="Account token generation failed"):
        AccountSessionFactory(
            id_source=cast(object, lambda: family_id),  # type: ignore[arg-type]
        ).create()


def test_account_factory_normalizes_opaque_codec_generation_failures() -> None:
    identifiers = iter(IDS)
    with pytest.raises(RuntimeError, match="Account token generation failed"):
        AccountSessionFactory(
            secret_source=cast(object, lambda _length: b"short"),  # type: ignore[arg-type]
            id_source=lambda: next(identifiers),
        ).create()


@pytest.mark.parametrize("parser", (parse_access_token, parse_refresh_token))
def test_session_parsers_reject_noncanonical_values(parser: object) -> None:
    with pytest.raises(AccountSessionRejected):
        cast(Callable[[object], ParsedAccountToken], parser)("private")


def test_recovery_parser_has_a_distinct_uniform_rejection() -> None:
    with pytest.raises(AccountRecoveryRejected):
        parse_recovery_token("private")


@pytest.mark.parametrize("current", (None, datetime(2026, 7, 22, 12, 0)))
def test_account_clock_requires_exact_utc(current: object) -> None:
    with pytest.raises(AccountSessionUnavailable):
        _utc(FixedClock(current))


@pytest.mark.parametrize("request_id", (None, "", "contains space"))
def test_account_request_id_is_closed(request_id: object) -> None:
    with pytest.raises(InvalidAccountModel):
        _request_id(request_id)


def test_account_service_rejects_bad_fingerprint_configuration_and_source() -> None:
    with pytest.raises(AccountSessionUnavailable):
        AccountSessionService(
            repository=cast(AccountSessionRepository, object()),
            password_hasher=FixedHasher(),
            clock=FixedClock(),
            session_factory=deterministic_factory(),
            recovery_factory=AccountRecoveryFactory(),
            fingerprint_key=b"short",
            dummy_password_hash=HASH,
        )
    service = AccountSessionService(
        repository=cast(AccountSessionRepository, object()),
        password_hasher=FixedHasher(),
        clock=FixedClock(),
        session_factory=deterministic_factory(),
        recovery_factory=AccountRecoveryFactory(),
        fingerprint_key=b"f" * 32,
        dummy_password_hash=HASH,
    )
    with pytest.raises(AccountAuthenticationRejected):
        service._source_fingerprint("private source with spaces")


@pytest.mark.asyncio
async def test_repository_normalizes_database_failures_and_rejects_wrong_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(AccountSessionUnavailable):
        SqlAlchemyAccountSessionRepository(cast(Database, object()))

    database = Database.from_url(
        "postgresql+asyncpg://unused:unused@127.0.0.1:1/unused",
        connect_timeout_seconds=0.01,
    )

    @asynccontextmanager
    async def failing_session() -> AsyncIterator[AsyncSession]:
        raise SQLAlchemyError("private database error")
        yield cast(AsyncSession, object())

    monkeypatch.setattr(database, "session", failing_session)
    repository = SqlAlchemyAccountSessionRepository(database)
    pending = deterministic_factory().create()
    presented_access = parse_access_token(pending.access.token)
    presented_refresh = parse_refresh_token(pending.refresh.token)
    recovery = AccountRecoveryFactory(
        secret_source=lambda length: b"r" * length,
        id_source=lambda: IDS[0],
    ).create()
    actor = AccountAuditActor(
        kind=AccountAuditActorKind.OPERATIONS,
        actor_id=IDS[0],
    )

    operations = (
        repository.login(
            login_name=LoginName.parse("alice.ops"),
            identifier_fingerprint=b"i" * 32,
            source_fingerprint=b"s" * 32,
            verify_password=lambda _stored: True,
            dummy_password_hash=HASH,
            pending=pending,
            authenticated_at=NOW,
            request_id="login",
        ),
        repository.refresh(
            presented=presented_refresh,
            pending=pending,
            refreshed_at=NOW,
            source_fingerprint=b"s" * 32,
            request_id="refresh",
        ),
        repository.logout(
            presented=presented_refresh,
            logged_out_at=NOW,
            request_id="logout",
        ),
        repository.authenticate_access(
            presented=presented_access,
            authenticated_at=NOW,
        ),
        repository.change_password(
            presented=presented_access,
            verify_current_password=lambda _stored: True,
            replacement=HASH,
            changed_at=NOW,
            request_id="change",
        ),
        repository.issue_recovery(
            login_name=LoginName.parse("alice.ops"),
            pending=recovery,
            actor=actor,
            issued_at=NOW,
            request_id="issue",
        ),
        repository.recover_password(
            presented=parse_recovery_token(recovery.token),
            replacement=HASH,
            recovered_at=NOW,
            request_id="recover",
        ),
    )
    for operation in operations:
        with pytest.raises(AccountSessionUnavailable):
            await operation
    await database.close()
