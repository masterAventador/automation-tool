#!/usr/bin/env python3
"""Run the isolated U9-06 account/device lifecycle through one real hidden Tauri App."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from run_e4_14_acceptance import start_control_plane
from run_t3_06_acceptance import (
    BACKEND_ROOT,
    FRONTEND_ROOT,
    REPOSITORY_ROOT,
    compose_command,
    unused_loopback_port,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

CONTROL_PLANE_PORT = 8765
APP_IDENTIFIER = "com.aventador.automationtool.u906acceptance"
LOGIN_NAME = "demo.u906"
PASSWORD = "U9-06 correct horse battery"
TAURI_CONFIG = FRONTEND_ROOT / "src-tauri" / "tauri.account-management-e2e.conf.json"


def base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def app_data_directory() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_IDENTIFIER
    if sys.platform == "win32":
        roaming = os.environ.get("APPDATA")
        if roaming is None:
            raise RuntimeError("Windows roaming AppData is unavailable")
        return Path(roaming) / APP_IDENTIFIER
    xdg = os.environ.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else Path.home() / ".local" / "share") / APP_IDENTIFIER


def require_hidden_configuration() -> None:
    configuration = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))
    windows = configuration.get("app", {}).get("windows", [])
    if (
        configuration.get("identifier") != APP_IDENTIFIER
        or len(windows) != 1
        or windows[0].get("visible") is not False
    ):
        raise RuntimeError("U9-06 requires one isolated visible=false Tauri window")


def require_control_plane_port_available() -> None:
    with socket.socket() as listener:
        try:
            listener.bind(("127.0.0.1", CONTROL_PLANE_PORT))
        except OSError as error:
            raise RuntimeError("U9-06 requires the fixed Control Plane port to be free") from error


def isolated_environment(database_port: int) -> tuple[dict[str, str], str, str]:
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("AUTOMATION_TOOL_")
    }
    database_password = secrets.token_hex(24)
    database_url = (
        "postgresql+asyncpg://automation_tool_u906:"
        f"{database_password}@127.0.0.1:{database_port}/automation_tool_u906"
    )
    capability = f"atoc1.{base64url(secrets.token_bytes(32))}"
    environment.update(
        {
            "AUTOMATION_TOOL_DEV_DB_USER": "unused_u906_dev",
            "AUTOMATION_TOOL_DEV_DB_PASSWORD": secrets.token_hex(24),
            "AUTOMATION_TOOL_DEV_DB_NAME": "unused_u906_dev",
            "AUTOMATION_TOOL_DEV_DB_PORT": str(unused_loopback_port()),
            "AUTOMATION_TOOL_TEST_DB_USER": "automation_tool_u906",
            "AUTOMATION_TOOL_TEST_DB_PASSWORD": database_password,
            "AUTOMATION_TOOL_TEST_DB_NAME": "automation_tool_u906",
            "AUTOMATION_TOOL_TEST_DB_PORT": str(database_port),
            "AUTOMATION_TOOL_DATABASE_URL": database_url,
            "AUTOMATION_TOOL_ACCOUNT_PASSWORD_PEPPER": base64url(secrets.token_bytes(32)),
            "AUTOMATION_TOOL_ACCOUNT_PASSWORD_PEPPER_VERSION": "1",
            "AUTOMATION_TOOL_ACCOUNT_FINGERPRINT_KEY": base64url(secrets.token_bytes(32)),
            "AUTOMATION_TOOL_ACCOUNT_OPERATIONS_CAPABILITY_DIGEST": base64url(
                hashlib.sha256(capability.encode("ascii")).digest()
            ),
            "AUTOMATION_TOOL_ACCOUNT_OPERATIONS_ACTOR_ID": str(uuid4()),
            "AUTOMATION_TOOL_CONTROL_PLANE_E2E_ORIGIN": (
                f"http://127.0.0.1:{CONTROL_PLANE_PORT}"
            ),
            "AUTOMATION_TOOL_U906_LOGIN_NAME": LOGIN_NAME,
            "AUTOMATION_TOOL_U906_PASSWORD": PASSWORD,
        }
    )
    return environment, database_url, capability


def account_operation(
    environment: dict[str, str],
    capability: str,
    arguments: list[str],
    *,
    password: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {"capability": capability}
    if password is not None:
        payload["password"] = password
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from automation_tool.control_plane.bootstrap.account_operations_cli import main; main()",
            *arguments,
        ],
        cwd=BACKEND_ROOT,
        env=environment,
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    decoded = json.loads(completed.stdout)
    if not isinstance(decoded, dict):
        raise RuntimeError("U9-06 account operation returned an invalid shape")
    return decoded


def run_phase(environment: dict[str, str], phase: str) -> None:
    phased = dict(environment)
    phased["AUTOMATION_TOOL_U906_PHASE"] = phase
    subprocess.run(
        ["pnpm", "test:account-management-app"],
        cwd=FRONTEND_ROOT,
        env=phased,
        check=True,
        timeout=180,
    )


def stop_control_plane(server: subprocess.Popen[bytes] | None) -> None:
    if server is None or server.poll() is not None:
        return
    server.terminate()
    try:
        server.wait(timeout=10)
    except subprocess.TimeoutExpired:
        server.kill()
        server.wait(timeout=5)


def wait_for_port_closed() -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", CONTROL_PLANE_PORT)) != 0:
                return
        time.sleep(0.05)
    raise RuntimeError("U9-06 Control Plane port remained open")


async def revoke_account_sessions(database_url: str, user_id: UUID) -> None:
    engine = create_async_engine(database_url)
    now = datetime.now(UTC)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "update account_session_families set revoked_at = :now, "
                    "revocation_reason = 'logout' "
                    "where user_id = :user_id and revoked_at is null"
                ),
                {"now": now, "user_id": user_id},
            )
            await connection.execute(
                text(
                    "update account_session_tokens set revoked_at = :now "
                    "where user_id = :user_id and revoked_at is null"
                ),
                {"now": now, "user_id": user_id},
            )
    finally:
        await engine.dispose()


async def verify_final_state(database_url: str, user_id: UUID) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            user = (
                await connection.execute(
                    text(
                        "select status, revision, credential_version from users where id = :user_id"
                    ),
                    {"user_id": user_id},
                )
            ).one()
            installation = (
                await connection.execute(
                    text(
                        "select status, revision, owner_user_id from installations "
                        "where owner_user_id = :user_id"
                    ),
                    {"user_id": user_id},
                )
            ).one()
            credentials = (
                await connection.execute(
                    text("select version, status from device_credentials order by version")
                )
            ).all()
            audit_counts = dict(
                (
                    await connection.execute(
                        text(
                            "select event_type, count(*) from account_audit_events "
                            "where subject_user_id = :user_id group by event_type"
                        ),
                        {"user_id": user_id},
                    )
                ).all()
            )
    finally:
        await engine.dispose()
    if user != ("active", 3, 2):
        raise RuntimeError("U9-06 final account lifecycle is invalid")
    if installation != ("revoked", 2, user_id):
        raise RuntimeError("U9-06 final owned Installation lifecycle is invalid")
    if credentials != [(1, "rotated"), (2, "rotated"), (3, "revoked")]:
        raise RuntimeError("U9-06 device credential rotation/revocation is invalid")
    required = {
        "account.created": 1,
        "account.disabled": 1,
        "account.enabled": 1,
        "device.bound": 3,
        "device.revoked": 1,
    }
    if any(audit_counts.get(event) != count for event, count in required.items()):
        raise RuntimeError("U9-06 append-only account audit is incomplete")


def main() -> None:
    require_control_plane_port_available()
    require_hidden_configuration()
    private_app_data = app_data_directory()
    if private_app_data.exists():
        raise RuntimeError("Refusing to reuse an existing U9-06 App data directory")
    project_name = f"automation-tool-u906-{os.getpid()}"
    environment, database_url, capability = isolated_environment(unused_loopback_port())
    compose = compose_command(project_name)
    server: subprocess.Popen[bytes] | None = None
    try:
        print("[U9-06] Starting isolated PostgreSQL and applying migrations")
        subprocess.run(
            [*compose, "up", "--detach", "--wait", "postgres-test"],
            cwd=REPOSITORY_ROOT,
            env=environment,
            check=True,
        )
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=BACKEND_ROOT,
            env=environment,
            check=True,
        )
        created = account_operation(
            environment,
            capability,
            ["create", "--login-name", LOGIN_NAME, "--request-id", "u906-create"],
            password=PASSWORD,
        )
        user_id = UUID(str(created["userId"]))
        print("[U9-06] Building the isolated hidden Tauri App")
        subprocess.run(
            ["pnpm", "build:tauri:account-management-test"],
            cwd=FRONTEND_ROOT,
            env=environment,
            check=True,
            timeout=900,
        )
        server = start_control_plane(port=CONTROL_PLANE_PORT, environment=environment)
        for phase in ("login", "restart"):
            print(f"[U9-06] Running {phase}")
            run_phase(environment, phase)

        stop_control_plane(server)
        server = None
        wait_for_port_closed()
        print("[U9-06] Running offline fail-closed launch")
        run_phase(environment, "offline")
        server = start_control_plane(port=CONTROL_PLANE_PORT, environment=environment)

        asyncio.run(revoke_account_sessions(database_url, user_id))
        run_phase(environment, "session-invalid")
        run_phase(environment, "relogin")

        disabled = account_operation(
            environment,
            capability,
            [
                "disable",
                "--user-id",
                str(user_id),
                "--expected-revision",
                "1",
                "--request-id",
                "u906-disable",
            ],
        )
        if disabled.get("status") != "disabled":
            raise RuntimeError("U9-06 account disable did not converge")
        run_phase(environment, "disabled")
        restored = account_operation(
            environment,
            capability,
            [
                "restore",
                "--user-id",
                str(user_id),
                "--expected-revision",
                "2",
                "--request-id",
                "u906-restore",
            ],
        )
        if restored.get("status") != "active":
            raise RuntimeError("U9-06 account restore did not converge")
        run_phase(environment, "post-restore-login")
        run_phase(environment, "device-revoke")
        asyncio.run(verify_final_state(database_url, user_id))
        print("[U9-06] Hidden real-Tauri longitudinal acceptance passed")
    finally:
        stop_control_plane(server)
        subprocess.run(
            [*compose, "down", "--volumes", "--remove-orphans"],
            cwd=REPOSITORY_ROOT,
            env=environment,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if private_app_data.exists():
            shutil.rmtree(private_app_data)


if __name__ == "__main__":
    main()
