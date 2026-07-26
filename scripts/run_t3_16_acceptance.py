#!/usr/bin/env python3
"""Run T3-16 through the hidden workbench UI and formal backend/Executor paths."""

from __future__ import annotations

import asyncio
import json
import os
import queue
import secrets
import shutil
import subprocess
import sys
import threading
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
from run_t3_14_acceptance import (
    CONFIRMED_TASK_REVISION,
    fake_executor_client,
    seed_attempt_and_offer,
    seed_task_confirmation,
)
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from automation_tool.control_plane.application.task_command_delivery import TaskCommandRecord
from automation_tool.control_plane.domain import (
    InstallationId,
    TaskCommandStatus,
    TaskCommandType,
    TaskEventType,
    TaskId,
    TaskStatus,
)
from automation_tool.control_plane.infrastructure.database import (
    Database,
    execution_attempts,
    task_commands,
    task_events,
    tasks,
)
from automation_tool.protocol import MAX_EXECUTOR_MESSAGE_BYTES

TAURI_CONFIG = FRONTEND_ROOT / "src-tauri" / "tauri.workbench-e2e.conf.json"
CONTROL_PLANE_PORT = reserve_control_plane_port()
APP_IDENTIFIER = "com.aventador.automationtool.t316acceptance"
ENVIRONMENT_ID = "t316-acceptance"
TASK_KEY = "task:workbench:tauri-acceptance"
DEVICE_CREDENTIAL_FILE = "device-credential-v1"


def require_control_plane_port_available() -> None:
    require_reserved_port_still_free(CONTROL_PLANE_PORT)


