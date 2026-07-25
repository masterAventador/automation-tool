#!/usr/bin/env python3
"""Run H8-07 through one hidden App, an abnormal outage, and repeated network flaps."""

from __future__ import annotations

import asyncio
import hashlib
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
    terminate_executor_processes,
)
from run_h8_01_acceptance import action_claims, resource_id, run_offer_fixture
from run_h8_04_acceptance import require_app_process
from run_h8_06_acceptance import (
    collect_wdio,
    ensure_app_process_stopped,
    read_pre_restart_checkpoint,
    resume_executor,
    seed_running_local_checkpoint,
    suspend_executor,
    verify_local_recovery,
    verify_server_recovery,
    wait_for_signal,
)
from run_i2_13_acceptance import require_port_closed
from run_t3_06_acceptance import (
    BACKEND_ROOT,
    FRONTEND_ROOT,
    REPOSITORY_ROOT,
    base64url,
    compose_command,
    unused_loopback_port,
)
from run_t3_14_acceptance import seed_attempt_and_offer, seed_task_confirmation
from run_t3_20_acceptance import start_control_plane, stop_control_plane
from sqlalchemy import select

from automation_tool.control_plane.domain import InstallationId, TaskId, TaskStatus
from automation_tool.control_plane.infrastructure.database import (
    Database,
    douyin_search_exposure_definitions,
)
from automation_tool.executor.ledger import ExecutorLedger, ExecutorLedgerRejected
from automation_tool.executor.side_effect_ledger import SideEffectState

TAURI_CONFIG = FRONTEND_ROOT / "src-tauri" / "tauri.network-recovery-e2e.conf.json"
CONTROL_PLANE_PORT = reserve_control_plane_port()
APP_IDENTIFIER = "com.aventador.automationtool.h807acceptance"
ENVIRONMENT_ID = "h807-acceptance"
TASK_KEYWORD = "H8-07 断网抖动恢复"
DEVICE_CREDENTIAL_FILE = "device-credential-v1"
EXECUTOR_BUILD_ID = "h8-07-network-recovery"
EXECUTOR_LEDGER_FILE = "executor-ledger.sqlite3"
EXPECTED_TERMINAL_TASK_STATUS = TaskStatus.CANCELLED


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
        raise RuntimeError("H8-07 requires one isolated visible=false App")


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
        "postgresql+asyncpg://automation_tool_h807:"
        f"{password}@127.0.0.1:{database_port}/automation_tool_h807"
    )
    token, public_key = signed_bootstrap()
    environment.update(
        {
            "AUTOMATION_TOOL_DEV_DB_USER": "unused_h807_dev",
            "AUTOMATION_TOOL_DEV_DB_PASSWORD": secrets.token_hex(24),
            "AUTOMATION_TOOL_DEV_DB_NAME": "unused_h807_dev",
            "AUTOMATION_TOOL_DEV_DB_PORT": str(unused_loopback_port()),
            "AUTOMATION_TOOL_TEST_DB_USER": "automation_tool_h807",
            "AUTOMATION_TOOL_TEST_DB_PASSWORD": password,
            "AUTOMATION_TOOL_TEST_DB_NAME": "automation_tool_h807",
            "AUTOMATION_TOOL_TEST_DB_PORT": str(database_port),
            "AUTOMATION_TOOL_DATABASE_URL": database_url,
            "AUTOMATION_TOOL_DEMO_ENVIRONMENT_ID": ENVIRONMENT_ID,
            "AUTOMATION_TOOL_DEMO_BOOTSTRAP_PUBLIC_KEY": public_key,
            "AUTOMATION_TOOL_H807_BOOTSTRAP_TOKEN": token,
            "AUTOMATION_TOOL_H807_ENVIRONMENT_ID": ENVIRONMENT_ID,
            "AUTOMATION_TOOL_CONTROL_PLANE_E2E_ORIGIN": (f"http://127.0.0.1:{CONTROL_PLANE_PORT}"),
        }
    )
    return (
        startup_gate_environment(environment, control_plane_port=CONTROL_PLANE_PORT),
        database_url,
    )


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
            raise RuntimeError("H8-07 App-created Task identity is invalid")
    finally:
        await database.close()
    credential_path = private_app_data / DEVICE_CREDENTIAL_FILE
    if not credential_path.is_file():
        raise RuntimeError("H8-07 App credential vault is missing")
    return typed_installation, typed_task, credential_path.read_text(encoding="ascii")


