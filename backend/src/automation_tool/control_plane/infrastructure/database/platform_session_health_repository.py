"""Atomic PostgreSQL convergence for platform Session health."""

from __future__ import annotations

from datetime import datetime
from typing import cast

from sqlalchemy import insert, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from automation_tool.control_plane.application.platform_session_health import (
    PendingPlatformSessionHealth,
    PlatformSessionHealthConvergenceResult,
    PlatformSessionHealthProjection,
    PlatformSessionHealthRejected,
    PlatformSessionHealthUnavailable,
)
from automation_tool.control_plane.domain import InstallationStatus
from automation_tool.control_plane.infrastructure.database.schema import (
    installations,
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

    async def converge(
        self,
        pending: PendingPlatformSessionHealth,
    ) -> PlatformSessionHealthConvergenceResult:
        if not isinstance(pending, PendingPlatformSessionHealth):
            raise PlatformSessionHealthRejected
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
                    raise PlatformSessionHealthRejected
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
                    raise PlatformSessionHealthRejected
                if pending.session_revision == projection.session_revision:
                    if pending.observed_at < projection.observed_at:
                        raise PlatformSessionHealthRejected
                    if pending.observed_at == projection.observed_at:
                        if pending.state is not projection.state:
                            raise PlatformSessionHealthRejected
                        return PlatformSessionHealthConvergenceResult(
                            projection=projection,
                            duplicate=True,
                        )
                    if projection.circuit_open and not pending.circuit_open:
                        raise PlatformSessionHealthRejected
                elif pending.observed_at <= projection.observed_at:
                    raise PlatformSessionHealthRejected
                if pending.received_at < projection.updated_at:
                    raise PlatformSessionHealthRejected

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
        except SQLAlchemyError:
            raise PlatformSessionHealthUnavailable from None
        except Exception:
            raise PlatformSessionHealthUnavailable from None


__all__ = ["SqlAlchemyPlatformSessionHealthRepository"]
