import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from conftest import AlembicRunner
from sqlalchemy import delete, insert, select, text, update
from sqlalchemy.exc import IntegrityError

from automation_tool.control_plane.domain import InstallationId, InstallationStatus
from automation_tool.control_plane.infrastructure.database import Database, installations

BASELINE_REVISION = "20260718_0001"
HEAD_REVISION = "20260718_0013"
EXPECTED_CONSTRAINTS = {
    "pk_installations",
    "uq_installations_device_public_key",
    "ck_installations_device_public_key_length",
    "ck_installations_id_uuid_v4",
    "ck_installations_revision_positive",
    "ck_installations_revocation_state",
    "ck_installations_status",
    "ck_installations_timestamp_order",
}


async def reset_installations(database: Database) -> None:
    async with database.session() as session:
        await session.execute(delete(installations))


@pytest.mark.asyncio
async def test_installation_migration_upgrades_and_downgrades_cleanly(
    postgresql_url: str, alembic_runner: AlembicRunner
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    alembic_runner(postgresql_url, "check")
    database = Database.from_url(postgresql_url)
    try:
        async with database.session() as session:
            revision = await session.scalar(text("select version_num from alembic_version"))
            table_name = await session.scalar(text("select to_regclass('public.installations')"))
            constraints = set(
                await session.scalars(
                    text(
                        "select conname from pg_constraint "
                        "where conrelid = 'public.installations'::regclass"
                    )
                )
            )
        assert revision == HEAD_REVISION
        assert table_name == "installations"
        assert constraints >= EXPECTED_CONSTRAINTS

        alembic_runner(postgresql_url, "downgrade", BASELINE_REVISION)
        async with database.session() as session:
            downgraded_revision = await session.scalar(
                text("select version_num from alembic_version")
            )
            removed_table = await session.scalar(text("select to_regclass('public.installations')"))
        assert downgraded_revision == BASELINE_REVISION
        assert removed_table is None
    finally:
        await database.close()
        alembic_runner(postgresql_url, "upgrade", "head")


@pytest.mark.asyncio
async def test_installation_defaults_and_revision_guard_survive_real_transactions(
    postgresql_url: str, alembic_runner: AlembicRunner
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    installation_id = InstallationId.new()
    public_key = secrets.token_bytes(32)
    try:
        await reset_installations(database)
        async with database.session() as session:
            created = (
                (
                    await session.execute(
                        insert(installations)
                        .values(id=installation_id.uuid, device_public_key=public_key)
                        .returning(*installations.c)
                    )
                )
                .mappings()
                .one()
            )

        assert created["id"] == installation_id.uuid
        assert created["device_public_key"] == public_key
        assert created["status"] == InstallationStatus.ACTIVE.value
        assert created["revision"] == 1
        assert created["created_at"].tzinfo is not None
        assert created["updated_at"] == created["created_at"]
        assert created["revoked_at"] is None

        revoked_at = created["created_at"] + timedelta(seconds=1)
        async with database.session() as session:
            new_revision = await session.scalar(
                update(installations)
                .where(
                    installations.c.id == installation_id.uuid,
                    installations.c.revision == 1,
                )
                .values(
                    status=InstallationStatus.REVOKED.value,
                    revision=installations.c.revision + 1,
                    updated_at=revoked_at,
                    revoked_at=revoked_at,
                )
                .returning(installations.c.revision)
            )
        assert new_revision == 2

        async with database.session() as session:
            stale_revision = await session.scalar(
                update(installations)
                .where(
                    installations.c.id == installation_id.uuid,
                    installations.c.revision == 1,
                )
                .values(revision=2)
                .returning(installations.c.revision)
            )
            persisted = (
                (
                    await session.execute(
                        select(installations).where(installations.c.id == installation_id.uuid)
                    )
                )
                .mappings()
                .one()
            )
        assert stale_revision is None
        assert persisted["status"] == InstallationStatus.REVOKED.value
        assert persisted["revision"] == 2
        assert persisted["revoked_at"] == revoked_at
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_installation_constraints_reject_invalid_security_state(
    postgresql_url: str, alembic_runner: AlembicRunner
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    created_at = datetime(2026, 1, 1, tzinfo=UTC)
    invalid_cases: tuple[dict[str, object], ...] = (
        {"id": UUID("123e4567-e89b-12d3-a456-426614174000")},
        {"device_public_key": secrets.token_bytes(31)},
        {"status": "suspended"},
        {"revision": 0},
        {"status": InstallationStatus.ACTIVE.value, "revoked_at": created_at},
        {"status": InstallationStatus.REVOKED.value, "revoked_at": None},
        {"updated_at": created_at - timedelta(seconds=1)},
        {
            "status": InstallationStatus.REVOKED.value,
            "revoked_at": created_at - timedelta(seconds=1),
        },
    )
    try:
        await reset_installations(database)
        for overrides in invalid_cases:
            values: dict[str, object] = {
                "id": InstallationId.new().uuid,
                "device_public_key": secrets.token_bytes(32),
                "status": InstallationStatus.ACTIVE.value,
                "revision": 1,
                "created_at": created_at,
                "updated_at": created_at,
                "revoked_at": None,
            }
            values.update(overrides)
            with pytest.raises(IntegrityError):
                async with database.session() as session:
                    await session.execute(insert(installations).values(values))
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_installation_id_and_public_key_are_unique(
    postgresql_url: str, alembic_runner: AlembicRunner
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    installation_id = InstallationId.new()
    public_key = secrets.token_bytes(32)
    try:
        await reset_installations(database)
        async with database.session() as session:
            await session.execute(
                insert(installations).values(
                    id=installation_id.uuid,
                    device_public_key=public_key,
                )
            )

        duplicate_cases = (
            {
                "id": installation_id.uuid,
                "device_public_key": secrets.token_bytes(32),
            },
            {
                "id": InstallationId.new().uuid,
                "device_public_key": public_key,
            },
        )
        for values in duplicate_cases:
            with pytest.raises(IntegrityError):
                async with database.session() as session:
                    await session.execute(insert(installations).values(values))
    finally:
        await database.close()
