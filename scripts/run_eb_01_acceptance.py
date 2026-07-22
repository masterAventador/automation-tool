#!/usr/bin/env python3
"""Verify EB-01 Playwright and embedded Chromium compatibility gates."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def require_file(relative_path: str) -> str:
    path = REPOSITORY_ROOT / relative_path
    if not path.is_file():
        raise AssertionError(f"缺少 EB-01 文件: {relative_path}")
    return path.read_text(encoding="utf-8")


def main() -> None:
    contract_text = require_file("contracts/browser/embedded-chromium-compatibility.v1.json")
    contract = json.loads(contract_text)
    if contract["production_runtime"]["playwright_python"] != "1.61.0":
        raise AssertionError("生产 Playwright Python 必须精确锁定 1.61.0")
    chromium = contract["production_runtime"]["chromium"]
    if chromium != {
        "browser_version": "149.0.7827.55",
        "install_by_default": True,
        "name": "chromium",
        "revision": "1228",
        "title": "Chrome for Testing",
    }:
        raise AssertionError("Chromium 完整版本或修订号漂移")
    target_ids = {item["id"] for item in contract["supported_targets"]}
    if target_ids != {"macos-arm64", "windows-x86_64"}:
        raise AssertionError("EB-01 必须只声明首发两种目标平台")
    if contract["test_harness"]["playwright_node"] != "1.61.1":
        raise AssertionError("Node Playwright 测试工具必须精确锁定 1.61.1")

    require_file("scripts/check_embedded_browser_compatibility.py")
    for fixture in (
        "contracts/browser/fixtures/component-valid-macos-arm64.json",
        "contracts/browser/fixtures/component-valid-windows-x86_64.json",
        "contracts/browser/fixtures/component-invalid-version.json",
        "contracts/browser/fixtures/component-invalid-revision.json",
        "contracts/browser/fixtures/component-invalid-platform.json",
    ):
        require_file(fixture)

    subprocess.run(
        [
            "uv",
            "run",
            "--project",
            "backend",
            "--locked",
            "python",
            "scripts/check_embedded_browser_compatibility.py",
            "--self-test",
        ],
        cwd=REPOSITORY_ROOT,
        check=True,
    )
    evidence = require_file("docs/development/EB-01.md")
    for phrase in (
        "# EB-01 完成证据",
        "状态：✅ 已完成",  # noqa: RUF001
        "## RED",
        "## GREEN",
        "## 失败矩阵",
        "## 正常用户路径验收",
        "## 真实边界",
        "## 清理",
        "## 遗留项",
    ):
        if phrase not in evidence:
            raise AssertionError(f"EB-01 独立证据缺少 {phrase}")
    roadmap = require_file("docs/embedded-browser-video-studio-roadmap.md")
    rows = [line for line in roadmap.splitlines() if line.startswith("| EB-01 |")]
    if len(rows) != 1 or not rows[0].endswith("| ✅ 已完成 |"):
        raise AssertionError("专项 Roadmap 中 EB-01 必须唯一且为已完成")
    print("EB-01 embedded Chromium compatibility acceptance passed")


if __name__ == "__main__":
    try:
        main()
    except (AssertionError, KeyError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        print(f"EB-01 acceptance failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
