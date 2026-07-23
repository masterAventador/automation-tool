#!/usr/bin/env python3
"""Build the App-owned OfflineMotionCatalog from the locked dependency manifest.

Downloads (or verifies cached) remote runtime dependencies into the git-ignored
staging tree, then regenerates the 134-item offline catalog from the read-only
``vendor/hyperframes`` submodule: every audited remote reference is rewritten to
a local relative path, bundled assets are copied unchanged for BM-13, generated
files are digest-locked in ``manifest.json`` and the aggregate digest must match
``contracts/video/offline-motion-dependencies.v1.json``. The submodule is never
modified and no downloaded or generated artifact is committed to git.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
import re
import shutil
import urllib.request
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = REPOSITORY_ROOT / "contracts/video/offline-motion-dependencies.v1.json"
CATALOG_CONTRACT_PATH = REPOSITORY_ROOT / "contracts/quality/motion-catalog.v1.json"
RIGHTS_PATH = REPOSITORY_ROOT / "contracts/quality/motion-catalog-rights.v1.json"
SUBMODULE_ROOT = REPOSITORY_ROOT / "vendor/hyperframes"
TEXT_SUFFIXES = frozenset({".html", ".js", ".css", ".svg"})
URL_PATTERN = re.compile(r"https?://[^\s\"'`<>)\\]+")
ALLOWED_LEFTOVER_DOMAINS = frozenset({"www.w3.org"})
PENDING_ASSET_CONCLUSIONS = frozenset(
    {"needs_asset_replacement", "needs_localization_and_asset_replacement"}
)
DOWNLOAD_USER_AGENT = "automation-tool-offline-motion-catalog/1.0"


class BuildError(SystemExit):
    """Raised when the offline catalog build must fail closed."""

    def __init__(self, message: str) -> None:
        super().__init__(f"offline motion catalog build failed: {message}")


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
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def strip_dependency_root(lock: dict, local_path: str) -> str:
    prefix = lock["layout"]["dependencyRoot"] + "/"
    if not local_path.startswith(prefix):
        raise BuildError(f"artifact path must live under the dependency root: {local_path}")
    return local_path[len(prefix) :]


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": DOWNLOAD_USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.read()
    except OSError as error:
        raise BuildError(f"download failed: {url}: {error}") from error


def verify_downloads(lock: dict, download_root: Path, offline: bool) -> None:
    expected: dict[str, dict] = {}
    for artifact in lock["artifacts"]:
        expected[strip_dependency_root(lock, artifact["localPath"])] = artifact
    for relative, artifact in sorted(expected.items()):
        target = download_root / relative
        if not target.is_file():
            if offline:
                raise BuildError(f"cached download is missing in offline mode: {relative}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(fetch(artifact["downloadUrl"]))
        actual = sha256_file(target)
        if actual != artifact["sha256"]:
            raise BuildError(
                f"download digest mismatch for {relative}: {actual} != {artifact['sha256']}"
            )
        if target.stat().st_size != artifact["bytes"]:
            raise BuildError(f"download size mismatch for {relative}")
    if download_root.is_dir():
        for path in sorted(download_root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(download_root).as_posix()
            if relative not in expected:
                raise BuildError(f"unexpected file in the download staging tree: {relative}")


def rewrite_rules(lock: dict) -> dict:
    exact: dict[str, str] = {}
    for artifact in lock["artifacts"]:
        for url in artifact["originalUrls"]:
            if url in exact:
                raise BuildError(f"duplicate rewrite for {url}")
            exact[url] = artifact["localPath"]
    for sheet in lock["stylesheets"]:
        if sheet["originalUrl"] in exact:
            raise BuildError(f"duplicate rewrite for {sheet['originalUrl']}")
        exact[sheet["originalUrl"]] = sheet["localPath"]
    rewrites = lock["rewrites"]
    return {
        "exact": exact,
        "prefixes": [
            (rule["from"], rule["toLocalPath"]) for rule in rewrites["prefixReplacements"]
        ],
        "texts": [(rule["from"], rule["to"]) for rule in rewrites["textReplacements"]],
        "removeDomains": list(rewrites["removeLinkDomains"]),
    }


def rewrite_text(text: str, rules: dict, depth: int) -> str:
    relative_prefix = "../" * depth
    for domain in rules["removeDomains"]:
        pattern = re.compile(
            rf"[ \t]*<link\b[^>]*href=\"https://{re.escape(domain)}\"[^>]*/?>[ \t]*\r?\n?"
        )
        text = pattern.sub("", text)
    for url in sorted(rules["exact"], key=len, reverse=True):
        text = text.replace(url, relative_prefix + rules["exact"][url])
    for prefix, local in rules["prefixes"]:
        text = text.replace(prefix, relative_prefix + local)
    for source, replacement in rules["texts"]:
        text = text.replace(source, replacement)
    leftovers = sorted(
        {
            url
            for url in URL_PATTERN.findall(text)
            if url.split("/")[2] not in ALLOWED_LEFTOVER_DOMAINS
        }
    )
    if leftovers:
        raise BuildError(f"unlocalized remote URLs remain after rewriting: {leftovers}")
    return text


def stylesheet_css(sheet: dict) -> str:
    css_dir = posixpath.dirname(sheet["localPath"])
    blocks = []
    for face in sheet["faces"]:
        relative = posixpath.relpath(face["artifactPath"], css_dir)
        blocks.append(
            "/* {subset} */\n@font-face {{\n"
            "  font-family: '{family}';\n"
            "  font-style: {style};\n"
            "  font-weight: {weight};\n"
            "  font-display: {display};\n"
            "  src: url({relative}) format('woff2');\n"
            "  unicode-range: {unicode_range};\n"
            "}}\n".format(
                subset=face["subset"],
                family=face["family"],
                style=face["style"],
                weight=face["weight"],
                display=face["display"],
                relative=relative,
                unicode_range=face["unicodeRange"],
            )
        )
    return "\n".join(blocks)


def generate_catalog(
    lock: dict,
    catalog_contract: dict,
    rights: dict,
    submodule_root: Path,
    download_root: Path,
    catalog_root: Path,
) -> dict:
    conclusions = {entry["name"]: entry["conclusion"] for entry in rights["items"]}
    rules = rewrite_rules(lock)
    item_root = lock["layout"]["itemRoot"]
    if catalog_root.exists():
        shutil.rmtree(catalog_root)
    catalog_root.mkdir(parents=True)

    manifest_items = []
    for item in catalog_contract["items"]:
        name = item["name"]
        conclusion = conclusions.get(name)
        if conclusion is None:
            raise BuildError(f"{name} has no rights conclusion")
        item_files = []
        for record in item["files"]:
            source = submodule_root / item["path"] / record["path"]
            if not source.is_file() or source.is_symlink():
                raise BuildError(f"{name} source file is missing: {record['path']}")
            if sha256_file(source) != record["sha256"]:
                raise BuildError(
                    f"{name} source drifted from the frozen catalog contract: {record['path']}"
                )
            destination_relative = f"{item_root}/{name}/{record['path']}"
            destination = catalog_root / destination_relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            suffix = Path(record["path"]).suffix.lower()
            if suffix in TEXT_SUFFIXES:
                depth = len(Path(destination_relative).parts) - 1
                rewritten = rewrite_text(
                    source.read_text(encoding="utf-8"), rules, depth=depth
                )
                destination.write_text(rewritten, encoding="utf-8")
            else:
                shutil.copyfile(source, destination)
            item_files.append(destination_relative)
        manifest_items.append(
            {
                "name": name,
                "type": item["type"],
                "pendingAssetReplacement": conclusion in PENDING_ASSET_CONCLUSIONS,
                "files": item_files,
            }
        )

    for artifact in lock["artifacts"]:
        source = download_root / strip_dependency_root(lock, artifact["localPath"])
        destination = catalog_root / artifact["localPath"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    for sheet in lock["stylesheets"]:
        destination = catalog_root / sheet["localPath"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(stylesheet_css(sheet), encoding="utf-8")

    files = []
    for path in sorted(catalog_root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(catalog_root).as_posix()
        if relative == "manifest.json":
            raise BuildError("manifest.json must not pre-exist in a fresh catalog")
        files.append(
            {"path": relative, "sha256": sha256_file(path), "bytes": path.stat().st_size}
        )
    manifest = {
        "schemaVersion": 1,
        "source": dict(lock["source"]),
        "counts": {"items": len(manifest_items), "files": len(files)},
        "items": manifest_items,
        "files": files,
    }
    (catalog_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def aggregate_digest(files: list[dict]) -> str:
    lines = "".join(
        f"{record['path']} {record['sha256']}\n"
        for record in sorted(files, key=lambda record: record["path"])
    )
    return sha256_bytes(lines.encode("utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=LOCK_PATH)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument(
        "--record-generated",
        action="store_true",
        help="write the generated aggregate back into the lock manifest (relock only)",
    )
    arguments = parser.parse_args()

    lock = load_json(arguments.lock)
    catalog_contract = load_json(CATALOG_CONTRACT_PATH)
    rights = load_json(RIGHTS_PATH)
    download_root = REPOSITORY_ROOT / lock["layout"]["downloadRoot"]
    catalog_root = REPOSITORY_ROOT / lock["layout"]["catalogRoot"]

    verify_downloads(lock, download_root, offline=arguments.offline)
    manifest = generate_catalog(
        lock, catalog_contract, rights, SUBMODULE_ROOT, download_root, catalog_root
    )
    aggregate = aggregate_digest(manifest["files"])
    generated = {"fileCount": len(manifest["files"]), "aggregateSha256": aggregate}
    if arguments.record_generated:
        lock["generated"] = generated
        arguments.lock.write_text(
            json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"lock manifest generated block updated: {generated}")
    elif lock["generated"] != generated:
        raise BuildError(
            "generated catalog drifted from the locked aggregate: "
            f"{generated} != {lock['generated']}"
        )
    pending = sum(item["pendingAssetReplacement"] for item in manifest["items"])
    print(
        "offline motion catalog built: "
        f"{manifest['counts']['items']} items, {manifest['counts']['files']} files, "
        f"{pending} items pending BM-13 asset replacement"
    )


if __name__ == "__main__":
    main()
