#!/usr/bin/env python3
"""LE-20 T3: production caption-font assembly and frozen-candidate gate."""

from __future__ import annotations

import hashlib
import sys
import tempfile
from pathlib import Path, PurePosixPath
from types import MappingProxyType

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "backend/src"))

import build_material_video_worker_candidate as candidate_builder
import subtitle_font_assets
from automation_tool.executor.captions import fonts as caption_fonts

ATTRIBUTION = (
    "Copyright 2019 The Big Shoulders Project Authors "
    "(https://github.com/xotypeco/big_shoulders)"
)


def _fixture() -> tuple[bytes, bytes, dict, MappingProxyType[str, object]]:
    font_payload = (
        ROOT
        / "assets/motion-catalog-overlay/fonts/big-shoulders-display-latin.woff2"
    ).read_bytes()
    license_payload = b"SIL OPEN FONT LICENSE Version 1.1\n"
    rights = {
        "entries": [
            {
                "id": "font-fixture",
                "category": "font",
                "bundledIn": "fixture-bundle",
                "packagedName": "fixture-face.woff2",
                "sha256": hashlib.sha256(font_payload).hexdigest(),
                "bytes": len(font_payload),
                "attribution": ATTRIBUTION,
                "license": "OFL-1.1",
                "redistributionAllowed": True,
                "commercialUseAllowed": True,
                "embeddingAllowed": True,
                "packagedLicenseName": "fixture-OFL.txt",
                "licenseTextSha256": hashlib.sha256(license_payload).hexdigest(),
                "licenseTextBytes": len(license_payload),
            }
        ]
    }
    registered = MappingProxyType(
        {
            "fixture-face": caption_fonts.RegisteredCaptionFont(
                packaged_name="fixture-face.woff2", bundle="fixture-bundle"
            )
        }
    )
    return font_payload, license_payload, rights, registered


def _candidate(directory: str) -> tuple[Path, dict, MappingProxyType[str, object]]:
    font_payload, license_payload, rights, registered = _fixture()
    candidate = Path(directory) / "candidate"
    root = candidate / "_internal/fonts/fixture-bundle"
    root.mkdir(parents=True)
    (root / "fixture-face.woff2").write_bytes(font_payload)
    (root / "fixture-OFL.txt").write_bytes(license_payload)
    return candidate, rights, registered


def _relative(_key: str) -> PurePosixPath:
    return PurePosixPath("fonts/fixture-bundle/fixture-face.woff2")


def test_spec_assembles_the_runtime_registry_layout_and_every_license() -> None:
    source = (
        ROOT / "workers/material_montage/material-video-worker.spec"
    ).read_text(encoding="utf-8")
    for production_fact in (
        "REGISTERED_CAPTION_FONTS",
        "packaged_relative_path",
        "resolve_font_file",
        "packaged_license_notices",
        "committed_font_license_source",
    ):
        assert production_fact in source


def test_committed_license_source_is_verified_and_cannot_escape_the_repository() -> None:
    rights = subtitle_font_assets.load_asset_rights()
    big_shoulders = next(
        entry for entry in rights["entries"] if entry["id"] == "font-big-shoulders-display"
    )
    source = subtitle_font_assets.committed_font_license_source(big_shoulders)
    assert source == (
        ROOT
        / "assets/motion-catalog-overlay/fonts/BigShouldersDisplay-OFL.txt"
    )
    escaped = dict(big_shoulders, licensePath="../../private.txt")
    try:
        subtitle_font_assets.committed_font_license_source(escaped)
    except subtitle_font_assets.SubtitleFontRightsError as error:
        assert "licensePath" in str(error)
    else:
        raise AssertionError("a committed font licence escaped the repository")


def test_gate_accepts_a_readable_registered_face_and_its_locked_license() -> None:
    with tempfile.TemporaryDirectory() as directory:
        candidate, rights, registered = _candidate(directory)
        candidate_builder.assert_registered_caption_fonts_present(
            candidate,
            registered=registered,
            relative_path=_relative,
            rights=rights,
        )


def test_gate_rejects_a_missing_or_substituted_runtime_face() -> None:
    for mutation in ("missing", "substituted"):
        with tempfile.TemporaryDirectory() as directory:
            candidate, rights, registered = _candidate(directory)
            face = candidate / "_internal/fonts/fixture-bundle/fixture-face.woff2"
            face.unlink() if mutation == "missing" else face.write_bytes(b"not the face")
            try:
                candidate_builder.assert_registered_caption_fonts_present(
                    candidate,
                    registered=registered,
                    relative_path=_relative,
                    rights=rights,
                )
            except candidate_builder.MaterialVideoWorkerPackageError as error:
                assert "fixture-face" in str(error)
            else:
                raise AssertionError(f"{mutation} registered face was accepted")


def test_gate_rejects_a_missing_or_substituted_runtime_font_license() -> None:
    for mutation in ("missing", "substituted"):
        with tempfile.TemporaryDirectory() as directory:
            candidate, rights, registered = _candidate(directory)
            notice = candidate / "_internal/fonts/fixture-bundle/fixture-OFL.txt"
            notice.unlink() if mutation == "missing" else notice.write_bytes(b"wrong")
            try:
                candidate_builder.assert_registered_caption_fonts_present(
                    candidate,
                    registered=registered,
                    relative_path=_relative,
                    rights=rights,
                )
            except candidate_builder.MaterialVideoWorkerPackageError as error:
                assert "fixture-OFL.txt" in str(error)
            else:
                raise AssertionError(f"{mutation} registered licence was accepted")


def main() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
    print("LE-20 caption font packaging tests passed")
    print(f"executed checks: {len(tests)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
