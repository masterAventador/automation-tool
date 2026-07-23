#!/usr/bin/env python3
"""VE-03 deterministic and production-App acceptance entrypoint."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from run_vf_06_acceptance import (
    APP_IDENTIFIER,
    FRONTEND,
    TAURI_CONFIG,
    app_data_directory,
    pnpm_executable,
    require_port_closed,
    unused_loopback_port,
)

ROOT = Path(__file__).resolve().parents[1]

# VE-03 只验收独立“视频剪辑”入口与剪辑工作台链路；复用 VF-06 隐藏隔离 App 门禁。
SPECS = ("./e2e-tauri/video-editing.spec.ts",)


def run_desktop_acceptance() -> None:
    configuration = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))
    windows = configuration.get("app", {}).get("windows", [])
    if (
        configuration.get("identifier") != APP_IDENTIFIER
        or len(windows) != 1
        or windows[0].get("visible") is not False
    ):
        raise RuntimeError("VE-03 acceptance must use its hidden isolated App")
    private_app_data = app_data_directory()
    if private_app_data.exists():
        shutil.rmtree(private_app_data)
    port = unused_loopback_port()
    environment = {key: value for key, value in os.environ.items() if key != "TAURI_WEBDRIVER_PORT"}
    environment["TAURI_WEBDRIVER_PORT"] = str(port)
    spec_arguments: list[str] = []
    for spec in SPECS:
        spec_arguments.extend(["--spec", spec])
    try:
        subprocess.run(
            [pnpm_executable(), "build:tauri:video-studio-test"],
            cwd=FRONTEND,
            env=environment,
            check=True,
        )
        require_port_closed(port)
        subprocess.run(
            [
                pnpm_executable(),
                "exec",
                "wdio",
                "run",
                "wdio.video-studio.conf.ts",
                *spec_arguments,
            ],
            cwd=FRONTEND,
            env=environment,
            check=True,
        )
        require_port_closed(port)
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
            raise RuntimeError("VE-03 failed to restore production Vite assets")


def main() -> int:
    required = (
        ROOT / "frontend/src/features/video-editing/video-editing-dto.ts",
        ROOT / "frontend/src/features/video-editing/video-editing-dto.test.ts",
        ROOT / "frontend/src/features/video-editing/video-editing-gateway.ts",
        ROOT / "frontend/src/features/video-editing/local-video-editing-gateway.ts",
        ROOT / "frontend/src/features/video-editing/local-video-editing-gateway.test.ts",
        ROOT / "frontend/src/features/video-editing/VideoEditingWorkbench.tsx",
        ROOT / "frontend/src/features/video-editing/VideoEditingWorkbench.test.tsx",
        ROOT / "frontend/e2e-tauri/video-editing.spec.ts",
        ROOT / "docs/development/VE-03.md",
    )
    missing = [path.relative_to(ROOT).as_posix() for path in required if not path.is_file()]
    if missing:
        raise SystemExit(f"VE-03 missing deliverables: {', '.join(missing)}")

    roadmap = (ROOT / "docs/embedded-browser-video-studio-roadmap.md").read_text(
        encoding="utf-8"
    )
    rows = [line for line in roadmap.splitlines() if line.startswith("| VE-03 |")]
    if len(rows) != 1 or not rows[0].endswith("| ✅ 已完成 |"):
        raise SystemExit("VE-03 roadmap row is missing, duplicated or incomplete")

    subprocess.run(
        [
            pnpm_executable(),
            "--dir",
            "frontend",
            "exec",
            "vitest",
            "run",
            "src/features/video-editing/",
            "src/app/WorkbenchShell.test.tsx",
        ],
        cwd=ROOT,
        check=True,
    )
    run_desktop_acceptance()
    print("VE-03 standalone video editing workbench acceptance passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
