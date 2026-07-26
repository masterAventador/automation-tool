#!/usr/bin/env python3
"""Run the update decision and installation acceptance (H8-21 and H8-22).

The three scenarios below used to be driven twice: once by a spec that invoked
`decide_app_update` over IPC, and once by `update-ui.spec.ts`, which clicks the
buttons a user clicks. Same runner, same App build, same feed, same assertions
about the cache and the artifact ledger — only the way the decision was made
differed, and a direct Command call is layered evidence rather than acceptance.
The clicking spec is therefore the only one left, and it answers for both tasks.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import Request
from fastapi.responses import Response

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
FRONTEND_ROOT = REPOSITORY_ROOT / "frontend"
TAURI_CONFIG = FRONTEND_ROOT / "src-tauri" / "tauri.update-installation-e2e.conf.json"
ACCEPTANCE_ASSETS = FRONTEND_ROOT / "dist-h821"
APP_IDENTIFIER = "com.aventador.automationtool.h821acceptance"

sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))
from desktop_e2e_prerequisites import desktop_e2e_startup_harness  # noqa: E402
from run_h8_20_acceptance import (  # noqa: E402
    PAYLOAD,
    PUBLIC_KEY_TEXT,
    SIGNATURE_TEXT,
    current_update_platform,
    pnpm_executable,
    require_port_closed,
    unused_loopback_port,
    wait_for_port,
    write_tls_identity,
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
        or len(windows) != 1
        or windows[0].get("visible") is not False
    ):
        raise RuntimeError("H8-21 acceptance must use its hidden isolated App")


def build_update_app(
    port: int,
    mode: dict[str, object],
    feed_ledger: list[dict[str, str]],
    artifact_ledger: list[str],
) -> Any:
    sys.path.insert(0, str(BACKEND_ROOT / "src"))
    from automation_tool.control_plane.application.desktop_updates import (
        DesktopUpdateCatalog,
    )
    from automation_tool.control_plane.bootstrap.app import create_app

    target, arch = current_update_platform()
    signature = base64.b64encode(SIGNATURE_TEXT.encode()).decode()

    def catalog(version: str, policy: str) -> DesktopUpdateCatalog:
        return DesktopUpdateCatalog.from_documents(
            [
                {
                    "version": version,
                    "channel": "stable",
                    "policy": policy,
                    "target": target,
                    "arch": arch,
                    "url": f"https://127.0.0.1:{port}/h821-artifact/{version}",
                    "signature": signature,
                    "sha256": hashlib.sha256(PAYLOAD).hexdigest(),
                    "sizeBytes": len(PAYLOAD),
                    "notes": "H8-21 isolated acceptance release",
                    "publishedAt": "2026-07-22T00:00:00Z",
                }
            ]
        )

    optional_02 = catalog("0.2.0", "optional")
    optional_03 = catalog("0.3.0", "optional")
    forced_02 = catalog("0.2.0", "forced")
    app = create_app(database=None, desktop_update_catalog=optional_02)

    @app.middleware("http")
    async def select_production_catalog(request: Request, call_next: Any) -> Response:
        if request.url.path.startswith("/desktop-updates/v1/"):
            scenario = str(mode["scenario"])
            count = int(mode["feed_count"]) + 1
            mode["feed_count"] = count
            if scenario == "optional":
                selected = optional_03 if count >= 4 else optional_02
                version = "0.3.0" if count >= 4 else "0.2.0"
                policy = "optional"
            else:
                selected = forced_02
                version = "0.2.0"
                policy = "forced"
            request.app.state.desktop_update_catalog = selected
            feed_ledger.append(
                {"scenario": scenario, "version": version, "policy": policy}
            )
        return await call_next(request)

    async def artifact(version: str) -> Response:
        artifact_ledger.append(version)
        return Response(
            PAYLOAD,
            headers={"cache-control": "no-store", "content-length": str(len(PAYLOAD))},
            media_type="application/octet-stream",
        )

    app.add_api_route(
        "/h821-artifact/{version}", artifact, methods=["GET"], include_in_schema=False
    )
    return app


def isolated_environment(update_port: int, webdriver_port: int) -> dict[str, str]:
    environment = dict(os.environ)
    for name in (
        "AUTOMATION_TOOL_UPDATE_ENDPOINT",
        "AUTOMATION_TOOL_UPDATE_PUBLIC_KEY",
        "AUTOMATION_TOOL_UPDATE_ACCEPT_INVALID_TLS",
        "AUTOMATION_TOOL_UPDATE_INSTALL_PROBE",
        "TAURI_WEBDRIVER_PORT",
        "H821_SCENARIO",
    ):
        environment.pop(name, None)
    environment["AUTOMATION_TOOL_UPDATE_ENDPOINT"] = (
        f"https://127.0.0.1:{update_port}/desktop-updates/v1/stable/"
        "{{target}}/{{arch}}/{{current_version}}"
    )
    environment["AUTOMATION_TOOL_UPDATE_PUBLIC_KEY"] = base64.b64encode(
        PUBLIC_KEY_TEXT.encode()
    ).decode()
    environment["AUTOMATION_TOOL_UPDATE_ACCEPT_INVALID_TLS"] = "1"
    environment["AUTOMATION_TOOL_UPDATE_INSTALL_PROBE"] = "1"
    environment["TAURI_WEBDRIVER_PORT"] = str(webdriver_port)
    return environment


def run_hidden_app(
    environment: dict[str, str],
    scenario: str,
    webdriver_port: int,
) -> None:
    require_port_closed(webdriver_port)
    run_environment = {**environment, "H821_SCENARIO": scenario}
    subprocess.run(
        [pnpm_executable(), "exec", "wdio", "run", "wdio.update-ui.conf.ts"],
        cwd=FRONTEND_ROOT,
        env=run_environment,
        check=True,
    )
    require_port_closed(webdriver_port)


def verify_cache(private_app_data: Path, expected_version: str) -> None:
    cache_directory = private_app_data / "app-updates" / "cache-v1"
    entries = sorted(path.name for path in cache_directory.iterdir())
    if entries != ["cache-manifest-v1", "candidate.package"]:
        raise RuntimeError("H8-21 did not preserve exactly one verified package")
    if (cache_directory / "candidate.package").read_bytes() != PAYLOAD:
        raise RuntimeError("H8-21 installation did not consume the signed cached bytes")
    raw_manifest = (cache_directory / "cache-manifest-v1").read_bytes()
    manifest = json.loads(raw_manifest)
    if manifest.get("version") != expected_version:
        raise RuntimeError("H8-21 cached version does not match the accepted release")
    if any(
        token in raw_manifest.lower()
        for token in (b"url", b"signature", b"http", b"path")
    ):
        raise RuntimeError("H8-21 persisted updater transport or install paths")
    if os.name == "posix":
        if stat.S_IMODE(cache_directory.stat().st_mode) != 0o700:
            raise RuntimeError("H8-21 cache directory is not private")
        for name in entries:
            if stat.S_IMODE((cache_directory / name).stat().st_mode) != 0o600:
                raise RuntimeError("H8-21 cache file is not private")


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
        raise RuntimeError("H8-21 requires isolated update and WebDriver ports")
    mode: dict[str, object] = {"scenario": "optional", "feed_count": 0}
    feed_ledger: list[dict[str, str]] = []
    artifact_ledger: list[str] = []
    server: uvicorn.Server | None = None
    server_thread: threading.Thread | None = None
    with tempfile.TemporaryDirectory(prefix="automation-tool-h821-") as temporary:
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
            target=server.run, name="automation-tool-h821", daemon=True
        )
        server_thread.start()
        wait_for_port(update_port)
        # All three scenarios run on the one App this harness prepares: the
        # startup gate is checked on every launch, so the forced-reopen scenario
        # needs the same prerequisites standing as the first one.
        with desktop_e2e_startup_harness(
            private_app_data,
            environment=isolated_environment(update_port, webdriver_port),
        ) as environment:
            try:
                subprocess.run(
                    [pnpm_executable(), "build:tauri:update-installation-test"],
                    cwd=FRONTEND_ROOT,
                    env=environment,
                    check=True,
                )
                run_hidden_app(environment, "optional", webdriver_port)
                verify_cache(private_app_data, "0.3.0")
                if artifact_ledger != ["0.2.0", "0.3.0"]:
                    raise RuntimeError(
                        "H8-21 optional decisions did not replace the cached version"
                    )

                shutil.rmtree(private_app_data)
                artifact_ledger.clear()
                mode.update({"scenario": "forced", "feed_count": 0})
                run_hidden_app(environment, "forced-first", webdriver_port)
                verify_cache(private_app_data, "0.2.0")
                if artifact_ledger != ["0.2.0"]:
                    raise RuntimeError(
                        "H8-21 forced first launch did not download exactly once"
                    )
                run_hidden_app(environment, "forced-reopen", webdriver_port)
                if artifact_ledger != ["0.2.0"]:
                    raise RuntimeError(
                        "H8-21 forced reopen downloaded the verified package again"
                    )
                verify_cache(private_app_data, "0.2.0")
            finally:
                if server is not None:
                    server.should_exit = True
                if server_thread is not None:
                    server_thread.join(timeout=10)
                    if server_thread.is_alive():
                        raise RuntimeError("H8-21 update server did not stop")
                if private_app_data.exists():
                    shutil.rmtree(private_app_data)
                if ACCEPTANCE_ASSETS.exists():
                    shutil.rmtree(ACCEPTANCE_ASSETS)
                require_port_closed(update_port)
                require_port_closed(webdriver_port)
    if [entry["scenario"] for entry in feed_ledger].count("optional") != 4:
        raise RuntimeError(
            "H8-21 optional App did not use the expected four production checks"
        )
    print(
        "Hidden App update decision and next-start forced installation acceptance passed"
    )


if __name__ == "__main__":
    run()
