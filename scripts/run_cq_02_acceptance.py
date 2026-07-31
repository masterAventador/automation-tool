#!/usr/bin/env python3
"""CQ-02 upstream-name acceptance in the real production Tauri App.

The static source scanner and Playwright UI Harness run first. The delivery
boundary then builds the hidden isolated ``video-studio-e2e`` App and drives
all eight ordinary workbench pages through WebdriverIO. The spec records W3C
computed accessibility roles and labels; this runner independently validates
that capture so a weakened browser assertion cannot turn the gate green.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from check_user_facing_branding import term_occurs
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
SPEC = "./e2e-tauri/upstream-name-leak.spec.ts"
EVIDENCE = (
    ROOT
    / ".local/embedded-browser-video-studio/cq-02-evidence"
    / "cq-02-real-app-accessibility.json"
)
EXPECTED_PAGES = [
    "AI 助理",
    "热点发现",
    "创作",
    "发布",
    "消息与互动",
    "自动化",
    "账号与平台",
    "设置",
]
UPSTREAM_NAMES = [
    "moneyprinterturbo",
    "money printer turbo",
    "money-printer-turbo",
    "hyperframes",
    "hyper frames",
    "hyper-frames",
    "browser use",
    "browser_use",
    "browseruse",
    "playwright",
    "chromium",
    "webdriver",
    "official_api",
    "b-roll",
    "poc",
]


def _run(
    arguments: list[str],
    *,
    cwd: Path = ROOT,
    env: dict[str, str] | None = None,
    timeout: int = 1_800,
) -> None:
    subprocess.run(arguments, cwd=cwd, env=env, check=True, timeout=timeout)


def run_regression_gates() -> None:
    _run([sys.executable, "scripts/test_cq_02_real_app_acceptance.py"])
    _run([sys.executable, "scripts/check_user_facing_branding.py", "--self-test"])
    _run([sys.executable, "scripts/check_user_facing_branding.py"])
    _run(
        [
            pnpm_executable(),
            "--dir",
            "frontend",
            "exec",
            "playwright",
            "test",
            "e2e/upstream-name-leak.spec.ts",
        ]
    )


def require_real_app_capture(capture_file: Path) -> tuple[int, int]:
    """Fail closed on the real App's W3C computed accessibility evidence."""
    if not capture_file.is_file():
        raise RuntimeError("CQ-02 real App did not write its accessibility capture")
    try:
        pages = json.loads(capture_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(
            "CQ-02 real App accessibility capture is unreadable"
        ) from error
    if not isinstance(pages, list):
        raise RuntimeError("CQ-02 real App accessibility capture must be a list")
    actual_pages = [
        page.get("page") if isinstance(page, dict) else None for page in pages
    ]
    if actual_pages != EXPECTED_PAGES:
        raise RuntimeError(
            "CQ-02 real App did not capture the complete workbench route: "
            f"expected {EXPECTED_PAGES!r}, got {actual_pages!r}"
        )

    violations: list[str] = []
    total_facts = 0
    for page in pages:
        title = page.get("title")
        facts = page.get("facts")
        if not isinstance(title, str) or title.strip() == "":
            raise RuntimeError(f"CQ-02 {page['page']} capture has no document title")
        if not isinstance(facts, list) or not facts:
            raise RuntimeError(
                f"CQ-02 {page['page']} capture has no computed accessibility facts"
            )
        total_facts += len(facts)
        audible_parts = [title]
        for index, fact in enumerate(facts):
            if not isinstance(fact, dict):
                raise RuntimeError(
                    f"CQ-02 {page['page']} accessibility fact {index} is not an object"
                )
            role = fact.get("role")
            label = fact.get("label")
            if not isinstance(role, str) or not isinstance(label, str):
                raise RuntimeError(
                    f"CQ-02 {page['page']} accessibility fact {index} is malformed"
                )
            if role == "" and label == "":
                raise RuntimeError(
                    f"CQ-02 {page['page']} accessibility fact {index} is empty"
                )
            audible_parts.extend((role, label))
        audible = "\n".join(audible_parts)
        for name in UPSTREAM_NAMES:
            if term_occurs(audible, name):
                violations.append(
                    f"{page['page']}: computed accessibility leaked {name!r}"
                )
    if violations:
        raise RuntimeError(
            "CQ-02 real App accessibility result contains upstream names:\n"
            + "\n".join(sorted(set(violations)))
        )
    print(
        "CQ-02 real App accessibility check passed "
        f"({len(pages)} pages, {total_facts} computed facts)"
    )
    return len(pages), total_facts


def run_desktop_acceptance() -> None:
    configuration = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))
    windows = configuration.get("app", {}).get("windows", [])
    if (
        configuration.get("identifier") != APP_IDENTIFIER
        or len(windows) != 1
        or windows[0].get("visible") is not False
    ):
        raise RuntimeError("CQ-02 acceptance must use its hidden isolated App")

    private_app_data = app_data_directory()
    if private_app_data.exists():
        shutil.rmtree(private_app_data)
    port = unused_loopback_port()
    environment = {
        key: value for key, value in os.environ.items() if key != "TAURI_WEBDRIVER_PORT"
    }
    environment["TAURI_WEBDRIVER_PORT"] = str(port)
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.unlink(missing_ok=True)
    environment["CQ02_ACCESSIBILITY_CAPTURE"] = str(EVIDENCE)

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
            require_real_app_capture(EVIDENCE)
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
        restore_failed = restore.returncode != 0
    if restore_failed:
        raise RuntimeError("CQ-02 failed to restore production Vite assets")


def main() -> int:
    run_regression_gates()
    run_desktop_acceptance()
    print(f"CQ-02 evidence: {EVIDENCE.relative_to(ROOT)}")
    print("CQ-02 production App accessibility-tree acceptance passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
