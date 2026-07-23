#!/usr/bin/env python3
"""Verify VF-03 private RenderJob workspaces and atomic Artifact import."""

from __future__ import annotations

import subprocess
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def require_file(relative_path: str) -> str:
    path = REPOSITORY_ROOT / relative_path
    if not path.is_file():
        raise AssertionError(f"缺少 VF-03 文件: {relative_path}")
    return path.read_text(encoding="utf-8")


def main() -> None:
    source = require_file("frontend/src-tauri/src/video_job_workspace.rs")
    tests = require_file("frontend/src-tauri/tests/video_job_workspace.rs")
    for phrase in (
        "VideoJobWorkspaceStore",
        "worker_output_directory",
        "save_checkpoint",
        "cleanup_expired",
        "import_output",
        "open_artifact",
        "statvfs",
        "GetDiskFreeSpaceExW",
    ):
        if phrase not in source:
            raise AssertionError(f"VF-03 存储边界缺少契约锚点: {phrase}")
    for phrase in (
        "isolates_jobs_and_atomically_imports_a_content_addressed_artifact",
        "checkpoint_survives_reopen_and_workspace_disposition_does_not_delete_artifacts",
        "rejects_traversal_links_replaced_workspaces_and_duplicate_names",
        "quota_and_free_space_failures_leave_no_partial_artifact_or_checkpoint",
        "retention_cleanup_preserves_active_jobs_and_initialization_recovers_partial_imports",
        "tampered_artifact_and_non_v4_job_fail_closed_without_path_reflection",
        "reopening_with_a_stricter_policy_rejects_existing_oversized_artifacts",
        "tauri_composition_root_owns_the_workspace_store_without_webview_path_commands",
    ):
        if phrase not in tests:
            raise AssertionError(f"VF-03 测试缺少失败矩阵锚点: {phrase}")

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
                "video_job_workspace",
            ],
            cwd=REPOSITORY_ROOT,
            check=True,
        )
    finally:
        if created_dist:
            frontend_dist.rmdir()

    evidence = require_file("docs/development/VF-03.md")
    for phrase in (
        "# VF-03 完成证据",
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
            raise AssertionError(f"VF-03 独立证据缺少 {phrase}")
    roadmap = require_file("docs/embedded-browser-video-studio-roadmap.md")
    rows = [line for line in roadmap.splitlines() if line.startswith("| VF-03 |")]
    if len(rows) != 1 or not rows[0].endswith("| ✅ 已完成 |"):
        raise AssertionError("专项 Roadmap 中 VF-03 必须唯一且为已完成")
    print("VF-03 video workspace acceptance passed")


if __name__ == "__main__":
    main()
