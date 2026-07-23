#!/usr/bin/env python3
"""Contract and tamper tests for the BM-13 motion asset overlay."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from check_motion_asset_overlay import CheckError, verify_overlay

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
RIGHTS_PATH = REPOSITORY_ROOT / "contracts/quality/motion-catalog-rights.v1.json"
OVERLAY_PATH = REPOSITORY_ROOT / "contracts/quality/motion-asset-overlay.v1.json"
ASSET_ROOT = REPOSITORY_ROOT / "assets/motion-catalog-overlay"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def expect_failure(callback, expected: str) -> None:
    try:
        callback()
    except CheckError as error:
        assert expected in str(error), f"{expected!r} not in {error!s}"
    else:
        raise AssertionError(f"expected failure containing {expected!r}")


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    counts = verify_overlay(OVERLAY_PATH, ASSET_ROOT, RIGHTS_PATH)
    assert counts == {
        "items": 70,
        "assetReplacementReferences": 150,
        "trademarkReplacementItems": 68,
        "trademarkReplacementReferences": 121,
        "assets": 44,
    }

    source_overlay = json.loads(OVERLAY_PATH.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="bm-13-overlay-test-") as temporary:
        root = Path(temporary)
        asset_root = root / "assets"
        shutil.copytree(ASSET_ROOT, asset_root)
        overlay_path = root / "overlay.json"
        write_json(overlay_path, source_overlay)

        tampered_asset = asset_root / source_overlay["assets"][0]["path"]
        tampered_asset.write_bytes(tampered_asset.read_bytes() + b"x")
        expect_failure(
            lambda: verify_overlay(overlay_path, asset_root, RIGHTS_PATH),
            "asset byte count drifted",
        )
        shutil.copy2(ASSET_ROOT / source_overlay["assets"][0]["path"], tampered_asset)

        missing_mapping = copy.deepcopy(source_overlay)
        item = next(entry for entry in missing_mapping["items"] if entry["assetReplacements"])
        item["assetReplacements"].pop()
        write_json(overlay_path, missing_mapping)
        expect_failure(
            lambda: verify_overlay(overlay_path, asset_root, RIGHTS_PATH),
            "asset replacement coverage drifted",
        )

        missing_trademark = copy.deepcopy(source_overlay)
        item = next(entry for entry in missing_trademark["items"] if entry["trademarkReplacements"])
        item["trademarkReplacements"].pop()
        write_json(overlay_path, missing_trademark)
        expect_failure(
            lambda: verify_overlay(overlay_path, asset_root, RIGHTS_PATH),
            "trademark replacement coverage drifted",
        )

        remote_asset = copy.deepcopy(source_overlay)
        svg = next(record for record in remote_asset["assets"] if record["path"].endswith(".svg"))
        svg_path = asset_root / svg["path"]
        svg_path.write_text(
            svg_path.read_text(encoding="utf-8").replace(
                "</svg>", '<image href="https://remote.invalid/a.png"/></svg>'
            ),
            encoding="utf-8",
        )
        svg["bytes"] = svg_path.stat().st_size
        svg["sha256"] = sha256_file(svg_path)
        write_json(overlay_path, remote_asset)
        expect_failure(
            lambda: verify_overlay(overlay_path, asset_root, RIGHTS_PATH),
            "remote URL remains",
        )
        shutil.copy2(ASSET_ROOT / svg["path"], svg_path)

        write_json(overlay_path, source_overlay)
        (asset_root / "undeclared.bin").write_bytes(b"undeclared")
        expect_failure(
            lambda: verify_overlay(overlay_path, asset_root, RIGHTS_PATH),
            "missing or undeclared files",
        )

    print(
        "motion asset overlay tests passed: "
        "70 items, 150 asset references, 68 trademark items/121 references, "
        "44 rights-reviewed assets and 5 tamper cases"
    )


if __name__ == "__main__":
    main()
