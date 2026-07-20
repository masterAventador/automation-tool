#!/usr/bin/env python3
"""Run D6-10 through a hidden Tauri App, real network, and formal Executor."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from io import StringIO
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
from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from automation_tool.control_plane.application.device_sessions import (
    DeviceSessionCapability,
)
from automation_tool.control_plane.domain import InstallationId, TaskId
from automation_tool.control_plane.infrastructure.database import (
    Database,
    execution_attempts,
    platform_session_health,
    task_commands,
    task_events,
    task_targets,
    tasks,
)
from automation_tool.executor import (
    ExecutorBootstrap,
    ExecutorCommandProcessor,
    ExecutorProcessReporter,
    LocalExecutorProcess,
    LocalSessionAuthenticator,
    RuntimeMetadata,
)
from automation_tool.executor.discovery_operation import (
    DouyinDiscoveryExecutionResult,
    DouyinDiscoveryOperationState,
)
from automation_tool.executor.ledger import ExecutorLedger
from automation_tool.protocol import (
    MAX_EXECUTOR_MESSAGE_BYTES,
    DouyinCandidate,
    DouyinCandidateSource,
    DouyinCandidateSummary,
    DouyinDiscoveryCommandPayload,
)

TAURI_CONFIG = FRONTEND_ROOT / "src-tauri" / "tauri.task-discovery-e2e.conf.json"
CONTROL_PLANE_PORT = 8765
APP_IDENTIFIER = "com.aventador.automationtool.d610acceptance"
ENVIRONMENT_ID = "d610-acceptance"
TASK_KEY = "task:discovery:tauri-acceptance"
DEVICE_CREDENTIAL_FILE = "device-credential-v1"


class DeterministicDiscoveryOperation:
    """Inject deterministic read-only candidates after D6-04..D6-07 browser acceptance."""

    def run(
        self,
        payload: DouyinDiscoveryCommandPayload,
        *,
        cancellation_requested: Callable[[], bool],
    ) -> DouyinDiscoveryExecutionResult:
        if cancellation_requested():
            raise RuntimeError("D6-10 acceptance was unexpectedly cancelled")
        candidates = tuple(
            DouyinCandidate(
                platform_target_id=f"acceptance-author-{index}",
                summary=DouyinCandidateSummary(
                    display_name=f"验收目标 {index}",
                    public_handle=f"acceptance_{index}",
                ),
                source=DouyinCandidateSource.GENERAL_SEARCH_AUTHOR,
                page_revision=payload.page_revision,
            )
            for index in (1, 2)
        )
        return DouyinDiscoveryExecutionResult(
            state=DouyinDiscoveryOperationState.COMPLETED,
            evidence="candidates_extracted",
            page_revision=payload.page_revision,
            candidates=candidates,
        )


def require_control_plane_port_available() -> None:
    with socket.socket() as listener:
        try:
            listener.bind(("127.0.0.1", CONTROL_PLANE_PORT))
        except OSError as error:
            raise RuntimeError(
                "D6-10 requires an unused local Control Plane port"
            ) from error


def require_hidden_tauri_configuration() -> None:
    configuration = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))
    windows = configuration.get("app", {}).get("windows", [])
    if len(windows) != 1 or windows[0].get("visible") is not False:
        raise RuntimeError("D6-10 Tauri acceptance must run with visible=false")


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
        "postgresql+asyncpg://automation_tool_d610:"
        f"{database_password}@127.0.0.1:{database_port}/automation_tool_d610"
    )
    bootstrap_token, bootstrap_public_key = signed_bootstrap()
    environment.update(
        {
            "AUTOMATION_TOOL_DEV_DB_USER": "unused_d610_dev",
            "AUTOMATION_TOOL_DEV_DB_PASSWORD": secrets.token_hex(24),
            "AUTOMATION_TOOL_DEV_DB_NAME": "unused_d610_dev",
            "AUTOMATION_TOOL_DEV_DB_PORT": str(unused_loopback_port()),
            "AUTOMATION_TOOL_TEST_DB_USER": "automation_tool_d610",
            "AUTOMATION_TOOL_TEST_DB_PASSWORD": database_password,
            "AUTOMATION_TOOL_TEST_DB_NAME": "automation_tool_d610",
            "AUTOMATION_TOOL_TEST_DB_PORT": str(database_port),
            "AUTOMATION_TOOL_DATABASE_URL": database_url,
            "AUTOMATION_TOOL_DEMO_ENVIRONMENT_ID": ENVIRONMENT_ID,
            "AUTOMATION_TOOL_DEMO_BOOTSTRAP_PUBLIC_KEY": bootstrap_public_key,
            "AUTOMATION_TOOL_D610_BOOTSTRAP_TOKEN": bootstrap_token,
            "AUTOMATION_TOOL_D610_ENVIRONMENT_ID": ENVIRONMENT_ID,
        }
    )
    return environment, database_url


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
                raise RuntimeError("D6-10 hidden App exited before creating its Task")
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
                    raise RuntimeError(
                        "D6-10 App credential vault is unreadable"
                    ) from error
                return InstallationId.parse(row[0]), TaskId.parse(row[1]), credential
            if time.monotonic() >= deadline:
                raise RuntimeError("D6-10 hidden App did not create its Task in time")
            await asyncio.sleep(0.05)
    finally:
        await engine.dispose()


async def seed_healthy_platform(
    database_url: str,
    installation_id: InstallationId,
) -> None:
    database = Database.from_url(database_url)
    now = datetime.now(UTC)
    try:
        async with database.session() as session:
            await session.execute(
                insert(platform_session_health).values(
                    installation_id=installation_id.uuid,
                    platform="douyin",
                    state="healthy",
                    session_revision=1,
                    observed_at=now,
                    updated_at=now,
                )
            )
    finally:
        await database.close()


def executor_session(credential: str) -> str:
    exchanged = post_json(
        CONTROL_PLANE_PORT,
        "/api/v1/device-sessions",
        credential,
        payload={"capability": DeviceSessionCapability.EXECUTOR_CONNECT.value},
        expected_status=201,
    )
    session_token = exchanged.get("sessionToken")
    if not isinstance(session_token, str):
        raise RuntimeError("D6-10 Executor Session exchange omitted its opaque token")
    return session_token


def start_executor(
    *,
    private_app_data: Path,
    installation_id: InstallationId,
    session_token: str,
) -> tuple[threading.Event, threading.Thread, list[BaseException]]:
    executor_id = str(uuid4())
    state_directory = private_app_data / "d6-10-executor-state"
    ledger = ExecutorLedger(
        state_directory=state_directory,
        installation_id=str(installation_id),
        executor_id=executor_id,
    )
    processor = ExecutorCommandProcessor(
        ledger=ledger,
        installation_id=str(installation_id),
        executor_id=executor_id,
        discovery_operation=DeterministicDiscoveryOperation(),
    )
    local_session_token = secrets.token_hex(32)
    bootstrap = ExecutorBootstrap.model_validate(
        {
            "bootstrap_version": "1",
            "websocket_url": (
                f"ws://127.0.0.1:{CONTROL_PLANE_PORT}/api/v1/executors/connect"
            ),
            "local_session_token": local_session_token,
            "session_token": session_token,
            "installation_id": str(installation_id),
            "executor_id": executor_id,
            "heartbeat_interval_seconds": 1,
            "state_directory": str(state_directory),
        }
    )
    authenticator = LocalSessionAuthenticator(bootstrap.local_session_token)
    reporter = ExecutorProcessReporter(StringIO(), authenticator)
    process = LocalExecutorProcess(
        bootstrap=bootstrap,
        metadata=RuntimeMetadata.detect(),
        reporter=reporter,
        command_processor=processor,
    )
    stop = threading.Event()
    failures: list[BaseException] = []

    def run() -> None:
        try:
            process.run(stop)
        except BaseException as error:
            failures.append(error)
        finally:
            authenticator.close()

    thread = threading.Thread(target=run, name="d6-10-formal-executor", daemon=True)
    thread.start()
    return stop, thread, failures


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
            attempt_row = (
                (
                    await session.execute(
                        select(execution_attempts).where(
                            execution_attempts.c.id == task_row["current_attempt_id"]
                        )
                    )
                )
                .mappings()
                .one()
            )
            command_rows = (
                (
                    await session.execute(
                        select(task_commands)
                        .where(task_commands.c.task_id == task_id.uuid)
                        .order_by(task_commands.c.sequence)
                    )
                )
                .mappings()
                .all()
            )
            event_rows = (
                (
                    await session.execute(
                        select(task_events)
                        .where(task_events.c.task_id == task_id.uuid)
                        .order_by(task_events.c.sequence)
                    )
                )
                .mappings()
                .all()
            )
            target_rows = (
                (
                    await session.execute(
                        select(task_targets)
                        .where(
                            task_targets.c.task_id == task_id.uuid,
                            task_targets.c.installation_id == installation_id.uuid,
                        )
                        .order_by(task_targets.c.ordinal)
                    )
                )
                .mappings()
                .all()
            )
            capabilities = list(
                await session.scalars(
                    text("select capability from device_sessions order by id")
                )
            )
    finally:
        await database.close()

    if (
        task_row["installation_id"] != installation_id.uuid
        or task_row["status"] != "awaiting_confirmation"
        or task_row["revision"] != 3
        or task_row["last_event_sequence"] != 2
    ):
        raise RuntimeError("D6-10 final Task projection is invalid")
    if attempt_row["status"] != "succeeded" or attempt_row["finished_at"] is None:
        raise RuntimeError("D6-10 final discovery Attempt is invalid")
    if len(command_rows) != 1 or (
        command_rows[0]["command_type"],
        command_rows[0]["status"],
        command_rows[0]["response_type"],
    ) != ("task.discover", "acknowledged", "task.accept"):
        raise RuntimeError("D6-10 command acknowledgement is invalid")
    if [row["event_type"] for row in event_rows] != [
        "task.discovery_started",
        "task.awaiting_confirmation",
    ]:
        raise RuntimeError("D6-10 Task event sequence is invalid")
    target_facts = [
        (
            row["ordinal"],
            row["platform_target_id"],
            row["display_name"],
            row["public_handle"],
            row["source"],
            row["page_revision"],
            row["disposition"],
        )
        for row in target_rows
    ]
    if target_facts != [
        (
            1,
            "acceptance-author-1",
            "验收目标 1",
            "acceptance_1",
            "general_search_author",
            1,
            "eligible",
        ),
        (
            2,
            "acceptance-author-2",
            "验收目标 2",
            "acceptance_2",
            "general_search_author",
            1,
            "eligible",
        ),
    ]:
        raise RuntimeError("D6-10 minimal Target projection is invalid")
    allowed = {
        DeviceSessionCapability.APP_CONTROL_PLANE.value,
        DeviceSessionCapability.EXECUTOR_CONNECT.value,
    }
    if capabilities.count(DeviceSessionCapability.EXECUTOR_CONNECT.value) != 1 or any(
        capability not in allowed for capability in capabilities
    ):
        raise RuntimeError("D6-10 used an unexpected Session capability")


def main() -> None:
    require_control_plane_port_available()
    require_hidden_tauri_configuration()
    private_app_data = app_data_directory()
    if private_app_data.exists():
        raise RuntimeError("Refusing to reuse an existing D6-10 App data directory")

    project_name = f"automation-tool-d610-{os.getpid()}"
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
        print("[D6-10] Starting isolated PostgreSQL")
        subprocess.run(
            [*compose, "up", "--detach", "--wait", "postgres-test"],
            check=True,
            cwd=REPOSITORY_ROOT,
            env=environment,
        )
        print("[D6-10] Applying the production Alembic migration chain")
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            check=True,
            cwd=BACKEND_ROOT,
            env=environment,
        )
        print("[D6-10] Starting the real Uvicorn boundary in the background")
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
        print("[D6-10] Running the real Tauri App with visible=false")
        app_process = subprocess.Popen(
            ["pnpm", "test:task-discovery-tauri"],
            cwd=FRONTEND_ROOT,
            env=environment,
        )
        installation_id, task_id, credential = asyncio.run(
            wait_for_app_task(database_url, private_app_data, app_process)
        )
        asyncio.run(seed_healthy_platform(database_url, installation_id))
        executor_stop, executor_thread, executor_failures = start_executor(
            private_app_data=private_app_data,
            installation_id=installation_id,
            session_token=executor_session(credential),
        )
        try:
            app_exit = app_process.wait(timeout=180)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("D6-10 hidden App acceptance did not finish") from error
        if app_exit != 0:
            raise RuntimeError("D6-10 hidden App acceptance failed")
        app_process = None
        verify_app_private_data(private_app_data)
        asyncio.run(verify_database_state(database_url, installation_id, task_id))
        print("[D6-10] Hidden-App discovery convergence acceptance passed")
    finally:
        if executor_stop is not None:
            executor_stop.set()
        if executor_thread is not None:
            executor_thread.join(timeout=10)
            if executor_thread.is_alive():
                cleanup_error = RuntimeError("D6-10 formal Executor did not stop")
            elif executor_failures:
                cleanup_error = RuntimeError("D6-10 formal Executor failed")
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
        if cleanup_error is not None:
            if executor_failures:
                raise cleanup_error from executor_failures[0]
            raise cleanup_error


if __name__ == "__main__":
    main()
