#!/usr/bin/env python3
"""Run T3-13 through a hidden Tauri App, real Uvicorn, PostgreSQL, and FakeExecutor."""

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
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from desktop_e2e_prerequisites import (
    prepare_startup_gate,
    require_reserved_port_still_free,
    reserve_control_plane_port,
    startup_gate_environment,
)
from run_i2_13_acceptance import post_json, require_port_closed
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
from sqlalchemy import insert, select, text, update
from sqlalchemy.ext.asyncio import create_async_engine

from automation_tool.control_plane.application.device_sessions import (
    DeviceSessionCapability,
)
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
    TaskCommandStatus,
    TaskCommandType,
    TaskEventType,
    TaskId,
    TaskStatus,
)
from automation_tool.control_plane.infrastructure.database import (
    Database,
    SqlAlchemyTaskCommandRepository,
    execution_attempts,
    task_commands,
    task_events,
    tasks,
)
from automation_tool.executor import (
    FakeExecutorClient,
    FakeExecutorClientConfiguration,
    FakeExecutorEngine,
    FakeExecutorScenario,
)
from automation_tool.protocol import MAX_EXECUTOR_MESSAGE_BYTES

TAURI_CONFIG = FRONTEND_ROOT / "src-tauri" / "tauri.task-control-e2e.conf.json"
CONTROL_PLANE_PORT = reserve_control_plane_port()
APP_IDENTIFIER = "com.aventador.automationtool.t313acceptance"
ENVIRONMENT_ID = "t313-acceptance"
TASK_KEY = "task:control:tauri-acceptance"
DEVICE_CREDENTIAL_FILE = "device-credential-v1"


def require_control_plane_port_available() -> None:
    require_reserved_port_still_free(CONTROL_PLANE_PORT)


