#!/usr/bin/env python3
"""Third-party software notice UI projection builder.

Composes ``contracts/quality/third-party-notice-ui.v1.json`` from the locked
source, asset-rights and motion-rights contracts: the upstream projects the
legal disclosure page has to name, plus the font and material rights summary it
publishes.

The projection is the only one of these payloads the frontend may import. The
source contracts also carry the internal rights review — per-item trademark
indicators, bundled sample asset paths, CDN addresses, verification markers —
which is 87 KB of data no user reads, so none of it is projected. Chinese
wording stays in the page: this file carries facts, not copy.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCES_PATH = REPOSITORY_ROOT / "contracts/quality/third-party-sources.v1.json"
ASSET_RIGHTS_PATH = REPOSITORY_ROOT / "contracts/quality/asset-rights-policy.v1.json"
MOTION_RIGHTS_PATH = REPOSITORY_ROOT / "contracts/quality/motion-catalog-rights.v1.json"
PROJECTION_PATH = REPOSITORY_ROOT / "contracts/quality/third-party-notice-ui.v1.json"

SCHEMA_VERSION = "1.0"
PROJECTION_ID = "automation-tool.third-party-notice-ui.v1"

CLEARED_CONCLUSION = "cleared"
REPOSITORY_ADDRESS = re.compile(r"^https://[^/]+/([^/]+)/([^/]+?)(?:\.git)?$")


class ProjectionError(RuntimeError):
    """Raised when the projection cannot be composed safely."""


def _load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ProjectionError(f"{path.name} must contain an object")
    return value


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProjectionError(f"{field} must be a non-empty string")
    return value


def _attribute(identifier: str, url: str) -> tuple[str, str]:
    """Derive ``owner/repository`` and the repository name from the locked URL.

    Deriving beats restating: the page can never disagree with the address the
    source lock and the submodule gate already agree on.
    """
    match = REPOSITORY_ADDRESS.match(url)
    if match is None:
        raise ProjectionError(f"{identifier}: url is not an attributable repository")
    owner, name = match.group(1), match.group(2)
    return f"{owner}/{name}", name


def _upstream_projects(sources: dict) -> list[dict]:
    entries = sources.get("sources")
    if not isinstance(entries, list) or not entries:
        raise ProjectionError("the source lock discloses no upstream project")
    # The lock's order is meaningful and already deterministic, so it is kept
    # rather than re-sorted: the page reads in the order the product uses them.
    projects = []
    for entry in entries:
        identifier = _text(entry.get("id"), "source id")
        url = _text(entry.get("url"), f"{identifier}: url")
        commit = _text(entry.get("commit"), f"{identifier}: commit")
        if len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit):
            raise ProjectionError(f"{identifier}: commit must be a full lowercase SHA-1")
        licence = entry.get("license")
        if not isinstance(licence, dict):
            raise ProjectionError(f"{identifier}: license must be an object")
        repository, name = _attribute(identifier, url)
        projects.append(
            {
                "id": identifier,
                "name": name,
                "repository": repository,
                "sourceUrl": url,
                "version": _text(entry.get("tag"), f"{identifier}: tag"),
                "commit": commit,
                "license": _text(licence.get("spdx"), f"{identifier}: license.spdx"),
            }
        )
    return projects


def _asset_rights(policy: dict) -> dict:
    shared = policy.get("distributionRequiredFields")
    if not isinstance(shared, list) or not shared:
        raise ProjectionError("the rights policy requires no distribution information")
    categories = policy.get("requiredCategories")
    if not isinstance(categories, dict) or not categories:
        raise ProjectionError("the rights policy covers no category")
    entries = policy.get("entries")
    if not isinstance(entries, list):
        raise ProjectionError("the rights policy has no register")
    projected = []
    for identifier in sorted(categories):
        fields = categories[identifier]
        if not isinstance(fields, list) or not fields:
            raise ProjectionError(f"{identifier}: category requires no rights information")
        projected.append({"id": identifier, "requiredFieldCount": len(fields)})
    return {
        "deniedByDefault": policy.get("defaultDecision") == "deny",
        "sharedRequiredFieldCount": len(shared),
        "registeredEntryCount": len(entries),
        "categories": projected,
    }


def _motion_asset_rights(review: dict) -> dict:
    stats = review.get("stats")
    if not isinstance(stats, dict):
        raise ProjectionError("the motion rights review has no summary")
    counts = stats.get("conclusionCounts")
    if not isinstance(counts, dict) or not counts:
        raise ProjectionError("the motion rights review counted no conclusion")
    total = sum(counts.values())
    if total <= 0:
        raise ProjectionError("the motion rights review counted no part")
    cleared = counts.get(CLEARED_CONCLUSION, 0)
    packages = review.get("remoteDependencyPackages")
    if not isinstance(packages, list) or not packages:
        raise ProjectionError("the motion rights review lists no borrowed package")
    dependencies = []
    for package in packages:
        name = _text(package.get("package"), "borrowed package name")
        count = package.get("itemCount")
        if not isinstance(count, int) or count <= 0:
            raise ProjectionError(f"{name}: borrowed package is used by no part")
        dependencies.append(
            {
                "name": name,
                "license": _text(package.get("assumedLicense"), f"{name}: assumedLicense"),
                "partCount": count,
            }
        )
    licence = review.get("codeLicense")
    source = review.get("source")
    if not isinstance(licence, dict) or not isinstance(source, dict):
        raise ProjectionError("the motion rights review discloses no licence or version")
    return {
        "codeLicense": _text(licence.get("spdx"), "motion codeLicense.spdx"),
        "version": _text(source.get("tag"), "motion source.tag"),
        "totalPartCount": total,
        "clearedPartCount": cleared,
        "partsNeedingWorkCount": total - cleared,
        "webFontFamilyCount": stats["googleFontFamilyCount"],
        "bundledSampleAssetPartCount": stats["itemsWithBundledSampleAssets"],
        "networkDependentPartCount": stats["itemsWithRuntimeRemoteDependencies"],
        "dependencies": dependencies,
    }


def compose_projection() -> dict:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "id": PROJECTION_ID,
        "upstreamProjects": _upstream_projects(_load(SOURCES_PATH)),
        "assetRights": _asset_rights(_load(ASSET_RIGHTS_PATH)),
        "motionAssetRights": _motion_asset_rights(_load(MOTION_RIGHTS_PATH)),
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
        "third-party notice ui projection written:",
        f"{len(projection['upstreamProjects'])} upstream projects,",
        f"{len(projection['motionAssetRights']['dependencies'])} borrowed packages",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
