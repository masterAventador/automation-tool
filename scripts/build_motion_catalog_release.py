#!/usr/bin/env python3
"""Compose the read-only, versioned 134-item motion catalog release (BM-14).

Inputs are the BM-12 staged ``OfflineMotionCatalog`` (verified with the full
BM-12 static gate first), the BM-13 asset overlay and the frozen BM-11
contracts; every input contract is digest-pinned by
``contracts/video/motion-catalog-release.v1.json``. The composition swaps every
registered bundled sample asset for its App-owned overlay replacement (renaming
files and rewriting literal references when the media type changes), applies
the frozen trademark-indicator replacements to item text files while protecting
catalog item ids and the closed technical keeplist, then writes a deterministic
``manifest.json`` and marks every file read-only. The submodule is never
touched and the release tree is never committed to git; missing items, remote
URLs, unregistered assets, indicator leftovers or digest drift fail the build.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import posixpath
import re
import shutil
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
RELEASE_LOCK_PATH = REPOSITORY_ROOT / "contracts/video/motion-catalog-release.v1.json"
DEP_LOCK_PATH = REPOSITORY_ROOT / "contracts/video/offline-motion-dependencies.v1.json"
CATALOG_CONTRACT_PATH = REPOSITORY_ROOT / "contracts/quality/motion-catalog.v1.json"
RIGHTS_PATH = REPOSITORY_ROOT / "contracts/quality/motion-catalog-rights.v1.json"
OVERLAY_PATH = REPOSITORY_ROOT / "contracts/quality/motion-asset-overlay.v1.json"
STAGED_GATE_PATH = REPOSITORY_ROOT / "scripts/check_offline_motion_catalog.py"
TEXT_SUFFIXES = frozenset({".html", ".js", ".css", ".svg"})
READ_ONLY_MODE = 0o444
WRITABLE_MODE = 0o644


class BuildError(SystemExit):
    """Raised when the release composition must fail closed."""

    def __init__(self, message: str) -> None:
        super().__init__(f"motion catalog release build failed: {message}")


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BuildError(f"cannot read {path}: {error}") from error
    if not isinstance(value, dict):
        raise BuildError(f"{path} must contain an object")
    return value


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def aggregate_digest(files: list[dict]) -> str:
    lines = "".join(
        f"{record['path']} {record['sha256']}\n"
        for record in sorted(files, key=lambda record: record["path"])
    )
    return sha256_bytes(lines.encode("utf-8"))


def verify_input_digests(release_lock: dict) -> None:
    for key, record in sorted(release_lock["inputs"].items()):
        actual_path = REPOSITORY_ROOT / record["path"]
        if not actual_path.is_file():
            raise BuildError(f"pinned input contract is missing: {record['path']}")
        actual = sha256_file(actual_path)
        if actual != record["sha256"]:
            raise BuildError(
                f"input contract drifted from the release lock pin ({key}): "
                f"{actual} != {record['sha256']}"
            )


def load_gate(path: Path):
    """Import a gate script by path so its own checks are reused, not restated."""
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_staged_gate(staged_root: Path, dep_lock: dict, catalog_contract: dict, rights: dict):
    """Re-run the full BM-12 static gate before composing anything."""
    module = load_gate(STAGED_GATE_PATH)
    try:
        return module.verify_catalog(staged_root, dep_lock, catalog_contract, rights)
    except SystemExit as error:
        raise BuildError(f"staged BM-12 catalog is not valid: {error}") from error


def trademark_ruleset(release_lock: dict, replacements: dict, item_names: list[str]) -> dict:
    forms = release_lock["trademarkScan"]["forms"]
    keeplist = release_lock["trademarkScan"]["technicalKeeplist"]

    def boundary_pattern(literal: str) -> re.Pattern[str]:
        return re.compile(
            r"(?<![0-9A-Za-z])" + re.escape(literal) + r"(?![0-9A-Za-z])", re.IGNORECASE
        )

    replace_rules = []
    for token in sorted(replacements):
        literals = forms.get(token)
        if not literals:
            raise BuildError(f"trademark indicator has no literal forms in the lock: {token}")
        for literal in sorted(literals, key=len, reverse=True):
            replace_rules.append((boundary_pattern(literal), replacements[token]))
    scan_rules = [
        boundary_pattern(literal)
        for token in sorted(forms)
        for literal in sorted(forms[token], key=len, reverse=True)
    ]
    protected = [
        re.compile(re.escape(entry), re.IGNORECASE)
        for entry in sorted(set(item_names) | set(keeplist), key=len, reverse=True)
    ]
    return {"replace": replace_rules, "scan": scan_rules, "protected": protected}


def _protected_spans(text: str, ruleset: dict) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for pattern in ruleset["protected"]:
        spans.extend(match.span() for match in pattern.finditer(text))
    return spans


def _is_protected(spans: list[tuple[int, int]], start: int, end: int) -> bool:
    return any(begin <= start and end <= stop for begin, stop in spans)


def apply_trademark(text: str, ruleset: dict) -> str:
    for pattern, replacement in ruleset["replace"]:
        spans = _protected_spans(text, ruleset)
        pieces: list[str] = []
        cursor = 0
        for match in pattern.finditer(text):
            if _is_protected(spans, match.start(), match.end()):
                continue
            pieces.append(text[cursor : match.start()])
            pieces.append(replacement)
            cursor = match.end()
        pieces.append(text[cursor:])
        text = "".join(pieces)
    return text


def find_trademark_leftovers(text: str, ruleset: dict) -> list[str]:
    spans = _protected_spans(text, ruleset)
    leftovers = {
        match.group(0)
        for pattern in ruleset["scan"]
        for match in pattern.finditer(text)
        if not _is_protected(spans, match.start(), match.end())
    }
    return sorted(leftovers)


def composed_asset_path(
    source_path: str, replacement_path: str, has_literal_reference: bool
) -> str:
    directory = posixpath.dirname(source_path)
    source_name = posixpath.basename(source_path)
    replacement_name = posixpath.basename(replacement_path)
    same_suffix = (
        posixpath.splitext(source_name)[1].lower()
        == posixpath.splitext(replacement_name)[1].lower()
    )
    # Dynamically addressed files (no literal reference, same media type) keep
    # their upstream basename so name-based lookups in item scripts resolve.
    name = replacement_name if has_literal_reference or not same_suffix else source_name
    return posixpath.join(directory, name) if directory else name


def load_overlay_assets(overlay: dict) -> dict[str, dict]:
    asset_root = REPOSITORY_ROOT / overlay["assetRoot"]
    registry: dict[str, dict] = {}
    for asset in overlay["assets"]:
        path = asset_root / asset["path"]
        if not path.is_file() or path.is_symlink():
            raise BuildError(f"overlay asset is missing: {asset['path']}")
        data = path.read_bytes()
        if sha256_bytes(data) != asset["sha256"] or len(data) != asset["bytes"]:
            raise BuildError(f"overlay asset drifted from its registered digest: {asset['path']}")
        registry[asset["id"]] = {"data": data, "record": asset}
    return registry


def _reset_release_root(release_root: Path) -> None:
    if release_root.exists():
        for path in release_root.rglob("*"):
            if path.is_file() and not path.is_symlink():
                path.chmod(WRITABLE_MODE)
        shutil.rmtree(release_root)
    release_root.mkdir(parents=True)


def _compose_item(
    item: dict,
    overlay_item: dict | None,
    ruleset: dict,
    assets: dict[str, dict],
    staged_item_dir: Path,
    release_item_dir: Path,
    item_prefix: str,
) -> dict:
    name = item["name"]
    contract_paths = [record["path"] for record in item["files"]]
    staged_text: dict[str, str] = {}
    for relative in contract_paths:
        source = staged_item_dir / relative
        if not source.is_file() or source.is_symlink():
            raise BuildError(f"{name} staged file is missing: {relative}")
        if Path(relative).suffix.lower() in TEXT_SUFFIXES:
            staged_text[relative] = source.read_text(encoding="utf-8")
    text_blob = "".join(staged_text.values())

    replacements = overlay_item["assetReplacements"] if overlay_item else []
    mapping: dict[str, dict] = {}
    for rule in replacements:
        source_path = rule["sourcePath"]
        if source_path not in contract_paths:
            raise BuildError(f"{name} overlay references an unknown source file: {source_path}")
        if source_path in mapping:
            raise BuildError(f"{name} has duplicate overlay replacements for {source_path}")
        asset = assets.get(rule["assetId"])
        if asset is None:
            raise BuildError(f"{name} references an unregistered overlay asset: {rule['assetId']}")
        composed = composed_asset_path(
            source_path, rule["replacementPath"], text_blob.count(source_path) > 0
        )
        mapping[source_path] = {"composedPath": composed, "assetId": rule["assetId"]}

    # Several source files may share one neutral overlay asset (e.g. multiple
    # chat icons); they legitimately merge into a single composed file. Any
    # other collision between composed paths must fail.
    composed_owner: dict[str, str] = {}
    for path in contract_paths:
        entry = mapping.get(path)
        composed = entry["composedPath"] if entry else path
        owner = entry["assetId"] if entry else f"source:{path}"
        if composed_owner.setdefault(composed, owner) != owner:
            raise BuildError(f"{name} composed file paths collide: {composed}")

    reference_rewrites = sorted(
        (
            (source_path, entry["composedPath"])
            for source_path, entry in mapping.items()
            if entry["composedPath"] != source_path
        ),
        key=lambda pair: len(pair[0]),
        reverse=True,
    )
    item_files: set[str] = set()
    written: set[str] = set()
    for relative in contract_paths:
        entry = mapping.get(relative)
        destination_relative = entry["composedPath"] if entry else relative
        if destination_relative in written:
            item_files.add(f"{item_prefix}/{destination_relative}")
            continue
        written.add(destination_relative)
        destination = release_item_dir / destination_relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if entry is not None:
            # Overlay assets are copied verbatim so their bytes keep matching
            # the digests registered by the BM-13 overlay contract.
            destination.write_bytes(assets[entry["assetId"]]["data"])
        elif relative in staged_text:
            text = staged_text[relative]
            for source_path, composed in reference_rewrites:
                text = text.replace(source_path, composed)
            text = apply_trademark(text, ruleset)
            for source_path, _composed in reference_rewrites:
                if source_path in text:
                    raise BuildError(
                        f"{name} still references a replaced asset path: {source_path}"
                    )
            leftovers = find_trademark_leftovers(text, ruleset)
            if leftovers:
                raise BuildError(f"{name} keeps trademark indicators: {leftovers}")
            destination.write_text(text, encoding="utf-8", newline="\n")
        else:
            shutil.copyfile(staged_item_dir / relative, destination)
        item_files.add(f"{item_prefix}/{destination_relative}")

    return {
        "name": name,
        "type": item["type"],
        "files": sorted(item_files),
        "assetReplacements": [
            {
                "sourcePath": source_path,
                "composedPath": mapping[source_path]["composedPath"],
                "assetId": mapping[source_path]["assetId"],
            }
            for source_path in sorted(mapping)
        ],
        "trademarkReplacements": len(overlay_item["trademarkReplacements"]) if overlay_item else 0,
    }


def build_release(
    release_lock: dict,
    dep_lock: dict,
    catalog_contract: dict,
    rights: dict,
    overlay: dict,
    staged_root: Path,
    release_root: Path,
) -> dict:
    verify_input_digests(release_lock)
    run_staged_gate(staged_root, dep_lock, catalog_contract, rights)
    assets = load_overlay_assets(overlay)
    overlay_items = {entry["name"]: entry for entry in overlay["items"]}
    known_names = {item["name"] for item in catalog_contract["items"]}
    unknown = sorted(set(overlay_items) - known_names)
    if unknown:
        raise BuildError(f"overlay covers unknown catalog items: {unknown}")

    item_root = release_lock["layout"]["itemRoot"]
    dependency_root = release_lock["layout"]["dependencyRoot"]
    item_names = sorted(known_names)
    _reset_release_root(release_root)

    manifest_items = []
    for item in catalog_contract["items"]:
        name = item["name"]
        overlay_item = overlay_items.get(name)
        replacements = {
            rule["indicator"]: rule["replacement"]
            for rule in (overlay_item["trademarkReplacements"] if overlay_item else [])
        }
        ruleset = trademark_ruleset(release_lock, replacements, item_names)
        manifest_items.append(
            _compose_item(
                item,
                overlay_item,
                ruleset,
                assets,
                staged_root / item_root / name,
                release_root / item_root / name,
                f"{item_root}/{name}",
            )
        )

    for path in sorted(staged_root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(staged_root).as_posix()
        if relative == "manifest.json" or relative.startswith(f"{item_root}/"):
            continue
        if not relative.startswith(f"{dependency_root}/"):
            raise BuildError(f"staged catalog contains an unexpected tree: {relative}")
        destination = release_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)

    files = []
    casefold_paths: dict[str, str] = {}
    for path in sorted(release_root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(release_root).as_posix()
        if relative == "manifest.json":
            raise BuildError("manifest.json must not pre-exist in a fresh release tree")
        folded = relative.casefold()
        existing = casefold_paths.get(folded)
        if existing is not None and existing != relative:
            raise BuildError(
                f"release paths collide on a case-insensitive filesystem: {existing} != {relative}"
            )
        casefold_paths[folded] = relative
        files.append({"path": relative, "sha256": sha256_file(path), "bytes": path.stat().st_size})
    manifest = {
        "schemaVersion": 1,
        "catalogVersion": release_lock["catalogVersion"],
        "inputs": release_lock["inputs"],
        "counts": {"items": len(manifest_items), "files": len(files)},
        "items": manifest_items,
        "files": files,
    }
    (release_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    for path in sorted(release_root.rglob("*")):
        if path.is_file():
            path.chmod(READ_ONLY_MODE)
    return manifest


def default_release_root(release_lock: dict | None = None) -> Path:
    """Where an ordinary build puts the read-only release tree."""
    lock = release_lock or load_json(RELEASE_LOCK_PATH)
    return REPOSITORY_ROOT / lock["layout"]["releaseRoot"] / lock["catalogVersion"]


def stage_for_release(*, staging: Path, release_root: Path | None = None) -> Path:
    """Copy the built release tree into a release staging area, verified first.

    The release packager stages every resource tree under one directory and
    installs from there. This is the catalog's entry into that path, and it is
    the reason PC-16 exists: until now the 134 parts were only ever a build
    artifact under `.local/`, so a signed, notarised package carried none of
    them and nothing refused to ship it.

    The tree is re-verified against the aggregate digest locked in
    `motion-catalog-release.v1.json` before a byte is copied. Staging an
    unverified tree would put the one thing the lock exists to guarantee — that
    the parts a customer renders are the parts the gates checked — behind
    whatever happened to be on the build machine's disk.
    """
    source = release_root or default_release_root()
    if not source.is_dir():
        raise BuildError(
            f"the release tree is not built at {source}; run this script first"
        )
    verify = load_gate(REPOSITORY_ROOT / "scripts/check_motion_catalog_release.py")
    verify.verify_release(
        source,
        load_json(RELEASE_LOCK_PATH),
        load_json(DEP_LOCK_PATH),
        load_json(CATALOG_CONTRACT_PATH),
        load_json(OVERLAY_PATH),
    )
    return _stage_tree(name="motion-catalog", source=source, staging=staging)


def _stage_tree(*, name: str, source: Path, staging: Path) -> Path:
    destination = staging / name
    if destination.exists():
        shutil.rmtree(destination)
    staging.mkdir(parents=True, exist_ok=True)
    # The release tree is deliberately read-only; a staged copy has to be
    # writable or the bundler cannot re-own the files it installs.
    shutil.copytree(source, destination)
    for path in destination.rglob("*"):
        if path.is_file():
            path.chmod(WRITABLE_MODE)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-lock", type=Path, default=RELEASE_LOCK_PATH)
    parser.add_argument("--staged-root", type=Path, default=None)
    parser.add_argument("--release-root", type=Path, default=None)
    parser.add_argument(
        "--record-generated",
        action="store_true",
        help="write the generated aggregate back into the release lock (relock only)",
    )
    arguments = parser.parse_args()

    release_lock = load_json(arguments.release_lock)
    dep_lock = load_json(DEP_LOCK_PATH)
    catalog_contract = load_json(CATALOG_CONTRACT_PATH)
    rights = load_json(RIGHTS_PATH)
    overlay = load_json(OVERLAY_PATH)
    staged_root = arguments.staged_root or REPOSITORY_ROOT / dep_lock["layout"]["catalogRoot"]
    release_root = arguments.release_root or (
        REPOSITORY_ROOT / release_lock["layout"]["releaseRoot"] / release_lock["catalogVersion"]
    )

    manifest = build_release(
        release_lock, dep_lock, catalog_contract, rights, overlay, staged_root, release_root
    )
    generated = {
        "fileCount": len(manifest["files"]),
        "aggregateSha256": aggregate_digest(manifest["files"]),
    }
    if arguments.record_generated:
        release_lock["generated"] = generated
        arguments.release_lock.write_text(
            json.dumps(release_lock, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print(f"release lock generated block updated: {generated}")
    elif release_lock["generated"] != generated:
        raise BuildError(
            "generated release drifted from the locked aggregate: "
            f"{generated} != {release_lock['generated']}"
        )
    applied = sum(len(item["assetReplacements"]) for item in manifest["items"])
    replaced_items = sum(bool(item["trademarkReplacements"]) for item in manifest["items"])
    print(
        "motion catalog release built: "
        f"version {manifest['catalogVersion']}, {manifest['counts']['items']} items, "
        f"{manifest['counts']['files']} files, {applied} asset replacements, "
        f"{replaced_items} items with trademark replacements"
    )


if __name__ == "__main__":
    main()
