#!/usr/bin/env python3
"""Run H8-08 through a hidden App, one suspended Executor, and a headless window loss."""

from __future__ import annotations

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
from io import StringIO
from pathlib import Path
from typing import Any, cast

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
from run_h8_04_acceptance import require_app_process
from run_h8_06_acceptance import (
    collect_wdio,
    ensure_app_process_stopped,
    resume_executor,
    suspend_executor,
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
from run_t3_20_acceptance import start_control_plane, stop_control_plane

from automation_tool.executor.browser_runtime import (
    BrowserLaunchRequest,
    BrowserRuntime,
    BrowserRuntimeRejected,
    BrowserWindow,
)
from automation_tool.executor.diagnostics import ExecutorRecoveryDiagnostics

TAURI_CONFIG = FRONTEND_ROOT / "src-tauri" / "tauri.system-resume-e2e.conf.json"
CONTROL_PLANE_PORT = reserve_control_plane_port()
APP_IDENTIFIER = "com.aventador.automationtool.h808acceptance"
ENVIRONMENT_ID = "h808-acceptance"
EXECUTOR_BUILD_ID = "h8-08-system-resume"
EXECUTOR_LEDGER_FILE = "executor-ledger.sqlite3"


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
        raise RuntimeError("H8-08 requires one isolated visible=false App")


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


def isolated_environment(database_port: int) -> dict[str, str]:
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("AUTOMATION_TOOL_")
    }
    password = secrets.token_hex(24)
    database_url = (
        "postgresql+asyncpg://automation_tool_h808:"
        f"{password}@127.0.0.1:{database_port}/automation_tool_h808"
    )
    token, public_key = signed_bootstrap()
    environment.update(
        {
            "AUTOMATION_TOOL_DEV_DB_USER": "unused_h808_dev",
            "AUTOMATION_TOOL_DEV_DB_PASSWORD": secrets.token_hex(24),
            "AUTOMATION_TOOL_DEV_DB_NAME": "unused_h808_dev",
            "AUTOMATION_TOOL_DEV_DB_PORT": str(unused_loopback_port()),
            "AUTOMATION_TOOL_TEST_DB_USER": "automation_tool_h808",
            "AUTOMATION_TOOL_TEST_DB_PASSWORD": password,
            "AUTOMATION_TOOL_TEST_DB_NAME": "automation_tool_h808",
            "AUTOMATION_TOOL_TEST_DB_PORT": str(database_port),
            "AUTOMATION_TOOL_DATABASE_URL": database_url,
            "AUTOMATION_TOOL_DEMO_ENVIRONMENT_ID": ENVIRONMENT_ID,
            "AUTOMATION_TOOL_DEMO_BOOTSTRAP_PUBLIC_KEY": public_key,
            "AUTOMATION_TOOL_H808_BOOTSTRAP_TOKEN": token,
            "AUTOMATION_TOOL_H808_ENVIRONMENT_ID": ENVIRONMENT_ID,
            "AUTOMATION_TOOL_CONTROL_PLANE_E2E_ORIGIN": (f"http://127.0.0.1:{CONTROL_PLANE_PORT}"),
        }
    )
    return startup_gate_environment(
        environment, control_plane_port=CONTROL_PLANE_PORT
    )


def system_browser_executable() -> Path:
    candidates = (
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
    )
    if sys.platform == "win32":
        candidates = tuple(
            Path(root) / relative
            for root in filter(
                None,
                (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)")),
            )
            for relative in (
                "Google/Chrome/Application/chrome.exe",
                "Microsoft/Edge/Application/msedge.exe",
            )
        )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError("H8-08 requires an installed Chrome or Edge executable")


def verify_headless_window_recovery(workspace: Path) -> None:
    output = StringIO()
    diagnostics = ExecutorRecoveryDiagnostics(output)
    executable = system_browser_executable()
    first_profile = workspace / "headless-profile-before-resume"
    recovered_profile = workspace / "headless-profile-after-resume"
    first_profile.mkdir(mode=0o700)
    recovered_profile.mkdir(mode=0o700)
    runtime = BrowserRuntime(diagnostics=diagnostics)
    runtime.start(
        BrowserLaunchRequest(
            executable_path=executable,
            profile_directory=first_profile,
            headless=True,
        )
    )
    window = runtime.primary_window()
    page = cast(Any, window.playwright_page)
    page.set_content("<main data-h8-08='ready'>resume-safe</main>")
    page.context.close()
    try:
        runtime.primary_window()
    except BrowserRuntimeRejected:
        pass
    else:
        raise RuntimeError("H8-08 accepted a browser window after its context closed")
    with suppress(BrowserRuntimeRejected):
        runtime.close()

    recovered = BrowserRuntime(diagnostics=diagnostics)
    try:
        recovered.start(
            BrowserLaunchRequest(
                executable_path=executable,
                profile_directory=recovered_profile,
                headless=True,
            )
        )
        if not isinstance(recovered.primary_window(), BrowserWindow):
            raise RuntimeError("H8-08 did not recover a headless browser window")
    finally:
        recovered.close()
    if output.getvalue().splitlines() != [
        "executor.recovery browser_window_unavailable",
        "executor.recovery browser_window_recovered",
    ]:
        raise RuntimeError("H8-08 browser recovery diagnostics are not exact")


