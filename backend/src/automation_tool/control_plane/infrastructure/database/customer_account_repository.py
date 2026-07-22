"""Atomic PostgreSQL persistence for operations-managed customer accounts."""

from datetime import datetime, timedelta
from typing import cast
from uuid import uuid4

from sqlalchemy import insert, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from automation_tool.control_plane.application.customer_accounts import (
    AccountAlreadyExists,
    AccountAuditContext,
    AccountAuthenticationRecord,
    AccountDataRejected,
    AccountNotFound,
    AccountPersistenceUnavailable,
    AccountRecord,
    AccountRevisionConflict,
    AccountTransitionRejected,
    EmergencyRevocationRecord,
)
from automation_tool.control_plane.domain import (
    AccountAuditEventType,
    AccountStatus,
    LoginName,
    PasswordHash,
    UserId,
)
from automation_tool.control_plane.infrastructure.database.schema import (
    account_audit_events,
    account_session_families,
    account_session_tokens,
    device_credentials,
    device_sessions,
    installations,
    user_password_credentials,
    users,
)
from automation_tool.control_plane.infrastructure.database.session import Database


def _account_record(row: RowMapping) -> AccountRecord:
    try:
        return AccountRecord(
            user_id=UserId.parse(row["id"]),
            login_name=LoginName.parse(row["login_name"]),
            status=AccountStatus(cast(str, row["status"])),
            credential_version=cast(int, row["credential_version"]),
            revision=cast(int, row["revision"]),
            created_at=cast(datetime, row["created_at"]),
            updated_at=cast(datetime, row["updated_at"]),
            locked_at=cast(datetime | None, row["locked_at"]),
            lock_expires_at=cast(datetime | None, row["lock_expires_at"]),
            disabled_at=cast(datetime | None, row["disabled_at"]),
        )
    except (KeyError, TypeError, ValueError):
        raise AccountDataRejected from None


def _audit_values(
    *,
    event_type: AccountAuditEventType,
    user_id: UserId,
    reason_code: str,
    audit: AccountAuditContext,
) -> dict[str, object]:
    return {
        "event_id": uuid4(),
        "event_type": event_type.value,
        "occurred_at": audit.occurred_at,
        "actor_kind": audit.actor.kind.value,
        "actor_id": audit.actor.actor_id,
        "subject_user_id": user_id.uuid,
        "outcome": "succeeded",
        "reason_code": reason_code,
        "request_id": audit.request_id,
        "source_fingerprint": audit.actor.source_fingerprint,
    }


def _password_hash(row: RowMapping) -> PasswordHash:
    try:
        return PasswordHash(
            encoded=cast(str, row["password_hash"]),
            pepper_version=cast(int, row["pepper_version"]),
            version=cast(int, row["password_version"]),
        )
    except (KeyError, TypeError, ValueError):
        raise AccountDataRejected from None


def _transition_event(
    current: AccountStatus,
    target: AccountStatus,
) -> tuple[AccountAuditEventType, str]:
    if target is AccountStatus.LOCKED and current is AccountStatus.ACTIVE:
        return AccountAuditEventType.ACCOUNT_LOCKED, "system_locked"
    if target is AccountStatus.DISABLED and current in {
        AccountStatus.ACTIVE,
        AccountStatus.LOCKED,
    }:
        return AccountAuditEventType.ACCOUNT_DISABLED, "operations_disabled"
    if target is AccountStatus.ACTIVE and current is AccountStatus.LOCKED:
        return AccountAuditEventType.ACCOUNT_UNLOCKED, "operations_unlocked"
    if target is AccountStatus.ACTIVE and current is AccountStatus.DISABLED:
        return AccountAuditEventType.ACCOUNT_ENABLED, "operations_restored"
    raise AccountTransitionRejected


