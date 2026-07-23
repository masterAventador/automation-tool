#!/usr/bin/env python3
"""Verify EB-02 shared Chromium cross-platform validation evidence."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def require_file(relative_path: str) -> str:
    path = REPOSITORY_ROOT / relative_path
    if not path.is_file():
        raise AssertionError(f"缺少 EB-02 文件: {relative_path}")
    return path.read_text(encoding="utf-8")


def main() -> None:
    matrix = json.loads(require_file("contracts/browser/shared-chromium-validation.v1.json"))
    if matrix["chromium"] != {"browser_version": "149.0.7827.55", "revision": "1228"}:
        raise AssertionError("EB-02 必须复用 EB-01 的唯一 Chromium")
    if matrix["catalog"]["installable_items"] != 134:
        raise AssertionError("必须覆盖 134 个可安装目录项")
    if matrix["style_count"] != 12:
        raise AssertionError("必须覆盖 12 套预置风格")
    if set(matrix["platform_results"]) != {"macos-arm64", "windows-x86_64"}:
        raise AssertionError("必须同时提供 macOS 与 Windows 结果")
    for target, result in matrix["platform_results"].items():
        if result["status"] != "passed" or not result["evidence_sha256"]:
            raise AssertionError(f"{target} 尚未形成真实通过证据")

    for relative_path in (
        "scripts/validate_shared_chromium.py",
        "tools/shared-browser-validation/pyproject.toml",
        "tools/shared-browser-validation/uv.lock",
        ".github/workflows/shared-chromium-validation.yml",
    ):
        require_file(relative_path)
    subprocess.run(
        [
            "uv",
            "run",
            "--project",
            "tools/shared-browser-validation",
            "--locked",
            "python",
            "scripts/validate_shared_chromium.py",
            "--check-contract",
        ],
        cwd=REPOSITORY_ROOT,
        check=True,
    )
    evidence = require_file("docs/development/EB-02.md")
    for phrase in (
        "# EB-02 完成证据",
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
            raise AssertionError(f"EB-02 独立证据缺少 {phrase}")
    roadmap = require_file("docs/embedded-browser-video-studio-roadmap.md")
    rows = [line for line in roadmap.splitlines() if line.startswith("| EB-02 |")]
    if len(rows) != 1 or not rows[0].endswith("| ✅ 已完成 |"):
        raise AssertionError("专项 Roadmap 中 EB-02 必须唯一且为已完成")
    print("EB-02 shared Chromium validation acceptance passed")


if __name__ == "__main__":
    try:
        main()
    except (AssertionError, KeyError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        print(f"EB-02 acceptance failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
