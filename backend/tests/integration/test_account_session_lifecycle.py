import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from conftest import AlembicRunner
from sqlalchemy import select, text

from automation_tool.control_plane.application.account_sessions import (
    AccountAuthenticationRejected,
    AccountRecoveryFactory,
    AccountRecoveryRejected,
    AccountSessionFactory,
    AccountSessionRejected,
    AccountSessionService,
)
from automation_tool.control_plane.application.customer_accounts import (
    AccountAuditActor,
    CustomerAccountService,
)
from automation_tool.control_plane.domain import AccountAuditActorKind, AccountStatus, UserId
from automation_tool.control_plane.infrastructure.database import (
    Database,
    account_login_rate_limits,
    account_recovery_tokens,
    account_session_families,
    account_session_tokens,
    user_password_credentials,
    users,
)
from automation_tool.control_plane.infrastructure.database.account_session_repository import (
    SqlAlchemyAccountSessionRepository,
)
from automation_tool.control_plane.infrastructure.database.customer_account_repository import (
    SqlAlchemyCustomerAccountRepository,
)
from automation_tool.control_plane.infrastructure.security.passwords import (
    Argon2idPasswordHasher,
)

HEAD_REVISION = "20260722_0029"
PREVIOUS_REVISION = "20260722_0028"
NOW = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)
PASSWORD = "correct horse battery staple"
NEW_PASSWORD = "replacement horse battery staple"
ACTOR_ID = UUID("123e4567-e89b-42d3-a456-426614174000")


@dataclass
class MutableClock:
    current: datetime = NOW

    def now(self) -> datetime:
        return self.current


def hasher() -> Argon2idPasswordHasher:
    return Argon2idPasswordHasher(pepper=b"p" * 32, pepper_version=1)


def operations_actor() -> AccountAuditActor:
    return AccountAuditActor(
        kind=AccountAuditActorKind.OPERATIONS,
        actor_id=ACTOR_ID,
        source_fingerprint=b"o" * 32,
    )


async def create_account(
    database: Database,
    *,
    login_name: str,
    password: str = PASSWORD,
) -> UserId:
    created = await CustomerAccountService(
        repository=SqlAlchemyCustomerAccountRepository(database),
        password_hasher=hasher(),
        clock=MutableClock(),
    ).create(
        login_name=login_name,
        password=password,
        actor=operations_actor(),
        request_id=f"create-{login_name}",
    )
    return created.user_id


def session_service(database: Database, clock: MutableClock) -> AccountSessionService:
    password_hasher = hasher()
    return AccountSessionService(
        repository=SqlAlchemyAccountSessionRepository(database),
        password_hasher=password_hasher,
        clock=clock,
        session_factory=AccountSessionFactory(),
        recovery_factory=AccountRecoveryFactory(),
        fingerprint_key=b"f" * 32,
        dummy_password_hash=password_hasher.hash("dummy password value for timing"),
    )


