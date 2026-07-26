#!/usr/bin/env python3
"""Run T3-15 through a hidden Tauri App, real Uvicorn, and PostgreSQL."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from desktop_e2e_prerequisites import (
    prepare_startup_gate,
    require_reserved_port_still_free,
    reserve_control_plane_port,
    startup_gate_environment,
    terminate_app_process_tree,
)
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
from run_t3_11_acceptance import (
    CONVERGED_EVENT_COUNT,
    exercise_fake_executor,
    wait_for_convergence,
)
from run_t3_14_acceptance import (
    CONFIRMED_TASK_REVISION,
    seed_attempt_and_offer,
    seed_task_confirmation,
)
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from automation_tool.control_plane.application.device_sessions import (
    DeviceSessionCapability,
)
from automation_tool.control_plane.application.task_command_delivery import (
    TaskCommandRecord,
)
from automation_tool.control_plane.domain import (
    InstallationId,
    TaskId,
    TaskStatus,
)
from automation_tool.control_plane.infrastructure.database import (
    Database,
    task_events,
    tasks,
)
from automation_tool.protocol import MAX_EXECUTOR_MESSAGE_BYTES

TAURI_CONFIG = FRONTEND_ROOT / "src-tauri" / "tauri.task-projection-e2e.conf.json"
CONTROL_PLANE_PORT = reserve_control_plane_port()
APP_IDENTIFIER = "com.aventador.automationtool.t315acceptance"
ENVIRONMENT_ID = "t315-acceptance"
TASK_KEY = "task:projection:tauri-acceptance"
DEVICE_CREDENTIAL_FILE = "device-credential-v1"
APP_CONTROL_PLANE_CAPABILITY = DeviceSessionCapability.APP_CONTROL_PLANE.value
EXECUTOR_CONNECT_CAPABILITY = DeviceSessionCapability.EXECUTOR_CONNECT.value


def require_control_plane_port_available() -> None:
    require_reserved_port_still_free(CONTROL_PLANE_PORT)


def require_hidden_tauri_configuration() -> None:
    configuration = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))
    windows = configuration.get("app", {}).get("windows", [])
    if len(windows) != 1 or windows[0].get("visible") is not False:
        raise RuntimeError("T3-15 Tauri acceptance must run with a hidden window")


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
        "postgresql+asyncpg://automation_tool_t315:"
        f"{database_password}@127.0.0.1:{database_port}/automation_tool_t315"
    )
    bootstrap_token, bootstrap_public_key = signed_bootstrap()
    environment.update(
        {
            "AUTOMATION_TOOL_DEV_DB_USER": "unused_t315_dev",
            "AUTOMATION_TOOL_DEV_DB_PASSWORD": secrets.token_hex(24),
            "AUTOMATION_TOOL_DEV_DB_NAME": "unused_t315_dev",
            "AUTOMATION_TOOL_DEV_DB_PORT": str(unused_loopback_port()),
            "AUTOMATION_TOOL_TEST_DB_USER": "automation_tool_t315",
            "AUTOMATION_TOOL_TEST_DB_PASSWORD": database_password,
            "AUTOMATION_TOOL_TEST_DB_NAME": "automation_tool_t315",
            "AUTOMATION_TOOL_TEST_DB_PORT": str(database_port),
            "AUTOMATION_TOOL_DATABASE_URL": database_url,
            "AUTOMATION_TOOL_DEMO_ENVIRONMENT_ID": ENVIRONMENT_ID,
            "AUTOMATION_TOOL_DEMO_BOOTSTRAP_PUBLIC_KEY": bootstrap_public_key,
            "AUTOMATION_TOOL_T315_BOOTSTRAP_TOKEN": bootstrap_token,
            "AUTOMATION_TOOL_T315_ENVIRONMENT_ID": ENVIRONMENT_ID,
        }
    )
    return (
        startup_gate_environment(environment, control_plane_port=CONTROL_PLANE_PORT),
        database_url,
    )


async def wait_for_app_task(
    database_url: str,
    private_app_data: Path,
    app_process: subprocess.Popen[bytes],
) -> tuple[InstallationId, TaskId, str]:
    engine = create_async_engine(database_url)
    deadline = time.monotonic() + 120
    try:
        while True:
            if app_process.poll() is not None:
                raise RuntimeError("T3-15 hidden App exited before creating its stream Task")
            async with engine.connect() as connection:
                row = (
                    await connection.execute(
                        text(
                            "select installation_id::text, id::text from tasks "
                            "where creation_idempotency_key = :key"
                        ),
                        {"key": TASK_KEY},
                    )
                ).one_or_none()
            credential_path = private_app_data / DEVICE_CREDENTIAL_FILE
            if row is not None and credential_path.is_file():
                try:
                    credential = credential_path.read_text(encoding="ascii")
                except (OSError, UnicodeError) as error:
                    raise RuntimeError("T3-15 App credential vault is unreadable") from error
                return InstallationId.parse(row[0]), TaskId.parse(row[1]), credential
            if time.monotonic() >= deadline:
                raise RuntimeError("T3-15 hidden App did not create its stream Task in time")
            await asyncio.sleep(0.05)
    finally:
        await engine.dispose()


async def verify_database_state(database_url: str, original: TaskCommandRecord) -> None:
    database = Database.from_url(database_url)
    try:
        async with database.session() as session:
            event_rows = (
                (
                    await session.execute(
                        select(
                            task_events.c.sequence,
                            task_events.c.progress_percent,
                        )
                        .where(task_events.c.task_id == original.task_id.uuid)
                        .order_by(task_events.c.sequence)
                    )
                )
                .mappings()
                .all()
            )
            session_capabilities = (
                (await session.execute(text("select capability from device_sessions order by id")))
                .scalars()
                .all()
            )
            task_projection = (
                (
                    await session.execute(
                        select(
                            tasks.c.status,
                            tasks.c.revision,
                            tasks.c.last_event_sequence,
                        ).where(tasks.c.id == original.task_id.uuid)
                    )
                )
                .mappings()
                .one()
            )
        if [row["sequence"] for row in event_rows] != [1, 2, 3, 4, 5]:
            raise RuntimeError("T3-15 durable App event timeline is invalid")
        if [row["progress_percent"] for row in event_rows] != [None, None, 50, None, None]:
            raise RuntimeError("T3-15 structured progress persistence is invalid")
        # The App exchanges a fresh single-capability device Session for every
        # Control Plane call, so the `app.control-plane` count is App uptime
        # times the workbench poll rate, not a property of this acceptance. A
        # request-timeline probe over two runs of this driver recorded
        #   POST /tasks, GET /tasks/{id}, GET /tasks/{id}/events   (this Task)
        #   GET /api/v1/workbench/status, GET /api/v1/tasks        (1 Hz polls)
        # and the poll fires 0..n times depending on how long the hidden App
        # lives after registration — the same run pinned to "exactly 3" passed
        # once and failed once. Three is therefore the floor, not the number.
        app_sessions = session_capabilities.count(APP_CONTROL_PLANE_CAPABILITY)
        executor_sessions = session_capabilities.count(EXECUTOR_CONNECT_CAPABILITY)
        unexpected = sorted(
            set(session_capabilities)
            - {APP_CONTROL_PLANE_CAPABILITY, EXECUTOR_CONNECT_CAPABILITY}
        )
        if app_sessions < 3 or executor_sessions != 1 or unexpected:
            raise RuntimeError(
                "T3-15 did not use the expected App and Executor Sessions: "
                f"{sorted(session_capabilities)}"
            )
        if dict(task_projection) != {
            "status": TaskStatus.SUCCEEDED.value,
            "revision": CONFIRMED_TASK_REVISION + CONVERGED_EVENT_COUNT,
            "last_event_sequence": 5,
        }:
            raise RuntimeError("T3-15 authoritative Task projection is invalid")
    finally:
        await database.close()


def main() -> None:
    require_control_plane_port_available()
    require_hidden_tauri_configuration()
    private_app_data = app_data_directory()
    if private_app_data.exists():
        raise RuntimeError("Refusing to reuse an existing T3-15 App data directory")
    prepare_startup_gate(private_app_data)

    project_name = f"automation-tool-t315-{os.getpid()}"
    database_port = unused_loopback_port()
    environment, database_url = isolated_environment(database_port)
    compose = compose_command(project_name)
    server: subprocess.Popen[bytes] | None = None
    app_process: subprocess.Popen[bytes] | None = None

    try:
        print("[T3-15] Starting isolated PostgreSQL")
        subprocess.run(
            [*compose, "up", "--detach", "--wait", "postgres-test"],
            check=True,
            cwd=REPOSITORY_ROOT,
            env=environment,
        )
        print("[T3-15] Applying the production Alembic migration chain")
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            check=True,
            cwd=BACKEND_ROOT,
            env=environment,
        )
        print("[T3-15] Starting the real Uvicorn boundary in the background")
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
        wait_for_control_plane()
        print("[T3-15] Running the real Tauri App with visible=false")
        app_process = subprocess.Popen(
            ["pnpm", "test:task-projection-tauri"],
            cwd=FRONTEND_ROOT,
            env=environment,
            start_new_session=True,
        )
        installation_id, task_id, credential = asyncio.run(
            wait_for_app_task(database_url, private_app_data, app_process)
        )
        asyncio.run(
            seed_task_confirmation(
                database_url,
                installation_id,
                task_id,
                include_target_results=False,
            )
        )
        original = asyncio.run(
            seed_attempt_and_offer(
                database_url,
                installation_id,
                task_id,
                label="task-projection",
                confirmed_target_revision=True,
            )
        )
        exercise_fake_executor(CONTROL_PLANE_PORT, credential, installation_id)
        asyncio.run(
            wait_for_convergence(
                database_url,
                original,
                task_revision_baseline=CONFIRMED_TASK_REVISION,
            )
        )
        try:
            app_exit = app_process.wait(timeout=180)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("T3-15 hidden App acceptance did not finish") from error
        if app_exit != 0:
            raise RuntimeError("T3-15 hidden App acceptance failed")
        app_process = None
        verify_app_private_data(private_app_data)
        asyncio.run(verify_database_state(database_url, original))
        print("[T3-15] Hidden-App Query, Tauri Channel, and reducer acceptance passed")
    finally:
        if app_process is not None:
            terminate_app_process_tree(app_process)
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
