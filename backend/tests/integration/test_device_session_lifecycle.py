import asyncio
import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from conftest import AlembicRunner
from fastapi.testclient import TestClient
from sqlalchemy import delete, insert, select, update

from automation_tool.control_plane import create_app
from automation_tool.control_plane.application.device_credentials import (
    DEVICE_CREDENTIAL_SCOPE,
    DeviceCredentialFactory,
    DeviceCredentialRejected,
    DeviceCredentialService,
)
from automation_tool.control_plane.application.device_sessions import (
    DEVICE_SESSION_CLOCK_SKEW,
    DeviceSessionCapability,
    DeviceSessionFactory,
    DeviceSessionRejected,
    DeviceSessionService,
)
from automation_tool.control_plane.domain import InstallationId, InstallationStatus
from automation_tool.control_plane.infrastructure.database import (
    Database,
    device_credentials,
    device_sessions,
    installation_registration_challenges,
    installations,
)
from automation_tool.control_plane.infrastructure.database.device_credential_repository import (
    SqlAlchemyDeviceCredentialRepository,
)
from automation_tool.control_plane.infrastructure.database.device_session_repository import (
    SqlAlchemyDeviceSessionRepository,
)

NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)


@dataclass
class MutableClock:
    current: datetime = NOW

    def now(self) -> datetime:
        return self.current


def credential_factory() -> DeviceCredentialFactory:
    return DeviceCredentialFactory(secret_source=secrets.token_bytes, id_source=uuid4)


def session_factory() -> DeviceSessionFactory:
    return DeviceSessionFactory(secret_source=secrets.token_bytes, id_source=uuid4)


async def reset_data(database: Database) -> None:
    async with database.session() as session:
        await session.execute(delete(installation_registration_challenges))
        await session.execute(delete(device_sessions))
        await session.execute(delete(device_credentials))
        await session.execute(delete(installations))


