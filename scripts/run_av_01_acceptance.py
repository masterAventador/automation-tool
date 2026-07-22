#!/usr/bin/env python3
"""Verify the AV-01 embedded-browser architecture baseline."""

from __future__ import annotations

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ADR_PATH = REPOSITORY_ROOT / "docs" / "adr" / "0001-embedded-chromium-runtime.md"
AV_01_EVIDENCE = (
    REPOSITORY_ROOT / "docs" / "development" / "AV-01.md"
)


def read(relative_path: str) -> str:
    return (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")


def require_phrases(relative_path: str, phrases: tuple[str, ...]) -> None:
    document = read(relative_path)
    missing = [phrase for phrase in phrases if phrase not in document]
    if missing:
        joined = "、".join(missing)
        raise AssertionError(f"{relative_path} 缺少 AV-01 基线：{joined}")


def require_absent(relative_path: str, phrases: tuple[str, ...]) -> None:
    document = read(relative_path)
    present = [phrase for phrase in phrases if phrase in document]
    if present:
        joined = "、".join(present)
        raise AssertionError(f"{relative_path} 仍包含冲突基线：{joined}")


def main() -> None:
    if not ADR_PATH.is_file():
        raise AssertionError("AV-01 缺少内置 Chromium ADR")
    if not AV_01_EVIDENCE.is_file():
        raise AssertionError("AV-01 缺少独立完成证据文件")

    require_phrases(
        "docs/adr/0001-embedded-chromium-runtime.md",
        (
            "状态：已接受",
            "替代 ADR-P002",
            "Playwright 锁定版本严格匹配",
            "全新的 App 私有运营 Profile",
            "人工接管",
            "首期边界",
            "不读取、复制或迁移用户默认浏览器",
        ),
    )
    require_phrases(
        "CLAUDE.md",
        (
            "专项 Roadmap 例外",
            "内置 Chromium",
            "用户电脑无需预装 Chrome 或 Edge",
            "正式 App 的正常用户入口",
            "docs/development/<任务ID>.md",
        ),
    )
    require_phrases(
        "docs/product-plan.md",
        (
            "ADR-P002：内置运营浏览器（替代原决策）",
            "App 安装包内置并校验 Chromium",
            "全新的 App 私有运营 Profile",
        ),
    )
    require_absent(
        "docs/product-plan.md",
        (
            "ADR-P002：不内嵌运营浏览器",
            "决策：使用可见的外部 Chrome/Edge 窗口",
            "Chrome/Edge 检测和选择",
        ),
    )
    require_phrases(
        "docs/project-structure.md",
        (
            "内置 Chromium 发行物",
            "浏览器 Manifest 与逐文件摘要",
            "不接受用户提供的浏览器可执行路径",
        ),
    )
    require_absent(
        "docs/project-structure.md",
        ("└── 系统 Chrome/Edge + App 独立运营 Profile",),
    )
    require_phrases(
        "docs/frontend-architecture.md",
        (
            "浏览器组件正常",
            "浏览器组件损坏",
            "浏览器组件版本不兼容",
        ),
    )
    require_phrases(
        "docs/backend-architecture.md",
        (
            "已验证的内置 Chromium",
            "浏览器可执行路径不得来自用户输入",
            "全新运营 Profile",
        ),
    )
    require_phrases(
        "docs/embedded-browser-video-studio-roadmap.md",
        (
            "| AV-01 |",
            "docs/development/<任务ID>.md",
        ),
    )
    roadmap = read("docs/embedded-browser-video-studio-roadmap.md")
    av_01_rows = [line for line in roadmap.splitlines() if line.startswith("| AV-01 |")]
    if len(av_01_rows) != 1 or not av_01_rows[0].endswith("| ✅ 已完成 |"):
        raise AssertionError("专项 Roadmap 中 AV-01 必须唯一且为已完成")
    require_phrases(
        "docs/development/AV-01.md",
        (
            "# AV-01 完成证据",
            "状态：✅ 已完成",
            "提交：",
            "## RED",
            "## GREEN",
            "## 失败矩阵",
            "## 正常用户路径验收",
            "## 真实边界",
            "## 清理",
        ),
    )
    print("AV-01 embedded-browser architecture baseline acceptance passed")


if __name__ == "__main__":
    main()
