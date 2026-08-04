"""Atomic PostgreSQL persistence for opaque customer account sessions."""

import secrets
from collections.abc import Sequence
from datetime import datetime
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import insert, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from automation_tool.control_plane.application.account_sessions import (
    ACCOUNT_ACCESS_LIFETIME,
    ACCOUNT_RECOVERY_LIFETIME,
    ACCOUNT_REFRESH_LIFETIME,
    LOGIN_FAILURE_WINDOW,
    LOGIN_IDENTIFIER_FAILURE_LIMIT,
    LOGIN_LOCK_LIFETIME,
    LOGIN_SOURCE_FAILURE_LIMIT,
    AccountAuthenticationRejected,
    AccountProjection,
    AccountRecoveryRejected,
    AccountSessionRejected,
    AccountSessionUnavailable,
    AuthenticatedAccountSession,
    IssuedAccountSession,
    IssuedRecoveryToken,
    ParsedAccountToken,
    PasswordVerifier,
    PendingAccountSession,
    PendingRecoveryToken,
)
from automation_tool.control_plane.application.customer_accounts import AccountAuditActor
from automation_tool.control_plane.domain import (
    AccountAuditActorKind,
    AccountAuditEventType,
    AccountStatus,
    LoginName,
    PasswordHash,
    UserId,
)
from automation_tool.control_plane.infrastructure.database.schema import (
    account_audit_events,
    account_login_rate_limits,
    account_recovery_tokens,
    account_session_families,
    account_session_tokens,
    user_password_credentials,
    users,
)
from automation_tool.control_plane.infrastructure.database.session import Database

_SYSTEM_ACTOR_ID = UUID("00000000-0000-4000-8000-000000000001")


def _password_hash(row: RowMapping) -> PasswordHash:
    return PasswordHash(
        encoded=cast(str, row["password_hash"]),
        pepper_version=cast(int, row["pepper_version"]),
        version=cast(int, row["password_version"]),
    )


def _projection(row: RowMapping) -> AccountProjection:
    return AccountProjection(
        user_id=UserId.parse(row["id"]),
        login_name=LoginName.parse(row["login_name"]),
        status=AccountStatus(cast(str, row["status"])),
    )


async def _audit(
    session: AsyncSession,
    *,
    event_type: AccountAuditEventType,
    occurred_at: datetime,
    actor_kind: AccountAuditActorKind,
    actor_id: UUID,
    subject_user_id: UUID | None,
    outcome: str,
    reason_code: str,
    request_id: str,
    source_fingerprint: bytes | None = None,
) -> None:
    await session.execute(
        insert(account_audit_events).values(
            event_id=uuid4(),
            event_type=event_type.value,
            occurred_at=occurred_at,
            actor_kind=actor_kind.value,
            actor_id=actor_id,
            subject_user_id=subject_user_id,
            outcome=outcome,
            reason_code=reason_code,
            request_id=request_id,
            source_fingerprint=source_fingerprint,
        )
    )


