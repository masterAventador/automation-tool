#!/usr/bin/env python3
"""IM-06 product theme, identity isolation, and normal App-path acceptance."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from build_material_video_worker_candidate import build_candidate
from run_im_05_acceptance import require_normal_app_entry

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts/quality/material-video-studio-theme.v1.json"
INIT_SCRIPT = ROOT / "frontend/src-tauri/src/material_video_studio_init.js"

# Every guard mechanism IM-06 pins in the embedded studio window. This is a
# coverage declaration, not a sample: `test_material_studio_guard_declaration.py`
# re-derives the pipeline steps and the fail-closed reasons from the script
# itself and fails when this list falls behind them, so a guard cannot be added
# to the window without being pinned here.
INITIALIZATION_GUARD_MARKERS = (
    # Window state and the fail-closed panel the user actually sees.
    "data-automation-tool-studio-state",
    "制作界面暂时不可用",
    "window.top !== window.self",
    # The reconcile pipeline, step by step.
    "installTheme",
    "removeTour",
    "sanitizeTextAndAccessibility",
    "removeExternalNavigation",
    "productizeHeaderAndSettings",
    "audit",
    "hasRequiredStructure",
    # Every way the guard can fail closed.
    "content_policy_",
    "initialization_error",
    "structure_timeout",
    # Product identity and settings ownership, also pinned by the Rust theme test.
    "制作服务设置",
    "素材\\s*API",
    "120_000",
)


def run(command: list[str]) -> None:
    subprocess.run(command, cwd=ROOT, check=True)


def require_contract() -> None:
    value = json.loads(CONTRACT.read_text(encoding="utf-8"))
    if value.get("version") != "material-video-studio-theme.v1":
        raise AssertionError("IM-06 theme contract version drifted")
    if value.get("productTitle") != "智能素材成片":
        raise AssertionError("IM-06 product title drifted")
    if value.get("settingsTitle") != "制作服务设置":
        raise AssertionError("IM-06 settings title drifted")
    if value.get("externalNavigationAllowed") is not False:
        raise AssertionError("IM-06 external navigation must stay disabled")
    if value.get("structureChangePolicy") != "fail_closed":
        raise AssertionError("IM-06 structure changes must fail closed")
    if value.get("structureReadyTimeoutMilliseconds") != 120_000:
        raise AssertionError("IM-06 cold-start structure timeout drifted")
    policy = value.get("settingsPolicy")
    if not isinstance(policy, dict):
        raise AssertionError("IM-06 settings policy is missing")
    expected = {
        "appOwnsModelService": True,
        "embeddedModelSettingsVisible": False,
        "materialApiSettingsVisible": True,
        "cacheSettingsVisible": True,
        "interfaceSettingsVisible": True,
    }
    if policy != expected:
        raise AssertionError("IM-06 settings ownership policy drifted")


def require_initialization_guard() -> None:
    source = INIT_SCRIPT.read_text(encoding="utf-8")
    for marker in INITIALIZATION_GUARD_MARKERS:
        if marker not in source:
            raise AssertionError(f"IM-06 initialization guard is missing {marker}")


def require_evidence() -> None:
    evidence = (ROOT / "docs/development/IM-06.md").read_text(encoding="utf-8")
    for marker in (
        "# IM-06 完成证据",
        "状态：✅ 已完成",
        "## RED",
        "## GREEN",
        "## 失败矩阵",
        "## 正常用户路径验收",
        "## 真实边界",
        "## 清理",
    ):
        if marker not in evidence:
            raise AssertionError(f"IM-06 evidence is missing {marker}")
    roadmap = (ROOT / "docs/embedded-browser-video-studio-roadmap.md").read_text(
        encoding="utf-8"
    )
    rows = [line for line in roadmap.splitlines() if line.startswith("| IM-06 |")]
    if len(rows) != 1 or not rows[0].endswith("| ✅ 已完成 |"):
        raise AssertionError("IM-06 roadmap status is not complete")


def main() -> int:
    require_contract()
    require_initialization_guard()
    run(
        [
            "pnpm",
            "--dir",
            "frontend",
            "exec",
            "vitest",
            "run",
            "src/platform/tauri/material-video-studio-theme.test.ts",
        ]
    )
    run(
        [
            "cargo",
            "test",
            "--manifest-path",
            "frontend/src-tauri/Cargo.toml",
            "--locked",
            "material_video_studio::theme_tests",
        ]
    )
    run([sys.executable, "scripts/check_user_facing_branding.py"])
    with tempfile.TemporaryDirectory(prefix="im06-acceptance-") as directory:
        candidate = Path(directory) / "material-video-worker"
        build_candidate(candidate)
        require_normal_app_entry(candidate)
    require_evidence()
    print("IM-06 product theme and normal App-path acceptance passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
