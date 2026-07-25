#!/usr/bin/env python3
"""Audit one live hidden-App Chrome tree for default Profile isolation."""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from desktop_e2e_prerequisites import (
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
    FRONTEND_ROOT / "src-tauri" / "tauri.default-profile-isolation-e2e.conf.json"
)
EXECUTOR_SPEC = BACKEND_ROOT / "tests/fixtures/automation-tool-executor-b515.spec"
APP_IDENTIFIER = "com.aventador.automationtool.b516acceptance"
ENVIRONMENT_ID = "b516-acceptance"
EXECUTOR_BUILD_ID = "b5-16-default-profile-isolation"
UUID_V4 = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


@dataclass(frozen=True, slots=True)
class ProcessRecord:
    process_id: int
    parent_process_id: int
    command: str


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
        raise RuntimeError("B5-16 Tauri acceptance must use its hidden isolated App")


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
    ready_path: Path,
    release_path: Path,
) -> tuple[dict[str, str], str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("AUTOMATION_TOOL_")
    }
    database_password = secrets.token_hex(24)
    database_url = (
        "postgresql+asyncpg://automation_tool_b516:"
        f"{database_password}@127.0.0.1:{database_port}/automation_tool_b516"
    )
    bootstrap_token, bootstrap_public_key = signed_bootstrap()
    environment.update(
        {
            "AUTOMATION_TOOL_DEV_DB_USER": "unused_b516_dev",
            "AUTOMATION_TOOL_DEV_DB_PASSWORD": secrets.token_hex(24),
            "AUTOMATION_TOOL_DEV_DB_NAME": "unused_b516_dev",
            "AUTOMATION_TOOL_DEV_DB_PORT": str(unused_loopback_port()),
            "AUTOMATION_TOOL_TEST_DB_USER": "automation_tool_b516",
            "AUTOMATION_TOOL_TEST_DB_PASSWORD": database_password,
            "AUTOMATION_TOOL_TEST_DB_NAME": "automation_tool_b516",
            "AUTOMATION_TOOL_TEST_DB_PORT": str(database_port),
            "AUTOMATION_TOOL_DATABASE_URL": database_url,
            "AUTOMATION_TOOL_DEMO_ENVIRONMENT_ID": ENVIRONMENT_ID,
            "AUTOMATION_TOOL_DEMO_BOOTSTRAP_PUBLIC_KEY": bootstrap_public_key,
            "AUTOMATION_TOOL_B515_BOOTSTRAP_TOKEN": bootstrap_token,
            "AUTOMATION_TOOL_B515_ENVIRONMENT_ID": ENVIRONMENT_ID,
            "AUTOMATION_TOOL_B515_PAGE_STATE": os.fspath(page_state_path),
            "AUTOMATION_TOOL_B516_READY_FILE": os.fspath(ready_path),
            "AUTOMATION_TOOL_B516_RELEASE_FILE": os.fspath(release_path),
            "AUTOMATION_TOOL_CONTROL_PLANE_E2E_ORIGIN": (
                f"http://127.0.0.1:{control_plane_port}"
            ),
        }
    )
    return (
        startup_gate_environment(environment, control_plane_port=control_plane_port),
        database_url,
    )


def current_private_profile(private_app_data: Path) -> Path:
    profile_root = private_app_data / "browser-profiles"
    marker = profile_root / "current-douyin-profile-v1"
    try:
        profile_id = marker.read_text(encoding="ascii")
        parsed = UUID(profile_id)
    except (OSError, UnicodeDecodeError, ValueError):
        raise RuntimeError("B5-16 current private Profile is unavailable") from None
    if (
        not UUID_V4.fullmatch(profile_id)
        or parsed.version != 4
        or str(parsed) != profile_id
    ):
        raise RuntimeError("B5-16 current private Profile identity is invalid")
    profile_directory = profile_root / "douyin" / profile_id
    if not profile_directory.is_dir() or profile_directory.is_symlink():
        raise RuntimeError("B5-16 current private Profile directory is invalid")
    return profile_directory.resolve(strict=True)


