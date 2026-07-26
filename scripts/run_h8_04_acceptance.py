#!/usr/bin/env python3
"""Run H8-04 through two hidden App processes around one hard App crash."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import closing, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

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
from sqlalchemy import select, text

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
    task_actions,
    task_commands,
    task_events,
    tasks,
)

TAURI_CONFIG = FRONTEND_ROOT / "src-tauri" / "tauri.app-crash-recovery-e2e.conf.json"
CONTROL_PLANE_PORT = reserve_control_plane_port()
APP_IDENTIFIER = "com.aventador.automationtool.h804acceptance"
ENVIRONMENT_ID = "h804-acceptance"
TASK_KEYWORD = "H8-04 App 崩溃恢复"
DEVICE_CREDENTIAL_FILE = "device-credential-v1"
EXECUTOR_BUILD_ID = "h8-04-app-crash-recovery"
EXECUTOR_LEDGER_FILE = "executor-ledger.sqlite3"


@dataclass(frozen=True, slots=True)
class DatabaseCheckpoint:
    task_row: tuple[object, ...]
    attempt_row: tuple[object, ...]
    commands: tuple[tuple[object, ...], ...]
    events: tuple[tuple[object, ...], ...]
    task_count: int
    attempt_count: int
    action_count: int
    definition_count: int


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
        raise RuntimeError("H8-04 requires one isolated visible=false App")


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
        "postgresql+asyncpg://automation_tool_h804:"
        f"{database_password}@127.0.0.1:{database_port}/automation_tool_h804"
    )
    bootstrap_token, bootstrap_public_key = signed_bootstrap()
    environment.update(
        {
            "AUTOMATION_TOOL_DEV_DB_USER": "unused_h804_dev",
            "AUTOMATION_TOOL_DEV_DB_PASSWORD": secrets.token_hex(24),
            "AUTOMATION_TOOL_DEV_DB_NAME": "unused_h804_dev",
            "AUTOMATION_TOOL_DEV_DB_PORT": str(unused_loopback_port()),
            "AUTOMATION_TOOL_TEST_DB_USER": "automation_tool_h804",
            "AUTOMATION_TOOL_TEST_DB_PASSWORD": database_password,
            "AUTOMATION_TOOL_TEST_DB_NAME": "automation_tool_h804",
            "AUTOMATION_TOOL_TEST_DB_PORT": str(database_port),
            "AUTOMATION_TOOL_DATABASE_URL": database_url,
            "AUTOMATION_TOOL_DEMO_ENVIRONMENT_ID": ENVIRONMENT_ID,
            "AUTOMATION_TOOL_DEMO_BOOTSTRAP_PUBLIC_KEY": bootstrap_public_key,
            "AUTOMATION_TOOL_H804_BOOTSTRAP_TOKEN": bootstrap_token,
            "AUTOMATION_TOOL_H804_ENVIRONMENT_ID": ENVIRONMENT_ID,
            "AUTOMATION_TOOL_CONTROL_PLANE_E2E_ORIGIN": (
                f"http://127.0.0.1:{CONTROL_PLANE_PORT}"
            ),
        }
    )
    return (
        startup_gate_environment(environment, control_plane_port=CONTROL_PLANE_PORT),
        database_url,
    )


def wait_for_signal(
    path: Path, process: subprocess.Popen[bytes], *, label: str
) -> dict[str, object]:
    deadline = time.monotonic() + 120
    while not path.is_file():
        if process.poll() is not None:
            raise RuntimeError(f"H8-04 App wrapper exited before {label}")
        if time.monotonic() >= deadline:
            raise RuntimeError(f"H8-04 timed out waiting for {label}")
        time.sleep(0.05)
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"H8-04 {label} signal is unreadable") from error
    if not isinstance(decoded, dict):
        raise RuntimeError(f"H8-04 {label} signal is invalid")
    return decoded


def require_app_process(process_id: int) -> None:
    if process_id <= 1 or process_id == os.getpid():
        raise RuntimeError("H8-04 refused an unsafe App process id")
    if sys.platform == "win32":
        completed = subprocess.run(
            ["tasklist", "/FI", f"PID eq {process_id}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        if "automation-tool-desktop.exe" not in completed.stdout.lower():
            raise RuntimeError("H8-04 PID does not identify the acceptance App")
        return
    completed = subprocess.run(
        ["ps", "-p", str(process_id), "-o", "command="],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    if "automation-tool-desktop" not in completed.stdout:
        raise RuntimeError("H8-04 PID does not identify the acceptance App")


def hard_kill_app(process_id: int) -> None:
    require_app_process(process_id)
    if sys.platform == "win32":
        completed = subprocess.run(
            ["taskkill", "/PID", str(process_id), "/F"],
            capture_output=True,
            timeout=15,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError("H8-04 failed to terminate the first App")
        return
    os.kill(process_id, signal.SIGKILL)


def wait_for_process_exit(process_id: int) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if sys.platform == "win32":
            completed = subprocess.run(
                ["tasklist", "/FI", f"PID eq {process_id}", "/NH"],
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
            if str(process_id) not in completed.stdout:
                return
        else:
            completed = subprocess.run(
                ["ps", "-p", str(process_id), "-o", "stat="],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if completed.returncode == 1:
                return
            if completed.returncode != 0:
                raise RuntimeError("H8-04 could not inspect the first App process")
            if not completed.stdout.strip() or completed.stdout.lstrip().startswith(
                "Z"
            ):
                return
        time.sleep(0.05)
    raise RuntimeError("H8-04 first App process did not exit after the hard kill")


def start_wdio(environment: dict[str, str], *, phase: str) -> subprocess.Popen[bytes]:
    phased = dict(environment)
    phased["AUTOMATION_TOOL_H804_PHASE"] = phase
    return subprocess.Popen(
        ["pnpm", "exec", "wdio", "run", "wdio.app-crash-recovery.conf.ts"],
        cwd=FRONTEND_ROOT,
        env=phased,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def collect_wdio(process: subprocess.Popen[bytes], *, timeout: int) -> tuple[int, str]:
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            output, _ = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
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
                (
                    await session.execute(
                        select(
                            douyin_search_exposure_definitions.c.installation_id,
                            douyin_search_exposure_definitions.c.task_id,
                            douyin_search_exposure_definitions.c.search_keyword,
                        ).where(
                            douyin_search_exposure_definitions.c.task_id
                            == typed_task.uuid
                        )
                    )
                )
                .mappings()
                .one()
            )
        if (
            definition["installation_id"] != typed_installation.uuid
            or definition["search_keyword"] != TASK_KEYWORD
        ):
            raise RuntimeError("H8-04 App-created Task identity is invalid")
    finally:
        await database.close()
    credential_path = private_app_data / DEVICE_CREDENTIAL_FILE
    if not credential_path.is_file():
        raise RuntimeError("H8-04 App credential vault is missing")
    credential = credential_path.read_text(encoding="ascii")
    return typed_installation, typed_task, credential


async def database_checkpoint(database_url: str, task_id: TaskId) -> DatabaseCheckpoint:
    database = Database.from_url(database_url)
    try:
        async with database.session() as session:
            task = (
                await session.execute(
                    select(
                        tasks.c.id,
                        tasks.c.installation_id,
                        tasks.c.status,
                        tasks.c.revision,
                        tasks.c.last_event_sequence,
                        tasks.c.created_at,
                    ).where(tasks.c.id == task_id.uuid)
                )
            ).one()
            attempt = (
                await session.execute(
                    select(
                        execution_attempts.c.id,
                        execution_attempts.c.status,
                        execution_attempts.c.revision,
                        execution_attempts.c.started_at,
                        execution_attempts.c.finished_at,
                    ).where(execution_attempts.c.task_id == task_id.uuid)
                )
            ).one()
            commands = tuple(
                (
                    await session.execute(
                        select(
                            task_commands.c.message_id,
                            task_commands.c.command_type,
                            task_commands.c.sequence,
                            task_commands.c.status,
                            task_commands.c.idempotency_key,
                        )
                        .where(task_commands.c.task_id == task_id.uuid)
                        .order_by(task_commands.c.sequence)
                    )
                ).all()
            )
            events = tuple(
                (
                    await session.execute(
                        select(
                            task_events.c.source_message_id,
                            task_events.c.event_type,
                            task_events.c.sequence,
                            task_events.c.task_revision,
                            task_events.c.task_status,
                            task_events.c.execution_attempt_id,
                            task_events.c.action_id,
                            task_events.c.progress_percent,
                            task_events.c.occurred_at,
                        )
                        .where(task_events.c.task_id == task_id.uuid)
                        .order_by(task_events.c.sequence)
                    )
                ).all()
            )
            task_count = int(
                await session.scalar(text("select count(*) from tasks")) or 0
            )
            attempt_count = int(
                await session.scalar(text("select count(*) from execution_attempts"))
                or 0
            )
            action_count = int(
                await session.scalar(select(text("count(*)")).select_from(task_actions))
                or 0
            )
            definition_count = int(
                await session.scalar(
                    select(text("count(*)")).select_from(
                        douyin_search_exposure_definitions
                    )
                )
                or 0
            )
            executor_sessions = int(
                await session.scalar(
                    text(
                        "select count(*) from device_sessions where capability = 'executor.connect'"
                    )
                )
                or 0
            )
        if (
            task[2] != TaskStatus.RUNNING.value
            or attempt[1] != ExecutionAttemptStatus.RUNNING.value
            or [row[1] for row in commands] != [TaskCommandType.TASK_OFFER.value]
            or commands[0][3] != TaskCommandStatus.ACKNOWLEDGED.value
            or [row[1] for row in events]
            != [TaskEventType.TASK_STARTED.value, TaskEventType.STEP_STARTED.value]
            or task_count != 1
            or attempt_count != 1
            or action_count != 0
            or definition_count != 1
            or executor_sessions != 2
        ):
            raise RuntimeError(
                "H8-04 running checkpoint contains unexpected business facts"
            )
        return DatabaseCheckpoint(
            task_row=tuple(task),
            attempt_row=tuple(attempt),
            commands=tuple(tuple(row) for row in commands),
            events=tuple(tuple(row) for row in events),
            task_count=task_count,
            attempt_count=attempt_count,
            action_count=action_count,
            definition_count=definition_count,
        )
    finally:
        await database.close()


def local_state(private_app_data: Path) -> tuple[tuple[object, ...], ...]:
    ledger_path = private_app_data / "local-executor" / "state" / EXECUTOR_LEDGER_FILE
    if not ledger_path.is_file():
        raise RuntimeError("H8-04 App-private Executor ledger is missing")
    with closing(sqlite3.connect(ledger_path)) as connection:
        command_rows = tuple(
            connection.execute(
                "SELECT message_id, idempotency_key, hex(intent_sha256), task_id, "
                "attempt_id, sequence, message_type, correlation_id "
                "FROM executor_commands ORDER BY sequence"
            ).fetchall()
        )
        checkpoint_rows = tuple(
            connection.execute(
                "SELECT attempt_id, task_id, last_command_sequence, "
                "last_event_sequence, state FROM executor_attempt_checkpoints"
            ).fetchall()
        )
        action_rows = tuple(
            connection.execute(
                "SELECT admission.action_id, side_effect.state, "
                "hex(side_effect.effect_fingerprint), "
                "hex(side_effect.verification_fingerprint) "
                "FROM executor_action_admissions AS admission "
                "JOIN executor_side_effects AS side_effect "
                "ON side_effect.action_id = admission.action_id "
                "ORDER BY admission.action_id"
            ).fetchall()
        )
    if (
        len(command_rows) != 1
        or command_rows[0][6] != "task.offer"
        or len(checkpoint_rows) != 1
        or checkpoint_rows[0][4] != "running"
        or sorted(row[1] for row in action_rows) != ["prepared", "verified"]
    ):
        raise RuntimeError("H8-04 local checkpoint is not the expected stable fixture")
    return command_rows + checkpoint_rows + action_rows


async def verify_database_state(
    database_url: str,
    task_id: TaskId,
    expected: DatabaseCheckpoint,
) -> None:
    actual = await database_checkpoint(database_url, task_id)
    if actual != expected:
        raise RuntimeError(
            "H8-04 App restart duplicated or changed Task business facts"
        )


def verify_local_state(
    private_app_data: Path,
    expected: tuple[tuple[object, ...], ...],
    credential: str,
) -> None:
    if local_state(private_app_data) != expected:
        raise RuntimeError("H8-04 App restart changed or replayed a local side effect")
    ledger = private_app_data / "local-executor" / "state" / EXECUTOR_LEDGER_FILE
    if credential.encode("ascii") in ledger.read_bytes():
        raise RuntimeError("H8-04 persisted the long-lived App credential in SQLite")
    verify_app_private_data(private_app_data)


def main() -> None:
    require_control_plane_port_available()
    require_hidden_tauri_configuration()
    private_app_data = app_data_directory()
    if private_app_data.exists():
        raise RuntimeError("Refusing to reuse an existing H8-04 App data directory")
    prepare_startup_gate(private_app_data, executor_package=False)

    project_name = f"automation-tool-h804-{os.getpid()}"
    database_port = unused_loopback_port()
    environment, database_url = isolated_environment(database_port)
    compose = compose_command(project_name)
    server: subprocess.Popen[bytes] | None = None
    first_app: subprocess.Popen[bytes] | None = None
    second_app: subprocess.Popen[bytes] | None = None
    package_entrypoint: Path | None = None

    with tempfile.TemporaryDirectory(prefix=f"{project_name}-") as temporary:
        workspace = Path(temporary)
        task_created_signal = workspace / "task-created.json"
        task_seeded_signal = workspace / "task-seeded.json"
        crash_ready_signal = workspace / "crash-ready.json"
        recovered_signal = workspace / "recovered.json"
        environment.update(
            {
                "AUTOMATION_TOOL_H804_TASK_CREATED_SIGNAL": os.fspath(
                    task_created_signal
                ),
                "AUTOMATION_TOOL_H804_TASK_SEEDED_SIGNAL": os.fspath(
                    task_seeded_signal
                ),
                "AUTOMATION_TOOL_H804_CRASH_READY_SIGNAL": os.fspath(
                    crash_ready_signal
                ),
                "AUTOMATION_TOOL_H804_RECOVERED_SIGNAL": os.fspath(recovered_signal),
            }
        )
        try:
            print("[H8-04] Building and signing the real PyInstaller Executor")
            package_source = build_signed_executor(
                workspace, build_id=EXECUTOR_BUILD_ID
            )
            package_root = install_executor_package(package_source)
            package_entrypoint = executor_entrypoint(package_root)

            print("[H8-04] Building the dedicated hidden Tauri App once")
            subprocess.run(
                ["pnpm", "build:tauri:app-crash-recovery-test"],
                check=True,
                cwd=FRONTEND_ROOT,
                env=environment,
            )
            print(f"[H8-04] Starting isolated PostgreSQL as {project_name}")
            subprocess.run(
                [*compose, "up", "--detach", "--wait", "postgres-test"],
                check=True,
                cwd=REPOSITORY_ROOT,
                env=environment,
            )
            print("[H8-04] Applying the production Alembic migration chain")
            subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", "head"],
                check=True,
                cwd=BACKEND_ROOT,
                env=environment,
            )
            print("[H8-04] Starting the real Control Plane")
            server = start_control_plane(
                port=CONTROL_PLANE_PORT,
                environment=environment,
            )

            print("[H8-04] Starting the first hidden App")
            first_app = start_wdio(environment, phase="before-crash")
            created = wait_for_signal(
                task_created_signal,
                first_app,
                label="the App-created Task",
            )
            installation_id = str(created.get("installationId", ""))
            task_id = str(created.get("taskId", ""))
            typed_installation, typed_task, credential = asyncio.run(
                read_task_identity(
                    database_url,
                    task_id,
                    installation_id,
                    private_app_data,
                )
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
                    label="h8-04-app-crash",
                    confirmed_target_revision=True,
                )
            )
            run_offer_fixture(credential, typed_installation)
            executor_id_path = private_app_data / "local-executor" / "executor-id-v1"
            executor_id = executor_id_path.read_text(encoding="ascii")
            ledger, dispatched, _prepared = seed_local_checkpoint(
                private_app_data / "local-executor" / "state",
                offer,
                executor_id,
            )
            ledger.verify_side_effect(
                action_id=dispatched[0],
                effect_fingerprint=dispatched[1],
                verification_fingerprint=hashlib.sha256(
                    b"h8-04-stable-platform-receipt"
                ).digest(),
                verified_at=datetime.now(UTC),
            )
            task_seeded_signal.write_text("{}\n", encoding="utf-8")

            crash_ready = wait_for_signal(
                crash_ready_signal,
                first_app,
                label="the running App crash checkpoint",
            )
            if (
                crash_ready.get("taskId") != task_id
                or crash_ready.get("installationId") != installation_id
            ):
                raise RuntimeError("H8-04 first App reported a mismatched Task")
            app_process_id = crash_ready.get("appProcessId")
            if not isinstance(app_process_id, int):
                raise RuntimeError("H8-04 first App omitted its process id")
            if (
                package_entrypoint is None
                or len(matching_executor_processes(package_entrypoint)) != 1
            ):
                raise RuntimeError("H8-04 did not start exactly one signed Executor")
            checkpoint = asyncio.run(database_checkpoint(database_url, typed_task))
            local_checkpoint = local_state(private_app_data)

            print(f"[H8-04] Hard-killing only the first App process {app_process_id}")
            hard_kill_app(app_process_id)
            wait_for_process_exit(app_process_id)
            first_exit, first_output = collect_wdio(first_app, timeout=45)
            first_app = None
            if first_exit == 0:
                raise RuntimeError(
                    "H8-04 first App phase exited normally instead of crashing"
                )
            if (
                package_entrypoint is None
                or len(matching_executor_processes(package_entrypoint)) != 1
            ):
                raise RuntimeError("H8-04 App crash interrupted its signed Executor")
            asyncio.run(verify_database_state(database_url, typed_task, checkpoint))
            verify_local_state(private_app_data, local_checkpoint, credential)

            print("[H8-04] Starting the second hidden App with the same AppData")
            environment["AUTOMATION_TOOL_H804_TASK_ID"] = task_id
            second_app = start_wdio(environment, phase="after-crash")
            recovered = wait_for_signal(
                recovered_signal,
                second_app,
                label="the restored authoritative UI",
            )
            if recovered.get("taskId") != task_id:
                raise RuntimeError("H8-04 second App restored a different Task")
            recovered_process_id = recovered.get("appProcessId")
            if (
                not isinstance(recovered_process_id, int)
                or recovered_process_id == app_process_id
            ):
                raise RuntimeError(
                    "H8-04 did not restore through a distinct second App"
                )
            second_exit, second_output = collect_wdio(second_app, timeout=120)
            second_app = None
            if second_exit != 0:
                print(second_output, end="")
                raise RuntimeError("H8-04 second hidden App acceptance failed")
            if (
                package_entrypoint is None
                or len(matching_executor_processes(package_entrypoint)) != 1
            ):
                raise RuntimeError(
                    "H8-04 second App duplicated or lost the signed Executor"
                )
            asyncio.run(verify_database_state(database_url, typed_task, checkpoint))
            verify_local_state(private_app_data, local_checkpoint, credential)
            if "app-crash-recovery" not in first_output:
                raise RuntimeError(
                    "H8-04 first WDIO phase did not run the intended spec"
                )
            print("[H8-04] Hidden-App crash recovery passed")
        finally:
            for process in (second_app, first_app):
                if process is not None and process.poll() is None:
                    process.terminate()
                    with suppress(subprocess.TimeoutExpired):
                        process.wait(timeout=10)
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=5)
            if package_entrypoint is not None:
                terminate_executor_processes(package_entrypoint)
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
