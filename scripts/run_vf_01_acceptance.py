#!/usr/bin/env python3
"""Verify VF-01 provider-neutral video domain contracts and evidence."""

from __future__ import annotations

import subprocess
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def require_file(relative_path: str) -> str:
    path = REPOSITORY_ROOT / relative_path
    if not path.is_file():
        raise AssertionError(f"缺少 VF-01 文件: {relative_path}")
    return path.read_text(encoding="utf-8")


def main() -> None:
    require_file("backend/src/automation_tool/control_plane/domain/video_creation.py")
    require_file("backend/tests/unit/control_plane/domain/test_video_creation.py")
    subprocess.run(
        [
            "uv",
            "run",
            "--project",
            "backend",
            "--locked",
            "pytest",
            "backend/tests/unit/control_plane/domain/test_video_creation.py",
            "-q",
        ],
        cwd=REPOSITORY_ROOT,
        check=True,
    )
    evidence = require_file("docs/development/VF-01.md")
    for phrase in (
        "# VF-01 完成证据",
        "状态：✅ 已完成",  # noqa: RUF001
        "## RED",
        "## GREEN",
        "## 失败矩阵",
        "## 正常用户路径验收",
        "## 真实边界",
        "## 清理",
        "## 文档变化",
        "## 遗留项",
    ):
        if phrase not in evidence:
            raise AssertionError(f"VF-01 独立证据缺少 {phrase}")
    roadmap = require_file("docs/embedded-browser-video-studio-roadmap.md")
    rows = [line for line in roadmap.splitlines() if line.startswith("| VF-01 |")]
    if len(rows) != 1 or not rows[0].endswith("| ✅ 已完成 |"):
        raise AssertionError("专项 Roadmap 中 VF-01 必须唯一且为已完成")
    print("VF-01 video domain contract acceptance passed")


if __name__ == "__main__":
    main()