def wait_for_transport_online(ledger_path: Path, package_entrypoint: Path, process_id: int) -> None:
    deadline = time.monotonic() + 30
    while True:
        if not any(
            candidate_id == process_id
            for candidate_id, _command in matching_executor_processes(package_entrypoint)
        ):
            raise RuntimeError("H8-08 signed Executor changed during resume")
        if ledger_path.is_file():
            with closing(sqlite3.connect(ledger_path)) as connection:
                gate = connection.execute(
                    "SELECT network_connected FROM executor_action_guard WHERE singleton_id = 1"
                ).fetchone()
                command_count = connection.execute(
                    "SELECT COUNT(*) FROM executor_commands"
                ).fetchone()
                outbox_count = connection.execute("SELECT COUNT(*) FROM executor_outbox").fetchone()
            if gate == (1,) and command_count == (0,) and outbox_count == (0,):
                return
        if time.monotonic() >= deadline:
            raise RuntimeError("H8-08 transport did not recover with an empty durable ledger")
        time.sleep(0.05)


def main() -> None:
    require_control_plane_port_available()
    require_hidden_tauri_configuration()
    private_app_data = app_data_directory()
    if private_app_data.exists():
        raise RuntimeError("Refusing to reuse an existing H8-08 App data directory")
    prepare_startup_gate(private_app_data, executor_package=False)

    project_name = f"automation-tool-h808-{os.getpid()}"
    database_port = unused_loopback_port()
    environment = isolated_environment(database_port)
    compose = compose_command(project_name)
    server: subprocess.Popen[bytes] | None = None
    app: subprocess.Popen[bytes] | None = None
    package_entrypoint: Path | None = None
    executor_process_id: int | None = None
    executor_suspended = False
    app_process_id: int | None = None

    with tempfile.TemporaryDirectory(prefix=f"{project_name}-") as temporary:
        workspace = Path(temporary).resolve()
        signals = {
            name: workspace / f"{name}.json"
            for name in (
                "executor-ready",
                "executor-resumed",
                "diagnostics-observed",
                "facts-verified",
            )
        }
        environment.update(
            {
                "AUTOMATION_TOOL_H808_EXECUTOR_READY_SIGNAL": os.fspath(signals["executor-ready"]),
                "AUTOMATION_TOOL_H808_EXECUTOR_RESUMED_SIGNAL": os.fspath(
                    signals["executor-resumed"]
                ),
                "AUTOMATION_TOOL_H808_DIAGNOSTICS_OBSERVED_SIGNAL": os.fspath(
                    signals["diagnostics-observed"]
                ),
                "AUTOMATION_TOOL_H808_FACTS_VERIFIED_SIGNAL": os.fspath(signals["facts-verified"]),
            }
        )
        try:
            print("[H8-08] Verifying real headless browser window loss and recovery")
            verify_headless_window_recovery(workspace)
            print("[H8-08] Building and signing the real PyInstaller Executor")
            package_source = build_signed_executor(workspace, build_id=EXECUTOR_BUILD_ID)
            package_root = install_executor_package(package_source)
            package_entrypoint = executor_entrypoint(package_root)

            print("[H8-08] Building the dedicated hidden Tauri App")
            subprocess.run(
                ["pnpm", "build:tauri:system-resume-test"],
                check=True,
                cwd=FRONTEND_ROOT,
                env=environment,
            )
            print(f"[H8-08] Starting isolated PostgreSQL as {project_name}")
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
                ["pnpm", "exec", "wdio", "run", "wdio.system-resume.conf.ts"],
                cwd=FRONTEND_ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            ready = wait_for_signal(signals["executor-ready"], app, label="the running Executor")
            app_process_id_value = ready.get("appProcessId")
            if not isinstance(app_process_id_value, int):
                raise RuntimeError("H8-08 App did not report its process identity")
            app_process_id = app_process_id_value
            require_app_process(app_process_id)
            processes = matching_executor_processes(package_entrypoint)
            if len(processes) != 1:
                raise RuntimeError("H8-08 did not start exactly one signed Executor")
            executor_process_id = processes[0][0]

            print("[H8-08] Suspending only the signed Executor for one bounded resume gap")
            suspend_executor(executor_process_id)
            executor_suspended = True
            time.sleep(6.25)
            resume_executor(executor_process_id)
            executor_suspended = False
            signals["executor-resumed"].write_text("{}\n", encoding="utf-8")
            try:
                observed = wait_for_signal(
                    signals["diagnostics-observed"],
                    app,
                    label="the App-observed fixed recovery diagnostics",
                )
            except RuntimeError:
                if app.stdout is not None and app.poll() is not None:
                    print(app.stdout.read().decode("utf-8", errors="replace"), end="")
                raise
            if observed.get("restartCount") != 0:
                raise RuntimeError("H8-08 Rust supervisor restarted the suspended Executor")
            wait_for_transport_online(
                private_app_data / "local-executor" / "state" / EXECUTOR_LEDGER_FILE,
                package_entrypoint,
                executor_process_id,
            )
            if matching_executor_processes(package_entrypoint) != processes:
                raise RuntimeError("H8-08 replaced the signed Executor after resume")
            signals["facts-verified"].write_text("{}\n", encoding="utf-8")
            exit_code, output = collect_wdio(app, timeout=60)
            app = None
            ensure_app_process_stopped(app_process_id)
            if exit_code != 0:
                print(output, end="")
                raise RuntimeError("H8-08 hidden App acceptance failed")
            print("[H8-08] Hidden-App system-resume recovery passed")
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