@pytest.mark.asyncio
async def test_account_session_migration_is_minimal_constrained_and_reversible(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        expected_columns = {
            "account_session_families": {
                "id",
                "user_id",
                "credential_version",
                "created_at",
                "absolute_expires_at",
                "revoked_at",
                "revocation_reason",
            },
            "account_session_tokens": {
                "id",
                "family_id",
                "user_id",
                "credential_version",
                "kind",
                "secret_digest",
                "created_at",
                "expires_at",
                "consumed_at",
                "revoked_at",
                "replaced_by_id",
            },
            "account_login_rate_limits": {
                "scope_kind",
                "scope_fingerprint",
                "window_started_at",
                "failure_count",
                "blocked_until",
                "updated_at",
            },
            "account_recovery_tokens": {
                "id",
                "user_id",
                "credential_version",
                "secret_digest",
                "issued_by_actor_id",
                "created_at",
                "expires_at",
                "consumed_at",
            },
        }
        async with database.session() as session:
            assert await session.scalar(text("select version_num from alembic_version")) == (
                HEAD_REVISION
            )
            user_columns = set(
                await session.scalars(
                    text(
                        "select column_name from information_schema.columns "
                        "where table_schema='public' and table_name='users'"
                    )
                )
            )
            actual_columns: dict[str, set[str]] = {}
            for table in expected_columns:
                actual_columns[table] = set(
                    await session.scalars(
                        text(
                            "select column_name from information_schema.columns "
                            "where table_schema='public' and table_name=:table"
                        ),
                        {"table": table},
                    )
                )
        assert "lock_expires_at" in user_columns
        assert actual_columns == expected_columns
        assert all(
            forbidden not in columns
            for columns in actual_columns.values()
            for forbidden in (
                "token",
                "password",
                "login_name",
                "raw_ip_address",
                "raw_user_agent",
                "metadata",
            )
        )

        alembic_runner(postgresql_url, "downgrade", PREVIOUS_REVISION)
        async with database.session() as session:
            assert await session.scalar(text("select version_num from alembic_version")) == (
                PREVIOUS_REVISION
            )
            for table in expected_columns:
                assert (
                    await session.scalar(
                        text("select to_regclass(:table)"),
                        {"table": table},
                    )
                    is None
                )
            assert "lock_expires_at" not in set(
                await session.scalars(
                    text(
                        "select column_name from information_schema.columns "
                        "where table_schema='public' and table_name='users'"
                    )
                )
            )
    finally:
        await database.close()
        alembic_runner(postgresql_url, "upgrade", "head")


@pytest.mark.asyncio
async def test_login_is_uniform_digest_only_and_temporarily_locks_known_accounts(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    login_name = f"login.{str(UserId.new()).replace('-', '')}"[:64]
    user_id = await create_account(database, login_name=login_name)
    clock = MutableClock()
    service = session_service(database, clock)
    try:
        with pytest.raises(AccountAuthenticationRejected):
            await service.login(
                login_name="missing.account",
                password="wrong password value",
                source_address="192.0.2.1",
                request_id="missing-login",
            )
        for attempt in range(5):
            with pytest.raises(AccountAuthenticationRejected):
                await service.login(
                    login_name=login_name.upper(),
                    password="wrong password value",
                    source_address="192.0.2.2",
                    request_id=f"wrong-{attempt}",
                )

        with pytest.raises(AccountAuthenticationRejected):
            await service.login(
                login_name=login_name,
                password=PASSWORD,
                source_address="192.0.2.3",
                request_id="locked-login",
            )
        async with database.session() as session:
            user = (
                (await session.execute(select(users).where(users.c.id == user_id.uuid)))
                .mappings()
                .one()
            )
        assert user["status"] == "locked"
        assert user["lock_expires_at"] == NOW + timedelta(minutes=15)

        clock.current += timedelta(minutes=16)
        issued = await service.login(
            login_name=login_name,
            password=PASSWORD,
            source_address="192.0.2.3",
            request_id="unlocked-login",
        )
        assert issued.access_token.startswith("atas1.")
        assert issued.refresh_token.startswith("atrs1.")
        assert issued.account.user_id == user_id
        assert issued.account.status is AccountStatus.ACTIVE
        authenticated = await service.authenticate(access_token=issued.access_token)
        assert authenticated.user_id == user_id
        assert authenticated.credential_version == 1

        access_secret = issued.access_token.rsplit(".", maxsplit=1)[1]
        refresh_secret = issued.refresh_token.rsplit(".", maxsplit=1)[1]
        async with database.session() as session:
            tokens = (
                (
                    await session.execute(
                        select(account_session_tokens).where(
                            account_session_tokens.c.user_id == user_id.uuid
                        )
                    )
                )
                .mappings()
                .all()
            )
        assert {row["kind"] for row in tokens} == {"access", "refresh"}
        assert all(len(row["secret_digest"]) == 32 for row in tokens)
        assert access_secret not in repr(tokens)
        assert refresh_secret not in repr(tokens)
        assert PASSWORD not in repr(tokens)
        unknown_access = AccountSessionFactory().create().access.token
        with pytest.raises(AccountSessionRejected):
            await service.authenticate(access_token=unknown_access)
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_refresh_is_single_use_and_replay_revokes_the_whole_family(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    login_name = f"refresh.{str(UserId.new()).replace('-', '')}"[:64]
    user_id = await create_account(database, login_name=login_name)
    clock = MutableClock()
    service = session_service(database, clock)
    try:
        first = await service.login(
            login_name=login_name,
            password=PASSWORD,
            source_address="192.0.2.10",
            request_id="refresh-login",
        )
        clock.current += timedelta(minutes=1)
        rotated = await service.refresh(
            refresh_token=first.refresh_token,
            source_address="192.0.2.10",
            request_id="refresh-rotate",
        )
        assert rotated.refresh_token != first.refresh_token
        assert rotated.access_token != first.access_token
        await service.authenticate(access_token=rotated.access_token)

        with pytest.raises(AccountSessionRejected):
            await service.logout(
                refresh_token=first.refresh_token,
                request_id="consumed-refresh-logout",
            )

        with pytest.raises(AccountSessionRejected):
            await service.refresh(
                refresh_token=first.refresh_token,
                source_address="192.0.2.10",
                request_id="refresh-replay",
            )
        with pytest.raises(AccountSessionRejected):
            await service.refresh(
                refresh_token=first.refresh_token,
                source_address="192.0.2.10",
                request_id="refresh-replay-after-family-revoked",
            )
        for token, operation in (
            (rotated.access_token, service.authenticate),
            (rotated.refresh_token, service.refresh),
        ):
            with pytest.raises(AccountSessionRejected):
                if operation == service.authenticate:
                    await service.authenticate(access_token=token)
                else:
                    await service.refresh(
                        refresh_token=token,
                        source_address="192.0.2.10",
                        request_id="revoked-family",
                    )

        async with database.session() as session:
            family = (
                (
                    await session.execute(
                        select(account_session_families).where(
                            account_session_families.c.user_id == user_id.uuid
                        )
                    )
                )
                .mappings()
                .one()
            )
        assert family["revoked_at"] == clock.current
        assert family["revocation_reason"] == "refresh_reuse"
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_logout_password_change_and_recovery_revoke_the_required_sessions(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    login_name = f"password.{str(UserId.new()).replace('-', '')}"[:64]
    user_id = await create_account(database, login_name=login_name)
    clock = MutableClock()
    service = session_service(database, clock)
    try:
        first = await service.login(
            login_name=login_name,
            password=PASSWORD,
            source_address="192.0.2.20",
            request_id="password-login-a",
        )
        second = await service.login(
            login_name=login_name,
            password=PASSWORD,
            source_address="192.0.2.21",
            request_id="password-login-b",
        )
        await service.logout(refresh_token=first.refresh_token, request_id="logout-a")
        with pytest.raises(AccountSessionRejected):
            await service.authenticate(access_token=first.access_token)
        await service.authenticate(access_token=second.access_token)

        with pytest.raises(AccountAuthenticationRejected):
            await service.change_password(
                access_token=second.access_token,
                current_password="wrong password value",
                new_password=NEW_PASSWORD,
                request_id="wrong-current",
            )
        await service.change_password(
            access_token=second.access_token,
            current_password=PASSWORD,
            new_password=NEW_PASSWORD,
            request_id="change-password",
        )
        with pytest.raises(AccountSessionRejected):
            await service.authenticate(access_token=second.access_token)
        with pytest.raises(AccountAuthenticationRejected):
            await service.login(
                login_name=login_name,
                password=PASSWORD,
                source_address="192.0.2.22",
                request_id="old-password",
            )
        changed = await service.login(
            login_name=login_name,
            password=NEW_PASSWORD,
            source_address="192.0.2.22",
            request_id="new-password",
        )

        recovery = await service.issue_recovery(
            login_name=login_name,
            actor=operations_actor(),
            request_id="issue-recovery",
        )
        assert recovery.recovery_token.startswith("atrp1.")
        recovery_secret = recovery.recovery_token.rsplit(".", maxsplit=1)[1]
        async with database.session() as session:
            recovery_row = (
                (
                    await session.execute(
                        select(account_recovery_tokens).where(
                            account_recovery_tokens.c.user_id == user_id.uuid
                        )
                    )
                )
                .mappings()
                .one()
            )
        assert len(recovery_row["secret_digest"]) == 32
        assert recovery_secret not in repr(recovery_row)

        await service.recover_password(
            recovery_token=recovery.recovery_token,
            new_password=PASSWORD,
            request_id="consume-recovery",
        )
        with pytest.raises(AccountRecoveryRejected):
            await service.recover_password(
                recovery_token=recovery.recovery_token,
                new_password=PASSWORD,
                request_id="replay-recovery",
            )
        with pytest.raises(AccountSessionRejected):
            await service.authenticate(access_token=changed.access_token)

        restored = await service.login(
            login_name=login_name,
            password=PASSWORD,
            source_address="192.0.2.23",
            request_id="recovered-login",
        )
        assert restored.account.user_id == user_id
        async with database.session() as session:
            user = (
                (await session.execute(select(users).where(users.c.id == user_id.uuid)))
                .mappings()
                .one()
            )
            credential = (
                (
                    await session.execute(
                        select(user_password_credentials).where(
                            user_password_credentials.c.user_id == user_id.uuid
                        )
                    )
                )
                .mappings()
                .one()
            )
        assert user["credential_version"] == 3
        assert credential["version"] == 3
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_source_rate_limit_blocks_cross_identifier_stuffing_without_raw_values(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    login_name = f"source.{str(UserId.new()).replace('-', '')}"[:64]
    await create_account(database, login_name=login_name)
    service = session_service(database, MutableClock())
    source = "198.51.100.44"
    try:
        for attempt in range(20):
            with pytest.raises(AccountAuthenticationRejected):
                await service.login(
                    login_name=f"missing.{attempt}",
                    password="wrong password value",
                    source_address=source,
                    request_id=f"stuffing-{attempt}",
                )
        with pytest.raises(AccountAuthenticationRejected):
            await service.login(
                login_name=login_name,
                password=PASSWORD,
                source_address=source,
                request_id="blocked-source",
            )
        allowed = await service.login(
            login_name=login_name,
            password=PASSWORD,
            source_address="198.51.100.45",
            request_id="different-source",
        )
        assert allowed.account.login_name.value == login_name

        async with database.session() as session:
            rate_rows = (
                (
                    await session.execute(
                        select(account_login_rate_limits).where(
                            account_login_rate_limits.c.scope_kind == "source"
                        )
                    )
                )
                .mappings()
                .all()
            )
        assert any(row["failure_count"] == 20 for row in rate_rows)
        assert source not in repr(rate_rows)
        assert hashlib.sha256(source.encode()).digest() not in {
            row["scope_fingerprint"] for row in rate_rows
        }
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_recovery_rejects_unknown_disabled_and_stale_credentials(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    clock = MutableClock()
    service = session_service(database, clock)
    actor = operations_actor()
    try:
        with pytest.raises(AccountRecoveryRejected):
            await service.issue_recovery(
                login_name="missing.recovery",
                actor=actor,
                request_id="unknown-recovery",
            )

        disabled_login = f"disabled.{str(UserId.new()).replace('-', '')}"[:64]
        disabled_id = await create_account(database, login_name=disabled_login)
        disabled_session = await service.login(
            login_name=disabled_login,
            password=PASSWORD,
            source_address="192.0.2.49",
            request_id="disabled-login",
        )
        await CustomerAccountService(
            repository=SqlAlchemyCustomerAccountRepository(database),
            password_hasher=hasher(),
            clock=clock,
        ).disable(
            user_id=disabled_id,
            expected_revision=1,
            actor=actor,
            request_id="disable-recovery-account",
        )
        with pytest.raises(AccountSessionRejected):
            await service.authenticate(access_token=disabled_session.access_token)
        async with database.session() as session:
            disabled_family = (
                (
                    await session.execute(
                        select(account_session_families).where(
                            account_session_families.c.user_id == disabled_id.uuid
                        )
                    )
                )
                .mappings()
                .one()
            )
        assert disabled_family["revocation_reason"] == "account_disabled"
        with pytest.raises(AccountRecoveryRejected):
            await service.issue_recovery(
                login_name=disabled_login,
                actor=actor,
                request_id="disabled-recovery",
            )

        stale_login = f"stale.{str(UserId.new()).replace('-', '')}"[:64]
        await create_account(database, login_name=stale_login)
        issued = await service.login(
            login_name=stale_login,
            password=PASSWORD,
            source_address="192.0.2.50",
            request_id="stale-login",
        )
        stale_recovery = await service.issue_recovery(
            login_name=stale_login,
            actor=actor,
            request_id="stale-recovery-issue",
        )
        await service.change_password(
            access_token=issued.access_token,
            current_password=PASSWORD,
            new_password=NEW_PASSWORD,
            request_id="stale-password-change",
        )
        with pytest.raises(AccountRecoveryRejected):
            await service.recover_password(
                recovery_token=stale_recovery.recovery_token,
                new_password=PASSWORD,
                request_id="stale-recovery-consume",
            )
    finally:
        await database.close()
