"""Real-PostgreSQL proof that unowned Installations cannot reach business APIs.

The Control Plane runs the same code for a local single-machine deployment and
for the cloud Customer Demo; only its configuration differs. These tests drive
the production wiring (`create_app()` reading its deployment environment) rather
than hand-built services, so the deployment-derived ownership requirement is
proven where it actually takes effect.
"""

import asyncio
import base64
import os
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest
from conftest import AlembicRunner
from fastapi.testclient import TestClient
from sqlalchemy import delete, insert, update
from starlette.testclient import WebSocketDenialResponse

from automation_tool.control_plane import create_app
from automation_tool.control_plane.application.device_credentials import (
    DEVICE_CREDENTIAL_SCOPE,
    DeviceCredentialFactory,
)
from automation_tool.control_plane.application.device_sessions import DeviceSessionCapability
from automation_tool.control_plane.application.executor_connections import (
    EXECUTOR_WEBSOCKET_SUBPROTOCOL,
)
from automation_tool.control_plane.domain import AccountStatus, InstallationId, UserId
from automation_tool.control_plane.infrastructure.database import (
    Database,
    device_credentials,
    device_sessions,
    installations,
    users,
)

BUSINESS_ROUTES = ("/api/v1/installations/current", "/api/v1/workbench/status")


def encoded_secret(source: bytes) -> str:
    return base64.urlsafe_b64encode(source).rstrip(b"=").decode("ascii")


PRODUCT_ACCOUNT_ENVIRONMENT = {
    "AUTOMATION_TOOL_ACCOUNT_PASSWORD_PEPPER": encoded_secret(bytes(range(32))),
    "AUTOMATION_TOOL_ACCOUNT_PASSWORD_PEPPER_VERSION": "1",
    "AUTOMATION_TOOL_ACCOUNT_FINGERPRINT_KEY": encoded_secret(bytes(range(32, 64))),
}


@contextmanager
def control_plane(
    monkeypatch: pytest.MonkeyPatch,
    database_url: str,
    *,
    product_accounts: bool,
) -> Iterator[TestClient]:
    """Start one Control Plane from an isolated deployment environment."""
    for name in [name for name in os.environ if name.startswith("AUTOMATION_TOOL_")]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AUTOMATION_TOOL_DATABASE_URL", database_url)
    if product_accounts:
        for name, value in PRODUCT_ACCOUNT_ENVIRONMENT.items():
            monkeypatch.setenv(name, value)
    with TestClient(create_app()) as client:
        yield client


def seeded_credential(
    database_url: str,
    *,
    owner_user_id: UUID | None,
) -> tuple[str, UUID]:
    """Create one active Installation and its active long-lived credential."""

    async def seed() -> tuple[str, UUID]:
        now = datetime.now(UTC)
        installation_id = InstallationId.new().uuid
        pending = DeviceCredentialFactory(
            secret_source=secrets.token_bytes,
            id_source=uuid4,
        ).create()
        database = Database.from_url(database_url)
        try:
            async with database.session() as session:
                await session.execute(
                    insert(installations).values(
                        id=installation_id,
                        device_public_key=secrets.token_bytes(32),
                        owner_user_id=owner_user_id,
                        created_at=now,
                        updated_at=now,
                    )
                )
                await session.execute(
                    insert(device_credentials).values(
                        id=pending.credential_id,
                        installation_id=installation_id,
                        version=1,
                        scope=DEVICE_CREDENTIAL_SCOPE,
                        secret_digest=pending.secret_digest,
                        status="active",
                        created_at=now,
                        updated_at=now,
                    )
                )
        finally:
            await database.close()
        return pending.credential, installation_id

    return asyncio.run(seed())


def seeded_user(database_url: str) -> UUID:
    async def seed() -> UUID:
        user_id = UserId.new().uuid
        now = datetime.now(UTC)
        database = Database.from_url(database_url)
        try:
            async with database.session() as session:
                await session.execute(
                    insert(users).values(
                        id=user_id,
                        login_name=f"owner-{uuid4().hex}",
                        created_at=now,
                        updated_at=now,
                    )
                )
        finally:
            await database.close()
        return cast(UUID, user_id)

    return asyncio.run(seed())


def disable_account(database_url: str, user_id: UUID) -> None:
    async def disable() -> None:
        now = datetime.now(UTC)
        database = Database.from_url(database_url)
        try:
            async with database.session() as session:
                await session.execute(
                    update(users)
                    .where(users.c.id == user_id)
                    .values(
                        status=AccountStatus.DISABLED.value,
                        revision=users.c.revision + 1,
                        updated_at=now,
                        disabled_at=now,
                    )
                )
        finally:
            await database.close()

    asyncio.run(disable())


def discard(
    database_url: str, installation_ids: tuple[UUID, ...], user_ids: tuple[UUID, ...]
) -> None:
    async def remove() -> None:
        database = Database.from_url(database_url)
        try:
            async with database.session() as session:
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
                if user_ids:
                    await session.execute(delete(users).where(users.c.id.in_(user_ids)))
        finally:
            await database.close()

    asyncio.run(remove())


