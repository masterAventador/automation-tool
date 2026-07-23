#!/usr/bin/env python3
"""IM-05 real frozen WebUI and normal App-entry acceptance."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

from build_material_video_worker_candidate import ENTRYPOINT, build_candidate

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
UPSTREAM = ROOT / "vendor/moneyprinterturbo"
TAURI_CONFIG = FRONTEND / "src-tauri/tauri.video-studio-e2e.conf.json"
CONTRACT = ROOT / "contracts/security/material-video-webui.v1.json"
APP_IDENTIFIER = "com.aventador.automationtool.vf06acceptance"


def run(command: list[str], *, environment: dict[str, str] | None = None) -> None:
    subprocess.run(command, cwd=ROOT, env=environment, check=True)


def app_data_directory() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support" / APP_IDENTIFIER
    if sys.platform == "win32":
        roaming = os.environ.get("APPDATA")
        if not roaming:
            raise RuntimeError("IM-05 Windows AppData is unavailable")
        return Path(roaming) / APP_IDENTIFIER
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return base / APP_IDENTIFIER


def unused_loopback_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def require_port_closed(port: int) -> None:
    with socket.socket() as probe:
        probe.settimeout(0.2)
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            raise RuntimeError(f"IM-05 isolated driver port remains open: {port}")


def require_contract() -> None:
    value = json.loads(CONTRACT.read_text(encoding="utf-8"))
    expected = {
        "schemaVersion": 1,
        "host": "127.0.0.1",
        "port": 0,
        "capabilityPathPrefix": "studio-",
        "capabilityEntropyBytes": 32,
        "endpointTransport": "authenticated_worker_ready_event",
        "endpointOwner": "tauri_only",
        "reactReceivesEndpoint": False,
        "userVisibleLocalhostUrl": False,
        "newWindowsAllowed": False,
        "downloadsAllowed": False,
        "topLevelNavigation": "exact_loopback_port_and_capability_path",
        "configurationRoot": "task_private_workspace",
        "storageRoot": "task_private_workspace",
        "upstreamSourceWritable": False,
        "workerDescendantsStoppedWithAppWindow": True,
    }
    if value != expected:
        raise AssertionError("IM-05 protected WebUI contract drifted")


def require_real_frozen_webui(candidate: Path) -> None:
    executable = (
        candidate / (f"{ENTRYPOINT}.exe" if os.name == "nt" else ENTRYPOINT)
    ).resolve(strict=True)
    environment = dict(os.environ)
    environment["AUTOMATION_TOOL_IM05_WORKER"] = str(executable)
    run(
        [
            "cargo",
            "test",
            "--manifest-path",
            "frontend/src-tauri/Cargo.toml",
            "--test",
            "material_video_gateway",
            "--locked",
            "frozen_worker_starts_real_web_ui_only_inside_the_task_workspace",
            "--",
            "--exact",
            "--test-threads=1",
        ],
        environment=environment,
    )
    for forbidden in (
        candidate / "config.toml",
        candidate / "_internal/config.toml",
        candidate / "_internal/upstream/config.toml",
        candidate / "_internal/upstream/storage",
    ):
        if forbidden.exists():
            raise AssertionError(
                f"IM-05 wrote into frozen upstream package: {forbidden.name}"
            )


def require_normal_app_entry(candidate: Path) -> None:
    executable = (
        candidate / (f"{ENTRYPOINT}.exe" if os.name == "nt" else ENTRYPOINT)
    ).resolve(strict=True)
    private_app_data = app_data_directory()
    if private_app_data.exists():
        shutil.rmtree(private_app_data)
    port = unused_loopback_port()
    require_port_closed(port)
    environment = {
        key: value for key, value in os.environ.items() if key != "TAURI_WEBDRIVER_PORT"
    }
    environment["TAURI_WEBDRIVER_PORT"] = str(port)
    environment["AUTOMATION_TOOL_IM05_WORKER"] = str(executable)
    try:
        subprocess.run(
            ["pnpm", "build:tauri:video-studio-test"],
            cwd=FRONTEND,
            env=environment,
            check=True,
        )
        require_port_closed(port)
        subprocess.run(
            [
                "pnpm",
                "exec",
                "wdio",
                "run",
                "wdio.video-studio.conf.ts",
                "--spec",
                "./e2e-tauri/material-video-webui.spec.ts",
            ],
            cwd=FRONTEND,
            env=environment,
            check=True,
        )
        require_port_closed(port)
    finally:
        subprocess.run(
            ["pnpm", "build"],
            cwd=FRONTEND,
            env=environment,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if private_app_data.exists():
            shutil.rmtree(private_app_data)
        require_port_closed(port)


def require_evidence() -> None:
    evidence = (ROOT / "docs/development/IM-05.md").read_text(encoding="utf-8")
    for marker in (
        "# IM-05 完成证据",
        "状态：🔍 待验收",
        "## RED",
        "## GREEN",
        "## 正常用户路径验收",
        "## 真实边界",
        "## 未完成的真实验收",
        "## 清理",
    ):
        if marker not in evidence:
            raise AssertionError(f"IM-05 evidence is missing {marker}")
    roadmap = (ROOT / "docs/embedded-browser-video-studio-roadmap.md").read_text(
        encoding="utf-8"
    )
    rows = [line for line in roadmap.splitlines() if line.startswith("| IM-05 |")]
    if len(rows) != 1 or not rows[0].endswith("| 🔍 待验收 |"):
        raise AssertionError(
            "IM-05 roadmap status is not pending real generation validation"
        )


def main() -> int:
    require_contract()
    upstream_before = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=UPSTREAM,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    run([sys.executable, "scripts/test_material_video_gateway.py"])
    run([sys.executable, "scripts/test_material_video_worker.py"])
    run(
        [
            "pnpm",
            "--dir",
            "frontend",
            "exec",
            "vitest",
            "run",
            "src/features/video-studio/VideoStudio.test.tsx",
            "src/platform/tauri/material-video-studio-gateway.test.ts",
        ]
    )
    with tempfile.TemporaryDirectory(prefix="im05-acceptance-") as directory:
        candidate = Path(directory) / "material-video-worker"
        build_candidate(candidate)
        require_real_frozen_webui(candidate)
        require_normal_app_entry(candidate)
    upstream_after = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=UPSTREAM,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    if upstream_after != upstream_before:
        raise AssertionError("IM-05 acceptance modified the upstream submodule")
    require_evidence()
    print("IM-05 real frozen WebUI and normal App-entry acceptance passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
