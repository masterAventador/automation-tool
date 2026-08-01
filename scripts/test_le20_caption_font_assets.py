#!/usr/bin/env python3
"""LE-20 T2: locked Plangothic assets, rights metadata and cache manifest."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import subtitle_font_assets

PLANGOTHIC_ATTRIBUTION = (
    "Copyright (c) 2024 by Fitzgerald P. Köeingsegg. All rights reserved."
)
PLANGOTHIC_FONTS = {
    "PlangothicP1-Regular.ttf": {
        "bytes": 20410664,
        "sha256": "550b5d0775b15405946b18f4843df439a51e69508d7e6778d94c1f7a53dc5ad6",
    },
    "PlangothicP2-Regular.ttf": {
        "bytes": 12459248,
        "sha256": "681933370adfe0fc7253f77735275a82fea09fe4f8adba907bdeb46c110daf8f",
    },
}


def test_plangothic_release_assets_are_exactly_locked_in_the_rights_sbom() -> None:
    fonts = {
        font.packaged_name: font for font in subtitle_font_assets.bundled_subtitle_fonts()
    }
    assert set(PLANGOTHIC_FONTS) < set(fonts)
    for name, expected in PLANGOTHIC_FONTS.items():
        font = fonts[name]
        assert font.source_url == (
            "https://github.com/Fitzgerald-Porthmouth-Koenigsegg/"
            f"Plangothic_Project/releases/download/V2.9.5795/{name}"
        )
        assert font.upstream_file_name == name
        assert font.sha256 == expected["sha256"]
        assert font.bytes == expected["bytes"]
        assert font.license == "OFL-1.1"
        assert font.attribution == PLANGOTHIC_ATTRIBUTION


def test_each_upstream_license_is_locked_and_will_travel_in_the_package() -> None:
    notices = {
        notice.packaged_name: notice
        for notice in subtitle_font_assets.packaged_license_notices()
    }
    assert set(notices) == {
        "NotoSansCJK-LICENSE.txt",
        "Plangothic-LICENSE-OFL.txt",
    }
    plangothic = notices["Plangothic-LICENSE-OFL.txt"]
    assert plangothic.source_url == (
        "https://raw.githubusercontent.com/Fitzgerald-Porthmouth-Koenigsegg/"
        "Plangothic_Project/V2.9.5795/LICENSE-OFL.txt"
    )
    assert plangothic.sha256 == (
        "e564f06d018e7b95bc3594c96a17f1d41865af4038c375e7aa974dd69df38602"
    )
    assert plangothic.bytes == 4302


def test_font_families_publish_separate_rights_derived_sbom_components() -> None:
    families = {
        family.component_id: family
        for family in subtitle_font_assets.bundled_font_families()
    }
    assert set(families) == {"subtitle-fonts", "plangothic-fonts"}
    noto = families["subtitle-fonts"]
    assert noto.display_name == "Noto Sans CJK SC"
    assert noto.version == "Sans2.004"
    assert noto.license_text_id == "ofl-1.1"
    assert noto.packaged_license_name == "NotoSansCJK-LICENSE.txt"
    plangothic = families["plangothic-fonts"]
    assert plangothic.display_name == "Plangothic P1/P2"
    assert plangothic.version == "V2.9.5795"
    assert plangothic.project_url == (
        "https://github.com/Fitzgerald-Porthmouth-Koenigsegg/Plangothic_Project"
    )
    assert plangothic.license_text_id == "ofl-1.1"
    assert plangothic.attribution == PLANGOTHIC_ATTRIBUTION
    assert plangothic.packaged_license_name == "Plangothic-LICENSE-OFL.txt"


def test_cache_manifest_refuses_a_license_that_overwrites_a_font() -> None:
    rights = subtitle_font_assets.load_asset_rights()
    for entry in rights["entries"]:
        if entry.get("noticeComponentId") == "plangothic-fonts":
            entry["packagedLicenseName"] = "PlangothicP1-Regular.ttf"
    with tempfile.TemporaryDirectory() as directory:
        try:
            subtitle_font_assets.ensure_subtitle_fonts(
                root=Path(directory), rights=rights, fetch=lambda _url: b"unused"
            )
        except subtitle_font_assets.SubtitleFontRightsError as error:
            assert "same cache file name" in str(error)
        else:
            raise AssertionError("a license was allowed to overwrite a font")


def test_source_allowlist_refuses_a_floating_or_neighbouring_download() -> None:
    rights = subtitle_font_assets.load_asset_rights()
    plangothic = next(
        entry for entry in rights["entries"] if entry["id"] == "font-plangothic-p1"
    )
    for source_url in (
        (
            "https://github.com/Fitzgerald-Porthmouth-Koenigsegg/"
            "Plangothic_Project/releases/latest/download/PlangothicP1-Regular.ttf"
        ),
        (
            "https://github.com/attacker/Plangothic_Project/releases/download/"
            "V2.9.5795/PlangothicP1-Regular.ttf"
        ),
    ):
        mutated = copy.deepcopy(rights)
        next(
            entry
            for entry in mutated["entries"]
            if entry["id"] == plangothic["id"]
        )["sourceUrl"] = source_url
        try:
            subtitle_font_assets.bundled_subtitle_fonts(mutated)
        except subtitle_font_assets.SubtitleFontRightsError as error:
            assert "locked upstream release" in str(error)
        else:
            raise AssertionError("an unpinned Plangothic source was accepted")


def test_coverage_contract_binds_the_reviewed_candidate_bytes() -> None:
    document = json.loads(
        (ROOT / "contracts/quality/caption-font-coverage.v1.json").read_text(
            encoding="utf-8"
        )
    )
    assets = document["candidate"]["assets"]
    assert {
        entry["packagedName"]: {"bytes": entry["bytes"], "sha256": entry["sha256"]}
        for entry in assets
    } == PLANGOTHIC_FONTS


def main() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
    print("LE-20 caption font asset tests passed")
    print(f"executed checks: {len(tests)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
