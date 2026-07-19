#!/usr/bin/env python3
"""Run T3-19 through one hidden App and the formal Task lifecycle paths."""

from __future__ import annotations

import asyncio
import json
import os
import queue
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
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

TAURI_CONFIG = FRONTEND_ROOT / "src-tauri" / "tauri.task-lifecycle-e2e.conf.json"
CONTROL_PLANE_PORT = 8765
APP_IDENTIFIER = "com.aventador.automationtool.t319acceptance"
ENVIRONMENT_ID = "t319-acceptance"
CONTROLLED_KEYWORD = "T3-19 取消链路"
SUCCEEDED_KEYWORD = "T3-19 成功链路"
DEVICE_CREDENTIAL_FILE = "device-credential-v1"


def require_control_plane_port_available() -> None:
    with socket.socket() as listener:
        try:
            listener.bind(("127.0.0.1", CONTROL_PLANE_PORT))
        except OSError as error:
            raise RuntimeError("T3-19 requires an unused Control Plane port") from error


def require_hidden_tauri_configuration() -> None:
    configuration = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))
    windows = configuration.get("app", {}).get("windows", [])
    if len(windows) != 1 or windows[0].get("visible") is not False:
        raise RuntimeError("T3-19 Tauri acceptance must run with visible=false")


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
        key: value
        for key, value in os.environ.items()
        if not key.startswith("AUTOMATION_TOOL_")
    }
    database_password = secrets.token_hex(24)
    database_url = (
        "postgresql+asyncpg://automation_tool_t319:"
        f"{database_password}@127.0.0.1:{database_port}/automation_tool_t319"
    )
    bootstrap_token, bootstrap_public_key = signed_bootstrap()
    environment.update(
        {
            "AUTOMATION_TOOL_DEV_DB_USER": "unused_t319_dev",
            "AUTOMATION_TOOL_DEV_DB_PASSWORD": secrets.token_hex(24),
            "AUTOMATION_TOOL_DEV_DB_NAME": "unused_t319_dev",
            "AUTOMATION_TOOL_DEV_DB_PORT": str(unused_loopback_port()),
            "AUTOMATION_TOOL_TEST_DB_USER": "automation_tool_t319",
            "AUTOMATION_TOOL_TEST_DB_PASSWORD": database_password,
            "AUTOMATION_TOOL_TEST_DB_NAME": "automation_tool_t319",
            "AUTOMATION_TOOL_TEST_DB_PORT": str(database_port),
            "AUTOMATION_TOOL_DATABASE_URL": database_url,
            "AUTOMATION_TOOL_DEMO_ENVIRONMENT_ID": ENVIRONMENT_ID,
            "AUTOMATION_TOOL_DEMO_BOOTSTRAP_PUBLIC_KEY": bootstrap_public_key,
            "AUTOMATION_TOOL_T319_BOOTSTRAP_TOKEN": bootstrap_token,
            "AUTOMATION_TOOL_T319_ENVIRONMENT_ID": ENVIRONMENT_ID,
        }
    )
    return environment, database_url


async def wait_for_task(
    database_url: str,
    private_app_data: Path,
    app_process: subprocess.Popen[bytes],
    keyword: str,
) -> tuple[InstallationId, TaskId, str]:
    engine = create_async_engine(database_url)
    deadline = time.monotonic() + 120
    try:
        while True:
            if app_process.poll() is not None:
                raise RuntimeError("T3-19 hidden App exited before creating its Task")
            async with engine.connect() as connection:
                row = (
                    await connection.execute(
                        select(
                            douyin_search_exposure_definitions.c.installation_id,
                            douyin_search_exposure_definitions.c.task_id,
                        ).where(
                            douyin_search_exposure_definitions.c.search_keyword
                            == keyword
                        )
                    )
                ).one_or_none()
            credential_path = private_app_data / DEVICE_CREDENTIAL_FILE
            if row is not None and credential_path.is_file():
                try:
                    credential = credential_path.read_text(encoding="ascii")
                except (OSError, UnicodeError) as error:
                    raise RuntimeError(
                        "T3-19 App credential vault is unreadable"
                    ) from error
                return (
                    InstallationId.parse(str(row[0])),
                    TaskId.parse(str(row[1])),
                    credential,
                )
            if time.monotonic() >= deadline:
                raise RuntimeError("T3-19 hidden App did not create the expected Task")
            await asyncio.sleep(0.05)
    finally:
        await engine.dispose()


