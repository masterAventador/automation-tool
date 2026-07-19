from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

import pytest
from conftest import AlembicRunner
from sqlalchemy import delete, insert, select, text
from sqlalchemy.exc import IntegrityError

from automation_tool.control_plane.application.platform_session_health import (
    PendingPlatformSessionHealth,
    PlatformSessionHealthRejected,
)
from automation_tool.control_plane.domain import InstallationId
from automation_tool.control_plane.infrastructure.database import (
    Database,
    SqlAlchemyPlatformSessionHealthRepository,
    installations,
    platform_session_health,
)
from automation_tool.protocol import PlatformSessionState

PREVIOUS_REVISION = "20260718_0013"
HEAD_REVISION = "20260718_0014"
NOW = datetime(2026, 7, 19, 11, 30, tzinfo=UTC)
EXPECTED_COLUMNS = {
    "installation_id",
    "platform",
    "state",
    "session_revision",
    "observed_at",
    "updated_at",
}
EXPECTED_CONSTRAINTS = {
    "ck_platform_session_health_platform",
    "ck_platform_session_health_revision_positive",
    "ck_platform_session_health_state",
    "ck_platform_session_health_time_order",
    "fk_platform_session_health_installation_id",
    "pk_platform_session_health",
}


async def reset_data(database: Database) -> None:
    async with database.session() as session:
        await session.execute(delete(platform_session_health))
        await session.execute(delete(installations))


async def seed_installation(database: Database) -> InstallationId:
    installation_id = InstallationId.new()
    async with database.session() as session:
        await session.execute(
            insert(installations).values(
                id=installation_id.uuid,
                device_public_key=secrets.token_bytes(32),
                created_at=NOW - timedelta(minutes=1),
                updated_at=NOW - timedelta(minutes=1),
            )
        )
    return installation_id


def pending(
    installation_id: InstallationId,
    *,
    state: PlatformSessionState,
    revision: int,
    observed_at: datetime,
    received_at: datetime,
) -> PendingPlatformSessionHealth:
    return PendingPlatformSessionHealth(
        installation_id=installation_id,
        platform="douyin",
        state=state,
        session_revision=revision,
        observed_at=observed_at,
        received_at=received_at,
    )


@pytest.mark.asyncio
async def test_platform_health_migration_is_closed_drift_free_and_reversible(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    alembic_runner(postgresql_url, "check")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        async with database.session() as session:
            revision = await session.scalar(text("select version_num from alembic_version"))
            columns = set(
                await session.scalars(
                    text(
                        "select column_name from information_schema.columns "
                        "where table_schema = 'public' "
                        "and table_name = 'platform_session_health'"
                    )
                )
            )
            constraints = set(
                await session.scalars(
                    text(
                        "select conname from pg_constraint where conrelid = "
                        "'public.platform_session_health'::regclass"
                    )
                )
            )
        assert revision == HEAD_REVISION
        assert columns == EXPECTED_COLUMNS
        assert constraints >= EXPECTED_CONSTRAINTS
        assert (
            not {
                "cookie",
                "profile_id",
                "profile_path",
                "qr_code",
                "captcha",
                "page_text",
                "executor_id",
                "message_id",
            }
            & columns
        )

        alembic_runner(postgresql_url, "downgrade", PREVIOUS_REVISION)
        async with database.session() as session:
            assert (
                await session.scalar(text("select to_regclass('public.platform_session_health')"))
                is None
            )
            assert await session.scalar(text("select to_regclass('public.installations')")) == (
                "installations"
            )
    finally:
        alembic_runner(postgresql_url, "upgrade", "head")
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_repository_converges_monotonically_and_never_reopens_same_epoch(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        installation_id = await seed_installation(database)
        repository = SqlAlchemyPlatformSessionHealthRepository(database)
        first = pending(
            installation_id,
            state=PlatformSessionState.HEALTHY,
            revision=1,
            observed_at=NOW,
            received_at=NOW + timedelta(seconds=1),
        )

        inserted = await repository.converge(first)
        duplicate = await repository.converge(first)
        opened = await repository.converge(
            pending(
                installation_id,
                state=PlatformSessionState.RISK,
                revision=1,
                observed_at=NOW + timedelta(seconds=2),
                received_at=NOW + timedelta(seconds=3),
            )
        )
        with pytest.raises(PlatformSessionHealthRejected):
            await repository.converge(
                pending(
                    installation_id,
                    state=PlatformSessionState.HEALTHY,
                    revision=1,
                    observed_at=NOW + timedelta(seconds=4),
                    received_at=NOW + timedelta(seconds=5),
                )
            )
        recovered = await repository.converge(
            pending(
                installation_id,
                state=PlatformSessionState.HEALTHY,
                revision=2,
                observed_at=NOW + timedelta(seconds=6),
                received_at=NOW + timedelta(seconds=7),
            )
        )

        assert inserted.duplicate is False
        assert duplicate.duplicate is True
        assert opened.projection.circuit_open is True
        assert recovered.projection.session_revision == 2
        assert recovered.projection.circuit_open is False
        for stale_or_unsafe in (
            pending(
                installation_id,
                state=PlatformSessionState.HEALTHY,
                revision=1,
                observed_at=NOW + timedelta(seconds=8),
                received_at=NOW + timedelta(seconds=9),
            ),
            pending(
                installation_id,
                state=PlatformSessionState.RISK,
                revision=2,
                observed_at=NOW + timedelta(seconds=3),
                received_at=NOW + timedelta(seconds=10),
            ),
        ):
            with pytest.raises(PlatformSessionHealthRejected):
                await repository.converge(stale_or_unsafe)

        async with database.session() as session:
            row = (
                (
                    await session.execute(
                        select(platform_session_health).where(
                            platform_session_health.c.installation_id == installation_id.uuid
                        )
                    )
                )
                .mappings()
                .one()
            )
        assert set(row) == EXPECTED_COLUMNS
        assert row["state"] == "healthy"
        assert row["session_revision"] == 2
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_database_rejects_invalid_platform_state_revision_time_and_parent(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        installation_id = await seed_installation(database)
        baseline = {
            "installation_id": installation_id.uuid,
            "platform": "douyin",
            "state": "healthy",
            "session_revision": 1,
            "observed_at": NOW,
            "updated_at": NOW + timedelta(seconds=1),
        }
        invalid = (
            {"platform": "xiaohongshu"},
            {"state": "authenticated"},
            {"session_revision": 0},
            {"updated_at": NOW - timedelta(microseconds=1)},
            {"installation_id": InstallationId.new().uuid},
        )
        for overrides in invalid:
            values = baseline | overrides
            async with database.session() as session:
                with pytest.raises(IntegrityError):
                    await session.execute(insert(platform_session_health).values(values))
    finally:
        await reset_data(database)
        await database.close()
