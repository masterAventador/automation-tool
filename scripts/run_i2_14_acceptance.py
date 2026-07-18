#!/usr/bin/env python3
"""Run the isolated I2-14 production-path Installation revocation acceptance."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
FRONTEND_ROOT = REPOSITORY_ROOT / "frontend"
COMPOSE_FILE = REPOSITORY_ROOT / "compose.yaml"
TAURI_CONFIG = FRONTEND_ROOT / "src-tauri" / "tauri.installation-revocation-e2e.conf.json"
CONTROL_PLANE_PORT = 8765
APP_IDENTIFIER = "com.aventador.automationtool.i214acceptance"
IDENTITY_FILE_NAME = "device-identity-ed25519-v1"
DEVICE_FILE_NAME = "device-credential-v1"
ENVIRONMENT_ID = "i214-acceptance"
DEVICE_CREDENTIAL_PATTERN = re.compile(
    rb"atdc1\.[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}"
    rb"-[0-9a-f]{12}\.[A-Za-z0-9_-]{43}"
)


def base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def unused_loopback_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def require_control_plane_port_available() -> None:
    with socket.socket() as listener:
        try:
            listener.bind(("127.0.0.1", CONTROL_PLANE_PORT))
        except OSError as error:
            raise RuntimeError("I2-14 requires an unused local Control Plane port") from error


def require_hidden_tauri_configuration() -> None:
    configuration = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))
    windows = configuration.get("app", {}).get("windows", [])
    if len(windows) != 1 or windows[0].get("visible") is not False:
        raise RuntimeError("I2-14 Tauri acceptance must run with a hidden window")


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
        "postgresql+asyncpg://automation_tool_i214:"
        f"{database_password}@127.0.0.1:{database_port}/automation_tool_i214"
    )
    bootstrap_token, bootstrap_public_key = signed_bootstrap()
    environment.update(
        {
            "AUTOMATION_TOOL_DEV_DB_USER": "unused_i214_dev",
            "AUTOMATION_TOOL_DEV_DB_PASSWORD": secrets.token_hex(24),
            "AUTOMATION_TOOL_DEV_DB_NAME": "unused_i214_dev",
            "AUTOMATION_TOOL_DEV_DB_PORT": str(unused_loopback_port()),
            "AUTOMATION_TOOL_TEST_DB_USER": "automation_tool_i214",
            "AUTOMATION_TOOL_TEST_DB_PASSWORD": database_password,
            "AUTOMATION_TOOL_TEST_DB_NAME": "automation_tool_i214",
            "AUTOMATION_TOOL_TEST_DB_PORT": str(database_port),
            "AUTOMATION_TOOL_DATABASE_URL": database_url,
            "AUTOMATION_TOOL_DEMO_ENVIRONMENT_ID": ENVIRONMENT_ID,
            "AUTOMATION_TOOL_DEMO_BOOTSTRAP_PUBLIC_KEY": bootstrap_public_key,
            "AUTOMATION_TOOL_I214_BOOTSTRAP_TOKEN": bootstrap_token,
            "AUTOMATION_TOOL_I214_ENVIRONMENT_ID": ENVIRONMENT_ID,
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


def wait_for_control_plane() -> None:
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        request_id = str(uuid4())
        request = Request(
            f"http://127.0.0.1:{CONTROL_PLANE_PORT}/api/v1/health",
            headers={"x-request-id": request_id},
        )
        try:
            with opener.open(request, timeout=1) as response:
                body = json.loads(response.read())
                if (
                    response.status == 200
                    and response.headers.get("x-request-id") == request_id
                    and response.headers.get("cache-control") == "no-store"
                    and body.get("service") == "control-plane"
                    and body.get("status") == "ok"
                ):
                    return
        except (OSError, URLError, ValueError):
            time.sleep(0.2)
    raise RuntimeError("The isolated Control Plane did not become healthy")


async def wait_for_registration(
    database_url: str,
    app_process: subprocess.Popen[bytes],
) -> str:
    engine = create_async_engine(database_url)
    deadline = time.monotonic() + 180
    try:
        while time.monotonic() < deadline:
            if app_process.poll() is not None:
                raise RuntimeError("The hidden Tauri acceptance exited before registration")
            async with engine.connect() as connection:
                rows = (
                    await connection.execute(
                        text("select id::text, status, revision from installations")
                    )
                ).all()
            if len(rows) == 1 and rows[0][1:] == ("active", 1):
                return str(rows[0][0])
            await asyncio.sleep(0.2)
    finally:
        await engine.dispose()
    raise RuntimeError("The hidden Tauri App did not register an Installation")


def revoke_with_operator_cli(
    installation_id: str,
    environment: dict[str, str],
) -> None:
    result = subprocess.run(
        [
            "uv",
            "run",
            "automation-tool-revoke-installation",
            "--installation-id",
            installation_id,
            "--expected-revision",
            "1",
        ],
        check=False,
        cwd=BACKEND_ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or result.stdout.strip() != '{"revision":2,"status":"revoked"}':
        raise RuntimeError("The operator revocation CLI failed safely")
    if result.stderr:
        raise RuntimeError("The operator revocation CLI emitted unexpected diagnostics")


def verify_app_private_data(path: Path) -> bytes:
    identity_path = path / IDENTITY_FILE_NAME
    device_path = path / DEVICE_FILE_NAME
    if not path.is_dir() or not identity_path.is_file() or not device_path.is_file():
        raise RuntimeError("The isolated Tauri App private data is incomplete")
    identity = identity_path.read_bytes()
    credential = device_path.read_bytes()
    if len(identity) != 32 or DEVICE_CREDENTIAL_PATTERN.fullmatch(credential) is None:
        raise RuntimeError("The isolated Tauri App private data has an invalid shape")
    if list(path.glob(".*.tmp")):
        raise RuntimeError("The isolated App private data contains temporary files")
    if os.name == "posix":
        for private_path in (path, identity_path, device_path):
            if stat.S_IMODE(private_path.stat().st_mode) & 0o077:
                raise RuntimeError("The isolated App private data is over-permissive")
    return Ed25519PrivateKey.from_private_bytes(identity).public_key().public_bytes_raw()


async def verify_database_state(database_url: str, expected_public_key: bytes) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            installation_rows = (
                await connection.execute(
                    text(
                        "select device_public_key, status, revision, revoked_at is not null "
                        "from installations"
                    )
                )
            ).all()
            credential_rows = (
                await connection.execute(
                    text("select version, status, revoked_at is not null from device_credentials")
                )
            ).all()
            session_rows = (
                await connection.execute(
                    text("select capability, revoked_at is not null from device_sessions")
                )
            ).all()
    finally:
        await engine.dispose()

    if installation_rows != [(expected_public_key, "revoked", 2, True)]:
        raise RuntimeError("The final Installation revocation state is invalid")
    if credential_rows != [(1, "revoked", True)]:
        raise RuntimeError("The final device credential revocation state is invalid")
    if not session_rows or any(row != ("app.control-plane", True) for row in session_rows):
        raise RuntimeError("The App Sessions were not all revoked atomically")


def main() -> None:
    require_control_plane_port_available()
    require_hidden_tauri_configuration()
    private_app_data = app_data_directory()
    if private_app_data.exists():
        raise RuntimeError("Refusing to reuse an existing I2-14 App data directory")

    project_name = f"automation-tool-i214-{os.getpid()}"
    database_port = unused_loopback_port()
    environment, database_url = isolated_environment(database_port)
    compose = compose_command(project_name)
    server: subprocess.Popen[bytes] | None = None
    app_process: subprocess.Popen[bytes] | None = None

    try:
        print("[I2-14] Starting isolated PostgreSQL")
        subprocess.run(
            [*compose, "up", "--detach", "--wait", "postgres-test"],
            check=True,
            cwd=REPOSITORY_ROOT,
            env=environment,
        )
        print("[I2-14] Applying the production Alembic migration chain")
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            check=True,
            cwd=BACKEND_ROOT,
            env=environment,
        )
        print("[I2-14] Starting the real FastAPI Control Plane")
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
                "--ws-max-size",
                "32768",
                "--no-access-log",
            ],
            cwd=BACKEND_ROOT,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        wait_for_control_plane()
        print("[I2-14] Starting the real Tauri App with its window hidden")
        app_process = subprocess.Popen(
            ["pnpm", "test:installation-revocation-tauri"],
            cwd=FRONTEND_ROOT,
            env=environment,
        )
        installation_id = asyncio.run(wait_for_registration(database_url, app_process))
        print("[I2-14] Revoking the Installation through the server-operator CLI")
        revoke_with_operator_cli(installation_id, environment)
        if app_process.wait(timeout=120) != 0:
            raise RuntimeError("The hidden Tauri revocation acceptance failed")
        expected_public_key = verify_app_private_data(private_app_data)
        asyncio.run(verify_database_state(database_url, expected_public_key))
        print("[I2-14] Production-path acceptance passed")
    finally:
        if app_process is not None and app_process.poll() is None:
            app_process.terminate()
            try:
                app_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                app_process.kill()
                app_process.wait(timeout=5)
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
