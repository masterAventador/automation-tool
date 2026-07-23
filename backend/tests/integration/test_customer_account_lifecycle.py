import asyncio
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from conftest import AlembicRunner
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from automation_tool.control_plane.application.customer_accounts import (
    AccountAlreadyExists,
    AccountAuditActor,
    AccountNotFound,
    AccountRecord,
    AccountRevisionConflict,
    CustomerAccountService,
)
from automation_tool.control_plane.domain import (
    AccountAuditActorKind,
    AccountStatus,
    LoginName,
    UserId,
)
from automation_tool.control_plane.infrastructure.database import (
    Database,
    account_audit_events,
    user_password_credentials,
    users,
)
from automation_tool.control_plane.infrastructure.database.customer_account_repository import (
    SqlAlchemyCustomerAccountRepository,
)
from automation_tool.control_plane.infrastructure.security.passwords import (
    Argon2idPasswordHasher,
)

HEAD_REVISION = "20260723_0031"
PREVIOUS_REVISION = "20260721_0027"
NOW = datetime(2026, 7, 22, 10, 0, tzinfo=UTC)
PASSWORD = "correct horse battery staple"
ACTOR_ID = UUID("123e4567-e89b-42d3-a456-426614174000")


@dataclass
class FixedClock:
    current: datetime = NOW

    def now(self) -> datetime:
        return self.current


def account_service(database: Database, *, at: datetime = NOW) -> CustomerAccountService:
    return CustomerAccountService(
        repository=SqlAlchemyCustomerAccountRepository(database),
        password_hasher=Argon2idPasswordHasher(pepper=b"p" * 32, pepper_version=1),
        clock=FixedClock(at),
    )


def operations_actor() -> AccountAuditActor:
    return AccountAuditActor(
        kind=AccountAuditActorKind.OPERATIONS,
        actor_id=ACTOR_ID,
        source_fingerprint=b"s" * 32,
    )


