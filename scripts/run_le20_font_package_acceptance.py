#!/usr/bin/env python3
"""Stage real registered faces exactly as frozen and run the production gate."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "backend/src"))

import build_material_video_worker_candidate as candidate_builder
import subtitle_font_assets
from automation_tool.executor.captions import fonts as caption_fonts


def run(font_cache: Path) -> dict[str, int]:
    if font_cache.name != subtitle_font_assets.CACHE_NAME or not font_cache.is_dir():
        raise RuntimeError("LE-20 acceptance requires a verified subtitle-fonts cache")
    os.environ[caption_fonts.BUILD_CACHE_OVERRIDE_VARIABLE] = str(font_cache.parent)
    rights = subtitle_font_assets.load_asset_rights()
    entries = {
        (entry.get("bundledIn"), entry.get("packagedName")): entry
        for entry in rights["entries"]
        if entry.get("category") == "font"
    }
    copied_licenses: set[Path] = set()
    payload_bytes = 0
    with tempfile.TemporaryDirectory(prefix="le20-caption-font-candidate-") as directory:
        candidate = Path(directory) / "candidate"
        for font_key, registered in caption_fonts.REGISTERED_CAPTION_FONTS.items():
            source = caption_fonts.resolve_font_file(font_key)
            relative = caption_fonts.packaged_relative_path(font_key)
            destination = candidate / "_internal" / Path(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            payload_bytes += destination.stat().st_size

            entry = entries[(registered.bundle, registered.packaged_name)]
            license_name = entry["packagedLicenseName"]
            license_destination = destination.parent / license_name
            if license_destination in copied_licenses:
                continue
            if registered.bundle == caption_fonts.MATERIAL_VIDEO_WORKER_BUNDLE:
                license_source = font_cache / license_name
            else:
                license_source = subtitle_font_assets.committed_font_license_source(
                    entry
                )
            shutil.copy2(license_source, license_destination)
            payload_bytes += license_destination.stat().st_size
            copied_licenses.add(license_destination)

        candidate_builder.assert_registered_caption_fonts_present(candidate)
    return {
        "fontCount": len(caption_fonts.REGISTERED_CAPTION_FONTS),
        "licenseCount": len(copied_licenses),
        "payloadBytes": payload_bytes,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--font-cache", required=True, type=Path)
    result = run(parser.parse_args().font_cache)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
