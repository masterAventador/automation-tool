#!/usr/bin/env python3
"""Fail closed when the fixed 134-item motion catalog or its rights ledger drifts.

The locked ``vendor/hyperframes`` submodule is the only scan source. This check
recomputes every mechanical fact (per-file SHA-256, sizes, runtime remote
dependencies, fonts, bundled assets, GPU tier, trademark indicators and the
closed per-item conclusion) and rejects any drift between the submodule, the
catalog contract and the rights ledger. ``--write`` regenerates both contracts
from the same deterministic rules; curated Chinese categories are fixed below.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = REPOSITORY_ROOT / "contracts/quality/motion-catalog.v1.json"
RIGHTS_PATH = REPOSITORY_ROOT / "contracts/quality/motion-catalog-rights.v1.json"
SOURCE_LOCK_PATH = REPOSITORY_ROOT / "contracts/quality/third-party-sources.v1.json"
SUBMODULE_ROOT = REPOSITORY_ROOT / "vendor/hyperframes"
REGISTRY_ROOT = SUBMODULE_ROOT / "registry"
OFFICIAL_INDEX_PATH = SUBMODULE_ROOT / "docs/public/catalog-index.json"

EXPECTED_COUNTS = {"total": 134, "blocks": 109, "components": 25, "officialPreview": 100}
CATEGORIES = (
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
)
CONCLUSIONS = (
    "cleared",
    "needs_localization",
    "needs_asset_replacement",
    "needs_localization_and_asset_replacement",
)
TEXT_SUFFIXES = frozenset({".html", ".js", ".svg", ".css"})
URL_PATTERN = re.compile(r"https?://[^\s\"'`<>)\\]+")
IGNORED_DOMAINS = frozenset({"www.w3.org"})
DOMAIN_CATEGORIES = {
    "cdn.jsdelivr.net": "jsdelivr",
    "fonts.googleapis.com": "google_fonts_css",
    "fonts.gstatic.com": "google_fonts_static",
    "cdnjs.cloudflare.com": "cloudflare_cdn",
    "www.gstatic.com": "gstatic_draco",
    "api.example.com": "placeholder_text",
}
LOCALIZATION_CATEGORIES = frozenset(
    {"jsdelivr", "google_fonts_css", "google_fonts_static", "cloudflare_cdn", "gstatic_draco"}
)
ASSET_KINDS_BY_SUFFIX = {
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".wav": "audio",
    ".glb": "model_3d",
    ".woff2": "font",
    ".svg": "vector",
    ".js": "script",
}
REPLACEMENT_ASSET_KINDS = frozenset({"image", "audio", "model_3d", "font", "vector"})
GOOGLE_FONT_FAMILY_PATTERN = re.compile(r"family=([^&:]+)")
MAP_DATA_PATTERN = re.compile(r"/npm/(?:us-atlas|es-atlas|world-atlas)@")
JSDELIVR_PACKAGE_PATTERN = re.compile(r"https://cdn\.jsdelivr\.net/npm/((?:@[^/@]+/)?[^/@]+)")
FONT_STACK_NOISE = ("-apple-system", "apple color emoji")
BRAND_PATTERNS = (
    ("apple", r"\bapple\b"),
    ("bild", r"\bbild\b"),
    ("heygen", r"\bheygen\b"),
    ("hyperframes", r"\bhyperframes\b"),
    ("instagram", r"\binstagram\b"),
    ("ios", r"\bios ?26\b|\bios\b"),
    ("iphone", r"\biphone\b"),
    ("macbook", r"\bmacbook\b"),
    ("macos", r"\bmacos\b"),
    ("reddit", r"\breddit\b"),
    ("sf_pro", r"\bsf pro\b"),
    ("sketchfab", r"\bsketchfab\b"),
    ("spotify", r"\bspotify\b"),
    ("tiktok", r"\btiktok\b"),
    ("twitter", r"\btwitter\b"),
    ("visual_studio", r"\bvisual studio\b"),
    ("vscode", r"\bvs ?code\b"),
    ("youtube", r"\byoutube\b"),
)
PROVENANCE_MARKERS = (("creative_commons", b"creativecommons"), ("sketchfab", b"sketchfab"))
GPU_TIERS = ("dom_css", "canvas_2d", "webgl", "webgpu")
WEBGPU_PATTERN = re.compile(r"webgpu|navigator\.gpu")
WEBGL_PATTERN = re.compile(r"webgl|three[@/.]")
CANVAS_2D_PATTERN = re.compile(r"getcontext\(\s*['\"]2d")
REMOTE_PACKAGE_LICENSES = {
    "gsap": "GSAP Standard License (Webflow)",
    "three": "MIT",
    "three.js": "MIT",
    "d3": "ISC",
    "topojson-client": "ISC",
    "us-atlas": "ISC",
    "es-atlas": "unverified",
    "world-atlas": "ISC",
    "draco-decoder": "Apache-2.0",
}

# Fixed Chinese product categories for all 134 installable parts, following the
# specialized roadmap section 6.6 groups plus the dedicated code-demo group.
CURATED_CATEGORIES = {
    "app-showcase": "产品与案例展示",
    "apple-money-count": "产品与案例展示",
    "blue-sweater-intro-video": "社交平台展示",
    "chromatic-radial-split": "转场",
    "cinematic-zoom": "转场",
    "code-3d-extrude": "代码演示",
    "code-diff": "代码演示",
    "code-highlight": "代码演示",
    "code-morph": "代码演示",
    "code-particle-assemble": "代码演示",
    "code-scroll": "代码演示",
    "code-shader-dissolve": "代码演示",
    "code-snippet-apple-terminal-basic": "代码演示",
    "code-snippet-apple-terminal-clear-dark": "代码演示",
    "code-snippet-apple-terminal-clear-light": "代码演示",
    "code-snippet-apple-terminal-grass": "代码演示",
    "code-snippet-apple-terminal-homebrew": "代码演示",
    "code-snippet-apple-terminal-man-page": "代码演示",
    "code-snippet-apple-terminal-novel": "代码演示",
    "code-snippet-apple-terminal-ocean": "代码演示",
    "code-snippet-apple-terminal-pro": "代码演示",
    "code-snippet-apple-terminal-red-sands": "代码演示",
    "code-snippet-apple-terminal-silver-aerogel": "代码演示",
    "code-snippet-apple-terminal-solid-colors": "代码演示",
    "code-snippet-dark-2026": "代码演示",
    "code-snippet-dark-modern": "代码演示",
    "code-snippet-dark-plus": "代码演示",
    "code-snippet-flight": "代码演示",
    "code-snippet-high-contrast": "代码演示",
    "code-snippet-high-contrast-light": "代码演示",
    "code-snippet-light-2026": "代码演示",
    "code-snippet-light-modern": "代码演示",
    "code-snippet-light-plus": "代码演示",
    "code-snippet-monokai": "代码演示",
    "code-snippet-solarized-light": "代码演示",
    "code-snippet-visual-studio-dark": "代码演示",
    "code-snippet-visual-studio-light": "代码演示",
    "code-typing": "代码演示",
    "cross-warp-morph": "转场",
    "data-chart": "数据与地图",
    "domain-warp-dissolve": "转场",
    "flash-through-white": "转场",
    "flowchart": "流程图",
    "flowchart-vertical": "流程图",
    "glitch": "转场",
    "gravitational-lens": "转场",
    "instagram-follow": "社交平台展示",
    "ios26-liquid-glass": "画面内复杂效果",
    "light-leak": "转场",
    "liquid-glass-context-menu": "画面内复杂效果",
    "liquid-glass-media-controls": "画面内复杂效果",
    "liquid-glass-notification": "画面内复杂效果",
    "liquid-glass-widgets": "画面内复杂效果",
    "logo-outro": "其他",
    "lower-third-bild": "人名与身份条",
    "lt-accent-underline": "人名与身份条",
    "lt-bold-block": "人名与身份条",
    "lt-clean-bar": "人名与身份条",
    "lt-color-block": "人名与身份条",
    "lt-dark-card": "人名与身份条",
    "lt-kicker-name": "人名与身份条",
    "lt-mask-reveal": "人名与身份条",
    "lt-side-rule": "人名与身份条",
    "lt-soft-pill": "人名与身份条",
    "lt-stack-bars": "人名与身份条",
    "macos-notification": "社交平台展示",
    "macos-tahoe-liquid-glass": "画面内复杂效果",
    "news-ticker": "其他",
    "north-korea-locked-down": "产品与案例展示",
    "nyc-paris-flight": "产品与案例展示",
    "reddit-post": "社交平台展示",
    "ridged-burn": "转场",
    "ripple-waves": "转场",
    "sdf-iris": "转场",
    "spain-map": "数据与地图",
    "spotify-card": "社交平台展示",
    "swirl-vortex": "转场",
    "thermal-distortion": "转场",
    "tiktok-follow": "社交平台展示",
    "transitions-3d": "转场",
    "transitions-blur": "转场",
    "transitions-cover": "转场",
    "transitions-destruction": "转场",
    "transitions-dissolve": "转场",
    "transitions-distortion": "转场",
    "transitions-grid": "转场",
    "transitions-light": "转场",
    "transitions-mechanical": "转场",
    "transitions-other": "转场",
    "transitions-push": "转场",
    "transitions-radial": "转场",
    "transitions-scale": "转场",
    "ui-3d-reveal": "产品与案例展示",
    "us-map": "数据与地图",
    "us-map-bubble": "数据与地图",
    "us-map-flow": "数据与地图",
    "us-map-hex": "数据与地图",
    "vfx-iphone-device": "画面内复杂效果",
    "vfx-liquid-background": "画面内复杂效果",
    "vfx-liquid-glass": "画面内复杂效果",
    "vfx-magnetic": "画面内复杂效果",
    "vfx-portal": "画面内复杂效果",
    "vfx-shatter": "画面内复杂效果",
    "vfx-text-cursor": "文字效果",
    "vpn-youtube-spot": "产品与案例展示",
    "whip-pan": "转场",
    "world-map": "数据与地图",
    "x-post": "社交平台展示",
    "yt-lower-third": "社交平台展示",
    "caption-blend-difference": "文字效果",
    "caption-clip-wipe": "字幕",
    "caption-editorial-emphasis": "字幕",
    "caption-emoji-pop": "字幕",
    "caption-glitch-rgb": "字幕",
    "caption-gradient-fill": "字幕",
    "caption-highlight": "字幕",
    "caption-kinetic-slam": "字幕",
    "caption-matrix-decode": "字幕",
    "caption-neon-accent": "字幕",
    "caption-neon-glow": "字幕",
    "caption-parallax-layers": "字幕",
    "caption-particle-burst": "字幕",
    "caption-pill-karaoke": "字幕",
    "caption-texture": "字幕",
    "caption-weight-shift": "字幕",
    "grain-overlay": "其他",
    "grid-pixelate-wipe": "转场",
    "morph-text": "文字效果",
    "motion-blur": "其他",
    "parallax-unzoom": "转场",
    "parallax-zoom": "转场",
    "shimmer-sweep": "文字效果",
    "texture-mask-text": "文字效果",
    "vignette": "其他",
}


def fail(message: str) -> None:
    raise SystemExit(f"motion catalog check failed: {message}")


def load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read {path}: {error}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def locked_source() -> dict[str, str]:
    lock = load_json(SOURCE_LOCK_PATH)
    if not isinstance(lock, dict):
        fail("third-party source lock must be an object")
    for source in lock.get("sources", []):
        if isinstance(source, dict) and source.get("id") == "hyperframes":
            commit, tag = source.get("commit"), source.get("tag")
            if not isinstance(commit, str) or not isinstance(tag, str):
                fail("hyperframes lock is incomplete")
            return {"id": "hyperframes", "commit": commit, "tag": tag}
    fail("hyperframes is missing from the third-party source lock")


def verify_submodule(commit: str) -> None:
    if not (SUBMODULE_ROOT / ".git").exists():
        fail("vendor/hyperframes submodule is not initialized")
    result = subprocess.run(
        ["git", "-C", str(SUBMODULE_ROOT), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        fail(f"cannot resolve submodule HEAD: {result.stderr.strip()}")
    if result.stdout.strip() != commit:
        fail("vendor/hyperframes checkout does not match the locked commit")


def official_preview_names() -> dict[str, str]:
    index = load_json(OFFICIAL_INDEX_PATH)
    if not isinstance(index, list) or len(index) != EXPECTED_COUNTS["officialPreview"]:
        fail("official catalog index must list exactly 100 items")
    names: dict[str, str] = {}
    for entry in index:
        if not isinstance(entry, dict):
            fail("official catalog index entry must be an object")
        name, kind = entry.get("name"), entry.get("type")
        if not isinstance(name, str) or kind not in ("block", "component"):
            fail("official catalog index entry is malformed")
        if name in names:
            fail(f"official catalog index duplicates {name}")
        names[name] = kind
    return names


def extract_urls(text: str) -> set[str]:
    urls = set()
    for url in URL_PATTERN.findall(text):
        domain = url.split("/")[2]
        if domain in IGNORED_DOMAINS:
            continue
        if domain not in DOMAIN_CATEGORIES:
            fail(f"unknown remote domain must be reviewed before cataloging: {domain}")
        urls.add(url)
    return urls


def remote_package(url: str) -> str | None:
    match = JSDELIVR_PACKAGE_PATTERN.match(url)
    if match is not None:
        return match.group(1)
    if url.startswith("https://cdnjs.cloudflare.com/ajax/libs/three.js/"):
        return "three.js"
    if url.startswith("https://www.gstatic.com/draco/"):
        return "draco-decoder"
    return None


def gpu_requirement(text: str) -> str:
    if WEBGPU_PATTERN.search(text):
        return "webgpu"
    if WEBGL_PATTERN.search(text):
        return "webgl"
    if CANVAS_2D_PATTERN.search(text):
        return "canvas_2d"
    return "dom_css"


def trademark_indicators(text: str) -> list[str]:
    cleaned = text
    for noise in FONT_STACK_NOISE:
        cleaned = cleaned.replace(noise, " ")
    return sorted(brand for brand, pattern in BRAND_PATTERNS if re.search(pattern, cleaned))


def conclusion_for(
    remote_categories: set[str], asset_kinds: set[str], indicators: list[str]
) -> str:
    needs_localization = bool(remote_categories & LOCALIZATION_CATEGORIES)
    needs_replacement = bool(asset_kinds & REPLACEMENT_ASSET_KINDS) or bool(indicators)
    if needs_localization and needs_replacement:
        return "needs_localization_and_asset_replacement"
    if needs_localization:
        return "needs_localization"
    if needs_replacement:
        return "needs_asset_replacement"
    return "cleared"


def authoring_facts(kind: str, name: str, manifest: dict[str, object]) -> dict[str, object]:
    """Upstream's own account of a part, kept for whoever has to orchestrate it.

    This catalog was frozen for a rights audit, which needed identity and
    digests and nothing else. An orchestrating model reads the same list to
    answer a different question — which part fits this beat, and how long does
    it run — and measured on 2026-07-27 two models given only title and category
    picked legal parts and then overshot the 20s sandbox budget by more than
    70%, because nothing in the list said `data-chart` runs 15 seconds while
    `lt-bold-block` runs 4.8.

    These are copied, never paraphrased: they are upstream's words about
    upstream's parts, and a local rewording would be a second source that goes
    stale on the next submodule bump with nothing able to notice.

    `duration` and `dimensions` are absent for components by design rather than
    by omission — upstream splits the registry into blocks, which are standalone
    sub-compositions owning a canvas and a timeline, and components, which are
    snippets pasted into a host and own neither. Measured across all 134 items,
    the two fields are present on exactly the 109 blocks. Carrying that as an
    explicit `null` is what lets a caller tell "no canvas of its own" apart from
    "we forgot to record one"; failing closed when the shape flips is what makes
    an upstream change in this rule something a human looks at.
    """
    description = manifest.get("description")
    if not isinstance(description, str) or not description.strip():
        fail(f"{name} has no upstream description")
    tags = manifest.get("tags")
    if not isinstance(tags, list) or not all(
        isinstance(tag, str) and tag for tag in tags
    ):
        fail(f"{name} tags must be a list of non-empty strings")
    duration = manifest.get("duration")
    dimensions = manifest.get("dimensions")
    if kind == "block":
        if type(duration) not in (int, float) or duration <= 0:
            fail(f"{name} is a block without a positive duration: {duration!r}")
        if not isinstance(dimensions, dict) or set(dimensions) != {"width", "height"}:
            fail(f"{name} is a block without width/height dimensions: {dimensions!r}")
        if any(type(dimensions[axis]) is not int or dimensions[axis] <= 0 for axis in dimensions):
            fail(f"{name} declares a non-positive canvas: {dimensions!r}")
    elif duration is not None or dimensions is not None:
        fail(f"{name} is a component yet declares a canvas or duration")
    return {
        "description": description,
        "tags": list(tags),
        "duration": duration,
        "dimensions": dict(dimensions) if isinstance(dimensions, dict) else None,
    }


def scan_item(kind: str, directory: Path) -> tuple[dict[str, object], dict[str, object]]:
    manifest = load_json(directory / "registry-item.json")
    if not isinstance(manifest, dict):
        fail(f"{directory.name} registry-item.json must be an object")
    name = manifest.get("name")
    if name != directory.name:
        fail(f"{directory.name} registry item name drifted: {name!r}")
    if manifest.get("type") != f"hyperframes:{kind}":
        fail(f"{name} registry item type drifted")
    category = CURATED_CATEGORIES.get(name)
    if category is None:
        fail(f"{name} has no curated Chinese category; review the new upstream item")

    files: list[dict[str, object]] = []
    text_blob_parts = [
        str(name),
        str(manifest.get("title", "")),
        str(manifest.get("description", "")),
        " ".join(manifest.get("tags", [])),
    ]
    urls: set[str] = set()
    asset_records: list[dict[str, str]] = []
    provenance: set[str] = set()
    manifest_files = manifest.get("files")
    if not isinstance(manifest_files, list) or not manifest_files:
        fail(f"{name} declares no installable files")
    for record in manifest_files:
        if not isinstance(record, dict):
            fail(f"{name} file record must be an object")
        relative = record.get("path")
        if not isinstance(relative, str) or relative.startswith("/") or ".." in relative.split("/"):
            fail(f"{name} file path is not canonical: {relative!r}")
        source = directory / relative
        if not source.is_file() or source.is_symlink():
            fail(f"{name} installable file is missing or not a regular file: {relative}")
        suffix = Path(relative).suffix.lower()
        files.append(
            {
                "path": relative,
                "target": record.get("target"),
                "sha256": sha256(source),
                "bytes": source.stat().st_size,
            }
        )
        if suffix in TEXT_SUFFIXES:
            text = source.read_text(encoding="utf-8", errors="replace")
            text_blob_parts.append(text)
            urls.update(extract_urls(text))
        if suffix != ".html":
            asset_kind = ASSET_KINDS_BY_SUFFIX.get(suffix)
            if asset_kind is None:
                fail(f"{name} bundles an unreviewed asset type: {relative}")
            asset_records.append({"path": relative, "kind": asset_kind})
        if suffix == ".glb":
            payload = source.read_bytes().lower()
            provenance.update(
                marker for marker, needle in PROVENANCE_MARKERS if needle in payload
            )
        if "hyperframes" in relative.lower():
            text_blob_parts.append("hyperframes")

    text_blob = "\n".join(text_blob_parts).lower()
    remote_categories = {DOMAIN_CATEGORIES[url.split("/")[2]] for url in urls}
    families = set()
    for url in urls:
        if url.split("/")[2] == "fonts.googleapis.com":
            families.update(
                family.replace("+", " ") for family in GOOGLE_FONT_FAMILY_PATTERN.findall(url)
            )
    indicators = trademark_indicators(text_blob)
    asset_records.sort(key=lambda record: record["path"])
    asset_kinds = {record["kind"] for record in asset_records}
    catalog_item = {
        "name": name,
        "type": kind,
        "path": f"registry/{kind}s/{name}",
        "title": manifest.get("title", ""),
        "category": category,
        **authoring_facts(kind, str(name), manifest),
        "officialPreview": False,
        "files": files,
    }
    rights_entry = {
        "name": name,
        "type": kind,
        "codeLicense": "Apache-2.0",
        "remoteDependencies": {
            "categories": sorted(remote_categories),
            "domains": sorted({url.split("/")[2] for url in urls}),
            "packages": sorted(
                {package for package in map(remote_package, urls) if package is not None}
            ),
            "urls": sorted(urls),
        },
        "mapData": any(MAP_DATA_PATTERN.search(url) for url in urls),
        "googleFontFamilies": sorted(families),
        "bundledAssets": asset_records,
        "embeddedProvenanceMarkers": sorted(provenance),
        "gpuRequirement": gpu_requirement(text_blob),
        "trademarkIndicators": indicators,
        "sampleAssetRights": "unverified" if asset_kinds & REPLACEMENT_ASSET_KINDS else "none",
        "conclusion": conclusion_for(remote_categories, asset_kinds, indicators),
    }
    return catalog_item, rights_entry


def build_contracts() -> tuple[dict[str, object], dict[str, object]]:
    source = locked_source()
    verify_submodule(source["commit"])
    catalog_items: list[dict[str, object]] = []
    rights_entries: list[dict[str, object]] = []
    for kind, subdirectory in (("block", "blocks"), ("component", "components")):
        parent = REGISTRY_ROOT / subdirectory
        if not parent.is_dir():
            fail(f"registry directory is missing: {parent}")
        for directory in sorted(parent.iterdir()):
            if not directory.is_dir():
                continue
            catalog_item, rights_entry = scan_item(kind, directory)
            catalog_items.append(catalog_item)
            rights_entries.append(rights_entry)
    catalog_items.sort(key=lambda item: item["name"])
    rights_entries.sort(key=lambda entry: entry["name"])

    names = [item["name"] for item in catalog_items]
    if len(names) != len(set(names)):
        fail("registry item names must be unique")
    unknown_curated = sorted(set(CURATED_CATEGORIES) - set(names))
    if unknown_curated:
        fail(f"curated categories reference unknown items: {unknown_curated}")

    official = official_preview_names()
    by_name = {item["name"]: item for item in catalog_items}
    for name, kind in official.items():
        item = by_name.get(name)
        if item is None or item["type"] != kind:
            fail(f"official preview item is not in the locked registry: {name}")
        item["officialPreview"] = True

    counts = {
        "total": len(catalog_items),
        "blocks": sum(item["type"] == "block" for item in catalog_items),
        "components": sum(item["type"] == "component" for item in catalog_items),
        "officialPreview": sum(bool(item["officialPreview"]) for item in catalog_items),
    }
    if counts != EXPECTED_COUNTS:
        fail(f"registry inventory drifted from the fixed counts: {counts}")
    category_counts: dict[str, int] = {}
    for item in catalog_items:
        category_counts[item["category"]] = category_counts.get(item["category"], 0) + 1

    catalog = {
        "schemaVersion": 1,
        "source": {**source, "registryPath": "registry"},
        "counts": counts,
        "categories": list(CATEGORIES),
        "categoryCounts": category_counts,
        "items": catalog_items,
    }

    package_items: dict[str, int] = {}
    for entry in rights_entries:
        for package in entry["remoteDependencies"]["packages"]:
            package_items[package] = package_items.get(package, 0) + 1
    unknown_packages = sorted(set(package_items) - set(REMOTE_PACKAGE_LICENSES))
    if unknown_packages:
        fail(f"remote packages must be reviewed before cataloging: {unknown_packages}")
    stats = {
        "itemsWithRuntimeRemoteDependencies": sum(
            bool(set(entry["remoteDependencies"]["categories"]) & LOCALIZATION_CATEGORIES)
            for entry in rights_entries
        ),
        "remoteDependencyCategoryCounts": {
            category: sum(
                category in entry["remoteDependencies"]["categories"]
                for entry in rights_entries
            )
            for category in sorted(DOMAIN_CATEGORIES.values())
        },
        "itemsWithMapData": sum(entry["mapData"] for entry in rights_entries),
        "itemsWithBundledSampleAssets": sum(
            entry["sampleAssetRights"] == "unverified" for entry in rights_entries
        ),
        "itemsWithTrademarkIndicators": sum(
            bool(entry["trademarkIndicators"]) for entry in rights_entries
        ),
        "gpuTierCounts": {
            tier: sum(entry["gpuRequirement"] == tier for entry in rights_entries)
            for tier in GPU_TIERS
        },
        "googleFontFamilyCount": len(
            {family for entry in rights_entries for family in entry["googleFontFamilies"]}
        ),
        "conclusionCounts": {
            conclusion: sum(entry["conclusion"] == conclusion for entry in rights_entries)
            for conclusion in sorted(CONCLUSIONS)
        },
    }
    rights = {
        "schemaVersion": 1,
        "source": {"id": source["id"], "commit": source["commit"], "tag": source["tag"]},
        "codeLicense": {
            "spdx": "Apache-2.0",
            "coverage": "repository_source_only",
            "note": (
                "Apache-2.0 covers upstream code; it does not grant likeness, font, "
                "audio, trademark or third-party sample-asset redistribution rights."
            ),
        },
        "conclusions": list(CONCLUSIONS),
        "remoteDependencyCategories": sorted(set(DOMAIN_CATEGORIES.values())),
        "assetKinds": sorted(set(ASSET_KINDS_BY_SUFFIX.values())),
        "gpuTiers": list(GPU_TIERS),
        "remoteDependencyPackages": [
            {
                "package": package,
                "itemCount": package_items[package],
                "assumedLicense": REMOTE_PACKAGE_LICENSES[package],
                "verification": "pending_bm12_localization",
            }
            for package in sorted(package_items)
        ],
        "stats": stats,
        "items": rights_entries,
    }
    return catalog, rights


def first_difference(expected: object, actual: object, path: str) -> str | None:
    if type(expected) is not type(actual):
        return f"{path} has type {type(actual).__name__}, expected {type(expected).__name__}"
    if isinstance(expected, dict):
        for key in expected:
            if key not in actual:
                return f"{path}.{key} is missing"
        for key in actual:
            if key not in expected:
                return f"{path}.{key} is unknown"
        for key in expected:
            difference = first_difference(expected[key], actual[key], f"{path}.{key}")
            if difference is not None:
                return difference
        return None
    if isinstance(expected, list):
        if len(expected) != len(actual):
            return f"{path} has {len(actual)} entries, expected {len(expected)}"
        for index, (expected_entry, actual_entry) in enumerate(
            zip(expected, actual, strict=True)
        ):
            difference = first_difference(expected_entry, actual_entry, f"{path}[{index}]")
            if difference is not None:
                return difference
        return None
    if expected != actual:
        return f"{path} drifted: {actual!r} != {expected!r}"
    return None


def write_json(path: Path, value: dict[str, object]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=CATALOG_PATH)
    parser.add_argument("--rights", type=Path, default=RIGHTS_PATH)
    parser.add_argument("--write", action="store_true")
    arguments = parser.parse_args()

    expected_catalog, expected_rights = build_contracts()
    if arguments.write:
        write_json(arguments.catalog, expected_catalog)
        write_json(arguments.rights, expected_rights)
        print(f"motion catalog contracts written: {arguments.catalog}, {arguments.rights}")
        return

    actual_catalog = load_json(arguments.catalog)
    difference = first_difference(expected_catalog, actual_catalog, "catalog")
    if difference is not None:
        fail(difference)
    actual_rights = load_json(arguments.rights)
    difference = first_difference(expected_rights, actual_rights, "rights")
    if difference is not None:
        fail(difference)
    stats = expected_rights["stats"]
    print(
        "motion catalog and rights ledger are valid: "
        f"134 items (109 blocks / 25 components, 100 official previews), "
        f"{stats['itemsWithRuntimeRemoteDependencies']} with runtime remote dependencies, "
        f"conclusions={stats['conclusionCounts']}"
    )


if __name__ == "__main__":
    main()
