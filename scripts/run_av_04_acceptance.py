#!/usr/bin/env python3
"""Verify AV-04 specialized roadmap and per-task evidence gates."""

from __future__ import annotations

import subprocess
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def require_file(relative_path: str) -> str:
    path = REPOSITORY_ROOT / relative_path
    if not path.is_file():
        raise AssertionError(f"缺少 AV-04 文件: {relative_path}")
    return path.read_text(encoding="utf-8")


def main() -> None:
    require_file("scripts/check_embedded_browser_video_roadmap.py")
    evidence = require_file("docs/development/AV-04.md")
    for phrase in (
        "# AV-04 完成证据",
        "状态：✅ 已完成",
        "## RED",
        "## GREEN",
        "## 失败矩阵",
        "## 正常用户路径验收",
        "## 真实边界",
        "## 清理",
        "## 遗留项",
    ):
        if phrase not in evidence:
            raise AssertionError(f"AV-04 独立证据缺少 {phrase}")
    roadmap = require_file("docs/embedded-browser-video-studio-roadmap.md")
    rows = [line for line in roadmap.splitlines() if line.startswith("| AV-04 |")]
    if len(rows) != 1 or not rows[0].endswith("| ✅ 已完成 |"):
        raise AssertionError("专项 Roadmap 中 AV-04 必须唯一且为已完成")
    subprocess.run(
        ["python3", "scripts/check_embedded_browser_video_roadmap.py", "--self-test"],
        cwd=REPOSITORY_ROOT,
        check=True,
    )
    subprocess.run(
        ["python3", "scripts/check_embedded_browser_video_roadmap.py"],
        cwd=REPOSITORY_ROOT,
        check=True,
    )
    print("AV-04 specialized roadmap and evidence acceptance passed")


if __name__ == "__main__":
    main()
