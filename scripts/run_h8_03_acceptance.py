#!/usr/bin/env python3
"""Run H8-03 through the hidden Task UI across a real Control Plane outage."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import closing
from pathlib import Path

from desktop_e2e_prerequisites import (
    prepare_startup_gate,
    terminate_app_process_tree,
)
from run_e4_07_acceptance import build_signed_executor
from run_e4_14_acceptance import (
    assert_no_executor_process,
    executor_entrypoint,
    install_executor_package,
    matching_executor_processes,
    start_control_plane,
    terminate_executor_processes,
)
from run_h8_01_acceptance import run_offer_fixture, seed_local_checkpoint
from run_i2_13_acceptance import require_port_closed, wait_for_control_plane
from run_t3_06_acceptance import (
    BACKEND_ROOT,
    FRONTEND_ROOT,
    REPOSITORY_ROOT,
    compose_command,
    unused_loopback_port,
    verify_app_private_data,
)
from run_t3_14_acceptance import seed_attempt_and_offer, seed_task_confirmation
from run_t3_18_acceptance import (
    CONTROL_PLANE_PORT,
    app_data_directory,
    isolated_environment,
    require_control_plane_port_available,
    require_hidden_tauri_configuration,
    wait_for_app_tasks,
)
from sqlalchemy import select, text

from automation_tool.control_plane.application.task_command_delivery import TaskCommandRecord
from automation_tool.control_plane.domain import (
    ExecutionAttemptStatus,
    TaskCommandStatus,
    TaskCommandType,
    TaskEventType,
    TaskStatus,
)
from automation_tool.control_plane.infrastructure.database import (
    Database,
    execution_attempts,
    task_commands,
    task_events,
    tasks,
)
from automation_tool.executor.ledger import EXECUTOR_LEDGER_SCHEMA_VERSION
from automation_tool.protocol import MAX_EXECUTOR_MESSAGE_BYTES

EXECUTOR_BUILD_ID = "h8-03-offline-emergency-stop"
TASK_EMERGENCY_STOP_FILE = "task-emergency-stop-v1"
EXECUTOR_LEDGER_FILE = "executor-ledger.sqlite3"


def stop_control_plane(server: subprocess.Popen[bytes]) -> None:
    server.terminate()
    try:
        server.wait(timeout=10)
    except subprocess.TimeoutExpired:
        server.kill()
        server.wait(timeout=5)
    require_port_closed(CONTROL_PLANE_PORT)


def start_replacement_control_plane(
    environment: dict[str, str],
) -> subprocess.Popen[bytes]:
    require_port_closed(CONTROL_PLANE_PORT)
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
    wait_for_control_plane(CONTROL_PLANE_PORT, server)
    return server


def wait_for_signal(
    path: Path,
    app_process: subprocess.Popen[bytes],
    *,
    label: str,
) -> None:
    deadline = time.monotonic() + 120
    while not path.is_file():
        if app_process.poll() is not None:
            raise RuntimeError(f"H8-03 hidden App exited before {label}")
        if time.monotonic() >= deadline:
            raise RuntimeError(f"H8-03 timed out waiting for {label}")
        time.sleep(0.05)


async def wait_for_offer_acknowledgement(
    database_url: str,
    offer: TaskCommandRecord,
    app_process: subprocess.Popen[bytes],
) -> None:
    database = Database.from_url(database_url)
    deadline = time.monotonic() + 120
    try:
        while True:
            if app_process.poll() is not None:
                raise RuntimeError("H8-03 hidden App exited before the real Executor offer ACK")
            async with database.session() as session:
                status = await session.scalar(
                    select(task_commands.c.status).where(
                        task_commands.c.message_id == offer.message_id
                    )
                )
            if status == TaskCommandStatus.ACKNOWLEDGED.value:
                return
            if time.monotonic() >= deadline:
                raise RuntimeError("H8-03 real Executor did not acknowledge its offer")
            await asyncio.sleep(0.05)
    finally:
        await database.close()


async def require_executor_session_count(
    database_url: str,
    expected: int,
    *,
    stage: str,
) -> None:
    database = Database.from_url(database_url)
    try:
        async with database.session() as session:
            count = await session.scalar(
                text(
                    "select count(*) from device_sessions "
                    "where capability = 'executor.connect'"
                )
            )
        if count != expected:
            raise RuntimeError(
                f"H8-03 expected {expected} Executor Sessions at {stage}, got {count}"
            )
    finally:
        await database.close()


def wait_for_local_emergency_stop(
    marker: Path,
    entrypoint: Path,
    app_process: subprocess.Popen[bytes],
) -> dict[str, object]:
    deadline = time.monotonic() + 120
    parsed: dict[str, object] | None = None
    while time.monotonic() < deadline:
        if app_process.poll() is not None:
            raise RuntimeError("H8-03 hidden App exited before persisting the emergency stop")
        if marker.is_file():
            try:
                decoded = json.loads(marker.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as error:
                raise RuntimeError("H8-03 emergency-stop marker is unreadable") from error
            if isinstance(decoded, dict):
                parsed = decoded
        if parsed is not None and not matching_executor_processes(entrypoint):
            return parsed
        time.sleep(0.05)
    raise RuntimeError("H8-03 did not persist and hard-stop the signed Executor offline")


async def verify_database_state(
    database_url: str,
    offer: TaskCommandRecord,
) -> None:
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
            events = list(
                await session.scalars(
                    select(task_events.c.event_type)
                    .where(task_events.c.task_id == offer.task_id.uuid)
                    .order_by(task_events.c.sequence)
                )
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
        if [row["command_type"] for row in commands] != [
            TaskCommandType.TASK_OFFER.value,
            TaskCommandType.TASK_EMERGENCY_STOP.value,
        ] or any(row["status"] != TaskCommandStatus.ACKNOWLEDGED.value for row in commands):
            raise RuntimeError("H8-03 command history did not converge exactly once")
        if events != [
            TaskEventType.TASK_STARTED.value,
            TaskEventType.STEP_STARTED.value,
            TaskEventType.TASK_OUTCOME_UNCERTAIN.value,
        ]:
            raise RuntimeError("H8-03 event history did not preserve uncertainty")
        if (
            task["status"] != TaskStatus.OUTCOME_UNCERTAIN.value
            or task["revision"] != 6
            or task["last_event_sequence"] != 3
            or attempt["status"] != ExecutionAttemptStatus.OUTCOME_UNCERTAIN.value
            or attempt["revision"] != 4
            or attempt["finished_at"] is None
        ):
            raise RuntimeError("H8-03 final Task or Attempt projection is invalid")
        if capabilities.count("executor.connect") != 3 or any(
            capability not in {"app.control-plane", "executor.connect"}
            for capability in capabilities
        ):
            raise RuntimeError(
                f"H8-03 used an unexpected Session capability: {capabilities!r}"
            )
    finally:
        await database.close()


def verify_local_state(
    private_app_data: Path,
    offer: TaskCommandRecord,
    credential: str,
) -> None:
    marker = private_app_data / "local-executor" / TASK_EMERGENCY_STOP_FILE
    if marker.exists():
        raise RuntimeError("H8-03 left a reconciled emergency-stop marker behind")
    ledger = private_app_data / "local-executor" / "state" / EXECUTOR_LEDGER_FILE
    if not ledger.is_file():
        raise RuntimeError("H8-03 App-private Executor ledger is missing")
    with closing(sqlite3.connect(ledger)) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()
        guard = connection.execute(
            "SELECT engaged, revision, changed_at FROM executor_action_guard WHERE singleton_id = 1"
        ).fetchone()
        checkpoint = connection.execute(
            "SELECT task_id, last_command_sequence, last_event_sequence, state "
            "FROM executor_attempt_checkpoints WHERE attempt_id = ?",
            (str(offer.execution_attempt_id),),
        ).fetchone()
        command_types = connection.execute(
            "SELECT message_type FROM executor_commands WHERE attempt_id = ? ORDER BY sequence",
            (str(offer.execution_attempt_id),),
        ).fetchall()
        side_effect_states = connection.execute(
            "SELECT side_effect.state FROM executor_side_effects AS side_effect "
            "JOIN executor_action_admissions AS admission "
            "ON admission.action_id = side_effect.action_id "
            "WHERE admission.execution_attempt_id = ? ORDER BY side_effect.action_id",
            (str(offer.execution_attempt_id),),
        ).fetchall()
    if (
        version != (EXECUTOR_LEDGER_SCHEMA_VERSION,)
        or guard is None
        or guard[0] != 1
        or guard[1] < 1
        or guard[2] is None
    ):
        raise RuntimeError("H8-03 local action emergency latch is not durable")
    if checkpoint != (str(offer.task_id), 2, 3, "outcome_uncertain"):
        raise RuntimeError("H8-03 local Attempt checkpoint is invalid")
    if command_types != [("task.offer",), ("task.emergency_stop",)]:
        raise RuntimeError("H8-03 local command history is invalid")
    if sorted(state for (state,) in side_effect_states) != ["prepared", "uncertain"]:
        raise RuntimeError("H8-03 local side-effect uncertainty is invalid")
    if credential.encode("ascii") in ledger.read_bytes():
        raise RuntimeError("H8-03 persisted the long-lived App credential in SQLite")
    verify_app_private_data(private_app_data)


def main() -> None:
    require_control_plane_port_available()
    require_hidden_tauri_configuration()
    private_app_data = app_data_directory()
    if private_app_data.exists():
        raise RuntimeError("Refusing to reuse existing H8-03 App data")
    prepare_startup_gate(private_app_data, executor_package=False)

    project_name = f"automation-tool-h803-{os.getpid()}"
    database_port = unused_loopback_port()
    environment, database_url = isolated_environment(database_port)
    compose = compose_command(project_name)
    server: subprocess.Popen[bytes] | None = None
    app_process: subprocess.Popen[bytes] | None = None
    package_entrypoint: Path | None = None

    with tempfile.TemporaryDirectory(prefix=f"{project_name}-") as temporary:
        workspace = Path(temporary)
        ready_signal = workspace / "app-ready"
        prepared_signal = workspace / "app-prepared"
        seeded_signal = workspace / "checkpoint-seeded"
        down_signal = workspace / "control-plane-down"
        offline_observed_signal = workspace / "offline-observed"
        environment.update(
            {
                "AUTOMATION_TOOL_H803_OFFLINE_EMERGENCY_STOP": "1",
                "AUTOMATION_TOOL_H803_READY_SIGNAL": os.fspath(ready_signal),
                "AUTOMATION_TOOL_H803_PREPARED_SIGNAL": os.fspath(prepared_signal),
                "AUTOMATION_TOOL_H803_SEEDED_SIGNAL": os.fspath(seeded_signal),
                "AUTOMATION_TOOL_H803_DOWN_SIGNAL": os.fspath(down_signal),
                "AUTOMATION_TOOL_H803_OFFLINE_OBSERVED_SIGNAL": os.fspath(
                    offline_observed_signal
                ),
            }
        )
        try:
            print("[H8-03] Building and signing the real PyInstaller Executor")
            package_source = build_signed_executor(workspace, build_id=EXECUTOR_BUILD_ID)
            package_root = install_executor_package(package_source)
            package_entrypoint = executor_entrypoint(package_root)

            print(f"[H8-03] Starting isolated PostgreSQL as {project_name}")
            subprocess.run(
                [*compose, "up", "--detach", "--wait", "postgres-test"],
                check=True,
                cwd=REPOSITORY_ROOT,
                env=environment,
            )
            print("[H8-03] Applying the production Alembic migration chain")
            subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", "head"],
                check=True,
                cwd=BACKEND_ROOT,
                env=environment,
            )
            print("[H8-03] Starting the first real Control Plane")
            server = start_control_plane(port=CONTROL_PLANE_PORT, environment=environment)
            print("[H8-03] Running the real Tauri App with visible=false")
            app_process = subprocess.Popen(
                ["pnpm", "test:task-run-tauri"],
                cwd=FRONTEND_ROOT,
                env=environment,
                    start_new_session=sys.platform != "win32",
                creationflags=(
                    subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
                ),
            )

            installation_id, _controlled_task, emergency_task, credential = asyncio.run(
                wait_for_app_tasks(database_url, private_app_data, app_process)
            )
            wait_for_signal(
                prepared_signal,
                app_process,
                label="the App's Task preparation",
            )
            asyncio.run(
                seed_task_confirmation(
                    database_url,
                    installation_id,
                    emergency_task,
                    include_target_results=False,
                )
            )
            offer = asyncio.run(
                seed_attempt_and_offer(
                    database_url,
                    installation_id,
                    emergency_task,
                    label="h8-03-offline-emergency",
                    confirmed_target_revision=True,
                )
            )
            run_offer_fixture(credential, installation_id)
            asyncio.run(wait_for_offer_acknowledgement(database_url, offer, app_process))
            asyncio.run(
                require_executor_session_count(
                    database_url,
                    1,
                    stage="the held offer fixture",
                )
            )
            executor_id = (
                private_app_data / "local-executor" / "executor-id-v1"
            ).read_text(encoding="ascii")
            seed_local_checkpoint(
                private_app_data / "local-executor" / "state",
                offer,
                executor_id,
            )
            seeded_signal.write_text("seeded\n", encoding="utf-8")

            wait_for_signal(ready_signal, app_process, label="the signed Executor startup")
            asyncio.run(
                require_executor_session_count(
                    database_url,
                    2,
                    stage="the signed Executor startup",
                )
            )
            if package_entrypoint is None or not matching_executor_processes(package_entrypoint):
                raise RuntimeError("H8-03 did not start the signed Executor before the outage")

            print("[H8-03] Stopping the Control Plane before the UI emergency stop")
            stop_control_plane(server)
            server = None
            down_signal.write_text("down\n", encoding="utf-8")

            marker = private_app_data / "local-executor" / TASK_EMERGENCY_STOP_FILE
            persisted = wait_for_local_emergency_stop(marker, package_entrypoint, app_process)
            asyncio.run(
                require_executor_session_count(
                    database_url,
                    2,
                    stage="the offline hard stop",
                )
            )
            wait_for_signal(
                offline_observed_signal,
                app_process,
                label="the App's offline failure projection",
            )
            if set(persisted) != {"version", "task_id", "idempotency_key"} or (
                persisted.get("version") != "1"
                or persisted.get("task_id") != str(emergency_task)
                or not str(persisted.get("idempotency_key", "")).startswith(
                    "task-run:emergency_stop:"
                )
            ):
                raise RuntimeError("H8-03 persisted an invalid emergency-stop intent")

            print("[H8-03] Restarting the Control Plane for automatic App reconciliation")
            server = start_replacement_control_plane(environment)
            try:
                app_exit = app_process.wait(timeout=300)
            except subprocess.TimeoutExpired as error:
                raise RuntimeError("H8-03 hidden App acceptance did not finish") from error
            if app_exit != 0:
                raise RuntimeError("H8-03 hidden App acceptance failed")
            app_process = None

            assert_no_executor_process(package_entrypoint)
            asyncio.run(verify_database_state(database_url, offer))
            verify_local_state(private_app_data, offer, credential)
            print("[H8-03] Hidden-App offline emergency-stop recovery passed")
        finally:
            if app_process is not None:
                terminate_app_process_tree(app_process)
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