def require_hidden_tauri_configuration() -> None:
    configuration = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))
    windows = configuration.get("app", {}).get("windows", [])
    if len(windows) != 1 or windows[0].get("visible") is not False:
        raise RuntimeError("T3-16 Tauri acceptance must run with visible=false")


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
        "postgresql+asyncpg://automation_tool_t316:"
        f"{database_password}@127.0.0.1:{database_port}/automation_tool_t316"
    )
    bootstrap_token, bootstrap_public_key = signed_bootstrap()
    environment.update(
        {
            "AUTOMATION_TOOL_DEV_DB_USER": "unused_t316_dev",
            "AUTOMATION_TOOL_DEV_DB_PASSWORD": secrets.token_hex(24),
            "AUTOMATION_TOOL_DEV_DB_NAME": "unused_t316_dev",
            "AUTOMATION_TOOL_DEV_DB_PORT": str(unused_loopback_port()),
            "AUTOMATION_TOOL_TEST_DB_USER": "automation_tool_t316",
            "AUTOMATION_TOOL_TEST_DB_PASSWORD": database_password,
            "AUTOMATION_TOOL_TEST_DB_NAME": "automation_tool_t316",
            "AUTOMATION_TOOL_TEST_DB_PORT": str(database_port),
            "AUTOMATION_TOOL_DATABASE_URL": database_url,
            "AUTOMATION_TOOL_DEMO_ENVIRONMENT_ID": ENVIRONMENT_ID,
            "AUTOMATION_TOOL_DEMO_BOOTSTRAP_PUBLIC_KEY": bootstrap_public_key,
            "AUTOMATION_TOOL_T316_BOOTSTRAP_TOKEN": bootstrap_token,
            "AUTOMATION_TOOL_T316_ENVIRONMENT_ID": ENVIRONMENT_ID,
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
                raise RuntimeError("T3-16 hidden App exited before creating its Task")
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
                    raise RuntimeError("T3-16 App credential vault is unreadable") from error
                return InstallationId.parse(row[0]), TaskId.parse(row[1]), credential
            if time.monotonic() >= deadline:
                raise RuntimeError("T3-16 hidden App did not create its Task in time")
            await asyncio.sleep(0.05)
    finally:
        await engine.dispose()


async def verify_database_state(database_url: str, offer: TaskCommandRecord) -> None:
    database = Database.from_url(database_url)
    try:
        async with database.session() as session:
            commands = (
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
            events = (
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
                (await session.execute(select(tasks).where(tasks.c.id == offer.task_id.uuid)))
                .mappings()
                .one()
            )
            attempt = (
                (
                    await session.execute(
                        select(execution_attempts).where(
                            execution_attempts.c.id == offer.execution_attempt_id.uuid
                        )
                    )
                )
                .mappings()
                .one()
            )
            capabilities = list(
                await session.scalars(text("select capability from device_sessions order by id"))
            )
        if [row["sequence"] for row in commands] != [1, 2]:
            raise RuntimeError("T3-16 command sequence is invalid")
        if [row["command_type"] for row in commands] != [
            TaskCommandType.TASK_OFFER.value,
            TaskCommandType.TASK_EMERGENCY_STOP.value,
        ] or any(row["status"] != TaskCommandStatus.ACKNOWLEDGED.value for row in commands):
            raise RuntimeError("T3-16 workbench commands were not acknowledged")
        if [row["event_type"] for row in events] != [
            TaskEventType.TASK_STARTED.value,
            TaskEventType.STEP_STARTED.value,
            TaskEventType.TASK_OUTCOME_UNCERTAIN.value,
        ]:
            raise RuntimeError("T3-16 event timeline is invalid")
        if (
            task["status"] != TaskStatus.OUTCOME_UNCERTAIN.value
            # The emergency stop projects the Task to CANCELLING the moment the
            # command is enqueued, and that projection advances the revision
            # without committing an event of its own.
            or task["revision"] != CONFIRMED_TASK_REVISION + 1 + len(events)
            or task["last_event_sequence"] != 3
            or attempt["finished_at"] is None
        ):
            raise RuntimeError(
                "T3-16 final Task projection is invalid: task "
                f"status={task['status']} revision={task['revision']} "
                f"last_event_sequence={task['last_event_sequence']}; attempt "
                f"status={attempt['status']} finished={attempt['finished_at'] is not None}"
            )
        if capabilities.count("executor.connect") != 1 or any(
            capability not in {"app.control-plane", "executor.connect"}
            for capability in capabilities
        ):
            raise RuntimeError(
                f"T3-16 used an unexpected Session capability: {sorted(capabilities)}"
            )
    finally:
        await database.close()


def main() -> None:
    require_control_plane_port_available()
    require_hidden_tauri_configuration()
    private_app_data = app_data_directory()
    if private_app_data.exists():
        raise RuntimeError("Refusing to reuse an existing T3-16 App data directory")
    prepare_startup_gate(private_app_data)

    project_name = f"automation-tool-t316-{os.getpid()}"
    database_port = unused_loopback_port()
    environment, database_url = isolated_environment(database_port)
    compose = compose_command(project_name)
    server: subprocess.Popen[bytes] | None = None
    app_process: subprocess.Popen[bytes] | None = None
    executor_thread: threading.Thread | None = None
    executor_result: queue.Queue[object] = queue.Queue()

    try:
        print("[T3-16] Starting isolated PostgreSQL")
        subprocess.run(
            [*compose, "up", "--detach", "--wait", "postgres-test"],
            check=True,
            cwd=REPOSITORY_ROOT,
            env=environment,
        )
        print("[T3-16] Applying the production Alembic migration chain")
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            check=True,
            cwd=BACKEND_ROOT,
            env=environment,
        )
        print("[T3-16] Starting the real Uvicorn boundary in the background")
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
        print("[T3-16] Running the real Tauri App with visible=false")
        app_process = subprocess.Popen(
            ["pnpm", "test:workbench-tauri"],
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
        offer = asyncio.run(
            seed_attempt_and_offer(
                database_url,
                installation_id,
                task_id,
                label="workbench",
                confirmed_target_revision=True,
            )
        )
        client = fake_executor_client(credential, installation_id)

        def run_executor() -> None:
            try:
                executor_result.put(client.run(max_commands=2))
            except Exception as error:
                executor_result.put(error)

        executor_thread = threading.Thread(target=run_executor, daemon=True)
        executor_thread.start()
        try:
            app_exit = app_process.wait(timeout=240)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("T3-16 hidden App acceptance did not finish") from error
        if app_exit != 0:
            raise RuntimeError("T3-16 hidden App acceptance failed")
        app_process = None
        executor_thread.join(timeout=10)
        if executor_thread.is_alive():
            raise RuntimeError("T3-16 FakeExecutor did not finish")
        processed = executor_result.get_nowait()
        if isinstance(processed, Exception):
            raise processed
        if processed != 2:
            raise RuntimeError("T3-16 FakeExecutor did not process offer and emergency stop")
        verify_app_private_data(private_app_data)
        asyncio.run(verify_database_state(database_url, offer))
        print("[T3-16] Hidden-App workbench UI and emergency-stop acceptance passed")
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
