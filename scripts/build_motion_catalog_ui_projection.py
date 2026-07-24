#!/usr/bin/env python3
"""BM-15 deterministic UI projection builder.

Composes ``contracts/video/motion-catalog-ui.v1.json`` from the locked BM-11
catalog and rights ledgers plus the BM-13 overlay trademark replacements: 134
items with closed Chinese category/type/performance/device/applicability/
provenance labels and sanitized display titles. The projection is the only
catalog payload the frontend may import; upstream names, trademark indicator
forms, domains and URLs must never appear in it.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = REPOSITORY_ROOT / "contracts/quality/motion-catalog.v1.json"
RIGHTS_PATH = REPOSITORY_ROOT / "contracts/quality/motion-catalog-rights.v1.json"
OVERLAY_PATH = REPOSITORY_ROOT / "contracts/quality/motion-asset-overlay.v1.json"
RELEASE_LOCK_PATH = REPOSITORY_ROOT / "contracts/video/motion-catalog-release.v1.json"
PROJECTION_PATH = REPOSITORY_ROOT / "contracts/video/motion-catalog-ui.v1.json"

SCHEMA_VERSION = "1.0"
PROJECTION_ID = "automation-tool.motion-catalog-ui.v1"

TYPE_LABELS = {"block": "完整画面块", "component": "局部组件"}
PERFORMANCE_LABELS = {
    "dom_css": "轻量",
    "canvas_2d": "中等",
    "webgl": "较高",
    "webgpu": "高",
}
DEVICE_LABELS = {
    "dom_css": "任意设备",
    "canvas_2d": "普通显卡即可",
    "webgl": "需要支持 3D 加速的显卡",
    "webgpu": "需要较新显卡与系统",
}
PROVENANCE_LABELS = {
    "cleared": "无需调整",
    "needs_localization": "文字已本地化",
    "needs_asset_replacement": "示例素材已替换",
    "needs_localization_and_asset_replacement": "文字已本地化，示例素材已替换",
}
APPLICABILITY_LABELS = {
    "转场": "分镜之间的切换与节奏",
    "字幕": "台词与要点的同步展示",
    "代码演示": "代码讲解与技术教程",
    "画面内复杂效果": "画面强调与氛围效果",
    "人名与身份条": "人物介绍与身份说明",
    "数据与地图": "数据指标与地理信息",
    "社交平台展示": "社交内容与账号展示",
    "产品与案例展示": "产品功能与案例呈现",
    "文字效果": "标题与文字动画",
    "流程图": "流程与结构说明",
    "其他": "通用画面补充",
}


class ProjectionError(RuntimeError):
    """Raised when the projection cannot be composed safely."""


def _load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ProjectionError(f"{path.name} must contain an object")
    return value


def _boundary_pattern(literal: str) -> re.Pattern[str]:
    return re.compile(
        r"(?<![0-9A-Za-z])" + re.escape(literal) + r"(?![0-9A-Za-z])", re.IGNORECASE
    )


def _sanitize_title(
    title: str,
    replacements: dict[str, str],
    forms: dict[str, list[str]],
) -> str:
    sanitized = title
    for token in sorted(replacements):
        literals = forms.get(token)
        if not literals:
            raise ProjectionError(
                f"trademark indicator has no literal forms in the lock: {token}"
            )
        for literal in sorted(literals, key=len, reverse=True):
            sanitized = _boundary_pattern(literal).sub(replacements[token], sanitized)
    for literals in forms.values():
        for literal in literals:
            if _boundary_pattern(literal).search(sanitized) is not None:
                raise ProjectionError(
                    f"display title still contains an indicator form: {title!r}"
                )
    return sanitized


def compose_projection() -> dict:
    catalog = _load(CATALOG_PATH)
    rights = _load(RIGHTS_PATH)
    overlay = _load(OVERLAY_PATH)
    release_lock = _load(RELEASE_LOCK_PATH)

    forms = release_lock["trademarkScan"]["forms"]
    rights_by_name = {entry["name"]: entry for entry in rights["items"]}
    overlay_by_name = {entry["name"]: entry for entry in overlay["items"]}

    items = []
    for entry in sorted(catalog["items"], key=lambda value: value["name"]):
        name = entry["name"]
        audit = rights_by_name.get(name)
        if audit is None:
            raise ProjectionError(f"rights ledger is missing item: {name}")
        overlay_item = overlay_by_name.get(name)
        replacements = {
            rule["indicator"]: rule["replacement"]
            for rule in (
                overlay_item["trademarkReplacements"] if overlay_item else []
            )
        }
        category = entry["category"]
        if category not in APPLICABILITY_LABELS:
            raise ProjectionError(f"{name}: category outside the closed set")
        items.append(
            {
                "id": name,
                "displayTitle": _sanitize_title(entry["title"], replacements, forms),
                "typeLabel": TYPE_LABELS[entry["type"]],
                "category": category,
                "officialPreview": bool(entry["officialPreview"]),
                "performanceLabel": PERFORMANCE_LABELS[audit["gpuRequirement"]],
                "deviceRequirementLabel": DEVICE_LABELS[audit["gpuRequirement"]],
                "applicabilityLabel": APPLICABILITY_LABELS[category],
                "provenanceLabel": PROVENANCE_LABELS[audit["conclusion"]],
            }
        )

    return {
        "schemaVersion": SCHEMA_VERSION,
        "id": PROJECTION_ID,
        "counts": catalog["counts"],
        "categories": catalog["categories"],
        "categoryCounts": catalog["categoryCounts"],
        "items": items,
    }


def serialize_projection(projection: dict) -> str:
    return json.dumps(projection, ensure_ascii=False, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=PROJECTION_PATH)
    arguments = parser.parse_args()
    projection = compose_projection()
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    with open(arguments.output, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(serialize_projection(projection))
    print(
        "motion catalog ui projection written:",
        f"{len(projection['items'])} items,",
        f"{len(projection['categories'])} categories",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
