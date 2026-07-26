#!/usr/bin/env python3
"""Run H8-16E through one isolated hidden Tauri App and its formal startup gate."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from desktop_e2e_prerequisites import (
    prepare_startup_gate,
    terminate_app_process_tree,
)
from run_e4_07_acceptance import build_signed_executor, start_control_plane
from run_e4_14_acceptance import (
    assert_no_executor_process,
    executor_entrypoint,
    install_executor_package,
    pnpm_executable,
    terminate_executor_processes,
)
from run_i2_13_acceptance import require_port_closed, unused_loopback_port

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_ROOT = REPOSITORY_ROOT / "frontend"
TAURI_CONFIG = FRONTEND_ROOT / "src-tauri" / "tauri.startup-environment-e2e.conf.json"
APP_IDENTIFIER = "com.aventador.automationtool.h816eacceptance"
ACTION_AUTHORIZATION_PUBLIC_KEY = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"


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


def require_hidden_configuration() -> None:
    configuration = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))
    windows = configuration.get("app", {}).get("windows", [])
    if (
        configuration.get("identifier") != APP_IDENTIFIER
        or len(windows) != 1
        or windows[0].get("visible") is not False
    ):
        raise RuntimeError("H8-16E acceptance must use its hidden isolated App")


def isolated_environment(
    control_plane_port: int, webdriver_port: int
) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("AUTOMATION_TOOL_")
    }
    environment.update(
        {
            "AUTOMATION_TOOL_CONTROL_PLANE_E2E_ORIGIN": (
                f"http://127.0.0.1:{control_plane_port}"
            ),
            "AUTOMATION_TOOL_ACTION_AUTHORIZATION_PUBLIC_KEY": (
                ACTION_AUTHORIZATION_PUBLIC_KEY
            ),
            "AUTOMATION_TOOL_LOCAL_ACTION_MINIMUM_INTERVAL_SECONDS": "30",
            "AUTOMATION_TOOL_LOCAL_ACTION_TASK_LIMIT": "20",
            "TAURI_WEBDRIVER_PORT": str(webdriver_port),
        }
    )
    return environment


def main() -> None:
    require_hidden_configuration()
    private_app_data = app_data_directory()
    if private_app_data.exists():
        raise RuntimeError("Refusing to reuse an existing H8-16E App data directory")
    prepare_startup_gate(private_app_data, embedded_browser=False, executor_package=False)
    webdriver_port = unused_loopback_port()
    control_plane = start_control_plane()
    require_port_closed(webdriver_port)
    environment = isolated_environment(control_plane.port, webdriver_port)
    app_process: subprocess.Popen[bytes] | None = None
    package_entrypoint: Path | None = None

    with tempfile.TemporaryDirectory(
        prefix=f"automation-tool-h816e-{os.getpid()}-"
    ) as temporary:
        try:
            print("[H8-16E] Building the real signed PyInstaller Executor")
            package_source = build_signed_executor(
                Path(temporary),
                build_id="h8-16e-startup-environment",
            )
            package_root = install_executor_package(package_source, private_app_data)
            package_entrypoint = executor_entrypoint(package_root)

            print("[H8-16E] Running one real Tauri App with visible=false")
            app_process = subprocess.Popen(
                [pnpm_executable(), "test:h8-16e-app"],
                cwd=FRONTEND_ROOT,
                env=environment,
                start_new_session=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            try:
                output_bytes, _ = app_process.communicate(timeout=360)
            except subprocess.TimeoutExpired as error:
                raise RuntimeError(
                    "H8-16E hidden App acceptance did not finish"
                ) from error
            output = output_bytes.decode("utf-8", errors="replace")
            print(output, end="")
            if app_process.returncode != 0:
                raise RuntimeError("H8-16E hidden App startup acceptance failed")
            app_process = None
            assert_no_executor_process(package_entrypoint)
            print("[H8-16E] Hidden-App startup environment acceptance passed")
        finally:
            if app_process is not None:
                terminate_app_process_tree(app_process)
            if package_entrypoint is not None:
                terminate_executor_processes(package_entrypoint)
            control_plane.stop()
            restore = subprocess.run(
                [pnpm_executable(), "build"],
                cwd=FRONTEND_ROOT,
                env=environment,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if private_app_data.exists():
                shutil.rmtree(private_app_data)
            require_port_closed(control_plane.port)
            require_port_closed(webdriver_port)
            if restore.returncode != 0:
                raise RuntimeError("H8-16E failed to restore production Vite assets")


if __name__ == "__main__":
    main()