def require_hidden_tauri_configuration() -> None:
    configuration = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))
    windows = configuration.get("app", {}).get("windows", [])
    if len(windows) != 1 or windows[0].get("visible") is not False:
        raise RuntimeError("T3-13 Tauri acceptance must run with visible=false")


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
        "postgresql+asyncpg://automation_tool_t313:"
        f"{database_password}@127.0.0.1:{database_port}/automation_tool_t313"
    )
    bootstrap_token, bootstrap_public_key = signed_bootstrap()
    environment.update(
        {
            "AUTOMATION_TOOL_DEV_DB_USER": "unused_t313_dev",
            "AUTOMATION_TOOL_DEV_DB_PASSWORD": secrets.token_hex(24),
            "AUTOMATION_TOOL_DEV_DB_NAME": "unused_t313_dev",
            "AUTOMATION_TOOL_DEV_DB_PORT": str(unused_loopback_port()),
            "AUTOMATION_TOOL_TEST_DB_USER": "automation_tool_t313",
            "AUTOMATION_TOOL_TEST_DB_PASSWORD": database_password,
            "AUTOMATION_TOOL_TEST_DB_NAME": "automation_tool_t313",
            "AUTOMATION_TOOL_TEST_DB_PORT": str(database_port),
            "AUTOMATION_TOOL_DATABASE_URL": database_url,
            "AUTOMATION_TOOL_DEMO_ENVIRONMENT_ID": ENVIRONMENT_ID,
            "AUTOMATION_TOOL_DEMO_BOOTSTRAP_PUBLIC_KEY": bootstrap_public_key,
            "AUTOMATION_TOOL_T313_BOOTSTRAP_TOKEN": bootstrap_token,
            "AUTOMATION_TOOL_T313_ENVIRONMENT_ID": ENVIRONMENT_ID,
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
                raise RuntimeError("T3-13 hidden App exited before creating its control Task")
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
                    raise RuntimeError("T3-13 App credential vault is unreadable") from error
                return InstallationId.parse(row[0]), TaskId.parse(row[1]), credential
            if time.monotonic() >= deadline:
                raise RuntimeError("T3-13 hidden App did not create its control Task in time")
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
                raise RuntimeError("T3-13 App Task fixture is not draft")
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
            idempotency_key="task:t313:offer:1",
            deadline_at=now + timedelta(minutes=3),
        )
    finally:
        await database.close()


def exercise_fake_executor(
    credential: str,
    installation_id: InstallationId,
) -> None:
    exchanged = post_json(
        CONTROL_PLANE_PORT,
        "/api/v1/device-sessions",
        credential,
        payload={"capability": DeviceSessionCapability.EXECUTOR_CONNECT.value},
        expected_status=201,
    )
    session_token = exchanged.get("sessionToken")
    if not isinstance(session_token, str):
        raise RuntimeError("T3-13 Executor Session exchange omitted its opaque token")
    client = FakeExecutorClient(
        configuration=FakeExecutorClientConfiguration(
            websocket_url=(f"ws://127.0.0.1:{CONTROL_PLANE_PORT}/api/v1/executors/connect"),
            session_token=session_token,
        ),
        engine=FakeExecutorEngine(
            installation_id=str(installation_id),
            executor_id=str(uuid4()),
            scenario=FakeExecutorScenario.HOLD,
        ),
    )
    if client.run(max_commands=3) != 3:
        raise RuntimeError("T3-13 FakeExecutor did not process offer, pause, and resume")


async def verify_database_state(database_url: str, original: TaskCommandRecord) -> None:
    database = Database.from_url(database_url)
    try:
        async with database.session() as session:
            command_rows = (
                (
                    await session.execute(
                        select(task_commands)
                        .where(task_commands.c.task_id == original.task_id.uuid)
                        .order_by(task_commands.c.sequence)
                    )
                )
                .mappings()
                .all()
            )
            event_rows = (
                (
                    await session.execute(
                        select(task_events)
                        .where(task_events.c.task_id == original.task_id.uuid)
                        .order_by(task_events.c.sequence)
                    )
                )
                .mappings()
                .all()
            )
            task_row = (
                (await session.execute(select(tasks).where(tasks.c.id == original.task_id.uuid)))
                .mappings()
                .one()
            )
            attempt_row = (
                (
                    await session.execute(
                        select(execution_attempts).where(
                            execution_attempts.c.id == original.execution_attempt_id.uuid
                        )
                    )
                )
                .mappings()
                .one()
            )
            capabilities = list(
                await session.scalars(text("select capability from device_sessions order by id"))
            )
        if [row["sequence"] for row in command_rows] != [1, 2, 3]:
            raise RuntimeError("T3-13 command sequence is invalid")
        if [row["command_type"] for row in command_rows] != [
            TaskCommandType.TASK_OFFER.value,
            TaskCommandType.TASK_PAUSE.value,
            TaskCommandType.TASK_RESUME.value,
        ]:
            raise RuntimeError("T3-13 command vocabulary is invalid")
        if any(row["status"] != TaskCommandStatus.ACKNOWLEDGED.value for row in command_rows):
            raise RuntimeError("T3-13 commands were not acknowledged")
        if [row["event_type"] for row in event_rows] != [
            TaskEventType.TASK_STARTED.value,
            TaskEventType.STEP_STARTED.value,
            TaskEventType.TASK_PAUSED.value,
            TaskEventType.TASK_RESUMED.value,
        ]:
            raise RuntimeError("T3-13 event timeline is invalid")
        if (
            task_row["status"] != TaskStatus.RUNNING.value
            or task_row["revision"] != 5
            or task_row["last_event_sequence"] != 4
            or attempt_row["status"] != ExecutionAttemptStatus.RUNNING.value
            or attempt_row["revision"] != 4
        ):
            raise RuntimeError("T3-13 final Task or Attempt projection is invalid")
        if capabilities.count(DeviceSessionCapability.EXECUTOR_CONNECT.value) != 1 or any(
            capability
            not in {
                DeviceSessionCapability.APP_CONTROL_PLANE.value,
                DeviceSessionCapability.EXECUTOR_CONNECT.value,
            }
            for capability in capabilities
        ):
            raise RuntimeError("T3-13 used an unexpected Session capability")
    finally:
        await database.close()


def main() -> None:
    require_control_plane_port_available()
    require_hidden_tauri_configuration()
    private_app_data = app_data_directory()
    if private_app_data.exists():
        raise RuntimeError("Refusing to reuse an existing T3-13 App data directory")
    prepare_startup_gate(private_app_data)

    project_name = f"automation-tool-t313-{os.getpid()}"
    database_port = unused_loopback_port()
    environment, database_url = isolated_environment(database_port)
    compose = compose_command(project_name)
    server: subprocess.Popen[bytes] | None = None
    app_process: subprocess.Popen[bytes] | None = None

    try:
        print("[T3-13] Starting isolated PostgreSQL")
        subprocess.run(
            [*compose, "up", "--detach", "--wait", "postgres-test"],
            check=True,
            cwd=REPOSITORY_ROOT,
            env=environment,
        )
        print("[T3-13] Applying the production Alembic migration chain")
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            check=True,
            cwd=BACKEND_ROOT,
            env=environment,
        )
        print("[T3-13] Starting the real Uvicorn boundary in the background")
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
        print("[T3-13] Running the real Tauri App with visible=false")
        app_process = subprocess.Popen(
            ["pnpm", "test:task-control-tauri"],
            cwd=FRONTEND_ROOT,
            env=environment,
        )
        installation_id, task_id, credential = asyncio.run(
            wait_for_app_task(database_url, private_app_data, app_process)
        )
        original = asyncio.run(seed_attempt_and_offer(database_url, installation_id, task_id))
        exercise_fake_executor(credential, installation_id)
        try:
            app_exit = app_process.wait(timeout=180)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("T3-13 hidden App acceptance did not finish") from error
        if app_exit != 0:
            raise RuntimeError("T3-13 hidden App acceptance failed")
        app_process = None
        verify_app_private_data(private_app_data)
        asyncio.run(verify_database_state(database_url, original))
        print("[T3-13] Hidden-App pause/resume acceptance passed")
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
