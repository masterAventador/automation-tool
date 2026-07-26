#!/usr/bin/env python3
"""BM-11 fixed-boundary tests for the 134-item motion catalog and rights ledger."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "scripts/check_motion_catalog.py"
CATALOG = ROOT / "contracts/quality/motion-catalog.v1.json"
RIGHTS = ROOT / "contracts/quality/motion-catalog-rights.v1.json"
LOCKED_COMMIT = "71d84ff27f1c2b2828f4fdf9015c3da4157140ee"
CATEGORIES = {
    "转场",
    "字幕",
    "代码演示",
    "画面内复杂效果",
    "人名与身份条",
    "数据与地图",
    "社交平台展示",
    "产品与案例展示",
    "文字效果",
    "流程图",
    "其他",
}
CONCLUSIONS = {
    "cleared",
    "needs_localization",
    "needs_asset_replacement",
    "needs_localization_and_asset_replacement",
}
REMOTE_CATEGORIES = {
    "jsdelivr",
    "google_fonts_css",
    "google_fonts_static",
    "cloudflare_cdn",
    "gstatic_draco",
    "placeholder_text",
}
LOCALIZATION_CATEGORIES = REMOTE_CATEGORIES - {"placeholder_text"}
ASSET_KINDS = {"image", "audio", "model_3d", "font", "vector", "script"}
REPLACEMENT_ASSET_KINDS = {"image", "audio", "model_3d", "font", "vector"}
GPU_TIERS = {"dom_css", "canvas_2d", "webgl", "webgpu"}
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def run_check(catalog: Path = CATALOG, rights: Path = RIGHTS) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECK), "--catalog", str(catalog), "--rights", str(rights)],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=ROOT,
    )


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict), f"{path.name} must contain an object"
    return value


def expect_check_failure(name: str, catalog: dict, rights: dict) -> None:
    with tempfile.TemporaryDirectory(prefix="automation-tool-bm11-test-") as temporary:
        catalog_path = Path(temporary) / "catalog.json"
        rights_path = Path(temporary) / "rights.json"
        catalog_path.write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")
        rights_path.write_text(json.dumps(rights, ensure_ascii=False), encoding="utf-8")
        result = run_check(catalog_path, rights_path)
        assert result.returncode != 0, f"{name}: tampered contract must fail"
        assert "motion catalog check failed" in result.stderr, f"{name}: {result.stderr}"


def expected_conclusion(entry: dict) -> str:
    needs_localization = bool(
        set(entry["remoteDependencies"]["categories"]) & LOCALIZATION_CATEGORIES
    )
    needs_replacement = bool(
        {asset["kind"] for asset in entry["bundledAssets"]} & REPLACEMENT_ASSET_KINDS
    ) or bool(entry["trademarkIndicators"])
    if needs_localization and needs_replacement:
        return "needs_localization_and_asset_replacement"
    if needs_localization:
        return "needs_localization"
    if needs_replacement:
        return "needs_asset_replacement"
    return "cleared"


def main() -> int:
    assert CHECK.is_file(), "scripts/check_motion_catalog.py is missing"
    assert CATALOG.is_file(), "contracts/quality/motion-catalog.v1.json is missing"
    assert RIGHTS.is_file(), "contracts/quality/motion-catalog-rights.v1.json is missing"

    green = run_check()
    assert green.returncode == 0, f"real contract must pass: {green.stderr}"
    assert "motion catalog" in green.stdout

    catalog = load(CATALOG)
    rights = load(RIGHTS)

    assert catalog["schemaVersion"] == 1
    assert catalog["source"]["commit"] == LOCKED_COMMIT
    counts = catalog["counts"]
    assert counts == {
        "total": 134,
        "blocks": 109,
        "components": 25,
        "officialPreview": 100,
    }, f"fixed counts drifted: {counts}"

    items = catalog["items"]
    assert len(items) == 134
    names = [item["name"] for item in items]
    assert len(set(names)) == 134, "item names must be unique"
    assert names == sorted(names), "items must be sorted by name"
    assert sum(item["type"] == "block" for item in items) == 109
    assert sum(item["type"] == "component" for item in items) == 25
    assert sum(bool(item["officialPreview"]) for item in items) == 100

    category_counter: dict[str, int] = {}
    for item in items:
        assert item["category"] in CATEGORIES, f"{item['name']} category is not closed"
        category_counter[item["category"]] = category_counter.get(item["category"], 0) + 1
        assert item["files"], f"{item['name']} must record installed files"
        for record in item["files"]:
            assert SHA256_PATTERN.fullmatch(record["sha256"]), (
                f"{item['name']} {record['path']} digest is invalid"
            )
            assert isinstance(record["bytes"], int) and record["bytes"] > 0
    assert set(catalog["categories"]) == CATEGORIES
    assert catalog["categoryCounts"] == category_counter
    assert sum(category_counter.values()) == 134

    assert rights["schemaVersion"] == 1
    assert rights["source"]["commit"] == LOCKED_COMMIT
    assert set(rights["conclusions"]) == CONCLUSIONS
    entries = rights["items"]
    assert len(entries) == 134
    assert [entry["name"] for entry in entries] == names, (
        "rights ledger must cover exactly the catalog items"
    )
    for entry in entries:
        assert entry["codeLicense"] == "Apache-2.0"
        assert set(entry["remoteDependencies"]["categories"]) <= REMOTE_CATEGORIES
        assert entry["gpuRequirement"] in GPU_TIERS
        assert {asset["kind"] for asset in entry["bundledAssets"]} <= ASSET_KINDS
        assert entry["conclusion"] in CONCLUSIONS
        assert entry["conclusion"] == expected_conclusion(entry), (
            f"{entry['name']} conclusion must follow the closed derivation rule"
        )
    stats = rights["stats"]
    assert stats["itemsWithRuntimeRemoteDependencies"] == sum(
        1
        for entry in entries
        if set(entry["remoteDependencies"]["categories"]) & LOCALIZATION_CATEGORIES
    )
    assert stats["conclusionCounts"] == {
        conclusion: sum(entry["conclusion"] == conclusion for entry in entries)
        for conclusion in sorted(CONCLUSIONS)
    }

    def clone() -> tuple[dict, dict]:
        return json.loads(json.dumps(catalog)), json.loads(json.dumps(rights))

    tampered_catalog, tampered_rights = clone()
    removed = tampered_catalog["items"].pop(0)
    expect_check_failure("missing item", tampered_catalog, tampered_rights)

    tampered_catalog, tampered_rights = clone()
    duplicate = json.loads(json.dumps(tampered_catalog["items"][0]))
    duplicate["name"] = "zz-unknown-part"
    tampered_catalog["items"].append(duplicate)
    expect_check_failure("unknown extra item", tampered_catalog, tampered_rights)

    tampered_catalog, tampered_rights = clone()
    tampered_catalog["items"][0]["files"][0]["sha256"] = "0" * 64
    expect_check_failure("digest drift", tampered_catalog, tampered_rights)

    tampered_catalog, tampered_rights = clone()
    target = tampered_catalog["items"][0]
    target["officialPreview"] = not target["officialPreview"]
    expect_check_failure("official preview drift", tampered_catalog, tampered_rights)

    tampered_catalog, tampered_rights = clone()
    tampered_catalog["items"][0]["category"] = "未知分类"
    expect_check_failure("category outside closed enum", tampered_catalog, tampered_rights)

    tampered_catalog, tampered_rights = clone()
    tampered_rights["items"].pop()
    expect_check_failure("rights ledger missing entry", tampered_catalog, tampered_rights)

    tampered_catalog, tampered_rights = clone()
    localized = next(
        entry
        for entry in tampered_rights["items"]
        if entry["conclusion"] != "cleared"
    )
    localized["conclusion"] = "cleared"
    expect_check_failure("conclusion rule violation", tampered_catalog, tampered_rights)

    tampered_catalog, tampered_rights = clone()
    tampered_rights["stats"]["itemsWithRuntimeRemoteDependencies"] += 1
    expect_check_failure("stats drift", tampered_catalog, tampered_rights)

    print(f"motion catalog tests passed: {removed['name']} tamper matrix rejected")
    print("executed checks: 9")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
