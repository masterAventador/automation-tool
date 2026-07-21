#!/usr/bin/env python3
"""Run H8-14 through one hidden App, real Uvicorn, and isolated PostgreSQL."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
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
from sqlalchemy import insert, text
from sqlalchemy.ext.asyncio import create_async_engine

from automation_tool.control_plane.domain import (
    ActionId,
    ActionOutcome,
    ActionStatus,
    ExecutionAttemptId,
    ExecutionAttemptStatus,
    InstallationId,
    TaskId,
    TaskStatus,
)
from automation_tool.control_plane.infrastructure.database import (
    Database,
    execution_attempts,
    installations,
    task_actions,
    tasks,
)
from automation_tool.control_plane.infrastructure.database.workbench_metrics_repository import (
    SqlAlchemyWorkbenchMetricsRepository,
)
from automation_tool.protocol import MAX_EXECUTOR_MESSAGE_BYTES, ActionResultEvidence

TAURI_CONFIG = FRONTEND_ROOT / "src-tauri" / "tauri.workbench-metrics-e2e.conf.json"
CONTROL_PLANE_PORT = 8765
APP_IDENTIFIER = "com.aventador.automationtool.h814acceptance"
ENVIRONMENT_ID = "h814-acceptance"
TASK_KEY = "task:workbench-metrics:tauri-acceptance"
DEVICE_CREDENTIAL_FILE = "device-credential-v1"


def require_control_plane_port_available() -> None:
    with socket.socket() as listener:
        try:
            listener.bind(("127.0.0.1", CONTROL_PLANE_PORT))
        except OSError as error:
            raise RuntimeError("H8-14 requires an unused Control Plane port") from error


def require_hidden_configuration() -> None:
    configuration = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))
    windows = configuration.get("app", {}).get("windows", [])
    if (
        configuration.get("identifier") != APP_IDENTIFIER
        or len(windows) != 1
        or windows[0].get("visible") is not False
    ):
        raise RuntimeError("H8-14 acceptance must use one isolated hidden App")


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
    payload = json.dumps(
        {
            "environmentId": ENVIRONMENT_ID,
            "expiresAt": int((now + timedelta(hours=1)).timestamp()),
            "notBefore": int((now - timedelta(seconds=30)).timestamp()),
            "purpose": "installation.register",
            "version": 1,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    payload_segment = base64url(payload)
    signing_input = f"atb1.{payload_segment}".encode("ascii")
    token = f"atb1.{payload_segment}.{base64url(signer.sign(signing_input))}"
    return token, base64url(signer.public_key().public_bytes_raw())


def isolated_environment(database_port: int, webdriver_port: int) -> tuple[dict[str, str], str]:
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("AUTOMATION_TOOL_")
    }
    password = secrets.token_hex(24)
    database_url = (
        "postgresql+asyncpg://automation_tool_h814:"
        f"{password}@127.0.0.1:{database_port}/automation_tool_h814"
    )
    bootstrap_token, bootstrap_public_key = signed_bootstrap()
    environment.update(
        {
            "AUTOMATION_TOOL_DEV_DB_USER": "unused_h814_dev",
            "AUTOMATION_TOOL_DEV_DB_PASSWORD": secrets.token_hex(24),
            "AUTOMATION_TOOL_DEV_DB_NAME": "unused_h814_dev",
            "AUTOMATION_TOOL_DEV_DB_PORT": str(unused_loopback_port()),
            "AUTOMATION_TOOL_TEST_DB_USER": "automation_tool_h814",
            "AUTOMATION_TOOL_TEST_DB_PASSWORD": password,
            "AUTOMATION_TOOL_TEST_DB_NAME": "automation_tool_h814",
            "AUTOMATION_TOOL_TEST_DB_PORT": str(database_port),
            "AUTOMATION_TOOL_DATABASE_URL": database_url,
            "AUTOMATION_TOOL_DEMO_ENVIRONMENT_ID": ENVIRONMENT_ID,
            "AUTOMATION_TOOL_DEMO_BOOTSTRAP_PUBLIC_KEY": bootstrap_public_key,
            "AUTOMATION_TOOL_H814_BOOTSTRAP_TOKEN": bootstrap_token,
            "AUTOMATION_TOOL_H814_ENVIRONMENT_ID": ENVIRONMENT_ID,
            "TAURI_WEBDRIVER_PORT": str(webdriver_port),
        }
    )
    return environment, database_url


async def wait_for_app_task(
    database_url: str,
    private_app_data: Path,
    app_process: subprocess.Popen[bytes],
) -> tuple[InstallationId, TaskId]:
    engine = create_async_engine(database_url)
    deadline = time.monotonic() + 180
    try:
        while True:
            if app_process.poll() is not None:
                raise RuntimeError("H8-14 hidden App exited before registration")
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
            if row is not None and (private_app_data / DEVICE_CREDENTIAL_FILE).is_file():
                return InstallationId.parse(row[0]), TaskId.parse(row[1])
            if time.monotonic() >= deadline:
                raise RuntimeError("H8-14 hidden App did not register in time")
            await asyncio.sleep(0.05)
    finally:
        await engine.dispose()


async def seed_metric_facts(
    database: Database,
    installation_id: InstallationId,
    *,
    prefix: str,
) -> None:
    statuses = (
        TaskStatus.SUCCEEDED,
        TaskStatus.PARTIALLY_SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.AWAITING_HUMAN,
        TaskStatus.OUTCOME_UNCERTAIN,
        TaskStatus.RUNNING,
        TaskStatus.CANCELLED,
    )
    task_ids = tuple(TaskId.new() for _ in statuses)
    attempt_id = ExecutionAttemptId.new()
    now = datetime.now(UTC)
    async with database.session() as session:
        await session.execute(
            insert(tasks),
            [
                {
                    "id": task_id.uuid,
                    "installation_id": installation_id.uuid,
                    "creation_idempotency_key": f"task:h814:{prefix}:{index}",
                    "status": status.value,
                    "revision": 1,
                    "created_at": now,
                    "updated_at": now,
                }
                for index, (task_id, status) in enumerate(zip(task_ids, statuses, strict=True))
            ],
        )
        await session.execute(
            insert(execution_attempts).values(
                id=attempt_id.uuid,
                task_id=task_ids[5].uuid,
                installation_id=installation_id.uuid,
                attempt_number=1,
                status=ExecutionAttemptStatus.RUNNING.value,
                revision=1,
                created_at=now,
                updated_at=now,
                started_at=now,
            )
        )
        action_facts = (
            (
                ActionStatus.VERIFIED,
                ActionOutcome.SUCCEEDED,
                ActionResultEvidence.COMMENT_CONFIRMED,
                now,
            ),
            (
                ActionStatus.VERIFIED,
                ActionOutcome.FAILED,
                ActionResultEvidence.LOGIN_REQUIRED,
                now,
            ),
            (
                ActionStatus.OUTCOME_UNCERTAIN,
                ActionOutcome.OUTCOME_UNCERTAIN,
                ActionResultEvidence.FINAL_STATE_UNCONFIRMED,
                now,
            ),
            (
                ActionStatus.CANCELLED,
                ActionOutcome.CANCELLED,
                ActionResultEvidence.ACTION_CANCELLED,
                now,
            ),
            (ActionStatus.PLANNED, ActionOutcome.PENDING, None, None),
        )
        await session.execute(
            insert(task_actions),
            [
                {
                    "id": ActionId.new().uuid,
                    "execution_attempt_id": attempt_id.uuid,
                    "task_id": task_ids[5].uuid,
                    "installation_id": installation_id.uuid,
                    "ordinal": ordinal,
                    "status": status.value,
                    "outcome": outcome.value,
                    "evidence_code": None if evidence is None else evidence.value,
                    "revision": 1,
                    "created_at": now,
                    "updated_at": now,
                    "finished_at": finished_at,
                }
                for ordinal, (status, outcome, evidence, finished_at) in enumerate(
                    action_facts, start=1
                )
            ],
        )


async def seed_and_verify(database_url: str, installation_id: InstallationId) -> None:
    database = Database.from_url(database_url)
    try:
        await seed_metric_facts(database, installation_id, prefix="current")
        other_installation = InstallationId.new()
        now = datetime.now(UTC)
        async with database.session() as session:
            await session.execute(
                insert(installations).values(
                    id=other_installation.uuid,
                    device_public_key=secrets.token_bytes(32),
                    created_at=now,
                    updated_at=now,
                )
            )
        await seed_metric_facts(database, other_installation, prefix="other")
        metrics = await SqlAlchemyWorkbenchMetricsRepository(database).get(
            installation_id=installation_id
        )
        if (
            metrics.task_total,
            metrics.task_succeeded,
            metrics.task_failed,
            metrics.task_handoff_required,
            metrics.task_outcome_uncertain,
            metrics.action_total,
            metrics.action_succeeded,
            metrics.action_failed,
            metrics.action_outcome_uncertain,
        ) != (8, 2, 1, 1, 1, 5, 1, 1, 1):
            raise RuntimeError("H8-14 seeded metric snapshot is invalid")
    finally:
        await database.close()


async def verify_final_state(database_url: str, installation_id: InstallationId) -> None:
    database = Database.from_url(database_url)
    try:
        metrics = await SqlAlchemyWorkbenchMetricsRepository(database).get(
            installation_id=installation_id
        )
        if metrics.task_total != 8 or metrics.action_total != 5:
            raise RuntimeError("H8-14 App changed read-only metric facts")
        async with database.session() as session:
            capabilities = list(
                await session.scalars(text("select capability from device_sessions order by id"))
            )
        if not capabilities or any(value != "app.control-plane" for value in capabilities):
            raise RuntimeError("H8-14 used an unexpected Session capability")
    finally:
        await database.close()


def main() -> None:
    require_control_plane_port_available()
    require_hidden_configuration()
    private_app_data = app_data_directory()
    if private_app_data.exists():
        raise RuntimeError("Refusing to reuse an existing H8-14 App data directory")
    database_port = unused_loopback_port()
    webdriver_port = unused_loopback_port()
    require_port_closed(webdriver_port)
    environment, database_url = isolated_environment(database_port, webdriver_port)
    compose = compose_command(f"automation-tool-h814-{os.getpid()}")
    server: subprocess.Popen[bytes] | None = None
    app_process: subprocess.Popen[bytes] | None = None
    restore_failed = False
    try:
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
        app_process = subprocess.Popen(
            ["pnpm", "test:h8-14-app"],
            cwd=FRONTEND_ROOT,
            env=environment,
        )
        installation_id, _task_id = asyncio.run(
            wait_for_app_task(database_url, private_app_data, app_process)
        )
        asyncio.run(seed_and_verify(database_url, installation_id))
        try:
            app_exit = app_process.wait(timeout=240)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("H8-14 hidden App acceptance did not finish") from error
        if app_exit != 0:
            raise RuntimeError("H8-14 hidden App acceptance failed")
        app_process = None
        verify_app_private_data(private_app_data)
        asyncio.run(verify_final_state(database_url, installation_id))
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
        restore = subprocess.run(
            ["pnpm", "build"],
            cwd=FRONTEND_ROOT,
            env=environment,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        restore_failed = restore.returncode != 0
        if private_app_data.exists():
            shutil.rmtree(private_app_data)
        require_port_closed(CONTROL_PLANE_PORT)
        require_port_closed(database_port)
        require_port_closed(webdriver_port)
    if restore_failed:
        raise RuntimeError("H8-14 failed to restore production Vite assets")


if __name__ == "__main__":
    main()
