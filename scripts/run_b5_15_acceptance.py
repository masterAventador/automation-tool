#!/usr/bin/env python3
"""Run B5-15 through four hidden App/Executor/browser lifetimes and one Profile."""

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
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from automation_tool.executor.ledger import EXECUTOR_LEDGER_SCHEMA_VERSION
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from desktop_e2e_prerequisites import (
    CURRENT_DOUYIN_PROFILE_FILE,
    OPERATIONS_PROFILE_ROOT,
    prepare_startup_gate,
    startup_gate_environment,
)
from run_b5_13_acceptance import (
    require_no_residual_project_processes,
    terminate_process,
    terminate_project_processes,
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
from run_t3_06_acceptance import FRONTEND_ROOT, base64url
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

TAURI_CONFIG = (
    FRONTEND_ROOT / "src-tauri" / "tauri.platform-session-reuse-e2e.conf.json"
)
EXECUTOR_SPEC = BACKEND_ROOT / "tests/fixtures/automation-tool-executor-b515.spec"
APP_IDENTIFIER = "com.aventador.automationtool.b515acceptance"
ENVIRONMENT_ID = "b515-acceptance"
EXECUTOR_BUILD_ID = "b5-15-platform-session-reuse"
PAGE_STATES = ("healthy", "healthy", "expired", "risk")
PHASES = ("first", "restart", "expired", "risk")


@dataclass(frozen=True, slots=True)
class ProfileIdentity:
    marker_digest: bytes
    device: int
    inode: int


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
        raise RuntimeError("B5-15 Tauri acceptance must use its hidden isolated App")


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
    *,
    control_plane_port: int,
    database_port: int,
    page_state_path: Path,
) -> tuple[dict[str, str], str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("AUTOMATION_TOOL_")
    }
    database_password = secrets.token_hex(24)
    database_url = (
        "postgresql+asyncpg://automation_tool_b515:"
        f"{database_password}@127.0.0.1:{database_port}/automation_tool_b515"
    )
    bootstrap_token, bootstrap_public_key = signed_bootstrap()
    environment.update(
        {
            "AUTOMATION_TOOL_DEV_DB_USER": "unused_b515_dev",
            "AUTOMATION_TOOL_DEV_DB_PASSWORD": secrets.token_hex(24),
            "AUTOMATION_TOOL_DEV_DB_NAME": "unused_b515_dev",
            "AUTOMATION_TOOL_DEV_DB_PORT": str(unused_loopback_port()),
            "AUTOMATION_TOOL_TEST_DB_USER": "automation_tool_b515",
            "AUTOMATION_TOOL_TEST_DB_PASSWORD": database_password,
            "AUTOMATION_TOOL_TEST_DB_NAME": "automation_tool_b515",
            "AUTOMATION_TOOL_TEST_DB_PORT": str(database_port),
            "AUTOMATION_TOOL_DATABASE_URL": database_url,
            "AUTOMATION_TOOL_DEMO_ENVIRONMENT_ID": ENVIRONMENT_ID,
            "AUTOMATION_TOOL_DEMO_BOOTSTRAP_PUBLIC_KEY": bootstrap_public_key,
            "AUTOMATION_TOOL_B515_BOOTSTRAP_TOKEN": bootstrap_token,
            "AUTOMATION_TOOL_B515_ENVIRONMENT_ID": ENVIRONMENT_ID,
            "AUTOMATION_TOOL_B515_PAGE_STATE": os.fspath(page_state_path),
            "AUTOMATION_TOOL_CONTROL_PLANE_E2E_ORIGIN": (
                f"http://127.0.0.1:{control_plane_port}"
            ),
        }
    )
    return (
        startup_gate_environment(environment, control_plane_port=control_plane_port),
        database_url,
    )


def write_page_state(path: Path, state: str) -> None:
    if state not in {"healthy", "expired", "risk"}:
        raise RuntimeError("B5-15 page state is invalid")
    path.write_text(state, encoding="ascii")
    path.chmod(0o600)


def profile_identity(private_app_data: Path) -> ProfileIdentity:
    profile_root = private_app_data / OPERATIONS_PROFILE_ROOT
    marker = profile_root / CURRENT_DOUYIN_PROFILE_FILE
    encoded_profile_id = marker.read_bytes()
    try:
        profile_id = encoded_profile_id.decode("ascii")
        parsed = UUID(profile_id)
    except (UnicodeDecodeError, ValueError):
        raise RuntimeError("B5-15 current Profile marker is invalid") from None
    if parsed.version != 4 or str(parsed) != profile_id:
        raise RuntimeError("B5-15 current Profile identity is invalid")
    profile_directory = profile_root / "douyin" / profile_id
    metadata = profile_directory.stat(follow_symlinks=False)
    if not profile_directory.is_dir() or profile_directory.is_symlink():
        raise RuntimeError("B5-15 current Profile directory is invalid")
    return ProfileIdentity(
        marker_digest=hashlib.sha256(encoded_profile_id).digest(),
        device=metadata.st_dev,
        inode=metadata.st_ino,
    )