class SqlAlchemyCustomerAccountRepository:
    """Persist one canonical User, current password hash and immutable audit facts."""

    def __init__(self, database: Database) -> None:
        if not isinstance(database, Database):
            raise AccountPersistenceUnavailable
        self._database = database

    async def create(
        self,
        *,
        user_id: UserId,
        login_name: LoginName,
        password_hash: PasswordHash,
        audit: AccountAuditContext,
    ) -> AccountRecord:
        values = {
            "id": user_id.uuid,
            "login_name": login_name.value,
            "status": AccountStatus.ACTIVE.value,
            "credential_version": 1,
            "revision": 1,
            "created_at": audit.occurred_at,
            "updated_at": audit.occurred_at,
            "locked_at": None,
            "lock_expires_at": None,
            "disabled_at": None,
        }
        try:
            async with self._database.session() as session:
                await session.execute(insert(users).values(**values))
                await session.execute(
                    insert(user_password_credentials).values(
                        user_id=user_id.uuid,
                        version=password_hash.version,
                        password_hash=password_hash.encoded,
                        pepper_version=password_hash.pepper_version,
                        created_at=audit.occurred_at,
                        updated_at=audit.occurred_at,
                    )
                )
                await session.execute(
                    insert(account_audit_events).values(
                        **_audit_values(
                            event_type=AccountAuditEventType.ACCOUNT_CREATED,
                            user_id=user_id,
                            reason_code="operations_provisioned",
                            audit=audit,
                        )
                    )
                )
        except IntegrityError:
            raise AccountAlreadyExists from None
        except SQLAlchemyError:
            raise AccountPersistenceUnavailable from None
        return _account_record(cast(RowMapping, values))

    async def find_for_authentication(
        self,
        login_name: LoginName,
    ) -> AccountAuthenticationRecord | None:
        try:
            async with self._database.session() as session:
                row = (
                    (
                        await session.execute(
                            select(
                                users,
                                user_password_credentials.c.version.label("password_version"),
                                user_password_credentials.c.password_hash,
                                user_password_credentials.c.pepper_version,
                            )
                            .join(
                                user_password_credentials,
                                user_password_credentials.c.user_id == users.c.id,
                            )
                            .where(users.c.login_name == login_name.value)
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
        except SQLAlchemyError:
            raise AccountPersistenceUnavailable from None
        if row is None:
            return None
        return AccountAuthenticationRecord(
            account=_account_record(row),
            password_hash=_password_hash(row),
        )

    async def transition(
        self,
        *,
        user_id: UserId,
        expected_revision: int,
        target_status: AccountStatus,
        audit: AccountAuditContext,
    ) -> AccountRecord:
        try:
            async with self._database.session() as session:
                current_row = (
                    (
                        await session.execute(
                            select(users).where(users.c.id == user_id.uuid).with_for_update()
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if current_row is None:
                    raise AccountNotFound
                current = _account_record(current_row)
                if current.revision != expected_revision:
                    raise AccountRevisionConflict
                event_type, reason_code = _transition_event(current.status, target_status)
                credential_version = current.credential_version + (
                    1 if target_status is AccountStatus.DISABLED else 0
                )
                values = {
                    "status": target_status.value,
                    "credential_version": credential_version,
                    "revision": current.revision + 1,
                    "updated_at": audit.occurred_at,
                    "locked_at": (
                        audit.occurred_at if target_status is AccountStatus.LOCKED else None
                    ),
                    "lock_expires_at": (
                        audit.occurred_at + timedelta(minutes=15)
                        if target_status is AccountStatus.LOCKED
                        else None
                    ),
                    "disabled_at": (
                        audit.occurred_at if target_status is AccountStatus.DISABLED else None
                    ),
                }
                updated_row = (
                    (
                        await session.execute(
                            update(users)
                            .where(users.c.id == user_id.uuid)
                            .values(**values)
                            .returning(users)
                        )
                    )
                    .mappings()
                    .one()
                )
                await session.execute(
                    insert(account_audit_events).values(
                        **_audit_values(
                            event_type=event_type,
                            user_id=user_id,
                            reason_code=reason_code,
                            audit=audit,
                        )
                    )
                )
                if target_status is AccountStatus.DISABLED:
                    await session.execute(
                        update(account_session_families)
                        .where(
                            account_session_families.c.user_id == user_id.uuid,
                            account_session_families.c.revoked_at.is_(None),
                        )
                        .values(
                            revoked_at=audit.occurred_at,
                            revocation_reason="account_disabled",
                        )
                    )
                    await session.execute(
                        update(account_session_tokens)
                        .where(
                            account_session_tokens.c.user_id == user_id.uuid,
                            account_session_tokens.c.revoked_at.is_(None),
                        )
                        .values(revoked_at=audit.occurred_at)
                    )
                    await session.execute(
                        insert(account_audit_events).values(
                            **_audit_values(
                                event_type=AccountAuditEventType.SESSION_ALL_REVOKED,
                                user_id=user_id,
                                reason_code="account_disabled",
                                audit=audit,
                            )
                        )
                    )
                return _account_record(updated_row)
        except SQLAlchemyError:
            raise AccountPersistenceUnavailable from None

    async def emergency_revoke(
        self,
        *,
        user_id: UserId,
        expected_revision: int,
        audit: AccountAuditContext,
    ) -> EmergencyRevocationRecord:
        try:
            async with self._database.session() as session:
                current_row = (
                    (
                        await session.execute(
                            select(users).where(users.c.id == user_id.uuid).with_for_update()
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if current_row is None:
                    raise AccountNotFound
                current = _account_record(current_row)
                if current.revision != expected_revision:
                    raise AccountRevisionConflict
                if current.status not in {AccountStatus.ACTIVE, AccountStatus.LOCKED}:
                    raise AccountTransitionRejected
                updated_row = (
                    (
                        await session.execute(
                            update(users)
                            .where(
                                users.c.id == user_id.uuid,
                                users.c.revision == expected_revision,
                            )
                            .values(
                                status=AccountStatus.DISABLED.value,
                                credential_version=current.credential_version + 1,
                                revision=current.revision + 1,
                                updated_at=audit.occurred_at,
                                locked_at=None,
                                lock_expires_at=None,
                                disabled_at=audit.occurred_at,
                            )
                            .returning(users)
                        )
                    )
                    .mappings()
                    .one()
                )
                device_ids = tuple(
                    (
                        await session.execute(
                            select(installations.c.id)
                            .where(
                                installations.c.owner_user_id == user_id.uuid,
                                installations.c.status == "active",
                            )
                            .order_by(installations.c.id)
                            .with_for_update()
                        )
                    )
                    .scalars()
                    .all()
                )
                await session.execute(
                    update(account_session_families)
                    .where(
                        account_session_families.c.user_id == user_id.uuid,
                        account_session_families.c.revoked_at.is_(None),
                    )
                    .values(
                        revoked_at=audit.occurred_at,
                        revocation_reason="account_disabled",
                    )
                )
                await session.execute(
                    update(account_session_tokens)
                    .where(
                        account_session_tokens.c.user_id == user_id.uuid,
                        account_session_tokens.c.revoked_at.is_(None),
                    )
                    .values(revoked_at=audit.occurred_at)
                )
                if device_ids:
                    await session.execute(
                        update(installations)
                        .where(
                            installations.c.id.in_(device_ids),
                            installations.c.status == "active",
                        )
                        .values(
                            status="revoked",
                            revision=installations.c.revision + 1,
                            updated_at=audit.occurred_at,
                            revoked_at=audit.occurred_at,
                        )
                    )
                    await session.execute(
                        update(device_credentials)
                        .where(
                            device_credentials.c.installation_id.in_(device_ids),
                            device_credentials.c.status == "active",
                        )
                        .values(
                            status="revoked",
                            updated_at=audit.occurred_at,
                            revoked_at=audit.occurred_at,
                        )
                    )
                    await session.execute(
                        update(device_sessions)
                        .where(
                            device_sessions.c.installation_id.in_(device_ids),
                            device_sessions.c.revoked_at.is_(None),
                        )
                        .values(revoked_at=audit.occurred_at)
                    )
                for _device_id in device_ids:
                    await session.execute(
                        insert(account_audit_events).values(
                            **_audit_values(
                                event_type=AccountAuditEventType.DEVICE_REVOKED,
                                user_id=user_id,
                                reason_code="operations_emergency_revoked",
                                audit=audit,
                            )
                        )
                    )
                for event_type in (
                    AccountAuditEventType.ACCOUNT_DISABLED,
                    AccountAuditEventType.SESSION_ALL_REVOKED,
                ):
                    await session.execute(
                        insert(account_audit_events).values(
                            **_audit_values(
                                event_type=event_type,
                                user_id=user_id,
                                reason_code="operations_emergency_revoked",
                                audit=audit,
                            )
                        )
                    )
                return EmergencyRevocationRecord(
                    account=_account_record(updated_row),
                    revoked_device_count=len(device_ids),
                )
        except SQLAlchemyError:
            raise AccountPersistenceUnavailable from None


__all__ = ["SqlAlchemyCustomerAccountRepository"]
