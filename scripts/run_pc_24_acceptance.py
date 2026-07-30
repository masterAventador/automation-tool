#!/usr/bin/env python3
"""PC-24 targeted real-render acceptance for build-time runtime-data inlining."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "backend/src"))

from check_motion_catalog_release import (  # noqa: E402
    OVERLAY_PATH,
    verify_release,
)
from run_bm_16_acceptance import (  # noqa: E402
    SWEEP_FRAMES,
    _render_once,
    _stage_chromium,
    _writable_release_copy,
)
from automation_tool.executor.motion_authoring.part_workspace import (  # noqa: E402
    referenced_assets,
)

TARGETS = (
    "spain-map",
    "us-map",
    "us-map-bubble",
    "us-map-flow",
    "world-map",
    "vfx-iphone-device",
)


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"{path} must contain an object")
    return value


def main() -> int:
    release_lock = load_json(ROOT / "contracts/video/motion-catalog-release.v1.json")
    dependency_lock = load_json(
        ROOT / "contracts/video/offline-motion-dependencies.v1.json"
    )
    catalog_contract = load_json(ROOT / "contracts/quality/motion-catalog.v1.json")
    overlay = load_json(OVERLAY_PATH)
    release = (
        ROOT
        / release_lock["layout"]["releaseRoot"]
        / release_lock["catalogVersion"]
    )
    manifest = verify_release(
        release,
        release_lock,
        dependency_lock,
        catalog_contract,
        overlay,
    )
    manifest_items = {item["name"]: item for item in manifest["items"]}
    catalog_items = {item["name"]: item for item in catalog_contract["items"]}

    with tempfile.TemporaryDirectory(prefix="automation-tool-pc24-") as temporary:
        run_root = Path(temporary)
        browser, chromium_major = _stage_chromium(run_root)
        workspace = _writable_release_copy(release, run_root / "workspace")
        results: dict[str, dict[str, object]] = {}
        for name in TARGETS:
            item = manifest_items[name]
            entries = [path for path in item["files"] if path.endswith(".html")]
            if len(entries) != 1:
                raise RuntimeError(f"{name}: expected one HTML entry, got {entries}")
            entry = entries[0]
            entry_path = workspace / entry
            referenced = referenced_assets(
                entry_path.read_text(encoding="utf-8"),
                catalog_root=workspace,
                origin=entry_path.parent,
                on_missing="skip",
            )
            allowed = sorted((set(item["files"]) | set(referenced)) - {entry})
            if len(allowed) > 128:
                raise RuntimeError(f"{name}: allowlist exceeds the sandbox maximum")
            catalog_item = catalog_items[name]
            duration = catalog_item.get("duration")
            dimensions = catalog_item.get("dimensions")
            overrides: dict[str, object] = {
                "sourceStartMillis": 0,
                "sourceEndMillis": int(duration * 1000) if duration else 3000,
            }
            if dimensions:
                overrides["canvas"] = {
                    "deviceScaleFactor": 1,
                    "height": dimensions["height"],
                    "width": dimensions["width"],
                }
            rendered = _render_once(
                browser,
                chromium_major,
                workspace,
                entry,
                allowed,
                SWEEP_FRAMES if duration else 1,
                spec_overrides=overrides,
            )
            blocked = rendered["event"]["blockedRequests"]
            distinct = len(set(rendered["frames"]))
            if blocked != 0:
                raise RuntimeError(f"{name}: runtime data caused {blocked} blocked requests")
            if duration and distinct < 2:
                raise RuntimeError(f"{name}: all rendered frames are identical")
            results[name] = {
                "blockedRequests": blocked,
                "distinctFrames": distinct,
            }
            print(
                f"[pc-24] rendered {name}: "
                f"{distinct}/{len(rendered['frames'])} distinct frames, "
                f"{blocked} blocked requests",
                flush=True,
            )
        shutil.rmtree(workspace / "frames", ignore_errors=True)

    if set(results) != set(TARGETS):
        raise RuntimeError("PC-24 did not cover all six runtime-data items")
    print("PC-24 targeted runtime-data acceptance passed: 6/6 real renders")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
