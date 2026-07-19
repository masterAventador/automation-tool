#!/usr/bin/env python3
"""Run B5-04 through one isolated hidden Tauri App."""

from __future__ import annotations

import json
import os
import shutil
import socket
import stat
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_ROOT = REPOSITORY_ROOT / "frontend"
TAURI_CONFIG = FRONTEND_ROOT / "src-tauri" / "tauri.browser-settings-e2e.conf.json"
APP_IDENTIFIER = "com.aventador.automationtool.b504acceptance"
SETTINGS_FILE = "browser-selection-v1"
CANONICAL_SELECTIONS = {
    b'{"browser":"google_chrome","version":1}',
    b'{"browser":"microsoft_edge","version":1}',
}


def pnpm_executable() -> str:
    name = "pnpm.cmd" if sys.platform == "win32" else "pnpm"
    executable = shutil.which(name)
    if executable is None:
        raise RuntimeError(f"B5-04 cannot find {name} on PATH")
    return executable


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
        raise RuntimeError("B5-04 acceptance must use its hidden isolated App")


def unused_loopback_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    require_port_closed(port)
    return port


def require_port_closed(port: int) -> None:
    with socket.socket() as probe:
        probe.settimeout(0.2)
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            raise RuntimeError(f"B5-04 refuses to reuse occupied loopback port {port}")


def isolated_environment(port: int) -> dict[str, str]:
    environment = {key: value for key, value in os.environ.items() if key != "TAURI_WEBDRIVER_PORT"}
    environment["TAURI_WEBDRIVER_PORT"] = str(port)
    return environment


def verify_persisted_selection(private_app_data: Path) -> None:
    settings_directory = private_app_data / "settings"
    settings_path = settings_directory / SETTINGS_FILE
    document = settings_path.read_bytes()
    if document not in CANONICAL_SELECTIONS:
        raise RuntimeError("B5-04 persisted a non-canonical browser selection")
    if any(token in document for token in (b"/Applications", b"Program Files", b".exe")):
        raise RuntimeError("B5-04 persisted a browser path")
    if os.name == "posix":
        if stat.S_IMODE(settings_directory.stat().st_mode) != 0o700:
            raise RuntimeError("B5-04 settings directory is not private")
        if stat.S_IMODE(settings_path.stat().st_mode) != 0o600:
            raise RuntimeError("B5-04 settings file is not private")


def run() -> None:
    require_hidden_configuration()
    private_app_data = app_data_directory()
    if private_app_data.exists():
        shutil.rmtree(private_app_data)
    port = unused_loopback_port()
    environment = isolated_environment(port)
    try:
        subprocess.run(
            [pnpm_executable(), "build:tauri:browser-settings-test"],
            cwd=FRONTEND_ROOT,
            env=environment,
            check=True,
        )
        require_port_closed(port)
        subprocess.run(
            [pnpm_executable(), "exec", "wdio", "run", "wdio.browser-settings.conf.ts"],
            cwd=FRONTEND_ROOT,
            env=environment,
            check=True,
        )
        require_port_closed(port)
        verify_persisted_selection(private_app_data)
    finally:
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
        require_port_closed(port)
        if restore.returncode != 0:
            raise RuntimeError("B5-04 failed to restore production Vite assets")


if __name__ == "__main__":
    run()
