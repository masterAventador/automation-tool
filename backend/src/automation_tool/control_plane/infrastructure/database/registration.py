"""Atomic PostgreSQL adapter for one-time installation registration."""

import hashlib
import secrets
from datetime import datetime
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError

from automation_tool.control_plane.application.device_credentials import (
    DEVICE_CREDENTIAL_SCOPE,
    IssuedDeviceCredential,
    PendingDeviceCredential,
)
from automation_tool.control_plane.application.registration import (
    InstallationAlreadyRegistered,
    RegisteredInstallation,
    RegistrationChallengeExpired,
    RegistrationChallengeRecord,
    RegistrationChallengeUsed,
    RegistrationProofRejected,
)
from automation_tool.control_plane.domain import DemoEnvironmentId, InstallationId
from automation_tool.control_plane.infrastructure.database.schema import (
    device_credentials,
    installation_registration_challenges,
    installations,
)
from automation_tool.control_plane.infrastructure.database.session import Database


class SqlAlchemyInstallationRegistrationRepository:
    """Serialize challenge consumption and installation creation in one transaction."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def save_challenge(self, challenge: RegistrationChallengeRecord) -> None:
        async with self._database.session() as session:
            await session.execute(
                insert(installation_registration_challenges).values(
                    id=challenge.challenge_id,
                    environment_id=str(challenge.environment_id),
                    bootstrap_fingerprint=challenge.bootstrap_fingerprint,
                    device_public_key=challenge.device_public_key,
                    proof_hash=challenge.proof_hash,
                    created_at=challenge.created_at,
                    expires_at=challenge.expires_at,
                )
            )

    async def complete_challenge(
        self,
        *,
        challenge_id: UUID,
        environment_id: DemoEnvironmentId,
        bootstrap_fingerprint: bytes,
        signing_payload: bytes,
        signature: bytes,
        completed_at: datetime,
        initial_credential: PendingDeviceCredential,
    ) -> RegisteredInstallation:
        try:
            async with self._database.session() as session:
                challenge = (
                    (
                        await session.execute(
                            select(installation_registration_challenges)
                            .where(installation_registration_challenges.c.id == challenge_id)
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if challenge is None:
                    raise RegistrationProofRejected
                if (
                    challenge["environment_id"] != str(environment_id)
                    or not secrets.compare_digest(
                        challenge["bootstrap_fingerprint"], bootstrap_fingerprint
                    )
                    or not secrets.compare_digest(
                        challenge["proof_hash"], hashlib.sha256(signing_payload).digest()
                    )
                ):
                    raise RegistrationProofRejected
                if challenge["consumed_at"] is not None:
                    raise RegistrationChallengeUsed
                if completed_at >= challenge["expires_at"]:
                    raise RegistrationChallengeExpired
                try:
                    Ed25519PublicKey.from_public_bytes(challenge["device_public_key"]).verify(
                        signature,
                        signing_payload,
                    )
                except InvalidSignature:
                    raise RegistrationProofRejected from None

                installation_id = InstallationId.new()
                created = (
                    (
                        await session.execute(
                            insert(installations)
                            .values(
                                id=installation_id.uuid,
                                device_public_key=challenge["device_public_key"],
                                created_at=completed_at,
                                updated_at=completed_at,
                            )
                            .returning(
                                installations.c.id,
                                installations.c.status,
                                installations.c.revision,
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
                await session.execute(
                    insert(device_credentials).values(
                        id=initial_credential.credential_id,
                        installation_id=installation_id.uuid,
                        version=1,
                        scope=DEVICE_CREDENTIAL_SCOPE,
                        secret_digest=initial_credential.secret_digest,
                        status="active",
                        created_at=completed_at,
                        updated_at=completed_at,
                    )
                )
                await session.execute(
                    update(installation_registration_challenges)
                    .where(installation_registration_challenges.c.id == challenge_id)
                    .values(
                        consumed_at=completed_at,
                        installation_id=installation_id.uuid,
                    )
                )
                return RegisteredInstallation(
                    installation_id=created["id"],
                    status=created["status"],
                    revision=created["revision"],
                    device_credential=IssuedDeviceCredential(
                        credential_id=initial_credential.credential_id,
                        installation_id=installation_id.uuid,
                        credential=initial_credential.credential,
                        version=1,
                        scope=DEVICE_CREDENTIAL_SCOPE,
                    ),
                )
        except IntegrityError:
            raise InstallationAlreadyRegistered from None


__all__ = ["SqlAlchemyInstallationRegistrationRepository"]
