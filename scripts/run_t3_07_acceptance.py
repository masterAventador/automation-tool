#!/usr/bin/env python3
"""Run the isolated T3-07 hidden-App Task query acceptance."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from run_t3_06_acceptance import (
    BACKEND_ROOT,
    FRONTEND_ROOT,
    REPOSITORY_ROOT,
    base64url,
    compose_command,
    unused_loopback_port,
    verify_app_private_data,
    wait_for_control_plane,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

TAURI_CONFIG = FRONTEND_ROOT / "src-tauri" / "tauri.task-query-e2e.conf.json"
CONTROL_PLANE_PORT = 8765
APP_IDENTIFIER = "com.aventador.automationtool.t307acceptance"
ENVIRONMENT_ID = "t307-acceptance"
OWNED_KEYS = (
    "task:query:tauri-acceptance:1",
    "task:query:tauri-acceptance:2",
    "task:query:tauri-acceptance:3",
)
FOREIGN_KEY = "task:query:foreign"


def require_control_plane_port_available() -> None:
    with socket.socket() as listener:
        try:
            listener.bind(("127.0.0.1", CONTROL_PLANE_PORT))
        except OSError as error:
            raise RuntimeError("T3-07 requires an unused local Control Plane port") from error


def require_hidden_tauri_configuration() -> None:
    configuration = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))
    windows = configuration.get("app", {}).get("windows", [])
    if len(windows) != 1 or windows[0].get("visible") is not False:
        raise RuntimeError("T3-07 Tauri acceptance must run with a hidden window")


def app_data_directory() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_IDENTIFIER
    if sys.platform == "win32":
        roaming = os.environ.get("APPDATA")
        if roaming is None:
            raise RuntimeError("Windows roaming AppData is unavailable")
        return Path(roaming) / APP_IDENTIFIER
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg_data_home) if xdg_data_home else Path.home() / ".local" / "share"
    return base / APP_IDENTIFIER


def signed_bootstrap() -> tuple[str, str]:
    signer = Ed25519PrivateKey.generate()
    now = datetime.now(UTC)
    claims = {
        "environmentId": ENVIRONMENT_ID,
        "expiresAt": int((now + timedelta(hours=1)).timestamp()),
        "notBefore": int((now - timedelta(seconds=30)).timestamp()),
        "purpose": "installation.register",
        "version": 1,
    }
    payload = json.dumps(
        claims,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    payload_segment = base64url(payload)
    signing_input = f"atb1.{payload_segment}".encode("ascii")
    token = f"atb1.{payload_segment}.{base64url(signer.sign(signing_input))}"
    public_key = base64url(signer.public_key().public_bytes_raw())
    return token, public_key


def isolated_environment(database_port: int) -> tuple[dict[str, str], str]:
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("AUTOMATION_TOOL_")
    }
    database_password = secrets.token_hex(24)
    database_url = (
        "postgresql+asyncpg://automation_tool_t307:"
        f"{database_password}@127.0.0.1:{database_port}/automation_tool_t307"
    )
    bootstrap_token, bootstrap_public_key = signed_bootstrap()
    environment.update(
        {
            "AUTOMATION_TOOL_DEV_DB_USER": "unused_t307_dev",
            "AUTOMATION_TOOL_DEV_DB_PASSWORD": secrets.token_hex(24),
            "AUTOMATION_TOOL_DEV_DB_NAME": "unused_t307_dev",
            "AUTOMATION_TOOL_DEV_DB_PORT": str(unused_loopback_port()),
            "AUTOMATION_TOOL_TEST_DB_USER": "automation_tool_t307",
            "AUTOMATION_TOOL_TEST_DB_PASSWORD": database_password,
            "AUTOMATION_TOOL_TEST_DB_NAME": "automation_tool_t307",
            "AUTOMATION_TOOL_TEST_DB_PORT": str(database_port),
            "AUTOMATION_TOOL_DATABASE_URL": database_url,
            "AUTOMATION_TOOL_DEMO_ENVIRONMENT_ID": ENVIRONMENT_ID,
            "AUTOMATION_TOOL_DEMO_BOOTSTRAP_PUBLIC_KEY": bootstrap_public_key,
            "AUTOMATION_TOOL_T307_BOOTSTRAP_TOKEN": bootstrap_token,
            "AUTOMATION_TOOL_T307_ENVIRONMENT_ID": ENVIRONMENT_ID,
        }
    )
    return environment, database_url


async def seed_foreign_task(database_url: str) -> tuple[str, str]:
    installation_id = uuid4()
    task_id = uuid4()
    now = datetime.now(UTC)
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "insert into installations "
                    "(id, device_public_key, status, revision, created_at, updated_at) "
                    "values (:id, :key, 'active', 1, :now, :now)"
                ),
                {"id": installation_id, "key": secrets.token_bytes(32), "now": now},
            )
            await connection.execute(
                text(
                    "insert into tasks "
                    "(id, installation_id, creation_idempotency_key, status, revision, "
                    "created_at, updated_at) "
                    "values (:id, :installation_id, :key, 'draft', 1, :now, :now)"
                ),
                {
                    "id": task_id,
                    "installation_id": installation_id,
                    "key": FOREIGN_KEY,
                    "now": now,
                },
            )
    finally:
        await engine.dispose()
    return str(installation_id), str(task_id)


async def verify_database_state(
    database_url: str,
    expected_public_key: bytes,
    foreign_installation_id: str,
    foreign_task_id: str,
) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            installation_rows = (
                await connection.execute(
                    text("select id::text, device_public_key, status, revision from installations")
                )
            ).all()
            task_rows = (
                await connection.execute(
                    text(
                        "select id::text, installation_id::text, creation_idempotency_key, "
                        "status, revision from tasks order by creation_idempotency_key"
                    )
                )
            ).all()
            session_rows = (
                await connection.execute(
                    text("select capability, revoked_at is not null from device_sessions")
                )
            ).all()
            challenge_rows = (
                await connection.execute(
                    text(
                        "select environment_id, consumed_at is not null "
                        "from installation_registration_challenges"
                    )
                )
            ).all()
    finally:
        await engine.dispose()

    own_installations = [row for row in installation_rows if row[1] == expected_public_key]
    if len(installation_rows) != 2 or len(own_installations) != 1:
        raise RuntimeError("The acceptance Installation isolation state is invalid")
    own_installation_id, _, own_status, own_revision = own_installations[0]
    if (own_status, own_revision) != ("active", 1):
        raise RuntimeError("The App Installation state is invalid")
    foreign_rows = [row for row in task_rows if row[1] == foreign_installation_id]
    own_rows = [row for row in task_rows if row[1] == own_installation_id]
    if foreign_rows != [(foreign_task_id, foreign_installation_id, FOREIGN_KEY, "draft", 1)]:
        raise RuntimeError("The foreign Task fixture changed unexpectedly")
    if len(own_rows) != 3 or {row[2] for row in own_rows} != set(OWNED_KEYS):
        raise RuntimeError("The App Task query set is invalid")
    if any(row[3:] != ("draft", 1) for row in own_rows):
        raise RuntimeError("The queried Task snapshots are invalid")
    if session_rows != [("app.control-plane", False)] * 7:
        raise RuntimeError("The formal App query path did not use seven scoped Sessions")
    if challenge_rows != [(ENVIRONMENT_ID, True)]:
        raise RuntimeError("The registration challenge state is invalid")


def main() -> None:
    require_control_plane_port_available()
    require_hidden_tauri_configuration()
    private_app_data = app_data_directory()
    if private_app_data.exists():
        raise RuntimeError("Refusing to reuse an existing T3-07 App data directory")

    project_name = f"automation-tool-t307-{os.getpid()}"
    database_port = unused_loopback_port()
    environment, database_url = isolated_environment(database_port)
    compose = compose_command(project_name)
    server: subprocess.Popen[bytes] | None = None

    try:
        print("[T3-07] Starting isolated PostgreSQL")
        subprocess.run(
            [*compose, "up", "--detach", "--wait", "postgres-test"],
            check=True,
            cwd=REPOSITORY_ROOT,
            env=environment,
        )
        print("[T3-07] Applying the production Alembic migration chain")
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            check=True,
            cwd=BACKEND_ROOT,
            env=environment,
        )
        foreign_installation_id, foreign_task_id = asyncio.run(seed_foreign_task(database_url))
        environment["AUTOMATION_TOOL_T307_FOREIGN_TASK_ID"] = foreign_task_id
        print("[T3-07] Starting the real FastAPI Control Plane")
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
                str(CONTROL_PLANE_PORT),
                "--ws",
                "websockets-sansio",
                "--no-access-log",
            ],
            cwd=BACKEND_ROOT,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        wait_for_control_plane()
        print("[T3-07] Running the real Tauri App with visible=false")
        subprocess.run(
            ["pnpm", "test:task-query-tauri"],
            check=True,
            cwd=FRONTEND_ROOT,
            env=environment,
        )
        expected_public_key = verify_app_private_data(private_app_data)
        asyncio.run(
            verify_database_state(
                database_url,
                expected_public_key,
                foreign_installation_id,
                foreign_task_id,
            )
        )
        print("[T3-07] Hidden-App production-path acceptance passed")
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
        if private_app_data.exists():
            shutil.rmtree(private_app_data)


if __name__ == "__main__":
    main()
