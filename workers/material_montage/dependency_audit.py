#!/usr/bin/env python3
"""Create a deterministic license inventory for one isolated runtime environment."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path


def normalized_license(distribution: importlib.metadata.Distribution) -> str:
    metadata = distribution.metadata
    for field in ("License-Expression", "License"):
        value = metadata.get(field)
        if value and value.strip().upper() not in {"UNKNOWN", "NONE"}:
            return value.strip()
    classifiers = [
        classifier.removeprefix("License :: ")
        for classifier in metadata.get_all("Classifier", [])
        if classifier.startswith("License :: ")
    ]
    if classifiers:
        return "; ".join(sorted(classifiers))
    raise RuntimeError("dependency license metadata is unavailable")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inventory() -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata.get("Name")
        if not name:
            raise RuntimeError("dependency name is unavailable")
        license_files: list[dict[str, str]] = []
        for relative in distribution.files or ():
            lowered = relative.name.lower()
            if not any(token in lowered for token in ("license", "copying", "notice")):
                continue
            resolved = Path(distribution.locate_file(relative))
            if resolved.is_file() and not resolved.is_symlink():
                license_files.append(
                    {
                        "path": str(relative).replace("\\", "/"),
                        "sha256": sha256(resolved),
                    }
                )
        entries.append(
            {
                "name": name,
                "version": distribution.version,
                "license": normalized_license(distribution),
                "licenseFiles": sorted(license_files, key=lambda item: item["path"]),
            }
        )
    return sorted(entries, key=lambda item: str(item["name"]).lower())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    if arguments.output.exists() or not arguments.output.parent.is_dir():
        raise RuntimeError("license inventory output is unsafe")
    entries = inventory()
    document = {
        "schemaVersion": 1,
        "distributionCount": len(entries),
        "distributions": entries,
    }
    arguments.output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
