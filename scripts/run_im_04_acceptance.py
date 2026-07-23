#!/usr/bin/env python3
"""IM-04 App model settings to real frozen material-video Worker acceptance."""

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
CONTRACT = ROOT / "contracts/video/material-video-script-model-adapter.v1.json"


def run(command: list[str], *, environment: dict[str, str] | None = None) -> None:
    subprocess.run(command, cwd=ROOT, env=environment, check=True)


def require_contract() -> None:
    value = json.loads(CONTRACT.read_text(encoding="utf-8"))
    if value != {
        "schemaVersion": 1,
        "sourceProvider": "bailian",
        "upstreamProvider": "openai",
        "protocol": "openai_chat_completions",
        "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "purpose": "script",
        "allowedModels": [
            "deepseek-v4-pro",
            "glm-5.2",
            "qwen3.7-max-2026-06-08",
        ],
        "credentialSource": "native_model_service_store",
        "credentialTransport": "private_worker_stdin_bootstrap",
        "writesUpstreamConfig": False,
        "exposesCredentialToReact": False,
        "exposesCredentialToWebUiDom": False,
        "logsCredentialOrRawProviderErrors": False,
        "connectionTestOwner": "native_model_service_settings",
    }:
        raise AssertionError("IM-04 model adapter contract drifted")


def require_frozen_adapter() -> MaterialVideoWorkerAudit:
    with tempfile.TemporaryDirectory(prefix="im04-acceptance-") as directory:
        candidate = Path(directory) / "material-video-worker"
        audit = build_candidate(candidate)
        candidate = candidate.resolve(strict=True)
        executable = candidate / (
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
        for forbidden in (
            candidate / "config.toml",
            candidate / "_internal/config.toml",
            candidate / "_internal/upstream/config.toml",
        ):
            if forbidden.exists():
                raise AssertionError(
                    f"IM-04 wrote upstream configuration: {forbidden.name}"
                )
        return audit


def require_evidence() -> None:
    text = (ROOT / "docs/development/IM-04.md").read_text(encoding="utf-8")
    for marker in (
        "# IM-04 完成证据",
        "状态：✅ 已完成",
        "## RED",
        "## GREEN",
        "## 失败矩阵",
        "## 正常用户路径验收",
        "## 真实边界",
        "## 清理",
    ):
        if marker not in text:
            raise AssertionError(f"IM-04 evidence is missing {marker}")
    roadmap = (ROOT / "docs/embedded-browser-video-studio-roadmap.md").read_text(
        encoding="utf-8"
    )
    rows = [line for line in roadmap.splitlines() if line.startswith("| IM-04 |")]
    if len(rows) != 1 or not rows[0].endswith("| ✅ 已完成 |"):
        raise AssertionError("IM-04 roadmap status is not completed")


def main() -> int:
    require_contract()
    run([sys.executable, "scripts/test_material_video_model_adapter.py"])
    run([sys.executable, "scripts/test_material_video_gateway.py"])
    run([sys.executable, "scripts/test_material_video_worker.py"])
    run(
        [
            "cargo",
            "test",
            "--manifest-path",
            "frontend/src-tauri/Cargo.toml",
            "--test",
            "model_service_settings",
            "--test",
            "local_video_orchestrator",
            "--locked",
        ]
    )
    audit = require_frozen_adapter()
    require_evidence()
    print(
        f"IM-04 {platform.system()} frozen model adapter acceptance passed: "
        f"{audit.file_count} files, {audit.package_bytes} bytes, "
        f"startup {audit.startup_seconds:.3f}s"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
