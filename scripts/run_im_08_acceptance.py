#!/usr/bin/env python3
"""IM-08 readiness gates; real evidence remains an explicit external input."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts/quality/material-video-formal-acceptance.v1.json"


def run(command: list[str]) -> None:
    subprocess.run(command, cwd=ROOT, check=True)


def require_contract() -> None:
    value = json.loads(CONTRACT.read_text(encoding="utf-8"))
    if value.get("version") != "material-video-formal-acceptance.v1":
        raise AssertionError("IM-08 contract version drifted")
    if value.get("sampleKinds") != ["knowledge_explainer", "news_summary", "ranked_list"]:
        raise AssertionError("IM-08 representative samples drifted")
    if set(value.get("requiredPlatforms", [])) != {"macos-arm64", "windows-x86_64"}:
        raise AssertionError("IM-08 formal platforms drifted")
    if value.get("mockOrFixtureMayCompleteTask") is not False:
        raise AssertionError("IM-08 must never complete from mock or fixture evidence")


def require_evidence_document() -> None:
    evidence = (ROOT / "docs/development/IM-08.md").read_text(encoding="utf-8")
    for marker in (
        "# IM-08 完成证据", "> 状态：🔍 待验收", "## RED", "## GREEN",
        "## 失败矩阵", "## 正常用户路径验收", "## 真实边界", "## 遗留项",
    ):
        if marker not in evidence:
            raise AssertionError(f"IM-08 evidence is missing {marker}")
    roadmap = (ROOT / "docs/embedded-browser-video-studio-roadmap.md").read_text(encoding="utf-8")
    rows = [line for line in roadmap.splitlines() if line.startswith("| IM-08 |")]
    if len(rows) != 1 or not rows[0].endswith("| 🔍 待验收 |"):
        raise AssertionError("IM-08 must remain pending until formal evidence passes")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--formal-evidence", type=Path)
    parser.add_argument("--ffprobe", default="ffprobe")
    arguments = parser.parse_args()
    require_contract()
    run([sys.executable, "scripts/test_im_08_evidence_verifier.py"])
    run([sys.executable, "scripts/check_embedded_browser_video_roadmap.py"])
    require_evidence_document()
    if arguments.formal_evidence is not None:
        run([
            sys.executable, "scripts/verify_im_08_formal_evidence.py",
            str(arguments.formal_evidence), "--ffprobe", arguments.ffprobe,
        ])
    print("IM-08 readiness gates passed; formal real-world evidence is still required")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