async def _rate_rows(
    session: AsyncSession,
    *,
    identifier_fingerprint: bytes,
    source_fingerprint: bytes,
    now: datetime,
) -> dict[str, RowMapping]:
    scopes = (
        ("identifier", identifier_fingerprint),
        ("source", source_fingerprint),
    )
    for kind, fingerprint in scopes:
        await session.execute(
            postgresql_insert(account_login_rate_limits)
            .values(
                scope_kind=kind,
                scope_fingerprint=fingerprint,
                window_started_at=now,
                failure_count=0,
                blocked_until=None,
                updated_at=now,
            )
            .on_conflict_do_nothing()
        )
    rows: dict[str, RowMapping] = {}
    for kind, fingerprint in scopes:
        row = (
            (
                await session.execute(
                    select(account_login_rate_limits)
                    .where(
                        account_login_rate_limits.c.scope_kind == kind,
                        account_login_rate_limits.c.scope_fingerprint == fingerprint,
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .one()
        )
        if now >= cast(datetime, row["window_started_at"]) + LOGIN_FAILURE_WINDOW:
            row = (
                (
                    await session.execute(
                        update(account_login_rate_limits)
                        .where(
                            account_login_rate_limits.c.scope_kind == kind,
                            account_login_rate_limits.c.scope_fingerprint == fingerprint,
                        )
                        .values(
                            window_started_at=now,
                            failure_count=0,
                            blocked_until=None,
                            updated_at=now,
                        )
                        .returning(account_login_rate_limits)
                    )
                )
                .mappings()
                .one()
            )
        rows[kind] = row
    return rows


async def _record_login_failure(
    session: AsyncSession,
    *,
    rate_rows: dict[str, RowMapping],
    now: datetime,
) -> bool:
    identifier_blocked = False
    for kind, limit in (
        ("identifier", LOGIN_IDENTIFIER_FAILURE_LIMIT),
        ("source", LOGIN_SOURCE_FAILURE_LIMIT),
    ):
        row = rate_rows[kind]
        count = min(cast(int, row["failure_count"]) + 1, LOGIN_SOURCE_FAILURE_LIMIT)
        blocked_until = now + LOGIN_LOCK_LIFETIME if count >= limit else None
        await session.execute(
            update(account_login_rate_limits)
            .where(
                account_login_rate_limits.c.scope_kind == kind,
                account_login_rate_limits.c.scope_fingerprint == row["scope_fingerprint"],
            )
            .values(
                failure_count=count,
                blocked_until=blocked_until,
                updated_at=now,
            )
        )
        if kind == "identifier" and blocked_until is not None:
            identifier_blocked = True
    return identifier_blocked


async def _reset_rate_rows(
    session: AsyncSession,
    rows: dict[str, RowMapping],
    now: datetime,
) -> None:
    for kind, row in rows.items():
        await session.execute(
            update(account_login_rate_limits)
            .where(
                account_login_rate_limits.c.scope_kind == kind,
                account_login_rate_limits.c.scope_fingerprint == row["scope_fingerprint"],
            )
            .values(
                window_started_at=now,
                failure_count=0,
                blocked_until=None,
                updated_at=now,
            )
        )


async def _insert_session(
    session: AsyncSession,
    *,
    pending: PendingAccountSession,
    user_row: RowMapping,
    now: datetime,
) -> IssuedAccountSession:
    user_id = cast(UUID, user_row["id"])
    credential_version = cast(int, user_row["credential_version"])
    refresh_expires_at = now + ACCOUNT_REFRESH_LIFETIME
    access_expires_at = now + ACCOUNT_ACCESS_LIFETIME
    await session.execute(
        insert(account_session_families).values(
            id=pending.family_id,
            user_id=user_id,
            credential_version=credential_version,
            created_at=now,
            absolute_expires_at=refresh_expires_at,
            revoked_at=None,
            revocation_reason=None,
        )
    )
    await session.execute(
        insert(account_session_tokens),
        [
            {
                "id": pending.access.token_id,
                "family_id": pending.family_id,
                "user_id": user_id,
                "credential_version": credential_version,
                "kind": "access",
                "secret_digest": pending.access.secret_digest,
                "created_at": now,
                "expires_at": access_expires_at,
                "consumed_at": None,
                "revoked_at": None,
                "replaced_by_id": None,
            },
            {
                "id": pending.refresh.token_id,
                "family_id": pending.family_id,
                "user_id": user_id,
                "credential_version": credential_version,
                "kind": "refresh",
                "secret_digest": pending.refresh.secret_digest,
                "created_at": now,
                "expires_at": refresh_expires_at,
                "consumed_at": None,
                "revoked_at": None,
                "replaced_by_id": None,
            },
        ],
    )
    return IssuedAccountSession(
        access_token=pending.access.token,
        refresh_token=pending.refresh.token,
        access_expires_at=access_expires_at,
        refresh_expires_at=refresh_expires_at,
        account=_projection(user_row),
    )


async def _load_token(
    session: AsyncSession,
    *,
    presented: ParsedAccountToken,
    kind: str,
) -> RowMapping:
    row = (
        (
            await session.execute(
                select(account_session_tokens)
                .where(account_session_tokens.c.id == presented.token_id)
                .with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    if (
        row is None
        or cast(str, row["kind"]) != kind
        or not secrets.compare_digest(
            presented.secret_digest,
            cast(bytes, row["secret_digest"]),
        )
    ):
        raise AccountSessionRejected
    return row


async def _load_active_binding(
    session: AsyncSession,
    *,
    token_row: RowMapping,
    now: datetime,
) -> tuple[RowMapping, RowMapping]:
    family_row = (
        (
            await session.execute(
                select(account_session_families)
                .where(account_session_families.c.id == token_row["family_id"])
                .with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    user_row = (
        (
            await session.execute(
                select(users).where(users.c.id == token_row["user_id"]).with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    if (
        family_row is None
        or user_row is None
        or token_row["revoked_at"] is not None
        or cast(datetime, token_row["expires_at"]) <= now
        or family_row["revoked_at"] is not None
        or cast(datetime, family_row["absolute_expires_at"]) <= now
        or cast(str, user_row["status"]) != AccountStatus.ACTIVE.value
        or token_row["credential_version"] != user_row["credential_version"]
        or family_row["credential_version"] != user_row["credential_version"]
    ):
        raise AccountSessionRejected
    return family_row, user_row


async def _revoke_family(
    session: AsyncSession,
    *,
    family_id: UUID,
    now: datetime,
    reason: str,
) -> None:
    await session.execute(
        update(account_session_families)
        .where(
            account_session_families.c.id == family_id,
            account_session_families.c.revoked_at.is_(None),
        )
        .values(revoked_at=now, revocation_reason=reason)
    )
    await session.execute(
        update(account_session_tokens)
        .where(
            account_session_tokens.c.family_id == family_id,
            account_session_tokens.c.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )


async def _revoke_all_families(
    session: AsyncSession,
    *,
    user_id: UUID,
    now: datetime,
    reason: str,
) -> None:
    await session.execute(
        update(account_session_families)
        .where(
            account_session_families.c.user_id == user_id,
            account_session_families.c.revoked_at.is_(None),
        )
        .values(revoked_at=now, revocation_reason=reason)
    )
    await session.execute(
        update(account_session_tokens)
        .where(
            account_session_tokens.c.user_id == user_id,
            account_session_tokens.c.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )


class SqlAlchemyAccountSessionRepository:
    """Rotate digest-only tokens and credentials inside serialized transactions."""

    def __init__(self, database: Database) -> None:
        if not isinstance(database, Database):
            raise AccountSessionUnavailable
        self._database = database

    async def login(
        self,
        *,
        login_name: LoginName,
        identifier_fingerprint: bytes,
        source_fingerprint: bytes,
        verify_password: PasswordVerifier,
        dummy_password_hash: PasswordHash,
        pending: PendingAccountSession,
        authenticated_at: datetime,
        request_id: str,
    ) -> IssuedAccountSession:
        rejected = False
        issued: IssuedAccountSession | None = None
        try:
            async with self._database.session() as session:
                rates = await _rate_rows(
                    session,
                    identifier_fingerprint=identifier_fingerprint,
                    source_fingerprint=source_fingerprint,
                    now=authenticated_at,
                )
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
                            .with_for_update(of=users)
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                verified = verify_password(
                    dummy_password_hash if row is None else _password_hash(row)
                )
                rate_blocked = any(
                    rate["blocked_until"] is not None
                    and cast(datetime, rate["blocked_until"]) > authenticated_at
                    for rate in rates.values()
                )
                account_blocked = False
                if row is not None and row["status"] == AccountStatus.LOCKED.value:
                    expires_at = cast(datetime | None, row["lock_expires_at"])
                    if expires_at is not None and expires_at <= authenticated_at:
                        row = (
                            (
                                await session.execute(
                                    update(users)
                                    .where(users.c.id == row["id"])
                                    .values(
                                        status=AccountStatus.ACTIVE.value,
                                        revision=users.c.revision + 1,
                                        updated_at=authenticated_at,
                                        locked_at=None,
                                        lock_expires_at=None,
                                    )
                                    .returning(users)
                                )
                            )
                            .mappings()
                            .one()
                        )
                        await _audit(
                            session,
                            event_type=AccountAuditEventType.ACCOUNT_UNLOCKED,
                            occurred_at=authenticated_at,
                            actor_kind=AccountAuditActorKind.SYSTEM,
                            actor_id=_SYSTEM_ACTOR_ID,
                            subject_user_id=cast(UUID, row["id"]),
                            outcome="succeeded",
                            reason_code="temporary_lock_expired",
                            request_id=request_id,
                        )
                    else:
                        account_blocked = True
                disabled = row is not None and row["status"] == AccountStatus.DISABLED.value
                if row is None or not verified or rate_blocked or account_blocked or disabled:
                    identifier_blocked = await _record_login_failure(
                        session,
                        rate_rows=rates,
                        now=authenticated_at,
                    )
                    if (
                        row is not None
                        and identifier_blocked
                        and row["status"] == AccountStatus.ACTIVE.value
                    ):
                        await session.execute(
                            update(users)
                            .where(users.c.id == row["id"])
                            .values(
                                status=AccountStatus.LOCKED.value,
                                revision=users.c.revision + 1,
                                updated_at=authenticated_at,
                                locked_at=authenticated_at,
                                lock_expires_at=authenticated_at + LOGIN_LOCK_LIFETIME,
                            )
                        )
                        await _audit(
                            session,
                            event_type=AccountAuditEventType.ACCOUNT_LOCKED,
                            occurred_at=authenticated_at,
                            actor_kind=AccountAuditActorKind.SYSTEM,
                            actor_id=_SYSTEM_ACTOR_ID,
                            subject_user_id=cast(UUID, row["id"]),
                            outcome="succeeded",
                            reason_code="login_failure_limit",
                            request_id=request_id,
                            source_fingerprint=source_fingerprint,
                        )
                    await _audit(
                        session,
                        event_type=AccountAuditEventType.LOGIN_FAILED,
                        occurred_at=authenticated_at,
                        actor_kind=AccountAuditActorKind.SYSTEM,
                        actor_id=_SYSTEM_ACTOR_ID,
                        subject_user_id=None if row is None else cast(UUID, row["id"]),
                        outcome="rejected",
                        reason_code="invalid_credentials",
                        request_id=request_id,
                        source_fingerprint=source_fingerprint,
                    )
                    rejected = True
                else:
                    await _reset_rate_rows(session, rates, authenticated_at)
                    issued = await _insert_session(
                        session,
                        pending=pending,
                        user_row=row,
                        now=authenticated_at,
                    )
                    await _audit(
                        session,
                        event_type=AccountAuditEventType.LOGIN_SUCCEEDED,
                        occurred_at=authenticated_at,
                        actor_kind=AccountAuditActorKind.USER,
                        actor_id=cast(UUID, row["id"]),
                        subject_user_id=cast(UUID, row["id"]),
                        outcome="succeeded",
                        reason_code="password_authenticated",
                        request_id=request_id,
                        source_fingerprint=source_fingerprint,
                    )
        except (OSError, SQLAlchemyError):
            raise AccountSessionUnavailable from None
        if rejected or issued is None:
            raise AccountAuthenticationRejected
        return issued

    async def refresh(
        self,
        *,
        presented: ParsedAccountToken,
        pending: PendingAccountSession,
        refreshed_at: datetime,
        source_fingerprint: bytes,
        request_id: str,
    ) -> IssuedAccountSession:
        reuse = False
        issued: IssuedAccountSession | None = None
        try:
            async with self._database.session() as session:
                token = await _load_token(session, presented=presented, kind="refresh")
                family = (
                    (
                        await session.execute(
                            select(account_session_families)
                            .where(account_session_families.c.id == token["family_id"])
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .one()
                )
                if token["consumed_at"] is not None:
                    if family["revoked_at"] is None:
                        await _revoke_family(
                            session,
                            family_id=cast(UUID, token["family_id"]),
                            now=refreshed_at,
                            reason="refresh_reuse",
                        )
                        await _audit(
                            session,
                            event_type=AccountAuditEventType.SESSION_REUSE_DETECTED,
                            occurred_at=refreshed_at,
                            actor_kind=AccountAuditActorKind.SYSTEM,
                            actor_id=_SYSTEM_ACTOR_ID,
                            subject_user_id=cast(UUID, token["user_id"]),
                            outcome="rejected",
                            reason_code="refresh_token_replayed",
                            request_id=request_id,
                            source_fingerprint=source_fingerprint,
                        )
                    reuse = True
                else:
                    family, user = await _load_active_binding(
                        session,
                        token_row=token,
                        now=refreshed_at,
                    )
                    absolute_expires_at = cast(datetime, family["absolute_expires_at"])
                    access_expires_at = min(
                        refreshed_at + ACCOUNT_ACCESS_LIFETIME,
                        absolute_expires_at,
                    )
                    refresh_expires_at = absolute_expires_at
                    await session.execute(
                        insert(account_session_tokens),
                        [
                            {
                                "id": pending.access.token_id,
                                "family_id": token["family_id"],
                                "user_id": token["user_id"],
                                "credential_version": token["credential_version"],
                                "kind": "access",
                                "secret_digest": pending.access.secret_digest,
                                "created_at": refreshed_at,
                                "expires_at": access_expires_at,
                                "consumed_at": None,
                                "revoked_at": None,
                                "replaced_by_id": None,
                            },
                            {
                                "id": pending.refresh.token_id,
                                "family_id": token["family_id"],
                                "user_id": token["user_id"],
                                "credential_version": token["credential_version"],
                                "kind": "refresh",
                                "secret_digest": pending.refresh.secret_digest,
                                "created_at": refreshed_at,
                                "expires_at": refresh_expires_at,
                                "consumed_at": None,
                                "revoked_at": None,
                                "replaced_by_id": None,
                            },
                        ],
                    )
                    await session.execute(
                        update(account_session_tokens)
                        .where(account_session_tokens.c.id == presented.token_id)
                        .values(
                            consumed_at=refreshed_at,
                            replaced_by_id=pending.refresh.token_id,
                        )
                    )
                    await session.execute(
                        update(account_session_tokens)
                        .where(
                            account_session_tokens.c.family_id == token["family_id"],
                            account_session_tokens.c.kind == "access",
                            account_session_tokens.c.revoked_at.is_(None),
                            account_session_tokens.c.id != pending.access.token_id,
                        )
                        .values(revoked_at=refreshed_at)
                    )
                    issued = IssuedAccountSession(
                        access_token=pending.access.token,
                        refresh_token=pending.refresh.token,
                        access_expires_at=access_expires_at,
                        refresh_expires_at=refresh_expires_at,
                        account=_projection(user),
                    )
                    await _audit(
                        session,
                        event_type=AccountAuditEventType.SESSION_REFRESHED,
                        occurred_at=refreshed_at,
                        actor_kind=AccountAuditActorKind.USER,
                        actor_id=cast(UUID, token["user_id"]),
                        subject_user_id=cast(UUID, token["user_id"]),
                        outcome="succeeded",
                        reason_code="refresh_rotated",
                        request_id=request_id,
                        source_fingerprint=source_fingerprint,
                    )
        except (OSError, SQLAlchemyError):
            raise AccountSessionUnavailable from None
        if reuse or issued is None:
            raise AccountSessionRejected
        return issued

    async def logout(
        self,
        *,
        presented: ParsedAccountToken,
        logged_out_at: datetime,
        request_id: str,
    ) -> None:
        try:
            async with self._database.session() as session:
                token = await _load_token(session, presented=presented, kind="refresh")
                await _load_active_binding(session, token_row=token, now=logged_out_at)
                if token["consumed_at"] is not None:
                    raise AccountSessionRejected
                await _revoke_family(
                    session,
                    family_id=cast(UUID, token["family_id"]),
                    now=logged_out_at,
                    reason="logout",
                )
                await _audit(
                    session,
                    event_type=AccountAuditEventType.SESSION_LOGGED_OUT,
                    occurred_at=logged_out_at,
                    actor_kind=AccountAuditActorKind.USER,
                    actor_id=cast(UUID, token["user_id"]),
                    subject_user_id=cast(UUID, token["user_id"]),
                    outcome="succeeded",
                    reason_code="user_logout",
                    request_id=request_id,
                )
        except (OSError, SQLAlchemyError):
            raise AccountSessionUnavailable from None

    async def authenticate_access(
        self,
        *,
        presented: ParsedAccountToken,
        authenticated_at: datetime,
    ) -> AuthenticatedAccountSession:
        try:
            async with self._database.session() as session:
                token = await _load_token(session, presented=presented, kind="access")
                family, _ = await _load_active_binding(
                    session,
                    token_row=token,
                    now=authenticated_at,
                )
                return AuthenticatedAccountSession(
                    token_id=cast(UUID, token["id"]),
                    family_id=cast(UUID, family["id"]),
                    user_id=UserId.parse(token["user_id"]),
                    credential_version=cast(int, token["credential_version"]),
                    expires_at=cast(datetime, token["expires_at"]),
                )
        except (OSError, SQLAlchemyError):
            raise AccountSessionUnavailable from None

    async def change_password(
        self,
        *,
        presented: ParsedAccountToken,
        verify_current_password: PasswordVerifier,
        replacement: PasswordHash,
        changed_at: datetime,
        request_id: str,
    ) -> None:
        rejected = False
        try:
            async with self._database.session() as session:
                token = await _load_token(session, presented=presented, kind="access")
                _, user = await _load_active_binding(
                    session,
                    token_row=token,
                    now=changed_at,
                )
                credential = (
                    (
                        await session.execute(
                            select(
                                user_password_credentials.c.version.label("password_version"),
                                user_password_credentials.c.password_hash,
                                user_password_credentials.c.pepper_version,
                            )
                            .where(user_password_credentials.c.user_id == token["user_id"])
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .one()
                )
                if not verify_current_password(_password_hash(credential)):
                    await _audit(
                        session,
                        event_type=AccountAuditEventType.CREDENTIAL_CHANGED,
                        occurred_at=changed_at,
                        actor_kind=AccountAuditActorKind.USER,
                        actor_id=cast(UUID, token["user_id"]),
                        subject_user_id=cast(UUID, token["user_id"]),
                        outcome="rejected",
                        reason_code="current_password_invalid",
                        request_id=request_id,
                    )
                    rejected = True
                else:
                    version = cast(int, user["credential_version"]) + 1
                    await session.execute(
                        update(user_password_credentials)
                        .where(user_password_credentials.c.user_id == token["user_id"])
                        .values(
                            version=version,
                            password_hash=replacement.encoded,
                            pepper_version=replacement.pepper_version,
                            updated_at=changed_at,
                        )
                    )
                    await session.execute(
                        update(users)
                        .where(users.c.id == token["user_id"])
                        .values(
                            credential_version=version,
                            revision=users.c.revision + 1,
                            updated_at=changed_at,
                        )
                    )
                    await _revoke_all_families(
                        session,
                        user_id=cast(UUID, token["user_id"]),
                        now=changed_at,
                        reason="credential_changed",
                    )
                    for event_type, reason_code in (
                        (AccountAuditEventType.CREDENTIAL_CHANGED, "user_password_changed"),
                        (AccountAuditEventType.SESSION_ALL_REVOKED, "credential_changed"),
                    ):
                        await _audit(
                            session,
                            event_type=event_type,
                            occurred_at=changed_at,
                            actor_kind=AccountAuditActorKind.USER,
                            actor_id=cast(UUID, token["user_id"]),
                            subject_user_id=cast(UUID, token["user_id"]),
                            outcome="succeeded",
                            reason_code=reason_code,
                            request_id=request_id,
                        )
        except (OSError, SQLAlchemyError):
            raise AccountSessionUnavailable from None
        if rejected:
            raise AccountAuthenticationRejected

    async def issue_recovery(
        self,
        *,
        login_name: LoginName,
        pending: PendingRecoveryToken,
        actor: AccountAuditActor,
        issued_at: datetime,
        request_id: str,
    ) -> IssuedRecoveryToken:
        try:
            async with self._database.session() as session:
                user = (
                    (
                        await session.execute(
                            select(users)
                            .where(users.c.login_name == login_name.value)
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if user is None or user["status"] == AccountStatus.DISABLED.value:
                    raise AccountRecoveryRejected
                expires_at = issued_at + ACCOUNT_RECOVERY_LIFETIME
                await session.execute(
                    insert(account_recovery_tokens).values(
                        id=pending.token_id,
                        user_id=user["id"],
                        credential_version=user["credential_version"],
                        secret_digest=pending.secret_digest,
                        issued_by_actor_id=actor.actor_id,
                        created_at=issued_at,
                        expires_at=expires_at,
                        consumed_at=None,
                    )
                )
                await _audit(
                    session,
                    event_type=AccountAuditEventType.RECOVERY_ISSUED,
                    occurred_at=issued_at,
                    actor_kind=actor.kind,
                    actor_id=actor.actor_id,
                    subject_user_id=cast(UUID, user["id"]),
                    outcome="succeeded",
                    reason_code="operations_recovery_issued",
                    request_id=request_id,
                    source_fingerprint=actor.source_fingerprint,
                )
                return IssuedRecoveryToken(
                    recovery_token=pending.token,
                    expires_at=expires_at,
                    account=_projection(user),
                )
        except (OSError, SQLAlchemyError):
            raise AccountSessionUnavailable from None

    async def recover_password(
        self,
        *,
        presented: ParsedAccountToken,
        replacement: PasswordHash,
        recovered_at: datetime,
        request_id: str,
    ) -> None:
        try:
            async with self._database.session() as session:
                recovery = (
                    (
                        await session.execute(
                            select(account_recovery_tokens)
                            .where(account_recovery_tokens.c.id == presented.token_id)
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if (
                    recovery is None
                    or not secrets.compare_digest(
                        presented.secret_digest,
                        cast(bytes, recovery["secret_digest"]),
                    )
                    or recovery["consumed_at"] is not None
                    or cast(datetime, recovery["expires_at"]) <= recovered_at
                ):
                    raise AccountRecoveryRejected
                user = (
                    (
                        await session.execute(
                            select(users).where(users.c.id == recovery["user_id"]).with_for_update()
                        )
                    )
                    .mappings()
                    .one()
                )
                if (
                    user["status"] == AccountStatus.DISABLED.value
                    or user["credential_version"] != recovery["credential_version"]
                ):
                    raise AccountRecoveryRejected
                version = cast(int, user["credential_version"]) + 1
                await session.execute(
                    update(account_recovery_tokens)
                    .where(account_recovery_tokens.c.id == presented.token_id)
                    .values(consumed_at=recovered_at)
                )
                await session.execute(
                    update(user_password_credentials)
                    .where(user_password_credentials.c.user_id == user["id"])
                    .values(
                        version=version,
                        password_hash=replacement.encoded,
                        pepper_version=replacement.pepper_version,
                        updated_at=recovered_at,
                    )
                )
                await session.execute(
                    update(users)
                    .where(users.c.id == user["id"])
                    .values(
                        status=AccountStatus.ACTIVE.value,
                        credential_version=version,
                        revision=users.c.revision + 1,
                        updated_at=recovered_at,
                        locked_at=None,
                        lock_expires_at=None,
                        disabled_at=None,
                    )
                )
                await _revoke_all_families(
                    session,
                    user_id=cast(UUID, user["id"]),
                    now=recovered_at,
                    reason="recovery",
                )
                for event_type, reason_code in (
                    (AccountAuditEventType.RECOVERY_CONSUMED, "recovery_token_consumed"),
                    (AccountAuditEventType.CREDENTIAL_CHANGED, "recovery_password_changed"),
                    (AccountAuditEventType.SESSION_ALL_REVOKED, "recovery"),
                ):
                    await _audit(
                        session,
                        event_type=event_type,
                        occurred_at=recovered_at,
                        actor_kind=AccountAuditActorKind.USER,
                        actor_id=cast(UUID, user["id"]),
                        subject_user_id=cast(UUID, user["id"]),
                        outcome="succeeded",
                        reason_code=reason_code,
                        request_id=request_id,
                    )
        except (OSError, SQLAlchemyError):
            raise AccountSessionUnavailable from None


__all__: Sequence[str] = ("SqlAlchemyAccountSessionRepository",)
