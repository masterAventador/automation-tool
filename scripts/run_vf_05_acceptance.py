#!/usr/bin/env python3
"""VF-05 deterministic acceptance entrypoint."""

from __future__ import annotations

import json
import os
import shutil
import socket
import stat
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from desktop_e2e_prerequisites import desktop_e2e_startup_harness  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
TAURI_CONFIG = FRONTEND / "src-tauri" / "tauri.model-service-e2e.conf.json"
APP_IDENTIFIER = "com.aventador.automationtool.vf05acceptance"
TEST_KEY = "sk-vf05-invalid-desktop-key-1234567890"


def run(*command: str) -> None:
    subprocess.run(command, cwd=ROOT, check=True)


def pnpm_executable() -> str:
    name = "pnpm.cmd" if sys.platform == "win32" else "pnpm"
    executable = shutil.which(name)
    if executable is None:
        raise RuntimeError(f"VF-05 cannot find {name} on PATH")
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
            raise RuntimeError(f"VF-05 refuses to reuse occupied loopback port {port}")


def verify_private_credentials(private_app_data: Path) -> None:
    directory = private_app_data / "model-services"
    expected = {
        "model-service-script-v1": "script",
        "model-service-video-creative-v1": "video_creative",
    }
    for file_name, purpose in expected.items():
        path = directory / file_name
        document = json.loads(path.read_text(encoding="utf-8"))
        if document != {
            "version": 1,
            "purpose": purpose,
            "model_id": "qwen3.7-max-2026-06-08",
            "api_key": TEST_KEY,
        }:
            raise RuntimeError(f"VF-05 persisted a non-canonical {purpose} credential")
        if os.name == "posix" and stat.S_IMODE(path.stat().st_mode) != 0o600:
            raise RuntimeError(f"VF-05 {purpose} credential is not private")
    if os.name == "posix" and stat.S_IMODE(directory.stat().st_mode) != 0o700:
        raise RuntimeError("VF-05 credential directory is not private")


def run_desktop_acceptance() -> None:
    configuration = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))
    windows = configuration.get("app", {}).get("windows", [])
    if (
        configuration.get("identifier") != APP_IDENTIFIER
        or len(windows) != 1
        or windows[0].get("visible") is not False
    ):
        raise RuntimeError("VF-05 acceptance must use its hidden isolated App")
    private_app_data = app_data_directory()
    if private_app_data.exists():
        shutil.rmtree(private_app_data)
    port = unused_loopback_port()
    base_environment = {
        key: value for key, value in os.environ.items() if key != "TAURI_WEBDRIVER_PORT"
    }
    base_environment["TAURI_WEBDRIVER_PORT"] = str(port)
    # The spec tolerates reaching the settings card through the startup repair
    # panel, so this driver could "pass" on a blocked App. That is the weaker
    # path: it proves the credential form works for a user whose install is
    # broken, and says nothing about the settings page every working install
    # actually uses. The harness makes the workbench the path under test.
    with desktop_e2e_startup_harness(
        private_app_data,
        environment=base_environment,
    ) as environment:
        try:
            subprocess.run(
                [pnpm_executable(), "build:tauri:model-service-test"],
                cwd=FRONTEND,
                env=environment,
                check=True,
            )
            require_port_closed(port)
            subprocess.run(
                [pnpm_executable(), "exec", "wdio", "run", "wdio.model-service.conf.ts"],
                cwd=FRONTEND,
                env=environment,
                check=True,
            )
            require_port_closed(port)
            verify_private_credentials(private_app_data)
        finally:
            restore = subprocess.run(
                [pnpm_executable(), "build"],
                cwd=FRONTEND,
                env=environment,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if private_app_data.exists():
                shutil.rmtree(private_app_data)
            require_port_closed(port)
            if restore.returncode != 0:
                raise RuntimeError("VF-05 failed to restore production Vite assets")


def main() -> int:
    required = (
        ROOT / "contracts/video/bailian-model-catalog.v1.json",
        ROOT / "frontend/src-tauri/src/model_service_settings.rs",
        ROOT / "frontend/src/features/settings/ModelServiceSettings.tsx",
        ROOT / "frontend/src/platform/tauri/model-service-gateway.ts",
        ROOT / "frontend/e2e-tauri/model-service.spec.ts",
        ROOT / "frontend/src/test-production-main.ts",
        TAURI_CONFIG,
        ROOT / "docs/development/VF-05.md",
    )
    missing = [path.relative_to(ROOT).as_posix() for path in required if not path.is_file()]
    if missing:
        raise SystemExit(f"VF-05 missing deliverables: {', '.join(missing)}")
    roadmap = (ROOT / "docs/embedded-browser-video-studio-roadmap.md").read_text(
        encoding="utf-8"
    )
    if roadmap.count("| VF-05 |") != 1 or "| VF-05 | 模型与服务密钥设置 |" not in roadmap:
        raise SystemExit("VF-05 roadmap row is missing or duplicated")
    vf05_row = next(line for line in roadmap.splitlines() if line.startswith("| VF-05 |"))
    if not vf05_row.endswith("| ✅ 已完成 |"):
        raise SystemExit("VF-05 roadmap row is not complete")
    run(
        "cargo",
        "test",
        "--manifest-path",
        "frontend/src-tauri/Cargo.toml",
        "--test",
        "model_service_settings",
    )
    run_desktop_acceptance()
    run(
        "pnpm",
        "--dir",
        "frontend",
        "exec",
        "vitest",
        "run",
        "src/features/settings/ModelServiceSettings.test.tsx",
        "src/platform/tauri/model-service-gateway.test.ts",
    )
    print("VF-05 model service settings acceptance passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
