#!/usr/bin/env python3
"""Run D6-12 through the hidden real Tauri UI and production network path."""

from __future__ import annotations

import asyncio
import json
import os
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
from run_d6_10_acceptance import executor_session, seed_healthy_platform, start_executor
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
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from automation_tool.control_plane.application.task_target_previews import (
    TASK_TARGET_CONFIRMATION_INTENT_VERSION,
    TaskTargetConfirmationIntent,
)
from automation_tool.control_plane.domain import (
    DouyinSearchExposureAction,
    InstallationId,
    TargetId,
    TaskId,
)
from automation_tool.control_plane.infrastructure.database import (
    Database,
    execution_attempts,
    task_events,
    task_target_confirmations,
    task_target_exclusions,
    task_targets,
    tasks,
)
from automation_tool.protocol import MAX_EXECUTOR_MESSAGE_BYTES

TAURI_CONFIG = FRONTEND_ROOT / "src-tauri" / "tauri.task-target-preview-ui-e2e.conf.json"
CONTROL_PLANE_PORT = reserve_control_plane_port()
APP_IDENTIFIER = "com.aventador.automationtool.d612acceptance"
ENVIRONMENT_ID = "d612-acceptance"
TASK_KEY = "task:preview-ui:tauri-acceptance"
DEVICE_CREDENTIAL_FILE = "device-credential-v1"


def require_control_plane_port_available() -> None:
    require_reserved_port_still_free(CONTROL_PLANE_PORT)


def require_hidden_tauri_configuration() -> None:
    configuration = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))
    windows = configuration.get("app", {}).get("windows", [])
    if len(windows) != 1 or windows[0].get("visible") is not False:
        raise RuntimeError("D6-12 Tauri acceptance must run with visible=false")


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
    return token, base64url(signer.public_key().public_bytes_raw())


