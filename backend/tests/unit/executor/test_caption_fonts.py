"""Caption font registry: what the renderer is allowed to draw with."""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Any

import pytest

from automation_tool.executor.captions import fonts

_REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
_ASSET_RIGHTS = _REPOSITORY_ROOT / "contracts/quality/asset-rights-policy.v1.json"

_OPEN_FONT_LICENSE = "OFL-1.1"


def _rights_document() -> dict[str, Any]:
    document: dict[str, Any] = json.loads(_ASSET_RIGHTS.read_text(encoding="utf-8"))
    return document


def _cleared_faces(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Font entries the register clears for redistribution, keyed by file name.

    Mirrors what `scripts/subtitle_font_assets.py` demands of the entries it
    ships, minus that module's `bundledIn == "material-video-worker"` filter
    and its `.ttf`/`.ttc` suffix rule -- both are the upstream WebUI's
    constraints, not the caption renderer's, and the renderer also draws with
    a face from the motion overlay bundle.
    """
    return {
        entry["packagedName"]: entry
        for entry in document["entries"]
        if entry.get("category") == "font"
        and entry.get("packagedName")
        and entry.get("license") == _OPEN_FONT_LICENSE
        and entry.get("redistributionAllowed") is True
        and entry.get("commercialUseAllowed") is True
        and entry.get("embeddingAllowed") is True
    }


def test_the_rights_register_still_denies_unregistered_assets() -> None:
    """The registry's whole claim rests on this default.

    "A face the register does not clear is a face the product does not ship"
    holds only while the register denies by default. If that flips, the
    registry stops being a packing list and becomes a suggestion.
    """
    assert _rights_document()["defaultDecision"] == "deny"


def test_the_registry_matches_the_cleared_faces_in_the_rights_register() -> None:
    """The two must name the same faces, in both directions.

    Forward: a key here that the register has not cleared would put an
    unlicensed font in the package -- exactly what `defaultDecision: "deny"`
    exists to stop -- and nothing else would notice until the file failed to
    open at runtime, or never, if a same-named file happened to be present.

    Backward: a newly cleared face that nobody added here is a decision left
    unmade. If it is deliberately not a caption face, the exclusion belongs in
    this test as a named constant, so the choice is recorded rather than
    silently absent.
    """
    cleared = _cleared_faces(_rights_document())

    assert {
        registered.packaged_name for registered in fonts.REGISTERED_CAPTION_FONTS.values()
    } == set(cleared)


def test_every_registered_face_records_the_bundle_that_carries_it() -> None:
    """`packagedName` is unique per bundle, not globally.

    The two Noto faces are fetched at build time into the material video
    Worker's bundle; the Big Shoulders face is committed under the motion
    overlay's. Dropping the bundle collapses two namespaces into one and
    leaves resolution pointing at a directory that holds only half of them.
    """
    cleared = _cleared_faces(_rights_document())

    for registered in fonts.REGISTERED_CAPTION_FONTS.values():
        assert registered.bundle == cleared[registered.packaged_name]["bundledIn"]
        assert registered.bundle


def test_every_registered_key_matches_the_control_plane_font_key_pattern() -> None:
    """Keys are what the Control Plane's CaptionStyle will send us."""
    from automation_tool.control_plane.domain import editing_project

    for font_key in fonts.REGISTERED_CAPTION_FONTS:
        assert editing_project._FONT_KEY_PATTERN.fullmatch(font_key) is not None


def test_every_packaged_name_is_a_bare_file_name() -> None:
    """A packaged name is joined onto a bundle root, so it must not traverse."""
    for registered in fonts.REGISTERED_CAPTION_FONTS.values():
        name = registered.packaged_name
        assert PurePosixPath(name).name == name
        assert not PurePosixPath(name).is_absolute()
        assert ".." not in name


def test_the_registry_cannot_be_mutated_at_runtime() -> None:
    """`Final` only stops rebinding; the closed set has to be closed.

    Without this, the promise that an unregistered key can never reach the
    filesystem is a convention rather than something enforced:
    `REGISTERED_CAPTION_FONTS["../../x"] = ...` type-checks and runs.
    """
    with pytest.raises(TypeError):
        fonts.REGISTERED_CAPTION_FONTS["../../etc/passwd"] = (  # type: ignore[index]
            fonts.RegisteredCaptionFont(
                packaged_name="passwd", bundle=fonts.MATERIAL_VIDEO_WORKER_BUNDLE
            )
        )


def test_a_registered_face_cannot_be_mutated_at_runtime() -> None:
    registered = fonts.REGISTERED_CAPTION_FONTS[fonts.DEFAULT_CAPTION_FONT_KEY]

    with pytest.raises(AttributeError):
        registered.packaged_name = "other.ttf"  # type: ignore[misc]


def test_the_default_caption_face_is_registered_and_carries_chinese() -> None:
    """The default has to be the CJK face, not a latin one.

    A latin default is the failure the font replacement work already hit once:
    every Chinese caption renders as boxes while everything else reports
    success.
    """
    assert fonts.DEFAULT_CAPTION_FONT_KEY in fonts.REGISTERED_CAPTION_FONTS
    assert (
        fonts.REGISTERED_CAPTION_FONTS[fonts.DEFAULT_CAPTION_FONT_KEY].bundle
        == fonts.MATERIAL_VIDEO_WORKER_BUNDLE
    )
