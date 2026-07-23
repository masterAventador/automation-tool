#!/usr/bin/env python3
"""IM-03 secure launcher and real frozen loopback gateway acceptance."""

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
)

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts/security/material-video-gateway.v1.json"


def run(command: list[str], *, environment: dict[str, str] | None = None) -> None:
    subprocess.run(command, cwd=ROOT, env=environment, check=True)


def require_contract() -> None:
    value = json.loads(CONTRACT.read_text(encoding="utf-8"))
    if (
        value.get("host") != "127.0.0.1"
        or value.get("port") != 0
        or value.get("sessionTokenBytes") != 32
        or value.get("maximumRequestBodyBytes") != 64 * 1024
        or value.get("rawLocalhostUrlsUserVisible") is not False
        or value.get("rawErrorsUserVisible") is not False
    ):
        raise AssertionError("IM-03 gateway boundary drifted")
    origins = value.get("allowedOrigins")
    if not isinstance(origins, list) or "*" in origins or len(origins) != 3:
        raise AssertionError("IM-03 CORS allowlist drifted")
    routes = value.get("routes")
    if not isinstance(routes, list) or len(routes) != 3:
        raise AssertionError("IM-03 route allowlist drifted")


def require_frozen_process() -> MaterialVideoWorkerAudit:
    with tempfile.TemporaryDirectory(prefix="im03-acceptance-") as directory:
        candidate = Path(directory) / "material-video-worker"
        audit = build_candidate(candidate)
        executable = candidate.resolve(strict=True) / (
            f"{ENTRYPOINT}.exe" if os.name == "nt" else ENTRYPOINT
        )
        environment = dict(os.environ)
        environment["AUTOMATION_TOOL_IM03_WORKER"] = str(executable)
        run(
            [
                "cargo",
                "test",
                "--manifest-path",
                "frontend/src-tauri/Cargo.toml",
                "--test",
                "material_video_gateway",
                "--locked",
                "--",
                "--test-threads=1",
            ],
            environment=environment,
        )
        if audit.file_count < 100 or audit.package_bytes < 100 * 1024 * 1024:
            raise AssertionError("IM-03 did not exercise the real frozen candidate")
        return audit


def require_evidence() -> None:
    text = (ROOT / "docs/development/IM-03.md").read_text(encoding="utf-8")
    for marker in (
        "# IM-03 完成证据",
        "状态：✅ 已完成",
        "## RED",
        "## GREEN",
        "## 失败矩阵",
        "## 正常用户路径验收",
        "## 真实边界",
        "## 清理",
    ):
        if marker not in text:
            raise AssertionError(f"IM-03 evidence is missing {marker}")
    roadmap = (ROOT / "docs/embedded-browser-video-studio-roadmap.md").read_text(
        encoding="utf-8"
    )
    rows = [line for line in roadmap.splitlines() if line.startswith("| IM-03 |")]
    if len(rows) != 1 or not rows[0].endswith("| ✅ 已完成 |"):
        raise AssertionError("IM-03 roadmap status is not completed")


def main() -> int:
    require_contract()
    run([sys.executable, "scripts/test_material_video_gateway.py"])
    run([sys.executable, "scripts/test_material_video_worker.py"])
    run(
        [
            "cargo",
            "test",
            "--manifest-path",
            "frontend/src-tauri/Cargo.toml",
            "--test",
            "local_video_orchestrator",
            "--locked",
            "--",
            "--test-threads=1",
        ]
    )
    audit = require_frozen_process()
    require_evidence()
    print(
        f"IM-03 {platform.system()} secure frozen gateway acceptance passed: "
        f"{audit.file_count} files, {audit.package_bytes} bytes, "
        f"startup {audit.startup_seconds:.3f}s"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
