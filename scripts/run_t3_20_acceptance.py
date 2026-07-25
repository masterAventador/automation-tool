#!/usr/bin/env python3
"""Run T3-20 through a hidden App across a real Control Plane restart."""

from __future__ import annotations

import asyncio
import json
import os
import queue
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

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
from run_t3_14_acceptance import seed_attempt_and_offer
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from automation_tool.control_plane.application.device_sessions import (
    DeviceSessionCapability,
)
from automation_tool.control_plane.application.task_command_delivery import (
    TaskCommandRecord,
)
from automation_tool.control_plane.domain import (
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
    douyin_search_exposure_definitions,
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

TAURI_CONFIG = FRONTEND_ROOT / "src-tauri" / "tauri.task-restart-e2e.conf.json"
CONTROL_PLANE_PORT = reserve_control_plane_port()
APP_IDENTIFIER = "com.aventador.automationtool.t320acceptance"
ENVIRONMENT_ID = "t320-acceptance"
TASK_KEYWORD = "T3-20 重启恢复"
DEVICE_CREDENTIAL_FILE = "device-credential-v1"


@dataclass(frozen=True, slots=True)
class RestartCheckpoint:
    task_id: UUID
    attempt_id: UUID
    command_ids: tuple[UUID, UUID]
    event_message_ids: tuple[UUID, UUID]
    task_created_at: datetime


def require_control_plane_port_available() -> None:
    require_reserved_port_still_free(CONTROL_PLANE_PORT)


def require_hidden_tauri_configuration() -> None:
    configuration = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))
    windows = configuration.get("app", {}).get("windows", [])
    if len(windows) != 1 or windows[0].get("visible") is not False:
        raise RuntimeError("T3-20 Tauri acceptance must run with visible=false")


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


def isolated_environment(
    database_port: int,
    signal_directory: Path,
) -> tuple[dict[str, str], str, dict[str, Path]]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("AUTOMATION_TOOL_")
    }
    database_password = secrets.token_hex(24)
    database_url = (
        "postgresql+asyncpg://automation_tool_t320:"
        f"{database_password}@127.0.0.1:{database_port}/automation_tool_t320"
    )
    bootstrap_token, bootstrap_public_key = signed_bootstrap()
    signals = {
        "ready": signal_directory / "ready",
        "down": signal_directory / "down",
        "unavailable": signal_directory / "unavailable",
        "up": signal_directory / "up",
    }
    environment.update(
        {
            "AUTOMATION_TOOL_DEV_DB_USER": "unused_t320_dev",
            "AUTOMATION_TOOL_DEV_DB_PASSWORD": secrets.token_hex(24),
            "AUTOMATION_TOOL_DEV_DB_NAME": "unused_t320_dev",
            "AUTOMATION_TOOL_DEV_DB_PORT": str(unused_loopback_port()),
            "AUTOMATION_TOOL_TEST_DB_USER": "automation_tool_t320",
            "AUTOMATION_TOOL_TEST_DB_PASSWORD": database_password,
            "AUTOMATION_TOOL_TEST_DB_NAME": "automation_tool_t320",
            "AUTOMATION_TOOL_TEST_DB_PORT": str(database_port),
            "AUTOMATION_TOOL_DATABASE_URL": database_url,
            "AUTOMATION_TOOL_DEMO_ENVIRONMENT_ID": ENVIRONMENT_ID,
            "AUTOMATION_TOOL_DEMO_BOOTSTRAP_PUBLIC_KEY": bootstrap_public_key,
            "AUTOMATION_TOOL_T320_BOOTSTRAP_TOKEN": bootstrap_token,
            "AUTOMATION_TOOL_T320_ENVIRONMENT_ID": ENVIRONMENT_ID,
            "AUTOMATION_TOOL_T320_READY_FILE": str(signals["ready"]),
            "AUTOMATION_TOOL_T320_DOWN_FILE": str(signals["down"]),
            "AUTOMATION_TOOL_T320_UNAVAILABLE_FILE": str(signals["unavailable"]),
            "AUTOMATION_TOOL_T320_UP_FILE": str(signals["up"]),
        }
    )
    return (
        startup_gate_environment(environment, control_plane_port=CONTROL_PLANE_PORT),
        database_url,
        signals,
    )


