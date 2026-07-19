import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from conftest import AlembicRunner
from sqlalchemy import delete, insert, text
from sqlalchemy.exc import IntegrityError

from automation_tool.control_plane.infrastructure.database import (
    Database,
    installation_registration_challenges,
)

PREVIOUS_REVISION = "20260718_0002"
HEAD_REVISION = "20260718_0016"
EXPECTED_CONSTRAINTS = {
    "pk_registration_challenges",
    "fk_registration_challenges_installation_id",
    "ck_registration_challenges_bootstrap_fingerprint_length",
    "ck_registration_challenges_device_public_key_length",
    "ck_registration_challenges_environment_id",
    "ck_registration_challenges_id_uuid_v4",
    "ck_registration_challenges_proof_hash_length",
    "ck_registration_challenges_expiry",
    "ck_registration_challenges_consumption_state",
}


@pytest.mark.asyncio
async def test_registration_challenge_migration_upgrades_and_rolls_back_cleanly(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        async with database.session() as session:
            revision = await session.scalar(text("select version_num from alembic_version"))
            table_name = await session.scalar(
                text("select to_regclass('public.installation_registration_challenges')")
            )
            constraints = set(
                await session.scalars(
                    text(
                        "select conname from pg_constraint where conrelid = "
                        "'public.installation_registration_challenges'::regclass"
                    )
                )
            )
        assert revision == HEAD_REVISION
        assert table_name == "installation_registration_challenges"
        assert constraints >= EXPECTED_CONSTRAINTS

        alembic_runner(postgresql_url, "downgrade", PREVIOUS_REVISION)
        async with database.session() as session:
            downgraded_revision = await session.scalar(
                text("select version_num from alembic_version")
            )
            removed = await session.scalar(
                text("select to_regclass('public.installation_registration_challenges')")
            )
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
async def test_registration_challenge_constraints_reject_unsafe_state(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    created_at = datetime(2026, 7, 18, tzinfo=UTC)
    invalid_cases: tuple[dict[str, object], ...] = (
        {"id": UUID("123e4567-e89b-12d3-a456-426614174000")},
        {"environment_id": "DEMO"},
        {"bootstrap_fingerprint": secrets.token_bytes(31)},
        {"device_public_key": secrets.token_bytes(33)},
        {"proof_hash": secrets.token_bytes(31)},
        {"expires_at": created_at},
        {"consumed_at": created_at + timedelta(seconds=1)},
    )
    try:
        async with database.session() as session:
            await session.execute(delete(installation_registration_challenges))
        for overrides in invalid_cases:
            values: dict[str, object] = {
                "id": uuid4(),
                "environment_id": "demo-cn-1",
                "bootstrap_fingerprint": secrets.token_bytes(32),
                "device_public_key": secrets.token_bytes(32),
                "proof_hash": secrets.token_bytes(32),
                "created_at": created_at,
                "expires_at": created_at + timedelta(minutes=5),
                "consumed_at": None,
                "installation_id": None,
            }
            values.update(overrides)
            with pytest.raises(IntegrityError):
                async with database.session() as session:
                    await session.execute(
                        insert(installation_registration_challenges).values(values)
                    )
    finally:
        await database.close()
