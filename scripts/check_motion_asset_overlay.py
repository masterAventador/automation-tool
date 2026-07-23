#!/usr/bin/env python3
"""Fail-closed gate for the BM-13 brand-neutral motion asset overlay."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import wave
import xml.etree.ElementTree as ET
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RIGHTS_PATH = REPOSITORY_ROOT / "contracts/quality/motion-catalog-rights.v1.json"
DEFAULT_OVERLAY_PATH = REPOSITORY_ROOT / "contracts/quality/motion-asset-overlay.v1.json"
DEFAULT_ASSET_ROOT = REPOSITORY_ROOT / "assets/motion-catalog-overlay"
PENDING_CONCLUSIONS = {
    "needs_asset_replacement",
    "needs_localization_and_asset_replacement",
}
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
REMOTE_URL_PATTERN = re.compile(rb"https?://", re.IGNORECASE)
FORBIDDEN_ASSET_TERMS = (
    b"apple",
    b"heygen",
    b"hyperframes",
    b"instagram",
    b"iphone",
    b"macbook",
    b"reddit",
    b"spotify",
    b"tiktok",
    b"twitter",
    b"visual studio",
    b"vscode",
    b"youtube",
)
COMMON_FIELDS = {
    "id",
    "category",
    "path",
    "source",
    "sourceUrl",
    "license",
    "licenseVersionOrDate",
    "acquiredAt",
    "sha256",
    "bytes",
    "redistributionAllowed",
    "commercialUseAllowed",
    "reviewedBy",
    "reviewedAt",
}


class CheckError(SystemExit):
    def __init__(self, message: str) -> None:
        super().__init__(f"motion asset overlay check failed: {message}")


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CheckError(f"cannot read {path}: {error}") from error
    if not isinstance(value, dict):
        raise CheckError(f"{path} must contain an object")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_relative(value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise CheckError("asset path must be a non-empty string")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        raise CheckError(f"asset path is not canonical: {value!r}")
    return path


def verify_png(payload: bytes, relative: Path) -> None:
    if not payload.startswith(b"\x89PNG\r\n\x1a\n") or len(payload) < 24:
        raise CheckError(f"invalid PNG: {relative}")
    width, height = struct.unpack(">II", payload[16:24])
    if width < 256 or height < 256:
        raise CheckError(f"generated raster is too small: {relative}: {width}x{height}")


def verify_wave(path: Path, relative: Path) -> None:
    try:
        with wave.open(str(path), "rb") as source:
            if (
                source.getnchannels() != 1
                or source.getsampwidth() != 2
                or source.getframerate() != 22050
                or source.getnframes() <= 0
            ):
                raise CheckError(f"audio format drifted: {relative}")
    except (wave.Error, EOFError) as error:
        raise CheckError(f"invalid WAV: {relative}: {error}") from error


def verify_glb(payload: bytes, relative: Path) -> None:
    if len(payload) < 28 or payload[:4] != b"glTF":
        raise CheckError(f"invalid GLB header: {relative}")
    version, declared_length = struct.unpack("<II", payload[4:12])
    if version != 2 or declared_length != len(payload):
        raise CheckError(f"GLB version or length drifted: {relative}")
    json_length, json_type = struct.unpack("<I4s", payload[12:20])
    if json_type != b"JSON" or 20 + json_length + 8 > len(payload):
        raise CheckError(f"GLB JSON chunk is invalid: {relative}")
    try:
        document = json.loads(payload[20 : 20 + json_length].decode().rstrip())
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CheckError(f"GLB JSON is invalid: {relative}") from error
    if document.get("asset", {}).get("version") != "2.0":
        raise CheckError(f"GLB asset version is invalid: {relative}")
    binary_offset = 20 + json_length
    binary_length, binary_type = struct.unpack("<I4s", payload[binary_offset : binary_offset + 8])
    if binary_type != b"BIN\0" or binary_offset + 8 + binary_length != len(payload):
        raise CheckError(f"GLB binary chunk is invalid: {relative}")


def verify_file(record: dict, asset_root: Path) -> None:
    missing = COMMON_FIELDS - set(record)
    if missing:
        raise CheckError(f"{record.get('id')} rights fields are missing: {sorted(missing)}")
    if record["redistributionAllowed"] is not True:
        raise CheckError(f"{record['id']} is not approved for redistribution")
    if record["commercialUseAllowed"] is not True:
        raise CheckError(f"{record['id']} is not approved for commercial use")
    if not isinstance(record["sha256"], str) or not SHA256_PATTERN.fullmatch(record["sha256"]):
        raise CheckError(f"{record['id']} has an invalid SHA-256")
    relative = canonical_relative(record["path"])
    if any(term.decode() in relative.as_posix().casefold() for term in FORBIDDEN_ASSET_TERMS):
        raise CheckError(f"brand term remains in App asset path: {relative}")
    path = asset_root / relative
    if not path.is_file() or path.is_symlink():
        raise CheckError(f"asset is missing, linked or not regular: {relative}")
    if path.stat().st_size != record["bytes"]:
        raise CheckError(f"asset byte count drifted: {relative}")
    actual = sha256_file(path)
    if actual != record["sha256"]:
        raise CheckError(f"asset digest drifted: {relative}: {actual}")
    payload = path.read_bytes()
    suffix = path.suffix.casefold()
    scanned = payload
    if suffix == ".svg":
        scanned = scanned.replace(b"http://www.w3.org/2000/svg", b"")
    if REMOTE_URL_PATTERN.search(scanned):
        raise CheckError(f"remote URL remains in App asset: {relative}")
    if suffix in {".svg", ".js"}:
        folded = scanned.lower()
        found = [term.decode() for term in FORBIDDEN_ASSET_TERMS if term in folded]
        if found:
            raise CheckError(f"brand terms remain in text asset {relative}: {found}")
    if suffix == ".png":
        verify_png(payload, relative)
    elif suffix == ".svg":
        try:
            root = ET.fromstring(payload)
        except ET.ParseError as error:
            raise CheckError(f"invalid SVG: {relative}: {error}") from error
        if not root.tag.endswith("svg"):
            raise CheckError(f"SVG root drifted: {relative}")
    elif suffix == ".wav":
        verify_wave(path, relative)
    elif suffix == ".glb":
        verify_glb(payload, relative)
    elif suffix == ".woff2":
        if payload[:4] != b"wOF2":
            raise CheckError(f"invalid WOFF2: {relative}")
    elif suffix == ".js":
        try:
            payload.decode("utf-8")
        except UnicodeDecodeError as error:
            raise CheckError(f"script is not UTF-8: {relative}") from error
    else:
        raise CheckError(f"unsupported overlay asset type: {relative}")

    category = record["category"]
    if category == "generated":
        required = {
            "model",
            "modelVersion",
            "generationRecord",
            "humanRightsReview",
            "rightsBasis",
            "rightsSources",
            "rightsCaveats",
        }
        if required - set(record):
            raise CheckError(f"generated rights fields are incomplete: {record['id']}")
        if record["humanRightsReview"].get("status") != "passed":
            raise CheckError(f"generated rights review did not pass: {record['id']}")
        source_urls = {source.get("url") for source in record["rightsSources"]}
        if source_urls != {
            "https://openai.com/policies/terms-of-use/",
            "https://openai.com/policies/services-agreement/",
        }:
            raise CheckError(f"generated rights sources drifted: {record['id']}")
    elif category == "font":
        if record.get("embeddingAllowed") is not True or not record.get("attribution"):
            raise CheckError("font embedding rights are incomplete")
        if record["sha256"] != record.get("bm12LockedSha256"):
            raise CheckError("font digest differs from the BM-12 verified artifact")
    elif category == "music_sfx":
        if record.get("syncUseAllowed") is not True:
            raise CheckError(f"audio sync rights are incomplete: {record['id']}")
        if record.get("contentIdRisk") != "none-self-authored":
            raise CheckError(f"audio Content ID review is incomplete: {record['id']}")
    elif category == "map_3d":
        if record.get("derivativeUseAllowed") is not True:
            raise CheckError(f"map/3D derivative rights are incomplete: {record['id']}")
    elif category not in {"vector_ui", "script"}:
        raise CheckError(f"unsupported rights category: {category}")


def verify_overlay(overlay_path: Path, asset_root: Path, rights_path: Path) -> dict:
    overlay = load_json(overlay_path)
    rights = load_json(rights_path)
    if overlay.get("schemaVersion") != 1:
        raise CheckError("schemaVersion must be 1")
    if overlay.get("assetRoot") != "assets/motion-catalog-overlay":
        raise CheckError("assetRoot drifted")
    if overlay.get("source", {}).get("catalogRightsSha256") != sha256_file(rights_path):
        raise CheckError("source rights contract digest drifted")

    expected = {
        item["name"]: item for item in rights["items"] if item["conclusion"] in PENDING_CONCLUSIONS
    }
    if len(expected) != 70:
        raise CheckError(f"BM-11/BM-12 pending set drifted: {len(expected)} != 70")
    items = overlay.get("items")
    if not isinstance(items, list):
        raise CheckError("items must be an array")
    item_map = {item.get("name"): item for item in items}
    if None in item_map or len(item_map) != len(items) or set(item_map) != set(expected):
        raise CheckError("pending item coverage drifted")

    assets = overlay.get("assets")
    if not isinstance(assets, list) or not assets:
        raise CheckError("assets must be a non-empty array")
    asset_map = {asset.get("id"): asset for asset in assets}
    if None in asset_map or len(asset_map) != len(assets):
        raise CheckError("asset IDs are missing or duplicated")
    path_set = {asset.get("path") for asset in assets}
    if None in path_set or len(path_set) != len(assets):
        raise CheckError("asset paths are missing or duplicated")
    for asset in assets:
        verify_file(asset, asset_root)

    on_disk = set()
    for path in asset_root.rglob("*"):
        if path.is_symlink():
            raise CheckError(f"symlink is not allowed in the asset tree: {path}")
        if path.is_file():
            on_disk.add(path.relative_to(asset_root).as_posix())
    if on_disk != path_set:
        raise CheckError(
            "asset tree has missing or undeclared files: "
            f"missing={sorted(path_set - on_disk)[:5]}, "
            f"extra={sorted(on_disk - path_set)[:5]}"
        )

    generated_roles = {
        asset.get("generationRecord", {}).get("role")
        for asset in assets
        if asset["source"] == "openai-image-generation"
    }
    if not {"avatar", "wallpaper", "illustration", "texture"} <= generated_roles:
        raise CheckError(f"required Image Generation roles are incomplete: {generated_roles}")

    asset_reference_count = 0
    trademark_reference_count = 0
    trademark_item_count = 0
    referenced_assets: set[str] = set()
    for name, source in expected.items():
        item = item_map[name]
        replacements = item.get("assetReplacements")
        if not isinstance(replacements, list):
            raise CheckError(f"{name} assetReplacements must be an array")
        expected_assets = {(record["path"], record["kind"]) for record in source["bundledAssets"]}
        actual_assets = {
            (record.get("sourcePath"), record.get("sourceKind")) for record in replacements
        }
        if actual_assets != expected_assets or len(actual_assets) != len(replacements):
            raise CheckError(f"{name} asset replacement coverage drifted")
        for replacement in replacements:
            asset_id = replacement.get("assetId")
            if asset_id not in asset_map:
                raise CheckError(f"{name} references unknown replacement asset {asset_id}")
            if replacement.get("replacementPath") != asset_map[asset_id]["path"]:
                raise CheckError(f"{name} replacement path drifted for {asset_id}")
            referenced_assets.add(asset_id)
        asset_reference_count += len(replacements)

        trademark_replacements = item.get("trademarkReplacements")
        if not isinstance(trademark_replacements, list):
            raise CheckError(f"{name} trademarkReplacements must be an array")
        expected_indicators = set(source["trademarkIndicators"])
        actual_indicators = {record.get("indicator") for record in trademark_replacements}
        if actual_indicators != expected_indicators or len(actual_indicators) != len(
            trademark_replacements
        ):
            raise CheckError(f"{name} trademark replacement coverage drifted")
        for replacement in trademark_replacements:
            value = replacement.get("replacement")
            if (
                not isinstance(value, str)
                or not value.strip()
                or value.casefold() == replacement["indicator"].casefold()
            ):
                raise CheckError(f"{name} has an unsafe trademark replacement")
        trademark_reference_count += len(trademark_replacements)
        trademark_item_count += bool(trademark_replacements)

    unused = sorted(set(asset_map) - referenced_assets)
    declared_library_only = sorted(
        asset["id"] for asset in assets if asset.get("libraryOnly") is True
    )
    if unused != declared_library_only or unused != ["generated-illustration"]:
        raise CheckError(f"unexpected unused overlay assets: {unused}")
    expected_counts = {
        "items": len(items),
        "assetReplacementReferences": asset_reference_count,
        "trademarkReplacementItems": trademark_item_count,
        "trademarkReplacementReferences": trademark_reference_count,
        "assets": len(assets),
    }
    if overlay.get("counts") != expected_counts:
        raise CheckError(f"overlay counts drifted: {overlay.get('counts')} != {expected_counts}")
    if trademark_item_count != 68:
        raise CheckError(f"trademark item count drifted: {trademark_item_count} != 68")
    return expected_counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--overlay", type=Path, default=DEFAULT_OVERLAY_PATH)
    parser.add_argument("--asset-root", type=Path, default=DEFAULT_ASSET_ROOT)
    parser.add_argument("--rights", type=Path, default=DEFAULT_RIGHTS_PATH)
    arguments = parser.parse_args()
    counts = verify_overlay(arguments.overlay, arguments.asset_root, arguments.rights)
    print(
        "motion asset overlay is valid: "
        f"{counts['items']} items, {counts['assetReplacementReferences']} asset references, "
        f"{counts['trademarkReplacementItems']} trademark items/"
        f"{counts['trademarkReplacementReferences']} references, "
        f"{counts['assets']} rights-reviewed assets"
    )


if __name__ == "__main__":
    main()
