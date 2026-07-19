#!/usr/bin/env python3
"""Run E4-12 through PostgreSQL, Uvicorn, Rust Manager, and packaged Executor."""

from __future__ import annotations

import asyncio
import json
import os
import platform
import secrets
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

from run_e4_07_acceptance import RUST_ROOT, build_signed_executor
from run_i2_13_acceptance import (
    BACKEND_ROOT,
    REPOSITORY_ROOT,
    compose_command,
    post_json,
    require_port_closed,
    seed_active_credential,
    unused_loopback_port,
    wait_for_control_plane,
)
from run_t3_11_acceptance import seed_attempt_and_offer, wait_for_convergence
from sqlalchemy import select

from automation_tool.control_plane.application.device_sessions import (
    DeviceSessionCapability,
)
from automation_tool.control_plane.application.task_command_delivery import (
    TaskCommandRecord,
)
from automation_tool.control_plane.infrastructure.database import (
    Database,
    task_commands,
    task_events,
)
from automation_tool.protocol import MAX_EXECUTOR_MESSAGE_BYTES


def isolated_environment(database_port: int) -> tuple[dict[str, str], str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("AUTOMATION_TOOL_")
    }
    database_password = secrets.token_hex(24)
    database_url = (
        "postgresql+asyncpg://automation_tool_e412:"
        f"{database_password}@127.0.0.1:{database_port}/automation_tool_e412"
    )
    environment.update(
        {
            "AUTOMATION_TOOL_DEV_DB_USER": "unused_e412_dev",
            "AUTOMATION_TOOL_DEV_DB_PASSWORD": secrets.token_hex(24),
            "AUTOMATION_TOOL_DEV_DB_NAME": "unused_e412_dev",
            "AUTOMATION_TOOL_DEV_DB_PORT": str(unused_loopback_port()),
            "AUTOMATION_TOOL_TEST_DB_USER": "automation_tool_e412",
            "AUTOMATION_TOOL_TEST_DB_PASSWORD": database_password,
            "AUTOMATION_TOOL_TEST_DB_NAME": "automation_tool_e412",
            "AUTOMATION_TOOL_TEST_DB_PORT": str(database_port),
            "AUTOMATION_TOOL_DATABASE_URL": database_url,
        }
    )
    return environment, database_url


def start_control_plane(
    *,
    port: int,
    environment: dict[str, str],
) -> subprocess.Popen[bytes]:
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
            str(port),
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
    wait_for_control_plane(port, server)
    return server


def issue_executor_session(port: int, credential: str) -> str:
    exchanged = post_json(
        port,
        "/api/v1/device-sessions",
        credential,
        payload={"capability": DeviceSessionCapability.EXECUTOR_CONNECT.value},
        expected_status=201,
    )
    session_token = exchanged.get("sessionToken")
    if not isinstance(session_token, str):
        raise RuntimeError("E4-12 Session exchange omitted its opaque token")
    return session_token


