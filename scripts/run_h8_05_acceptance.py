#!/usr/bin/env python3
"""Run H8-05 through the hidden App, signed Executor, and real Control Plane."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import closing, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from desktop_e2e_prerequisites import (
    prepare_startup_gate,
    require_reserved_port_still_free,
    reserve_control_plane_port,
    startup_gate_environment,
)
from run_e4_07_acceptance import build_signed_executor
from run_e4_14_acceptance import (
    executor_entrypoint,
    install_executor_package,
    matching_executor_processes,
    start_control_plane,
    terminate_executor_processes,
)
from run_h8_01_acceptance import run_offer_fixture, seed_local_checkpoint
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
from sqlalchemy import insert, select, text

from automation_tool.control_plane.domain import (
    ActionOutcome,
    ActionStatus,
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
    task_actions,
    task_commands,
    task_events,
    tasks,
)

TAURI_CONFIG = FRONTEND_ROOT / "src-tauri" / "tauri.executor-crash-recovery-e2e.conf.json"
CONTROL_PLANE_PORT = reserve_control_plane_port()
APP_IDENTIFIER = "com.aventador.automationtool.h805acceptance"
ENVIRONMENT_ID = "h805-acceptance"
TASK_KEYWORD = "H8-05 Executor 崩溃恢复"
DEVICE_CREDENTIAL_FILE = "device-credential-v1"
EXECUTOR_BUILD_ID = "h8-05-executor-crash-recovery"
EXECUTOR_LEDGER_FILE = "executor-ledger.sqlite3"


def require_control_plane_port_available() -> None:
    require_reserved_port_still_free(CONTROL_PLANE_PORT)


def require_hidden_tauri_configuration() -> None:
    configuration = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))
    windows = configuration.get("app", {}).get("windows", [])
    if (
        configuration.get("identifier") != APP_IDENTIFIER
        or len(windows) != 1
        or windows[0].get("visible") is not False
    ):
        raise RuntimeError("H8-05 requires one isolated visible=false App")


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
        "postgresql+asyncpg://automation_tool_h805:"
        f"{password}@127.0.0.1:{database_port}/automation_tool_h805"
    )
    token, public_key = signed_bootstrap()
    environment.update(
        {
            "AUTOMATION_TOOL_DEV_DB_USER": "unused_h805_dev",
            "AUTOMATION_TOOL_DEV_DB_PASSWORD": secrets.token_hex(24),
            "AUTOMATION_TOOL_DEV_DB_NAME": "unused_h805_dev",
            "AUTOMATION_TOOL_DEV_DB_PORT": str(unused_loopback_port()),
            "AUTOMATION_TOOL_TEST_DB_USER": "automation_tool_h805",
            "AUTOMATION_TOOL_TEST_DB_PASSWORD": password,
            "AUTOMATION_TOOL_TEST_DB_NAME": "automation_tool_h805",
            "AUTOMATION_TOOL_TEST_DB_PORT": str(database_port),
            "AUTOMATION_TOOL_DATABASE_URL": database_url,
            "AUTOMATION_TOOL_DEMO_ENVIRONMENT_ID": ENVIRONMENT_ID,
            "AUTOMATION_TOOL_DEMO_BOOTSTRAP_PUBLIC_KEY": public_key,
            "AUTOMATION_TOOL_H805_BOOTSTRAP_TOKEN": token,
            "AUTOMATION_TOOL_H805_ENVIRONMENT_ID": ENVIRONMENT_ID,
            "AUTOMATION_TOOL_CONTROL_PLANE_E2E_ORIGIN": (f"http://127.0.0.1:{CONTROL_PLANE_PORT}"),
        }
    )
    return (
        startup_gate_environment(environment, control_plane_port=CONTROL_PLANE_PORT),
        database_url,
    )


def wait_for_signal(
    path: Path, process: subprocess.Popen[bytes], *, label: str
) -> dict[str, object]:
    deadline = time.monotonic() + 150
    while not path.is_file():
        if process.poll() is not None:
            raise RuntimeError(f"H8-05 App exited before {label}")
        if time.monotonic() >= deadline:
            raise RuntimeError(f"H8-05 timed out waiting for {label}")
        time.sleep(0.05)
    decoded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(decoded, dict):
        raise RuntimeError(f"H8-05 {label} signal is invalid")
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
            raise RuntimeError("H8-05 App-created Task identity is invalid")
    finally:
        await database.close()
    credential_path = private_app_data / DEVICE_CREDENTIAL_FILE
    if not credential_path.is_file():
        raise RuntimeError("H8-05 App credential vault is missing")
    return typed_installation, typed_task, credential_path.read_text(encoding="ascii")


async def seed_server_action(
    database_url: str,
    installation_id: InstallationId,
    task_id: TaskId,
    attempt_id: UUID,
    action_id: str,
) -> None:
    database = Database.from_url(database_url)
    now = datetime.now(UTC)
    try:
        async with database.session() as session:
            await session.execute(
                insert(task_actions).values(
                    id=UUID(action_id),
                    execution_attempt_id=attempt_id,
                    task_id=task_id.uuid,
                    installation_id=installation_id.uuid,
                    ordinal=1,
                    status=ActionStatus.DISPATCHED.value,
                    outcome=ActionOutcome.PENDING.value,
                    revision=2,
                    created_at=now,
                    updated_at=now,
                )
            )
    finally:
        await database.close()


async def verify_server_recovery(database_url: str, task_id: TaskId) -> None:
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
            command_rows = (
                await session.execute(
                    select(task_commands.c.command_type, task_commands.c.status).where(
                        task_commands.c.task_id == task_id.uuid
                    )
                )
            ).all()
            event_rows = (
                await session.execute(
                    select(
                        task_events.c.event_type,
                        task_events.c.sequence,
                        task_events.c.action_id,
                    )
                    .where(task_events.c.task_id == task_id.uuid)
                    .order_by(task_events.c.sequence)
                )
            ).all()
            action = (
                await session.execute(
                    select(
                        task_actions.c.status,
                        task_actions.c.outcome,
                        task_actions.c.evidence_code,
                        task_actions.c.revision,
                    ).where(task_actions.c.task_id == task_id.uuid)
                )
            ).one()
            session_count = int(
                await session.scalar(
                    text(
                        "select count(*) from device_sessions where capability = 'executor.connect'"
                    )
                )
                or 0
            )
            counts_list: list[int] = []
            for table in (tasks, execution_attempts, task_commands, task_actions):
                counts_list.append(
                    int(await session.scalar(select(text("count(*)")).select_from(table)) or 0)
                )
            counts = tuple(counts_list)
        if (
            task != (TaskStatus.OUTCOME_UNCERTAIN.value, 3)
            or attempt != (ExecutionAttemptStatus.OUTCOME_UNCERTAIN.value,)
            or command_rows
            != [(TaskCommandType.TASK_OFFER.value, TaskCommandStatus.ACKNOWLEDGED.value)]
            or [row[:2] for row in event_rows]
            != [
                (TaskEventType.TASK_STARTED.value, 1),
                (TaskEventType.STEP_STARTED.value, 2),
                (TaskEventType.TASK_OUTCOME_UNCERTAIN.value, 3),
            ]
            or event_rows[2][2] is None
            or action
            != (
                ActionStatus.OUTCOME_UNCERTAIN.value,
                ActionOutcome.OUTCOME_UNCERTAIN.value,
                "recovery_unconfirmed",
                3,
            )
            or session_count != 2
            or counts != (1, 1, 1, 1)
        ):
            raise RuntimeError("H8-05 Control Plane did not converge exact recovery facts")
    finally:
        await database.close()


def verify_local_recovery(private_app_data: Path, credential: str) -> None:
    ledger_path = private_app_data / "local-executor" / "state" / EXECUTOR_LEDGER_FILE
    with closing(sqlite3.connect(ledger_path)) as connection:
        checkpoint = connection.execute(
            "SELECT last_command_sequence, last_event_sequence, state, revision "
            "FROM executor_attempt_checkpoints"
        ).fetchall()
        effects = connection.execute(
            "SELECT state, revision FROM executor_side_effects ORDER BY prepared_at"
        ).fetchall()
        outbox = connection.execute(
            "SELECT idempotency_key, json_extract(envelope, '$.message_type'), "
            "json_extract(envelope, '$.payload.evidence'), delivered "
            "FROM executor_outbox ORDER BY ordinal"
        ).fetchall()
    if (
        checkpoint != [(1, 3, "outcome_uncertain", 3)]
        or effects != [("uncertain", 3), ("prepared", 1)]
        or len(outbox) != 1
        or not str(outbox[0][0]).startswith("executor:recovery:")
        or outbox[0][1:] != ("task.outcome_uncertain", "recovery_unconfirmed", 1)
    ):
        raise RuntimeError("H8-05 SQLite ledger/outbox did not align exactly once")
    if credential.encode("ascii") in ledger_path.read_bytes():
        raise RuntimeError("H8-05 persisted the App credential in SQLite")
    verify_app_private_data(private_app_data)


def main() -> None:
    require_control_plane_port_available()
    require_hidden_tauri_configuration()
    private_app_data = app_data_directory()
    if private_app_data.exists():
        raise RuntimeError("Refusing to reuse an existing H8-05 App data directory")
    prepare_startup_gate(private_app_data, executor_package=False)

    project_name = f"automation-tool-h805-{os.getpid()}"
    database_port = unused_loopback_port()
    environment, database_url = isolated_environment(database_port)
    compose = compose_command(project_name)
    server: subprocess.Popen[bytes] | None = None
    app: subprocess.Popen[bytes] | None = None
    package_entrypoint: Path | None = None

    with tempfile.TemporaryDirectory(prefix=f"{project_name}-") as temporary:
        workspace = Path(temporary)
        task_created_signal = workspace / "task-created.json"
        task_seeded_signal = workspace / "task-seeded.json"
        recovered_signal = workspace / "recovered.json"
        environment.update(
            {
                "AUTOMATION_TOOL_H805_TASK_CREATED_SIGNAL": os.fspath(task_created_signal),
                "AUTOMATION_TOOL_H805_TASK_SEEDED_SIGNAL": os.fspath(task_seeded_signal),
                "AUTOMATION_TOOL_H805_RECOVERED_SIGNAL": os.fspath(recovered_signal),
            }
        )
        try:
            print("[H8-05] Building and signing the real PyInstaller Executor")
            package_source = build_signed_executor(workspace, build_id=EXECUTOR_BUILD_ID)
            package_root = install_executor_package(package_source, private_app_data)
            package_entrypoint = executor_entrypoint(package_root)

            print("[H8-05] Building the dedicated hidden Tauri App")
            subprocess.run(
                ["pnpm", "build:tauri:executor-crash-recovery-test"],
                check=True,
                cwd=FRONTEND_ROOT,
                env=environment,
            )
            print(f"[H8-05] Starting isolated PostgreSQL as {project_name}")
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
            server = start_control_plane(port=CONTROL_PLANE_PORT, environment=environment)

            app = subprocess.Popen(
                ["pnpm", "exec", "wdio", "run", "wdio.executor-crash-recovery.conf.ts"],
                cwd=FRONTEND_ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            created = wait_for_signal(task_created_signal, app, label="the App-created Task")
            installation_id = str(created.get("installationId", ""))
            task_id = str(created.get("taskId", ""))
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
                    label="h8-05-executor-crash",
                    confirmed_target_revision=True,
                )
            )
            run_offer_fixture(credential, typed_installation)
            executor_id = (private_app_data / "local-executor" / "executor-id-v1").read_text(
                encoding="ascii"
            )
            _ledger, dispatched, _prepared = seed_local_checkpoint(
                private_app_data / "local-executor" / "state",
                offer,
                executor_id,
            )
            asyncio.run(
                seed_server_action(
                    database_url,
                    typed_installation,
                    typed_task,
                    offer.execution_attempt_id.uuid,
                    dispatched[0],
                )
            )
            task_seeded_signal.write_text("{}\n", encoding="utf-8")

            recovered = wait_for_signal(
                recovered_signal,
                app,
                label="the restarted Executor and uncertain Task",
            )
            if (
                recovered.get("taskId") != task_id
                or recovered.get("installationId") != installation_id
                or recovered.get("restart_count") != 1
            ):
                raise RuntimeError("H8-05 App reported a mismatched recovery result")
            if (
                package_entrypoint is None
                or len(matching_executor_processes(package_entrypoint)) != 1
            ):
                raise RuntimeError("H8-05 did not retain exactly one signed Executor")
            asyncio.run(verify_server_recovery(database_url, typed_task))
            verify_local_recovery(private_app_data, credential)

            exit_code, output = collect_wdio(app, timeout=60)
            app = None
            if exit_code != 0:
                print(output, end="")
                raise RuntimeError("H8-05 hidden App acceptance failed")
            print("[H8-05] Hidden-App Executor crash recovery passed")
        finally:
            if app is not None and app.poll() is None:
                app.terminate()
                with suppress(subprocess.TimeoutExpired):
                    app.wait(timeout=10)
                if app.poll() is None:
                    app.kill()
                    app.wait(timeout=5)
            if package_entrypoint is not None:
                terminate_executor_processes(package_entrypoint)
            if server is not None and server.poll() is None:
                server.terminate()
                with suppress(subprocess.TimeoutExpired):
                    server.wait(timeout=10)
                if server.poll() is None:
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
