from __future__ import annotations

import asyncio
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from conftest import AlembicRunner
from fastapi.testclient import TestClient
from sqlalchemy import delete, insert, select
from starlette.websockets import WebSocketDisconnect

from automation_tool.control_plane import create_app
from automation_tool.control_plane.api.executor_websocket import (
    EXECUTOR_CLOSE_AUTHENTICATION_REJECTED,
)
from automation_tool.control_plane.application.device_credentials import (
    DEVICE_CREDENTIAL_SCOPE,
    DeviceCredentialFactory,
    DeviceCredentialRejected,
)
from automation_tool.control_plane.application.device_sessions import (
    DeviceSessionCapability,
    DeviceSessionFactory,
    DeviceSessionRejected,
    DeviceSessionService,
)
from automation_tool.control_plane.application.executor_connections import (
    EXECUTOR_WEBSOCKET_SUBPROTOCOL,
)
from automation_tool.control_plane.application.installation_revocations import (
    InstallationRevocationRejected,
    InstallationRevocationService,
)
from automation_tool.control_plane.domain import InstallationId
from automation_tool.control_plane.infrastructure.database import (
    Database,
    SqlAlchemyInstallationRevocationRepository,
    device_credentials,
    device_sessions,
    installation_registration_challenges,
    installations,
)
from automation_tool.control_plane.infrastructure.database.device_session_repository import (
    SqlAlchemyDeviceSessionRepository,
)

NOW = datetime(2020, 1, 1, 13, 0, tzinfo=UTC)
REVOKED_AT = NOW + timedelta(seconds=1)
EXECUTOR_ID = UUID("123e4567-e89b-42d3-a456-426614174004")


@dataclass
class FixedClock:
    value: datetime = NOW

    def now(self) -> datetime:
        return self.value


async def reset_data(database: Database) -> None:
    async with database.session() as session:
        await session.execute(delete(installation_registration_challenges))
        await session.execute(delete(device_sessions))
        await session.execute(delete(device_credentials))
        await session.execute(delete(installations))


async def seed_credential(database: Database) -> tuple[str, InstallationId]:
    installation_id = InstallationId.new()
    pending = DeviceCredentialFactory(
        secret_source=secrets.token_bytes,
        id_source=uuid4,
    ).create()
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


def session_service(database: Database) -> DeviceSessionService:
    return DeviceSessionService(
        repository=SqlAlchemyDeviceSessionRepository(database, require_installation_owner=False),
        clock=FixedClock(),
        session_factory=DeviceSessionFactory(
            secret_source=secrets.token_bytes,
            id_source=uuid4,
        ),
    )


def revocation_service(database: Database) -> InstallationRevocationService:
    return InstallationRevocationService(
        repository=SqlAlchemyInstallationRevocationRepository(database),
        clock=FixedClock(REVOKED_AT),
    )


def hello(installation_id: InstallationId) -> str:
    return json.dumps(
        {
            "protocol_version": "1.0",
            "message_id": "123e4567-e89b-42d3-a456-426614174001",
            "message_type": "executor.hello",
            "sent_at": "2020-01-01T13:00:00Z",
            "deadline_at": "2020-01-01T13:00:30Z",
            "installation_id": str(installation_id),
            "executor_id": str(EXECUTOR_ID),
            "correlation_id": "123e4567-e89b-42d3-a456-426614174002",
            "idempotency_key": "executor:hello:installation-revocation",
            "sequence": 1,
            "payload": {
                "architecture": "arm64",
                "executor_version": "0.1.0",
                "platform": "macos",
            },
        },
        separators=(",", ":"),
    )


