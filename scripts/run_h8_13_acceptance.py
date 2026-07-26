#!/usr/bin/env python3
"""Run H8-13 through one isolated hidden Tauri App and inspect the produced ZIP."""

from __future__ import annotations

import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import zlib
from hashlib import sha256
from pathlib import Path
from typing import cast
from zipfile import ZIP_STORED, ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parent))

from desktop_e2e_prerequisites import desktop_e2e_startup_harness  # noqa: E402

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_ROOT = REPOSITORY_ROOT / "frontend"
TAURI_CONFIG = FRONTEND_ROOT / "src-tauri" / "tauri.diagnostic-export-e2e.conf.json"
WDIO_SPEC = FRONTEND_ROOT / "e2e-tauri" / "diagnostic-export.spec.ts"
APP_IDENTIFIER = "com.aventador.automationtool.h813acceptance"
PAGE_ID = "123e4567-e89b-42d3-a456-426614174000"
TRACE_ID = "223e4567-e89b-42d3-a456-426614174000"
SCREENSHOT_ID = "323e4567-e89b-42d3-a456-426614174000"


def pnpm_executable() -> str:
    name = "pnpm.cmd" if sys.platform == "win32" else "pnpm"
    executable = shutil.which(name)
    if executable is None:
        raise RuntimeError(f"H8-13 cannot find {name} on PATH")
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
    if not WDIO_SPEC.is_file():
        raise RuntimeError("H8-13 diagnostic export App spec is unavailable")
    configuration = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))
    windows = configuration.get("app", {}).get("windows", [])
    if (
        configuration.get("identifier") != APP_IDENTIFIER
        or len(windows) != 1
        or windows[0].get("visible") is not False
    ):
        raise RuntimeError("H8-13 acceptance must use one isolated hidden App")


def unused_loopback_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = cast(int, listener.getsockname()[1])
    require_port_closed(port)
    return port


def require_port_closed(port: int) -> None:
    with socket.socket() as probe:
        probe.settimeout(0.2)
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            raise RuntimeError(f"H8-13 refuses to reuse occupied loopback port {port}")


def png() -> bytes:
    output = bytearray(b"\x89PNG\r\n\x1a\n")
    for kind, payload in (
        (b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)),
        (b"IDAT", b"\x01"),
        (b"IEND", b""),
    ):
        output.extend(struct.pack(">I", len(payload)))
        output.extend(kind)
        output.extend(payload)
        output.extend(struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))
    return bytes(output)