async def seed_active_credential(database: Database) -> tuple[str, InstallationId]:
    installation_id = InstallationId.new()
    pending = credential_factory().create()
    async with database.session() as session:
        await session.execute(
            insert(installations).values(
                id=installation_id.uuid,
                device_public_key=secrets.token_bytes(32),
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.execute(
            insert(device_credentials).values(
                id=pending.credential_id,
                installation_id=installation_id.uuid,
                version=1,
                scope=DEVICE_CREDENTIAL_SCOPE,
                secret_digest=pending.secret_digest,
                status="active",
                created_at=NOW,
                updated_at=NOW,
            )
        )
    return pending.credential, installation_id


def session_service(database: Database, clock: MutableClock) -> DeviceSessionService:
    return DeviceSessionService(
        repository=SqlAlchemyDeviceSessionRepository(database, require_installation_owner=False),
        clock=clock,
        session_factory=session_factory(),
    )


def credential_service(database: Database, clock: MutableClock) -> DeviceCredentialService:
    return DeviceCredentialService(
        repository=SqlAlchemyDeviceCredentialRepository(database),
        clock=clock,
        credential_factory=credential_factory(),
    )


@pytest.mark.asyncio
async def test_exchange_persists_only_digest_and_authenticates_exact_capability(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        credential, installation_id = await seed_active_credential(database)
        clock = MutableClock()
        service = session_service(database, clock)

        issued = await service.exchange(
            device_credential=credential,
            capability=DeviceSessionCapability.APP_CONTROL_PLANE,
        )
        secret = issued.session_token.rsplit(".", maxsplit=1)[1]
        decoded_secret = base64.urlsafe_b64decode(secret + "=")
        async with database.session() as session:
            row = (
                (
                    await session.execute(
                        select(device_sessions).where(device_sessions.c.id == issued.session_id)
                    )
                )
                .mappings()
                .one()
            )

        assert issued.installation_id == installation_id.uuid
        assert issued.credential_version == 1
        assert row["installation_id"] == installation_id.uuid
        assert row["device_credential_id"] == issued.credential_id
        assert row["credential_version"] == 1
        assert row["secret_digest"] == hashlib.sha256(decoded_secret).digest()
        assert issued.session_token not in repr(row)
        authenticated = await service.authenticate(
            session_token=issued.session_token,
            required_capability=DeviceSessionCapability.APP_CONTROL_PLANE,
        )
        assert authenticated.session_id == issued.session_id
        assert authenticated.capability is DeviceSessionCapability.APP_CONTROL_PLANE

        with pytest.raises(DeviceSessionRejected):
            await service.authenticate(
                session_token=issued.session_token,
                required_capability=DeviceSessionCapability.EXECUTOR_CONNECT,
            )
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_authentication_uses_inclusive_not_before_and_exclusive_expiry(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        credential, _ = await seed_active_credential(database)
        clock = MutableClock()
        service = session_service(database, clock)
        issued = await service.exchange(
            device_credential=credential,
            capability=DeviceSessionCapability.EXECUTOR_CONNECT,
        )

        for accepted_at in (
            issued.issued_at - DEVICE_SESSION_CLOCK_SKEW,
            issued.expires_at - timedelta(microseconds=1),
        ):
            clock.current = accepted_at
            authenticated = await service.authenticate(
                session_token=issued.session_token,
                required_capability=DeviceSessionCapability.EXECUTOR_CONNECT,
            )
            assert authenticated.session_id == issued.session_id

        for rejected_at in (
            issued.not_before - timedelta(microseconds=1),
            issued.expires_at,
        ):
            clock.current = rejected_at
            with pytest.raises(DeviceSessionRejected):
                await service.authenticate(
                    session_token=issued.session_token,
                    required_capability=DeviceSessionCapability.EXECUTOR_CONNECT,
                )
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_unknown_wrong_secret_and_wrong_capability_are_uniformly_rejected(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        credential, _ = await seed_active_credential(database)
        clock = MutableClock()
        service = session_service(database, clock)
        issued = await service.exchange(
            device_credential=credential,
            capability=DeviceSessionCapability.APP_CONTROL_PLANE,
        )
        unknown = session_factory().create().session_token
        wrong_secret = (
            f"{issued.session_token.rsplit('.', maxsplit=1)[0]}."
            f"{base64.urlsafe_b64encode(b'x' * 32).rstrip(b'=').decode('ascii')}"
        )

        rejected_presentations = (
            (unknown, DeviceSessionCapability.APP_CONTROL_PLANE),
            (wrong_secret, DeviceSessionCapability.APP_CONTROL_PLANE),
            (issued.session_token, DeviceSessionCapability.EXECUTOR_CONNECT),
        )
        for token, capability in rejected_presentations:
            with pytest.raises(DeviceSessionRejected) as captured:
                await service.authenticate(
                    session_token=token,
                    required_capability=capability,
                )
            assert str(captured.value) == "Device session is rejected"
            assert token not in str(captured.value)
    finally:
        await database.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["rotate", "revoke"])
async def test_parent_credential_change_immediately_revokes_existing_sessions(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
    operation: str,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        credential, _ = await seed_active_credential(database)
        clock = MutableClock()
        sessions = session_service(database, clock)
        issued = await sessions.exchange(
            device_credential=credential,
            capability=DeviceSessionCapability.APP_CONTROL_PLANE,
        )
        clock.current = NOW + timedelta(seconds=1)
        lifecycle = credential_service(database, clock)

        await getattr(lifecycle, operation)(credential)

        with pytest.raises(DeviceSessionRejected):
            await sessions.authenticate(
                session_token=issued.session_token,
                required_capability=DeviceSessionCapability.APP_CONTROL_PLANE,
            )
        async with database.session() as session:
            revoked_at = await session.scalar(
                select(device_sessions.c.revoked_at).where(
                    device_sessions.c.id == issued.session_id
                )
            )
        assert revoked_at == clock.current
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_revoked_installation_rejects_exchange_and_existing_session(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        credential, installation_id = await seed_active_credential(database)
        clock = MutableClock()
        service = session_service(database, clock)
        issued = await service.exchange(
            device_credential=credential,
            capability=DeviceSessionCapability.APP_CONTROL_PLANE,
        )
        clock.current = NOW + timedelta(seconds=1)
        async with database.session() as session:
            await session.execute(
                update(installations)
                .where(installations.c.id == installation_id.uuid)
                .values(
                    status=InstallationStatus.REVOKED.value,
                    revision=2,
                    updated_at=clock.current,
                    revoked_at=clock.current,
                )
            )

        with pytest.raises(DeviceSessionRejected):
            await service.authenticate(
                session_token=issued.session_token,
                required_capability=DeviceSessionCapability.APP_CONTROL_PLANE,
            )
        with pytest.raises(DeviceCredentialRejected):
            await service.exchange(
                device_credential=credential,
                capability=DeviceSessionCapability.APP_CONTROL_PLANE,
            )
    finally:
        await database.close()


def test_real_http_exchange_returns_a_session_accepted_by_the_repository(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")

    async def prepare() -> str:
        seed_database = Database.from_url(postgresql_url)
        try:
            await reset_data(seed_database)
            current, _ = await seed_active_credential(seed_database)
            return current
        finally:
            await seed_database.close()

    current = asyncio.run(prepare())
    app_database = Database.from_url(postgresql_url)
    clock = MutableClock()
    sessions = session_service(app_database, clock)
    app = create_app(
        database=app_database,
        device_session_service=sessions,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/device-sessions",
            headers={"authorization": f"Bearer {current}"},
            json={"capability": "executor.connect"},
        )

    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    authenticated = asyncio.run(
        sessions.authenticate(
            session_token=response.json()["sessionToken"],
            required_capability=DeviceSessionCapability.EXECUTOR_CONNECT,
        )
    )
    assert authenticated.capability is DeviceSessionCapability.EXECUTOR_CONNECT
