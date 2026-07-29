"""Caption font registry: what the renderer is allowed to draw with."""

from __future__ import annotations

import json
import sys
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