@pytest.mark.asyncio
async def test_atomic_revocation_invalidates_all_access_and_preserves_other_installations(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        credential, installation_id = await seed_credential(database)
        other_credential, other_installation_id = await seed_credential(database)
        sessions = session_service(database)
        app_session = await sessions.exchange(
            device_credential=credential,
            capability=DeviceSessionCapability.APP_CONTROL_PLANE,
        )
        executor_session = await sessions.exchange(
            device_credential=credential,
            capability=DeviceSessionCapability.EXECUTOR_CONNECT,
        )
        other_session = await sessions.exchange(
            device_credential=other_credential,
            capability=DeviceSessionCapability.APP_CONTROL_PLANE,
        )

        revoked = await revocation_service(database).revoke(
            installation_id=installation_id,
            expected_revision=1,
        )

        assert revoked.revision == 2
        assert revoked.revoked_at == REVOKED_AT
        for issued in (app_session, executor_session):
            with pytest.raises(DeviceSessionRejected):
                await sessions.authenticate(
                    session_token=issued.session_token,
                    required_capability=issued.capability,
                )
        with pytest.raises(DeviceCredentialRejected, match="Device credential is rejected"):
            await sessions.exchange(
                device_credential=credential,
                capability=DeviceSessionCapability.APP_CONTROL_PLANE,
            )
        assert (
            await sessions.authenticate(
                session_token=other_session.session_token,
                required_capability=DeviceSessionCapability.APP_CONTROL_PLANE,
            )
        ).installation_id == other_installation_id.uuid

        async with database.session() as session:
            installation_row = (
                (
                    await session.execute(
                        select(installations).where(installations.c.id == installation_id.uuid)
                    )
                )
                .mappings()
                .one()
            )
            credential_states = (
                (
                    await session.execute(
                        select(device_credentials.c.status).where(
                            device_credentials.c.installation_id == installation_id.uuid
                        )
                    )
                )
                .scalars()
                .all()
            )
            revoked_sessions = (
                (
                    await session.execute(
                        select(device_sessions.c.revoked_at).where(
                            device_sessions.c.installation_id == installation_id.uuid
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert installation_row["status"] == "revoked"
        assert installation_row["revision"] == 2
        assert installation_row["revoked_at"] == REVOKED_AT
        assert credential_states == ["revoked"]
        assert revoked_sessions == [REVOKED_AT, REVOKED_AT]
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_stale_repeated_unknown_and_concurrent_revocations_fail_closed(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        _, installation_id = await seed_credential(database)
        service = revocation_service(database)
        with pytest.raises(InstallationRevocationRejected):
            await service.revoke(installation_id=installation_id, expected_revision=2)
        with pytest.raises(InstallationRevocationRejected):
            await service.revoke(
                installation_id=InstallationId.new(),
                expected_revision=1,
            )

        results = await asyncio.gather(
            service.revoke(installation_id=installation_id, expected_revision=1),
            service.revoke(installation_id=installation_id, expected_revision=1),
            return_exceptions=True,
        )
        assert sum(not isinstance(result, Exception) for result in results) == 1
        assert sum(isinstance(result, InstallationRevocationRejected) for result in results) == 1
        with pytest.raises(InstallationRevocationRejected):
            await service.revoke(installation_id=installation_id, expected_revision=2)
    finally:
        await database.close()


def test_real_http_and_live_websocket_close_after_operator_revocation(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")

    async def prepare() -> tuple[str, InstallationId]:
        database = Database.from_url(postgresql_url)
        try:
            await reset_data(database)
            return await seed_credential(database)
        finally:
            await database.close()

    credential, installation_id = asyncio.run(prepare())
    app_database = Database.from_url(postgresql_url)
    app = create_app(
        database=app_database,
        device_session_service=session_service(app_database),
        executor_connection_recheck_interval_seconds=0.01,
    )
    try:
        with TestClient(app) as client:
            app_exchange = client.post(
                "/api/v1/device-sessions",
                headers={"authorization": f"Bearer {credential}"},
                json={"capability": "app.control-plane"},
            )
            executor_exchange = client.post(
                "/api/v1/device-sessions",
                headers={"authorization": f"Bearer {credential}"},
                json={"capability": "executor.connect"},
            )
            assert app_exchange.status_code == executor_exchange.status_code == 201
            app_token = app_exchange.json()["sessionToken"]
            executor_token = executor_exchange.json()["sessionToken"]
            assert (
                client.get(
                    "/api/v1/installations/current",
                    headers={"authorization": f"Bearer {app_token}"},
                ).status_code
                == 200
            )

            with client.websocket_connect(
                "/api/v1/executors/connect",
                headers={"authorization": f"Bearer {executor_token}"},
                subprotocols=[EXECUTOR_WEBSOCKET_SUBPROTOCOL],
            ) as websocket:
                websocket.send_text(hello(installation_id))

                async def revoke() -> None:
                    operator_database = Database.from_url(postgresql_url)
                    try:
                        await revocation_service(operator_database).revoke(
                            installation_id=installation_id,
                            expected_revision=1,
                        )
                    finally:
                        await operator_database.close()

                asyncio.run(revoke())
                with pytest.raises(WebSocketDisconnect) as captured:
                    websocket.receive_text()

            assert captured.value.code == EXECUTOR_CLOSE_AUTHENTICATION_REJECTED
            denied = client.get(
                "/api/v1/installations/current",
                headers={"authorization": f"Bearer {app_token}"},
            )
            assert denied.status_code == 401
            assert denied.json()["error"]["code"] == "installation_access_denied"
            assert (
                client.post(
                    "/api/v1/device-sessions",
                    headers={"authorization": f"Bearer {credential}"},
                    json={"capability": "app.control-plane"},
                ).status_code
                == 401
            )
    finally:
        asyncio.run(app_database.close())
