#!/usr/bin/env python3
"""Static gate for the composed read-only motion catalog release (BM-14).

Fails closed when the versioned release tree contains any file without a locked
digest, digest or size drift, a missing declared file, a symlink, a writable
file, any remote ``http(s)://`` reference outside the reviewed allowances, any
trademark-indicator leftover outside the protected catalog item ids and the
closed technical keeplist, a catalog item missing from the 134-item contract, a
bundled sample asset that does not carry its registered overlay replacement
bytes, or an aggregate that differs from
``contracts/video/motion-catalog-release.v1.json``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
import re
import stat
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
RELEASE_LOCK_PATH = REPOSITORY_ROOT / "contracts/video/motion-catalog-release.v1.json"
DEP_LOCK_PATH = REPOSITORY_ROOT / "contracts/video/offline-motion-dependencies.v1.json"
CATALOG_CONTRACT_PATH = REPOSITORY_ROOT / "contracts/quality/motion-catalog.v1.json"
OVERLAY_PATH = REPOSITORY_ROOT / "contracts/quality/motion-asset-overlay.v1.json"
TEXT_SUFFIXES = frozenset({".html", ".js", ".css", ".svg"})
URL_PATTERN = re.compile(r"https?://[^\s\"'`<>)\\]+")
ALLOWED_DOMAINS = frozenset({"www.w3.org"})
WRITE_BITS = stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH


class CheckError(SystemExit):
    """Raised when the composed release violates the locked contract."""

    def __init__(self, message: str) -> None:
        super().__init__(f"motion catalog release check failed: {message}")


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


def aggregate_digest(files: list[dict]) -> str:
    lines = "".join(
        f"{record['path']} {record['sha256']}\n"
        for record in sorted(files, key=lambda record: record["path"])
    )
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()


def verify_input_pins(release_lock: dict) -> None:
    for key, record in sorted(release_lock.get("inputs", {}).items()):
        pinned = REPOSITORY_ROOT / record["path"]
        if not pinned.is_file():
            raise CheckError(f"pinned input contract is missing ({key}): {record['path']}")
        actual = sha256_file(pinned)
        if actual != record["sha256"]:
            raise CheckError(
                f"input contract drifted from the release lock pin ({key}): "
                f"{actual} != {record['sha256']}"
            )


def _indicator_scan_rules(release_lock: dict, item_names: set[str]) -> dict:
    scan = release_lock["trademarkScan"]
    patterns = [
        re.compile(r"(?<![0-9A-Za-z])" + re.escape(literal) + r"(?![0-9A-Za-z])", re.IGNORECASE)
        for token in sorted(scan["forms"])
        for literal in sorted(scan["forms"][token], key=len, reverse=True)
    ]
    protected = [
        re.compile(re.escape(entry), re.IGNORECASE)
        for entry in sorted(item_names | set(scan["technicalKeeplist"]), key=len, reverse=True)
    ]
    return {"patterns": patterns, "protected": protected}


def _indicator_leftovers(text: str, rules: dict) -> list[str]:
    spans = [match.span() for pattern in rules["protected"] for match in pattern.finditer(text)]
    leftovers = {
        match.group(0)
        for pattern in rules["patterns"]
        for match in pattern.finditer(text)
        if not any(begin <= match.start() and match.end() <= stop for begin, stop in spans)
    }
    return sorted(leftovers)


def _verify_item_replacements(
    name: str,
    manifest_item: dict,
    overlay_item: dict | None,
    contract_paths: list[str],
    declared: dict[str, dict],
    assets: dict[str, dict],
    item_prefix: str,
    item_texts: dict[str, str],
) -> set[str]:
    """Validate one item's overlay application and return its expected file set."""
    manifest_rules = {
        rule["sourcePath"]: rule for rule in manifest_item.get("assetReplacements", [])
    }
    overlay_rules = {
        rule["sourcePath"]: rule
        for rule in (overlay_item["assetReplacements"] if overlay_item else [])
    }
    if set(manifest_rules) != set(overlay_rules):
        raise CheckError(
            f"{name} asset replacements drifted from the overlay contract: "
            f"manifest={sorted(manifest_rules)}, overlay={sorted(overlay_rules)}"
        )
    expected_trademarks = len(overlay_item["trademarkReplacements"]) if overlay_item else 0
    if manifest_item.get("trademarkReplacements") != expected_trademarks:
        raise CheckError(f"{name} must declare {expected_trademarks} trademark replacements")

    mapping: dict[str, str] = {}
    for source_path, manifest_rule in manifest_rules.items():
        overlay_rule = overlay_rules[source_path]
        if manifest_rule["assetId"] != overlay_rule["assetId"]:
            raise CheckError(f"{name} replacement asset drifted for {source_path}")
        asset = assets.get(overlay_rule["assetId"])
        if asset is None:
            raise CheckError(f"{name} references an unregistered overlay asset: {source_path}")
        composed = manifest_rule["composedPath"]
        if posixpath.dirname(composed) != posixpath.dirname(source_path):
            raise CheckError(f"{name} composed file left its source directory: {composed}")
        source_name = posixpath.basename(source_path)
        composed_name = posixpath.basename(composed)
        replacement_name = posixpath.basename(overlay_rule["replacementPath"])
        if composed_name == source_name:
            if (
                posixpath.splitext(source_name)[1].lower()
                != posixpath.splitext(replacement_name)[1].lower()
            ):
                raise CheckError(
                    f"{name} kept the source name across a media type change: {composed}"
                )
        elif composed_name != replacement_name:
            raise CheckError(f"{name} composed an unregistered file name: {composed}")
        full = f"{item_prefix}/{composed}"
        record = declared.get(full)
        if record is None:
            raise CheckError(f"{name} composed asset has no locked digest: {full}")
        if record["sha256"] != asset["sha256"] or record["bytes"] != asset["bytes"]:
            raise CheckError(
                f"{name} composed asset does not carry the overlay replacement bytes: {full}"
            )
        if composed != source_path:
            for text_path, text in item_texts.items():
                if source_path in text:
                    raise CheckError(
                        f"{name} still references a replaced asset path in {text_path}"
                    )
        mapping[source_path] = composed

    return {f"{item_prefix}/{mapping.get(path, path)}" for path in contract_paths}


