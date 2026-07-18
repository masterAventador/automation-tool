"""PostgreSQL adapter for digest-authenticated device credential lifecycle."""

import secrets
from datetime import datetime

from sqlalchemy import insert, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from automation_tool.control_plane.application.device_credentials import (
    DEVICE_CREDENTIAL_SCOPE,
    DeviceCredentialRejected,
    IssuedDeviceCredential,
    ParsedDeviceCredential,
    PendingDeviceCredential,
    RevokedDeviceCredential,
)
from automation_tool.control_plane.domain import InstallationStatus
from automation_tool.control_plane.infrastructure.database.schema import (
    device_credentials,
    installations,
)
from automation_tool.control_plane.infrastructure.database.session import Database


async def _lock_authenticated_credential(
    session: AsyncSession,
    presented: ParsedDeviceCredential,
) -> RowMapping:
    installation_id = await session.scalar(
        select(device_credentials.c.installation_id).where(
            device_credentials.c.id == presented.credential_id
        )
    )
    if installation_id is None:
        raise DeviceCredentialRejected
    installation_status = await session.scalar(
        select(installations.c.status)
        .where(installations.c.id == installation_id)
        .with_for_update()
    )
    if installation_status != InstallationStatus.ACTIVE.value:
        raise DeviceCredentialRejected
    credential = (
        (
            await session.execute(
                select(device_credentials)
                .where(device_credentials.c.id == presented.credential_id)
                .with_for_update()
            )
        )
        .mappings()
        .one()
    )
    if (
        credential["status"] != "active"
        or credential["scope"] != DEVICE_CREDENTIAL_SCOPE
        or not secrets.compare_digest(
            credential["secret_digest"],
            presented.secret_digest,
        )
    ):
        raise DeviceCredentialRejected
    return credential


class SqlAlchemyDeviceCredentialRepository:
    """Serialize lifecycle changes per installation and retain credential history."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def rotate(
        self,
        *,
        presented: ParsedDeviceCredential,
        replacement: PendingDeviceCredential,
        rotated_at: datetime,
    ) -> IssuedDeviceCredential:
        async with self._database.session() as session:
            current = await _lock_authenticated_credential(session, presented)
            next_version = current["version"] + 1
            await session.execute(
                update(device_credentials)
                .where(device_credentials.c.id == current["id"])
                .values(
                    status="rotated",
                    updated_at=rotated_at,
                    revoked_at=rotated_at,
                    replaced_by_id=replacement.credential_id,
                )
            )
            await session.execute(
                insert(device_credentials).values(
                    id=replacement.credential_id,
                    installation_id=current["installation_id"],
                    version=next_version,
                    scope=DEVICE_CREDENTIAL_SCOPE,
                    secret_digest=replacement.secret_digest,
                    status="active",
                    created_at=rotated_at,
                    updated_at=rotated_at,
                )
            )
            return IssuedDeviceCredential(
                credential_id=replacement.credential_id,
                installation_id=current["installation_id"],
                credential=replacement.credential,
                version=next_version,
                scope=DEVICE_CREDENTIAL_SCOPE,
            )

    async def revoke(
        self,
        *,
        presented: ParsedDeviceCredential,
        revoked_at: datetime,
    ) -> RevokedDeviceCredential:
        async with self._database.session() as session:
            current = await _lock_authenticated_credential(session, presented)
            await session.execute(
                update(device_credentials)
                .where(device_credentials.c.id == current["id"])
                .values(
                    status="revoked",
                    updated_at=revoked_at,
                    revoked_at=revoked_at,
                )
            )
            return RevokedDeviceCredential(
                credential_id=current["id"],
                installation_id=current["installation_id"],
                version=current["version"],
                status="revoked",
            )


__all__ = ["SqlAlchemyDeviceCredentialRepository"]
