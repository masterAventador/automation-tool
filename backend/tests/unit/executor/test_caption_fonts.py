"""Caption font registry: what the renderer is allowed to draw with."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont

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


def _staged_bundle_roots(tmp_path: Path) -> dict[str, Path]:
    """One directory per bundle, each holding that bundle's faces.

    This is the shape both run modes produce: the package collapses the
    bundles under its own root, a checkout leaves them where each bundle
    already keeps them. Either way a face is found as bundle -> root -> name.
    """
    roots: dict[str, Path] = {}
    for registered in fonts.REGISTERED_CAPTION_FONTS.values():
        root = tmp_path / registered.bundle
        root.mkdir(exist_ok=True)
        (root / registered.packaged_name).write_bytes(b"")
        roots[registered.bundle] = root
    return roots


class TestFontKeyPattern:
    def test_the_pattern_matches_the_control_plane_contract(self) -> None:
        """Copied across the deployment boundary, so pinned rather than shared.

        The Executor may not import a Control Plane domain module
        (CLAUDE.md 4.3). A test may reach across; production code may not.
        """
        from automation_tool.control_plane.domain import editing_project

        assert fonts.FONT_KEY_PATTERN.pattern == editing_project._FONT_KEY_PATTERN.pattern

    def test_the_guard_holds_even_when_called_with_match(self) -> None:
        r"""What the `\Z` anchor buys, stated as behaviour not as characters.

        No `fullmatch` case can pin this anchor: under `fullmatch` a `$` is
        equivalent, because the verb already requires the whole string to be
        consumed and both refuse a trailing newline. The entire value of `\Z`
        is that the guard survives the calling verb degrading to `match`,
        where `$` would accept "noto\n" because `$` also matches just before
        a final newline.

        So this asserts the property rather than the pattern text. Reverting
        the anchor to `$` turns it red; a test comparing the pattern string
        to a literal would also turn red, but would only be reporting that
        someone edited a string.
        """
        assert fonts.FONT_KEY_PATTERN.match("noto\n") is None


class TestBundleLayout:
    def test_every_registered_bundle_has_a_source_location(self) -> None:
        """A registered face with no known bundle root is unreachable."""
        for registered in fonts.REGISTERED_CAPTION_FONTS.values():
            assert fonts.bundle_root(registered.bundle).name

    def test_source_locations_agree_with_the_rights_register(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`path` in the register is the discriminant between the two kinds.

        An entry carrying `path` is committed in the repository and is found
        there; an entry without one is fetched at build time by digest and
        lives in the build cache, never in the tree. That single field is why
        the two kinds resolve differently, and this test is what keeps the
        code's idea of "where" tied to the register's.
        """
        monkeypatch.delattr(sys, "_MEIPASS", raising=False)
        # Pointing the build cache inside the checkout is a supported setup --
        # CLAUDE.md 3 names the in-repo `.local/` as the place for local run
        # and test data -- and it would otherwise put the fetched faces under
        # the repository root and fail the assertion below. The failure would
        # name this font code rather than the developer's environment, so it
        # would cost someone a wasted investigation.
        monkeypatch.delenv(fonts.BUILD_CACHE_OVERRIDE_VARIABLE, raising=False)
        cleared = _cleared_faces(_rights_document())

        for registered in fonts.REGISTERED_CAPTION_FONTS.values():
            entry = cleared[registered.packaged_name]
            resolved = fonts.bundle_root(registered.bundle) / registered.packaged_name
            declared_path = entry.get("path")
            if declared_path is None:
                # Fetched by digest into the build cache: it must not be
                # looked for inside the checkout, because it is never there.
                assert _REPOSITORY_ROOT not in resolved.parents
            else:
                assert resolved == _REPOSITORY_ROOT / declared_path
                assert resolved.is_file(), resolved

    def test_the_packaged_layout_is_one_directory_per_bundle(self) -> None:
        """The relative path LE-20 has to satisfy, and the only statement of it."""
        assert fonts.packaged_relative_path("big-shoulders-display") == PurePosixPath(
            "fonts/motion-catalog-overlay/big-shoulders-display-latin.woff2"
        )
        assert fonts.packaged_relative_path("noto-sans-cjk-sc-bold") == PurePosixPath(
            "fonts/material-video-worker/NotoSansCJKsc-Bold.ttf"
        )

    def test_every_registered_face_has_a_packaged_relative_path(self) -> None:
        """LE-20's factory gate is a loop over this; nothing may be missing."""
        seen = set()
        for font_key in fonts.REGISTERED_CAPTION_FONTS:
            relative = fonts.packaged_relative_path(font_key)
            assert not relative.is_absolute()
            assert ".." not in relative.parts
            assert relative.parts[0] == fonts.PACKAGED_FONT_DIRECTORY_NAME
            seen.add(relative)
        assert len(seen) == len(fonts.REGISTERED_CAPTION_FONTS)

    def test_a_frozen_run_resolves_exactly_the_packaged_relative_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Resolution and the published layout must not be able to disagree.

        LE-20 will assert files exist at `packaged_relative_path`; the renderer
        opens whatever `resolve_font_file` returns. If those two drift, the
        gate passes on files nothing reads -- the same two-sources-of-truth
        fault the registry itself was just fixed for.
        """
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

        for font_key, registered in fonts.REGISTERED_CAPTION_FONTS.items():
            staged = tmp_path / fonts.packaged_relative_path(font_key)
            staged.parent.mkdir(parents=True, exist_ok=True)
            staged.write_bytes(b"")

            assert fonts.resolve_font_file(font_key) == staged
            assert fonts.bundle_root(registered.bundle) == staged.parent

    def test_an_unknown_bundle_is_refused(self) -> None:
        with pytest.raises(fonts.CaptionFontUnavailable):
            fonts.bundle_root("no-such-bundle")

    @pytest.mark.parametrize(
        "font_key", ["../../etc/passwd", "helvetica", "Noto", "noto\n", "", b"noto"]
    )
    def test_an_unresolvable_key_has_no_packaged_path(self, font_key: object) -> None:
        """The guard here needs its own tests, not its sibling's.

        `packaged_relative_path` is written as a guarded function but every
        caller today feeds it a registered key, so nothing held the guard in
        place: replacing its `_registered_font(...)` call with a bare
        `REGISTERED_CAPTION_FONTS[...]` lookup left the whole suite green.

        The blast radius is nil today -- it touches no filesystem and has no
        production caller yet -- but LE-20 will import it, and a guard no test
        pins is one a later refactor can quietly delete. Pin it while it is
        still cheap.
        """
        with pytest.raises(fonts.CaptionFontRejected):
            fonts.packaged_relative_path(font_key)  # type: ignore[arg-type]


class TestResolveFontFile:
    def test_a_registered_key_resolves_under_its_own_bundle_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        roots = _staged_bundle_roots(tmp_path)
        monkeypatch.setattr(fonts, "bundle_root", lambda bundle: roots[bundle])

        resolved = fonts.resolve_font_file("noto-sans-cjk-sc-bold")

        assert resolved == roots[fonts.MATERIAL_VIDEO_WORKER_BUNDLE] / "NotoSansCJKsc-Bold.ttf"

    def test_faces_from_different_bundles_resolve_under_different_roots(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The single-root design this replaced got exactly this wrong."""
        roots = _staged_bundle_roots(tmp_path)
        monkeypatch.setattr(fonts, "bundle_root", lambda bundle: roots[bundle])

        cjk = fonts.resolve_font_file("noto-sans-cjk-sc-bold")
        latin = fonts.resolve_font_file("big-shoulders-display")

        assert cjk.parent != latin.parent

    def test_every_registered_key_resolves(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        roots = _staged_bundle_roots(tmp_path)
        monkeypatch.setattr(fonts, "bundle_root", lambda bundle: roots[bundle])

        for font_key in fonts.REGISTERED_CAPTION_FONTS:
            assert fonts.resolve_font_file(font_key).is_file()

    @pytest.mark.parametrize(
        "font_key",
        [
            "Noto-Sans-CJK-SC-Bold",
            "noto sans cjk sc bold",
            "noto/sans",
            "../../etc/passwd",
            "/etc/passwd",
            "1noto",
            "-noto",
            "",
            "n" * 65,
            "noto-sans-cjk-sc-bold\n",
        ],
    )
    def test_a_malformed_key_is_refused(self, font_key: str) -> None:
        with pytest.raises(fonts.CaptionFontRejected):
            fonts.resolve_font_file(font_key)

    @pytest.mark.parametrize("font_key", [b"noto", None, 7, ["noto"]])
    def test_a_non_string_key_is_refused(self, font_key: object) -> None:
        with pytest.raises(fonts.CaptionFontRejected):
            fonts.resolve_font_file(font_key)  # type: ignore[arg-type]

    def test_a_key_at_the_maximum_length_clears_the_pattern(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """64 characters is the boundary the Control Plane accepts."""
        monkeypatch.setattr(fonts, "bundle_root", lambda bundle: tmp_path)

        with pytest.raises(fonts.CaptionFontRejected) as rejection:
            fonts.resolve_font_file("n" * 64)

        assert "unregistered" in str(rejection.value)

    def test_a_wellformed_but_unregistered_key_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(fonts, "bundle_root", lambda bundle: tmp_path)

        with pytest.raises(fonts.CaptionFontRejected):
            fonts.resolve_font_file("helvetica")

    def test_an_unresolvable_key_never_reaches_the_filesystem(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The closed register, not the pattern, is what enforces this.

        A traversal string is not a key in the map, so the lookup misses
        before any path exists. Deleting the pattern guard leaves this green,
        which is why the guard has its own test below.
        """

        def _explode(bundle: str) -> Path:
            raise AssertionError("no bundle root may be consulted for an unknown key")

        monkeypatch.setattr(fonts, "bundle_root", _explode)

        for font_key in ("../../etc/passwd", "helvetica"):
            with pytest.raises(fonts.CaptionFontRejected):
                fonts.resolve_font_file(font_key)

    def test_a_malformed_key_is_not_echoed_back_in_the_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """This is the pattern guard's real job, and the one that can regress.

        The unregistered-key branch names the key so an operator can see which
        setting is wrong. That is only safe because the pattern already ran: a
        key reaching it is `[a-z][a-z0-9-]{0,63}` and cannot carry a path, a
        newline or a quote. Without the guard a traversal string would be
        echoed straight into the message, putting untrusted input in a log --
        which CLAUDE.md 7 forbids outright.
        """
        monkeypatch.setattr(fonts, "bundle_root", lambda bundle: tmp_path)

        for font_key in ("../../etc/passwd", "noto-sans-cjk-sc-bold\n", "a'; DROP TABLE"):
            with pytest.raises(fonts.CaptionFontRejected) as rejection:
                fonts.resolve_font_file(font_key)
            assert font_key not in str(rejection.value)

    def test_a_missing_face_is_reported_as_unavailable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(fonts, "bundle_root", lambda bundle: tmp_path)

        with pytest.raises(fonts.CaptionFontUnavailable) as rejection:
            fonts.resolve_font_file("noto-sans-cjk-sc-bold")

        assert "noto-sans-cjk-sc-bold" in str(rejection.value)

    def test_a_directory_in_place_of_a_face_is_reported_as_unavailable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "NotoSansCJKsc-Bold.ttf").mkdir()
        monkeypatch.setattr(fonts, "bundle_root", lambda bundle: tmp_path)

        with pytest.raises(fonts.CaptionFontUnavailable):
            fonts.resolve_font_file("noto-sans-cjk-sc-bold")


class TestBuildCacheRootBranches:
    """Each platform branch, asserted on its own.

    coverage.py scores one `if` as one branch, so a run that never takes the
    last return can still report full coverage. Only one case per branch
    proves each is reachable and correct.
    """

    @pytest.fixture(autouse=True)
    def _clean_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delattr(sys, "_MEIPASS", raising=False)
        monkeypatch.delenv(fonts.BUILD_CACHE_OVERRIDE_VARIABLE, raising=False)
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        # `HOME` rather than a patched `Path.home`: `expanduser` reads the
        # environment directly, so patching only the method would leave the
        # override branch expanding `~` against this machine's real home.
        monkeypatch.setenv("HOME", "/home/u")

    def _fetched_bundle_root(self) -> Path:
        return fonts.bundle_root(fonts.MATERIAL_VIDEO_WORKER_BUNDLE)

    def test_an_explicit_override_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(fonts.BUILD_CACHE_OVERRIDE_VARIABLE, "~/custom")

        assert self._fetched_bundle_root() == Path("/home/u/custom/subtitle-fonts")

    def test_macos_uses_library_caches(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")

        assert self._fetched_bundle_root().parent == Path(
            "/home/u/Library/Caches/automation-tool-build"
        )

    def test_windows_uses_local_appdata(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("LOCALAPPDATA", "/appdata")

        assert self._fetched_bundle_root().parent == Path("/appdata/automation-tool-build")

    def test_windows_without_local_appdata_falls_back(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "platform", "win32")

        assert self._fetched_bundle_root().parent == Path(
            "/home/u/AppData/Local/automation-tool-build"
        )

    def test_linux_honours_xdg_cache_home(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setenv("XDG_CACHE_HOME", "/xdg")

        assert self._fetched_bundle_root().parent == Path("/xdg/automation-tool-build")

    def test_linux_without_xdg_uses_dot_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")

        assert self._fetched_bundle_root().parent == Path("/home/u/.cache/automation-tool-build")


def _synthesise_face(path: Path, inked: Sequence[int] = (), blank: Sequence[int] = ()) -> None:
    """Write a minimal face covering exactly the codepoints asked for.

    Synthetic faces are for the judgement logic only -- which codepoints a face
    covers, and what the chain does about the ones it does not. They say
    nothing about format handling: a real `OTTO`/CFF face and a real WOFF2
    face take parsing paths this builder never produces, which is why T5
    exercises both real formats directly.

    `blank` codepoints get a glyph with no contours. That is not a broken
    glyph -- it is what a space is -- and it exists here because "covered" has
    to mean "the cmap maps it", not "it puts ink on the page". Judging by ink
    would report every space as missing and send it down the fallback chain.
    """
    names: list[str] = [".notdef"]
    character_map: dict[int, str] = {}
    for index, codepoint in enumerate([*inked, *blank]):
        name = f"g{index}"
        names.append(name)
        character_map[codepoint] = name

    builder = FontBuilder(unitsPerEm=1000, isTTF=True)
    builder.setupGlyphOrder(names)
    builder.setupCharacterMap(character_map)

    blank_codepoints = set(blank)
    glyphs: dict[str, Any] = {}
    metrics: dict[str, tuple[int, int]] = {}

    def _add_glyph(name: str, *, inked: bool) -> None:
        pen = TTGlyphPen(None)
        if inked:
            pen.moveTo((100, 0))
            pen.lineTo((100, 700))
            pen.lineTo((600, 700))
            pen.lineTo((600, 0))
            pen.closePath()
        glyphs[name] = pen.glyph()
        metrics[name] = (700, 100)

    _add_glyph(".notdef", inked=False)
    for codepoint, name in character_map.items():
        _add_glyph(name, inked=codepoint not in blank_codepoints)
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics(metrics)
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": "Synth", "styleName": "Regular"})
    builder.setupOS2()
    builder.setupPost()
    builder.save(path)


def _stage_synthetic_faces(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    coverage: Mapping[str, Sequence[int]],
    blank: Mapping[str, Sequence[int]] | None = None,
) -> None:
    """Put a synthetic face at every registered key's real resolved location."""
    blank = blank or {}
    roots: dict[str, Path] = {}
    for font_key, registered in fonts.REGISTERED_CAPTION_FONTS.items():
        root = tmp_path / registered.bundle
        root.mkdir(exist_ok=True)
        roots[registered.bundle] = root
        if font_key in coverage:
            _synthesise_face(
                root / registered.packaged_name,
                inked=coverage[font_key],
                blank=blank.get(font_key, ()),
            )
    monkeypatch.setattr(fonts, "bundle_root", lambda bundle: roots[bundle])


_LATIN = "big-shoulders-display"
_CJK = "noto-sans-cjk-sc-bold"


@pytest.fixture(autouse=True)
def _clear_coverage_cache() -> Iterator[None]:
    """Coverage is memoised per key, and tests point one key at many files.

    Without this the second test to use a key would read the first test's
    face. The cache is keyed on the font key, not on the resolved bytes,
    because in production a key resolves to one packaged file for the life of
    the process.
    """
    fonts.glyph_coverage.cache_clear()
    yield
    fonts.glyph_coverage.cache_clear()


class TestGlyphCoverage:
    def test_a_mapped_codepoint_is_covered(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stage_synthetic_faces(tmp_path, monkeypatch, {_LATIN: [ord("A")]})

        assert ord("A") in fonts.glyph_coverage(_LATIN)

    def test_an_unmapped_codepoint_is_not_covered(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stage_synthetic_faces(tmp_path, monkeypatch, {_LATIN: [ord("A")]})

        assert ord("中") not in fonts.glyph_coverage(_LATIN)

    def test_a_codepoint_drawn_with_no_contours_is_still_covered(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A space is mapped and inkless; that is coverage, not absence.

        Judging coverage by whether a glyph paints anything would push every
        space onto the fallback chain, and fail closed on a caption made only
        of spaces. Both real faces map U+0020 to a contourless glyph.
        """
        _stage_synthetic_faces(
            tmp_path, monkeypatch, {_LATIN: [ord("A")]}, blank={_LATIN: [ord(" ")]}
        )

        assert ord(" ") in fonts.glyph_coverage(_LATIN)

    def test_codepoints_beyond_the_basic_plane_are_covered(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Astral codepoints need a format 12 subtable; format 4 cannot hold them."""
        _stage_synthetic_faces(tmp_path, monkeypatch, {_CJK: [0x20BB7]})

        assert 0x20BB7 in fonts.glyph_coverage(_CJK)

    def test_a_missing_face_is_reported_as_unavailable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stage_synthetic_faces(tmp_path, monkeypatch, {})

        with pytest.raises(fonts.CaptionFontUnavailable):
            fonts.glyph_coverage(_LATIN)

    def test_an_unparsable_face_is_reported_as_unavailable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stage_synthetic_faces(tmp_path, monkeypatch, {})
        registered = fonts.REGISTERED_CAPTION_FONTS[_LATIN]
        (tmp_path / registered.bundle / registered.packaged_name).write_bytes(b"not a font")

        with pytest.raises(fonts.CaptionFontUnavailable):
            fonts.glyph_coverage(_LATIN)

    def test_a_face_with_no_character_map_is_reported_as_unavailable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Parses as a font, carries no cmap: fontTools raises KeyError here.

        A separate case from unparsable bytes, which raise TTLibError. Without
        it the `KeyError` in the handler is unpinned and reads as defensive
        noise, and deleting it would turn this into an unhandled crash.
        """
        _stage_synthetic_faces(tmp_path, monkeypatch, {_LATIN: [ord("A")]})
        registered = fonts.REGISTERED_CAPTION_FONTS[_LATIN]
        path = tmp_path / registered.bundle / registered.packaged_name
        stripped = TTFont(str(path))
        del stripped["cmap"]
        stripped.save(str(path))
        stripped.close()

        with pytest.raises(fonts.CaptionFontUnavailable):
            fonts.glyph_coverage(_LATIN)

    def test_a_face_that_disappears_after_resolution_is_reported_as_unavailable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Resolution checks the file exists; opening it can still lose the race."""
        _stage_synthetic_faces(tmp_path, monkeypatch, {_LATIN: [ord("A")]})

        def _vanished(*_args: object, **_kwargs: object) -> TTFont:
            raise OSError("file removed between resolving and opening it")

        monkeypatch.setattr(fonts, "TTFont", _vanished)

        with pytest.raises(fonts.CaptionFontUnavailable):
            fonts.glyph_coverage(_LATIN)

    def test_a_woff2_face_without_brotli_is_reported_as_unavailable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The one dependency whose absence is invisible until a face is read.

        FreeType decompresses WOFF2 on its own, so PIL keeps working and only
        the cmap read fails. Losing `brotli` would therefore surface as an
        unhandled ImportError from deep inside coverage rather than as a
        stated font problem.
        """
        _stage_synthetic_faces(tmp_path, monkeypatch, {_LATIN: [ord("A")]})

        def _no_brotli(*_args: object, **_kwargs: object) -> TTFont:
            raise ImportError("The WOFF2 decoder requires the Brotli Python extension")

        monkeypatch.setattr(fonts, "TTFont", _no_brotli)

        with pytest.raises(fonts.CaptionFontUnavailable):
            fonts.glyph_coverage(_LATIN)

    def test_an_unregistered_key_is_refused(self) -> None:
        with pytest.raises(fonts.CaptionFontRejected):
            fonts.glyph_coverage("helvetica")

    def test_coverage_is_memoised_per_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reading a real face's cmap costs ~25 ms; per character that is fatal."""
        _stage_synthetic_faces(tmp_path, monkeypatch, {_LATIN: [ord("A")]})
        reads = 0
        original = fonts.resolve_font_file

        def _counted(font_key: str) -> Path:
            nonlocal reads
            reads += 1
            return original(font_key)

        monkeypatch.setattr(fonts, "resolve_font_file", _counted)

        for _ in range(5):
            fonts.glyph_coverage(_LATIN)

        assert reads == 1


class TestFontToolsNotdefAssumption:
    def test_a_notdef_mapping_cannot_survive_a_round_trip(self, tmp_path: Path) -> None:
        r"""Coverage takes the cmap at face value; this is why that is safe.

        `glyph_coverage` treats every codepoint in `getBestCmap()` as covered,
        with no filter for entries pointing at `.notdef`. That is only correct
        while such an entry cannot exist in a file: in cmap format 4 "maps to
        glyph 0" and "not mapped" are the same bytes, and fontTools drops the
        mapping on write in both format 4 and format 12.

        This pins the assumption instead of defending against it with a branch
        no test could reach. If a future fontTools starts preserving such a
        mapping, this turns red and `glyph_coverage` needs revisiting.
        """
        path = tmp_path / "notdef.ttf"
        _synthesise_face(path, inked=[ord("A")])

        face = TTFont(str(path))
        for subtable in face["cmap"].tables:
            subtable.cmap[ord("B")] = ".notdef"
        face.save(path)
        face.close()

        with TTFont(str(path), lazy=True) as reloaded:
            character_map = reloaded.getBestCmap()

        assert ord("A") in character_map
        assert ord("B") not in character_map


class TestFontChain:
    def test_the_first_face_covering_a_character_wins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stage_synthetic_faces(
            tmp_path, monkeypatch, {_LATIN: [ord("A")], _CJK: [ord("A"), ord("中")]}
        )
        chain = fonts.FontChain((_LATIN, _CJK))

        assert chain.face_for("A") == _LATIN
        assert chain.face_for("中") == _CJK

    def test_the_chain_order_decides(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _stage_synthetic_faces(
            tmp_path, monkeypatch, {_LATIN: [ord("A")], _CJK: [ord("A"), ord("中")]}
        )

        assert fonts.FontChain((_CJK, _LATIN)).face_for("A") == _CJK

    def test_a_character_no_face_covers_fails_closed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Refuse rather than draw the tofu box.

        Drawing `.notdef` would leave every downstream assertion green -- the
        PNG is non-empty, its size is right, ffprobe counts the frames -- while
        the viewer sees boxes. Failing here is the only point where it is
        still detectable.
        """
        _stage_synthetic_faces(tmp_path, monkeypatch, {_LATIN: [ord("A")], _CJK: [ord("中")]})
        chain = fonts.FontChain((_LATIN, _CJK))

        # Premise first: this test must fail because the chain refuses, not
        # because a fixture failed to build a face.
        assert fonts.glyph_coverage(_LATIN) == frozenset({ord("A")})
        assert fonts.glyph_coverage(_CJK) == frozenset({ord("中")})

        with pytest.raises(fonts.CaptionGlyphUnavailable):
            chain.face_for("😀")

    def test_the_refusal_names_the_codepoint_and_not_the_character(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Caption text is user content and must not reach a log (CLAUDE.md 7)."""
        _stage_synthetic_faces(tmp_path, monkeypatch, {_LATIN: [ord("A")], _CJK: []})
        chain = fonts.FontChain((_LATIN, _CJK))

        with pytest.raises(fonts.CaptionGlyphUnavailable) as refusal:
            chain.face_for("😀")

        assert "U+1F600" in str(refusal.value)
        assert "😀" not in str(refusal.value)

    def test_an_empty_chain_is_rejected(self) -> None:
        with pytest.raises(fonts.CaptionFontRejected):
            fonts.FontChain(())

    def test_a_repeated_face_is_rejected(self) -> None:
        with pytest.raises(fonts.CaptionFontRejected):
            fonts.FontChain((_LATIN, _LATIN))

    @pytest.mark.parametrize("font_key", ["helvetica", "../../etc/passwd", "Noto"])
    def test_an_unresolvable_key_is_rejected_when_the_chain_is_built(self, font_key: str) -> None:
        """Fail at construction, not at the first character that needs it."""
        with pytest.raises(fonts.CaptionFontRejected):
            fonts.FontChain((_LATIN, font_key))

    def test_a_chain_cannot_be_mutated(self) -> None:
        chain = fonts.FontChain((_LATIN,))

        with pytest.raises(AttributeError):
            chain.keys = ()  # type: ignore[misc]


class TestSegmentRuns:
    @pytest.fixture
    def chain(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> fonts.FontChain:
        _stage_synthetic_faces(
            tmp_path,
            monkeypatch,
            {_LATIN: [ord("A"), ord("B")], _CJK: [ord("中"), ord("文")]},
        )
        return fonts.FontChain((_LATIN, _CJK))

    def test_an_empty_text_produces_no_runs(self, chain: fonts.FontChain) -> None:
        assert fonts.segment_runs("", chain) == ()

    def test_a_single_character_produces_one_run(self, chain: fonts.FontChain) -> None:
        assert fonts.segment_runs("A", chain) == (fonts.TextRun(_LATIN, "A"),)

    def test_consecutive_characters_sharing_a_face_merge(self, chain: fonts.FontChain) -> None:
        assert fonts.segment_runs("AB", chain) == (fonts.TextRun(_LATIN, "AB"),)

    def test_a_change_of_face_starts_a_new_run(self, chain: fonts.FontChain) -> None:
        assert fonts.segment_runs("AB中文", chain) == (
            fonts.TextRun(_LATIN, "AB"),
            fonts.TextRun(_CJK, "中文"),
        )

    def test_a_face_may_recur_after_an_interruption(self, chain: fonts.FontChain) -> None:
        assert fonts.segment_runs("A中B", chain) == (
            fonts.TextRun(_LATIN, "A"),
            fonts.TextRun(_CJK, "中"),
            fonts.TextRun(_LATIN, "B"),
        )

    def test_the_runs_reproduce_the_original_text(self, chain: fonts.FontChain) -> None:
        text = "AB中文A"
        assert "".join(run.text for run in fonts.segment_runs(text, chain)) == text

    def test_an_uncovered_character_fails_the_whole_segmentation(
        self, chain: fonts.FontChain
    ) -> None:
        with pytest.raises(fonts.CaptionGlyphUnavailable):
            fonts.segment_runs("A😀B", chain)