def isolated_environment(database_port: int) -> tuple[dict[str, str], str]:
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("AUTOMATION_TOOL_")
    }
    password = secrets.token_hex(24)
    database_url = (
        "postgresql+asyncpg://automation_tool_d612:"
        f"{password}@127.0.0.1:{database_port}/automation_tool_d612"
    )
    token, public_key = signed_bootstrap()
    environment.update(
        {
            "AUTOMATION_TOOL_DEV_DB_USER": "unused_d612_dev",
            "AUTOMATION_TOOL_DEV_DB_PASSWORD": secrets.token_hex(24),
            "AUTOMATION_TOOL_DEV_DB_NAME": "unused_d612_dev",
            "AUTOMATION_TOOL_DEV_DB_PORT": str(unused_loopback_port()),
            "AUTOMATION_TOOL_TEST_DB_USER": "automation_tool_d612",
            "AUTOMATION_TOOL_TEST_DB_PASSWORD": password,
            "AUTOMATION_TOOL_TEST_DB_NAME": "automation_tool_d612",
            "AUTOMATION_TOOL_TEST_DB_PORT": str(database_port),
            "AUTOMATION_TOOL_DATABASE_URL": database_url,
            "AUTOMATION_TOOL_DEMO_ENVIRONMENT_ID": ENVIRONMENT_ID,
            "AUTOMATION_TOOL_DEMO_BOOTSTRAP_PUBLIC_KEY": public_key,
            "AUTOMATION_TOOL_D612_BOOTSTRAP_TOKEN": token,
            "AUTOMATION_TOOL_D612_ENVIRONMENT_ID": ENVIRONMENT_ID,
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
                raise RuntimeError("D6-12 hidden App exited before preparing its Task")
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
                    raise RuntimeError("D6-12 App credential vault is unreadable") from error
                return InstallationId.parse(row[0]), TaskId.parse(row[1]), credential
            if time.monotonic() >= deadline:
                raise RuntimeError("D6-12 hidden App did not prepare its Task in time")
            await asyncio.sleep(0.05)
    finally:
        await engine.dispose()


async def verify_database_state(
    database_url: str,
    installation_id: InstallationId,
    task_id: TaskId,
) -> None:
    database = Database.from_url(database_url)
    try:
        async with database.session() as session:
            task_row = (
                (await session.execute(select(tasks).where(tasks.c.id == task_id.uuid)))
                .mappings()
                .one()
            )
            # Confirming targets releases the finished discovery attempt, so the
            # Task no longer names it: `current_attempt_id` is cleared on
            # `task.targets_confirmed` precisely so the action-execution attempt
            # can be admitted afterwards. The attempt is therefore located by the
            # Task it belongs to, and the released slot is asserted below.
            attempt_row = (
                (
                    await session.execute(
                        select(execution_attempts).where(
                            execution_attempts.c.task_id == task_id.uuid,
                            execution_attempts.c.installation_id == installation_id.uuid,
                        )
                    )
                )
                .mappings()
                .one()
            )
            targets = (
                (
                    await session.execute(
                        select(task_targets)
                        .where(task_targets.c.task_id == task_id.uuid)
                        .order_by(task_targets.c.ordinal)
                    )
                )
                .mappings()
                .all()
            )
            exclusions = (
                (
                    await session.execute(
                        select(task_target_exclusions).where(
                            task_target_exclusions.c.task_id == task_id.uuid
                        )
                    )
                )
                .mappings()
                .all()
            )
            confirmation = (
                (
                    await session.execute(
                        select(task_target_confirmations).where(
                            task_target_confirmations.c.task_id == task_id.uuid
                        )
                    )
                )
                .mappings()
                .one()
            )
            events = list(
                await session.scalars(
                    select(task_events.c.event_type)
                    .where(task_events.c.task_id == task_id.uuid)
                    .order_by(task_events.c.sequence)
                )
            )
        if (
            task_row["installation_id"] != installation_id.uuid
            or task_row["status"] != "queued"
            or task_row["revision"] != 6
            or task_row["last_event_sequence"] != 5
            or task_row["current_attempt_id"] is not None
            or attempt_row["status"] != "succeeded"
        ):
            raise RuntimeError("D6-12 Task/Attempt did not converge after UI confirmation")
        if len(targets) != 2 or exclusions:
            raise RuntimeError("D6-12 UI persisted the wrong target selection")
        intent = TaskTargetConfirmationIntent(
            installation_id=installation_id,
            task_id=task_id,
            page_revision=1,
            confirmation_revision=5,
            action=DouyinSearchExposureAction.COMMENT,
            message_template="您好 {{target_display_name}} 期待您的分享",
            selected_target_ids=tuple(TargetId.parse(row["id"]) for row in targets),
        )
        if (
            confirmation["page_revision"] != 1
            or confirmation["selection_task_revision"] != 5
            or confirmation["confirmed_task_revision"] != 6
            or confirmation["selected_target_count"] != 2
            or confirmation["action"] != intent.action.value
            or confirmation["message_template"] != intent.message_template
            or confirmation["intent_version"] != TASK_TARGET_CONFIRMATION_INTENT_VERSION
            or bytes(confirmation["intent_fingerprint"]) != intent.fingerprint()
        ):
            raise RuntimeError("D6-12 UI did not bind confirmation to the latest revision")
        if events != [
            "task.discovery_started",
            "task.awaiting_confirmation",
            "task.target_selection_updated",
            "task.target_selection_updated",
            "task.targets_confirmed",
        ]:
            raise RuntimeError("D6-12 UI produced a non-contiguous event history")
    finally:
        await database.close()


def main() -> None:
    require_control_plane_port_available()
    require_hidden_tauri_configuration()
    private_app_data = app_data_directory()
    if private_app_data.exists():
        raise RuntimeError("Refusing to reuse an existing D6-12 App data directory")
    prepare_startup_gate(private_app_data)
    project_name = f"automation-tool-d612-{os.getpid()}"
    database_port = unused_loopback_port()
    environment, database_url = isolated_environment(database_port)
    compose = compose_command(project_name)
    server: subprocess.Popen[bytes] | None = None
    app_process: subprocess.Popen[bytes] | None = None
    executor_stop: threading.Event | None = None
    executor_thread: threading.Thread | None = None
    executor_failures: list[BaseException] = []
    cleanup_error: RuntimeError | None = None
    try:
        print("[D6-12] Starting isolated PostgreSQL")
        subprocess.run(
            [*compose, "up", "--detach", "--wait", "postgres-test"],
            check=True,
            cwd=REPOSITORY_ROOT,
            env=environment,
        )
        print("[D6-12] Applying the production Alembic chain")
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
        print("[D6-12] Running the real hidden Tauri App UI")
        app_process = subprocess.Popen(
            ["pnpm", "test:task-target-preview-ui-tauri"],
            cwd=FRONTEND_ROOT,
            env=environment,
            start_new_session=True,
        )
        installation_id, task_id, credential = asyncio.run(
            wait_for_app_task(database_url, private_app_data, app_process)
        )
        asyncio.run(seed_healthy_platform(database_url, installation_id))
        executor_stop, executor_thread, executor_failures = start_executor(
            private_app_data=private_app_data,
            installation_id=installation_id,
            session_token=executor_session(credential),
            state_directory_name="d6-12-executor-state",
            thread_name="d6-12-formal-executor",
        )
        try:
            app_exit = app_process.wait(timeout=180)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("D6-12 hidden App UI acceptance did not finish") from error
        if app_exit != 0:
            raise RuntimeError("D6-12 hidden App UI acceptance failed")
        app_process = None
        verify_app_private_data(private_app_data)
        asyncio.run(verify_database_state(database_url, installation_id, task_id))
        print("[D6-12] Hidden-App target preview UI acceptance passed")
    finally:
        if executor_stop is not None:
            executor_stop.set()
        if executor_thread is not None:
            executor_thread.join(timeout=10)
            if executor_thread.is_alive():
                cleanup_error = RuntimeError("D6-12 formal Executor did not stop")
            elif executor_failures:
                cleanup_error = RuntimeError("D6-12 formal Executor failed")
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
        if cleanup_error is not None:
            if executor_failures:
                raise cleanup_error from executor_failures[0]
            raise cleanup_error


if __name__ == "__main__":
    main()
