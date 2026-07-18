"""PostgreSQL transaction for revoking one Installation and all active access."""

from datetime import datetime

from sqlalchemy import select, update

from automation_tool.control_plane.application.installation_revocations import (
    InstallationRevocationRejected,
    RevokedInstallation,
)
from automation_tool.control_plane.domain import InstallationId, InstallationStatus
from automation_tool.control_plane.infrastructure.database.schema import (
    device_credentials,
    device_sessions,
    installations,
)
from automation_tool.control_plane.infrastructure.database.session import Database


class SqlAlchemyInstallationRevocationRepository:
    """Serialize revocation and invalidate credentials and sessions atomically."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def revoke(
        self,
        *,
        installation_id: InstallationId,
        expected_revision: int,
        revoked_at: datetime,
    ) -> RevokedInstallation:
        async with self._database.session() as session:
            installation = (
                (
                    await session.execute(
                        select(installations)
                        .where(installations.c.id == installation_id.uuid)
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if (
                installation is None
                or installation["status"] != InstallationStatus.ACTIVE.value
                or installation["revision"] != expected_revision
            ):
                raise InstallationRevocationRejected
            next_revision = expected_revision + 1
            await session.execute(
                update(installations)
                .where(
                    installations.c.id == installation_id.uuid,
                    installations.c.revision == expected_revision,
                    installations.c.status == InstallationStatus.ACTIVE.value,
                )
                .values(
                    status=InstallationStatus.REVOKED.value,
                    revision=next_revision,
                    updated_at=revoked_at,
                    revoked_at=revoked_at,
                )
            )
            await session.execute(
                update(device_credentials)
                .where(
                    device_credentials.c.installation_id == installation_id.uuid,
                    device_credentials.c.status == "active",
                )
                .values(
                    status="revoked",
                    updated_at=revoked_at,
                    revoked_at=revoked_at,
                )
            )
            await session.execute(
                update(device_sessions)
                .where(
                    device_sessions.c.installation_id == installation_id.uuid,
                    device_sessions.c.revoked_at.is_(None),
                )
                .values(revoked_at=revoked_at)
            )
            return RevokedInstallation(
                installation_id=installation_id,
                revision=next_revision,
                revoked_at=revoked_at,
            )


__all__ = ["SqlAlchemyInstallationRevocationRepository"]
