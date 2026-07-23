#!/usr/bin/env python3
"""IM-07 RenderJob/Artifact reconciliation and normal App-path acceptance."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from build_material_video_worker_candidate import build_candidate
from run_im_05_acceptance import require_normal_app_entry

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts/quality/material-render-job-reconciliation.v1.json"


def run(command: list[str]) -> None:
    import subprocess

    subprocess.run(command, cwd=ROOT, check=True)


def require_contract() -> None:
    value = json.loads(CONTRACT.read_text(encoding="utf-8"))
    if value.get("version") != "material-render-job-reconciliation.v1":
        raise AssertionError("IM-07 contract version drifted")
    if value.get("authority") != "tauri_app":
        raise AssertionError("IM-07 authority must remain in the App")
    if value.get("workerDeletionAllowed") is not False:
        raise AssertionError("IM-07 worker must not delete authoritative tasks")
    if value.get("maximumProjectedJobs") != 100:
        raise AssertionError("IM-07 projected job bound drifted")
    observation = value.get("workerObservation")
    if not isinstance(observation, dict) or observation.get("pathsAllowed") is not False:
        raise AssertionError("IM-07 observation must not expose paths")


def require_evidence() -> None:
    evidence = (ROOT / "docs/development/IM-07.md").read_text(encoding="utf-8")
    for marker in (
        "# IM-07 完成证据",
        "> 状态：🔍 待验收",
        "## RED",
        "## GREEN",
        "## 正常用户路径验收",
        "## 真实边界",
        "## 清理",
    ):
        if marker not in evidence:
            raise AssertionError(f"IM-07 evidence is missing {marker}")
    roadmap = (ROOT / "docs/embedded-browser-video-studio-roadmap.md").read_text(encoding="utf-8")
    rows = [line for line in roadmap.splitlines() if line.startswith("| IM-07 |")]
    if len(rows) != 1 or not rows[0].endswith("| 🔍 待验收 |"):
        raise AssertionError("IM-07 must stay pending until real generation credentials exist")


def main() -> int:
    require_contract()
    run([sys.executable, "scripts/test_material_video_worker.py"])
    run([
        "pnpm", "--dir", "frontend", "exec", "vitest", "run",
        "src/features/video-studio/VideoStudio.test.tsx",
        "src/platform/tauri/material-video-studio-gateway.test.ts",
    ])
    run([
        "cargo", "test", "--manifest-path", "frontend/src-tauri/Cargo.toml",
        "--locked", "material_video_studio", "--no-fail-fast",
    ])
    with tempfile.TemporaryDirectory(prefix="im07-acceptance-") as directory:
        candidate = Path(directory) / "material-video-worker"
        build_candidate(candidate)
        require_normal_app_entry(candidate)
    require_evidence()
    print("IM-07 deterministic reconciliation and normal App-path acceptance passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
