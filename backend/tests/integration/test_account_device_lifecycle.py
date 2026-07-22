import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from conftest import AlembicRunner
from sqlalchemy import delete, insert, select

from automation_tool.control_plane.application.account_devices import (
    AccountDeviceRevocationRejected,
    AccountDeviceService,
)
from automation_tool.control_plane.application.account_sessions import AuthenticatedAccountSession
from automation_tool.control_plane.domain import InstallationId, UserId
from automation_tool.control_plane.infrastructure.database import (
    Database,
    SqlAlchemyAccountDeviceRepository,
    account_audit_events,
    device_credentials,
    device_sessions,
    installations,
    users,
)

NOW = datetime(2026, 7, 23, 2, 20, tzinfo=UTC)


@dataclass
class Clock:
    current: datetime = NOW

    def now(self) -> datetime:
        return self.current


class Sessions:
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


async def seed_installation(database: Database, user_id: UserId) -> InstallationId:
    installation_id = InstallationId.new()
    credential_id = uuid4()
    async with database.session() as session:
        await session.execute(
            insert(installations).values(
                id=installation_id.uuid,
                device_public_key=secrets.token_bytes(32),
                owner_user_id=user_id.uuid,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.execute(
            insert(device_credentials).values(
                id=credential_id,
                installation_id=installation_id.uuid,
                version=1,
                scope="device.session.exchange",
                secret_digest=secrets.token_bytes(32),
                status="active",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.execute(
            insert(device_sessions).values(
                id=uuid4(),
                installation_id=installation_id.uuid,
                device_credential_id=credential_id,
                credential_version=1,
                capability="app.control-plane",
                secret_digest=secrets.token_bytes(32),
                created_at=NOW,
                not_before=NOW,
                expires_at=NOW + timedelta(minutes=5),
            )
        )
    return installation_id


@pytest.mark.asyncio
async def test_current_account_lists_and_revokes_only_one_owned_device(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    first = await seed_user(database, f"devices-{uuid4().hex}")
    second = await seed_user(database, f"devices-{uuid4().hex}")
    target = await seed_installation(database, first)
    preserved = await seed_installation(database, first)
    foreign = await seed_installation(database, second)
    service = AccountDeviceService(
        repository=SqlAlchemyAccountDeviceRepository(database),
        account_sessions=Sessions(first),
        clock=Clock(),
    )
    try:
        listed = await service.list_devices(access_token="atas1.private")
        assert {item.installation_id for item in listed} == {target, preserved}

        revoked = await service.revoke_device(
            access_token="atas1.private",
            installation_id=target,
            expected_revision=1,
            request_id="user-revoke-device",
        )
        assert revoked.status.value == "revoked"
        assert revoked.revision == 2
        with pytest.raises(AccountDeviceRevocationRejected):
            await service.revoke_device(
                access_token="atas1.private",
                installation_id=foreign,
                expected_revision=1,
                request_id="cross-account-revoke",
            )

        async with database.session() as session:
            installation_rows = (
                await session.execute(
                    select(
                        installations.c.id,
                        installations.c.status,
                        installations.c.revision,
                    ).where(installations.c.id.in_((target.uuid, preserved.uuid, foreign.uuid)))
                )
            ).all()
            target_credential = await session.scalar(
                select(device_credentials.c.status).where(
                    device_credentials.c.installation_id == target.uuid
                )
            )
            target_session_revoked = await session.scalar(
                select(device_sessions.c.revoked_at).where(
                    device_sessions.c.installation_id == target.uuid
                )
            )
            audit = (
                await session.execute(
                    select(
                        account_audit_events.c.event_type,
                        account_audit_events.c.actor_kind,
                        account_audit_events.c.actor_id,
                        account_audit_events.c.subject_user_id,
                        account_audit_events.c.reason_code,
                    ).where(account_audit_events.c.request_id == "user-revoke-device")
                )
            ).one()
        states = {row.id: (row.status, row.revision) for row in installation_rows}
        assert states[target.uuid] == ("revoked", 2)
        assert states[preserved.uuid] == ("active", 1)
        assert states[foreign.uuid] == ("active", 1)
        assert target_credential == "revoked"
        assert target_session_revoked == NOW
        assert audit == ("device.revoked", "user", first.uuid, first.uuid, "user_device_revoked")
    finally:
        ids = (target.uuid, preserved.uuid, foreign.uuid)
        async with database.session() as session:
            await session.execute(
                delete(device_sessions).where(device_sessions.c.installation_id.in_(ids))
            )
            await session.execute(
                delete(device_credentials).where(device_credentials.c.installation_id.in_(ids))
            )
            await session.execute(delete(installations).where(installations.c.id.in_(ids)))
        await database.close()