def seed_prepared_side_effect(
    ledger: ExecutorLedger,
    *,
    installation_id: InstallationId,
    task_id: TaskId,
    attempt_id: str,
    executor_id: str,
) -> tuple[str, bytes]:
    base = datetime.now(UTC) - timedelta(seconds=30)
    claims = action_claims(
        action_id=resource_id(807),
        target_id=resource_id(907),
        installation_id=installation_id,
        task_id=task_id,
        attempt_id=attempt_id,
        executor_id=executor_id,
        authorized_at=base,
    )
    effect = hashlib.sha256(b"h8-07-prepared-effect").digest()
    ledger.admit_action(
        claims=claims,
        authorization_fingerprint=hashlib.sha256(b"h8-07-authorization").digest(),
        admitted_at=base + timedelta(seconds=1),
        minimum_interval_seconds=1,
        task_action_limit=100,
    )
    ledger.prepare_side_effect(
        action_id=str(claims.action_id),
        effect_fingerprint=effect,
        prepared_at=base + timedelta(seconds=2),
    )
    return str(claims.action_id), effect


def kill_control_plane_abruptly(process: subprocess.Popen[bytes]) -> None:
    process.kill()
    process.wait(timeout=5)
    require_port_closed(CONTROL_PLANE_PORT)


def wait_for_transport_connected(
    ledger_path: Path,
    *,
    connected: bool,
    package_entrypoint: Path,
    executor_process_id: int | None,
    timeout: float = 35,
) -> None:
    deadline = time.monotonic() + timeout
    expected = (int(connected),)
    while True:
        if executor_process_id is not None and not any(
            process_id == executor_process_id
            for process_id, _command in matching_executor_processes(package_entrypoint)
        ):
            raise RuntimeError("H8-07 signed Executor exited during network recovery")
        with closing(sqlite3.connect(ledger_path)) as connection:
            row = connection.execute(
                "SELECT network_connected FROM executor_action_guard WHERE singleton_id = 1"
            ).fetchone()
        if row == expected:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(f"H8-07 transport gate did not become {connected!r}: {row!r}")
        time.sleep(0.05)


def wait_for_local_event_spool(ledger_path: Path, cancel_message_id: str) -> None:
    deadline = time.monotonic() + 20
    while True:
        with closing(sqlite3.connect(ledger_path)) as connection:
            outbox = connection.execute(
                "SELECT source_message_id, json_extract(envelope, '$.message_type') "
                "FROM executor_outbox ORDER BY ordinal"
            ).fetchall()
        if outbox == [
            (cancel_message_id, "task.control_ack"),
            (cancel_message_id, "task.cancelled"),
        ]:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(f"H8-07 local event spool did not settle exactly: {outbox!r}")
        time.sleep(0.05)


def verify_dispatch_is_blocked(
    state_directory: Path,
    *,
    installation_id: InstallationId,
    executor_id: str,
    action_id: str,
    effect_fingerprint: bytes,
) -> None:
    ledger = ExecutorLedger(
        state_directory=state_directory,
        installation_id=str(installation_id),
        executor_id=executor_id,
    )
    try:
        ledger.begin_side_effect_dispatch(
            action_id=action_id,
            effect_fingerprint=effect_fingerprint,
            dispatched_at=datetime.now(UTC),
        )
    except ExecutorLedgerRejected:
        pass
    else:
        raise RuntimeError("H8-07 allowed a new side effect while offline")
    retained = ledger.get_side_effect(action_id)
    if retained is None or retained.state is not SideEffectState.PREPARED or retained.revision != 1:
        raise RuntimeError("H8-07 changed the prepared side effect while offline")


async def wait_for_server_recovery(
    database_url: str,
    task_id: TaskId,
    checkpoint: tuple[tuple[object, ...], ...],
) -> None:
    deadline = time.monotonic() + 30
    while True:
        try:
            await verify_server_recovery(database_url, task_id, checkpoint)
            return
        except RuntimeError:
            if time.monotonic() >= deadline:
                raise
            await asyncio.sleep(0.05)


