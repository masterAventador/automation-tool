#!/usr/bin/env python3
"""BM-02 isolated Node 22 Worker acceptance."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

from build_motion_video_worker_candidate import build_candidate, isolated_environment

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = ROOT / "vendor/hyperframes"
CONTRACT = ROOT / "contracts/quality/motion-video-worker-package.v1.json"


def git_status() -> tuple[str, str]:
    return (
        subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=UPSTREAM, text=True),
        subprocess.check_output(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=UPSTREAM,
            text=True,
        ),
    )


def require_contract() -> None:
    value = json.loads(CONTRACT.read_text(encoding="utf-8"))
    runtime = value.get("runtime")
    layout = value.get("packageLayout")
    if value.get("workerId") != "motion_composition_v1" or value.get("workerVersion") != "0.7.68":
        raise AssertionError("BM-02 Worker identity drifted")
    if not isinstance(runtime, dict) or (
        runtime.get("name") != "node"
        or runtime.get("version") != "22.23.1"
        or runtime.get("requiresGlobalRuntime") is not False
    ):
        raise AssertionError("BM-02 Node runtime lock drifted")
    if not isinstance(layout, dict) or (
        layout.get("runtimeExecutable") != "runtime/node"
        or layout.get("workerEntrypoint") != "app/worker.mjs"
        or layout.get("writesIntoUpstreamCheckout") is not False
    ):
        raise AssertionError("BM-02 package isolation drifted")


def require_real_candidate() -> None:
    before = git_status()
    with tempfile.TemporaryDirectory(prefix="bm02-acceptance-") as directory:
        package = Path(directory) / "motion-video-worker"
        audit = build_candidate(package)
        package = package.resolve(strict=True)
        if audit.node_version != "22.23.1" or audit.runtime_bytes < 50 * 1024 * 1024:
            raise AssertionError("BM-02 real packaged runtime audit is implausible")
        executable = package / "runtime" / ("node.exe" if os.name == "nt" else "node")
        worker = package / "app/worker.mjs"
        rejected = subprocess.run(
            [str(executable), str(worker)],
            env=isolated_environment(),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if rejected.returncode != 64 or rejected.stdout or rejected.stderr != (
            "Motion composition worker command is required\n"
        ):
            raise AssertionError("BM-02 packaged no-command boundary failed")
        environment = os.environ.copy()
        environment["BM02_PACKAGE_ROOT"] = str(package)
        subprocess.run(
            [
                "cargo",
                "test",
                "--manifest-path",
                "frontend/src-tauri/Cargo.toml",
                "--test",
                "local_video_orchestrator",
                "bundled_node_candidate_uses_packaged_runtime_and_protocol",
                "--",
                "--exact",
                "--nocapture",
            ],
            cwd=ROOT,
            env=environment,
            check=True,
            timeout=180,
        )
    if git_status() != before:
        raise AssertionError("BM-02 acceptance wrote into the upstream checkout")


def require_evidence() -> None:
    text = (ROOT / "docs/development/BM-02.md").read_text(encoding="utf-8")
    for heading in (
        "# BM-02 完成证据", "状态：🔍 待验收", "## RED", "## GREEN", "## 失败矩阵",
        "## 正常用户路径验收", "## 真实边界", "## 清理", "## 文档变化",
    ):
        if heading not in text:
            raise AssertionError(f"BM-02 evidence is missing {heading}")
    roadmap = (ROOT / "docs/embedded-browser-video-studio-roadmap.md").read_text(encoding="utf-8")
    rows = [line for line in roadmap.splitlines() if line.startswith("| BM-02 |")]
    if len(rows) != 1 or not rows[0].endswith("| 🔍 待验收 |"):
        raise AssertionError("BM-02 roadmap status is not pending native validation")


def main() -> int:
    require_contract()
    subprocess.run([sys.executable, "scripts/test_motion_video_worker.py"], cwd=ROOT, check=True)
    require_real_candidate()
    require_evidence()
    print(f"BM-02 {platform.system()} isolated Node 22 Worker acceptance passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
