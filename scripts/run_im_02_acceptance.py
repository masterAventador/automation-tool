#!/usr/bin/env python3
"""IM-02 isolated Python runtime and real frozen-candidate acceptance."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

from build_material_video_worker_candidate import (
    ENTRYPOINT,
    MaterialVideoWorkerAudit,
    build_candidate,
    expected_dependency_count,
    load_contract,
    probe_environment,
)

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = ROOT / "vendor/moneyprinterturbo"
CONTRACT = ROOT / "contracts/quality/material-video-worker-package.v1.json"


def git_status() -> tuple[str, str]:
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=UPSTREAM, text=True
    )
    status = subprocess.check_output(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=UPSTREAM,
        text=True,
    )
    return head, status


def require_contract() -> None:
    value = json.loads(CONTRACT.read_text(encoding="utf-8"))
    if value.get("workerId") != "material_montage_v1":
        raise AssertionError("IM-02 worker id drifted")
    build = value.get("build")
    python = value.get("python")
    license_policy = value.get("licensePolicy")
    if not isinstance(build, dict) or (
        build.get("mode") != "onedir"
        or build.get("sharesEnvironmentWithRpaExecutor") is not False
        or build.get("writesIntoUpstreamCheckout") is not False
    ):
        raise AssertionError("IM-02 isolation policy drifted")
    if not isinstance(python, dict) or python.get("requires") != ">=3.11,<3.12":
        raise AssertionError("IM-02 Python line drifted")
    if not isinstance(license_policy, dict) or (
        license_policy.get("inventoryRequired") is not True
        or license_policy.get("unknownLicenseAllowed") is not False
    ):
        raise AssertionError("IM-02 license policy drifted")


def require_real_candidate() -> MaterialVideoWorkerAudit:
    before = git_status()
    with tempfile.TemporaryDirectory(prefix="im02-acceptance-") as directory:
        candidate = Path(directory) / "material-video-worker"
        audit = build_candidate(candidate)
        if (
            audit.python_version != "3.11.15"
            or audit.dependency_count != expected_dependency_count(load_contract())
            or audit.file_count < 100
            or audit.package_bytes < 100 * 1024 * 1024
            or not 0 < audit.startup_seconds <= 30
        ):
            raise AssertionError("IM-02 real candidate audit is implausible")
        executable = candidate / (
            f"{ENTRYPOINT}.exe" if os.name == "nt" else ENTRYPOINT
        )
        rejected = subprocess.run(
            [str(executable)],
            cwd=candidate,
            env=probe_environment(),
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if (
            rejected.returncode != 64
            or rejected.stdout
            or rejected.stderr != "Material video worker command is required\n"
        ):
            raise AssertionError("IM-02 frozen process rejection boundary failed")
        inventory = json.loads(
            (
                candidate / "_internal/licenses/material-video-worker-dependencies.json"
            ).read_text(encoding="utf-8")
        )
        if any(not item.get("license") for item in inventory["distributions"]):
            raise AssertionError("IM-02 dependency license inventory is incomplete")
    if git_status() != before:
        raise AssertionError("IM-02 acceptance wrote into the upstream checkout")
    return audit


def require_evidence() -> None:
    evidence = ROOT / "docs/development/IM-02.md"
    text = evidence.read_text(encoding="utf-8")
    for heading in (
        "# IM-02 完成证据",
        "状态：✅ 已完成",
        "## RED",
        "## GREEN",
        "## 失败矩阵",
        "## 正常用户路径验收",
        "## 真实边界",
        "## 清理",
        "## 文档变化",
    ):
        if heading not in text:
            raise AssertionError(f"IM-02 evidence is missing {heading}")
    roadmap = (ROOT / "docs/embedded-browser-video-studio-roadmap.md").read_text(
        encoding="utf-8"
    )
    rows = [line for line in roadmap.splitlines() if line.startswith("| IM-02 |")]
    if len(rows) != 1 or not rows[0].endswith("| ✅ 已完成 |"):
        raise AssertionError("IM-02 roadmap status is not completed")


def main() -> int:
    require_contract()
    subprocess.run(
        [sys.executable, "scripts/test_material_video_worker.py"], cwd=ROOT, check=True
    )
    audit = require_real_candidate()
    require_evidence()
    print(
        f"IM-02 {platform.system()} isolated frozen Worker acceptance passed: "
        f"{audit.file_count} files, {audit.package_bytes} bytes, "
        f"startup {audit.startup_seconds:.3f}s, Python {audit.python_version}, "
        f"{audit.dependency_count} dependencies"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