def process_records() -> tuple[ProcessRecord, ...]:
    if sys.platform == "win32":
        raise RuntimeError("B5-16 live open-file audit currently requires lsof")
    completed = subprocess.run(
        ["ps", "-ww", "-axo", "pid=,ppid=,command="],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    records: list[ProcessRecord] = []
    for line in completed.stdout.splitlines():
        fields = line.strip().split(maxsplit=2)
        if len(fields) != 3 or not fields[0].isdigit() or not fields[1].isdigit():
            continue
        records.append(ProcessRecord(int(fields[0]), int(fields[1]), fields[2]))
    return tuple(records)


def default_profile_roots() -> tuple[Path, ...]:
    home = Path.home()
    if sys.platform == "darwin":
        return (
            home / "Library" / "Application Support" / "Google" / "Chrome",
            home / "Library" / "Application Support" / "Microsoft Edge",
        )
    return (
        home / ".config" / "google-chrome",
        home / ".config" / "microsoft-edge",
    )


def audit_live_browser(private_app_data: Path) -> None:
    lsof = shutil.which("lsof")
    if lsof is None:
        raise RuntimeError("B5-16 requires the system lsof utility")
    private_profile = current_private_profile(private_app_data)
    private_text = os.fspath(private_profile)
    expected_argument = f"--user-data-dir={private_text}"
    records = process_records()
    roots = [
        record
        for record in records
        if expected_argument in record.command and "--type=" not in record.command
    ]
    if len(roots) != 1 or "google chrome" not in roots[0].command.casefold():
        raise RuntimeError(
            "B5-16 did not find exactly one App-owned system Chrome root"
        )

    by_parent: dict[int, list[ProcessRecord]] = {}
    for record in records:
        by_parent.setdefault(record.parent_process_id, []).append(record)
    audited: dict[int, ProcessRecord] = {roots[0].process_id: roots[0]}
    pending = [roots[0].process_id]
    while pending:
        parent = pending.pop()
        for child in by_parent.get(parent, []):
            if child.process_id not in audited:
                audited[child.process_id] = child
                pending.append(child.process_id)
    for record in records:
        if private_text in record.command:
            audited[record.process_id] = record

    default_roots = tuple(
        os.fspath(path).casefold() for path in default_profile_roots()
    )
    for record in audited.values():
        command = record.command.casefold()
        if (
            "--user-data-dir=" in command
            and expected_argument.casefold() not in command
        ):
            raise RuntimeError(
                "B5-16 Chrome process tree changed its Profile directory"
            )
        if any(root in command for root in default_roots):
            raise RuntimeError("B5-16 Chrome process tree referenced a default Profile")

    completed = subprocess.run(
        [
            lsof,
            "-nP",
            "-p",
            ",".join(str(process_id) for process_id in sorted(audited)),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    opened_files = completed.stdout.casefold()
    if private_text.casefold() not in opened_files:
        raise RuntimeError("B5-16 lsof did not observe the App-owned private Profile")
    if any(root in opened_files for root in default_roots):
        raise RuntimeError("B5-16 lsof observed a default browser Profile")


def wait_for_ready(path: Path, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if path.is_file():
            return
        if process.poll() is not None:
            raise RuntimeError("B5-16 hidden App exited before the live audit")
        time.sleep(0.1)
    raise RuntimeError("B5-16 hidden App did not expose the live audit window")


def sanitized_output(output: str, private_app_data: Path) -> str:
    redacted = output.replace(os.fspath(private_app_data), "<app-private-data>")
    return re.sub(
        r"(?<![0-9a-f])[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}(?![0-9a-f])",
        "<uuid-v4>",
        redacted,
        flags=re.IGNORECASE,
    )


def verify_local_session_state(private_app_data: Path) -> None:
    ledger = private_app_data / "local-executor" / "state" / "executor-ledger.sqlite3"
    if not ledger.is_file():
        raise RuntimeError("B5-16 Local Executor ledger is missing")
    with sqlite3.connect(ledger) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()
        row = connection.execute(
            "SELECT platform, state, session_revision FROM executor_platform_sessions"
        ).fetchone()
    if version != (2,) or row != ("douyin", "expired", 1):
        raise RuntimeError("B5-16 Local Executor Session state is invalid")


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
    if installation_count != 1 or rows != [("douyin", "expired", 1, True)]:
        raise RuntimeError("B5-16 authoritative platform state is invalid")
    if gate_count != 0 or task_count != 0:
        raise RuntimeError("B5-16 created an unexpected gate or Task")


def main() -> None:
    require_hidden_tauri_configuration()
    control_plane_port, database_port = isolated_ports()
    project_name = f"automation-tool-b516-{os.getpid()}"
    private_app_data = app_data_directory()
    if private_app_data.exists():
        raise RuntimeError("Refusing to reuse an existing B5-16 App data directory")
    prepare_startup_gate(private_app_data, executor_package=False)
    compose = compose_command(project_name)
    server: subprocess.Popen[bytes] | None = None
    app_process: subprocess.Popen[bytes] | None = None
    package_entrypoint: Path | None = None

    with tempfile.TemporaryDirectory(prefix=f"{project_name}-") as temporary:
        workspace = Path(temporary)
        page_state_path = workspace / "page-state"
        ready_path = workspace / "ready"
        release_path = workspace / "release"
        page_state_path.write_text("expired", encoding="ascii")
        page_state_path.chmod(0o600)
        environment, database_url = isolated_environment(
            control_plane_port=control_plane_port,
            database_port=database_port,
            page_state_path=page_state_path,
            ready_path=ready_path,
            release_path=release_path,
        )
        try:
            print("[B5-16] Building the separately signed acceptance Executor")
            package_source = build_signed_executor(
                workspace,
                build_id=EXECUTOR_BUILD_ID,
                spec_path=EXECUTOR_SPEC,
            )
            package_root = install_executor_package(package_source, private_app_data)
            package_entrypoint = executor_entrypoint(package_root)

            require_port_available(database_port)
            print(f"[B5-16] Starting isolated PostgreSQL as {project_name}")
            subprocess.run(
                [*compose, "up", "--detach", "--wait", "postgres-test"],
                check=True,
                cwd=REPOSITORY_ROOT,
                env=environment,
            )
            print("[B5-16] Applying the production Alembic migration chain")
            subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", "head"],
                check=True,
                cwd=BACKEND_ROOT,
                env=environment,
            )
            print(
                f"[B5-16] Starting Control Plane on isolated port {control_plane_port}"
            )
            server = start_control_plane(
                port=control_plane_port, environment=environment
            )

            print("[B5-16] Building and starting the hidden Tauri App")
            subprocess.run(
                ["pnpm", "build:tauri:default-profile-isolation-test"],
                check=True,
                cwd=FRONTEND_ROOT,
                env=environment,
                timeout=600,
            )
            app_process = subprocess.Popen(
                [
                    "pnpm",
                    "exec",
                    "wdio",
                    "run",
                    "wdio.default-profile-isolation.conf.ts",
                ],
                cwd=FRONTEND_ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            wait_for_ready(ready_path, app_process)
            audit_live_browser(private_app_data)
            print(
                "[B5-16] Live Chrome command-line and open-file isolation audit passed"
            )
            release_path.write_text("release", encoding="ascii")
            release_path.chmod(0o600)
            try:
                output_bytes, _ = app_process.communicate(timeout=180)
            except subprocess.TimeoutExpired as error:
                raise RuntimeError("B5-16 hidden App did not finish") from error
            output = output_bytes.decode("utf-8", errors="replace")
            print(sanitized_output(output, private_app_data), end="")
            if app_process.returncode != 0:
                raise RuntimeError("B5-16 hidden App production-path acceptance failed")
            app_process = None

            verify_local_session_state(private_app_data)
            asyncio.run(verify_database_state(database_url))
            require_no_residual_project_processes(private_app_data, package_entrypoint)
            print("[B5-16] Hidden-App default Profile isolation acceptance passed")
        finally:
            if app_process is not None:
                if not release_path.exists():
                    release_path.write_text("release", encoding="ascii")
                terminate_process(app_process)
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
