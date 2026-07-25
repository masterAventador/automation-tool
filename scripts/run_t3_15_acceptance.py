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
from run_t3_11_acceptance import exercise_fake_executor, wait_for_convergence
from sqlalchemy import insert, select, text, update
from sqlalchemy.ext.asyncio import create_async_engine

from automation_tool.control_plane.application.executor_connection_registry import (
    ExecutorConnectionRegistry,
)
from automation_tool.control_plane.application.task_command_delivery import (
    TaskCommandDeliveryService,
    TaskCommandRecord,
)
from automation_tool.control_plane.domain import (
    ExecutionAttemptId,
    ExecutionAttemptStatus,
    InstallationId,
    TaskCommandType,
    TaskId,
    TaskStatus,
)
from automation_tool.control_plane.infrastructure.database import (
    Database,
    SqlAlchemyTaskCommandRepository,
    execution_attempts,
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


async def seed_attempt_and_offer(
    database_url: str,
    installation_id: InstallationId,
    task_id: TaskId,
) -> TaskCommandRecord:
    database = Database.from_url(database_url)
    attempt_id = ExecutionAttemptId.new()
    now = datetime.now(UTC)
    try:
        async with database.session() as session:
            current = await session.scalar(
                select(tasks.c.status).where(
                    tasks.c.id == task_id.uuid,
                    tasks.c.installation_id == installation_id.uuid,
                )
            )
            if current != TaskStatus.DRAFT.value:
                raise RuntimeError("T3-15 App Task fixture is not draft")
            await session.execute(
                update(tasks)
                .where(
                    tasks.c.id == task_id.uuid,
                    tasks.c.installation_id == installation_id.uuid,
                )
                .values(status=TaskStatus.QUEUED.value, updated_at=now)
            )
            await session.execute(
                insert(execution_attempts).values(
                    id=attempt_id.uuid,
                    task_id=task_id.uuid,
                    installation_id=installation_id.uuid,
                    attempt_number=1,
                    status=ExecutionAttemptStatus.ACCEPTED.value,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.execute(
                update(tasks)
                .where(tasks.c.id == task_id.uuid)
                .values(current_attempt_id=attempt_id.uuid)
            )
        service = TaskCommandDeliveryService(
            repository=SqlAlchemyTaskCommandRepository(database),
            registry=ExecutorConnectionRegistry(),
        )
        return await service.enqueue(
            installation_id=installation_id,
            task_id=task_id,
            execution_attempt_id=attempt_id,
            sequence=1,
            command_type=TaskCommandType.TASK_OFFER,
            idempotency_key="task:t315:offer:1",
            deadline_at=now + timedelta(minutes=3),
        )
    finally:
        await database.close()


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
        if sorted(session_capabilities) != [
            "app.control-plane",
            "app.control-plane",
            "app.control-plane",
            "executor.connect",
        ]:
            raise RuntimeError("T3-15 did not use the expected App and Executor Sessions")
        if dict(task_projection) != {
            "status": TaskStatus.SUCCEEDED.value,
            "revision": 6,
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
        )
        installation_id, task_id, credential = asyncio.run(
            wait_for_app_task(database_url, private_app_data, app_process)
        )
        original = asyncio.run(seed_attempt_and_offer(database_url, installation_id, task_id))
        exercise_fake_executor(CONTROL_PLANE_PORT, credential, installation_id)
        asyncio.run(wait_for_convergence(database_url, original))
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
        require_port_closed(CONTROL_PLANE_PORT)
        require_port_closed(database_port)


if __name__ == "__main__":
    main()
