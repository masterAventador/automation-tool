#!/usr/bin/env python3
"""BM-15 parts catalog production-path acceptance.

Reruns the deterministic BM-15 gates, builds the hidden isolated
``video-studio-e2e`` Tauri App, then drives the real production page path:
工作台 → 视频制作 → 品牌动效成片 → 动效零件, browsing the locked 134-item
catalog by Chinese category, checking the audited per-item labels, and
overriding a per-beat selection. No render, browser download or model call
is involved; the App UI must stay free of raw part ids and indicator words.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from desktop_e2e_prerequisites import video_studio_startup_harness
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


def _run(
    arguments: list[str],
    *,
    cwd: Path = ROOT,
    env: dict[str, str] | None = None,
    timeout: int = 1_200,
) -> None:
    subprocess.run(arguments, cwd=cwd, env=env, check=True, timeout=timeout)


def run_deterministic_gates() -> None:
    _run([sys.executable, "scripts/check_motion_catalog_ui_projection.py"])
    _run([sys.executable, "scripts/test_motion_catalog_ui_projection.py"])
    _run([sys.executable, "scripts/test_motion_authoring_agent.py"])
    _run(
        [
            pnpm_executable(),
            "--dir",
            "frontend",
            "exec",
            "vitest",
            "run",
            "src/features/video-studio/motion-parts-catalog.test.ts",
            "src/features/video-studio/MotionPartsCatalog.test.tsx",
            "src/features/video-studio/VideoStudio.test.tsx",
        ]
    )
    _run([pnpm_executable(), "--dir", "frontend", "typecheck"])


def run_desktop_acceptance() -> None:
    import json

    configuration = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))
    windows = configuration.get("app", {}).get("windows", [])
    if (
        configuration.get("identifier") != APP_IDENTIFIER
        or len(windows) != 1
        or windows[0].get("visible") is not False
    ):
        raise RuntimeError("BM-15 acceptance must use its hidden isolated App")
    private_app_data = app_data_directory()
    if private_app_data.exists():
        shutil.rmtree(private_app_data)
    port = unused_loopback_port()
    environment = {key: value for key, value in os.environ.items() if key != "TAURI_WEBDRIVER_PORT"}
    environment["TAURI_WEBDRIVER_PORT"] = str(port)
    try:
        with video_studio_startup_harness(
            private_app_data,
            environment=environment,
        ) as environment:
            _run(
                [pnpm_executable(), "build:tauri:video-studio-test"],
                cwd=FRONTEND,
                env=environment,
            )
            require_port_closed(port)
            _run(
                [
                    pnpm_executable(),
                    "exec",
                    "wdio",
                    "run",
                    "wdio.video-studio.conf.ts",
                    "--spec",
                    "./e2e-tauri/motion-parts-catalog.spec.ts",
                ],
                cwd=FRONTEND,
                env=environment,
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
            timeout=600,
        )
        if private_app_data.exists():
            shutil.rmtree(private_app_data)
        require_port_closed(port)
        if restore.returncode != 0:
            raise RuntimeError("BM-15 failed to restore production Vite assets")


def main() -> int:
    run_deterministic_gates()
    run_desktop_acceptance()
    print("BM-15 parts catalog production-path acceptance passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
