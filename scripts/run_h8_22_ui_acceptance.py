#!/usr/bin/env python3
"""Run H8-22 update controls through a hidden production App UI."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import uvicorn

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_ROOT = REPOSITORY_ROOT / "frontend"
TAURI_CONFIG = FRONTEND_ROOT / "src-tauri" / "tauri.update-ui-e2e.conf.json"
ACCEPTANCE_ASSETS = FRONTEND_ROOT / "dist-h822-ui"
APP_IDENTIFIER = "com.aventador.automationtool.h822uiacceptance"

sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))
from run_h8_20_acceptance import (  # noqa: E402
    pnpm_executable,
    require_port_closed,
    unused_loopback_port,
    wait_for_port,
    write_tls_identity,
)
from run_h8_21_acceptance import (  # noqa: E402
    build_update_app,
    isolated_environment,
    verify_cache,
)


def app_data_directory() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_IDENTIFIER
    if sys.platform == "win32":
        roaming = os.environ.get("APPDATA")
        if not roaming:
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
        or configuration.get("build", {}).get("frontendDist") != "../dist-h822-ui"
        or len(windows) != 1
        or windows[0].get("visible") is not False
    ):
        raise RuntimeError("H8-22 UI acceptance must use its hidden isolated App")


def run_hidden_app(
    environment: dict[str, str], scenario: str, webdriver_port: int
) -> None:
    require_port_closed(webdriver_port)
    run_environment = dict(environment)
    run_environment.pop("H821_SCENARIO", None)
    run_environment["H822_UI_SCENARIO"] = scenario
    subprocess.run(
        [pnpm_executable(), "exec", "wdio", "run", "wdio.update-ui.conf.ts"],
        cwd=FRONTEND_ROOT,
        env=run_environment,
        check=True,
    )
    require_port_closed(webdriver_port)


def run() -> None:
    require_hidden_configuration()
    private_app_data = app_data_directory()
    if private_app_data.exists():
        shutil.rmtree(private_app_data)
    if ACCEPTANCE_ASSETS.exists():
        shutil.rmtree(ACCEPTANCE_ASSETS)
    update_port = unused_loopback_port()
    webdriver_port = unused_loopback_port()
    if update_port == webdriver_port:
        raise RuntimeError("H8-22 requires isolated update and WebDriver ports")
    mode: dict[str, object] = {"scenario": "optional", "feed_count": 0}
    feed_ledger: list[dict[str, str]] = []
    artifact_ledger: list[str] = []
    server: uvicorn.Server | None = None
    server_thread: threading.Thread | None = None
    with tempfile.TemporaryDirectory(prefix="automation-tool-h822-ui-") as temporary:
        certificate_path, key_path = write_tls_identity(Path(temporary))
        app = build_update_app(update_port, mode, feed_ledger, artifact_ledger)
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=update_port,
                ssl_certfile=str(certificate_path),
                ssl_keyfile=str(key_path),
                access_log=False,
                log_level="critical",
            )
        )
        server_thread = threading.Thread(
            target=server.run, name="automation-tool-h822-ui", daemon=True
        )
        server_thread.start()
        wait_for_port(update_port)
        environment = isolated_environment(update_port, webdriver_port)
        try:
            subprocess.run(
                [pnpm_executable(), "build:tauri:update-ui-test"],
                cwd=FRONTEND_ROOT,
                env=environment,
                check=True,
            )
            run_hidden_app(environment, "optional", webdriver_port)
            verify_cache(private_app_data, "0.3.0")
            if artifact_ledger != ["0.2.0", "0.3.0"]:
                raise RuntimeError(
                    "H8-22 UI did not atomically replace the optional package"
                )

            if private_app_data.exists():
                shutil.rmtree(private_app_data)
            artifact_ledger.clear()
            mode.update({"scenario": "forced", "feed_count": 0})
            run_hidden_app(environment, "forced", webdriver_port)
            verify_cache(private_app_data, "0.2.0")
            if artifact_ledger != ["0.2.0"]:
                raise RuntimeError(
                    "H8-22 forced UI downloaded an unexpected package count"
                )
        finally:
            if server is not None:
                server.should_exit = True
            if server_thread is not None:
                server_thread.join(timeout=10)
                if server_thread.is_alive():
                    raise RuntimeError("H8-22 update server did not stop")
            if private_app_data.exists():
                shutil.rmtree(private_app_data)
            if ACCEPTANCE_ASSETS.exists():
                shutil.rmtree(ACCEPTANCE_ASSETS)
            require_port_closed(update_port)
            require_port_closed(webdriver_port)
    optional_feeds = [entry for entry in feed_ledger if entry["scenario"] == "optional"]
    forced_feeds = [entry for entry in feed_ledger if entry["scenario"] == "forced"]
    if len(optional_feeds) != 4 or len(forced_feeds) != 1:
        raise RuntimeError("H8-22 UI did not use the expected production feed calls")
    print(
        "Hidden App update settings, optional decisions, overwrite and forced UI passed"
    )


if __name__ == "__main__":
    run()