def run_rust_manager(
    *,
    package_root: Path,
    workspace: Path,
    control_plane_port: int,
    session_token: str,
    installation_id: str,
    executor_id: str,
) -> None:
    configuration_path = workspace / "executor-manager.json"
    configuration_path.write_text(
        json.dumps(
            {
                "packageRoot": os.fspath(package_root),
                "websocketUrl": (
                    f"ws://127.0.0.1:{control_plane_port}/api/v1/executors/connect"
                ),
                "sessionToken": session_token,
                "installationId": installation_id,
                "executorId": executor_id,
                "stateDirectory": os.fspath(workspace / "executor-state"),
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    configuration_path.chmod(0o600)
    environment = os.environ.copy()
    environment["AUTOMATION_TOOL_E407_CONFIGURATION"] = os.fspath(configuration_path)
    completed = subprocess.run(
        [
            "cargo",
            "test",
            "--locked",
            "--test",
            "executor_manager",
            "real_packaged_executor_uses_the_public_manager_lifecycle",
            "--",
            "--ignored",
            "--exact",
        ],
        cwd=RUST_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if session_token in completed.stdout or session_token in completed.stderr:
        raise RuntimeError("E4-12 Rust Manager reflected the Control Plane Session")
    if completed.returncode != 0:
        diagnostic = (completed.stdout + "\n" + completed.stderr).replace(
            session_token,
            "[REDACTED]",
        )[-4000:]
        raise RuntimeError(f"E4-12 Rust Manager acceptance failed\n{diagnostic}")


def ledger_snapshot(workspace: Path, original: TaskCommandRecord) -> tuple[object, ...]:
    ledger_path = workspace / "executor-state" / "executor-ledger.sqlite3"
    if not ledger_path.is_file():
        raise RuntimeError("E4-12 packaged Executor did not create its durable ledger")
    with sqlite3.connect(ledger_path) as connection:
        commands = connection.execute(
            "SELECT message_id, idempotency_key FROM executor_commands ORDER BY message_id"
        ).fetchall()
        checkpoints = connection.execute(
            """
            SELECT attempt_id, state, last_command_sequence, last_event_sequence, revision
            FROM executor_attempt_checkpoints ORDER BY attempt_id
            """
        ).fetchall()
        outbox = connection.execute(
            """
            SELECT message_id, idempotency_key, envelope, delivered
            FROM executor_outbox ORDER BY ordinal
            """
        ).fetchall()
    if commands != [(str(original.message_id), original.idempotency_key)]:
        raise RuntimeError(
            "E4-12 command receipt is not the original Control Plane command"
        )
    if checkpoints != [(str(original.execution_attempt_id), "terminal", 1, 5, 2)]:
        raise RuntimeError("E4-12 Attempt checkpoint did not converge atomically")
    if len(outbox) != 6 or any(row[3] != 1 for row in outbox):
        raise RuntimeError("E4-12 durable outbox is incomplete or not fully delivered")
    message_types = [json.loads(row[2])["message_type"] for row in outbox]
    if message_types != [
        "task.accept",
        "task.started",
        "step.started",
        "step.progress",
        "step.completed",
        "task.completed",
    ]:
        raise RuntimeError(
            "E4-12 durable outcome batch is not the fixed success sequence"
        )
    return commands, checkpoints, outbox


async def control_plane_snapshot(
    database_url: str,
    original: TaskCommandRecord,
) -> tuple[object, ...]:
    database = Database.from_url(database_url)
    try:
        async with database.session() as session:
            command = (
                (
                    await session.execute(
                        select(
                            task_commands.c.message_id,
                            task_commands.c.status,
                            task_commands.c.response_message_id,
                            task_commands.c.delivery_attempts,
                        ).where(task_commands.c.message_id == original.message_id)
                    )
                )
                .tuples()
                .one()
            )
            events = (
                (
                    await session.execute(
                        select(
                            task_events.c.sequence,
                            task_events.c.event_type,
                            task_events.c.source_message_id,
                            task_events.c.progress_percent,
                        )
                        .where(task_events.c.task_id == original.task_id.uuid)
                        .order_by(task_events.c.sequence)
                    )
                )
                .tuples()
                .all()
            )
    finally:
        await database.close()
    if len(events) != 5 or [row[0] for row in events] != [1, 2, 3, 4, 5]:
        raise RuntimeError(
            "E4-12 Control Plane did not retain one contiguous event timeline"
        )
    if events[2][3] != 100:
        raise RuntimeError(
            "E4-12 Control Plane did not retain the real progress payload"
        )
    return command, tuple(events)


def stop_process(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def main() -> None:
    if platform.system() != "Darwin":
        raise RuntimeError("E4-12 local packaged acceptance currently requires macOS")
    project_name = f"automation-tool-e412-{os.getpid()}"
    database_port = unused_loopback_port()
    control_plane_port = unused_loopback_port()
    environment, database_url = isolated_environment(database_port)
    compose = compose_command(project_name)
    server: subprocess.Popen[bytes] | None = None
    try:
        print("[E4-12] Starting isolated PostgreSQL")
        subprocess.run(
            [*compose, "up", "--detach", "--wait", "postgres-test"],
            check=True,
            cwd=REPOSITORY_ROOT,
            env=environment,
        )
        print("[E4-12] Applying the production Alembic migration chain")
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            check=True,
            cwd=BACKEND_ROOT,
            env=environment,
        )
        credential, installation_id = asyncio.run(seed_active_credential(database_url))
        original = asyncio.run(seed_attempt_and_offer(database_url, installation_id))
        server = start_control_plane(port=control_plane_port, environment=environment)
        session_token = issue_executor_session(control_plane_port, credential)
        with tempfile.TemporaryDirectory(prefix="automation-tool-e4-12-") as directory:
            workspace = Path(directory).resolve(strict=True)
            print("[E4-12] Building and signing the real PyInstaller Executor")
            package_root = build_signed_executor(workspace, build_id="e4-12-real")
            arguments = {
                "package_root": package_root,
                "workspace": workspace,
                "control_plane_port": control_plane_port,
                "session_token": session_token,
                "installation_id": str(installation_id),
                "executor_id": "123e4567-e89b-42d3-a456-426614174004",
            }
            print("[E4-12] Dispatching through Rust Manager and the packaged Executor")
            run_rust_manager(**arguments)
            asyncio.run(wait_for_convergence(database_url, original))
            first_ledger = ledger_snapshot(workspace, original)
            first_control_plane = asyncio.run(
                control_plane_snapshot(database_url, original)
            )
            print("[E4-12] Restarting against the same ledger for exact replay")
            run_rust_manager(**arguments)
            asyncio.run(wait_for_convergence(database_url, original))
            second_ledger = ledger_snapshot(workspace, original)
            second_control_plane = asyncio.run(
                control_plane_snapshot(database_url, original)
            )
            if first_ledger != second_ledger:
                raise RuntimeError(
                    "E4-12 restart regenerated durable Executor messages"
                )
            if first_control_plane != second_control_plane:
                raise RuntimeError(
                    "E4-12 restart duplicated or advanced Control Plane facts"
                )
            ledger_bytes = (
                workspace / "executor-state" / "executor-ledger.sqlite3"
            ).read_bytes()
            if session_token.encode() in ledger_bytes:
                raise RuntimeError(
                    "E4-12 persisted the Control Plane Session in SQLite"
                )
        print(
            "E4-12 acceptance passed: Control Plane -> Rust Manager -> packaged Executor "
            "-> durable exact replay"
        )
    finally:
        stop_process(server)
        subprocess.run(
            [*compose, "down", "--volumes", "--remove-orphans"],
            check=False,
            cwd=REPOSITORY_ROOT,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        require_port_closed(control_plane_port)
        require_port_closed(database_port)


if __name__ == "__main__":
    main()
