#!/usr/bin/env python3
"""Run the complete MVP journey through one isolated hidden Tauri App."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from run_b5_13_acceptance import (
    require_no_residual_project_processes,
    terminate_process,
    terminate_project_processes,
)
from run_e4_07_acceptance import build_signed_executor
from run_e4_14_acceptance import (
    assert_no_executor_process,
    executor_entrypoint,
    install_executor_package,
    pnpm_executable,
    start_control_plane,
    terminate_executor_processes,
)
from run_i2_13_acceptance import (
    BACKEND_ROOT,
    REPOSITORY_ROOT,
    compose_command,
    require_port_closed,
    unused_loopback_port,
)
from run_t3_06_acceptance import FRONTEND_ROOT, base64url, verify_app_private_data
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

TAURI_CONFIG = FRONTEND_ROOT / "src-tauri" / "tauri.mvp-user-journey-e2e.conf.json"
EXECUTOR_SPEC = (
    BACKEND_ROOT / "tests" / "fixtures" / "automation-tool-executor-h816f.spec"
)
APP_IDENTIFIER = "com.aventador.automationtool.h816facceptance"
ENVIRONMENT_ID = "h816f-acceptance"
EXECUTOR_BUILD_ID = "h8-16f-mvp-user-journey"
EXECUTOR_LEDGER_FILE = "executor-ledger.sqlite3"
DEVICE_CREDENTIAL_FILE = "device-credential-v1"


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


def require_port_available(port: int) -> None:
    with socket.socket() as listener:
        try:
            listener.bind(("127.0.0.1", port))
        except OSError as error:
            raise RuntimeError(
                f"H8-16F refuses to reuse occupied loopback port {port}"
            ) from error


def isolated_ports() -> tuple[int, int, int, int]:
    ports: list[int] = []
    while len(ports) < 4:
        candidate = unused_loopback_port()
        if candidate not in ports:
            require_port_available(candidate)
            ports.append(candidate)
    return ports[0], ports[1], ports[2], ports[3]


def require_hidden_configuration() -> None:
    configuration = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))
    windows = configuration.get("app", {}).get("windows", [])
    if (
        configuration.get("identifier") != APP_IDENTIFIER
        or len(windows) != 1
        or windows[0].get("visible") is not False
    ):
        raise RuntimeError("H8-16F acceptance must use one isolated hidden App")


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


def isolated_environment(
    *,
    control_plane_port: int,
    database_port: int,
    webdriver_port: int,
    development_database_port: int,
    observations: Path,
) -> tuple[dict[str, str], str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("AUTOMATION_TOOL_")
    }
    database_password = secrets.token_hex(24)
    database_url = (
        "postgresql+asyncpg://automation_tool_h816f:"
        f"{database_password}@127.0.0.1:{database_port}/automation_tool_h816f"
    )
    bootstrap_token, bootstrap_public_key = signed_bootstrap()
    action_signer = Ed25519PrivateKey.generate()
    environment.update(
        {
            "AUTOMATION_TOOL_DEV_DB_USER": "unused_h816f_dev",
            "AUTOMATION_TOOL_DEV_DB_PASSWORD": secrets.token_hex(24),
            "AUTOMATION_TOOL_DEV_DB_NAME": "unused_h816f_dev",
            "AUTOMATION_TOOL_DEV_DB_PORT": str(development_database_port),
            "AUTOMATION_TOOL_TEST_DB_USER": "automation_tool_h816f",
            "AUTOMATION_TOOL_TEST_DB_PASSWORD": database_password,
            "AUTOMATION_TOOL_TEST_DB_NAME": "automation_tool_h816f",
            "AUTOMATION_TOOL_TEST_DB_PORT": str(database_port),
            "AUTOMATION_TOOL_DATABASE_URL": database_url,
            "AUTOMATION_TOOL_DEMO_ENVIRONMENT_ID": ENVIRONMENT_ID,
            "AUTOMATION_TOOL_DEMO_BOOTSTRAP_PUBLIC_KEY": bootstrap_public_key,
            "AUTOMATION_TOOL_T317_BOOTSTRAP_TOKEN": bootstrap_token,
            "AUTOMATION_TOOL_T317_ENVIRONMENT_ID": ENVIRONMENT_ID,
            "AUTOMATION_TOOL_CONTROL_PLANE_E2E_ORIGIN": (
                f"http://127.0.0.1:{control_plane_port}"
            ),
            "AUTOMATION_TOOL_ACTION_AUTHORIZATION_PRIVATE_KEY": base64url(
                action_signer.private_bytes_raw()
            ),
            "AUTOMATION_TOOL_ACTION_AUTHORIZATION_PUBLIC_KEY": base64url(
                action_signer.public_key().public_bytes_raw()
            ),
            "AUTOMATION_TOOL_ACTION_MINIMUM_INTERVAL_SECONDS": "1",
            "AUTOMATION_TOOL_ACTION_TASK_LIMIT": "20",
            "AUTOMATION_TOOL_ACTION_DAILY_LIMIT": "100",
            "AUTOMATION_TOOL_ACTION_CONSECUTIVE_FAILURE_THRESHOLD": "3",
            "AUTOMATION_TOOL_LOCAL_ACTION_MINIMUM_INTERVAL_SECONDS": "1",
            "AUTOMATION_TOOL_LOCAL_ACTION_TASK_LIMIT": "20",
            "AUTOMATION_TOOL_H816F_OBSERVATIONS": os.fspath(observations),
            "TAURI_WEBDRIVER_PORT": str(webdriver_port),
        }
    )
    return environment, database_url


def verify_observations(path: Path) -> None:
    if not path.is_file():
        raise RuntimeError("H8-16F controlled browser observations are missing")
    if os.name == "posix" and stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise RuntimeError("H8-16F browser observations are over-permissive")
    documents = [
        json.loads(line) for line in path.read_text(encoding="ascii").splitlines()
    ]
    events = [document.get("event") for document in documents]
    required = {
        "login_browser_started": 1,
        "discovery_completed": 1,
        "action_browser_started": 1,
        "browse_page_closed": 1,
    }
    if {event: events.count(event) for event in required} != required:
        raise RuntimeError(
            "H8-16F browser lifecycle facts are incomplete or duplicated"
        )
    facts = {document["event"]: document for document in documents}
    if (
        facts["login_browser_started"].get("headless") is not True
        or facts["action_browser_started"].get("headless") is not True
        or facts["discovery_completed"].get("candidateCount") != 2
        or facts["browse_page_closed"].get("sideEffects") != 0
    ):
        raise RuntimeError(
            "H8-16F controlled browser facts violated the acceptance boundary"
        )


def verify_executor_private_data(private_app_data: Path) -> None:
    verify_app_private_data(private_app_data)
    ledger = private_app_data / "local-executor" / "state" / EXECUTOR_LEDGER_FILE
    credential = private_app_data / DEVICE_CREDENTIAL_FILE
    if not ledger.is_file():
        raise RuntimeError("H8-16F Local Executor ledger is missing")
    with closing(sqlite3.connect(ledger)) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()
        identities = connection.execute(
            "SELECT installation_id, executor_id FROM executor_identity"
        ).fetchall()
    if version != (7,) or len(identities) != 1:
        raise RuntimeError(
            "H8-16F Local Executor ledger has an invalid identity or version"
        )
    if credential.read_bytes() in ledger.read_bytes():
        raise RuntimeError(
            "H8-16F persisted the long-lived device credential in SQLite"
        )
    if os.name == "posix" and stat.S_IMODE(ledger.stat().st_mode) != 0o600:
        raise RuntimeError("H8-16F Local Executor ledger is over-permissive")


async def verify_database_state(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            installation_count = await connection.scalar(
                text("select count(*) from installations")
            )
            tasks = (
                await connection.execute(
                    text(
                        "select t.id::text, t.status, d.search_keyword, d.action, "
                        "d.target_limit, d.minimum_interval_seconds, "
                        "d.maximum_interval_seconds, d.preview_required, "
                        "d.final_confirmation_required "
                        "from tasks t join douyin_search_exposure_definitions d "
                        "on d.task_id = t.id"
                    )
                )
            ).all()
            attempts = (
                await connection.execute(
                    text(
                        "select attempt_number, status from execution_attempts "
                        "order by attempt_number"
                    )
                )
            ).all()
            targets = (
                await connection.execute(
                    text(
                        "select ordinal, display_name, disposition from task_targets "
                        "order by ordinal"
                    )
                )
            ).all()
            exclusions = (
                await connection.execute(
                    text(
                        "select tt.ordinal from task_target_exclusions e "
                        "join task_targets tt on tt.id = e.target_id order by tt.ordinal"
                    )
                )
            ).all()
            confirmations = (
                await connection.execute(
                    text(
                        "select selected_target_count, action, message_template "
                        "from task_target_confirmations"
                    )
                )
            ).all()
            actions = (
                await connection.execute(
                    text(
                        "select ordinal, status, outcome, evidence_code "
                        "from task_actions order by ordinal"
                    )
                )
            ).all()
            command_types = list(
                await connection.scalars(
                    text(
                        "select command_type from task_commands order by created_at, sequence"
                    )
                )
            )
            unacknowledged_commands = await connection.scalar(
                text(
                    "select count(*) from task_commands where status <> 'acknowledged'"
                )
            )
            event_types = list(
                await connection.scalars(
                    text("select event_type from task_events order by sequence")
                )
            )
            platform = (
                await connection.execute(
                    text("select platform, state from platform_session_health")
                )
            ).all()
            executor_sessions = await connection.scalar(
                text(
                    "select count(*) from device_sessions where capability = 'executor.connect'"
                )
            )
    finally:
        await engine.dispose()

    if installation_count != 1:
        raise RuntimeError("H8-16F did not preserve one Installation")
    if len(tasks) != 1 or tasks[0][1:] != (
        "succeeded",
        "新能源汽车",
        "browse",
        2,
        1,
        1,
        True,
        True,
    ):
        raise RuntimeError("H8-16F Task definition or terminal state is invalid")
    if attempts != [(1, "succeeded"), (2, "succeeded")]:
        raise RuntimeError("H8-16F discovery and action Attempts did not converge")
    if targets != [
        (1, "验收目标 1", "eligible"),
        (2, "验收目标 2", "eligible"),
    ] or exclusions != [(2,)]:
        raise RuntimeError(
            "H8-16F target discovery or user exclusion facts are invalid"
        )
    if confirmations != [(1, "browse", None)]:
        raise RuntimeError(
            "H8-16F final confirmation did not preserve the selected intent"
        )
    if actions != [(1, "verified", "succeeded", "profile_visible")]:
        raise RuntimeError("H8-16F controlled browse Action result is invalid")
    if (
        command_types.count("task.discover") != 1
        or command_types.count("task.offer") != 1
    ):
        raise RuntimeError("H8-16F command lifecycle is incomplete")
    if command_types.count("action.execute") != 1 or unacknowledged_commands != 0:
        raise RuntimeError("H8-16F Action command was not acknowledged exactly once")
    required_events = {
        "task.awaiting_confirmation",
        "task.target_selection_updated",
        "task.targets_confirmed",
        "task.started",
        "step.started",
        "step.completed",
        "task.completed",
    }
    if not required_events.issubset(event_types):
        raise RuntimeError("H8-16F durable Task timeline is incomplete")
    if platform != [("douyin", "healthy")] or executor_sessions != 1:
        raise RuntimeError(
            "H8-16F platform Session or signed Executor Session is invalid"
        )


def main() -> None:
    require_hidden_configuration()
    control_plane_port, database_port, webdriver_port, development_database_port = (
        isolated_ports()
    )
    project_name = f"automation-tool-h816f-{os.getpid()}"
    private_app_data = app_data_directory()
    if private_app_data.exists():
        raise RuntimeError("Refusing to reuse an existing H8-16F App data directory")
    compose = compose_command(project_name)
    server: subprocess.Popen[bytes] | None = None
    app_process: subprocess.Popen[bytes] | None = None
    package_entrypoint: Path | None = None
    restore_failed = False

    with tempfile.TemporaryDirectory(prefix=f"{project_name}-") as temporary:
        workspace = Path(temporary)
        observations = workspace / "controlled-browser-observations.jsonl"
        environment, database_url = isolated_environment(
            control_plane_port=control_plane_port,
            database_port=database_port,
            webdriver_port=webdriver_port,
            development_database_port=development_database_port,
            observations=observations,
        )
        try:
            print("[H8-16F] Building the controlled real signed PyInstaller Executor")
            package_source = build_signed_executor(
                workspace,
                build_id=EXECUTOR_BUILD_ID,
                spec_path=EXECUTOR_SPEC,
            )
            package_root = install_executor_package(package_source, private_app_data)
            package_entrypoint = executor_entrypoint(package_root)

            print(f"[H8-16F] Starting isolated PostgreSQL as {project_name}")
            subprocess.run(
                [*compose, "up", "--detach", "--wait", "postgres-test"],
                check=True,
                cwd=REPOSITORY_ROOT,
                env=environment,
            )
            print("[H8-16F] Applying the complete production Alembic chain")
            subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", "head"],
                check=True,
                cwd=BACKEND_ROOT,
                env=environment,
            )
            print(
                f"[H8-16F] Starting Control Plane on isolated port {control_plane_port}"
            )
            server = start_control_plane(
                port=control_plane_port, environment=environment
            )

            print(
                "[H8-16F] Running one hidden App and only headless controlled browsers"
            )
            app_process = subprocess.Popen(
                [pnpm_executable(), "test:h8-16f-app"],
                cwd=FRONTEND_ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=sys.platform != "win32",
            )
            try:
                output_bytes, _ = app_process.communicate(timeout=600)
            except subprocess.TimeoutExpired as error:
                raise RuntimeError(
                    "H8-16F hidden App journey did not finish"
                ) from error
            output = output_bytes.decode("utf-8", errors="replace")
            print(output, end="")
            if app_process.returncode != 0:
                raise RuntimeError("H8-16F hidden App original-caller journey failed")
            app_process = None

            verify_observations(observations)
            verify_executor_private_data(private_app_data)
            asyncio.run(verify_database_state(database_url))
            assert_no_executor_process(package_entrypoint)
            require_no_residual_project_processes(private_app_data, package_entrypoint)
            print("[H8-16F] Complete hidden-App MVP user journey passed")
        finally:
            if app_process is not None:
                terminate_process(app_process)
            if package_entrypoint is not None:
                terminate_project_processes(private_app_data, package_entrypoint)
                terminate_executor_processes(package_entrypoint)
            if server is not None:
                terminate_process(server)
            subprocess.run(
                [*compose, "down", "--volumes", "--remove-orphans"],
                check=False,
                cwd=REPOSITORY_ROOT,
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            restore = subprocess.run(
                [pnpm_executable(), "build"],
                cwd=FRONTEND_ROOT,
                env=environment,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            restore_failed = restore.returncode != 0
            if private_app_data.exists():
                shutil.rmtree(private_app_data)
            for port in (
                control_plane_port,
                database_port,
                webdriver_port,
                development_database_port,
            ):
                require_port_closed(port)
    if restore_failed:
        raise RuntimeError("H8-16F failed to restore production Vite assets")


if __name__ == "__main__":
    main()
