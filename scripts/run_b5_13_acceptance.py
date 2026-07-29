#!/usr/bin/env python3
"""Run B5-13 through one hidden App, Control Plane, and signed Executor."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from desktop_e2e_prerequisites import (
    CURRENT_DOUYIN_PROFILE_FILE,
    OPERATIONS_PROFILE_ROOT,
    prepare_startup_gate,
    startup_gate_environment,
    terminate_app_process_tree,
)
from run_e4_07_acceptance import build_signed_executor
from run_e4_14_acceptance import (
    executor_entrypoint,
    install_executor_package,
    require_port_available,
    start_control_plane,
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

TAURI_CONFIG = FRONTEND_ROOT / "src-tauri" / "tauri.platform-session-e2e.conf.json"
APP_IDENTIFIER = "com.aventador.automationtool.b513acceptance"
ENVIRONMENT_ID = "b513-acceptance"
EXECUTOR_BUILD_ID = "b5-13-platform-session"
VALID_STATES = {"healthy", "expired", "missing", "risk", "unknown"}


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
        raise RuntimeError("B5-13 Tauri acceptance must use its hidden isolated App")


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
        key: value
        for key, value in os.environ.items()
        if not key.startswith("AUTOMATION_TOOL_")
    }
    database_password = secrets.token_hex(24)
    database_url = (
        "postgresql+asyncpg://automation_tool_b513:"
        f"{database_password}@127.0.0.1:{database_port}/automation_tool_b513"
    )
    bootstrap_token, bootstrap_public_key = signed_bootstrap()
    environment.update(
        {
            "AUTOMATION_TOOL_DEV_DB_USER": "unused_b513_dev",
            "AUTOMATION_TOOL_DEV_DB_PASSWORD": secrets.token_hex(24),
            "AUTOMATION_TOOL_DEV_DB_NAME": "unused_b513_dev",
            "AUTOMATION_TOOL_DEV_DB_PORT": str(unused_loopback_port()),
            "AUTOMATION_TOOL_TEST_DB_USER": "automation_tool_b513",
            "AUTOMATION_TOOL_TEST_DB_PASSWORD": database_password,
            "AUTOMATION_TOOL_TEST_DB_NAME": "automation_tool_b513",
            "AUTOMATION_TOOL_TEST_DB_PORT": str(database_port),
            "AUTOMATION_TOOL_DATABASE_URL": database_url,
            "AUTOMATION_TOOL_DEMO_ENVIRONMENT_ID": ENVIRONMENT_ID,
            "AUTOMATION_TOOL_DEMO_BOOTSTRAP_PUBLIC_KEY": bootstrap_public_key,
            "AUTOMATION_TOOL_B513_BOOTSTRAP_TOKEN": bootstrap_token,
            "AUTOMATION_TOOL_B513_ENVIRONMENT_ID": ENVIRONMENT_ID,
            "AUTOMATION_TOOL_CONTROL_PLANE_E2E_ORIGIN": (
                f"http://127.0.0.1:{control_plane_port}"
            ),
        }
    )
    return (
        startup_gate_environment(environment, control_plane_port=control_plane_port),
        database_url,
    )


async def verify_database_state(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            installation_count = await connection.scalar(
                text("select count(*) from installations")
            )
            rows = (
                await connection.execute(
                    text(
                        "select platform, state, session_revision, "
                        "observed_at <= updated_at from platform_session_health"
                    )
                )
            ).all()
            gate_rows = (
                await connection.execute(
                    text(
                        "select platform, state, session_revision "
                        "from platform_session_gates"
                    )
                )
            ).all()
            task_count = await connection.scalar(text("select count(*) from tasks"))
    finally:
        await engine.dispose()
    if installation_count != 1:
        raise RuntimeError("B5-13 acceptance did not create exactly one Installation")
    if len(rows) != 1:
        raise RuntimeError(
            "B5-13 acceptance did not persist one platform health projection"
        )
    platform, state, revision, timestamp_ordered = rows[0]
    if (
        platform != "douyin"
        or state != "missing"
        or not isinstance(revision, int)
        or revision < 1
        or timestamp_ordered is not True
    ):
        raise RuntimeError("B5-13 platform health projection is invalid")
    if gate_rows != [("douyin", "blocked", revision)] or task_count != 0:
        raise RuntimeError("B5-14 logout gate or blocked Task projection is invalid")


def verify_logout_local_state(private_app_data: Path) -> None:
    profile_root = private_app_data / OPERATIONS_PROFILE_ROOT
    current_marker = profile_root / CURRENT_DOUYIN_PROFILE_FILE
    platform_root = profile_root / "douyin"
    if current_marker.exists():
        # 标记是谁、什么时候复活的——current_douyin_profile() 在标记缺失时会新建
        # Profile 并写回标记，所以登出后的任何一次读取都能让它回来。mtime 定位
        # 复活时刻（在登出命令内部还是登出之后的哪一步），内容是新 Profile 的 id。
        import datetime as _dt
        marker_mtime = _dt.datetime.fromtimestamp(current_marker.stat().st_mtime)
        marker_content = current_marker.read_bytes()[:64]
        siblings = sorted(
            f"{child.name}@{_dt.datetime.fromtimestamp(child.stat().st_mtime):%H:%M:%S}"
            for child in platform_root.iterdir()
        ) if platform_root.is_dir() else []
        raise RuntimeError(
            "B5-14 safe logout retained the current Profile marker: "
            f"mtime={marker_mtime:%H:%M:%S} content={marker_content!r} "
            f"douyin_dir={siblings}"
        )
    if platform_root.is_dir() and any(platform_root.iterdir()):
        raise RuntimeError("B5-14 safe logout retained a Profile or removal tombstone")
    ledger = private_app_data / "local-executor" / "state" / "executor-ledger.sqlite3"
    if not ledger.is_file():
        raise RuntimeError("B5-14 safe logout removed the Local Executor ledger")


def matching_project_processes(
    private_app_data: Path, package_entrypoint: Path
) -> list[int]:
    markers = (
        os.fspath(private_app_data.resolve()),
        os.fspath(package_entrypoint.resolve()),
    )
    if sys.platform == "win32":
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
            int(row["ProcessId"])
            for row in rows
            if isinstance(row, dict)
            and isinstance(row.get("ProcessId"), int)
            and any(marker in str(row.get("CommandLine") or "") for marker in markers)
        ]
    completed = subprocess.run(
        ["ps", "-axo", "pid=,command="],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    matches: list[int] = []
    for line in completed.stdout.splitlines():
        if not any(marker in line for marker in markers):
            continue
        process_id, _, _command = line.strip().partition(" ")
        if process_id.isdigit() and int(process_id) != os.getpid():
            matches.append(int(process_id))
    return matches


def require_no_residual_project_processes(
    private_app_data: Path, package_entrypoint: Path
) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if not matching_project_processes(private_app_data, package_entrypoint):
            return
        time.sleep(0.2)
    raise RuntimeError("B5-13 left an App, Executor, or browser process running")


def terminate_project_processes(
    private_app_data: Path, package_entrypoint: Path
) -> None:
    for process_id in matching_project_processes(private_app_data, package_entrypoint):
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(process_id), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            continue
        with suppress(ProcessLookupError, PermissionError):
            process_group = os.getpgid(process_id)
            if process_group != os.getpgrp():
                os.killpg(process_group, signal.SIGKILL)
            else:
                os.kill(process_id, signal.SIGKILL)


def terminate_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def main() -> None:
    require_hidden_tauri_configuration()
    control_plane_port, database_port = isolated_ports()
    project_name = f"automation-tool-b513-{os.getpid()}"
    private_app_data = app_data_directory()
    if private_app_data.exists():
        raise RuntimeError("Refusing to reuse an existing B5-13 App data directory")
    prepare_startup_gate(private_app_data, executor_package=False)
    environment, database_url = isolated_environment(
        control_plane_port=control_plane_port,
        database_port=database_port,
    )
    compose = compose_command(project_name)
    server: subprocess.Popen[bytes] | None = None
    app_process: subprocess.Popen[bytes] | None = None
    package_entrypoint: Path | None = None

    with tempfile.TemporaryDirectory(prefix=f"{project_name}-") as temporary:
        workspace = Path(temporary)
        try:
            print("[B5-13] Building the real signed PyInstaller Executor")
            package_source = build_signed_executor(
                workspace, build_id=EXECUTOR_BUILD_ID
            )
            package_root = install_executor_package(package_source)
            package_entrypoint = executor_entrypoint(package_root)

            require_port_available(database_port)
            print(f"[B5-13] Starting isolated PostgreSQL as {project_name}")
            subprocess.run(
                [*compose, "up", "--detach", "--wait", "postgres-test"],
                check=True,
                cwd=REPOSITORY_ROOT,
                env=environment,
            )
            print("[B5-13] Applying the production Alembic migration chain")
            subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", "head"],
                check=True,
                cwd=BACKEND_ROOT,
                env=environment,
            )
            print(
                f"[B5-13] Starting Control Plane on isolated port {control_plane_port}"
            )
            server = start_control_plane(
                port=control_plane_port, environment=environment
            )

            print(
                "[B5-13] Running the real Tauri App with visible=false and headless browser"
            )
            app_process = subprocess.Popen(
                ["pnpm", "test:platform-session-tauri"],
                cwd=FRONTEND_ROOT,
                env=environment,
                start_new_session=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            try:
                app_output_bytes, _ = app_process.communicate(timeout=480)
            except subprocess.TimeoutExpired as error:
                raise RuntimeError(
                    "B5-13 hidden App acceptance did not finish"
                ) from error
            app_output = app_output_bytes.decode("utf-8", errors="replace")
            print(app_output, end="")
            if app_process.returncode != 0:
                raise RuntimeError("B5-13 hidden App production-path acceptance failed")
            app_process = None

            verify_app_private_data(private_app_data)
            verify_logout_local_state(private_app_data)
            asyncio.run(verify_database_state(database_url))
            require_no_residual_project_processes(private_app_data, package_entrypoint)
            print(
                "[B5-13/B5-14] Hidden-App platform status and safe logout acceptance passed"
            )
        finally:
            if app_process is not None:
                terminate_app_process_tree(app_process)
            if package_entrypoint is not None:
                terminate_project_processes(private_app_data, package_entrypoint)
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
            if private_app_data.exists():
                shutil.rmtree(private_app_data)
            require_port_closed(control_plane_port)
            require_port_closed(database_port)


if __name__ == "__main__":
    main()
