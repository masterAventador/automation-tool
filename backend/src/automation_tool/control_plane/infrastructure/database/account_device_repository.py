"""Atomic PostgreSQL adapter for current-account Installation management."""

from datetime import datetime
from typing import cast
from uuid import uuid4

from sqlalchemy import insert, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import SQLAlchemyError

from automation_tool.control_plane.application.account_devices import (
    AccountDeviceRecord,
    AccountDeviceRevocationRejected,
    AccountDevicesUnavailable,
)
from automation_tool.control_plane.domain import (
    AccountAuditActorKind,
    AccountAuditEventType,
    InstallationId,
    InstallationStatus,
    UserId,
)
from automation_tool.control_plane.infrastructure.database.schema import (
    account_audit_events,
    device_credentials,
    device_sessions,
    installations,
)
from automation_tool.control_plane.infrastructure.database.session import Database


def _record(row: RowMapping) -> AccountDeviceRecord:
    try:
        return AccountDeviceRecord(
            installation_id=InstallationId.parse(row["id"]),
            status=InstallationStatus(cast(str, row["status"])),
            revision=cast(int, row["revision"]),
            created_at=cast(datetime, row["created_at"]),
            updated_at=cast(datetime, row["updated_at"]),
        )
    except (KeyError, TypeError, ValueError):
        raise AccountDevicesUnavailable from None


class SqlAlchemyAccountDeviceRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def list_owned(self, *, user_id: UserId) -> tuple[AccountDeviceRecord, ...]:
        try:
            async with self._database.session() as session:
                rows = (
                    (
                        await session.execute(
                            select(installations)
                            .where(installations.c.owner_user_id == user_id.uuid)
                            .order_by(installations.c.created_at, installations.c.id)
                        )
                    )
                    .mappings()
                    .all()
                )
        except (OSError, SQLAlchemyError):
            raise AccountDevicesUnavailable from None
        return tuple(_record(row) for row in rows)

    async def revoke_owned(
        self,
        *,
        user_id: UserId,
        installation_id: InstallationId,
        expected_revision: int,
        revoked_at: datetime,
        request_id: str,
    ) -> AccountDeviceRecord:
        try:
            async with self._database.session() as session:
                row = (
                    (
                        await session.execute(
                            select(installations)
                            .where(
                                installations.c.id == installation_id.uuid,
                                installations.c.owner_user_id == user_id.uuid,
                            )
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if (
                    row is None
                    or row["status"] != InstallationStatus.ACTIVE.value
                    or row["revision"] != expected_revision
                ):
                    raise AccountDeviceRevocationRejected
                updated = (
                    (
                        await session.execute(
                            update(installations)
                            .where(
                                installations.c.id == installation_id.uuid,
                                installations.c.owner_user_id == user_id.uuid,
                                installations.c.status == InstallationStatus.ACTIVE.value,
                                installations.c.revision == expected_revision,
                            )
                            .values(
                                status=InstallationStatus.REVOKED.value,
                                revision=expected_revision + 1,
                                updated_at=revoked_at,
                                revoked_at=revoked_at,
                            )
                            .returning(*installations.c)
                        )
                    )
                    .mappings()
                    .one()
                )
                await session.execute(
                    update(device_credentials)
                    .where(
                        device_credentials.c.installation_id == installation_id.uuid,
                        device_credentials.c.status == "active",
                    )
                    .values(status="revoked", updated_at=revoked_at, revoked_at=revoked_at)
                )
                await session.execute(
                    update(device_sessions)
                    .where(
                        device_sessions.c.installation_id == installation_id.uuid,
                        device_sessions.c.revoked_at.is_(None),
                    )
                    .values(revoked_at=revoked_at)
                )
                await session.execute(
                    insert(account_audit_events).values(
                        event_id=uuid4(),
                        event_type=AccountAuditEventType.DEVICE_REVOKED.value,
                        occurred_at=revoked_at,
                        actor_kind=AccountAuditActorKind.USER.value,
                        actor_id=user_id.uuid,
                        subject_user_id=user_id.uuid,
                        outcome="succeeded",
                        reason_code="user_device_revoked",
                        request_id=request_id,
                    )
                )
                return _record(updated)
        except (OSError, SQLAlchemyError):
            raise AccountDevicesUnavailable from None


__all__ = ["SqlAlchemyAccountDeviceRepository"]
