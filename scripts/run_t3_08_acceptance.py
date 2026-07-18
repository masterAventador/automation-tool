#!/usr/bin/env python3
"""Run the isolated T3-08 real-network Executor registry acceptance."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from run_i2_13_acceptance import (
    BACKEND_ROOT,
    REPOSITORY_ROOT,
    compose_command,
    open_executor,
    post_json,
    require_port_closed,
    seed_active_credential,
    unused_loopback_port,
    wait_for_control_plane,
)
from sqlalchemy import select
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import ClientConnection

from automation_tool.control_plane.api.executor_websocket import (
    EXECUTOR_CLOSE_CONNECTION_REPLACED,
    EXECUTOR_CLOSE_PROTOCOL_REJECTED,
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
)
from automation_tool.protocol import MAX_EXECUTOR_MESSAGE_BYTES


def isolated_environment(database_port: int) -> tuple[dict[str, str], str]:
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("AUTOMATION_TOOL_")
    }
    database_password = secrets.token_hex(24)
    database_url = (
        "postgresql+asyncpg://automation_tool_t308:"
        f"{database_password}@127.0.0.1:{database_port}/automation_tool_t308"
    )
    environment.update(
        {
            "AUTOMATION_TOOL_DEV_DB_USER": "unused_t308_dev",
            "AUTOMATION_TOOL_DEV_DB_PASSWORD": secrets.token_hex(24),
            "AUTOMATION_TOOL_DEV_DB_NAME": "unused_t308_dev",
            "AUTOMATION_TOOL_DEV_DB_PORT": str(unused_loopback_port()),
            "AUTOMATION_TOOL_TEST_DB_USER": "automation_tool_t308",
            "AUTOMATION_TOOL_TEST_DB_PASSWORD": database_password,
            "AUTOMATION_TOOL_TEST_DB_NAME": "automation_tool_t308",
            "AUTOMATION_TOOL_TEST_DB_PORT": str(database_port),
            "AUTOMATION_TOOL_DATABASE_URL": database_url,
        }
    )
    return environment, database_url


def lifecycle_message(
    installation_id: InstallationId,
    executor_id: str,
    *,
    message_type: str,
    sequence: int,
) -> str:
    sent_at = datetime.now(UTC)
    payload = (
        {
            "architecture": "arm64",
            "executor_version": "0.1.0",
            "platform": "macos",
        }
        if message_type == "executor.hello"
        else {"status": "healthy"}
    )
    return json.dumps(
        {
            "protocol_version": "1.0",
            "message_id": str(uuid4()),
            "message_type": message_type,
            "sent_at": sent_at.isoformat(timespec="microseconds").replace("+00:00", "Z"),
            "deadline_at": (sent_at + timedelta(seconds=30))
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z"),
            "installation_id": str(installation_id),
            "executor_id": executor_id,
            "correlation_id": str(uuid4()),
            "idempotency_key": f"executor:{message_type}:{uuid4()}",
            "sequence": sequence,
            "payload": payload,
        },
        separators=(",", ":"),
    )


def expect_closed(connection: ClientConnection, *, code: int, reason: str) -> None:
    try:
        connection.recv(timeout=5)
    except ConnectionClosed as error:
        if error.code == code and error.reason == reason:
            return
    raise RuntimeError("The T3-08 WebSocket closed with an unexpected public result")


def expect_still_open(connection: ClientConnection) -> None:
    try:
        connection.recv(timeout=0.25)
    except TimeoutError:
        return
    except ConnectionClosed as error:
        raise RuntimeError("The current T3-08 Executor connection closed unexpectedly") from error
    raise RuntimeError("The T3-08 server sent an unexpected unsolicited message")


def exercise_real_registry(
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
        raise RuntimeError("The T3-08 session exchange omitted its opaque session")
    first_executor_id = str(uuid4())
    replacement_executor_id = str(uuid4())

    with open_executor(port, session_token, EXECUTOR_WEBSOCKET_SUBPROTOCOL) as first:
        first.send(
            lifecycle_message(
                installation_id,
                first_executor_id,
                message_type="executor.hello",
                sequence=1,
            )
        )
        first.send(
            lifecycle_message(
                installation_id,
                first_executor_id,
                message_type="executor.heartbeat",
                sequence=2,
            )
        )
        expect_still_open(first)

        with open_executor(port, session_token, EXECUTOR_WEBSOCKET_SUBPROTOCOL) as replacement:
            replacement.send(
                lifecycle_message(
                    installation_id,
                    replacement_executor_id,
                    message_type="executor.hello",
                    sequence=1,
                )
            )
            expect_closed(
                first,
                code=EXECUTOR_CLOSE_CONNECTION_REPLACED,
                reason="Executor connection was replaced",
            )
            replacement.send(
                lifecycle_message(
                    installation_id,
                    replacement_executor_id,
                    message_type="executor.heartbeat",
                    sequence=2,
                )
            )
            expect_still_open(replacement)
            replacement.send(
                lifecycle_message(
                    installation_id,
                    replacement_executor_id,
                    message_type="executor.heartbeat",
                    sequence=2,
                )
            )
            expect_closed(
                replacement,
                code=EXECUTOR_CLOSE_PROTOCOL_REJECTED,
                reason="Executor protocol is rejected",
            )

    with open_executor(port, session_token, EXECUTOR_WEBSOCKET_SUBPROTOCOL) as recovered:
        recovered.send(
            lifecycle_message(
                installation_id,
                replacement_executor_id,
                message_type="executor.hello",
                sequence=1,
            )
        )
        recovered.send(
            lifecycle_message(
                installation_id,
                replacement_executor_id,
                message_type="executor.heartbeat",
                sequence=2,
            )
        )
        expect_still_open(recovered)


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
                    select(device_sessions.c.capability, device_sessions.c.revoked_at).where(
                        device_sessions.c.installation_id == installation_id.uuid
                    )
                )
            ).all()
    finally:
        await database.close()
    if credential_rows != [("active",)]:
        raise RuntimeError("The T3-08 credential database state is invalid")
    if len(session_rows) != 1 or session_rows[0][0] != "executor.connect":
        raise RuntimeError("The T3-08 Session database state is invalid")
    if session_rows[0][1] is not None:
        raise RuntimeError("The T3-08 Session was unexpectedly revoked")


def main() -> None:
    project_name = f"automation-tool-t308-{os.getpid()}"
    database_port = unused_loopback_port()
    control_plane_port = unused_loopback_port()
    environment, database_url = isolated_environment(database_port)
    compose = compose_command(project_name)
    server: subprocess.Popen[bytes] | None = None

    try:
        print("[T3-08] Starting isolated PostgreSQL")
        subprocess.run(
            [*compose, "up", "--detach", "--wait", "postgres-test"],
            check=True,
            cwd=REPOSITORY_ROOT,
            env=environment,
        )
        print("[T3-08] Applying the production Alembic migration chain")
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            check=True,
            cwd=BACKEND_ROOT,
            env=environment,
        )
        credential, installation_id = asyncio.run(seed_active_credential(database_url))
        print("[T3-08] Starting the real Uvicorn network boundary in the background")
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
        print("[T3-08] Exercising replacement, heartbeat, stale rejection, and recovery")
        exercise_real_registry(control_plane_port, credential, installation_id)
        asyncio.run(verify_database_state(database_url, installation_id))
        print("[T3-08] Real-network Registry acceptance passed")
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