def seed_artifacts(private_app_data: Path) -> dict[str, bytes]:
    state = private_app_data / "local-executor" / "state"
    documents = {
        f"page-drift/{PAGE_ID}.json": json.dumps(
            {
                "artifact_id": PAGE_ID,
                "artifact_version": "executor.page-drift-artifact.v1",
                "evidence": "page_version_unknown",
                "observed_at": "2026-07-21T01:02:03Z",
                "operation": "douyin_target_discovery",
                "page_revision": 1,
                "platform": "douyin",
                "stage": "search",
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii"),
        f"browser/traces/{TRACE_ID}.json": json.dumps(
            {
                "artifact_id": TRACE_ID,
                "artifact_version": "executor.browser-diagnostic-trace.v1",
                "captured_at": "2026-07-21T01:02:03Z",
                "operation": "douyin_target_discovery",
                "page_revision": 2,
                "platform": "douyin",
                "redaction_version": "browser-skeleton.v1",
                "screenshot_artifact_id": SCREENSHOT_ID,
                "stage": "extraction",
                "trigger": "failure",
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii"),
        f"browser/screenshots/{SCREENSHOT_ID}.png": png(),
    }
    source_paths = {
        f"page-drift/{PAGE_ID}.json": state
        / "artifacts"
        / "evidence"
        / "page-drift"
        / f"{PAGE_ID}.json",
        f"browser/traces/{TRACE_ID}.json": state
        / "artifacts"
        / "diagnostics"
        / "traces"
        / f"{TRACE_ID}.json",
        f"browser/screenshots/{SCREENSHOT_ID}.png": state
        / "artifacts"
        / "diagnostics"
        / "screenshots"
        / f"{SCREENSHOT_ID}.png",
    }
    for archive_name, path in source_paths.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(documents[archive_name])
    return documents


def verify_export(export_directory: Path, expected: dict[str, bytes]) -> None:
    packages = tuple(export_directory.glob("automation-tool-diagnostics-*.zip"))
    if len(packages) != 1:
        raise RuntimeError("H8-13 must produce exactly one diagnostic package")
    if packages[0].stat().st_size > 12 * 1024 * 1024:
        raise RuntimeError("H8-13 diagnostic package exceeded its byte limit")
    with ZipFile(packages[0]) as archive:
        names = set(archive.namelist())
        required = set(expected) | {"executor/diagnostics.txt", "manifest.json"}
        if names != required:
            raise RuntimeError("H8-13 diagnostic package contains a non-whitelisted entry")
        if any(info.compress_type != ZIP_STORED for info in archive.infolist()):
            raise RuntimeError("H8-13 diagnostic package must use deterministic stored entries")
        for name, content in expected.items():
            if archive.read(name) != content:
                raise RuntimeError("H8-13 changed a validated artifact during export")
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("export_version") != "1" or len(manifest.get("entries", [])) != len(
            required - {"manifest.json"}
        ):
            raise RuntimeError("H8-13 manifest is not canonical")
        exported = {
            entry["path"]: (entry["size_bytes"], entry["sha256"]) for entry in manifest["entries"]
        }
        for name in required - {"manifest.json"}:
            content = archive.read(name)
            if exported.get(name) != (len(content), sha256(content).hexdigest()):
                raise RuntimeError("H8-13 manifest digest does not match its entry")
        joined = b"\n".join(archive.read(name) for name in archive.namelist())
        for forbidden in (
            str(private_path_token()).encode(),
            b"executor-ledger.sqlite3",
            b"sessionid",
            b"comment_body",
            b"message_body",
        ):
            if forbidden in joined:
                raise RuntimeError("H8-13 exported private or out-of-scope content")


def private_path_token() -> Path:
    return Path.home()


def main() -> None:
    require_hidden_configuration()
    private_app_data = app_data_directory()
    if private_app_data.exists():
        shutil.rmtree(private_app_data)
    port = unused_loopback_port()
    base_environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"TAURI_WEBDRIVER_PORT", "AUTOMATION_TOOL_H813_EXPORT_DIRECTORY"}
    }
    base_environment["TAURI_WEBDRIVER_PORT"] = str(port)
    restore_failed = False
    with tempfile.TemporaryDirectory(prefix="automation-tool-h813-") as temporary:
        export_directory = Path(temporary).resolve()
        base_environment["AUTOMATION_TOOL_H813_EXPORT_DIRECTORY"] = str(export_directory)
        # Seeded before the harness stages anything: these are the App's own
        # private diagnostics files, and the harness only touches the resource
        # directory the build reads from.
        expected = seed_artifacts(private_app_data)
        with desktop_e2e_startup_harness(
            private_app_data,
            environment=base_environment,
        ) as environment:
            try:
                subprocess.run(
                    [pnpm_executable(), "build:tauri:diagnostic-export-test"],
                    cwd=FRONTEND_ROOT,
                    env=environment,
                    check=True,
                )
                require_port_closed(port)
                wdio = subprocess.run(
                    [
                        pnpm_executable(),
                        "exec",
                        "wdio",
                        "run",
                        "wdio.diagnostic-export.conf.ts",
                    ],
                    cwd=FRONTEND_ROOT,
                    env=environment,
                    check=False,
                )
                if wdio.returncode != 0:
                    package_count = len(tuple(export_directory.glob("*.zip")))
                    raise RuntimeError(
                        f"H8-13 hidden App failed after producing {package_count} package(s)"
                    )
                require_port_closed(port)
                verify_export(export_directory, expected)
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
                if private_app_data.exists():
                    shutil.rmtree(private_app_data)
                require_port_closed(port)
    if restore_failed:
        raise RuntimeError("H8-13 failed to restore production Vite assets")


if __name__ == "__main__":
    main()
