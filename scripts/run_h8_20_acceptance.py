#!/usr/bin/env python3
"""Run H8-20 through a hidden Tauri App and the production FastAPI update feed."""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import platform
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi import Request
from fastapi.responses import Response, StreamingResponse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from desktop_e2e_prerequisites import desktop_e2e_startup_harness  # noqa: E402

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
FRONTEND_ROOT = REPOSITORY_ROOT / "frontend"
TAURI_CONFIG = FRONTEND_ROOT / "src-tauri" / "tauri.update-download-e2e.conf.json"
APP_IDENTIFIER = "com.aventador.automationtool.h820acceptance"
PAYLOAD = b"test"
PUBLIC_KEY_TEXT = (
    "untrusted comment: minisign public key E7620F1842B4E81F\n"
    "RWQf6LRCGA9i53mlYecO4IzT51TGPpvWucNSCh1CBM0QTaLn73Y7GFO3"
)
SIGNATURE_TEXT = (
    "untrusted comment: signature from minisign secret key\n"
    "RUQf6LRCGA9i559r3g7V1qNyJDApGip8MfqcadIgT9CuhV3EMhHoN1mGTkUidF/"
    "z7SrlQgXdy8ofjb7bNJJylDOocrCo8KLzZwo=\n"
    "trusted comment: timestamp:1556193335\tfile:test\n"
    "y/rUw2y8/hOUYjZU71eHp/Wo1KZ40fGy2VJEDl34XMJM+TX48Ss/17u3IvIfbVR1"
    "FkZZSNCisQbuQY+bHwhEBg=="
)


def pnpm_executable() -> str:
    name = "pnpm.cmd" if sys.platform == "win32" else "pnpm"
    executable = shutil.which(name)
    if executable is None:
        raise RuntimeError(f"H8-20 cannot find {name} on PATH")
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
        raise RuntimeError("H8-20 acceptance must use its hidden isolated App")


def unused_loopback_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = int(listener.getsockname()[1])
    require_port_closed(port)
    return port


def require_port_closed(port: int) -> None:
    with socket.socket() as probe:
        probe.settimeout(0.2)
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            raise RuntimeError(f"H8-20 refuses to reuse occupied loopback port {port}")


def wait_for_port(port: int) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            probe.settimeout(0.2)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.05)
    raise RuntimeError("H8-20 update server did not start")


def write_tls_identity(directory: Path) -> tuple[Path, Path]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    key_path = directory / "localhost-key.pem"
    certificate_path = directory / "localhost-cert.pem"
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    if os.name == "posix":
        key_path.chmod(0o600)
        certificate_path.chmod(0o600)
    return certificate_path, key_path


def current_update_platform() -> tuple[str, str]:
    target = "darwin" if sys.platform == "darwin" else "windows" if sys.platform == "win32" else ""
    machine = platform.machine().lower()
    arch = (
        "aarch64"
        if machine in {"arm64", "aarch64"}
        else "x86_64"
        if machine in {"amd64", "x86_64"}
        else ""
    )
    if not target or not arch:
        raise RuntimeError("H8-20 acceptance supports only packaged macOS and Windows targets")
    return target, arch


def build_update_app(port: int, request_ledger: list[dict[str, str]]) -> Any:
    sys.path.insert(0, str(BACKEND_ROOT / "src"))
    from automation_tool.control_plane.application.desktop_updates import DesktopUpdateCatalog
    from automation_tool.control_plane.bootstrap.app import create_app

    target, arch = current_update_platform()
    signature = base64.b64encode(SIGNATURE_TEXT.encode()).decode()
    catalog = DesktopUpdateCatalog.from_documents(
        [
            {
                "version": "0.2.0",
                "channel": "stable",
                "policy": "optional",
                "target": target,
                "arch": arch,
                "url": f"https://127.0.0.1:{port}/h820-artifact",
                "signature": signature,
                "sha256": hashlib.sha256(PAYLOAD).hexdigest(),
                "sizeBytes": len(PAYLOAD),
                "notes": "H8-20 isolated acceptance release",
                "publishedAt": "2026-07-21T00:00:00Z",
            }
        ]
    )
    app = create_app(database=None, desktop_update_catalog=catalog)
    artifact_attempt = 0

    async def truncated_body() -> AsyncIterator[bytes]:
        yield PAYLOAD[:2]
        raise ConnectionError("intentional H8-20 interrupted transfer")

    async def artifact(request: Request) -> Response:
        nonlocal artifact_attempt
        artifact_attempt += 1
        request_ledger.append(
            {
                "range": request.headers.get("range", ""),
                "if-range": request.headers.get("if-range", ""),
            }
        )
        common_headers = {"etag": '"artifact-v1"', "cache-control": "no-store"}
        if artifact_attempt == 1:
            return StreamingResponse(
                truncated_body(),
                status_code=200,
                headers={**common_headers, "content-length": str(len(PAYLOAD))},
                media_type="application/octet-stream",
            )
        if artifact_attempt == 2:
            if (
                request.headers.get("range") != "bytes=2-"
                or request.headers.get("if-range") != '"artifact-v1"'
            ):
                return Response(status_code=412)
            return Response(
                PAYLOAD[2:],
                status_code=206,
                headers={
                    **common_headers,
                    "content-length": "2",
                    "content-range": "bytes 2-3/4",
                },
                media_type="application/octet-stream",
            )
        return Response(PAYLOAD, headers=common_headers, media_type="application/octet-stream")

    app.add_api_route("/h820-artifact", artifact, methods=["GET"], include_in_schema=False)
    return app


