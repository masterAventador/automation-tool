from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from conftest import AlembicRunner
from sqlalchemy import delete, insert, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from automation_tool.control_plane.application.platform_session_health import (
    PendingPlatformSessionHealth,
    PlatformSessionHealthRejected,
    PlatformSessionHealthUnavailable,
)
from automation_tool.control_plane.domain import InstallationId
from automation_tool.control_plane.infrastructure.database import (
    Database,
    SqlAlchemyPlatformSessionHealthRepository,
    installations,
    platform_session_gates,
    platform_session_health,
)
from automation_tool.control_plane.infrastructure.database import (
    platform_session_health_repository as health_repository_module,
)
from automation_tool.protocol import PlatformSessionState

PREVIOUS_REVISION = "20260718_0013"
HEAD_REVISION = "20260721_0023"
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
        await session.execute(delete(platform_session_gates))
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
        async with database.session() as session:
            gate_columns = set(
                await session.scalars(
                    text(
                        "select column_name from information_schema.columns "
                        "where table_schema = 'public' "
                        "and table_name = 'platform_session_gates'"
                    )
                )
            )
        assert gate_columns == {
            "installation_id",
            "platform",
            "state",
            "session_revision",
            "updated_at",
        }

        alembic_runner(postgresql_url, "downgrade", PREVIOUS_REVISION)
        async with database.session() as session:
            assert (
                await session.scalar(text("select to_regclass('public.platform_session_health')"))
                is None
            )
            assert (
                await session.scalar(text("select to_regclass('public.platform_session_gates')"))
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
async def test_logout_gate_is_idempotent_and_only_a_later_healthy_epoch_reopens_work(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        installation_id = await seed_installation(database)
        repository = SqlAlchemyPlatformSessionHealthRepository(database)
        await repository.converge(
            pending(
                installation_id,
                state=PlatformSessionState.HEALTHY,
                revision=1,
                observed_at=NOW,
                received_at=NOW + timedelta(seconds=1),
            )
        )

        first = await repository.begin_logout(
            installation_id,
            "douyin",
            NOW + timedelta(seconds=2),
        )
        duplicate = await repository.begin_logout(
            installation_id,
            "douyin",
            NOW + timedelta(seconds=3),
        )
        await repository.converge(
            pending(
                installation_id,
                state=PlatformSessionState.MISSING,
                revision=2,
                observed_at=NOW + timedelta(seconds=4),
                received_at=NOW + timedelta(seconds=5),
            )
        )
        async with database.session() as session:
            assert (
                await session.scalar(
                    select(platform_session_gates.c.session_revision).where(
                        platform_session_gates.c.installation_id == installation_id.uuid
                    )
                )
                == 2
            )
        await repository.converge(
            pending(
                installation_id,
                state=PlatformSessionState.HEALTHY,
                revision=3,
                observed_at=NOW + timedelta(seconds=6),
                received_at=NOW + timedelta(seconds=7),
            )
        )
        async with database.session() as session:
            assert (
                await session.scalar(
                    select(platform_session_gates.c.session_revision).where(
                        platform_session_gates.c.installation_id == installation_id.uuid
                    )
                )
                is None
            )

        assert first.session_revision == 2
        assert duplicate == first
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


@pytest.mark.asyncio
async def test_repository_queries_and_rejects_every_stale_or_invalid_transition(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        installation_id = await seed_installation(database)
        repository = SqlAlchemyPlatformSessionHealthRepository(database)

        assert await repository.get(installation_id, "douyin") is None
        first = pending(
            installation_id,
            state=PlatformSessionState.HEALTHY,
            revision=1,
            observed_at=NOW,
            received_at=NOW + timedelta(seconds=10),
        )
        await repository.converge(first)
        assert (
            await repository.get(installation_id, "douyin")
            == (await repository.converge(first)).projection
        )

        invalid_transitions = (
            pending(
                installation_id,
                state=PlatformSessionState.RISK,
                revision=1,
                observed_at=NOW,
                received_at=NOW + timedelta(seconds=11),
            ),
            pending(
                installation_id,
                state=PlatformSessionState.RISK,
                revision=2,
                observed_at=NOW,
                received_at=NOW + timedelta(seconds=12),
            ),
            pending(
                installation_id,
                state=PlatformSessionState.RISK,
                revision=1,
                observed_at=NOW + timedelta(seconds=1),
                received_at=NOW + timedelta(seconds=9),
            ),
        )
        for candidate in invalid_transitions:
            with pytest.raises(PlatformSessionHealthRejected):
                await repository.converge(candidate)

        unknown = pending(
            InstallationId.new(),
            state=PlatformSessionState.HEALTHY,
            revision=1,
            observed_at=NOW,
            received_at=NOW + timedelta(seconds=1),
        )
        with pytest.raises(PlatformSessionHealthRejected):
            await repository.converge(unknown)
        with pytest.raises(PlatformSessionHealthRejected):
            await repository.begin_logout(unknown.installation_id, "douyin", NOW)

        with pytest.raises(PlatformSessionHealthRejected):
            await repository.get(object(), "douyin")
        with pytest.raises(PlatformSessionHealthRejected):
            await repository.get(installation_id, "private")
        with pytest.raises(PlatformSessionHealthRejected):
            await repository.converge(object())  # type: ignore[arg-type]
        with pytest.raises(PlatformSessionHealthRejected):
            await repository.begin_logout(installation_id, "private", NOW)
        with pytest.raises(PlatformSessionHealthRejected):
            await repository.begin_logout(
                installation_id,
                "douyin",
                datetime(2026, 7, 19, 11, 30),
            )
        with pytest.raises(PlatformSessionHealthRejected):
            SqlAlchemyPlatformSessionHealthRepository(object())  # type: ignore[arg-type]

        def reject_corrupt_row(row: object) -> object:
            raise PlatformSessionHealthRejected

        monkeypatch.setattr(health_repository_module, "_projection", reject_corrupt_row)
        with pytest.raises(PlatformSessionHealthRejected):
            await repository.get(installation_id, "douyin")
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_repository_maps_database_failures_to_closed_domain_errors(
    postgresql_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyPlatformSessionHealthRepository(database)
    installation_id = InstallationId.new()
    candidate = pending(
        installation_id,
        state=PlatformSessionState.HEALTHY,
        revision=1,
        observed_at=NOW,
        received_at=NOW,
    )

    async def assert_failures(error: Exception) -> None:
        @asynccontextmanager
        async def failing_session() -> AsyncIterator[AsyncSession]:
            raise error
            yield  # pragma: no cover - required to declare an async context manager

        monkeypatch.setattr(database, "session", failing_session)
        expected = (
            PlatformSessionHealthRejected
            if isinstance(error, IntegrityError)
            else PlatformSessionHealthUnavailable
        )
        with pytest.raises(expected):
            await repository.converge(candidate)
        with pytest.raises(expected):
            await repository.begin_logout(installation_id, "douyin", NOW)
        with pytest.raises(PlatformSessionHealthUnavailable):
            await repository.get(installation_id, "douyin")

    try:
        await assert_failures(
            IntegrityError("private statement", {}, RuntimeError("private database"))
        )
        await assert_failures(SQLAlchemyError("private database"))
        await assert_failures(RuntimeError("private database"))
    finally:
        await database.close()