def verify_network_recovery_local_facts(
    private_app_data: Path,
    *,
    action_id: str,
) -> None:
    ledger_path = private_app_data / "local-executor" / "state" / EXECUTOR_LEDGER_FILE
    with closing(sqlite3.connect(ledger_path)) as connection:
        side_effect = connection.execute(
            "SELECT action_id, state, revision FROM executor_side_effects"
        ).fetchall()
        network_gate = connection.execute(
            "SELECT network_connected FROM executor_action_guard WHERE singleton_id = 1"
        ).fetchone()
        pending_count = connection.execute(
            "SELECT COUNT(*) FROM executor_outbox WHERE delivered = 0"
        ).fetchone()
    if (
        side_effect != [(action_id, SideEffectState.PREPARED.value, 1)]
        or network_gate != (1,)
        or pending_count != (0,)
    ):
        raise RuntimeError(
            "H8-07 local network/spool facts did not close exactly: "
            f"side_effect={side_effect!r}, network_gate={network_gate!r}, "
            f"pending_count={pending_count!r}"
        )


def main() -> None:
    require_control_plane_port_available()
    require_hidden_tauri_configuration()
    private_app_data = app_data_directory()
    if private_app_data.exists():
        raise RuntimeError("Refusing to reuse an existing H8-07 App data directory")
    prepare_startup_gate(private_app_data, executor_package=False)

    project_name = f"automation-tool-h807-{os.getpid()}"
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
                "network-down",
                "unavailable",
                "network-stable",
                "recovered",
                "facts-verified",
            )
        }
        environment.update(
            {
                "AUTOMATION_TOOL_H807_TASK_CREATED_SIGNAL": os.fspath(signals["task-created"]),
                "AUTOMATION_TOOL_H807_TASK_SEEDED_SIGNAL": os.fspath(signals["task-seeded"]),
                "AUTOMATION_TOOL_H807_EXECUTOR_READY_SIGNAL": os.fspath(signals["executor-ready"]),
                "AUTOMATION_TOOL_H807_EXECUTOR_SUSPENDED_SIGNAL": os.fspath(
                    signals["executor-suspended"]
                ),
                "AUTOMATION_TOOL_H807_CANCEL_SUBMITTED_SIGNAL": os.fspath(
                    signals["cancel-submitted"]
                ),
                "AUTOMATION_TOOL_H807_NETWORK_DOWN_SIGNAL": os.fspath(signals["network-down"]),
                "AUTOMATION_TOOL_H807_UNAVAILABLE_SIGNAL": os.fspath(signals["unavailable"]),
                "AUTOMATION_TOOL_H807_NETWORK_STABLE_SIGNAL": os.fspath(signals["network-stable"]),
                "AUTOMATION_TOOL_H807_RECOVERED_SIGNAL": os.fspath(signals["recovered"]),
                "AUTOMATION_TOOL_H807_FACTS_VERIFIED_SIGNAL": os.fspath(signals["facts-verified"]),
            }
        )
        try:
            print("[H8-07] Building and signing the real PyInstaller Executor")
            package_source = build_signed_executor(workspace, build_id=EXECUTOR_BUILD_ID)
            package_root = install_executor_package(package_source, private_app_data)
            package_entrypoint = executor_entrypoint(package_root)

            print("[H8-07] Building the dedicated hidden Tauri App")
            subprocess.run(
                ["pnpm", "build:tauri:network-recovery-test"],
                check=True,
                cwd=FRONTEND_ROOT,
                env=environment,
            )
            print(f"[H8-07] Starting isolated PostgreSQL as {project_name}")
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
                ["pnpm", "exec", "wdio", "run", "wdio.network-recovery.conf.ts"],
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
                raise RuntimeError("H8-07 App did not report its process identity")
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
                    label="h8-07-network-recovery",
                    confirmed_target_revision=True,
                )
            )
            run_offer_fixture(credential, typed_installation)
            executor_id = (private_app_data / "local-executor" / "executor-id-v1").read_text(
                encoding="ascii"
            )
            state_directory = private_app_data / "local-executor" / "state"
            ledger = seed_running_local_checkpoint(state_directory, offer, executor_id)
            action_id, effect_fingerprint = seed_prepared_side_effect(
                ledger,
                installation_id=typed_installation,
                task_id=typed_task,
                attempt_id=str(offer.execution_attempt_id),
                executor_id=executor_id,
            )
            signals["task-seeded"].write_text("{}\n", encoding="utf-8")

            wait_for_signal(signals["executor-ready"], app, label="the running signed Executor")
            processes = matching_executor_processes(package_entrypoint)
            if len(processes) != 1:
                raise RuntimeError("H8-07 did not start exactly one signed Executor")
            executor_process_id = processes[0][0]
            suspend_executor(executor_process_id)
            executor_suspended = True
            signals["executor-suspended"].write_text("{}\n", encoding="utf-8")

            wait_for_signal(signals["cancel-submitted"], app, label="the App-submitted cancel")
            checkpoint = asyncio.run(read_pre_restart_checkpoint(database_url, typed_task))
            cancel_message_id = str(checkpoint[1][0])
            print("[H8-07] Killing the real Control Plane without a WebSocket close frame")
            kill_control_plane_abruptly(server)
            server = None
            signals["network-down"].write_text("{}\n", encoding="utf-8")
            resume_executor(executor_process_id)
            executor_suspended = False
            ledger_path = state_directory / EXECUTOR_LEDGER_FILE
            wait_for_transport_connected(
                ledger_path,
                connected=False,
                package_entrypoint=package_entrypoint,
                executor_process_id=executor_process_id,
            )
            wait_for_local_event_spool(ledger_path, cancel_message_id)
            verify_dispatch_is_blocked(
                state_directory,
                installation_id=typed_installation,
                executor_id=executor_id,
                action_id=action_id,
                effect_fingerprint=effect_fingerprint,
            )
            wait_for_signal(signals["unavailable"], app, label="the unavailable App UI")
            if matching_executor_processes(package_entrypoint) != processes:
                raise RuntimeError("H8-07 replaced the signed Executor during the outage")

            print("[H8-07] Restoring and flapping the real Control Plane twice")
            server = start_control_plane(environment)
            asyncio.run(wait_for_server_recovery(database_url, typed_task, checkpoint))
            wait_for_transport_connected(
                ledger_path,
                connected=True,
                package_entrypoint=package_entrypoint,
                executor_process_id=executor_process_id,
            )
            for _flap in range(2):
                kill_control_plane_abruptly(server)
                server = None
                wait_for_transport_connected(
                    ledger_path,
                    connected=False,
                    package_entrypoint=package_entrypoint,
                    executor_process_id=executor_process_id,
                )
                if matching_executor_processes(package_entrypoint) != processes:
                    raise RuntimeError("H8-07 Rust supervisor restarted the Executor during jitter")
                server = start_control_plane(environment)
                wait_for_transport_connected(
                    ledger_path,
                    connected=True,
                    package_entrypoint=package_entrypoint,
                    executor_process_id=executor_process_id,
                )
            signals["network-stable"].write_text("{}\n", encoding="utf-8")

            recovered = wait_for_signal(signals["recovered"], app, label="the converged Task")
            if (
                recovered.get("taskId") != task_id
                or recovered.get("installationId") != installation_id
                or recovered.get("restart_count") != 0
            ):
                raise RuntimeError(
                    f"H8-07 App did not recover {EXPECTED_TERMINAL_TASK_STATUS.value!r} exactly"
                )
            if matching_executor_processes(package_entrypoint) != processes:
                raise RuntimeError("H8-07 Rust supervisor restarted the healthy Executor")
            asyncio.run(verify_server_recovery(database_url, typed_task, checkpoint))
            verify_local_recovery(private_app_data, checkpoint, credential)
            wait_for_transport_connected(
                ledger_path,
                connected=True,
                package_entrypoint=package_entrypoint,
                executor_process_id=executor_process_id,
            )
            verify_network_recovery_local_facts(private_app_data, action_id=action_id)
            signals["facts-verified"].write_text("{}\n", encoding="utf-8")
            exit_code, output = collect_wdio(app, timeout=60)
            app = None
            ensure_app_process_stopped(app_process_id)
            if exit_code != 0:
                print(output, end="")
                raise RuntimeError("H8-07 hidden App acceptance failed")
            print("[H8-07] Hidden-App abnormal network recovery passed")
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