def verify_local_session_state(private_app_data: Path) -> None:
    ledger = private_app_data / "local-executor" / "state" / "executor-ledger.sqlite3"
    if not ledger.is_file():
        raise RuntimeError("B5-15 Local Executor ledger is missing")
    with sqlite3.connect(ledger) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()
        row = connection.execute(
            "SELECT platform, state, session_revision FROM executor_platform_sessions"
        ).fetchone()
    if version != (EXECUTOR_LEDGER_SCHEMA_VERSION,) or row != ("douyin", "risk", 2):
        raise RuntimeError("B5-15 Local Executor Session epoch is invalid")


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
            gate_count = await connection.scalar(
                text("select count(*) from platform_session_gates")
            )
            task_count = await connection.scalar(text("select count(*) from tasks"))
    finally:
        await engine.dispose()
    if installation_count != 1:
        raise RuntimeError("B5-15 acceptance did not retain exactly one Installation")
    if rows != [("douyin", "risk", 2, True)]:
        raise RuntimeError("B5-15 final platform health projection is invalid")
    if gate_count != 0 or task_count != 0:
        raise RuntimeError("B5-15 created an unexpected gate or Task")


def run_hidden_app_phase(
    *,
    phase: str,
    page_state: str,
    page_state_path: Path,
    environment: dict[str, str],
) -> None:
    write_page_state(page_state_path, page_state)
    phase_environment = environment | {"AUTOMATION_TOOL_B515_PHASE": phase}
    process = subprocess.Popen(
        ["pnpm", "exec", "wdio", "run", "wdio.platform-session-reuse.conf.ts"],
        cwd=FRONTEND_ROOT,
        env=phase_environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        output_bytes, _ = process.communicate(timeout=480)
    except subprocess.TimeoutExpired as error:
        terminate_process(process)
        raise RuntimeError("B5-15 hidden App phase did not finish") from error
    output = output_bytes.decode("utf-8", errors="replace")
    print(output, end="")
    if process.returncode != 0:
        raise RuntimeError("B5-15 hidden App production-path phase failed")


def main() -> None:
    require_hidden_tauri_configuration()
    control_plane_port, database_port = isolated_ports()
    project_name = f"automation-tool-b515-{os.getpid()}"
    private_app_data = app_data_directory()
    if private_app_data.exists():
        raise RuntimeError("Refusing to reuse an existing B5-15 App data directory")
    prepare_startup_gate(private_app_data, executor_package=False)
    compose = compose_command(project_name)
    server: subprocess.Popen[bytes] | None = None
    package_entrypoint: Path | None = None

    with tempfile.TemporaryDirectory(prefix=f"{project_name}-") as temporary:
        workspace = Path(temporary)
        page_state_path = workspace / "page-state"
        write_page_state(page_state_path, "healthy")
        environment, database_url = isolated_environment(
            control_plane_port=control_plane_port,
            database_port=database_port,
            page_state_path=page_state_path,
        )
        try:
            print("[B5-15] Building the separately signed acceptance Executor")
            package_source = build_signed_executor(
                workspace,
                build_id=EXECUTOR_BUILD_ID,
                spec_path=EXECUTOR_SPEC,
            )
            package_root = install_executor_package(package_source, private_app_data)
            package_entrypoint = executor_entrypoint(package_root)

            require_port_available(database_port)
            print(f"[B5-15] Starting isolated PostgreSQL as {project_name}")
            subprocess.run(
                [*compose, "up", "--detach", "--wait", "postgres-test"],
                check=True,
                cwd=REPOSITORY_ROOT,
                env=environment,
            )
            print("[B5-15] Applying the production Alembic migration chain")
            subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", "head"],
                check=True,
                cwd=BACKEND_ROOT,
                env=environment,
            )
            print(
                f"[B5-15] Starting Control Plane on isolated port {control_plane_port}"
            )
            server = start_control_plane(
                port=control_plane_port, environment=environment
            )

            print("[B5-15] Building one hidden Tauri App for four restart phases")
            subprocess.run(
                ["pnpm", "build:tauri:platform-session-reuse-test"],
                check=True,
                cwd=FRONTEND_ROOT,
                env=environment,
                timeout=600,
            )

            observed_identity: ProfileIdentity | None = None
            for phase, state in zip(PHASES, PAGE_STATES, strict=True):
                print(f"[B5-15] Running hidden restart phase: {phase}")
                run_hidden_app_phase(
                    phase=phase,
                    page_state=state,
                    page_state_path=page_state_path,
                    environment=environment,
                )
                if package_entrypoint is None:
                    raise RuntimeError("B5-15 Executor entrypoint is unavailable")
                require_no_residual_project_processes(
                    private_app_data, package_entrypoint
                )
                current_identity = profile_identity(private_app_data)
                if observed_identity is None:
                    observed_identity = current_identity
                elif current_identity != observed_identity:
                    raise RuntimeError(
                        "B5-15 did not retain one stable Profile across restarts"
                    )

            verify_local_session_state(private_app_data)
            asyncio.run(verify_database_state(database_url))
            print(
                "[B5-15] Hidden-App Profile reuse and fail-closed handoff acceptance passed"
            )
        finally:
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