@pytest.mark.asyncio
async def test_customer_account_migration_is_minimal_constrained_and_reversible(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        async with database.session() as session:
            revision = await session.scalar(text("select version_num from alembic_version"))
            columns: dict[str, set[str]] = {}
            for table in ("users", "user_password_credentials", "account_audit_events"):
                columns[table] = set(
                    await session.scalars(
                        text(
                            "select column_name from information_schema.columns "
                            "where table_schema = 'public' and table_name = :table"
                        ),
                        {"table": table},
                    )
                )
            constraints = {
                table: {
                    name
                    for name in await session.scalars(
                        text(
                            "select conname from pg_constraint where conrelid = "
                            "cast(('public.' || :table) as regclass)"
                        ),
                        {"table": table},
                    )
                    if name.startswith(("pk_", "fk_", "ck_", "uq_"))
                }
                for table in ("users", "user_password_credentials", "account_audit_events")
            }
            audit_triggers = set(
                await session.scalars(
                    text(
                        "select tgname from pg_trigger where tgrelid = "
                        "'public.account_audit_events'::regclass and not tgisinternal"
                    )
                )
            )

        assert revision == HEAD_REVISION
        assert columns == {
            "users": {
                "id",
                "login_name",
                "status",
                "credential_version",
                "revision",
                "created_at",
                "updated_at",
                "locked_at",
                "lock_expires_at",
                "disabled_at",
            },
            "user_password_credentials": {
                "user_id",
                "version",
                "password_hash",
                "pepper_version",
                "created_at",
                "updated_at",
            },
            "account_audit_events": {
                "event_id",
                "event_type",
                "occurred_at",
                "actor_kind",
                "actor_id",
                "subject_user_id",
                "outcome",
                "reason_code",
                "request_id",
                "source_fingerprint",
            },
        }
        assert all(
            forbidden not in columns[table]
            for table in columns
            for forbidden in (
                "email",
                "phone",
                "role",
                "organization_id",
                "tenant_id",
                "raw_ip_address",
                "raw_user_agent",
                "metadata",
            )
        )
        assert constraints["users"] == {
            "pk_users",
            "uq_users_login_name",
            "uq_users_id_credential_version",
            "ck_users_id_uuid_v4",
            "ck_users_login_name",
            "ck_users_status",
            "ck_users_versions_positive",
            "ck_users_lifecycle_state",
            "ck_users_timestamp_order",
        }
        assert constraints["user_password_credentials"] == {
            "pk_user_password_credentials",
            "fk_user_password_credentials_user",
            "ck_user_password_credentials_version_positive",
            "ck_user_password_credentials_hash",
            "ck_user_password_credentials_pepper_version_positive",
            "ck_user_password_credentials_timestamp_order",
        }
        assert constraints["account_audit_events"] == {
            "pk_account_audit_events",
            "fk_account_audit_events_subject_user",
            "ck_account_audit_events_id_uuid_v4",
            "ck_account_audit_events_actor_id_uuid_v4",
            "ck_account_audit_events_type",
            "ck_account_audit_events_actor_kind",
            "ck_account_audit_events_outcome",
            "ck_account_audit_events_reason_code",
            "ck_account_audit_events_request_id",
            "ck_account_audit_events_source_fingerprint",
        }
        assert audit_triggers == {
            "trg_account_audit_events_no_update_delete",
            "trg_account_audit_events_no_truncate",
        }

        alembic_runner(postgresql_url, "downgrade", PREVIOUS_REVISION)
        async with database.session() as session:
            assert await session.scalar(text("select version_num from alembic_version")) == (
                PREVIOUS_REVISION
            )
            for table in ("users", "user_password_credentials", "account_audit_events"):
                assert (
                    await session.scalar(text("select to_regclass(:table)"), {"table": table})
                    is None
                )
            assert (
                await session.scalar(
                    text("select to_regprocedure('reject_account_audit_mutation()')")
                )
                is None
            )
    finally:
        await database.close()
        alembic_runner(postgresql_url, "upgrade", "head")


@pytest.mark.asyncio
async def test_account_creation_persists_only_canonical_identity_hash_and_minimal_audit(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        created = await account_service(database).create(
            login_name="Alice.OPS",
            password=PASSWORD,
            actor=operations_actor(),
            request_id="u9-02-create",
        )

        assert created.login_name == LoginName.parse("alice.ops")
        assert created.status is AccountStatus.ACTIVE
        assert created.credential_version == 1
        assert created.revision == 1
        assert PASSWORD not in repr(created)

        repository = SqlAlchemyCustomerAccountRepository(database)
        authentication = await repository.find_for_authentication(LoginName.parse("ALICE.OPS"))
        assert authentication is not None
        assert authentication.account == created
        assert Argon2idPasswordHasher(pepper=b"p" * 32, pepper_version=1).verify(
            PASSWORD, authentication.password_hash
        )
        assert await repository.find_for_authentication(LoginName.parse("missing.account")) is None

        async with database.session() as session:
            password_row = (
                (
                    await session.execute(
                        select(user_password_credentials).where(
                            user_password_credentials.c.user_id == created.user_id.uuid
                        )
                    )
                )
                .mappings()
                .one()
            )
            audit_row = (
                (
                    await session.execute(
                        select(account_audit_events).where(
                            account_audit_events.c.subject_user_id == created.user_id.uuid
                        )
                    )
                )
                .mappings()
                .one()
            )
        assert password_row["password_hash"].startswith("$argon2id$")
        assert password_row["password_hash"] != PASSWORD
        assert password_row["pepper_version"] == 1
        assert audit_row["event_type"] == "account.created"
        assert audit_row["actor_kind"] == "operations"
        assert audit_row["actor_id"] == ACTOR_ID
        assert audit_row["outcome"] == "succeeded"
        assert audit_row["reason_code"] == "operations_provisioned"
        assert audit_row["request_id"] == "u9-02-create"
        assert audit_row["source_fingerprint"] == b"s" * 32
        assert "Alice.OPS" not in repr(audit_row)
        assert PASSWORD not in repr(audit_row)
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_case_insensitive_concurrent_creation_has_one_winner_and_one_audit(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    login_name = f"Concurrent.{UserId.new()}".replace("-", "")[:64]
    try:
        results = await asyncio.gather(
            account_service(database).create(
                login_name=login_name.upper(),
                password=PASSWORD,
                actor=operations_actor(),
                request_id="u9-02-concurrent-a",
            ),
            account_service(database).create(
                login_name=login_name.lower(),
                password=PASSWORD,
                actor=operations_actor(),
                request_id="u9-02-concurrent-b",
            ),
            return_exceptions=True,
        )

        assert Counter(type(result) for result in results) == Counter(
            {AccountRecord: 1, AccountAlreadyExists: 1}
        )
        winner = next(result for result in results if isinstance(result, AccountRecord))
        async with database.session() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(users)
                    .where(users.c.login_name == login_name.lower())
                )
                == 1
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(account_audit_events)
                    .where(account_audit_events.c.subject_user_id == winner.user_id.uuid)
                )
                == 1
            )
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_database_rejects_noncanonical_identity_weak_hash_and_incoherent_state(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    created = await account_service(database).create(
        login_name=f"constraints.{str(UserId.new()).replace('-', '')}"[:64],
        password=PASSWORD,
        actor=operations_actor(),
        request_id="u9-02-constraints-create",
    )
    try:
        invalid_updates = (
            update(users)
            .where(users.c.id == created.user_id.uuid)
            .values(login_name="Uppercase.Is.Rejected"),
            update(user_password_credentials)
            .where(user_password_credentials.c.user_id == created.user_id.uuid)
            .values(password_hash="$argon2id$v=19$m=1,t=1,p=1$c2FsdA$aGFzaA"),
            update(users)
            .where(users.c.id == created.user_id.uuid)
            .values(status="disabled", disabled_at=None),
        )
        for statement in invalid_updates:
            with pytest.raises(IntegrityError):
                async with database.session() as session:
                    await session.execute(statement)

        authentication = await SqlAlchemyCustomerAccountRepository(
            database
        ).find_for_authentication(created.login_name)
        assert authentication is not None
        assert authentication.account.status is AccountStatus.ACTIVE
        assert Argon2idPasswordHasher(pepper=b"p" * 32, pepper_version=1).verify(
            PASSWORD,
            authentication.password_hash,
        )
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_disable_is_single_winner_restore_is_explicit_and_audit_is_append_only(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    created = await account_service(database).create(
        login_name=f"state.{str(UserId.new()).replace('-', '')}"[:64],
        password=PASSWORD,
        actor=operations_actor(),
        request_id="u9-02-state-create",
    )
    try:
        disable_service = account_service(database, at=NOW + timedelta(seconds=1))
        disabled_results = await asyncio.gather(
            disable_service.disable(
                user_id=created.user_id,
                expected_revision=1,
                actor=operations_actor(),
                request_id="u9-02-disable-a",
            ),
            disable_service.disable(
                user_id=created.user_id,
                expected_revision=1,
                actor=operations_actor(),
                request_id="u9-02-disable-b",
            ),
            return_exceptions=True,
        )
        assert Counter(type(result) for result in disabled_results) == Counter(
            {AccountRecord: 1, AccountRevisionConflict: 1}
        )
        disabled = next(result for result in disabled_results if isinstance(result, AccountRecord))
        assert disabled.status is AccountStatus.DISABLED
        assert disabled.revision == 2
        assert disabled.credential_version == 2
        assert disabled.disabled_at == NOW + timedelta(seconds=1)

        restored = await account_service(database, at=NOW + timedelta(minutes=1)).restore(
            user_id=created.user_id,
            expected_revision=2,
            actor=operations_actor(),
            request_id="u9-02-restore",
        )
        assert restored.status is AccountStatus.ACTIVE
        assert restored.revision == 3
        assert restored.credential_version == 2
        assert restored.disabled_at is None
        assert restored.updated_at == NOW + timedelta(minutes=1)

        with pytest.raises(AccountNotFound):
            await account_service(database).disable(
                user_id=UserId.new(),
                expected_revision=1,
                actor=operations_actor(),
                request_id="u9-02-missing",
            )

        async with database.session() as session:
            events = list(
                await session.scalars(
                    select(account_audit_events.c.event_type)
                    .where(account_audit_events.c.subject_user_id == created.user_id.uuid)
                    .order_by(account_audit_events.c.occurred_at, account_audit_events.c.event_id)
                )
            )
        assert Counter(events) == Counter(
            {
                "account.created": 1,
                "account.disabled": 1,
                "session.all_revoked": 1,
                "account.enabled": 1,
            }
        )

        with pytest.raises(DBAPIError):
            async with database.session() as session:
                await session.execute(
                    update(account_audit_events)
                    .where(account_audit_events.c.subject_user_id == created.user_id.uuid)
                    .values(reason_code="mutated")
                )
        with pytest.raises(DBAPIError):
            async with database.session() as session:
                await session.execute(text("truncate table account_audit_events"))

        async with database.session() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(account_audit_events)
                    .where(account_audit_events.c.subject_user_id == created.user_id.uuid)
                )
                == 4
            )
    finally:
        await database.close()