def exchanged_session(client: TestClient, credential: str, capability: str) -> str:
    exchanged = client.post(
        "/api/v1/device-sessions",
        headers={"authorization": f"Bearer {credential}"},
        json={"capability": capability},
    )
    assert exchanged.status_code == 201
    return str(exchanged.json()["sessionToken"])


def test_unowned_installation_cannot_reach_business_apis_where_accounts_exist(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    credential, installation_id = seeded_credential(postgresql_url, owner_user_id=None)
    try:
        with control_plane(monkeypatch, postgresql_url, product_accounts=True) as client:
            session_token = exchanged_session(
                client,
                credential,
                DeviceSessionCapability.APP_CONTROL_PLANE.value,
            )

            for route in BUSINESS_ROUTES:
                response = client.get(
                    route,
                    headers={"authorization": f"Bearer {session_token}"},
                )

                assert response.status_code == 401, route
                assert response.headers["cache-control"] == "no-store"
                assert response.json()["error"]["code"] == "installation_access_denied"
                assert response.json()["error"]["message"] == "Installation access is unavailable"
                assert str(installation_id) not in response.text
                assert "owner" not in response.text.lower()
    finally:
        discard(postgresql_url, (installation_id,), ())


def test_unowned_installation_still_reaches_business_apis_on_a_local_deployment(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    credential, installation_id = seeded_credential(postgresql_url, owner_user_id=None)
    try:
        with control_plane(monkeypatch, postgresql_url, product_accounts=False) as client:
            session_token = exchanged_session(
                client,
                credential,
                DeviceSessionCapability.APP_CONTROL_PLANE.value,
            )

            for route in BUSINESS_ROUTES:
                response = client.get(
                    route,
                    headers={"authorization": f"Bearer {session_token}"},
                )

                assert response.status_code == 200, route
            probe = client.get(
                "/api/v1/installations/current",
                headers={"authorization": f"Bearer {session_token}"},
            )
            assert probe.json() == {"installationId": str(installation_id), "status": "active"}
    finally:
        discard(postgresql_url, (installation_id,), ())


def test_account_owned_installation_reaches_business_apis_where_accounts_exist(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    user_id = seeded_user(postgresql_url)
    credential, installation_id = seeded_credential(postgresql_url, owner_user_id=user_id)
    try:
        with control_plane(monkeypatch, postgresql_url, product_accounts=True) as client:
            session_token = exchanged_session(
                client,
                credential,
                DeviceSessionCapability.APP_CONTROL_PLANE.value,
            )

            for route in BUSINESS_ROUTES:
                response = client.get(
                    route,
                    headers={"authorization": f"Bearer {session_token}"},
                )

                assert response.status_code == 200, route
    finally:
        discard(postgresql_url, (installation_id,), (user_id,))


def test_disabling_the_owning_account_immediately_closes_business_apis(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    user_id = seeded_user(postgresql_url)
    credential, installation_id = seeded_credential(postgresql_url, owner_user_id=user_id)
    try:
        with control_plane(monkeypatch, postgresql_url, product_accounts=True) as client:
            session_token = exchanged_session(
                client,
                credential,
                DeviceSessionCapability.APP_CONTROL_PLANE.value,
            )
            headers = {"authorization": f"Bearer {session_token}"}
            assert client.get("/api/v1/installations/current", headers=headers).status_code == 200

            disable_account(postgresql_url, user_id)

            rejected = client.get("/api/v1/installations/current", headers=headers)
            assert rejected.status_code == 401
            assert rejected.json()["error"]["code"] == "installation_access_denied"
    finally:
        discard(postgresql_url, (installation_id,), (user_id,))


def test_unowned_installation_cannot_open_the_executor_channel_where_accounts_exist(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    credential, installation_id = seeded_credential(postgresql_url, owner_user_id=None)
    try:
        with control_plane(monkeypatch, postgresql_url, product_accounts=True) as client:
            session_token = exchanged_session(
                client,
                credential,
                DeviceSessionCapability.EXECUTOR_CONNECT.value,
            )

            with (
                pytest.raises(WebSocketDenialResponse) as denied,
                client.websocket_connect(
                    "/api/v1/executors/connect",
                    headers={"authorization": f"Bearer {session_token}"},
                    subprotocols=[EXECUTOR_WEBSOCKET_SUBPROTOCOL],
                ),
            ):
                pass

            assert denied.value.status_code == 403
    finally:
        discard(postgresql_url, (installation_id,), ())


def test_unowned_installation_still_opens_the_executor_channel_on_a_local_deployment(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    credential, installation_id = seeded_credential(postgresql_url, owner_user_id=None)
    try:
        with control_plane(monkeypatch, postgresql_url, product_accounts=False) as client:
            session_token = exchanged_session(
                client,
                credential,
                DeviceSessionCapability.EXECUTOR_CONNECT.value,
            )

            with client.websocket_connect(
                "/api/v1/executors/connect",
                headers={"authorization": f"Bearer {session_token}"},
                subprotocols=[EXECUTOR_WEBSOCKET_SUBPROTOCOL],
            ) as websocket:
                assert websocket.accepted_subprotocol == EXECUTOR_WEBSOCKET_SUBPROTOCOL
    finally:
        discard(postgresql_url, (installation_id,), ())
