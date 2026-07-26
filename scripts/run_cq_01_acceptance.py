#!/usr/bin/env python3
"""CQ-01 plain-language comprehension acceptance in the real production App.

The delivery evidence for CQ-01 is a real user path, not a scan: this script
builds the hidden isolated ``video-studio-e2e`` Tauri App and drives the real
production pages with WebdriverIO —
工作台 → 视频制作 → 两张制作方式卡片 → 动效零件 → 制作设置的 12 套整体风格
→ 视频剪辑 → 设置与诊断/平台状态/新建任务 — asserting on the text a normal
user actually sees. No render, browser download, model call or platform account
is involved.

The spec writes every visited page's rendered text and accessible names to a
capture file, and this script judges them with ``check_user_facing_branding``'s
matcher, so the industry term rule keeps a single implementation shared by the
source scan and the real App evidence.

The static gates run first so a contract or copy regression is reported before
the App build.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from check_user_facing_branding import CONTRACT_PATH, industry_term_occurs
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
SPEC = "./e2e-tauri/plain-language-comprehension.spec.ts"


def _run(
    arguments: list[str],
    *,
    cwd: Path = ROOT,
    env: dict[str, str] | None = None,
    timeout: int = 1_800,
) -> None:
    subprocess.run(arguments, cwd=cwd, env=env, check=True, timeout=timeout)


def run_regression_gates() -> None:
    _run([sys.executable, "scripts/check_user_facing_branding.py", "--self-test"])
    _run([sys.executable, "scripts/check_user_facing_branding.py"])
    _run([sys.executable, "scripts/test_user_facing_branding.py"])
    _run(
        [
            pnpm_executable(),
            "--dir",
            "frontend",
            "exec",
            "vitest",
            "run",
            "src/features/video-studio/VideoStudio.test.tsx",
        ]
    )


def require_plain_captured_pages(capture_file: Path) -> None:
    """Judge the real App text with the same matcher used on the source tree."""
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    mappings = contract["plainLanguageMappings"]
    industry = contract["unexplainedIndustryTerms"]
    pages = json.loads(capture_file.read_text(encoding="utf-8"))
    if not pages:
        raise RuntimeError("CQ-01 captured no page text from the real App")
    violations: list[str] = []
    for page in pages:
        rendered = "\n".join([page["text"], *page["accessibleNames"]])
        for entry in industry:
            term = entry["term"]
            plain = mappings[term]
            if industry_term_occurs(rendered, term) and plain not in rendered:
                violations.append(f"{page['page']}: 未解释的行业词 {term!r}, 应写成 {plain!r}")
    if violations:
        raise RuntimeError(
            "CQ-01 真实 App 页面出现未解释行业词:\n" + "\n".join(sorted(set(violations)))
        )
    print(f"CQ-01 real App plain-language check passed ({len(pages)} pages)")


def run_desktop_acceptance() -> None:
    configuration = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))
    windows = configuration.get("app", {}).get("windows", [])
    if (
        configuration.get("identifier") != APP_IDENTIFIER
        or len(windows) != 1
        or windows[0].get("visible") is not False
    ):
        raise RuntimeError("CQ-01 acceptance must use its hidden isolated App")
    private_app_data = app_data_directory()
    if private_app_data.exists():
        shutil.rmtree(private_app_data)
    port = unused_loopback_port()
    environment = {key: value for key, value in os.environ.items() if key != "TAURI_WEBDRIVER_PORT"}
    environment["TAURI_WEBDRIVER_PORT"] = str(port)
    with tempfile.TemporaryDirectory(prefix="automation-tool-cq01-") as temporary:
        capture_file = Path(temporary) / "captured-pages.json"
        environment["CQ01_PAGE_TEXT_FILE"] = str(capture_file)
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
                require_plain_captured_pages(capture_file)
        finally:
            # Restoring production assets must never replace the original
            # failure, so a restore problem is raised only after the try block
            # finished without its own exception.
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
            restore_failed = restore.returncode != 0
        if restore_failed:
            raise RuntimeError("CQ-01 failed to restore production Vite assets")


def main() -> int:
    run_regression_gates()
    run_desktop_acceptance()
    print("CQ-01 plain-language comprehension acceptance passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
