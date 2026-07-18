from __future__ import annotations

import asyncio
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from conftest import AlembicRunner
from fastapi.testclient import TestClient
from sqlalchemy import delete, insert
from starlette.testclient import WebSocketDenialResponse
from starlette.websockets import WebSocketDisconnect

from automation_tool.control_plane import create_app
from automation_tool.control_plane.api.executor_websocket import (
    EXECUTOR_CLOSE_AUTHENTICATION_REJECTED,
)
from automation_tool.control_plane.application.device_credentials import (
    DEVICE_CREDENTIAL_SCOPE,
    DeviceCredentialFactory,
)
from automation_tool.control_plane.application.device_sessions import (
    DeviceSessionCapability,
    DeviceSessionFactory,
    DeviceSessionService,
)
from automation_tool.control_plane.application.executor_connections import (
    EXECUTOR_WEBSOCKET_SUBPROTOCOL,
)
from automation_tool.control_plane.domain import InstallationId
from automation_tool.control_plane.infrastructure.database import (
    Database,
    device_credentials,
    device_sessions,
    installation_registration_challenges,
    installations,
)
from automation_tool.control_plane.infrastructure.database.device_session_repository import (
    SqlAlchemyDeviceSessionRepository,
)

NOW = datetime(2020, 1, 1, 12, 0, tzinfo=UTC)
EXECUTOR_ID = UUID("123e4567-e89b-42d3-a456-426614174004")


@dataclass
class FixedClock:
    def now(self) -> datetime:
        return NOW


async def reset_data(database: Database) -> None:
    async with database.session() as session:
        await session.execute(delete(installation_registration_challenges))
        await session.execute(delete(device_sessions))
        await session.execute(delete(device_credentials))
        await session.execute(delete(installations))


async def seed_active_credential(database: Database) -> tuple[str, InstallationId]:
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


def executor_hello(installation_id: InstallationId) -> str:
    return json.dumps(
        {
            "protocol_version": "1.0",
            "message_id": "123e4567-e89b-42d3-a456-426614174001",
            "message_type": "executor.hello",
            "sent_at": "2020-01-01T12:00:00Z",
            "deadline_at": "2020-01-01T12:00:30Z",
            "installation_id": str(installation_id),
            "executor_id": str(EXECUTOR_ID),
            "correlation_id": "123e4567-e89b-42d3-a456-426614174002",
            "idempotency_key": "executor:hello:1",
            "sequence": 1,
            "payload": {
                "architecture": "arm64",
                "executor_version": "0.1.0",
                "platform": "macos",
            },
        },
        separators=(",", ":"),
    )


def test_real_postgresql_revocation_closes_live_websocket_and_rejects_reconnect(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")

    async def prepare() -> tuple[str, InstallationId]:
        database = Database.from_url(postgresql_url)
        try:
            await reset_data(database)
            return await seed_active_credential(database)
        finally:
            await database.close()

    credential, installation_id = asyncio.run(prepare())
    app_database = Database.from_url(postgresql_url)
    sessions = DeviceSessionService(
        repository=SqlAlchemyDeviceSessionRepository(app_database),
        clock=FixedClock(),
        session_factory=DeviceSessionFactory(
            secret_source=secrets.token_bytes,
            id_source=uuid4,
        ),
    )
    app = create_app(
        database=app_database,
        device_session_service=sessions,
        executor_connection_recheck_interval_seconds=0.01,
    )

    try:
        with TestClient(app) as client:
            exchanged = client.post(
                "/api/v1/device-sessions",
                headers={"authorization": f"Bearer {credential}"},
                json={"capability": DeviceSessionCapability.EXECUTOR_CONNECT.value},
            )
            assert exchanged.status_code == 201
            session_token = exchanged.json()["sessionToken"]
            headers = {"authorization": f"Bearer {session_token}"}
            with client.websocket_connect(
                "/api/v1/executors/connect",
                headers=headers,
                subprotocols=[EXECUTOR_WEBSOCKET_SUBPROTOCOL],
            ) as websocket:
                websocket.send_text(executor_hello(installation_id))
                revoked = client.post(
                    "/api/v1/device-credentials/revocations",
                    headers={"authorization": f"Bearer {credential}"},
                )
                assert revoked.status_code == 200
                with pytest.raises(WebSocketDisconnect) as captured:
                    websocket.receive_text()

            assert captured.value.code == EXECUTOR_CLOSE_AUTHENTICATION_REJECTED
            with (
                pytest.raises(WebSocketDenialResponse) as reconnect,
                client.websocket_connect(
                    "/api/v1/executors/connect",
                    headers=headers,
                    subprotocols=[EXECUTOR_WEBSOCKET_SUBPROTOCOL],
                ),
            ):
                pass
            assert reconnect.value.status_code == 403
    finally:
        asyncio.run(app_database.close())
