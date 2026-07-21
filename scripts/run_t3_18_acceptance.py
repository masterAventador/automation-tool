#!/usr/bin/env python3
"""Run T3-18 through the hidden Task detail UI and formal control paths."""

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
from run_t3_14_acceptance import (
    fake_executor_client,
    seed_attempt_and_offer,
    seed_task_confirmation,
)
from sqlalchemy import insert, select, text, update
from sqlalchemy.ext.asyncio import create_async_engine

from automation_tool.control_plane.application.task_command_delivery import (
    TaskCommandRecord,
)
from automation_tool.control_plane.domain import (
    ActionId,
    ExecutionAttemptStatus,
    InstallationId,
    TargetId,
    TaskCommandStatus,
    TaskCommandType,
    TaskEventType,
    TaskId,
    TaskStatus,
)
from automation_tool.control_plane.infrastructure.database import (
    Database,
    action_risk_authorizations,
    execution_attempts,
    task_actions,
    task_commands,
    task_events,
    tasks,
)
from automation_tool.protocol import (
    MAX_EXECUTOR_MESSAGE_BYTES,
)

TAURI_CONFIG = FRONTEND_ROOT / "src-tauri" / "tauri.task-run-e2e.conf.json"
CONTROL_PLANE_PORT = 8765
APP_IDENTIFIER = "com.aventador.automationtool.t318acceptance"
ENVIRONMENT_ID = "t318-acceptance"
CONTROLLED_TASK_KEY = "task:run:controlled:tauri-acceptance"
EMERGENCY_TASK_KEY = "task:run:emergency:tauri-acceptance"
DEVICE_CREDENTIAL_FILE = "device-credential-v1"


def require_control_plane_port_available() -> None:
    with socket.socket() as listener:
        try:
            listener.bind(("127.0.0.1", CONTROL_PLANE_PORT))
        except OSError as error:
            raise RuntimeError("T3-18 requires an unused Control Plane port") from error


def require_hidden_tauri_configuration() -> None:
    configuration = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))
    windows = configuration.get("app", {}).get("windows", [])
    if len(windows) != 1 or windows[0].get("visible") is not False:
        raise RuntimeError("T3-18 Tauri acceptance must run with visible=false")


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
        "postgresql+asyncpg://automation_tool_t318:"
        f"{database_password}@127.0.0.1:{database_port}/automation_tool_t318"
    )
    bootstrap_token, bootstrap_public_key = signed_bootstrap()
    environment.update(
        {
            "AUTOMATION_TOOL_DEV_DB_USER": "unused_t318_dev",
            "AUTOMATION_TOOL_DEV_DB_PASSWORD": secrets.token_hex(24),
            "AUTOMATION_TOOL_DEV_DB_NAME": "unused_t318_dev",
            "AUTOMATION_TOOL_DEV_DB_PORT": str(unused_loopback_port()),
            "AUTOMATION_TOOL_TEST_DB_USER": "automation_tool_t318",
            "AUTOMATION_TOOL_TEST_DB_PASSWORD": database_password,
            "AUTOMATION_TOOL_TEST_DB_NAME": "automation_tool_t318",
            "AUTOMATION_TOOL_TEST_DB_PORT": str(database_port),
            "AUTOMATION_TOOL_DATABASE_URL": database_url,
            "AUTOMATION_TOOL_DEMO_ENVIRONMENT_ID": ENVIRONMENT_ID,
            "AUTOMATION_TOOL_DEMO_BOOTSTRAP_PUBLIC_KEY": bootstrap_public_key,
            "AUTOMATION_TOOL_T318_BOOTSTRAP_TOKEN": bootstrap_token,
            "AUTOMATION_TOOL_T318_ENVIRONMENT_ID": ENVIRONMENT_ID,
        }
    )
    return environment, database_url