def fake_executor_client(
    credential: str,
    installation_id: InstallationId,
    scenario: FakeExecutorScenario,
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
        raise RuntimeError("T3-19 Executor Session exchange omitted its opaque token")
    return FakeExecutorClient(
        configuration=FakeExecutorClientConfiguration(
            websocket_url=f"ws://127.0.0.1:{CONTROL_PLANE_PORT}/api/v1/executors/connect",
            session_token=session_token,
        ),
        engine=FakeExecutorEngine(
            installation_id=str(installation_id),
            executor_id=str(uuid4()),
            scenario=scenario,
        ),
    )


def start_executor(
    client: FakeExecutorClient,
    max_commands: int,
) -> tuple[threading.Thread, queue.Queue[object]]:
    result: queue.Queue[object] = queue.Queue()

    def run() -> None:
        try:
            result.put(client.run(max_commands=max_commands))
        except Exception as error:
            result.put(error)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, result


def finish_executor(
    thread: threading.Thread,
    result: queue.Queue[object],
    expected_commands: int,
) -> None:
    thread.join(timeout=180)
    if thread.is_alive():
        raise RuntimeError("T3-19 FakeExecutor did not finish")
    processed = result.get_nowait()
    if isinstance(processed, Exception):
        raise processed
    if processed != expected_commands:
        raise RuntimeError("T3-19 FakeExecutor processed an unexpected command count")


async def verify_database_state(
    database_url: str,
    controlled_offer: TaskCommandRecord,
    succeeded_offer: TaskCommandRecord,
) -> None:
    database = Database.from_url(database_url)
    expectations = (
        (
            controlled_offer,
            [
                TaskCommandType.TASK_OFFER,
                TaskCommandType.TASK_PAUSE,
                TaskCommandType.TASK_RESUME,
                TaskCommandType.TASK_CANCEL,
            ],
            [
                TaskEventType.TASK_STARTED,
                TaskEventType.STEP_STARTED,
                TaskEventType.TASK_PAUSED,
                TaskEventType.TASK_RESUMED,
                TaskEventType.TASK_CANCELLED,
            ],
            TaskStatus.CANCELLED,
            ExecutionAttemptStatus.CANCELLED,
            7,
        ),
        (
            succeeded_offer,
            [TaskCommandType.TASK_OFFER],
            [
                TaskEventType.TASK_STARTED,
                TaskEventType.STEP_STARTED,
                TaskEventType.STEP_PROGRESS,
                TaskEventType.STEP_COMPLETED,
                TaskEventType.TASK_COMPLETED,
            ],
            TaskStatus.SUCCEEDED,
            ExecutionAttemptStatus.SUCCEEDED,
            6,
        ),
    )
    try:
        async with database.session() as session:
            for (
                offer,
                command_types,
                event_types,
                task_status,
                attempt_status,
                revision,
            ) in expectations:
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
                    (
                        await session.execute(
                            select(tasks).where(tasks.c.id == offer.task_id.uuid)
                        )
                    )
                    .mappings()
                    .one()
                )
                attempt = (
                    (
                        await session.execute(
                            select(execution_attempts).where(
                                execution_attempts.c.id
                                == offer.execution_attempt_id.uuid
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
                if [row["command_type"] for row in commands] != [
                    command_type.value for command_type in command_types
                ] or any(
                    row["status"] != TaskCommandStatus.ACKNOWLEDGED.value
                    for row in commands
                ):
                    raise RuntimeError(
                        "T3-19 Task commands were not exactly acknowledged"
                    )
                if [row["event_type"] for row in events] != [
                    event_type.value for event_type in event_types
                ]:
                    raise RuntimeError("T3-19 persisted event timeline is invalid")
                if (
                    task["status"] != task_status.value
                    or task["revision"] != revision
                    or task["last_event_sequence"] != len(event_types)
                    or attempt["status"] != attempt_status.value
                    or attempt["finished_at"] is None
                ):
                    raise RuntimeError("T3-19 final Task projection is invalid")
            definitions = list(
                await session.scalars(
                    select(
                        douyin_search_exposure_definitions.c.search_keyword
                    ).order_by(douyin_search_exposure_definitions.c.search_keyword)
                )
            )
            capabilities = list(
                await session.scalars(
                    text("select capability from device_sessions order by id")
                )
            )
        if definitions != sorted([CONTROLLED_KEYWORD, SUCCEEDED_KEYWORD]):
            raise RuntimeError(
                "T3-19 did not persist exactly two UI-created definitions"
            )
        if capabilities.count("executor.connect") != 2 or any(
            capability not in {"app.control-plane", "executor.connect"}
            for capability in capabilities
        ):
            raise RuntimeError("T3-19 used an unexpected Session capability")
    finally:
        await database.close()


def main() -> None:
    require_control_plane_port_available()
    require_hidden_tauri_configuration()
    private_app_data = app_data_directory()
    if private_app_data.exists():
        raise RuntimeError("Refusing to reuse an existing T3-19 App data directory")

    project_name = f"automation-tool-t319-{os.getpid()}"
    database_port = unused_loopback_port()
    environment, database_url = isolated_environment(database_port)
    compose = compose_command(project_name)
    server: subprocess.Popen[bytes] | None = None
    app_process: subprocess.Popen[bytes] | None = None

    try:
        print("[T3-19] Starting isolated PostgreSQL")
        subprocess.run(
            [*compose, "up", "--detach", "--wait", "postgres-test"],
            check=True,
            cwd=REPOSITORY_ROOT,
            env=environment,
        )
        print("[T3-19] Applying the production Alembic migration chain")
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            check=True,
            cwd=BACKEND_ROOT,
            env=environment,
        )
        print("[T3-19] Starting the real Uvicorn boundary in the background")
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
        print("[T3-19] Running the real Tauri App with visible=false")
        app_process = subprocess.Popen(
            ["pnpm", "test:task-lifecycle-tauri"],
            cwd=FRONTEND_ROOT,
            env=environment,
        )

        installation_id, controlled_task_id, credential = asyncio.run(
            wait_for_task(
                database_url, private_app_data, app_process, CONTROLLED_KEYWORD
            )
        )
        controlled_offer = asyncio.run(
            seed_attempt_and_offer(
                database_url,
                installation_id,
                controlled_task_id,
                label="task-lifecycle-controlled",
            )
        )
        controlled_thread, controlled_result = start_executor(
            fake_executor_client(
                credential, installation_id, FakeExecutorScenario.HOLD
            ),
            4,
        )
        finish_executor(controlled_thread, controlled_result, 4)

        second_installation_id, succeeded_task_id, second_credential = asyncio.run(
            wait_for_task(
                database_url, private_app_data, app_process, SUCCEEDED_KEYWORD
            )
        )
        if second_installation_id != installation_id or second_credential != credential:
            raise RuntimeError("T3-19 App lifecycle escaped its Installation vault")
        succeeded_offer = asyncio.run(
            seed_attempt_and_offer(
                database_url,
                installation_id,
                succeeded_task_id,
                label="task-lifecycle-succeeded",
            )
        )
        succeeded_thread, succeeded_result = start_executor(
            fake_executor_client(
                credential, installation_id, FakeExecutorScenario.SUCCEED
            ),
            1,
        )
        finish_executor(succeeded_thread, succeeded_result, 1)

        try:
            app_exit = app_process.wait(timeout=300)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("T3-19 hidden App acceptance did not finish") from error
        if app_exit != 0:
            raise RuntimeError("T3-19 hidden App acceptance failed")
        app_process = None
        verify_app_private_data(private_app_data)
        asyncio.run(
            verify_database_state(database_url, controlled_offer, succeeded_offer)
        )
        print("[T3-19] Hidden-App create/control/succeed/refresh acceptance passed")
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
