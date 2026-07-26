#!/usr/bin/env python3
"""Run E4-14 through one hidden App and a real signed Local Executor."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import closing, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from acceptance_postgres import managed_test_postgres
from automation_tool.executor.ledger import EXECUTOR_LEDGER_SCHEMA_VERSION
from automation_tool.protocol import MAX_EXECUTOR_MESSAGE_BYTES
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from desktop_e2e_prerequisites import (
    DEBUG_APP_RESOURCE_ROOT,
    prepare_startup_gate,
    startup_gate_environment,
    terminate_app_process_tree,
)
from run_e4_07_acceptance import build_signed_executor
from run_i2_13_acceptance import (
    BACKEND_ROOT,
    REPOSITORY_ROOT,
    compose_command,
    require_port_closed,
    unused_loopback_port,
    wait_for_control_plane,
)
from run_t3_06_acceptance import FRONTEND_ROOT, base64url, verify_app_private_data
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

TAURI_CONFIG = FRONTEND_ROOT / "src-tauri" / "tauri.executor-lifecycle-e2e.conf.json"
APP_IDENTIFIER = "com.aventador.automationtool.e414acceptance"
ENVIRONMENT_ID = "e414-acceptance"
EXECUTOR_BUILD_ID = "e4-14-hidden-app"
DEVICE_CREDENTIAL_FILE = "device-credential-v1"
EXECUTOR_ID_FILE = "executor-id-v1"
EXECUTOR_LEDGER_FILE = "executor-ledger.sqlite3"
BROWSER_DIAGNOSTIC_SETTINGS_FILE = "browser-diagnostic-settings-v1"


def require_port_available(port: int) -> None:
    with socket.socket() as listener:
        try:
            listener.bind(("127.0.0.1", port))
        except OSError as error:
            raise RuntimeError(f"E4-14 refuses to reuse occupied loopback port {port}") from error


def isolated_ports() -> tuple[int, int]:
    control_plane_port = unused_loopback_port()
    database_port = unused_loopback_port()
    while database_port == control_plane_port:
        database_port = unused_loopback_port()
    require_port_available(control_plane_port)
    require_port_available(database_port)
    return control_plane_port, database_port


def require_hidden_tauri_configuration() -> None:
    configuration = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))
    windows = configuration.get("app", {}).get("windows", [])
    if (
        configuration.get("identifier") != APP_IDENTIFIER
        or len(windows) != 1
        or windows[0].get("visible") is not False
    ):
        raise RuntimeError("E4-14 Tauri acceptance must use its hidden isolated App")


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


def isolated_environment(
    *, control_plane_port: int, database_port: int
) -> tuple[dict[str, str], str]:
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("AUTOMATION_TOOL_")
    }
    database_password = secrets.token_hex(24)
    database_url = (
        "postgresql+asyncpg://automation_tool_e414:"
        f"{database_password}@127.0.0.1:{database_port}/automation_tool_e414"
    )
    bootstrap_token, bootstrap_public_key = signed_bootstrap()
    environment.update(
        {
            "AUTOMATION_TOOL_DEV_DB_USER": "unused_e414_dev",
            "AUTOMATION_TOOL_DEV_DB_PASSWORD": secrets.token_hex(24),
            "AUTOMATION_TOOL_DEV_DB_NAME": "unused_e414_dev",
            "AUTOMATION_TOOL_DEV_DB_PORT": str(unused_loopback_port()),
            "AUTOMATION_TOOL_TEST_DB_USER": "automation_tool_e414",
            "AUTOMATION_TOOL_TEST_DB_PASSWORD": database_password,
            "AUTOMATION_TOOL_TEST_DB_NAME": "automation_tool_e414",
            "AUTOMATION_TOOL_TEST_DB_PORT": str(database_port),
            "AUTOMATION_TOOL_DATABASE_URL": database_url,
            "AUTOMATION_TOOL_DEMO_ENVIRONMENT_ID": ENVIRONMENT_ID,
            "AUTOMATION_TOOL_DEMO_BOOTSTRAP_PUBLIC_KEY": bootstrap_public_key,
            "AUTOMATION_TOOL_E414_BOOTSTRAP_TOKEN": bootstrap_token,
            "AUTOMATION_TOOL_E414_ENVIRONMENT_ID": ENVIRONMENT_ID,
            "AUTOMATION_TOOL_CONTROL_PLANE_E2E_ORIGIN": (f"http://127.0.0.1:{control_plane_port}"),
        }
    )
    return (
        startup_gate_environment(environment, control_plane_port=control_plane_port),
        database_url,
    )


def install_executor_package(
    source: Path, *, resource_root: Path = DEBUG_APP_RESOURCE_ROOT
) -> Path:
    """Stage a test package where every App build resolves packaged resources."""
    local_executor = resource_root / "local-executor"
    local_executor.mkdir(parents=True, exist_ok=True)
    package_root = local_executor / "package"
    shutil.rmtree(package_root, ignore_errors=True)
    shutil.copytree(source, package_root)
    return package_root


def start_control_plane(*, port: int, environment: dict[str, str]) -> subprocess.Popen[bytes]:
    require_port_available(port)
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
    try:
        wait_for_control_plane(port, server)
    except BaseException:
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
        raise
    return server


def executor_entrypoint(package_root: Path) -> Path:
    name = "automation-tool-executor.exe" if sys.platform == "win32" else "automation-tool-executor"
    path = package_root / name
    if not path.is_file():
        raise RuntimeError("E4-14 signed Executor entrypoint is missing")
    return path


def matching_executor_processes(entrypoint: Path) -> list[tuple[int, str]]:
    target = os.fspath(entrypoint.resolve())
    if sys.platform != "win32":
        completed = subprocess.run(
            ["ps", "-axo", "pid=,command="],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        matches: list[tuple[int, str]] = []
        for line in completed.stdout.splitlines():
            if target not in line:
                continue
            process_id, _, command = line.strip().partition(" ")
            if process_id.isdigit():
                matches.append((int(process_id), command.strip()))
        return matches
    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            (
                "Get-CimInstance Win32_Process | "
                "Select-Object ProcessId,ExecutablePath,CommandLine | "
                "ConvertTo-Json -Compress"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    decoded = json.loads(completed.stdout or "[]")
    rows = decoded if isinstance(decoded, list) else [decoded]
    return [
        (int(row["ProcessId"]), json.dumps(row, sort_keys=True))
        for row in rows
        if isinstance(row, dict)
        and isinstance(row.get("ProcessId"), int)
        and (row.get("ExecutablePath") == target or target in str(row.get("CommandLine") or ""))
    ]


def assert_no_executor_process(entrypoint: Path) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if not matching_executor_processes(entrypoint):
            return
        time.sleep(0.1)
    raise RuntimeError("E4-14 App exit left its signed Executor process running")


def terminate_executor_processes(entrypoint: Path) -> None:
    for process_id, _description in matching_executor_processes(entrypoint):
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(process_id), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            with suppress(ProcessLookupError):
                os.kill(process_id, signal.SIGKILL)


def verify_executor_app_data(private_app_data: Path, installation_id: str) -> None:
    verify_app_private_data(private_app_data)
    executor_root = private_app_data / "local-executor"
    executor_id_path = executor_root / EXECUTOR_ID_FILE
    diagnostic_settings_path = executor_root / BROWSER_DIAGNOSTIC_SETTINGS_FILE
    ledger_path = executor_root / "state" / EXECUTOR_LEDGER_FILE
    if (
        not executor_id_path.is_file()
        or not diagnostic_settings_path.is_file()
        or not ledger_path.is_file()
    ):
        raise RuntimeError("E4-14 App-private Executor state is incomplete")
    if json.loads(diagnostic_settings_path.read_bytes()) != {
        "version": "1",
        "capture_successful_runs": True,
    }:
        raise RuntimeError("E4-14 successful diagnostic setting was not persisted")
    executor_id_text = executor_id_path.read_text(encoding="ascii")
    executor_id = UUID(executor_id_text)
    if executor_id.version != 4 or str(executor_id) != executor_id_text:
        raise RuntimeError("E4-14 stable Executor identity is not canonical UUIDv4")
    with closing(sqlite3.connect(ledger_path)) as connection:
        if connection.execute("PRAGMA user_version").fetchone() != (
            EXECUTOR_LEDGER_SCHEMA_VERSION,
        ):
            raise RuntimeError("E4-14 Executor ledger did not migrate to the current schema")
        identity = connection.execute(
            "SELECT installation_id, executor_id FROM executor_identity"
        ).fetchone()
    if identity != (installation_id, executor_id_text):
        raise RuntimeError("E4-14 ledger identity escaped the App-private binding")
    credential = (private_app_data / DEVICE_CREDENTIAL_FILE).read_bytes()
    ledger_bytes = ledger_path.read_bytes()
    if credential in ledger_bytes:
        raise RuntimeError("E4-14 persisted the long-lived App credential in SQLite")
    if os.name == "posix":
        for path, expected in [
            (executor_root, 0o700),
            (executor_id_path, 0o600),
            (diagnostic_settings_path, 0o600),
            (executor_root / "state", 0o700),
            (ledger_path, 0o600),
        ]:
            if stat.S_IMODE(path.stat().st_mode) != expected:
                raise RuntimeError("E4-14 Executor App-data permissions are invalid")


async def verify_database_state(database_url: str, installation_id: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            installations = (
                await connection.execute(
                    text("select id::text, status, revision from installations")
                )
            ).all()
            capabilities = list(
                await connection.scalars(
                    text("select capability from device_sessions order by created_at")
                )
            )
    finally:
        await engine.dispose()
    if installations != [(installation_id, "active", 1)]:
        raise RuntimeError("E4-14 Installation final state is invalid")
    if capabilities.count("executor.connect") < 2 or any(
        value not in {"app.control-plane", "executor.connect"} for value in capabilities
    ):
        raise RuntimeError("E4-14 did not use only the intended Session capabilities")


async def acceptance_fact_summary(database_url: str) -> dict[str, object]:
    """Return only non-secret lifecycle facts for a failed App-path diagnosis."""
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            installation_count = await connection.scalar(text("select count(*) from installations"))
            session_capabilities = (
                await connection.execute(
                    text(
                        "select capability, count(*) from device_sessions "
                        "group by capability order by capability"
                    )
                )
            ).all()
    finally:
        await engine.dispose()
    return {
        "installationCount": installation_count,
        "sessionCapabilities": [list(row) for row in session_capabilities],
    }


def pnpm_executable() -> str:
    name = "pnpm.cmd" if sys.platform == "win32" else "pnpm"
    executable = shutil.which(name)
    if executable is None:
        raise RuntimeError("E4-14 pnpm executable is unavailable")
    return executable


def main() -> None:
    require_hidden_tauri_configuration()
    control_plane_port, database_port = isolated_ports()
    project_name = f"automation-tool-e414-{os.getpid()}"
    private_app_data = app_data_directory()
    if private_app_data.exists():
        raise RuntimeError("Refusing to reuse an existing E4-14 App data directory")
    prepare_startup_gate(private_app_data, executor_package=False)
    environment, database_url = isolated_environment(
        control_plane_port=control_plane_port,
        database_port=database_port,
    )
    compose = compose_command(project_name)
    postgres_context = managed_test_postgres(
        compose=compose,
        database_port=database_port,
        environment=environment,
        repository_root=REPOSITORY_ROOT,
    )
    postgres_started = False
    server: subprocess.Popen[bytes] | None = None
    app_process: subprocess.Popen[bytes] | None = None
    package_entrypoint: Path | None = None

    with tempfile.TemporaryDirectory(prefix=f"{project_name}-") as temporary:
        workspace = Path(temporary)
        try:
            print("[E4-14] Building the real signed PyInstaller Executor")
            package_source = build_signed_executor(
                workspace,
                build_id=EXECUTOR_BUILD_ID,
            )
            package_root = install_executor_package(package_source)
            package_entrypoint = executor_entrypoint(package_root)

            require_port_available(database_port)
            print(f"[E4-14] Starting isolated PostgreSQL as {project_name}")
            postgres_context.__enter__()
            postgres_started = True
            print("[E4-14] Applying the production Alembic migration chain")
            subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", "head"],
                check=True,
                cwd=BACKEND_ROOT,
                env=environment,
            )
            print(f"[E4-14] Starting Control Plane on isolated port {control_plane_port}")
            server = start_control_plane(
                port=control_plane_port,
                environment=environment,
            )

            print("[E4-14] Running one real Tauri App with visible=false")
            app_process = subprocess.Popen(
                [pnpm_executable(), "test:executor-lifecycle-tauri"],
                cwd=FRONTEND_ROOT,
                env=environment,
                start_new_session=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            try:
                app_output_bytes, _ = app_process.communicate(timeout=420)
            except subprocess.TimeoutExpired as error:
                raise RuntimeError("E4-14 hidden App lifecycle did not finish") from error
            app_output = app_output_bytes.decode("utf-8", errors="replace")
            print(app_output, end="")
            if app_process.returncode != 0:
                facts = asyncio.run(acceptance_fact_summary(database_url))
                raise RuntimeError(f"E4-14 hidden App lifecycle acceptance failed: {facts}")
            app_process = None

            installation_rows = asyncio.run(_installation_ids(database_url))
            if len(installation_rows) != 1:
                raise RuntimeError("E4-14 did not create exactly one Installation")
            installation_id = installation_rows[0]
            verify_executor_app_data(private_app_data, installation_id)
            assert_no_executor_process(package_entrypoint)
            asyncio.run(verify_database_state(database_url, installation_id))
            print("[E4-14] Hidden-App signed Executor lifecycle acceptance passed")
        finally:
            if app_process is not None:
                terminate_app_process_tree(app_process)
            if package_entrypoint is not None:
                terminate_executor_processes(package_entrypoint)
            if server is not None and server.poll() is None:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)
            if postgres_started:
                postgres_context.__exit__(None, None, None)
            if private_app_data.exists():
                shutil.rmtree(private_app_data)
            require_port_closed(control_plane_port)
            require_port_closed(database_port)


async def _installation_ids(database_url: str) -> list[str]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            return list(
                await connection.scalars(text("select id::text from installations order by id"))
            )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    main()