def start_control_plane(environment: dict[str, str]) -> subprocess.Popen[bytes]:
    process = subprocess.Popen(
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
    return process


def stop_control_plane(process: subprocess.Popen[bytes]) -> None:
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    require_port_closed(CONTROL_PLANE_PORT)


async def wait_for_task(
    database_url: str,
    private_app_data: Path,
    app_process: subprocess.Popen[bytes],
) -> tuple[InstallationId, TaskId, str]:
    engine = create_async_engine(database_url)
    deadline = time.monotonic() + 120
    try:
        while True:
            if app_process.poll() is not None:
                raise RuntimeError("T3-20 hidden App exited before creating its Task")
            async with engine.connect() as connection:
                row = (
                    await connection.execute(
                        select(
                            douyin_search_exposure_definitions.c.installation_id,
                            douyin_search_exposure_definitions.c.task_id,
                        ).where(
                            douyin_search_exposure_definitions.c.search_keyword
                            == TASK_KEYWORD
                        )
                    )
                ).one_or_none()
            credential_path = private_app_data / DEVICE_CREDENTIAL_FILE
            if row is not None and credential_path.is_file():
                try:
                    credential = credential_path.read_text(encoding="ascii")
                except (OSError, UnicodeError) as error:
                    raise RuntimeError(
                        "T3-20 App credential vault is unreadable"
                    ) from error
                return (
                    InstallationId.parse(str(row[0])),
                    TaskId.parse(str(row[1])),
                    credential,
                )
            if time.monotonic() >= deadline:
                raise RuntimeError("T3-20 hidden App did not create the expected Task")
            await asyncio.sleep(0.05)
    finally:
        await engine.dispose()


def fake_executor_client(
    credential: str,
    installation_id: InstallationId,
) -> FakeExecutorClient:
    exchanged = post_json(
        CONTROL_PLANE_PORT,
        "/api/v1/device-sessions",
        credential,
        payload={"capability": DeviceSessionCapability.EXECUTOR_CONNECT.value},
        expected_status=201,
    )
    session_token = exchanged.get("sessionToken")
    if not isinstance(session_token, str):
        raise RuntimeError("T3-20 Executor Session exchange omitted its opaque token")
    return FakeExecutorClient(
        configuration=FakeExecutorClientConfiguration(
            websocket_url=(
                f"ws://127.0.0.1:{CONTROL_PLANE_PORT}/api/v1/executors/connect"
            ),
            session_token=session_token,
        ),
        engine=FakeExecutorEngine(
            installation_id=str(installation_id),
            executor_id=str(uuid4()),
            scenario=FakeExecutorScenario.HOLD,
        ),
    )


def wait_for_signal(
    path: Path,
    app_process: subprocess.Popen[bytes],
    *,
    label: str,
) -> None:
    deadline = time.monotonic() + 120
    while not path.is_file():
        if app_process.poll() is not None:
            raise RuntimeError(f"T3-20 hidden App exited before the {label} signal")
        if time.monotonic() >= deadline:
            raise RuntimeError(f"T3-20 timed out waiting for the {label} signal")
        time.sleep(0.05)


async def read_restart_checkpoint(
    database_url: str,
    offer: TaskCommandRecord,
) -> RestartCheckpoint:
    database = Database.from_url(database_url)
    try:
        async with database.session() as session:
            command_rows = (
                (
                    await session.execute(
                        select(task_commands)
                        .where(task_commands.c.task_id == offer.task_id.uuid)
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
                        .where(task_events.c.task_id == offer.task_id.uuid)
                        .order_by(task_events.c.sequence)
                    )
                )
                .mappings()
                .all()
            )
            task = (
                (
                    await session.execute(
                        select(tasks).where(tasks.c.id == offer.task_id.uuid)
                    )
                )
                .mappings()
                .one()
            )
        if [row["command_type"] for row in command_rows] != [
            TaskCommandType.TASK_OFFER.value,
            TaskCommandType.TASK_CANCEL.value,
        ] or [row["status"] for row in command_rows] != [
            TaskCommandStatus.ACKNOWLEDGED.value,
            TaskCommandStatus.PENDING.value,
        ]:
            raise RuntimeError("T3-20 pre-restart Command checkpoint is invalid")
        if [row["event_type"] for row in event_rows] != [
            TaskEventType.TASK_STARTED.value,
            TaskEventType.STEP_STARTED.value,
        ]:
            raise RuntimeError("T3-20 pre-restart Event checkpoint is invalid")
        if (
            task["status"] != TaskStatus.CANCELLING.value
            or task["revision"] != 4
            or task["last_event_sequence"] != 2
        ):
            raise RuntimeError("T3-20 pre-restart Task checkpoint is invalid")
        return RestartCheckpoint(
            task_id=task["id"],
            attempt_id=offer.execution_attempt_id.uuid,
            command_ids=(command_rows[0]["message_id"], command_rows[1]["message_id"]),
            event_message_ids=(
                event_rows[0]["source_message_id"],
                event_rows[1]["source_message_id"],
            ),
            task_created_at=task["created_at"],
        )
    finally:
        await database.close()


async def verify_database_state(
    database_url: str,
    checkpoint: RestartCheckpoint,
) -> None:
    database = Database.from_url(database_url)
    try:
        async with database.session() as session:
            command_rows = (
                (
                    await session.execute(
                        select(task_commands)
                        .where(task_commands.c.task_id == checkpoint.task_id)
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
                        .where(task_events.c.task_id == checkpoint.task_id)
                        .order_by(task_events.c.sequence)
                    )
                )
                .mappings()
                .all()
            )
            task = (
                (
                    await session.execute(
                        select(tasks).where(tasks.c.id == checkpoint.task_id)
                    )
                )
                .mappings()
                .one()
            )
            attempt = (
                (
                    await session.execute(
                        select(execution_attempts).where(
                            execution_attempts.c.id == checkpoint.attempt_id
                        )
                    )
                )
                .mappings()
                .one()
            )
            definitions = list(
                await session.scalars(
                    select(douyin_search_exposure_definitions.c.search_keyword)
                )
            )
            capabilities = list(
                await session.scalars(
                    text("select capability from device_sessions order by id")
                )
            )
        if tuple(row["message_id"] for row in command_rows) != checkpoint.command_ids:
            raise RuntimeError("T3-20 replaced a persistent Command across restart")
        if [row["command_type"] for row in command_rows] != [
            TaskCommandType.TASK_OFFER.value,
            TaskCommandType.TASK_CANCEL.value,
        ] or any(
            row["status"] != TaskCommandStatus.ACKNOWLEDGED.value
            for row in command_rows
        ):
            raise RuntimeError("T3-20 Commands did not converge after restart")
        if tuple(
            row["source_message_id"] for row in event_rows[:2]
        ) != checkpoint.event_message_ids or [
            row["event_type"] for row in event_rows
        ] != [
            TaskEventType.TASK_STARTED.value,
            TaskEventType.STEP_STARTED.value,
            TaskEventType.TASK_CANCELLED.value,
        ]:
            raise RuntimeError("T3-20 Events did not persist and converge")
        if (
            task["created_at"] != checkpoint.task_created_at
            or task["status"] != TaskStatus.CANCELLED.value
            or task["revision"] != 5
            or task["last_event_sequence"] != 3
            or attempt["status"] != ExecutionAttemptStatus.CANCELLED.value
            or attempt["finished_at"] is None
        ):
            raise RuntimeError("T3-20 final Task projection is invalid")
        if definitions != [TASK_KEYWORD]:
            raise RuntimeError("T3-20 did not retain the UI-created definition")
        if capabilities.count("executor.connect") != 1 or any(
            capability not in {"app.control-plane", "executor.connect"}
            for capability in capabilities
        ):
            raise RuntimeError("T3-20 used an unexpected Session capability")
    finally:
        await database.close()


def main() -> None:
    require_control_plane_port_available()
    require_hidden_tauri_configuration()
    private_app_data = app_data_directory()
    if private_app_data.exists():
        raise RuntimeError("Refusing to reuse an existing T3-20 App data directory")
    prepare_startup_gate(private_app_data)

    project_name = f"automation-tool-t320-{os.getpid()}"
    database_port = unused_loopback_port()
    signal_directory = Path(tempfile.mkdtemp(prefix="automation-tool-t320-"))
    environment, database_url, signals = isolated_environment(
        database_port, signal_directory
    )
    compose = compose_command(project_name)
    server: subprocess.Popen[bytes] | None = None
    app_process: subprocess.Popen[bytes] | None = None
    recovery_thread: threading.Thread | None = None
    recovery_result: queue.Queue[object] = queue.Queue()

    try:
        print("[T3-20] Starting isolated PostgreSQL")
        subprocess.run(
            [*compose, "up", "--detach", "--wait", "postgres-test"],
            check=True,
            cwd=REPOSITORY_ROOT,
            env=environment,
        )
        print("[T3-20] Applying the production Alembic migration chain")
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            check=True,
            cwd=BACKEND_ROOT,
            env=environment,
        )
        print("[T3-20] Starting the first real Uvicorn process")
        server = start_control_plane(environment)
        print("[T3-20] Running the real Tauri App with visible=false")
        app_process = subprocess.Popen(
            ["pnpm", "test:task-restart-tauri"],
            cwd=FRONTEND_ROOT,
            env=environment,
        )

        installation_id, task_id, credential = asyncio.run(
            wait_for_task(database_url, private_app_data, app_process)
        )
        offer = asyncio.run(
            seed_attempt_and_offer(
                database_url,
                installation_id,
                task_id,
                label="task-restart",
            )
        )
        executor = fake_executor_client(credential, installation_id)
        if executor.run(max_commands=1) != 1:
            raise RuntimeError("T3-20 FakeExecutor did not accept the initial offer")

        wait_for_signal(signals["ready"], app_process, label="restart-ready")
        checkpoint = asyncio.run(read_restart_checkpoint(database_url, offer))
        print("[T3-20] Stopping the Control Plane with a pending Command")
        stop_control_plane(server)
        server = None
        signals["down"].write_text("down\n", encoding="utf-8")
        wait_for_signal(signals["unavailable"], app_process, label="unavailable UI")

        def reconnect_executor() -> None:
            try:
                recovery_result.put(
                    executor.run_reconnecting(
                        max_commands=1,
                        max_reconnects=100,
                        reconnect_delay=timedelta(milliseconds=100),
                    )
                )
            except Exception as error:
                recovery_result.put(error)

        recovery_thread = threading.Thread(target=reconnect_executor, daemon=True)
        recovery_thread.start()
        time.sleep(0.25)
        print("[T3-20] Restarting the Control Plane against the same PostgreSQL")
        server = start_control_plane(environment)
        signals["up"].write_text("up\n", encoding="utf-8")

        recovery_thread.join(timeout=180)
        if recovery_thread.is_alive():
            raise RuntimeError("T3-20 FakeExecutor reconnect did not finish")
        recovered = recovery_result.get_nowait()
        if isinstance(recovered, Exception):
            raise recovered
        if recovered != 1:
            raise RuntimeError(
                "T3-20 FakeExecutor processed an unexpected command count"
            )

        try:
            app_exit = app_process.wait(timeout=300)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("T3-20 hidden App acceptance did not finish") from error
        if app_exit != 0:
            raise RuntimeError("T3-20 hidden App acceptance failed")
        app_process = None
        verify_app_private_data(private_app_data)
        asyncio.run(verify_database_state(database_url, checkpoint))
        print("[T3-20] Hidden-App Control Plane restart recovery passed")
    finally:
        if app_process is not None and app_process.poll() is None:
            app_process.terminate()
            try:
                app_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                app_process.kill()
                app_process.wait(timeout=5)
        if server is not None and server.poll() is None:
            stop_control_plane(server)
        if recovery_thread is not None and recovery_thread.is_alive():
            recovery_thread.join(timeout=15)
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
        if signal_directory.exists():
            shutil.rmtree(signal_directory)
        require_port_closed(CONTROL_PLANE_PORT)
        require_port_closed(database_port)


if __name__ == "__main__":
    main()
