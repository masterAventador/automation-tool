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
    IssuedDeviceCredential,
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

NOW = datetime(2026, 7, 18, 9, 0, tzinfo=UTC)


@dataclass
class FixedClock:
    current: datetime = NOW

    def now(self) -> datetime:
        return self.current


def credential_factory() -> DeviceCredentialFactory:
    return DeviceCredentialFactory(secret_source=secrets.token_bytes, id_source=uuid4)


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


def manager(
    database: Database, *, at: datetime = NOW + timedelta(minutes=1)
) -> DeviceCredentialService:
    return DeviceCredentialService(
        repository=SqlAlchemyDeviceCredentialRepository(database),
        clock=FixedClock(at),
        credential_factory=credential_factory(),
    )


@pytest.mark.asyncio
async def test_rotation_atomically_replaces_the_active_version_and_persists_only_digest(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        current, installation_id = await seed_active_credential(database)

        issued = await manager(database).rotate(current)

        assert issued.installation_id == installation_id.uuid
        assert issued.version == 2
        assert issued.scope == DEVICE_CREDENTIAL_SCOPE
        assert issued.credential.startswith(f"atdc1.{issued.credential_id}.")
        replacement_secret = issued.credential.rsplit(".", maxsplit=1)[1]
        decoded_secret = base64.urlsafe_b64decode(replacement_secret + "=")
        async with database.session() as session:
            rows = (
                (
                    await session.execute(
                        select(device_credentials)
                        .where(device_credentials.c.installation_id == installation_id.uuid)
                        .order_by(device_credentials.c.version)
                    )
                )
                .mappings()
                .all()
            )
        assert len(rows) == 2
        assert rows[0]["status"] == "rotated"
        assert rows[0]["revoked_at"] == NOW + timedelta(minutes=1)
        assert rows[0]["replaced_by_id"] == issued.credential_id
        assert rows[1]["status"] == "active"
        assert rows[1]["version"] == 2
        assert rows[1]["secret_digest"] == hashlib.sha256(decoded_secret).digest()
        assert issued.credential not in repr(rows)
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_old_wrong_and_revoked_credentials_are_uniformly_rejected(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        current, _ = await seed_active_credential(database)
        lifecycle = manager(database)
        rotated = await lifecycle.rotate(current)
        unknown = credential_factory().create().credential
        wrong_secret = (
            f"{current.rsplit('.', maxsplit=1)[0]}."
            f"{base64.urlsafe_b64encode(b'x' * 32).rstrip(b'=').decode('ascii')}"
        )

        for rejected in (current, wrong_secret, unknown):
            with pytest.raises(DeviceCredentialRejected) as captured:
                await lifecycle.revoke(rejected)
            assert str(captured.value) == "Device credential is rejected"
            assert rejected not in str(captured.value)

        revoked = await lifecycle.revoke(rotated.credential)
        assert revoked.credential_id == rotated.credential_id
        assert revoked.version == 2
        assert revoked.status == "revoked"
        with pytest.raises(DeviceCredentialRejected):
            await lifecycle.rotate(rotated.credential)
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_revoked_installation_cannot_rotate_or_revoke_its_credential(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        current, installation_id = await seed_active_credential(database)
        revoked_at = NOW + timedelta(seconds=1)
        async with database.session() as session:
            await session.execute(
                update(installations)
                .where(installations.c.id == installation_id.uuid)
                .values(
                    status=InstallationStatus.REVOKED.value,
                    revision=2,
                    updated_at=revoked_at,
                    revoked_at=revoked_at,
                )
            )

        for operation in (manager(database).rotate, manager(database).revoke):
            with pytest.raises(DeviceCredentialRejected):
                await operation(current)
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_two_concurrent_rotations_allow_exactly_one_success(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        current, installation_id = await seed_active_credential(database)
        results = await asyncio.gather(
            manager(database).rotate(current),
            manager(database).rotate(current),
            return_exceptions=True,
        )

        assert sum(isinstance(result, IssuedDeviceCredential) for result in results) == 1
        assert sum(isinstance(result, DeviceCredentialRejected) for result in results) == 1
        async with database.session() as session:
            states = list(
                await session.scalars(
                    select(device_credentials.c.status)
                    .where(device_credentials.c.installation_id == installation_id.uuid)
                    .order_by(device_credentials.c.version)
                )
            )
        assert states == ["rotated", "active"]
    finally:
        await database.close()


def test_real_http_lifecycle_rejects_every_superseded_or_revoked_version(
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
    app = create_app(
        database=app_database,
        device_credential_service=manager(app_database),
    )

    with TestClient(app) as client:
        rotated = client.post(
            "/api/v1/device-credentials/rotations",
            headers={"authorization": f"Bearer {current}"},
        )
        assert rotated.status_code == 201
        replacement = rotated.json()["credential"]
        old_version = client.post(
            "/api/v1/device-credentials/revocations",
            headers={"authorization": f"Bearer {current}"},
        )
        revoked = client.post(
            "/api/v1/device-credentials/revocations",
            headers={"authorization": f"Bearer {replacement}"},
        )
        revoked_version = client.post(
            "/api/v1/device-credentials/rotations",
            headers={"authorization": f"Bearer {replacement}"},
        )

    for rejected in (old_version, revoked_version):
        assert rejected.status_code == 401
        assert rejected.json()["error"]["code"] == "device_credential_invalid"
        assert current not in rejected.text
        assert replacement not in rejected.text
    assert revoked.status_code == 200
    assert revoked.json() == {"status": "revoked", "version": 2}
