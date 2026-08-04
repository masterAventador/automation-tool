#!/usr/bin/env python3
"""PB-07 的正常用户路径验收：从真实 App 左侧导航点进发布页。

同任务的 `publishing.spec.ts` 走 `core.invoke("get_publish_workspace")`，证明的是
桥的契约而不是导航；它的文件头写着「clicking through to the page in the real App」
未覆盖，理由是 debug 构建停在启动环境闸。这里复用 CQ-02 的
`video_studio_startup_harness` 把启动环境备好，工作台就能挂载，发布页可以从正常入口
点进去。

驱动只做一件事：备环境、构建隐藏隔离 App、跑那一条 spec、确认端口收干净。断言全部
在 spec 里，由 WebDriver 读真实 WebView。
"""

from __future__ import annotations

import json
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

SPEC = "./e2e-tauri/publishing-navigation.spec.ts"


def _run(arguments: list[str], *, cwd: Path, env: dict[str, str]) -> None:
    subprocess.run(arguments, cwd=cwd, env=env, check=True, timeout=1800)


def run_desktop_acceptance() -> None:
    """Build the hidden video-studio App and drive the one navigation spec.

    Kept as its own named function rather than inlined into `main`, because
    `scripts/test_video_studio_startup_gate_drivers.py` requires every driver
    that builds this App to do it inside one top-level function that `main`
    calls. Writing the build straight into `main` made this driver the eighth
    one and the first to break that shape — the gate caught it the same day.
    """
    configuration = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))
    windows = configuration.get("app", {}).get("windows", [])
    if (
        configuration.get("identifier") != APP_IDENTIFIER
        or len(windows) != 1
        or windows[0].get("visible") is not False
    ):
        raise RuntimeError(
            "PB-07 navigation acceptance must use its hidden isolated App"
        )

    private_app_data = app_data_directory()
    if private_app_data.exists():
        shutil.rmtree(private_app_data)
    port = unused_loopback_port()
    environment = {
        key: value for key, value in os.environ.items() if key != "TAURI_WEBDRIVER_PORT"
    }
    environment["TAURI_WEBDRIVER_PORT"] = str(port)

    restore_failed = False
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
                    SPEC,
                ],
                cwd=FRONTEND,
                env=environment,
            )
            require_port_closed(port)
    finally:
        restore = subprocess.run(
            [pnpm_executable(), "build"],
            cwd=FRONTEND,
            check=False,
        )
        restore_failed = restore.returncode != 0
        if private_app_data.exists():
            shutil.rmtree(private_app_data)

    if restore_failed:
        raise RuntimeError(
            "PB-07 navigation acceptance could not restore the normal build"
        )


def main() -> int:
    run_desktop_acceptance()
    print("PB-07 publish page normal-navigation acceptance passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
