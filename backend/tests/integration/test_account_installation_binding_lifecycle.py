import asyncio
import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from conftest import AlembicRunner
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import delete, insert, select, text, update
from sqlalchemy.exc import IntegrityError

from automation_tool.control_plane.application.account_installation_bindings import (
    AccountInstallationBindingService,
    BindingChallengeUsed,
    CrossAccountBindingRejected,
    RevokedInstallationBindingRejected,
)
from automation_tool.control_plane.application.account_sessions import (
    AccountSessionRejected,
    AuthenticatedAccountSession,
)
from automation_tool.control_plane.application.device_credentials import (
    DeviceCredentialFactory,
    DeviceCredentialRejected,
)
from automation_tool.control_plane.application.device_sessions import (
    DeviceSessionCapability,
    DeviceSessionFactory,
    DeviceSessionRejected,
    DeviceSessionService,
)
from automation_tool.control_plane.domain import UserId
from automation_tool.control_plane.infrastructure.database import (
    Database,
    SqlAlchemyAccountInstallationBindingRepository,
    account_audit_events,
    account_installation_binding_challenges,
    device_credentials,
    device_sessions,
    installations,
    users,
)
from automation_tool.control_plane.infrastructure.database.device_session_repository import (
    SqlAlchemyDeviceSessionRepository,
)

NOW = datetime(2026, 7, 23, 1, 0, tzinfo=UTC)


@dataclass
class MutableClock:
    current: datetime = NOW

    def now(self) -> datetime:
        return self.current


class FixedAccountSessions:
    def __init__(self, user_id: UserId) -> None:
        self.user_id = user_id

    async def authenticate(self, *, access_token: object) -> AuthenticatedAccountSession:
        return AuthenticatedAccountSession(
            token_id=uuid4(),
            family_id=uuid4(),
            user_id=self.user_id,
            credential_version=1,
            expires_at=NOW + timedelta(minutes=10),
        )


def binding_service(
    database: Database, user_id: UserId, clock: MutableClock
) -> AccountInstallationBindingService:
    return AccountInstallationBindingService(
        repository=SqlAlchemyAccountInstallationBindingRepository(database),
        account_sessions=FixedAccountSessions(user_id),
        clock=clock,
        credential_factory=DeviceCredentialFactory(
            secret_source=secrets.token_bytes,
            id_source=uuid4,
        ),
    )


async def seed_user(database: Database, login_name: str) -> UserId:
    user_id = UserId.new()
    async with database.session() as session:
        await session.execute(
            insert(users).values(
                id=user_id.uuid,
                login_name=login_name,
                created_at=NOW,
                updated_at=NOW,
            )
        )
    return user_id


async def cleanup_users(
    database: Database,
    user_ids: tuple[UserId, ...],
    additional_installation_ids: tuple[UUID, ...] = (),
) -> None:
    raw_ids = tuple(user_id.uuid for user_id in user_ids)
    async with database.session() as session:
        owned_installation_ids = tuple(
            await session.scalars(
                select(installations.c.id).where(installations.c.owner_user_id.in_(raw_ids))
            )
        )
        installation_ids = tuple(set(owned_installation_ids + additional_installation_ids))
        await session.execute(
            delete(account_installation_binding_challenges).where(
                account_installation_binding_challenges.c.user_id.in_(raw_ids)
            )
        )
        if installation_ids:
            await session.execute(
                delete(device_sessions).where(
                    device_sessions.c.installation_id.in_(installation_ids)
                )
            )
            await session.execute(
                delete(device_credentials).where(
                    device_credentials.c.installation_id.in_(installation_ids)
                )
            )
            await session.execute(
                delete(installations).where(installations.c.id.in_(installation_ids))
            )


async def bind(
    service: AccountInstallationBindingService,
    key: Ed25519PrivateKey,
    request_id: str,
):
    challenge = await service.issue_challenge(
        access_token="atas1.private",
        device_public_key=key.public_key().public_bytes_raw(),
    )
    return await service.complete_binding(
        access_token="atas1.private",
        challenge_id=challenge.challenge_id,
        signing_payload=challenge.signing_payload,
        signature=key.sign(challenge.signing_payload),
        request_id=request_id,
    )


