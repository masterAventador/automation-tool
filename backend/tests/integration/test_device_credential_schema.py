import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from conftest import AlembicRunner
from sqlalchemy import delete, insert, text
from sqlalchemy.exc import IntegrityError

from automation_tool.control_plane.application.device_credentials import DEVICE_CREDENTIAL_SCOPE
from automation_tool.control_plane.domain import InstallationId
from automation_tool.control_plane.infrastructure.database import (
    Database,
    device_credentials,
    device_sessions,
    installation_registration_challenges,
    installations,
)

PREVIOUS_REVISION = "20260718_0003"
HEAD_REVISION = "20260722_0028"
EXPECTED_CONSTRAINTS = {
    "pk_device_credentials",
    "fk_device_credentials_installation_id",
    "fk_device_credentials_replaced_by_id",
    "uq_device_credentials_installation_version",
    "uq_device_credentials_secret_digest",
    "ck_device_credentials_id_uuid_v4",
    "ck_device_credentials_version_positive",
    "ck_device_credentials_scope",
    "ck_device_credentials_secret_digest_length",
    "ck_device_credentials_status",
    "ck_device_credentials_lifecycle_state",
    "ck_device_credentials_timestamp_order",
}


async def reset_data(database: Database) -> None:
    async with database.session() as session:
        await session.execute(delete(installation_registration_challenges))
        await session.execute(delete(device_sessions))
        await session.execute(delete(device_credentials))
        await session.execute(delete(installations))


async def create_installation(database: Database) -> UUID:
    installation_id = InstallationId.new().uuid
    async with database.session() as session:
        await session.execute(
            insert(installations).values(
                id=installation_id,
                device_public_key=secrets.token_bytes(32),
            )
        )
    return installation_id


def credential_values(
    installation_id: UUID,
    *,
    version: int = 1,
    status: str = "active",
) -> dict[str, object]:
    created_at = datetime(2026, 7, 18, 9, 0, tzinfo=UTC)
    return {
        "id": uuid4(),
        "installation_id": installation_id,
        "version": version,
        "scope": DEVICE_CREDENTIAL_SCOPE,
        "secret_digest": secrets.token_bytes(32),
        "status": status,
        "created_at": created_at,
        "updated_at": created_at,
        "revoked_at": None,
        "replaced_by_id": None,
    }


@pytest.mark.asyncio
async def test_device_credential_migration_upgrades_and_rolls_back_cleanly(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    alembic_runner(postgresql_url, "check")
    database = Database.from_url(postgresql_url)
    try:
        async with database.session() as session:
            revision = await session.scalar(text("select version_num from alembic_version"))
            table_name = await session.scalar(
                text("select to_regclass('public.device_credentials')")
            )
            constraints = set(
                await session.scalars(
                    text(
                        "select conname from pg_constraint where conrelid = "
                        "'public.device_credentials'::regclass"
                    )
                )
            )
            active_index = await session.scalar(
                text(
                    "select indexdef from pg_indexes where schemaname = 'public' "
                    "and tablename = 'device_credentials' "
                    "and indexname = 'uq_device_credentials_active_installation'"
                )
            )
        assert revision == HEAD_REVISION
        assert table_name == "device_credentials"
        assert constraints >= EXPECTED_CONSTRAINTS
        assert active_index is not None
        assert "UNIQUE INDEX" in active_index
        assert "WHERE ((status)::text = 'active'::text)" in active_index

        alembic_runner(postgresql_url, "downgrade", PREVIOUS_REVISION)
        async with database.session() as session:
            downgraded_revision = await session.scalar(
                text("select version_num from alembic_version")
            )
            removed = await session.scalar(text("select to_regclass('public.device_credentials')"))
            installations_remain = await session.scalar(
                text("select to_regclass('public.installations')")
            )
        assert downgraded_revision == PREVIOUS_REVISION
        assert removed is None
        assert installations_remain == "installations"
    finally:
        await database.close()
        alembic_runner(postgresql_url, "upgrade", "head")


@pytest.mark.asyncio
async def test_device_credential_constraints_reject_unsafe_state(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        installation_id = await create_installation(database)
        created_at = datetime(2026, 7, 18, 9, 0, tzinfo=UTC)
        invalid_cases: tuple[dict[str, object], ...] = (
            {"id": UUID("123e4567-e89b-12d3-a456-426614174000")},
            {"version": 0},
            {"scope": "device.session.exchange device.task.write"},
            {"secret_digest": secrets.token_bytes(31)},
            {"status": "expired"},
            {"status": "active", "revoked_at": created_at},
            {"status": "revoked", "revoked_at": None},
            {"status": "rotated", "revoked_at": created_at, "replaced_by_id": None},
            {"updated_at": created_at - timedelta(seconds=1)},
            {"revoked_at": created_at - timedelta(seconds=1), "status": "revoked"},
        )
        for overrides in invalid_cases:
            values = credential_values(installation_id)
            values.update(overrides)
            with pytest.raises(IntegrityError):
                async with database.session() as session:
                    await session.execute(insert(device_credentials).values(values))
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_only_one_active_version_and_all_versions_and_digests_are_unique(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        installation_id = await create_installation(database)
        second_installation_id = await create_installation(database)
        original = credential_values(installation_id)
        async with database.session() as session:
            await session.execute(insert(device_credentials).values(original))

        duplicate_cases = (
            credential_values(installation_id, version=2),
            {
                **credential_values(installation_id),
                "status": "revoked",
                "revoked_at": original["created_at"],
            },
            {
                **credential_values(second_installation_id),
                "secret_digest": original["secret_digest"],
            },
        )
        for values in duplicate_cases:
            with pytest.raises(IntegrityError):
                async with database.session() as session:
                    await session.execute(insert(device_credentials).values(values))
    finally:
        await database.close()
