import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from conftest import AlembicRunner
from sqlalchemy import delete, insert, text
from sqlalchemy.exc import IntegrityError

from automation_tool.control_plane.application.device_credentials import DEVICE_CREDENTIAL_SCOPE
from automation_tool.control_plane.application.device_sessions import (
    DEVICE_SESSION_CLOCK_SKEW,
    DEVICE_SESSION_LIFETIME,
    DeviceSessionCapability,
)
from automation_tool.control_plane.domain import InstallationId
from automation_tool.control_plane.infrastructure.database import (
    Database,
    device_credentials,
    device_sessions,
    installation_registration_challenges,
    installations,
)

PREVIOUS_REVISION = "20260718_0004"
HEAD_REVISION = "20260718_0013"
EXPECTED_CONSTRAINTS = {
    "pk_device_sessions",
    "fk_device_sessions_credential_binding",
    "uq_device_sessions_secret_digest",
    "ck_device_sessions_id_uuid_v4",
    "ck_device_sessions_credential_version_positive",
    "ck_device_sessions_capability",
    "ck_device_sessions_secret_digest_length",
    "ck_device_sessions_time_window",
    "ck_device_sessions_revocation_time",
}

NOW = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)


async def reset_data(database: Database) -> None:
    async with database.session() as session:
        await session.execute(delete(installation_registration_challenges))
        await session.execute(delete(device_sessions))
        await session.execute(delete(device_credentials))
        await session.execute(delete(installations))


async def seed_credential(database: Database) -> tuple[UUID, UUID]:
    installation_id = InstallationId.new().uuid
    credential_id = uuid4()
    async with database.session() as session:
        await session.execute(
            insert(installations).values(
                id=installation_id,
                device_public_key=secrets.token_bytes(32),
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.execute(
            insert(device_credentials).values(
                id=credential_id,
                installation_id=installation_id,
                version=1,
                scope=DEVICE_CREDENTIAL_SCOPE,
                secret_digest=secrets.token_bytes(32),
                status="active",
                created_at=NOW,
                updated_at=NOW,
            )
        )
    return installation_id, credential_id


def session_values(
    installation_id: UUID,
    credential_id: UUID,
) -> dict[str, object]:
    return {
        "id": uuid4(),
        "installation_id": installation_id,
        "device_credential_id": credential_id,
        "credential_version": 1,
        "capability": DeviceSessionCapability.APP_CONTROL_PLANE.value,
        "secret_digest": secrets.token_bytes(32),
        "created_at": NOW,
        "not_before": NOW - DEVICE_SESSION_CLOCK_SKEW,
        "expires_at": NOW + DEVICE_SESSION_LIFETIME,
        "revoked_at": None,
    }


@pytest.mark.asyncio
async def test_device_session_migration_upgrades_and_rolls_back_cleanly(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    alembic_runner(postgresql_url, "check")
    database = Database.from_url(postgresql_url)
    try:
        async with database.session() as session:
            revision = await session.scalar(text("select version_num from alembic_version"))
            table_name = await session.scalar(text("select to_regclass('public.device_sessions')"))
            constraints = set(
                await session.scalars(
                    text(
                        "select conname from pg_constraint where conrelid = "
                        "'public.device_sessions'::regclass"
                    )
                )
            )
            columns = set(
                await session.scalars(
                    text(
                        "select column_name from information_schema.columns "
                        "where table_schema = 'public' and table_name = 'device_sessions'"
                    )
                )
            )
        assert revision == HEAD_REVISION
        assert table_name == "device_sessions"
        assert constraints >= EXPECTED_CONSTRAINTS
        assert "session_token" not in columns
        assert "token" not in columns
        assert "secret_digest" in columns

        alembic_runner(postgresql_url, "downgrade", PREVIOUS_REVISION)
        async with database.session() as session:
            downgraded_revision = await session.scalar(
                text("select version_num from alembic_version")
            )
            removed = await session.scalar(text("select to_regclass('public.device_sessions')"))
            credentials_remain = await session.scalar(
                text("select to_regclass('public.device_credentials')")
            )
        assert downgraded_revision == PREVIOUS_REVISION
        assert removed is None
        assert credentials_remain == "device_credentials"
    finally:
        await database.close()
        alembic_runner(postgresql_url, "upgrade", "head")


@pytest.mark.asyncio
async def test_device_session_constraints_reject_unsafe_binding_and_time_windows(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        installation_id, credential_id = await seed_credential(database)
        invalid_cases: tuple[dict[str, object], ...] = (
            {"id": UUID("123e4567-e89b-12d3-a456-426614174000")},
            {"installation_id": uuid4()},
            {"device_credential_id": uuid4()},
            {"credential_version": 0},
            {"credential_version": 2},
            {"capability": "*"},
            {"capability": "app.control-plane executor"},
            {"secret_digest": secrets.token_bytes(31)},
            {"not_before": NOW + timedelta(microseconds=1)},
            {"not_before": NOW - DEVICE_SESSION_CLOCK_SKEW - timedelta(microseconds=1)},
            {"expires_at": NOW},
            {"expires_at": NOW + DEVICE_SESSION_LIFETIME + timedelta(microseconds=1)},
            {"revoked_at": NOW - timedelta(microseconds=1)},
        )
        for overrides in invalid_cases:
            values = session_values(installation_id, credential_id)
            values.update(overrides)
            with pytest.raises(IntegrityError):
                async with database.session() as session:
                    await session.execute(insert(device_sessions).values(values))
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_device_session_digest_is_globally_unique(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        installation_id, credential_id = await seed_credential(database)
        original = session_values(installation_id, credential_id)
        async with database.session() as session:
            await session.execute(insert(device_sessions).values(original))
        duplicate = session_values(installation_id, credential_id)
        duplicate["secret_digest"] = original["secret_digest"]
        with pytest.raises(IntegrityError):
            async with database.session() as session:
                await session.execute(insert(device_sessions).values(duplicate))
    finally:
        await database.close()
