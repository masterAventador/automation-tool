#!/usr/bin/env python3
"""Run H8-06 through one hidden App, signed Executor, and restarted Control Plane."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import closing, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from run_e4_07_acceptance import build_signed_executor
from run_e4_14_acceptance import (
    executor_entrypoint,
    install_executor_package,
    matching_executor_processes,
    terminate_executor_processes,
)
from run_h8_01_acceptance import run_offer_fixture
from run_h8_04_acceptance import (
    hard_kill_app,
    require_app_process,
    wait_for_process_exit,
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
)
from run_t3_14_acceptance import seed_attempt_and_offer, seed_task_confirmation
from run_t3_20_acceptance import start_control_plane, stop_control_plane
from sqlalchemy import select, text

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
from automation_tool.executor.ledger import AttemptCheckpointState, ExecutorLedger
from automation_tool.protocol import EXECUTOR_PROTOCOL_VERSION, TaskCommandEnvelope

TAURI_CONFIG = FRONTEND_ROOT / "src-tauri" / "tauri.control-plane-recovery-e2e.conf.json"
CONTROL_PLANE_PORT = 8765
APP_IDENTIFIER = "com.aventador.automationtool.h806acceptance"
ENVIRONMENT_ID = "h806-acceptance"
TASK_KEYWORD = "H8-06 Control Plane 重启恢复"
DEVICE_CREDENTIAL_FILE = "device-credential-v1"
EXECUTOR_BUILD_ID = "h8-06-control-plane-recovery"
EXECUTOR_LEDGER_FILE = "executor-ledger.sqlite3"


def require_control_plane_port_available() -> None:
    with socket.socket() as listener:
        try:
            listener.bind(("127.0.0.1", CONTROL_PLANE_PORT))
        except OSError as error:
            raise RuntimeError("H8-06 requires an unused Control Plane port") from error


def require_hidden_tauri_configuration() -> None:
    configuration = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))
    windows = configuration.get("app", {}).get("windows", [])
    if (
        configuration.get("identifier") != APP_IDENTIFIER
        or len(windows) != 1
        or windows[0].get("visible") is not False
    ):
        raise RuntimeError("H8-06 requires one isolated visible=false App")


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
    payload = json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("ascii")
    payload_segment = base64url(payload)
    signing_input = f"atb1.{payload_segment}".encode("ascii")
    token = f"atb1.{payload_segment}.{base64url(signer.sign(signing_input))}"
    return token, base64url(signer.public_key().public_bytes_raw())


def isolated_environment(database_port: int) -> tuple[dict[str, str], str]:
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("AUTOMATION_TOOL_")
    }
    password = secrets.token_hex(24)
    database_url = (
        "postgresql+asyncpg://automation_tool_h806:"
        f"{password}@127.0.0.1:{database_port}/automation_tool_h806"
    )
    token, public_key = signed_bootstrap()
    environment.update(
        {
            "AUTOMATION_TOOL_DEV_DB_USER": "unused_h806_dev",
            "AUTOMATION_TOOL_DEV_DB_PASSWORD": secrets.token_hex(24),
            "AUTOMATION_TOOL_DEV_DB_NAME": "unused_h806_dev",
            "AUTOMATION_TOOL_DEV_DB_PORT": str(unused_loopback_port()),
            "AUTOMATION_TOOL_TEST_DB_USER": "automation_tool_h806",
            "AUTOMATION_TOOL_TEST_DB_PASSWORD": password,
            "AUTOMATION_TOOL_TEST_DB_NAME": "automation_tool_h806",
            "AUTOMATION_TOOL_TEST_DB_PORT": str(database_port),
            "AUTOMATION_TOOL_DATABASE_URL": database_url,
            "AUTOMATION_TOOL_DEMO_ENVIRONMENT_ID": ENVIRONMENT_ID,
            "AUTOMATION_TOOL_DEMO_BOOTSTRAP_PUBLIC_KEY": public_key,
            "AUTOMATION_TOOL_H806_BOOTSTRAP_TOKEN": token,
            "AUTOMATION_TOOL_H806_ENVIRONMENT_ID": ENVIRONMENT_ID,
            "AUTOMATION_TOOL_CONTROL_PLANE_E2E_ORIGIN": (f"http://127.0.0.1:{CONTROL_PLANE_PORT}"),
        }
    )
    return environment, database_url


def wait_for_signal(
    path: Path, process: subprocess.Popen[bytes], *, label: str
) -> dict[str, object]:
    deadline = time.monotonic() + 150
    while not path.is_file():
        if process.poll() is not None:
            raise RuntimeError(f"H8-06 App exited before {label}")
        if time.monotonic() >= deadline:
            raise RuntimeError(f"H8-06 timed out waiting for {label}")
        time.sleep(0.05)
    decoded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(decoded, dict):
        raise RuntimeError(f"H8-06 {label} signal is invalid")
    return decoded


def collect_wdio(process: subprocess.Popen[bytes], *, timeout: int) -> tuple[int, str]:
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.terminate()
        with suppress(subprocess.TimeoutExpired):
            output, _ = process.communicate(timeout=10)
        if process.poll() is None:
            process.kill()
            output, _ = process.communicate(timeout=5)
    return process.returncode, output.decode("utf-8", errors="replace")


def ensure_app_process_stopped(process_id: int | None) -> None:
    if process_id is None:
        return
    time.sleep(0.25)
    try:
        require_app_process(process_id)
    except (RuntimeError, subprocess.CalledProcessError):
        return
    hard_kill_app(process_id)
    wait_for_process_exit(process_id)


async def read_task_identity(
    database_url: str,
    task_id: str,
    installation_id: str,
    private_app_data: Path,
) -> tuple[InstallationId, TaskId, str]:
    typed_installation = InstallationId.parse(installation_id)
    typed_task = TaskId.parse(task_id)
    database = Database.from_url(database_url)
    try:
        async with database.session() as session:
            definition = (
                await session.execute(
                    select(
                        douyin_search_exposure_definitions.c.installation_id,
                        douyin_search_exposure_definitions.c.search_keyword,
                    ).where(douyin_search_exposure_definitions.c.task_id == typed_task.uuid)
                )
            ).one()
        if definition != (typed_installation.uuid, TASK_KEYWORD):
            raise RuntimeError("H8-06 App-created Task identity is invalid")
    finally:
        await database.close()
    credential_path = private_app_data / DEVICE_CREDENTIAL_FILE
    if not credential_path.is_file():
        raise RuntimeError("H8-06 App credential vault is missing")
    return typed_installation, typed_task, credential_path.read_text(encoding="ascii")


def seed_running_local_checkpoint(
    state_directory: Path,
    original: TaskCommandRecord,
    executor_id: str,
) -> ExecutorLedger:
    state_directory.mkdir(mode=0o700, exist_ok=True)
    ledger = ExecutorLedger(
        state_directory=state_directory,
        installation_id=str(original.installation_id),
        executor_id=executor_id,
    )
    offer = TaskCommandEnvelope.model_validate(
        {
            "protocol_version": EXECUTOR_PROTOCOL_VERSION,
            "message_id": str(original.message_id),
            "message_type": "task.offer",
            "sent_at": original.created_at,
            "deadline_at": original.deadline_at,
            "installation_id": str(original.installation_id),
            "executor_id": executor_id,
            "correlation_id": str(original.correlation_id),
            "idempotency_key": original.idempotency_key,
            "sequence": original.sequence,
            "payload": {
                "task_event_sequence_baseline": original.task_event_sequence_baseline
            },
            "task_id": str(original.task_id),
            "execution_attempt_id": str(original.execution_attempt_id),
        }
    )
    ledger.receive_command(offer)
    ledger.compare_and_set_checkpoint(
        attempt_id=str(original.execution_attempt_id),
        expected_revision=1,
        state=AttemptCheckpointState.RUNNING,
        last_event_sequence=2,
    )
    return ledger


def suspend_executor(process_id: int) -> None:
    if sys.platform == "win32":
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                f"Suspend-Process -Id {process_id} -ErrorAction Stop",
            ],
            check=True,
        )
        return
    os.kill(process_id, signal.SIGSTOP)


def resume_executor(process_id: int) -> None:
    if sys.platform == "win32":
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                f"Resume-Process -Id {process_id} -ErrorAction Stop",
            ],
            check=True,
        )
        return
    os.kill(process_id, signal.SIGCONT)


async def read_pre_restart_checkpoint(
    database_url: str,
    task_id: TaskId,
) -> tuple[tuple[object, ...], ...]:
    database = Database.from_url(database_url)
    deadline = time.monotonic() + 10
    try:
        while True:
            async with database.session() as session:
                task = (
                    await session.execute(
                        select(tasks.c.status, tasks.c.last_event_sequence).where(
                            tasks.c.id == task_id.uuid
                        )
                    )
                ).one()
                attempt = (
                    await session.execute(
                        select(execution_attempts.c.status).where(
                            execution_attempts.c.task_id == task_id.uuid
                        )
                    )
                ).one()
                commands = tuple(
                    (
                        await session.execute(
                            select(
                                task_commands.c.message_id,
                                task_commands.c.idempotency_key,
                                task_commands.c.command_type,
                                task_commands.c.status,
                                task_commands.c.sequence,
                                task_commands.c.created_at,
                            )
                            .where(task_commands.c.task_id == task_id.uuid)
                            .order_by(task_commands.c.sequence)
                        )
                    ).all()
                )
                events = tuple(
                    (
                        await session.execute(
                            select(task_events.c.event_type, task_events.c.sequence)
                            .where(task_events.c.task_id == task_id.uuid)
                            .order_by(task_events.c.sequence)
                        )
                    ).all()
                )
            if (
                task == (TaskStatus.CANCELLING.value, 2)
                and attempt == (ExecutionAttemptStatus.CANCELLING.value,)
                and [(row[2], row[3], row[4]) for row in commands]
                == [
                    (
                        TaskCommandType.TASK_OFFER.value,
                        TaskCommandStatus.ACKNOWLEDGED.value,
                        1,
                    ),
                    (
                        TaskCommandType.TASK_CANCEL.value,
                        TaskCommandStatus.DELIVERED.value,
                        2,
                    ),
                ]
                and events
                == (
                    (TaskEventType.TASK_STARTED.value, 1),
                    (TaskEventType.STEP_STARTED.value, 2),
                )
            ):
                return commands
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "H8-06 pre-restart checkpoint did not settle to delivered: "
                    f"task={task!r}, attempt={attempt!r}, "
                    f"commands={[(row[2], row[3], row[4]) for row in commands]!r}, "
                    f"events={events!r}"
                )
            await asyncio.sleep(0.05)
    finally:
        await database.close()


async def verify_server_recovery(
    database_url: str,
    task_id: TaskId,
    checkpoint: tuple[tuple[object, ...], ...],
) -> None:
    database = Database.from_url(database_url)
    try:
        async with database.session() as session:
            task = (
                await session.execute(
                    select(tasks.c.status, tasks.c.last_event_sequence).where(
                        tasks.c.id == task_id.uuid
                    )
                )
            ).one()
            attempt = (
                await session.execute(
                    select(execution_attempts.c.status).where(
                        execution_attempts.c.task_id == task_id.uuid
                    )
                )
            ).one()
            commands = tuple(
                (
                    await session.execute(
                        select(
                            task_commands.c.message_id,
                            task_commands.c.idempotency_key,
                            task_commands.c.command_type,
                            task_commands.c.status,
                            task_commands.c.sequence,
                            task_commands.c.created_at,
                        )
                        .where(task_commands.c.task_id == task_id.uuid)
                        .order_by(task_commands.c.sequence)
                    )
                ).all()
            )
            events = tuple(
                (
                    await session.execute(
                        select(task_events.c.event_type, task_events.c.sequence)
                        .where(task_events.c.task_id == task_id.uuid)
                        .order_by(task_events.c.sequence)
                    )
                ).all()
            )
            session_count = int(
                await session.scalar(
                    text(
                        "select count(*) from device_sessions where capability = 'executor.connect'"
                    )
                )
                or 0
            )
        expected_commands = tuple(
            (*row[:3], TaskCommandStatus.ACKNOWLEDGED.value, *row[4:]) for row in checkpoint
        )
        if (
            task != (TaskStatus.CANCELLED.value, 3)
            or attempt != (ExecutionAttemptStatus.CANCELLED.value,)
            or commands != expected_commands
            or events
            != (
                (TaskEventType.TASK_STARTED.value, 1),
                (TaskEventType.STEP_STARTED.value, 2),
                (TaskEventType.TASK_CANCELLED.value, 3),
            )
            or session_count != 2
        ):
            raise RuntimeError("H8-06 Control Plane did not converge exact durable facts")
    finally:
        await database.close()


def verify_local_recovery(
    private_app_data: Path,
    checkpoint: tuple[tuple[object, ...], ...],
    credential: str,
) -> None:
    ledger_path = private_app_data / "local-executor" / "state" / EXECUTOR_LEDGER_FILE
    with closing(sqlite3.connect(ledger_path)) as connection:
        local_checkpoint = connection.execute(
            "SELECT last_command_sequence, last_event_sequence, state, revision "
            "FROM executor_attempt_checkpoints"
        ).fetchall()
        commands = connection.execute(
            "SELECT message_id, idempotency_key, message_type, sequence "
            "FROM executor_commands ORDER BY sequence"
        ).fetchall()
        outbox = connection.execute(
            "SELECT source_message_id, json_extract(envelope, '$.message_type'), delivered "
            "FROM executor_outbox ORDER BY ordinal"
        ).fetchall()
    expected_commands = [(str(row[0]), str(row[1]), str(row[2]), int(row[4])) for row in checkpoint]
    cancel_message_id = str(checkpoint[1][0])
    if (
        local_checkpoint != [(2, 3, "terminal", 4)]
        or commands != expected_commands
        or outbox
        != [
            (cancel_message_id, "task.control_ack", 1),
            (cancel_message_id, "task.cancelled", 1),
        ]
    ):
        raise RuntimeError(
            "H8-06 SQLite command/checkpoint/outbox did not converge once: "
            f"checkpoint={local_checkpoint!r}, "
            f"commands={[(row[2], row[3]) for row in commands]!r}, "
            f"outbox={[(row[1], row[2]) for row in outbox]!r}"
        )
    if credential.encode("ascii") in ledger_path.read_bytes():
        raise RuntimeError("H8-06 persisted the App credential in SQLite")
    verify_app_private_data(private_app_data)


def main() -> None:
    require_control_plane_port_available()
    require_hidden_tauri_configuration()
    private_app_data = app_data_directory()
    if private_app_data.exists():
        raise RuntimeError("Refusing to reuse an existing H8-06 App data directory")

    project_name = f"automation-tool-h806-{os.getpid()}"
    database_port = unused_loopback_port()
    environment, database_url = isolated_environment(database_port)
    compose = compose_command(project_name)
    server: subprocess.Popen[bytes] | None = None
    app: subprocess.Popen[bytes] | None = None
    package_entrypoint: Path | None = None
    executor_process_id: int | None = None
    executor_suspended = False
    app_process_id: int | None = None

    with tempfile.TemporaryDirectory(prefix=f"{project_name}-") as temporary:
        workspace = Path(temporary)
        signals = {
            name: workspace / f"{name}.json"
            for name in (
                "task-created",
                "task-seeded",
                "executor-ready",
                "executor-suspended",
                "cancel-submitted",
                "control-plane-down",
                "unavailable",
                "control-plane-up",
                "recovered",
            )
        }
        environment.update(
            {
                "AUTOMATION_TOOL_H806_TASK_CREATED_SIGNAL": os.fspath(signals["task-created"]),
                "AUTOMATION_TOOL_H806_TASK_SEEDED_SIGNAL": os.fspath(signals["task-seeded"]),
                "AUTOMATION_TOOL_H806_EXECUTOR_READY_SIGNAL": os.fspath(signals["executor-ready"]),
                "AUTOMATION_TOOL_H806_EXECUTOR_SUSPENDED_SIGNAL": os.fspath(
                    signals["executor-suspended"]
                ),
                "AUTOMATION_TOOL_H806_CANCEL_SUBMITTED_SIGNAL": os.fspath(
                    signals["cancel-submitted"]
                ),
                "AUTOMATION_TOOL_H806_CONTROL_PLANE_DOWN_SIGNAL": os.fspath(
                    signals["control-plane-down"]
                ),
                "AUTOMATION_TOOL_H806_UNAVAILABLE_SIGNAL": os.fspath(signals["unavailable"]),
                "AUTOMATION_TOOL_H806_CONTROL_PLANE_UP_SIGNAL": os.fspath(
                    signals["control-plane-up"]
                ),
                "AUTOMATION_TOOL_H806_RECOVERED_SIGNAL": os.fspath(signals["recovered"]),
            }
        )
        try:
            print("[H8-06] Building and signing the real PyInstaller Executor")
            package_source = build_signed_executor(workspace, build_id=EXECUTOR_BUILD_ID)
            package_root = install_executor_package(package_source, private_app_data)
            package_entrypoint = executor_entrypoint(package_root)

            print("[H8-06] Building the dedicated hidden Tauri App")
            subprocess.run(
                ["pnpm", "build:tauri:control-plane-recovery-test"],
                check=True,
                cwd=FRONTEND_ROOT,
                env=environment,
            )
            print(f"[H8-06] Starting isolated PostgreSQL as {project_name}")
            subprocess.run(
                [*compose, "up", "--detach", "--wait", "postgres-test"],
                check=True,
                cwd=REPOSITORY_ROOT,
                env=environment,
            )
            subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", "head"],
                check=True,
                cwd=BACKEND_ROOT,
                env=environment,
            )
            server = start_control_plane(environment)

            app = subprocess.Popen(
                ["pnpm", "exec", "wdio", "run", "wdio.control-plane-recovery.conf.ts"],
                cwd=FRONTEND_ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            created = wait_for_signal(signals["task-created"], app, label="the App Task")
            installation_id = str(created.get("installationId", ""))
            task_id = str(created.get("taskId", ""))
            app_process_id_value = created.get("appProcessId")
            if not isinstance(app_process_id_value, int):
                raise RuntimeError("H8-06 App did not report its process identity")
            app_process_id = app_process_id_value
            require_app_process(app_process_id)
            typed_installation, typed_task, credential = asyncio.run(
                read_task_identity(database_url, task_id, installation_id, private_app_data)
            )
            asyncio.run(
                seed_task_confirmation(
                    database_url,
                    typed_installation,
                    typed_task,
                    include_target_results=False,
                )
            )
            offer = asyncio.run(
                seed_attempt_and_offer(
                    database_url,
                    typed_installation,
                    typed_task,
                    label="h8-06-control-plane-restart",
                    confirmed_target_revision=True,
                )
            )
            run_offer_fixture(credential, typed_installation)
            executor_id = (private_app_data / "local-executor" / "executor-id-v1").read_text(
                encoding="ascii"
            )
            seed_running_local_checkpoint(
                private_app_data / "local-executor" / "state",
                offer,
                executor_id,
            )
            signals["task-seeded"].write_text("{}\n", encoding="utf-8")

            wait_for_signal(signals["executor-ready"], app, label="the running signed Executor")
            processes = matching_executor_processes(package_entrypoint)
            if len(processes) != 1:
                raise RuntimeError("H8-06 did not start exactly one signed Executor")
            executor_process_id = processes[0][0]
            suspend_executor(executor_process_id)
            executor_suspended = True
            signals["executor-suspended"].write_text("{}\n", encoding="utf-8")

            wait_for_signal(signals["cancel-submitted"], app, label="the App-submitted cancel")
            checkpoint = asyncio.run(read_pre_restart_checkpoint(database_url, typed_task))
            print("[H8-06] Stopping the real Control Plane with one pending App command")
            stop_control_plane(server)
            server = None
            signals["control-plane-down"].write_text("{}\n", encoding="utf-8")
            resume_executor(executor_process_id)
            executor_suspended = False
            wait_for_signal(signals["unavailable"], app, label="the unavailable App UI")
            if matching_executor_processes(package_entrypoint) != processes:
                raise RuntimeError(
                    "H8-06 replaced the signed Executor while Control Plane was down"
                )

            print("[H8-06] Restarting the Control Plane against the same PostgreSQL")
            server = start_control_plane(environment)
            signals["control-plane-up"].write_text("{}\n", encoding="utf-8")
            recovered = wait_for_signal(signals["recovered"], app, label="the converged Task")
            if (
                recovered.get("taskId") != task_id
                or recovered.get("installationId") != installation_id
                or recovered.get("restart_count") != 0
            ):
                raise RuntimeError("H8-06 App reported a mismatched recovery result")
            if matching_executor_processes(package_entrypoint) != processes:
                raise RuntimeError("H8-06 Rust supervisor restarted the healthy Executor")
            exit_code, output = collect_wdio(app, timeout=60)
            app = None
            ensure_app_process_stopped(app_process_id)
            if exit_code != 0:
                print(output, end="")
                raise RuntimeError("H8-06 hidden App acceptance failed")
            asyncio.run(verify_server_recovery(database_url, typed_task, checkpoint))
            verify_local_recovery(private_app_data, checkpoint, credential)
            print("[H8-06] Hidden-App Control Plane restart recovery passed")
        finally:
            if executor_suspended and executor_process_id is not None:
                with suppress(Exception):
                    resume_executor(executor_process_id)
            if app is not None and app.poll() is None:
                app.terminate()
                with suppress(subprocess.TimeoutExpired):
                    app.wait(timeout=10)
                if app.poll() is None:
                    app.kill()
                    app.wait(timeout=5)
            ensure_app_process_stopped(app_process_id)
            if package_entrypoint is not None:
                terminate_executor_processes(package_entrypoint)
            if server is not None and server.poll() is None:
                stop_control_plane(server)
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