def verify_release(
    release_root: Path,
    release_lock: dict,
    dep_lock: dict,
    catalog_contract: dict,
    overlay: dict,
) -> dict:
    verify_input_pins(release_lock)
    manifest_path = release_root / "manifest.json"
    if not manifest_path.is_file():
        raise CheckError("release manifest.json is missing; run the release build first")
    manifest = load_json(manifest_path)
    if manifest.get("schemaVersion") != 1:
        raise CheckError("release manifest schemaVersion must be 1")
    if manifest.get("catalogVersion") != release_lock["catalogVersion"]:
        raise CheckError("release manifest catalogVersion drifted from the lock")
    if manifest.get("inputs", {}) != release_lock.get("inputs", {}):
        raise CheckError("release manifest input pins drifted from the lock")

    declared: dict[str, dict] = {}
    for record in manifest.get("files", []):
        path = record.get("path")
        if not isinstance(path, str) or path.startswith("/") or ".." in path.split("/"):
            raise CheckError(f"manifest file path is not canonical: {path!r}")
        if path in declared:
            raise CheckError(f"manifest declares {path} twice")
        declared[path] = record
    if not declared:
        raise CheckError("release manifest declares no files")

    item_root = release_lock["layout"]["itemRoot"]
    dependency_prefix = release_lock["layout"]["dependencyRoot"] + "/"
    documentation_domains = frozenset(dep_lock.get("embeddedDocumentationUrlDomains", []))
    item_names = {item["name"] for item in catalog_contract["items"]}
    scan_rules = _indicator_scan_rules(release_lock, item_names)

    on_disk: set[str] = set()
    remote_urls: list[str] = []
    indicator_hits: list[str] = []
    item_texts: dict[str, dict[str, str]] = {}
    for path in sorted(release_root.rglob("*")):
        if path.is_symlink():
            raise CheckError(f"symlink is not allowed in the release tree: {path}")
        if not path.is_file():
            continue
        if path.stat().st_mode & WRITE_BITS:
            raise CheckError(
                f"release file must be read-only: {path.relative_to(release_root).as_posix()}"
            )
        relative = path.relative_to(release_root).as_posix()
        if relative == "manifest.json":
            continue
        on_disk.add(relative)
        record = declared.get(relative)
        if record is None:
            raise CheckError(f"file has no locked digest (unregistered): {relative}")
        actual = sha256_file(path)
        if actual != record.get("sha256"):
            raise CheckError(f"digest drift: {relative}: {actual} != {record.get('sha256')}")
        if path.stat().st_size != record.get("bytes"):
            raise CheckError(f"size drift: {relative}")
        if path.suffix.lower() in TEXT_SUFFIXES:
            allowed = ALLOWED_DOMAINS
            if relative.startswith(dependency_prefix):
                allowed = ALLOWED_DOMAINS | documentation_domains
            text = path.read_text(encoding="utf-8", errors="replace")
            remote_urls.extend(
                f"{relative}: {url}"
                for url in URL_PATTERN.findall(text)
                if url.split("/")[2] not in allowed
            )
            if relative.startswith(f"{item_root}/"):
                name = relative.split("/")[1]
                item_texts.setdefault(name, {})[relative] = text
                indicator_hits.extend(
                    f"{relative}: {hit}" for hit in _indicator_leftovers(text, scan_rules)
                )
    missing = sorted(set(declared) - on_disk)
    if missing:
        raise CheckError(f"declared files are missing on disk: {missing[:5]}")
    if remote_urls:
        raise CheckError(f"remote URLs found in the release tree: {remote_urls[:5]}")
    if indicator_hits:
        raise CheckError(f"trademark indicators remain in item files: {indicator_hits[:5]}")

    assets = {asset["id"]: asset for asset in overlay["assets"]}
    overlay_items = {entry["name"]: entry for entry in overlay["items"]}
    manifest_items = {item["name"]: item for item in manifest.get("items", [])}
    contract_items = {item["name"]: item for item in catalog_contract["items"]}
    if set(manifest_items) != set(contract_items):
        missing_items = sorted(set(contract_items) - set(manifest_items))
        extra_items = sorted(set(manifest_items) - set(contract_items))
        raise CheckError(
            f"catalog items drifted: missing={missing_items[:5]}, extra={extra_items[:5]}"
        )
    for name, contract_item in contract_items.items():
        manifest_item = manifest_items[name]
        expected_files = _verify_item_replacements(
            name,
            manifest_item,
            overlay_items.get(name),
            [record["path"] for record in contract_item["files"]],
            declared,
            assets,
            f"{item_root}/{name}",
            item_texts.get(name, {}),
        )
        if set(manifest_item.get("files", [])) != expected_files:
            raise CheckError(f"{name} composed file set drifted")
        for relative in expected_files:
            if relative not in declared:
                raise CheckError(f"{name} composed file has no locked digest: {relative}")

    generated = {
        "fileCount": len(declared),
        "aggregateSha256": aggregate_digest(list(declared.values())),
    }
    if generated != release_lock["generated"]:
        raise CheckError(
            f"release aggregate drifted from the lock: {generated} != {release_lock['generated']}"
        )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-lock", type=Path, default=RELEASE_LOCK_PATH)
    parser.add_argument("--release-root", type=Path, default=None)
    arguments = parser.parse_args()

    release_lock = load_json(arguments.release_lock)
    dep_lock = load_json(DEP_LOCK_PATH)
    catalog_contract = load_json(CATALOG_CONTRACT_PATH)
    overlay = load_json(OVERLAY_PATH)
    release_root = arguments.release_root or (
        REPOSITORY_ROOT / release_lock["layout"]["releaseRoot"] / release_lock["catalogVersion"]
    )
    manifest = verify_release(release_root, release_lock, dep_lock, catalog_contract, overlay)
    applied = sum(len(item["assetReplacements"]) for item in manifest["items"])
    replaced_items = sum(bool(item["trademarkReplacements"]) for item in manifest["items"])
    print(
        "motion catalog release is valid: "
        f"version {manifest['catalogVersion']}, {manifest['counts']['items']} items, "
        f"{manifest['counts']['files']} files, 0 remote URLs, 0 indicator leftovers, "
        f"{applied} asset replacements verified, "
        f"{replaced_items} items with trademark replacements"
    )


if __name__ == "__main__":
    main()
