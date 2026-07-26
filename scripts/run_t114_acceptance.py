#!/usr/bin/env python3
"""Run T114: a killed App leaves the operations profile leased, and the real UI
must say the one thing that clears it.

The path this drives is the one a user reaches without doing anything unusual:
a login that stopped at "go and scan the code" keeps the operations profile
leased on purpose (`executor_platform.rs` releases only on `healthy`), so any
ungraceful exit while that browser is open leaves `{"state":"active"}` in the
profile lock. Every later press of either platform button then fails forever.

Nothing here needs a Douyin account. The login page facts come from the same
separately signed acceptance Executor B5-15 uses, while the App, the Rust
bridge, the Tauri commands, the embedded Chromium, the profile store and the
lock file all stay real -- which is what makes a failure here point at the
product instead of at the fixture.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

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
from run_h8_04_acceptance import (
    collect_wdio,
    hard_kill_app,
    wait_for_process_exit,
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
    FRONTEND_ROOT / "src-tauri" / "tauri.platform-session-recovery-e2e.conf.json"
)
EXECUTOR_SPEC = BACKEND_ROOT / "tests/fixtures/automation-tool-executor-b515.spec"
APP_IDENTIFIER = "com.aventador.automationtool.t114acceptance"
ENVIRONMENT_ID = "t114-acceptance"
EXECUTOR_BUILD_ID = "t114-platform-session-recovery"
PROFILE_LOCK_FILE = ".automation-tool-profile-lock-v1"
ACTIVE_MARKER = b'{"state":"active","version":1}'


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
        raise RuntimeError("T114 Tauri acceptance must use its hidden isolated App")


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
    workspace: Path,
) -> tuple[dict[str, str], str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("AUTOMATION_TOOL_")
    }
    database_password = secrets.token_hex(24)
    database_url = (
        "postgresql+asyncpg://automation_tool_t114:"
        f"{database_password}@127.0.0.1:{database_port}/automation_tool_t114"
    )
    bootstrap_token, bootstrap_public_key = signed_bootstrap()
    environment.update(
        {
            "AUTOMATION_TOOL_DEV_DB_USER": "unused_t114_dev",
            "AUTOMATION_TOOL_DEV_DB_PASSWORD": secrets.token_hex(24),
            "AUTOMATION_TOOL_DEV_DB_NAME": "unused_t114_dev",
            "AUTOMATION_TOOL_DEV_DB_PORT": str(unused_loopback_port()),
            "AUTOMATION_TOOL_TEST_DB_USER": "automation_tool_t114",
            "AUTOMATION_TOOL_TEST_DB_PASSWORD": database_password,
            "AUTOMATION_TOOL_TEST_DB_NAME": "automation_tool_t114",
            "AUTOMATION_TOOL_TEST_DB_PORT": str(database_port),
            "AUTOMATION_TOOL_DATABASE_URL": database_url,
            "AUTOMATION_TOOL_DEMO_ENVIRONMENT_ID": ENVIRONMENT_ID,
            "AUTOMATION_TOOL_DEMO_BOOTSTRAP_PUBLIC_KEY": bootstrap_public_key,
            # The acceptance Executor and the Installation-registration command
            # are shared with B5-15 and read these fixed names.
            "AUTOMATION_TOOL_B515_BOOTSTRAP_TOKEN": bootstrap_token,
            "AUTOMATION_TOOL_B515_ENVIRONMENT_ID": ENVIRONMENT_ID,
            "AUTOMATION_TOOL_B515_PAGE_STATE": os.fspath(page_state_path),
            "AUTOMATION_TOOL_T114_LEASE_HELD_SIGNAL": os.fspath(
                workspace / "lease-held.json"
            ),
            "AUTOMATION_TOOL_T114_RECOVERED_SIGNAL": os.fspath(
                workspace / "recovered.json"
            ),
            "AUTOMATION_TOOL_CONTROL_PLANE_E2E_ORIGIN": (
                f"http://127.0.0.1:{control_plane_port}"
            ),
        }
    )
    return (
        startup_gate_environment(environment, control_plane_port=control_plane_port),
        database_url,
    )


def start_wdio(environment: dict[str, str], *, phase: str) -> subprocess.Popen[bytes]:
    phased = dict(environment)
    phased["AUTOMATION_TOOL_T114_PHASE"] = phase
    return subprocess.Popen(
        ["pnpm", "exec", "wdio", "run", "wdio.platform-session-recovery.conf.ts"],
        cwd=FRONTEND_ROOT,
        env=phased,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def wait_for_signal(
    path: Path, process: subprocess.Popen[bytes], *, label: str
) -> dict[str, object]:
    deadline = time.monotonic() + 420
    while time.monotonic() < deadline:
        if path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise RuntimeError(f"T114 signal for {label} is malformed")
            return payload
        if process.poll() is not None:
            _, output = collect_wdio(process, timeout=10)
            print(output, end="")
            raise RuntimeError(f"T114 App exited before publishing {label}")
        time.sleep(0.1)
    raise RuntimeError(f"T114 never observed {label}")


def current_profile_directory(private_app_data: Path) -> Path:
    profile_root = private_app_data / OPERATIONS_PROFILE_ROOT
    profile_id = (profile_root / CURRENT_DOUYIN_PROFILE_FILE).read_text(encoding="ascii")
    parsed = UUID(profile_id)
    if parsed.version != 4 or str(parsed) != profile_id:
        raise RuntimeError("T114 current Profile identity is invalid")
    directory = profile_root / "douyin" / profile_id
    if not directory.is_dir() or directory.is_symlink():
        raise RuntimeError("T114 current Profile directory is invalid")
    return directory


def require_abandoned_lease(profile_directory: Path) -> None:
    """The killed App must have left the marker the product itself writes.

    Seeding this by hand would prove nothing: the point is that the shipping
    build produces this state on an ordinary ungraceful exit.
    """
    lock_path = profile_directory / PROFILE_LOCK_FILE
    if not lock_path.is_file():
        raise RuntimeError("T114 killed App left no profile lock at all")
    marker = lock_path.read_bytes()
    if marker != ACTIVE_MARKER:
        raise RuntimeError(
            "T114 expected the killed App to leave an active lease marker, "
            f"found {marker!r}"
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
                    text("select platform, state from platform_session_health")
                )
            ).all()
            task_count = await connection.scalar(text("select count(*) from tasks"))
    finally:
        await engine.dispose()
    if installation_count != 1:
        raise RuntimeError("T114 acceptance did not retain exactly one Installation")
    if rows != [("douyin", "missing")]:
        raise RuntimeError(
            f"T114 safe logout did not publish a missing Session: {rows}"
        )
    if task_count != 0:
        raise RuntimeError("T114 created an unexpected Task")


def main() -> None:
    require_hidden_tauri_configuration()
    control_plane_port, database_port = isolated_ports()
    project_name = f"automation-tool-t114-{os.getpid()}"
    private_app_data = app_data_directory()
    if private_app_data.exists():
        raise RuntimeError("Refusing to reuse an existing T114 App data directory")
    prepare_startup_gate(private_app_data, executor_package=False)
    compose = compose_command(project_name)
    server: subprocess.Popen[bytes] | None = None
    package_entrypoint: Path | None = None
    first_app: subprocess.Popen[bytes] | None = None
    second_app: subprocess.Popen[bytes] | None = None

    with tempfile.TemporaryDirectory(prefix=f"{project_name}-") as temporary:
        workspace = Path(temporary)
        page_state_path = workspace / "page-state"
        # `expired` renders the scan-code page, so the login settles on
        # `awaiting_scan` and the App deliberately keeps the profile leased.
        page_state_path.write_text("expired", encoding="ascii")
        page_state_path.chmod(0o600)
        environment, database_url = isolated_environment(
            control_plane_port=control_plane_port,
            database_port=database_port,
            page_state_path=page_state_path,
            workspace=workspace,
        )
        lease_held_signal = Path(environment["AUTOMATION_TOOL_T114_LEASE_HELD_SIGNAL"])
        recovered_signal = Path(environment["AUTOMATION_TOOL_T114_RECOVERED_SIGNAL"])
        try:
            print("[T114] Building the separately signed acceptance Executor")
            package_source = build_signed_executor(
                workspace,
                build_id=EXECUTOR_BUILD_ID,
                spec_path=EXECUTOR_SPEC,
            )
            package_root = install_executor_package(package_source)
            package_entrypoint = executor_entrypoint(package_root)

            require_port_available(database_port)
            print(f"[T114] Starting isolated PostgreSQL as {project_name}")
            subprocess.run(
                [*compose, "up", "--detach", "--wait", "postgres-test"],
                check=True,
                cwd=REPOSITORY_ROOT,
                env=environment,
            )
            print("[T114] Applying the production Alembic migration chain")
            subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", "head"],
                check=True,
                cwd=BACKEND_ROOT,
                env=environment,
            )
            print(f"[T114] Starting Control Plane on port {control_plane_port}")
            server = start_control_plane(
                port=control_plane_port, environment=environment
            )

            print("[T114] Building one hidden Tauri App for both phases")
            subprocess.run(
                ["pnpm", "build:tauri:platform-session-recovery-test"],
                check=True,
                cwd=FRONTEND_ROOT,
                env=environment,
                timeout=900,
            )

            print("[T114] Phase 1: leaving the operations profile leased")
            first_app = start_wdio(environment, phase="abandon")
            held = wait_for_signal(
                lease_held_signal, first_app, label="the held profile lease"
            )
            app_process_id = held.get("appProcessId")
            if not isinstance(app_process_id, int):
                raise RuntimeError("T114 first App omitted its process id")
            profile_directory = current_profile_directory(private_app_data)

            print(f"[T114] Hard-killing the App holding the lease ({app_process_id})")
            hard_kill_app(app_process_id)
            wait_for_process_exit(app_process_id)
            first_exit, first_output = collect_wdio(first_app, timeout=60)
            first_app = None
            print(first_output, end="")
            if first_exit == 0:
                raise RuntimeError("T114 first App exited normally instead of crashing")

            require_abandoned_lease(profile_directory)
            print("[T114] Confirmed the killed App left an active lease marker")
            terminate_project_processes(private_app_data, package_entrypoint)
            require_no_residual_project_processes(private_app_data, package_entrypoint)

            print("[T114] Phase 2: the restarted App must name the recovery")
            second_app = start_wdio(environment, phase="blocked")
            wait_for_signal(
                recovered_signal, second_app, label="the recovered platform page"
            )
            second_exit, second_output = collect_wdio(second_app, timeout=120)
            second_app = None
            print(second_output, end="")
            if second_exit != 0:
                raise RuntimeError("T114 restarted App phase failed")

            if profile_directory.exists():
                raise RuntimeError("T114 safe logout left the abandoned Profile behind")
            require_no_residual_project_processes(private_app_data, package_entrypoint)
            asyncio.run(verify_database_state(database_url))
            print("[T114] Abandoned-lease blocking and safe-logout recovery passed")
        finally:
            for pending in (first_app, second_app):
                if pending is not None:
                    terminate_process(pending)
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