async def wait_for_app_tasks(
    database_url: str,
    private_app_data: Path,
    app_process: subprocess.Popen[bytes],
) -> tuple[InstallationId, TaskId, TaskId, str]:
    engine = create_async_engine(database_url)
    deadline = time.monotonic() + 120
    try:
        while True:
            if app_process.poll() is not None:
                raise RuntimeError("T3-18 hidden App exited before creating both Tasks")
            async with engine.connect() as connection:
                rows = (
                    await connection.execute(
                        text(
                            "select creation_idempotency_key, installation_id::text, id::text "
                            "from tasks where creation_idempotency_key in (:controlled, :emergency)"
                        ),
                        {
                            "controlled": CONTROLLED_TASK_KEY,
                            "emergency": EMERGENCY_TASK_KEY,
                        },
                    )
                ).all()
            by_key = {row[0]: row for row in rows}
            credential_path = private_app_data / DEVICE_CREDENTIAL_FILE
            if (
                CONTROLLED_TASK_KEY in by_key
                and EMERGENCY_TASK_KEY in by_key
                and credential_path.is_file()
            ):
                controlled = by_key[CONTROLLED_TASK_KEY]
                emergency = by_key[EMERGENCY_TASK_KEY]
                if controlled[1] != emergency[1]:
                    raise RuntimeError("T3-18 Task fixtures escaped their Installation scope")
                try:
                    credential = credential_path.read_text(encoding="ascii")
                except (OSError, UnicodeError) as error:
                    raise RuntimeError("T3-18 App credential vault is unreadable") from error
                return (
                    InstallationId.parse(controlled[1]),
                    TaskId.parse(controlled[2]),
                    TaskId.parse(emergency[2]),
                    credential,
                )
            if time.monotonic() >= deadline:
                raise RuntimeError("T3-18 hidden App did not create both Tasks in time")
            await asyncio.sleep(0.05)
    finally:
        await engine.dispose()


