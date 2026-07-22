"""Atomic PostgreSQL account Installation binding adapter."""

import hashlib
import secrets
from datetime import datetime
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from automation_tool.control_plane.application.account_installation_bindings import (
    AccountBindingChallengeRecord,
    BindingChallengeExpired,
    BindingChallengeUsed,
    BindingProofRejected,
    CrossAccountBindingRejected,
    RevokedInstallationBindingRejected,
)
from automation_tool.control_plane.application.account_sessions import AccountSessionRejected
from automation_tool.control_plane.application.device_credentials import (
    DEVICE_CREDENTIAL_SCOPE,
    IssuedDeviceCredential,
    PendingDeviceCredential,
)
from automation_tool.control_plane.application.registration import RegisteredInstallation
from automation_tool.control_plane.domain import (
    AccountAuditActorKind,
    AccountAuditEventType,
    AccountStatus,
    InstallationId,
    InstallationStatus,
    UserId,
)
from automation_tool.control_plane.infrastructure.database.schema import (
    account_audit_events,
    account_installation_binding_challenges,
    device_credentials,
    device_sessions,
    installations,
    users,
)
from automation_tool.control_plane.infrastructure.database.session import Database


class SqlAlchemyAccountInstallationBindingRepository:
    """Consume one account-bound proof and rotate the device credential atomically."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def save_challenge(self, challenge: AccountBindingChallengeRecord) -> None:
        try:
            async with self._database.session() as session:
                await session.execute(
                    delete(account_installation_binding_challenges).where(
                        account_installation_binding_challenges.c.device_public_key
                        == challenge.device_public_key,
                        account_installation_binding_challenges.c.consumed_at.is_(None),
                        account_installation_binding_challenges.c.expires_at
                        <= challenge.created_at,
                    )
                )
                await session.execute(
                    insert(account_installation_binding_challenges).values(
                        id=challenge.challenge_id,
                        user_id=challenge.user_id.uuid,
                        device_public_key=challenge.device_public_key,
                        proof_hash=challenge.proof_hash,
                        created_at=challenge.created_at,
                        expires_at=challenge.expires_at,
                    )
                )
        except IntegrityError:
            raise BindingChallengeUsed from None

    async def complete_challenge(
        self,
        *,
        challenge_id: UUID,
        user_id: UserId,
        signing_payload: bytes,
        signature: bytes,
        completed_at: datetime,
        pending_credential: PendingDeviceCredential,
        request_id: str,
    ) -> RegisteredInstallation:
        try:
            async with self._database.session() as session:
                user_status = await session.scalar(
                    select(users.c.status).where(users.c.id == user_id.uuid).with_for_update()
                )
                if user_status != AccountStatus.ACTIVE.value:
                    raise AccountSessionRejected
                challenge = (
                    (
                        await session.execute(
                            select(account_installation_binding_challenges)
                            .where(account_installation_binding_challenges.c.id == challenge_id)
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if challenge is None or challenge["user_id"] != user_id.uuid:
                    raise BindingProofRejected
                if not secrets.compare_digest(
                    challenge["proof_hash"], hashlib.sha256(signing_payload).digest()
                ):
                    raise BindingProofRejected
                if challenge["consumed_at"] is not None:
                    raise BindingChallengeUsed
                if completed_at >= challenge["expires_at"]:
                    raise BindingChallengeExpired
                try:
                    Ed25519PublicKey.from_public_bytes(challenge["device_public_key"]).verify(
                        signature, signing_payload
                    )
                except (InvalidSignature, ValueError):
                    raise BindingProofRejected from None

                installation = (
                    (
                        await session.execute(
                            select(installations)
                            .where(
                                installations.c.device_public_key == challenge["device_public_key"]
                            )
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if installation is None:
                    installation_id = InstallationId.new().uuid
                    installation = (
                        (
                            await session.execute(
                                insert(installations)
                                .values(
                                    id=installation_id,
                                    device_public_key=challenge["device_public_key"],
                                    owner_user_id=user_id.uuid,
                                    created_at=completed_at,
                                    updated_at=completed_at,
                                )
                                .returning(*installations.c)
                            )
                        )
                        .mappings()
                        .one()
                    )
                else:
                    installation_id = installation["id"]
                    if installation["status"] != InstallationStatus.ACTIVE.value:
                        raise RevokedInstallationBindingRejected
                    owner = installation["owner_user_id"]
                    if owner is not None and owner != user_id.uuid:
                        raise CrossAccountBindingRejected
                    if owner is None:
                        installation = (
                            (
                                await session.execute(
                                    update(installations)
                                    .where(
                                        installations.c.id == installation_id,
                                        installations.c.owner_user_id.is_(None),
                                    )
                                    .values(
                                        owner_user_id=user_id.uuid,
                                        revision=installations.c.revision + 1,
                                        updated_at=completed_at,
                                    )
                                    .returning(*installations.c)
                                )
                            )
                            .mappings()
                            .one()
                        )

                current = (
                    (
                        await session.execute(
                            select(device_credentials)
                            .where(
                                device_credentials.c.installation_id == installation_id,
                                device_credentials.c.status == "active",
                            )
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if current is not None:
                    next_version = current["version"] + 1
                    await session.execute(
                        update(device_credentials)
                        .where(device_credentials.c.id == current["id"])
                        .values(
                            status="rotated",
                            updated_at=completed_at,
                            revoked_at=completed_at,
                            replaced_by_id=pending_credential.credential_id,
                        )
                    )
                    await session.execute(
                        update(device_sessions)
                        .where(
                            device_sessions.c.device_credential_id == current["id"],
                            device_sessions.c.revoked_at.is_(None),
                        )
                        .values(revoked_at=completed_at)
                    )
                else:
                    maximum = await session.scalar(
                        select(func.max(device_credentials.c.version)).where(
                            device_credentials.c.installation_id == installation_id
                        )
                    )
                    next_version = int(maximum or 0) + 1
                await session.execute(
                    insert(device_credentials).values(
                        id=pending_credential.credential_id,
                        installation_id=installation_id,
                        version=next_version,
                        scope=DEVICE_CREDENTIAL_SCOPE,
                        secret_digest=pending_credential.secret_digest,
                        status="active",
                        created_at=completed_at,
                        updated_at=completed_at,
                    )
                )
                await session.execute(
                    update(account_installation_binding_challenges)
                    .where(account_installation_binding_challenges.c.id == challenge_id)
                    .values(consumed_at=completed_at, installation_id=installation_id)
                )
                await session.execute(
                    insert(account_audit_events).values(
                        event_id=uuid4(),
                        event_type=AccountAuditEventType.DEVICE_BOUND.value,
                        occurred_at=completed_at,
                        actor_kind=AccountAuditActorKind.USER.value,
                        actor_id=user_id.uuid,
                        subject_user_id=user_id.uuid,
                        outcome="succeeded",
                        reason_code="device_bound",
                        request_id=request_id,
                    )
                )
                return RegisteredInstallation(
                    installation_id=installation_id,
                    status=installation["status"],
                    revision=installation["revision"],
                    device_credential=IssuedDeviceCredential(
                        credential_id=pending_credential.credential_id,
                        installation_id=installation_id,
                        credential=pending_credential.credential,
                        version=next_version,
                        scope=DEVICE_CREDENTIAL_SCOPE,
                    ),
                )
        except IntegrityError:
            raise BindingChallengeUsed from None


__all__ = ["SqlAlchemyAccountInstallationBindingRepository"]
