"""Atomic PostgreSQL convergence for platform Session health."""

from __future__ import annotations

from datetime import datetime
from typing import cast

from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from automation_tool.control_plane.application.platform_session_health import (
    PendingPlatformSessionHealth,
    PlatformSessionHealthConvergenceResult,
    PlatformSessionHealthProjection,
    PlatformSessionHealthRejected,
    PlatformSessionHealthUnavailable,
    PlatformSessionLogoutGate,
)
from automation_tool.control_plane.domain import InstallationStatus
from automation_tool.control_plane.infrastructure.database.schema import (
    installations,
    platform_session_gates,
    platform_session_health,
)
from automation_tool.control_plane.infrastructure.database.session import Database
from automation_tool.protocol import PlatformSessionState


def _projection(row: RowMapping) -> PlatformSessionHealthProjection:
    from automation_tool.control_plane.domain import InstallationId

    return PlatformSessionHealthProjection(
        installation_id=InstallationId.parse(row["installation_id"]),
        platform=cast(str, row["platform"]),
        state=PlatformSessionState(cast(str, row["state"])),
        session_revision=cast(int, row["session_revision"]),
        observed_at=cast(datetime, row["observed_at"]),
        updated_at=cast(datetime, row["updated_at"]),
    )


class SqlAlchemyPlatformSessionHealthRepository:
    """Lock an Installation and reject stale or implicit-recovery reports."""

    def __init__(self, database: Database) -> None:
        if not isinstance(database, Database):
            raise PlatformSessionHealthRejected
        self._database = database

    async def get(
        self,
        installation_id: object,
        platform: str,
    ) -> PlatformSessionHealthProjection | None:
        from automation_tool.control_plane.domain import InstallationId

        if not isinstance(installation_id, InstallationId) or platform != "douyin":
            raise PlatformSessionHealthRejected
        try:
            async with self._database.session() as session:
                row = (
                    (
                        await session.execute(
                            select(platform_session_health).where(
                                platform_session_health.c.installation_id == installation_id.uuid,
                                platform_session_health.c.platform == platform,
                            )
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                return None if row is None else _projection(row)
        except (OSError, SQLAlchemyError):
            raise PlatformSessionHealthUnavailable from None
        except PlatformSessionHealthRejected:
            raise
        except Exception:
            raise PlatformSessionHealthUnavailable from None

    async def converge(
        self,
        pending: PendingPlatformSessionHealth,
    ) -> PlatformSessionHealthConvergenceResult:
        if not isinstance(pending, PendingPlatformSessionHealth):
            raise PlatformSessionHealthRejected("not_a_pending_health_record")
        try:
            async with self._database.session() as session:
                installation = (
                    (
                        await session.execute(
                            select(installations)
                            .where(installations.c.id == pending.installation_id.uuid)
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if (
                    installation is None
                    or installation["status"] != InstallationStatus.ACTIVE.value
                ):
                    raise PlatformSessionHealthRejected("installation_is_not_active")
                current = (
                    (
                        await session.execute(
                            select(platform_session_health)
                            .where(
                                platform_session_health.c.installation_id
                                == pending.installation_id.uuid,
                                platform_session_health.c.platform == pending.platform,
                            )
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if current is None:
                    await session.execute(
                        insert(platform_session_health).values(
                            installation_id=pending.installation_id.uuid,
                            platform=pending.platform,
                            state=pending.state.value,
                            session_revision=pending.session_revision,
                            observed_at=pending.observed_at,
                            updated_at=pending.received_at,
                        )
                    )
                    projection = PlatformSessionHealthProjection(
                        installation_id=pending.installation_id,
                        platform=pending.platform,
                        state=pending.state,
                        session_revision=pending.session_revision,
                        observed_at=pending.observed_at,
                        updated_at=pending.received_at,
                    )
                    return PlatformSessionHealthConvergenceResult(
                        projection=projection,
                        duplicate=False,
                    )

                projection = _projection(current)
                if pending.session_revision < projection.session_revision:
                    raise PlatformSessionHealthRejected("revision_went_backwards")
                if pending.session_revision == projection.session_revision:
                    if pending.observed_at < projection.observed_at:
                        raise PlatformSessionHealthRejected("same_revision_observed_earlier")
                    if pending.observed_at == projection.observed_at:
                        if pending.state is not projection.state:
                            raise PlatformSessionHealthRejected("same_observation_different_state")
                        return PlatformSessionHealthConvergenceResult(
                            projection=projection,
                            duplicate=True,
                        )
                    if projection.circuit_open and not pending.circuit_open:
                        raise PlatformSessionHealthRejected("circuit_reopened_without_cause")
                elif pending.observed_at <= projection.observed_at:
                    raise PlatformSessionHealthRejected("newer_revision_observed_no_later")
                if pending.received_at < projection.updated_at:
                    raise PlatformSessionHealthRejected("received_before_last_update")

                gate_revision = await session.scalar(
                    select(platform_session_gates.c.session_revision)
                    .where(
                        platform_session_gates.c.installation_id == pending.installation_id.uuid,
                        platform_session_gates.c.platform == pending.platform,
                    )
                    .with_for_update()
                )

                await session.execute(
                    update(platform_session_health)
                    .where(
                        platform_session_health.c.installation_id == pending.installation_id.uuid,
                        platform_session_health.c.platform == pending.platform,
                        platform_session_health.c.session_revision == projection.session_revision,
                    )
                    .values(
                        state=pending.state.value,
                        session_revision=pending.session_revision,
                        observed_at=pending.observed_at,
                        updated_at=pending.received_at,
                    )
                )
                if (
                    pending.state is PlatformSessionState.HEALTHY
                    and isinstance(gate_revision, int)
                    and pending.session_revision > gate_revision
                ):
                    await session.execute(
                        delete(platform_session_gates).where(
                            platform_session_gates.c.installation_id
                            == pending.installation_id.uuid,
                            platform_session_gates.c.platform == pending.platform,
                            platform_session_gates.c.session_revision == gate_revision,
                        )
                    )
                return PlatformSessionHealthConvergenceResult(
                    projection=PlatformSessionHealthProjection(
                        installation_id=pending.installation_id,
                        platform=pending.platform,
                        state=pending.state,
                        session_revision=pending.session_revision,
                        observed_at=pending.observed_at,
                        updated_at=pending.received_at,
                    ),
                    duplicate=False,
                )
        except PlatformSessionHealthRejected:
            raise
        except IntegrityError:
            raise PlatformSessionHealthRejected from None
        except (OSError, SQLAlchemyError):
            raise PlatformSessionHealthUnavailable from None
        except Exception:
            raise PlatformSessionHealthUnavailable from None

    async def begin_logout(
        self,
        installation_id: object,
        platform: str,
        blocked_at: datetime,
    ) -> PlatformSessionLogoutGate:
        from automation_tool.control_plane.domain import InstallationId

        if (
            not isinstance(installation_id, InstallationId)
            or platform != "douyin"
            or not isinstance(blocked_at, datetime)
            or blocked_at.utcoffset() is None
        ):
            raise PlatformSessionHealthRejected
        try:
            async with self._database.session() as session:
                installation_status = await session.scalar(
                    select(installations.c.status)
                    .where(installations.c.id == installation_id.uuid)
                    .with_for_update()
                )
                if installation_status != InstallationStatus.ACTIVE.value:
                    raise PlatformSessionHealthRejected
                existing = (
                    (
                        await session.execute(
                            select(platform_session_gates)
                            .where(
                                platform_session_gates.c.installation_id == installation_id.uuid,
                                platform_session_gates.c.platform == platform,
                            )
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if existing is not None:
                    return PlatformSessionLogoutGate(
                        installation_id=installation_id,
                        platform=platform,
                        state="blocked",
                        session_revision=cast(int, existing["session_revision"]),
                        updated_at=cast(datetime, existing["updated_at"]),
                    )
                current_revision = await session.scalar(
                    select(platform_session_health.c.session_revision).where(
                        platform_session_health.c.installation_id == installation_id.uuid,
                        platform_session_health.c.platform == platform,
                    )
                )
                revision = (current_revision if isinstance(current_revision, int) else 0) + 1
                await session.execute(
                    insert(platform_session_gates).values(
                        installation_id=installation_id.uuid,
                        platform=platform,
                        state="blocked",
                        session_revision=revision,
                        updated_at=blocked_at,
                    )
                )
                return PlatformSessionLogoutGate(
                    installation_id=installation_id,
                    platform=platform,
                    state="blocked",
                    session_revision=revision,
                    updated_at=blocked_at,
                )
        except PlatformSessionHealthRejected:
            raise
        except IntegrityError:
            raise PlatformSessionHealthRejected from None
        except (OSError, SQLAlchemyError):
            raise PlatformSessionHealthUnavailable from None
        except Exception:
            raise PlatformSessionHealthUnavailable from None


__all__ = ["SqlAlchemyPlatformSessionHealthRepository"]
