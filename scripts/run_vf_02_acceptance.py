#!/usr/bin/env python3
"""Verify VF-02 authenticated local video worker lifecycle and evidence."""

from __future__ import annotations

import subprocess
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def require_file(relative_path: str) -> str:
    path = REPOSITORY_ROOT / relative_path
    if not path.is_file():
        raise AssertionError(f"缺少 VF-02 文件: {relative_path}")
    return path.read_text(encoding="utf-8")


def main() -> None:
    source = require_file("frontend/src-tauri/src/local_video_orchestrator.rs")
    require_file("frontend/src-tauri/src/managed_process_tree.rs")
    tests = require_file("frontend/src-tauri/tests/local_video_orchestrator.rs")
    for phrase in (
        "VideoWorkerKind",
        "VideoWorkerRestartPolicy",
        "127.0.0.1",
        "local_session_token",
        "worker.cancel",
        "worker.health",
    ):
        if phrase not in source:
            raise AssertionError(f"VF-02 生命周期缺少契约锚点: {phrase}")
    for phrase in (
        "starts_python_and_node_workers_on_distinct_authenticated_loopback_ports",
        "rejects_forged_ready_proof_and_cleans_the_failed_process",
        "startup_timeout_fails_closed",
        "crash_recovery_kills_descendants_before_joining_worker_pipes",
        "stops_after_the_crash_recovery_budget_is_exhausted",
        "tauri_composition_root_owns_the_orchestrator_without_a_webview_command",
    ):
        if phrase not in tests:
            raise AssertionError(f"VF-02 测试缺少失败矩阵锚点: {phrase}")

    frontend_dist = REPOSITORY_ROOT / "frontend" / "dist"
    created_dist = not frontend_dist.exists()
    frontend_dist.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [
                "cargo",
                "test",
                "--manifest-path",
                "frontend/src-tauri/Cargo.toml",
                "--test",
                "local_video_orchestrator",
            ],
            cwd=REPOSITORY_ROOT,
            check=True,
        )
    finally:
        if created_dist:
            frontend_dist.rmdir()

    evidence = require_file("docs/development/VF-02.md")
    for phrase in (
        "# VF-02 完成证据",
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
            raise AssertionError(f"VF-02 独立证据缺少 {phrase}")
    roadmap = require_file("docs/embedded-browser-video-studio-roadmap.md")
    rows = [line for line in roadmap.splitlines() if line.startswith("| VF-02 |")]
    if len(rows) != 1 or not rows[0].endswith("| ✅ 已完成 |"):
        raise AssertionError("专项 Roadmap 中 VF-02 必须唯一且为已完成")
    print("VF-02 local video orchestrator acceptance passed")


if __name__ == "__main__":
    main()
