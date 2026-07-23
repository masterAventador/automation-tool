#!/usr/bin/env python3
"""Write the runtime manifest after a locked media-toolchain build."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("target_id", choices=("macos-arm64", "windows-x86_64"))
    args = parser.parse_args()
    root = args.root.resolve()
    if root.is_symlink() or not root.is_dir():
        raise SystemExit("media toolchain root must be a real directory")
    manifest_path = root / "manifest.json"
    files = []
    for path in sorted(root.rglob("*")):
        if path == manifest_path or path.is_symlink():
            continue
        if path.is_file():
            files.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "size": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
    document = {
        "schema_version": 1,
        "target_id": args.target_id,
        "version": "8.1.2",
        "license": "GPL-3.0-or-later",
        "files": files,
    }
    temporary = root / ".manifest.json.tmp"
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, manifest_path)
    print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
