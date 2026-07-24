#!/usr/bin/env python3
"""BM-15 UI projection contract tests.

Proves ``contracts/video/motion-catalog-ui.v1.json`` is a deterministic,
user-safe projection of the locked BM-11 catalog and rights ledgers: 134
items with closed Chinese category/type/performance/device/applicability/
provenance labels, sanitized display titles, and zero upstream names,
trademark indicator forms, domains or URLs in the frontend-facing payload.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "scripts/build_motion_catalog_ui_projection.py"
CHECK = ROOT / "scripts/check_motion_catalog_ui_projection.py"
PROJECTION = ROOT / "contracts/video/motion-catalog-ui.v1.json"
CATALOG = ROOT / "contracts/quality/motion-catalog.v1.json"
RIGHTS = ROOT / "contracts/quality/motion-catalog-rights.v1.json"
OVERLAY = ROOT / "contracts/quality/motion-asset-overlay.v1.json"
RELEASE_LOCK = ROOT / "contracts/video/motion-catalog-release.v1.json"

TYPE_LABELS = {"完整画面块", "局部组件"}
PERFORMANCE_LABELS = {"轻量", "中等", "较高", "高"}
DEVICE_LABELS = {
    "任意设备",
    "普通显卡即可",
    "需要支持 3D 加速的显卡",
    "需要较新显卡与系统",
}
PROVENANCE_LABELS = {
    "无需调整",
    "文字已本地化",
    "示例素材已替换",
    "文字已本地化，示例素材已替换",
}
UPSTREAM_WORDS = re.compile(
    r"(?<![0-9A-Za-z])(hyperframes|moneyprinterturbo|heygen|apple|ios|iphone|macbook"
    r"|macos|vscode|vs code|visual studio|sf pro|sf-pro|sfpro|spotify|tiktok"
    r"|twitter|youtube|instagram|reddit|bild)(?![0-9A-Za-z])",
    re.IGNORECASE,
)
URL_MARKERS = ("http://", "https://", "://", "www.", ".com", ".net", ".org", ".dev")


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict), f"{path.name} must contain an object"
    return value


def run_check(projection: Path = PROJECTION) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECK), "--projection", str(projection)],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=ROOT,
    )


def expect_check_failure(name: str, projection: dict) -> None:
    with tempfile.TemporaryDirectory(
        prefix="automation-tool-bm15-test-"
    ) as temporary:
        path = Path(temporary) / "projection.json"
        path.write_text(json.dumps(projection, ensure_ascii=False), encoding="utf-8")
        result = run_check(path)
        assert result.returncode != 0, f"{name}: tampered projection must fail"
        assert "motion catalog ui projection check failed" in result.stderr, (
            f"{name}: {result.stderr}"
        )


def main() -> int:
    assert BUILD.is_file(), "scripts/build_motion_catalog_ui_projection.py is missing"
    assert CHECK.is_file(), "scripts/check_motion_catalog_ui_projection.py is missing"
    assert PROJECTION.is_file(), "contracts/video/motion-catalog-ui.v1.json is missing"

    # The committed projection must pass its own black-box gate.
    passing = run_check()
    assert passing.returncode == 0, f"committed projection must pass: {passing.stderr}"

    # Determinism: rebuilding into a scratch path reproduces identical bytes.
    with tempfile.TemporaryDirectory(prefix="automation-tool-bm15-test-") as temporary:
        rebuilt = Path(temporary) / "projection.json"
        build = subprocess.run(
            [sys.executable, str(BUILD), "--output", str(rebuilt)],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=ROOT,
        )
        assert build.returncode == 0, f"builder must succeed: {build.stderr}"
        assert rebuilt.read_bytes() == PROJECTION.read_bytes(), (
            "rebuilt projection must be byte-identical to the committed contract"
        )

    projection = load(PROJECTION)
    catalog = load(CATALOG)
    rights = load(RIGHTS)

    # Closed counts mirrored from the locked catalog.
    assert projection["counts"] == catalog["counts"], "counts must mirror the catalog"
    items = projection["items"]
    assert len(items) == 134, "projection must expose exactly 134 items"
    assert projection["categories"] == catalog["categories"], (
        "Chinese categories must mirror the locked catalog order"
    )

    ids = [item["id"] for item in items]
    assert ids == sorted(ids), "items must be sorted by id for determinism"
    assert len(set(ids)) == 134, "item ids must be unique"
    assert set(ids) == {entry["name"] for entry in catalog["items"]}, (
        "projection ids must equal the locked catalog ids"
    )

    rights_by_name = {entry["name"]: entry for entry in rights["items"]}
    gpu_to_performance = {
        "dom_css": "轻量",
        "canvas_2d": "中等",
        "webgl": "较高",
        "webgpu": "高",
    }
    # Item ids are locked catalog reference keys and never rendered to users;
    # all remaining fields are user-visible text and must stay clean.
    displayed = json.loads(json.dumps(projection))
    for entry in displayed["items"]:
        del entry["id"]
    serialized = json.dumps(displayed, ensure_ascii=False)
    assert UPSTREAM_WORDS.search(serialized) is None, (
        "projection must not contain upstream or trademark indicator words"
    )
    assert not any(marker in serialized for marker in URL_MARKERS), (
        "projection must not contain domains or URLs"
    )

    for item in items:
        assert set(item) == {
            "id",
            "displayTitle",
            "typeLabel",
            "category",
            "officialPreview",
            "performanceLabel",
            "deviceRequirementLabel",
            "applicabilityLabel",
            "provenanceLabel",
        }, f"{item.get('id')}: closed key set"
        assert item["typeLabel"] in TYPE_LABELS
        assert item["category"] in projection["categories"]
        assert item["performanceLabel"] in PERFORMANCE_LABELS
        assert item["deviceRequirementLabel"] in DEVICE_LABELS
        assert item["provenanceLabel"] in PROVENANCE_LABELS
        assert isinstance(item["officialPreview"], bool)
        assert (
            isinstance(item["displayTitle"], str)
            and 1 <= len(item["displayTitle"]) <= 120
        )
        assert (
            isinstance(item["applicabilityLabel"], str)
            and 1 <= len(item["applicabilityLabel"]) <= 60
        )
        entry = rights_by_name[item["id"]]
        assert item["performanceLabel"] == gpu_to_performance[entry["gpuRequirement"]], (
            f"{item['id']}: performance label must derive from the audited GPU tier"
        )

    # Every Chinese category groups at least one item and category counts match.
    grouped: dict[str, int] = {}
    for item in items:
        grouped[item["category"]] = grouped.get(item["category"], 0) + 1
    assert grouped == catalog["categoryCounts"], (
        "per-category counts must mirror the locked catalog"
    )

    def clone() -> dict:
        return json.loads(json.dumps(projection))

    tampered = clone()
    tampered["items"][0]["displayTitle"] = "Apple Money Count"
    expect_check_failure("indicator title reintroduced", tampered)

    tampered = clone()
    tampered["items"].pop()
    expect_check_failure("missing item", tampered)

    tampered = clone()
    tampered["items"][0]["performanceLabel"] = "极高"
    expect_check_failure("performance label outside closed set", tampered)

    tampered = clone()
    tampered["items"][0]["category"] = "未知分类"
    expect_check_failure("category outside closed set", tampered)

    tampered = clone()
    tampered["items"][0]["provenanceLabel"] = "无需调整" if (
        projection["items"][0]["provenanceLabel"] != "无需调整"
    ) else "文字已本地化"
    expect_check_failure("provenance label drift", tampered)

    tampered = clone()
    tampered["counts"] = dict(tampered["counts"], total=133)
    expect_check_failure("count drift", tampered)

    tampered = clone()
    tampered["items"][1]["officialPreview"] = not tampered["items"][1]["officialPreview"]
    expect_check_failure("official preview flag flipped", tampered)

    print("BM-15 motion catalog ui projection tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