async def verify_database_state(
    database_url: str,
    controlled_offer: TaskCommandRecord,
    emergency_offer: TaskCommandRecord,
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
            8,
        ),
        (
            emergency_offer,
            [TaskCommandType.TASK_OFFER, TaskCommandType.TASK_EMERGENCY_STOP],
            [
                TaskEventType.TASK_STARTED,
                TaskEventType.STEP_STARTED,
                TaskEventType.TASK_OUTCOME_UNCERTAIN,
            ],
            TaskStatus.OUTCOME_UNCERTAIN,
            ExecutionAttemptStatus.OUTCOME_UNCERTAIN,
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
                if [row["command_type"] for row in commands] != [
                    command_type.value for command_type in command_types
                ] or any(row["status"] != TaskCommandStatus.ACKNOWLEDGED.value for row in commands):
                    raise RuntimeError("T3-18 Task commands were not exactly acknowledged")
                if [row["event_type"] for row in events] != [
                    event_type.value for event_type in event_types
                ]:
                    raise RuntimeError("T3-18 persisted event timeline is invalid")
                if (
                    task["status"] != task_status.value
                    or task["revision"] != revision
                    or task["last_event_sequence"] != len(event_types)
                    or attempt["status"] != attempt_status.value
                    or attempt["finished_at"] is None
                ):
                    raise RuntimeError(
                        "T3-18 final Task projection is invalid: "
                        f"task_status={task['status']!r}, revision={task['revision']!r}, "
                        f"last_event_sequence={task['last_event_sequence']!r}, "
                        f"attempt_status={attempt['status']!r}, "
                        f"attempt_finished={attempt['finished_at'] is not None!r}"
                    )
            capabilities = list(
                await session.scalars(text("select capability from device_sessions order by id"))
            )
        if capabilities.count("executor.connect") != 1 or any(
            capability not in {"app.control-plane", "executor.connect"}
            for capability in capabilities
        ):
            raise RuntimeError("T3-18 used an unexpected Session capability")
    finally:
        await database.close()


async def seed_target_results(
    database_url: str,
    installation_id: InstallationId,
    controlled_offer: TaskCommandRecord,
    target_ids: tuple[TargetId, ...],
) -> None:
    """Seed A7-15 facts; the hidden App must read them through its production API."""
    if len(target_ids) != 4:
        raise RuntimeError("A7-15 target-result fixture must contain four targets")
    database = Database.from_url(database_url)
    now = datetime.now(UTC)
    try:
        async with database.session() as session:
            for count, (target_id, ordinal, outcome, evidence) in enumerate(
                (
                    (target_ids[0], 1, "succeeded", "comment_confirmed"),
                    (target_ids[2], 3, "failed", "login_required"),
                    (target_ids[3], 4, "outcome_uncertain", "final_state_unconfirmed"),
                ),
                start=1,
            ):
                action_id = ActionId.new()
                await session.execute(
                    insert(task_actions).values(
                        id=action_id.uuid,
                        execution_attempt_id=controlled_offer.execution_attempt_id.uuid,
                        task_id=controlled_offer.task_id.uuid,
                        installation_id=installation_id.uuid,
                        ordinal=ordinal,
                        status=(
                            "outcome_uncertain" if outcome == "outcome_uncertain" else "verified"
                        ),
                        outcome=outcome,
                        evidence_code=evidence,
                        revision=3,
                        created_at=now,
                        updated_at=now,
                        finished_at=now,
                    )
                )
                await session.execute(
                    insert(action_risk_authorizations).values(
                        action_id=action_id.uuid,
                        target_id=target_id.uuid,
                        execution_attempt_id=controlled_offer.execution_attempt_id.uuid,
                        task_id=controlled_offer.task_id.uuid,
                        installation_id=installation_id.uuid,
                        ordinal=ordinal,
                        platform="douyin",
                        action="comment",
                        policy_version="action-risk-policy.v1",
                        effective_minimum_interval_seconds=30,
                        task_action_limit=10,
                        daily_action_limit=100,
                        consecutive_failure_threshold=3,
                        task_count_after=count,
                        daily_count_after=count,
                        authorized_day=now.date(),
                        authorized_at=now,
                        created_at=now,
                    )
                )
            await session.execute(
                update(tasks)
                .where(tasks.c.id == controlled_offer.task_id.uuid)
                .values(updated_at=now)
            )
    finally:
        await database.close()


def main() -> None:
    require_control_plane_port_available()
    require_hidden_tauri_configuration()
    private_app_data = app_data_directory()
    if private_app_data.exists():
        raise RuntimeError("Refusing to reuse an existing T3-18 App data directory")

    project_name = f"automation-tool-t318-{os.getpid()}"
    database_port = unused_loopback_port()
    environment, database_url = isolated_environment(database_port)
    compose = compose_command(project_name)
    server: subprocess.Popen[bytes] | None = None
    app_process: subprocess.Popen[bytes] | None = None
    executor_thread: threading.Thread | None = None
    executor_result: queue.Queue[object] = queue.Queue()

    try:
        print("[T3-18] Starting isolated PostgreSQL")
        subprocess.run(
            [*compose, "up", "--detach", "--wait", "postgres-test"],
            check=True,
            cwd=REPOSITORY_ROOT,
            env=environment,
        )
        print("[T3-18] Applying the production Alembic migration chain")
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            check=True,
            cwd=BACKEND_ROOT,
            env=environment,
        )
        print("[T3-18] Starting the real Uvicorn boundary in the background")
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
        print("[T3-18] Running the real Tauri App with visible=false")
        app_process = subprocess.Popen(
            ["pnpm", "test:task-run-tauri"],
            cwd=FRONTEND_ROOT,
            env=environment,
        )
        installation_id, controlled_task_id, emergency_task_id, credential = asyncio.run(
            wait_for_app_tasks(database_url, private_app_data, app_process)
        )
        controlled_target_ids = asyncio.run(
            seed_task_confirmation(
                database_url,
                installation_id,
                controlled_task_id,
                include_target_results=True,
            )
        )
        asyncio.run(
            seed_task_confirmation(
                database_url,
                installation_id,
                emergency_task_id,
                include_target_results=False,
            )
        )
        controlled_offer = asyncio.run(
            seed_attempt_and_offer(
                database_url,
                installation_id,
                controlled_task_id,
                label="task-run-controlled",
                confirmed_target_revision=True,
            )
        )
        asyncio.run(
            seed_target_results(
                database_url,
                installation_id,
                controlled_offer,
                controlled_target_ids,
            )
        )
        emergency_offer = asyncio.run(
            seed_attempt_and_offer(
                database_url,
                installation_id,
                emergency_task_id,
                label="task-run-emergency",
                confirmed_target_revision=True,
            )
        )
        client = fake_executor_client(credential, installation_id)

        def run_executor() -> None:
            try:
                executor_result.put(client.run(max_commands=6))
            except Exception as error:
                executor_result.put(error)

        executor_thread = threading.Thread(target=run_executor, daemon=True)
        executor_thread.start()
        try:
            app_exit = app_process.wait(timeout=300)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("T3-18 hidden App acceptance did not finish") from error
        if app_exit != 0:
            raise RuntimeError("T3-18 hidden App acceptance failed")
        app_process = None
        executor_thread.join(timeout=10)
        if executor_thread.is_alive():
            raise RuntimeError("T3-18 FakeExecutor did not finish")
        processed = executor_result.get_nowait()
        if isinstance(processed, Exception):
            raise processed
        if processed != 6:
            raise RuntimeError("T3-18 FakeExecutor did not process all six commands")
        verify_app_private_data(private_app_data)
        asyncio.run(verify_database_state(database_url, controlled_offer, emergency_offer))
        print("[T3-18] Hidden-App Task run details and all controls acceptance passed")
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