def isolated_environment(update_port: int, webdriver_port: int) -> dict[str, str]:
    environment = dict(os.environ)
    for name in (
        "AUTOMATION_TOOL_UPDATE_ENDPOINT",
        "AUTOMATION_TOOL_UPDATE_PUBLIC_KEY",
        "AUTOMATION_TOOL_UPDATE_ACCEPT_INVALID_TLS",
        "TAURI_WEBDRIVER_PORT",
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
    environment["TAURI_WEBDRIVER_PORT"] = str(webdriver_port)
    return environment


def verify_private_cache(private_app_data: Path, requests: list[dict[str, str]]) -> None:
    if requests != [
        {"range": "", "if-range": ""},
        {"range": "bytes=2-", "if-range": '"artifact-v1"'},
    ]:
        raise RuntimeError("H8-20 did not perform the exact interrupted/resumed HTTP sequence")
    cache_directory = private_app_data / "app-updates" / "cache-v1"
    entries = sorted(path.name for path in cache_directory.iterdir())
    if entries != ["cache-manifest-v1", "candidate.package"]:
        raise RuntimeError("H8-20 did not converge to one verified cached candidate")
    if (cache_directory / "candidate.package").read_bytes() != PAYLOAD:
        raise RuntimeError("H8-20 cached package bytes differ from the signed artifact")
    manifest = (cache_directory / "cache-manifest-v1").read_bytes()
    decoded = json.loads(manifest)
    if decoded.get("version") != "0.2.0" or decoded.get("sizeBytes") != len(PAYLOAD):
        raise RuntimeError("H8-20 cache manifest identity is invalid")
    lowered = manifest.lower()
    if any(token in lowered for token in (b"url", b"signature", b"http", b"path")):
        raise RuntimeError("H8-20 persisted private updater transport data")
    if os.name == "posix":
        if stat.S_IMODE(cache_directory.stat().st_mode) != 0o700:
            raise RuntimeError("H8-20 cache directory is not private")
        for name in entries:
            if stat.S_IMODE((cache_directory / name).stat().st_mode) != 0o600:
                raise RuntimeError("H8-20 cache file is not private")


def run() -> None:
    require_hidden_configuration()
    private_app_data = app_data_directory()
    if private_app_data.exists():
        shutil.rmtree(private_app_data)
    update_port = unused_loopback_port()
    webdriver_port = unused_loopback_port()
    if update_port == webdriver_port:
        raise RuntimeError("H8-20 requires isolated update and WebDriver ports")
    request_ledger: list[dict[str, str]] = []
    server: uvicorn.Server | None = None
    server_thread: threading.Thread | None = None
    restore_failed = False
    with tempfile.TemporaryDirectory(prefix="automation-tool-h820-") as temporary:
        certificate_path, key_path = write_tls_identity(Path(temporary))
        app = build_update_app(update_port, request_ledger)
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
            target=server.run, name="automation-tool-h820", daemon=True
        )
        server_thread.start()
        wait_for_port(update_port)
        # The isolated update feed is this acceptance's own subject; the harness
        # adds what the *startup gate* needs before the App will mount anything
        # at all — the compile-time action-trust triple that `tauri build` bakes
        # in, the verified embedded browser, the signed Executor package, and a
        # Control Plane on the origin this build is compiled to call.
        with desktop_e2e_startup_harness(
            private_app_data,
            environment=isolated_environment(update_port, webdriver_port),
        ) as environment:
            try:
                subprocess.run(
                    [pnpm_executable(), "build:tauri:update-download-test"],
                    cwd=FRONTEND_ROOT,
                    env=environment,
                    check=True,
                )
                require_port_closed(webdriver_port)
                subprocess.run(
                    [pnpm_executable(), "exec", "wdio", "run", "wdio.update-download.conf.ts"],
                    cwd=FRONTEND_ROOT,
                    env=environment,
                    check=True,
                )
                require_port_closed(webdriver_port)
                verify_private_cache(private_app_data, request_ledger)
            finally:
                restore = subprocess.run(
                    [pnpm_executable(), "build"],
                    cwd=FRONTEND_ROOT,
                    env=environment,
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                restore_failed = restore.returncode != 0
                if server is not None:
                    server.should_exit = True
                if server_thread is not None:
                    server_thread.join(timeout=10)
                    if server_thread.is_alive():
                        raise RuntimeError("H8-20 update server did not stop")
                if private_app_data.exists():
                    shutil.rmtree(private_app_data)
                require_port_closed(update_port)
                require_port_closed(webdriver_port)
    if restore_failed:
        raise RuntimeError("H8-20 failed to restore production Vite assets")
    print("Hidden App production-feed resumable update acceptance passed")


if __name__ == "__main__":
    run()
