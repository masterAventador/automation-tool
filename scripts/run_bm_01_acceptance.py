#!/usr/bin/env python3
"""BM-01 locked upstream and twelve public motion-style preset contract."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = ROOT / "vendor/hyperframes"
CONTRACT = ROOT / "contracts/video/motion-style-presets.v1.json"
EXPECTED_COMMIT = "71d84ff27f1c2b2828f4fdf9015c3da4157140ee"
PUBLIC_IDS = {
    "biennale-yellow", "blockframe", "blue-professional", "bold-poster", "broadside",
    "capsule", "cartesian", "cobalt-grid", "coral", "creative-mode", "daisy-days",
    "editorial-forest",
}


def git(*arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(UPSTREAM), *arguments], capture_output=True, text=True, check=True,
    ).stdout.strip()


def require_contract() -> None:
    value = json.loads(CONTRACT.read_text(encoding="utf-8"))
    if set(value) != {
        "version", "upstreamVersion", "upstreamCommit", "publicPresetCount",
        "internalPresetsNotExposed", "presets",
    }:
        raise AssertionError("BM-01 contract schema drifted")
    if value["version"] != "motion-style-presets.v1" or value["upstreamVersion"] != "v0.7.68":
        raise AssertionError("BM-01 version drifted")
    if value["upstreamCommit"] != EXPECTED_COMMIT or value["publicPresetCount"] != 12:
        raise AssertionError("BM-01 upstream or public count drifted")
    if value["internalPresetsNotExposed"] != ["code-editorial"]:
        raise AssertionError("BM-01 internal-only preset policy drifted")
    presets = value["presets"]
    if not isinstance(presets, list) or len(presets) != 12:
        raise AssertionError("BM-01 must expose exactly twelve presets")
    if {item.get("id") for item in presets if isinstance(item, dict)} != PUBLIC_IDS:
        raise AssertionError("BM-01 public preset identifiers drifted")
    names = set()
    for item in presets:
        if set(item) != {"id", "displayName", "summary"}:
            raise AssertionError("BM-01 preset schema drifted")
        if not all(isinstance(item[key], str) and item[key].strip() for key in item):
            raise AssertionError("BM-01 preset copy is incomplete")
        if item["displayName"] in names or item["displayName"] == item["id"]:
            raise AssertionError("BM-01 Chinese display names must be unique")
        names.add(item["displayName"])


def require_upstream() -> None:
    if git("rev-parse", "HEAD") != EXPECTED_COMMIT:
        raise AssertionError("BM-01 submodule commit drifted")
    if git("describe", "--tags", "--exact-match") != "v0.7.68":
        raise AssertionError("BM-01 submodule tag drifted")
    if git("status", "--porcelain=v1", "--untracked-files=all"):
        raise AssertionError("BM-01 upstream worktree must stay clean")
    root = UPSTREAM / "skills/hyperframes-creative/frame-presets"
    installed = {path.name for path in root.iterdir() if path.is_dir()}
    if installed != PUBLIC_IDS | {"code-editorial"}:
        raise AssertionError("BM-01 installed preset inventory drifted")
    for preset in PUBLIC_IDS:
        directory = root / preset
        for name in ("FRAME.md", "caption-skin.html", "frame-showcase.html"):
            path = directory / name
            if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
                raise AssertionError(f"BM-01 preset asset missing: {preset}/{name}")


def require_evidence() -> None:
    evidence = (ROOT / "docs/development/BM-01.md").read_text(encoding="utf-8")
    for marker in (
        "# BM-01 完成证据", "> 状态：✅ 已完成", "## RED", "## GREEN",
        "## 失败矩阵", "## 正常用户路径验收", "## 真实边界", "## 遗留项",
    ):
        if marker not in evidence:
            raise AssertionError(f"BM-01 evidence is missing {marker}")
    roadmap = (ROOT / "docs/embedded-browser-video-studio-roadmap.md").read_text(encoding="utf-8")
    rows = [line for line in roadmap.splitlines() if line.startswith("| BM-01 |")]
    if len(rows) != 1 or not rows[0].endswith("| ✅ 已完成 |"):
        raise AssertionError("BM-01 roadmap status is not complete")


def main() -> int:
    require_contract()
    require_upstream()
    require_evidence()
    subprocess.run(["python3", "scripts/check_embedded_browser_video_roadmap.py"], cwd=ROOT, check=True)
    print("BM-01 locked upstream and twelve public motion styles passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
