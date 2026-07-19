#!/usr/bin/env python3
"""Run T3-17 through the hidden Task form and production App request path."""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from run_i2_13_acceptance import require_port_closed
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

TAURI_CONFIG = FRONTEND_ROOT / "src-tauri" / "tauri.task-create-form-e2e.conf.json"
CONTROL_PLANE_PORT = 8765
APP_IDENTIFIER = "com.aventador.automationtool.t317acceptance"
ENVIRONMENT_ID = "t317-acceptance"
IDEMPOTENCY_KEY_PATTERN = re.compile(
    r"task:create:douyin-search:[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}"
    r"-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)


def require_control_plane_port_available() -> None:
    with socket.socket() as listener:
        try:
            listener.bind(("127.0.0.1", CONTROL_PLANE_PORT))
        except OSError as error:
            raise RuntimeError("T3-17 requires an unused Control Plane port") from error


def require_hidden_tauri_configuration() -> None:
    configuration = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))
    windows = configuration.get("app", {}).get("windows", [])
    if len(windows) != 1 or windows[0].get("visible") is not False:
        raise RuntimeError("T3-17 Tauri acceptance must run with visible=false")


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
        "postgresql+asyncpg://automation_tool_t317:"
        f"{database_password}@127.0.0.1:{database_port}/automation_tool_t317"
    )
    bootstrap_token, bootstrap_public_key = signed_bootstrap()
    environment.update(
        {
            "AUTOMATION_TOOL_DEV_DB_USER": "unused_t317_dev",
            "AUTOMATION_TOOL_DEV_DB_PASSWORD": secrets.token_hex(24),
            "AUTOMATION_TOOL_DEV_DB_NAME": "unused_t317_dev",
            "AUTOMATION_TOOL_DEV_DB_PORT": str(unused_loopback_port()),
            "AUTOMATION_TOOL_TEST_DB_USER": "automation_tool_t317",
            "AUTOMATION_TOOL_TEST_DB_PASSWORD": database_password,
            "AUTOMATION_TOOL_TEST_DB_NAME": "automation_tool_t317",
            "AUTOMATION_TOOL_TEST_DB_PORT": str(database_port),
            "AUTOMATION_TOOL_DATABASE_URL": database_url,
            "AUTOMATION_TOOL_DEMO_ENVIRONMENT_ID": ENVIRONMENT_ID,
            "AUTOMATION_TOOL_DEMO_BOOTSTRAP_PUBLIC_KEY": bootstrap_public_key,
            "AUTOMATION_TOOL_T317_BOOTSTRAP_TOKEN": bootstrap_token,
            "AUTOMATION_TOOL_T317_ENVIRONMENT_ID": ENVIRONMENT_ID,
        }
    )
    return environment, database_url


async def verify_database_state(database_url: str, expected_public_key: bytes) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            installations = (
                await connection.execute(
                    text("select id, device_public_key, status, revision from installations")
                )
            ).all()
            definitions = (
                await connection.execute(
                    text(
                        "select t.installation_id, t.creation_idempotency_key, t.status, "
                        "t.revision, d.template, d.search_keyword, d.action, "
                        "d.message_template, d.target_limit, d.minimum_interval_seconds, "
                        "d.maximum_interval_seconds, d.preview_required, "
                        "d.final_confirmation_required "
                        "from tasks t join douyin_search_exposure_definitions d "
                        "on d.task_id = t.id and d.installation_id = t.installation_id"
                    )
                )
            ).all()
            challenges = (
                await connection.execute(
                    text(
                        "select environment_id, consumed_at is not null "
                        "from installation_registration_challenges"
                    )
                )
            ).all()
            credentials = (
                await connection.execute(
                    text("select version, status from device_credentials order by version")
                )
            ).all()
            sessions = (
                await connection.execute(
                    text("select credential_version, capability from device_sessions")
                )
            ).all()
    finally:
        await engine.dispose()

    if len(installations) != 1:
        raise RuntimeError("T3-17 must create exactly one Installation")
    installation_id, public_key, status, revision = installations[0]
    if (public_key, status, revision) != (expected_public_key, "active", 1):
        raise RuntimeError("T3-17 Installation state is invalid")
    if len(definitions) != 1:
        raise RuntimeError("T3-17 must atomically persist one Task definition")
    definition = definitions[0]
    if (
        definition[0] != installation_id
        or IDEMPOTENCY_KEY_PATTERN.fullmatch(definition[1]) is None
        or tuple(definition[2:])
        != (
            "draft",
            1,
            "douyin.search_exposure.v1",
            "😀" * 80,
            "browse",
            None,
            100,
            30,
            90,
            True,
            True,
        )
    ):
        raise RuntimeError("T3-17 persisted Task definition differs from the App form")
    if challenges != [(ENVIRONMENT_ID, True)]:
        raise RuntimeError("T3-17 registration challenge state is invalid")
    if credentials != [(1, "active")]:
        raise RuntimeError("T3-17 App credential state is invalid")
    if sessions != [(1, "app.control-plane")]:
        raise RuntimeError("T3-17 did not use exactly one formal App Session")


def main() -> None:
    require_control_plane_port_available()
    require_hidden_tauri_configuration()
    private_app_data = app_data_directory()
    if private_app_data.exists():
        raise RuntimeError("Refusing to reuse an existing T3-17 App data directory")

    project_name = f"automation-tool-t317-{os.getpid()}"
    database_port = unused_loopback_port()
    environment, database_url = isolated_environment(database_port)
    compose = compose_command(project_name)
    server: subprocess.Popen[bytes] | None = None
    try:
        print("[T3-17] Starting isolated PostgreSQL")
        subprocess.run(
            [*compose, "up", "--detach", "--wait", "postgres-test"],
            check=True,
            cwd=REPOSITORY_ROOT,
            env=environment,
        )
        print("[T3-17] Applying the production Alembic migration chain")
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            check=True,
            cwd=BACKEND_ROOT,
            env=environment,
        )
        print("[T3-17] Starting the real Uvicorn boundary in the background")
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
        print("[T3-17] Running the real Tauri App with visible=false")
        subprocess.run(
            ["pnpm", "test:task-create-form-tauri"],
            check=True,
            cwd=FRONTEND_ROOT,
            env=environment,
        )
        expected_public_key = verify_app_private_data(private_app_data)
        asyncio.run(verify_database_state(database_url, expected_public_key))
        print("[T3-17] Hidden-App form, Tauri command, API, and persistence acceptance passed")
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
        require_port_closed(CONTROL_PLANE_PORT)
        require_port_closed(database_port)


if __name__ == "__main__":
    main()
