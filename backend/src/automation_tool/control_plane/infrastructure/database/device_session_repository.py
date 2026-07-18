"""PostgreSQL adapter for short-lived, capability-bound device sessions."""

import secrets
from datetime import datetime

from sqlalchemy import insert, select

from automation_tool.control_plane.application.device_credentials import (
    DEVICE_CREDENTIAL_SCOPE,
    ParsedDeviceCredential,
)
from automation_tool.control_plane.application.device_sessions import (
    AuthenticatedDeviceSession,
    DeviceSessionCapability,
    DeviceSessionRejected,
    IssuedDeviceSession,
    ParsedDeviceSession,
    PendingDeviceSession,
)
from automation_tool.control_plane.domain import InstallationStatus
from automation_tool.control_plane.infrastructure.database.device_credential_repository import (
    lock_authenticated_device_credential,
)
from automation_tool.control_plane.infrastructure.database.schema import (
    device_credentials,
    device_sessions,
    installations,
)
from automation_tool.control_plane.infrastructure.database.session import Database


class SqlAlchemyDeviceSessionRepository:
    """Issue and authenticate digest-only sessions under their live parent credential."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def issue(
        self,
        *,
        presented_credential: ParsedDeviceCredential,
        pending_session: PendingDeviceSession,
        capability: DeviceSessionCapability,
        issued_at: datetime,
        not_before: datetime,
        expires_at: datetime,
    ) -> IssuedDeviceSession:
        async with self._database.session() as session:
            credential = await lock_authenticated_device_credential(
                session,
                presented_credential,
            )
            await session.execute(
                insert(device_sessions).values(
                    id=pending_session.session_id,
                    installation_id=credential["installation_id"],
                    device_credential_id=credential["id"],
                    credential_version=credential["version"],
                    capability=capability.value,
                    secret_digest=pending_session.secret_digest,
                    created_at=issued_at,
                    not_before=not_before,
                    expires_at=expires_at,
                )
            )
            return IssuedDeviceSession(
                session_id=pending_session.session_id,
                installation_id=credential["installation_id"],
                credential_id=credential["id"],
                credential_version=credential["version"],
                session_token=pending_session.session_token,
                capability=capability,
                issued_at=issued_at,
                not_before=not_before,
                expires_at=expires_at,
            )

    async def authenticate(
        self,
        *,
        presented_session: ParsedDeviceSession,
        required_capability: DeviceSessionCapability,
        authenticated_at: datetime,
    ) -> AuthenticatedDeviceSession:
        async with self._database.session() as session:
            binding = (
                (
                    await session.execute(
                        select(
                            device_sessions.c.installation_id,
                            device_sessions.c.device_credential_id,
                        ).where(device_sessions.c.id == presented_session.session_id)
                    )
                )
                .mappings()
                .one_or_none()
            )
            if binding is None:
                raise DeviceSessionRejected

            installation_status = await session.scalar(
                select(installations.c.status)
                .where(installations.c.id == binding["installation_id"])
                .with_for_update()
            )
            if installation_status != InstallationStatus.ACTIVE.value:
                raise DeviceSessionRejected

            credential = (
                (
                    await session.execute(
                        select(device_credentials)
                        .where(device_credentials.c.id == binding["device_credential_id"])
                        .with_for_update()
                    )
                )
                .mappings()
                .one()
            )

            stored = (
                (
                    await session.execute(
                        select(device_sessions)
                        .where(device_sessions.c.id == presented_session.session_id)
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if stored is None:
                raise DeviceSessionRejected

            if (
                credential["installation_id"] != stored["installation_id"]
                or credential["id"] != stored["device_credential_id"]
                or credential["version"] != stored["credential_version"]
                or credential["status"] != "active"
                or credential["scope"] != DEVICE_CREDENTIAL_SCOPE
                or stored["revoked_at"] is not None
                or stored["capability"] != required_capability.value
                or authenticated_at < stored["not_before"]
                or authenticated_at >= stored["expires_at"]
                or not secrets.compare_digest(
                    stored["secret_digest"],
                    presented_session.secret_digest,
                )
            ):
                raise DeviceSessionRejected

            return AuthenticatedDeviceSession(
                session_id=stored["id"],
                installation_id=stored["installation_id"],
                credential_id=stored["device_credential_id"],
                credential_version=stored["credential_version"],
                capability=required_capability,
                expires_at=stored["expires_at"],
            )


__all__ = ["SqlAlchemyDeviceSessionRepository"]
