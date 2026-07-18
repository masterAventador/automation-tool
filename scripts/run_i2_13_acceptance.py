#!/usr/bin/env python3
"""Run the isolated I2-13 real-network WebSocket acceptance."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener
from uuid import UUID, uuid4

from sqlalchemy import insert, select
from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.sync.client import ClientConnection, connect

from automation_tool.control_plane.api.executor_websocket import (
    EXECUTOR_CLOSE_AUTHENTICATION_REJECTED,
    EXECUTOR_CLOSE_IDENTITY_REJECTED,
)
from automation_tool.control_plane.application.device_credentials import (
    DEVICE_CREDENTIAL_SCOPE,
    DeviceCredentialFactory,
)
from automation_tool.control_plane.application.device_sessions import (
    DeviceSessionCapability,
)
from automation_tool.control_plane.application.executor_connections import (
    EXECUTOR_WEBSOCKET_SUBPROTOCOL,
)
from automation_tool.control_plane.domain import InstallationId
from automation_tool.control_plane.infrastructure.database import (
    Database,
    device_credentials,
    device_sessions,
    installations,
)
from automation_tool.protocol import MAX_EXECUTOR_MESSAGE_BYTES

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
COMPOSE_FILE = REPOSITORY_ROOT / "compose.yaml"
EXECUTOR_ID = UUID("123e4567-e89b-42d3-a456-426614174004")
OPENER = build_opener(ProxyHandler({}))


def unused_loopback_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def require_port_closed(port: int) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with socket.socket() as client:
            client.settimeout(0.2)
            if client.connect_ex(("127.0.0.1", port)) != 0:
                return
        time.sleep(0.1)
    raise RuntimeError("I2-13 left a loopback listener running")


def isolated_environment(database_port: int) -> tuple[dict[str, str], str]:
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("AUTOMATION_TOOL_")
    }
    database_password = secrets.token_hex(24)
    database_url = (
        "postgresql+asyncpg://automation_tool_i213:"
        f"{database_password}@127.0.0.1:{database_port}/automation_tool_i213"
    )
    environment.update(
        {
            "AUTOMATION_TOOL_DEV_DB_USER": "unused_i213_dev",
            "AUTOMATION_TOOL_DEV_DB_PASSWORD": secrets.token_hex(24),
            "AUTOMATION_TOOL_DEV_DB_NAME": "unused_i213_dev",
            "AUTOMATION_TOOL_DEV_DB_PORT": str(unused_loopback_port()),
            "AUTOMATION_TOOL_TEST_DB_USER": "automation_tool_i213",
            "AUTOMATION_TOOL_TEST_DB_PASSWORD": database_password,
            "AUTOMATION_TOOL_TEST_DB_NAME": "automation_tool_i213",
            "AUTOMATION_TOOL_TEST_DB_PORT": str(database_port),
            "AUTOMATION_TOOL_DATABASE_URL": database_url,
        }
    )
    return environment, database_url


def compose_command(project_name: str) -> list[str]:
    return [
        "docker",
        "compose",
        "--project-name",
        project_name,
        "--env-file",
        os.devnull,
        "--file",
        str(COMPOSE_FILE),
    ]


def wait_for_control_plane(port: int, server: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise RuntimeError("The isolated I2-13 Control Plane stopped during startup")
        request = Request(f"http://127.0.0.1:{port}/api/v1/health")
        try:
            with OPENER.open(request, timeout=1) as response:
                body = json.loads(response.read())
                if (
                    response.status == 200
                    and response.headers.get("cache-control") == "no-store"
                    and body.get("service") == "control-plane"
                    and body.get("status") == "ok"
                ):
                    return
        except (OSError, URLError, ValueError):
            time.sleep(0.2)
    raise RuntimeError("The isolated I2-13 Control Plane did not become healthy")


async def seed_active_credential(database_url: str) -> tuple[str, InstallationId]:
    database = Database.from_url(database_url)
    installation_id = InstallationId.new()
    pending = DeviceCredentialFactory(
        secret_source=secrets.token_bytes,
        id_source=uuid4,
    ).create()
    now = datetime.now(UTC)
    try:
        async with database.session() as session:
            await session.execute(
                insert(installations).values(
                    id=installation_id.uuid,
                    device_public_key=secrets.token_bytes(32),
                    created_at=now,
                    updated_at=now,
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
                    created_at=now,
                    updated_at=now,
                )
            )
    finally:
        await database.close()
    return pending.credential, installation_id


def post_json(
    port: int,
    path: str,
    bearer: str,
    *,
    payload: dict[str, object] | None = None,
    expected_status: int,
) -> dict[str, Any]:
    body = None
    headers = {
        "accept": "application/json",
        "authorization": f"Bearer {bearer}",
        "x-request-id": str(uuid4()),
    }
    if payload is not None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers["content-type"] = "application/json"
    request = Request(
        f"http://127.0.0.1:{port}{path}",
        data=body,
        headers=headers,
        method="POST",
    )
    with OPENER.open(request, timeout=5) as response:
        if response.status != expected_status:
            raise RuntimeError("An I2-13 REST precondition returned an unexpected status")
        if response.headers.get("cache-control") != "no-store":
            raise RuntimeError("An I2-13 credential response was cacheable")
        decoded = json.loads(response.read())
    if not isinstance(decoded, dict):
        raise RuntimeError("An I2-13 REST precondition returned an invalid body")
    return decoded


def lifecycle_message(
    installation_id: InstallationId,
    *,
    message_type: str,
    claimed_installation_id: InstallationId | None = None,
    sequence: int,
) -> str:
    sent_at = datetime.now(UTC)
    deadline_at = sent_at + timedelta(seconds=30)
    payload: dict[str, str]
    if message_type == "executor.hello":
        payload = {
            "architecture": "arm64",
            "executor_version": "0.1.0",
            "platform": "macos",
        }
    else:
        payload = {"status": "healthy"}
    return json.dumps(
        {
            "protocol_version": "1.0",
            "message_id": str(uuid4()),
            "message_type": message_type,
            "sent_at": sent_at.isoformat(timespec="microseconds").replace("+00:00", "Z"),
            "deadline_at": deadline_at.isoformat(timespec="microseconds").replace("+00:00", "Z"),
            "installation_id": str(claimed_installation_id or installation_id),
            "executor_id": str(EXECUTOR_ID),
            "correlation_id": str(uuid4()),
            "idempotency_key": f"executor:{message_type}:{uuid4()}",
            "sequence": sequence,
            "payload": payload,
        },
        separators=(",", ":"),
    )


def open_executor(port: int, session_token: str, subprotocol: str) -> ClientConnection:
    return connect(
        f"ws://127.0.0.1:{port}/api/v1/executors/connect",
        additional_headers={"authorization": f"Bearer {session_token}"},
        subprotocols=[subprotocol],
        compression=None,
        max_size=MAX_EXECUTOR_MESSAGE_BYTES,
        open_timeout=5,
        close_timeout=2,
        proxy=None,
    )


def expect_handshake_rejected(port: int, session_token: str) -> None:
    try:
        with open_executor(port, session_token, "automation-tool.executor.future"):
            pass
    except InvalidStatus as error:
        if error.response.status_code == 403:
            return
    raise RuntimeError("The I2-13 subprotocol boundary did not reject the upgrade")


def expect_closed(connection: ClientConnection, *, code: int, reason: str | None) -> None:
    try:
        connection.recv(timeout=5)
    except ConnectionClosed as error:
        if error.code == code and (reason is None or error.reason == reason):
            return
    raise RuntimeError("The I2-13 WebSocket closed with an unexpected public result")


def exercise_real_websocket(
    port: int,
    credential: str,
    installation_id: InstallationId,
) -> None:
    exchanged = post_json(
        port,
        "/api/v1/device-sessions",
        credential,
        payload={"capability": DeviceSessionCapability.EXECUTOR_CONNECT.value},
        expected_status=201,
    )
    session_token = exchanged.get("sessionToken")
    if not isinstance(session_token, str):
        raise RuntimeError("The I2-13 session exchange omitted its opaque session")

    expect_handshake_rejected(port, session_token)

    with open_executor(port, session_token, EXECUTOR_WEBSOCKET_SUBPROTOCOL) as oversized:
        oversized.send("x" * (MAX_EXECUTOR_MESSAGE_BYTES + 1))
        expect_closed(oversized, code=1009, reason=None)

    with open_executor(port, session_token, EXECUTOR_WEBSOCKET_SUBPROTOCOL) as impersonating:
        impersonating.send(
            lifecycle_message(
                installation_id,
                message_type="executor.hello",
                claimed_installation_id=InstallationId.new(),
                sequence=1,
            )
        )
        expect_closed(
            impersonating,
            code=EXECUTOR_CLOSE_IDENTITY_REJECTED,
            reason="Executor identity is rejected",
        )

    with open_executor(port, session_token, EXECUTOR_WEBSOCKET_SUBPROTOCOL) as connected:
        connected.send(
            lifecycle_message(
                installation_id,
                message_type="executor.hello",
                sequence=1,
            )
        )
        connected.send(
            lifecycle_message(
                installation_id,
                message_type="executor.heartbeat",
                sequence=2,
            )
        )
        revoked = post_json(
            port,
            "/api/v1/device-credentials/revocations",
            credential,
            expected_status=200,
        )
        if revoked != {"version": 1, "status": "revoked"}:
            raise RuntimeError("The I2-13 credential revocation returned an invalid result")
        expect_closed(
            connected,
            code=EXECUTOR_CLOSE_AUTHENTICATION_REJECTED,
            reason="Executor authentication is rejected",
        )

    try:
        with open_executor(port, session_token, EXECUTOR_WEBSOCKET_SUBPROTOCOL):
            pass
    except InvalidStatus as error:
        if error.response.status_code == 403:
            return
    raise RuntimeError("The revoked I2-13 session unexpectedly reconnected")


async def verify_database_state(database_url: str, installation_id: InstallationId) -> None:
    database = Database.from_url(database_url)
    try:
        async with database.session() as session:
            credential_rows = (
                await session.execute(
                    select(device_credentials.c.status).where(
                        device_credentials.c.installation_id == installation_id.uuid
                    )
                )
            ).all()
            session_rows = (
                await session.execute(
                    select(
                        device_sessions.c.capability,
                        device_sessions.c.revoked_at.is_not(None),
                    ).where(device_sessions.c.installation_id == installation_id.uuid)
                )
            ).all()
    finally:
        await database.close()
    if credential_rows != [("revoked",)]:
        raise RuntimeError("The I2-13 credential database state is invalid")
    if session_rows != [(DeviceSessionCapability.EXECUTOR_CONNECT.value, True)]:
        raise RuntimeError("The I2-13 session database state is invalid")


def main() -> None:
    project_name = f"automation-tool-i213-{os.getpid()}"
    database_port = unused_loopback_port()
    control_plane_port = unused_loopback_port()
    environment, database_url = isolated_environment(database_port)
    compose = compose_command(project_name)
    server: subprocess.Popen[bytes] | None = None

    try:
        print("[I2-13] Starting isolated PostgreSQL")
        subprocess.run(
            [*compose, "up", "--detach", "--wait", "postgres-test"],
            check=True,
            cwd=REPOSITORY_ROOT,
            env=environment,
        )
        print("[I2-13] Applying the production Alembic migration chain")
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            check=True,
            cwd=BACKEND_ROOT,
            env=environment,
        )
        credential, installation_id = asyncio.run(seed_active_credential(database_url))
        print("[I2-13] Starting the real Uvicorn network boundary in the background")
        server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "automation_tool.control_plane:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(control_plane_port),
                "--ws-max-size",
                str(MAX_EXECUTOR_MESSAGE_BYTES),
                "--ws",
                "websockets-sansio",
                "--no-access-log",
            ],
            cwd=BACKEND_ROOT,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        wait_for_control_plane(control_plane_port, server)
        print("[I2-13] Exercising the real REST and WebSocket callers")
        exercise_real_websocket(
            control_plane_port,
            credential,
            installation_id,
        )
        asyncio.run(verify_database_state(database_url, installation_id))
        print("[I2-13] Real-network acceptance passed")
    except HTTPError as error:
        raise RuntimeError("An I2-13 REST boundary returned an unexpected status") from error
    finally:
        if server is not None and server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
        subprocess.run(
            [*compose, "down", "--volumes", "--remove-orphans"],
            check=False,
            cwd=REPOSITORY_ROOT,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        require_port_closed(control_plane_port)
        require_port_closed(database_port)


if __name__ == "__main__":
    main()
