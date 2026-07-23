#!/usr/bin/env python3
"""Static offline gate for the generated 134-item OfflineMotionCatalog.

Fails closed when the generated catalog staging tree contains any remote
``http://``/``https://`` reference (only ``www.w3.org`` namespace URIs are
allowed), any file without a locked digest, digest or size drift, undeclared
extra files, a missing catalog item, an aggregate that differs from
``contracts/video/offline-motion-dependencies.v1.json``, or an item whose
pending BM-13 asset-replacement status is hidden instead of declared.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = REPOSITORY_ROOT / "contracts/video/offline-motion-dependencies.v1.json"
CATALOG_CONTRACT_PATH = REPOSITORY_ROOT / "contracts/quality/motion-catalog.v1.json"
RIGHTS_PATH = REPOSITORY_ROOT / "contracts/quality/motion-catalog-rights.v1.json"
TEXT_SUFFIXES = frozenset({".html", ".js", ".css", ".svg"})
URL_PATTERN = re.compile(r"https?://[^\s\"'`<>)\\]+")
ALLOWED_DOMAINS = frozenset({"www.w3.org"})
PENDING_ASSET_CONCLUSIONS = frozenset(
    {"needs_asset_replacement", "needs_localization_and_asset_replacement"}
)


class CheckError(SystemExit):
    """Raised when the offline catalog violates the locked contract."""

    def __init__(self, message: str) -> None:
        super().__init__(f"offline motion catalog check failed: {message}")


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


def verify_catalog(
    catalog_root: Path, lock: dict, catalog_contract: dict, rights: dict
) -> dict:
    manifest_path = catalog_root / "manifest.json"
    if not manifest_path.is_file():
        raise CheckError("generated catalog manifest.json is missing; run the build first")
    manifest = load_json(manifest_path)
    if manifest.get("schemaVersion") != 1:
        raise CheckError("generated manifest schemaVersion must be 1")

    declared: dict[str, dict] = {}
    for record in manifest.get("files", []):
        path = record.get("path")
        if not isinstance(path, str) or path.startswith("/") or ".." in path.split("/"):
            raise CheckError(f"manifest file path is not canonical: {path!r}")
        if path in declared:
            raise CheckError(f"manifest declares {path} twice")
        declared[path] = record
    if not declared:
        raise CheckError("generated manifest declares no files")

    dependency_prefix = lock["layout"]["dependencyRoot"] + "/"
    documentation_domains = frozenset(lock.get("embeddedDocumentationUrlDomains", []))
    on_disk: set[str] = set()
    remote_urls: list[str] = []
    for path in sorted(catalog_root.rglob("*")):
        if not path.is_file():
            continue
        if path.is_symlink():
            raise CheckError(f"symlink is not allowed in the catalog: {path}")
        relative = path.relative_to(catalog_root).as_posix()
        if relative == "manifest.json":
            continue
        on_disk.add(relative)
        record = declared.get(relative)
        if record is None:
            raise CheckError(f"file has no locked digest (undeclared): {relative}")
        actual = sha256_file(path)
        if actual != record.get("sha256"):
            raise CheckError(f"digest drift: {relative}: {actual} != {record.get('sha256')}")
        if path.stat().st_size != record.get("bytes"):
            raise CheckError(f"size drift: {relative}")
        if path.suffix.lower() in TEXT_SUFFIXES:
            # Verbatim vendored libraries under the dependency root keep their
            # license banners and documentation comments; only the closed,
            # reviewed domain list is tolerated there. Item files stay at zero.
            allowed = ALLOWED_DOMAINS
            if relative.startswith(dependency_prefix):
                allowed = ALLOWED_DOMAINS | documentation_domains
            text = path.read_text(encoding="utf-8", errors="replace")
            remote_urls.extend(
                f"{relative}: {url}"
                for url in URL_PATTERN.findall(text)
                if url.split("/")[2] not in allowed
            )
    missing = sorted(set(declared) - on_disk)
    if missing:
        raise CheckError(f"declared files are missing on disk: {missing[:5]}")
    if remote_urls:
        raise CheckError(f"remote URLs found in the offline catalog: {remote_urls[:5]}")

    conclusions = {entry["name"]: entry["conclusion"] for entry in rights["items"]}
    manifest_items = {item["name"]: item for item in manifest.get("items", [])}
    contract_items = {item["name"]: item for item in catalog_contract["items"]}
    if set(manifest_items) != set(contract_items):
        missing_items = sorted(set(contract_items) - set(manifest_items))
        extra_items = sorted(set(manifest_items) - set(contract_items))
        raise CheckError(
            f"catalog items drifted: missing={missing_items[:5]}, extra={extra_items[:5]}"
        )
    item_root = lock["layout"]["itemRoot"]
    for name, contract_item in contract_items.items():
        manifest_item = manifest_items[name]
        expected_files = {
            f"{item_root}/{name}/{record['path']}" for record in contract_item["files"]
        }
        if set(manifest_item.get("files", [])) != expected_files:
            raise CheckError(f"{name} generated file set drifted")
        for relative in expected_files:
            if relative not in declared:
                raise CheckError(f"{name} generated file has no locked digest: {relative}")
        conclusion = conclusions.get(name)
        if conclusion is None:
            raise CheckError(f"{name} has no rights conclusion")
        expected_pending = conclusion in PENDING_ASSET_CONCLUSIONS
        if manifest_item.get("pendingAssetReplacement") != expected_pending:
            raise CheckError(
                f"{name} must declare pendingAssetReplacement={expected_pending}; "
                "pending BM-13 items cannot be silently hidden"
            )

    for artifact in lock.get("artifacts", []):
        if artifact["localPath"] not in declared:
            raise CheckError(f"locked dependency is missing: {artifact['localPath']}")
        if declared[artifact["localPath"]]["sha256"] != artifact["sha256"]:
            raise CheckError(f"locked dependency digest drifted: {artifact['localPath']}")
    for sheet in lock.get("stylesheets", []):
        if sheet["localPath"] not in declared:
            raise CheckError(f"generated font stylesheet is missing: {sheet['localPath']}")

    generated = {
        "fileCount": len(declared),
        "aggregateSha256": aggregate_digest(list(declared.values())),
    }
    if generated != lock["generated"]:
        raise CheckError(
            f"catalog aggregate drifted from the lock manifest: {generated} != "
            f"{lock['generated']}"
        )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=LOCK_PATH)
    parser.add_argument("--catalog-root", type=Path, default=None)
    arguments = parser.parse_args()

    lock = load_json(arguments.lock)
    catalog_contract = load_json(CATALOG_CONTRACT_PATH)
    rights = load_json(RIGHTS_PATH)
    catalog_root = arguments.catalog_root or REPOSITORY_ROOT / lock["layout"]["catalogRoot"]
    manifest = verify_catalog(catalog_root, lock, catalog_contract, rights)
    pending = sorted(
        item["name"] for item in manifest["items"] if item["pendingAssetReplacement"]
    )
    print(
        "offline motion catalog is valid: "
        f"{manifest['counts']['items']} items, {manifest['counts']['files']} files, "
        "0 remote URLs, "
        f"{len(pending)} items pending BM-13 asset replacement"
    )


if __name__ == "__main__":
    main()