@pytest.mark.asyncio
async def test_binding_migration_is_reversible_and_owner_cannot_be_reassigned(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    first = await seed_user(database, f"owner-{uuid4().hex}")
    second = await seed_user(database, f"owner-{uuid4().hex}")
    installation_id = uuid4()
    try:
        async with database.session() as session:
            revision = await session.scalar(text("select version_num from alembic_version"))
            columns = set(
                await session.scalars(
                    text(
                        "select column_name from information_schema.columns "
                        "where table_name = 'account_installation_binding_challenges'"
                    )
                )
            )
            await session.execute(
                insert(installations).values(
                    id=installation_id,
                    device_public_key=secrets.token_bytes(32),
                    owner_user_id=first.uuid,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        assert revision == "20260723_0030"
        assert columns == {
            "id",
            "user_id",
            "device_public_key",
            "proof_hash",
            "expires_at",
            "consumed_at",
            "installation_id",
            "created_at",
        }
        with pytest.raises(IntegrityError):
            async with database.session() as session:
                await session.execute(
                    update(installations)
                    .where(installations.c.id == installation_id)
                    .values(owner_user_id=second.uuid)
                )

        alembic_runner(postgresql_url, "downgrade", "20260722_0029")
        async with database.session() as session:
            removed = await session.scalar(
                text("select to_regclass('public.account_installation_binding_challenges')")
            )
            owner_column = await session.scalar(
                text(
                    "select column_name from information_schema.columns "
                    "where table_name = 'installations' and column_name = 'owner_user_id'"
                )
            )
        assert removed is None
        assert owner_column is None
    finally:
        await database.close()
        alembic_runner(postgresql_url, "upgrade", "head")
        cleanup_database = Database.from_url(postgresql_url)
        try:
            await cleanup_users(
                cleanup_database,
                (first, second),
                (installation_id,),
            )
        finally:
            await cleanup_database.close()


@pytest.mark.asyncio
async def test_binding_rotates_credentials_and_rejects_replay_cross_account_and_revoked(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    first = await seed_user(database, f"bind-{uuid4().hex}")
    second = await seed_user(database, f"bind-{uuid4().hex}")
    clock = MutableClock()
    first_service = binding_service(database, first, clock)
    second_service = binding_service(database, second, clock)
    device_key = Ed25519PrivateKey.generate()
    try:
        challenge = await first_service.issue_challenge(
            access_token="atas1.private",
            device_public_key=device_key.public_key().public_bytes_raw(),
        )
        initial = await first_service.complete_binding(
            access_token="atas1.private",
            challenge_id=challenge.challenge_id,
            signing_payload=challenge.signing_payload,
            signature=device_key.sign(challenge.signing_payload),
            request_id="first-bind",
        )
        with pytest.raises(BindingChallengeUsed):
            await first_service.complete_binding(
                access_token="atas1.private",
                challenge_id=challenge.challenge_id,
                signing_payload=challenge.signing_payload,
                signature=device_key.sign(challenge.signing_payload),
                request_id="replay-bind",
            )

        attacker_challenge = await second_service.issue_challenge(
            access_token="atas1.private",
            device_public_key=device_key.public_key().public_bytes_raw(),
        )
        with pytest.raises(CrossAccountBindingRejected):
            await second_service.complete_binding(
                access_token="atas1.private",
                challenge_id=attacker_challenge.challenge_id,
                signing_payload=attacker_challenge.signing_payload,
                signature=device_key.sign(attacker_challenge.signing_payload),
                request_id="cross-account",
            )

        clock.current += timedelta(seconds=1)
        rotated = await bind(first_service, device_key, "same-owner-rotation")
        assert rotated.installation_id == initial.installation_id
        assert rotated.device_credential.version == 2
        assert rotated.device_credential.credential != initial.device_credential.credential

        async with database.session() as session:
            installation = (
                (
                    await session.execute(
                        select(installations).where(installations.c.id == initial.installation_id)
                    )
                )
                .mappings()
                .one()
            )
            credentials = (
                (
                    await session.execute(
                        select(device_credentials)
                        .where(device_credentials.c.installation_id == initial.installation_id)
                        .order_by(device_credentials.c.version)
                    )
                )
                .mappings()
                .all()
            )
            audit_types = set(
                await session.scalars(
                    select(account_audit_events.c.event_type).where(
                        account_audit_events.c.subject_user_id == first.uuid
                    )
                )
            )
        assert installation["owner_user_id"] == first.uuid
        assert [row["status"] for row in credentials] == ["rotated", "active"]
        secret_segment = rotated.device_credential.credential.rsplit(".", 1)[1]
        secret = base64.urlsafe_b64decode(secret_segment + ("=" * (-len(secret_segment) % 4)))
        assert credentials[1]["secret_digest"] == hashlib.sha256(secret).digest()
        assert "device.bound" in audit_types

        revoked_key = Ed25519PrivateKey.generate()
        revoked_id = uuid4()
        async with database.session() as session:
            await session.execute(
                insert(installations).values(
                    id=revoked_id,
                    device_public_key=revoked_key.public_key().public_bytes_raw(),
                    owner_user_id=first.uuid,
                    status="revoked",
                    revision=2,
                    created_at=NOW,
                    updated_at=NOW,
                    revoked_at=NOW,
                )
            )
        revoked_challenge = await first_service.issue_challenge(
            access_token="atas1.private",
            device_public_key=revoked_key.public_key().public_bytes_raw(),
        )
        with pytest.raises(RevokedInstallationBindingRejected):
            await first_service.complete_binding(
                access_token="atas1.private",
                challenge_id=revoked_challenge.challenge_id,
                signing_payload=revoked_challenge.signing_payload,
                signature=revoked_key.sign(revoked_challenge.signing_payload),
                request_id="revoked-device",
            )
    finally:
        await cleanup_users(database, (first, second))
        await database.close()


@pytest.mark.asyncio
async def test_pending_challenge_has_one_concurrent_winner_and_disabled_account_cannot_bind(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    user_id = await seed_user(database, f"concurrent-{uuid4().hex}")
    service = binding_service(database, user_id, MutableClock())
    key = Ed25519PrivateKey.generate()
    try:
        results = await asyncio.gather(
            service.issue_challenge(
                access_token="atas1.private",
                device_public_key=key.public_key().public_bytes_raw(),
            ),
            service.issue_challenge(
                access_token="atas1.private",
                device_public_key=key.public_key().public_bytes_raw(),
            ),
            return_exceptions=True,
        )
        assert sum(not isinstance(item, Exception) for item in results) == 1
        assert sum(isinstance(item, BindingChallengeUsed) for item in results) == 1

        winner = next(item for item in results if not isinstance(item, Exception))
        async with database.session() as session:
            await session.execute(
                update(users)
                .where(users.c.id == user_id.uuid)
                .values(
                    status="disabled",
                    revision=users.c.revision + 1,
                    updated_at=NOW + timedelta(seconds=1),
                    disabled_at=NOW + timedelta(seconds=1),
                )
            )
        with pytest.raises(AccountSessionRejected):
            await service.complete_binding(
                access_token="atas1.private",
                challenge_id=winner.challenge_id,
                signing_payload=winner.signing_payload,
                signature=key.sign(winner.signing_payload),
                request_id="disabled-account",
            )
        async with database.session() as session:
            consumed = await session.scalar(
                select(account_installation_binding_challenges.c.consumed_at).where(
                    account_installation_binding_challenges.c.id == winner.challenge_id
                )
            )
        assert consumed is None
    finally:
        await cleanup_users(database, (user_id,))
        await database.close()


@pytest.mark.asyncio
async def test_account_disable_invalidates_owned_device_exchange_and_live_session(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    user_id = await seed_user(database, f"disable-{uuid4().hex}")
    clock = MutableClock()
    device_key = Ed25519PrivateKey.generate()
    try:
        bound = await bind(
            binding_service(database, user_id, clock), device_key, "bind-before-disable"
        )
        sessions = DeviceSessionService(
            repository=SqlAlchemyDeviceSessionRepository(database),
            clock=clock,
            session_factory=DeviceSessionFactory(
                secret_source=secrets.token_bytes,
                id_source=uuid4,
            ),
        )
        issued = await sessions.exchange(
            device_credential=bound.device_credential.credential,
            capability=DeviceSessionCapability.APP_CONTROL_PLANE,
        )
        clock.current += timedelta(seconds=1)
        async with database.session() as session:
            await session.execute(
                update(users)
                .where(users.c.id == user_id.uuid)
                .values(
                    status="disabled",
                    revision=users.c.revision + 1,
                    updated_at=clock.current,
                    disabled_at=clock.current,
                )
            )
        with pytest.raises(DeviceSessionRejected):
            await sessions.authenticate(
                session_token=issued.session_token,
                required_capability=DeviceSessionCapability.APP_CONTROL_PLANE,
            )
        with pytest.raises(DeviceCredentialRejected):
            await sessions.exchange(
                device_credential=bound.device_credential.credential,
                capability=DeviceSessionCapability.APP_CONTROL_PLANE,
            )
    finally:
        await cleanup_users(database, (user_id,))
        await database.close()
