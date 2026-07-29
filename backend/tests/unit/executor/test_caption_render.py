"""Caption style values, the PIL face behind a key, and laying a caption out."""

from __future__ import annotations

import dataclasses
import errno
import math
import os
import sys
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, Final

import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib.tables.TupleVariation import TupleVariation
from PIL import Image, ImageDraw, ImageFont

from automation_tool.executor.captions import fonts, render

_REGISTERED_KEY: Final = "noto-sans-cjk-sc-bold"

# A style every field of which is comfortably inside its range, so a case
# built from it differs from a valid style in exactly the field under test.
_VALID: Final[dict[str, Any]] = {
    "font_key": _REGISTERED_KEY,
    "font_px": 48,
    "stroke_px": 2,
    "line_spacing": 1.5,
}

# Endpoints and the first value past each end, as literals.
#
# Deliberately not derived from `render.MIN_CAPTION_FONT_PX` and friends: a
# case computed from the constant it is meant to pin moves with the constant,
# so widening the range by one would still be green. These numbers are read
# off `editing_project.CaptionStyle` by eye, and
# `TestCrossLayerContract` is what keeps that reading honest.
_FONT_PX_ACCEPTED: Final = (12, 200)
_FONT_PX_REFUSED: Final = (11, 201)
_STROKE_PX_ACCEPTED: Final = (0, 20)
_STROKE_PX_REFUSED: Final = (-1, 21)
_LINE_SPACING_ACCEPTED: Final = (1.0, 3.0)
# One representable step outside, not 0.0 and 4.0. A refusal case sitting far
# from the boundary only proves that somewhere out there gets refused; it
# cannot say where the boundary is. On this line that is not hypothetical --
# a sibling task shipped a guard whose refusal fixture sat 72 units clear of
# the edge, and every off-by-k mutation for k in 1..71 survived it.
_LINE_SPACING_REFUSED: Final = (math.nextafter(1.0, 0.0), math.nextafter(3.0, math.inf))


def _style(**overrides: Any) -> render.CaptionRenderStyle:
    return render.CaptionRenderStyle(**{**_VALID, **overrides})


def _synthesise_face(
    path: Path,
    *,
    covers: str = "A",
    blank: str = "",
    stem: tuple[int, int] = (100, 200),
    ascent: int = 900,
    descent: int = -300,
    notdef_inked: bool = False,
    instances: Sequence[tuple[str, int]] = (),
    widen_at_maximum: bool = False,
    family_name: str = "Synth",
) -> None:
    """Write a minimal face, optionally a variable one.

    A second builder next to `test_caption_fonts._synthesise_face` rather than
    a shared one: that builder varies what a face *covers*, which is what the
    chain reasons about, and this one varies whether a face *has axes*, which
    is what loading reasons about. Neither needs the other's dimension, and
    every test module under `tests/unit/executor` is self-contained today.
    Layout needs both -- it draws with a chain -- so `covers` was added here
    rather than moving the axis dimension into the fonts module's builder.

    `covers` and `blank` are the characters the face maps: the first to a
    visible stem, the second to a glyph with no contours, which is what a
    space is. `stem` is that glyph's horizontal span in font units out of an
    advance of 700, so a caller can make neighbouring glyphs sit far apart or
    nearly touch. `ascent` and `descent` are the face's own vertical metrics,
    so two faces on one line can disagree about how tall that line is -- the
    packaged faces do, by 8 px at 48 px.

    `notdef_inked` gives `.notdef` a filled box of its own, a different shape
    from any stem. Without it every "is this tofu?" assertion would be
    comparing against a blank bitmap and would pass on a caption that drew
    nothing at all -- the packaged CJK face puts 966 pixels of ink on the page
    for a character it cannot draw (measured at 48 px), so a synthetic face
    with a blank `.notdef` is the easy half of the problem, not the real one.

    `instances` turns the face into a variable one carrying those named
    instances on a `wght` axis; empty leaves it static, which is the shape
    both packaged Noto faces have. `widen_at_maximum` adds real `gvar` deltas
    so the outline actually thickens towards the top of the axis -- without
    them every instance would draw identical ink and a test could only observe
    that a name was set, not that anything happened.

    `family_name` exists so a face staged somewhere other than the packaged
    location can be told apart from the packaged one by name alone.
    """
    drawn = [*covers, *blank]
    names = [f"g{index}" for index in range(len(drawn))]
    builder = FontBuilder(unitsPerEm=1000, isTTF=True)
    builder.setupGlyphOrder([".notdef", *names])
    builder.setupCharacterMap(
        {ord(character): name for character, name in zip(drawn, names, strict=True)}
    )

    glyphs: dict[str, Any] = {}
    metrics: dict[str, tuple[int, int]] = {}

    def _add(name: str, span: tuple[int, int] | None) -> None:
        """A filled box across `span`, or a glyph with no contours at all.

        The left side bearing has to follow the box rather than stay at a
        fixed 100: FreeType places an outline by the bearing hmtx declares,
        not by where the contour sits, so a stem drawn at 300 with a bearing
        of 100 is rendered back at 100 and the two are indistinguishable on
        the page.
        """
        pen = TTGlyphPen(None)
        if span is not None:
            left, right = span
            pen.moveTo((left, 0))
            pen.lineTo((left, 700))
            pen.lineTo((right, 700))
            pen.lineTo((right, 0))
            pen.closePath()
        glyphs[name] = pen.glyph()
        metrics[name] = (700, 100 if span is None else span[0])

    _add(".notdef", (250, 550) if notdef_inked else None)
    for character, name in zip(drawn, names, strict=True):
        _add(name, None if character in blank else stem)
    builder.setupGlyf(glyphs)

    builder.setupHorizontalMetrics(metrics)
    builder.setupHorizontalHeader(ascent=ascent, descent=descent)
    builder.setupNameTable({"familyName": family_name, "styleName": "Thin"})
    builder.setupOS2()
    builder.setupPost()

    if instances:
        builder.setupFvar(
            axes=[("wght", 100, 100, 900, "Weight")],
            instances=[
                {"location": {"wght": weight}, "stylename": name} for name, weight in instances
            ],
        )
        if widen_at_maximum:
            # Four outline points then four phantom points; the two right-hand
            # points move out by 400 units at the top of the axis.
            deltas = [(0, 0), (0, 0), (400, 0), (400, 0), None, None, None, None]
            builder.setupGvar({names[0]: [TupleVariation({"wght": (0.0, 1.0, 1.0)}, deltas)]})

    builder.save(path)


def _stage_face(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    font_key: str = _REGISTERED_KEY,
    contents: bytes | None = None,
    **face: Any,
) -> Path:
    """Put one face at the key's real resolved location and return that path.

    Goes through `fonts.bundle_root` rather than pointing the loader at a
    file, so the test still exercises registry lookup and resolution -- the
    part that keeps a caller's string out of the filesystem.
    """
    registered = fonts.REGISTERED_CAPTION_FONTS[font_key]
    root = tmp_path / registered.bundle
    root.mkdir(exist_ok=True)
    path = root / registered.packaged_name
    if contents is None:
        _synthesise_face(path, **face)
    else:
        path.write_bytes(contents)
    monkeypatch.setattr(fonts, "bundle_root", lambda bundle: tmp_path / bundle)
    return path


def _redirect_installed_font_search(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point PIL's user-level font search at a directory of our own, and name it.

    When FreeType refuses a file, `ImageFont.truetype` catches its own
    `OSError` and walks the platform's font directories for a file of the same
    base name. The user-level one of those comes out of the environment on
    every platform -- `HOME` on macOS, `WINDIR` on Windows, `XDG_DATA_HOME`
    and `XDG_DATA_DIRS` on Linux -- which is what lets a case below stage a
    collision of its own rather than hoping for one.

    **This does not empty the search path on macOS.** PIL's darwin branch is
    `["/Library/Fonts", "/System/Library/Fonts", expanduser("~/Library/Fonts")]`
    and only the third follows `HOME`; the first two are hard-coded and are
    still walked. So:

    * the case that stages a collision is unaffected -- it only needs PIL to
      answer with a face other than the packaged one, which its own premise
      assertion states outright;
    * the two cases that want *no* collision are still machine-dependent. What
      changed is the direction: the biggest source of same-named faces is out
      of the way (267 in `~/Library/Fonts` here against 372 files under
      `/System/Library/Fonts`), and if a system directory ever does hold one,
      the premise assertion in those cases goes red and says so instead of the
      case quietly covering the other branch. Closing it properly would mean
      faking `sys.platform`, which buys determinism by testing a platform this
      is not running on.

    The returned directory is not created: a case that wants the collision
    creates it and writes a face there, and a case that wants no collision
    leaves it absent.
    """
    searched = tmp_path / "installed-fonts"
    if sys.platform == "win32":
        monkeypatch.setenv("WINDIR", str(searched))
        return searched / "fonts"
    if sys.platform == "darwin":
        monkeypatch.setenv("HOME", str(searched))
        return searched / "Library/Fonts"
    # `XDG_DATA_DIRS` as well: PIL falls back to /usr/local/share:/usr/share
    # when it is unset, which is not ours to empty.
    monkeypatch.setenv("XDG_DATA_HOME", str(searched))
    monkeypatch.setenv("XDG_DATA_DIRS", str(searched))
    return searched / "fonts"


def _ink(face: ImageFont.FreeTypeFont, text: str = "A") -> int:
    """How many pixels the face puts on the page for `text`."""
    mask = face.getmask(text, mode="L")
    width, height = mask.size
    return sum(
        1 for index in range(width * height) if mask.getpixel((index % width, index // width))
    )


class TestCaptionRenderStyle:
    def test_a_valid_style_keeps_its_values(self) -> None:
        style = _style()

        assert style.font_key == _REGISTERED_KEY
        assert style.font_px == 48
        assert style.stroke_px == 2
        assert style.line_spacing == 1.5

    def test_a_style_cannot_be_mutated(self) -> None:
        style = _style()

        with pytest.raises(AttributeError):
            style.font_px = 96  # type: ignore[misc]

    @pytest.mark.parametrize("font_px", _FONT_PX_ACCEPTED)
    def test_the_font_size_endpoints_are_inside_the_range(self, font_px: int) -> None:
        assert _style(font_px=font_px).font_px == font_px

    @pytest.mark.parametrize("font_px", _FONT_PX_REFUSED)
    def test_a_font_size_one_step_outside_the_range_is_refused(self, font_px: int) -> None:
        with pytest.raises(render.CaptionStyleRejected, match="font_px"):
            _style(font_px=font_px)

    @pytest.mark.parametrize("stroke_px", _STROKE_PX_ACCEPTED)
    def test_the_stroke_endpoints_are_inside_the_range(self, stroke_px: int) -> None:
        assert _style(stroke_px=stroke_px).stroke_px == stroke_px

    @pytest.mark.parametrize("stroke_px", _STROKE_PX_REFUSED)
    def test_a_stroke_one_step_outside_the_range_is_refused(self, stroke_px: int) -> None:
        """The range guard, not the letterform guard.

        At the baseline 48 px even a 21 px stroke is still narrower than the
        letterform, so the cross-field guard below cannot stand in for this
        one -- which is what the message assertion pins.
        """
        with pytest.raises(render.CaptionStyleRejected, match=r"stroke_px .* outside"):
            _style(stroke_px=stroke_px)

    @pytest.mark.parametrize("line_spacing", _LINE_SPACING_ACCEPTED)
    def test_the_line_spacing_endpoints_are_inside_the_range(self, line_spacing: float) -> None:
        assert _style(line_spacing=line_spacing).line_spacing == line_spacing

    @pytest.mark.parametrize("line_spacing", _LINE_SPACING_REFUSED)
    def test_a_line_spacing_one_step_outside_the_range_is_refused(
        self, line_spacing: float
    ) -> None:
        with pytest.raises(render.CaptionStyleRejected, match="line_spacing"):
            _style(line_spacing=line_spacing)

    def test_the_probes_really_are_one_step_outside(self) -> None:
        """Premise: `nextafter` has to land on a different float.

        If these collapsed onto the endpoints the refusal cases above would be
        asserting that an accepted value is refused, and would fail loudly --
        but the pair below is what says *why* they are the right probes, and
        it costs one line to keep the intent from rotting into `0.0`/`4.0`.
        """
        below, above = _LINE_SPACING_REFUSED
        assert below < 1.0
        assert math.nextafter(below, math.inf) == 1.0
        assert above > 3.0
        assert math.nextafter(above, 0.0) == 3.0

    def test_a_boolean_stroke_is_refused_while_the_same_int_is_accepted(self) -> None:
        """`isinstance(False, int)` is True, so the check has to be `type(...) is`.

        Zero is inside the stroke range, which makes `False` the one boolean
        that a loose check would wave through: it would produce a style whose
        `stroke_px` prints as `False` and behaves as 0 everywhere downstream.
        """
        assert _style(stroke_px=0).stroke_px == 0

        with pytest.raises(render.CaptionStyleRejected, match="must be an int"):
            _style(stroke_px=False)

    def test_an_int_subclass_font_size_is_refused(self) -> None:
        """The other half of `type(...) is int`, with an in-range value."""

        class Pixels(int):
            pass

        with pytest.raises(render.CaptionStyleRejected, match="must be an int"):
            _style(font_px=Pixels(48))

    @pytest.mark.parametrize("font_px", [48.0, "48", None])
    def test_a_font_size_that_is_not_an_int_is_refused(self, font_px: object) -> None:
        with pytest.raises(render.CaptionStyleRejected, match="must be an int"):
            _style(font_px=font_px)

    def test_an_integer_line_spacing_is_refused_while_the_float_is_accepted(self) -> None:
        """`line_spacing=1` is the easiest way to get this wrong.

        It reads as valid, it is numerically inside the range, and upstream
        refuses it. Accepting it here would mean a style the Control Plane
        rejects still renders in the Executor.
        """
        assert _style(line_spacing=1.0).line_spacing == 1.0

        with pytest.raises(render.CaptionStyleRejected, match="must be a float"):
            _style(line_spacing=1)

    def test_a_float_subclass_line_spacing_is_refused(self) -> None:
        class Spacing(float):
            pass

        with pytest.raises(render.CaptionStyleRejected, match="must be a float"):
            _style(line_spacing=Spacing(1.5))

    @pytest.mark.parametrize(
        "font_key",
        ["", "Noto-Sans", "noto sans", "noto_sans", "1noto", "-noto", "noto\n", "n" * 65],
    )
    def test_a_malformed_font_key_is_refused(self, font_key: str) -> None:
        with pytest.raises(render.CaptionStyleRejected, match="font key"):
            _style(font_key=font_key)

    @pytest.mark.parametrize("font_key", [None, 12, b"noto", ["noto"]])
    def test_a_font_key_that_is_not_a_string_is_refused(self, font_key: object) -> None:
        with pytest.raises(render.CaptionStyleRejected, match="font key"):
            _style(font_key=font_key)

    def test_a_font_key_at_the_maximum_length_is_accepted(self) -> None:
        assert _style(font_key="n" * 64).font_key == "n" * 64

    def test_a_malformed_key_is_not_echoed_back_in_the_error(self) -> None:
        """Same rule as `fonts._registered_font`: caller text stays out of logs.

        A style is assembled from user settings, so its font key is caller
        text until the pattern has cleared it (CLAUDE.md 7).
        """
        with pytest.raises(render.CaptionStyleRejected) as refusal:
            _style(font_key="../../etc/passwd")

        assert "passwd" not in str(refusal.value)

    def test_the_style_does_not_require_the_key_to_be_registered(self) -> None:
        """Shape now, existence later -- and upstream draws the line here too.

        `CaptionStyle` in the Control Plane checks the pattern and nothing
        else, because whether a face is installed is not a property of the
        settings value. Refusing an unregistered key here would make the two
        layers disagree about the same input; `fonts.resolve_font_file`
        refuses it at the point where the answer is actually known.
        """
        assert _style(font_key="not-registered-anywhere").font_key == "not-registered-anywhere"

    def test_a_stroke_that_eats_the_letterform_is_refused(self) -> None:
        """The stroke sits on both sides of the outline, so it costs twice."""
        with pytest.raises(render.CaptionStyleRejected, match="letterform"):
            _style(font_px=12, stroke_px=6)

    def test_a_stroke_just_under_the_letterform_limit_is_accepted(self) -> None:
        assert _style(font_px=12, stroke_px=5).stroke_px == 5

    def test_a_refusal_is_catchable_at_the_package_boundary(self) -> None:
        """The class hierarchy is pinned by `TestRefusalHierarchy` in the
        fonts tests; this pins that the raise site really uses that class.
        """
        with pytest.raises(fonts.CaptionFontRejected):
            _style(font_px=0)


class TestCrossLayerContract:
    """The Executor may not import the Control Plane; a test may.

    Production code copying a contract across a deployment boundary
    (CLAUDE.md 4.3) is only safe while something holds the two copies
    together. Tests run with both packages in one checkout, so reaching
    across here costs nothing at runtime.
    """

    def test_executor_bounds_match_the_control_plane_contract(self) -> None:
        from automation_tool.control_plane.domain import editing_project as upstream

        assert render.MIN_CAPTION_FONT_PX == upstream.MIN_CAPTION_FONT_PX
        assert render.MAX_CAPTION_FONT_PX == upstream.MAX_CAPTION_FONT_PX
        assert render.MAX_CAPTION_STROKE_PX == upstream.MAX_CAPTION_STROKE_PX
        assert render.MIN_CAPTION_LINE_SPACING == upstream.MIN_CAPTION_LINE_SPACING
        assert render.MAX_CAPTION_LINE_SPACING == upstream.MAX_CAPTION_LINE_SPACING
        assert render.FONT_KEY_PATTERN.pattern == upstream._FONT_KEY_PATTERN.pattern

    def test_the_two_layers_carry_the_same_fields(self) -> None:
        from automation_tool.control_plane.domain import editing_project as upstream

        assert {field.name for field in dataclasses.fields(render.CaptionRenderStyle)} == {
            field.name for field in dataclasses.fields(upstream.CaptionStyle)
        }

    def test_the_two_layers_reach_the_same_verdict_on_every_boundary_case(self) -> None:
        """Only the verdict is compared; the exception types differ on purpose.

        Upstream answers one fixed `InvalidEditingProjectModel` because it
        sits on an API boundary where naming the bad field leaks the shape of
        the model. This layer answers a field-specific message because its
        caller is LE-10, one process away, and a message that does not say
        which field is wrong just means the search happens by hand.

        `MIN_CAPTION_STROKE_PX` has no upstream twin -- upstream spells that
        bound as a literal `0` -- so it is this matrix, not the constant
        comparison above, that pins it.
        """
        from automation_tool.control_plane.domain import editing_project as upstream

        overrides: list[dict[str, Any]] = [{}]
        for field, values in (
            ("font_px", (*_FONT_PX_ACCEPTED, *_FONT_PX_REFUSED)),
            ("stroke_px", (*_STROKE_PX_ACCEPTED, *_STROKE_PX_REFUSED)),
            ("line_spacing", (*_LINE_SPACING_ACCEPTED, *_LINE_SPACING_REFUSED)),
        ):
            overrides.extend({field: value} for value in values)
        overrides.extend(
            [
                {"font_px": 12, "stroke_px": 5},
                {"font_px": 12, "stroke_px": 6},
                {"stroke_px": False},
                {"line_spacing": 1},
                {"font_key": "Noto"},
                {"font_key": "noto\n"},
                {"font_key": "not-registered-anywhere"},
            ]
        )
        cases = [{**_VALID, **override} for override in overrides]

        verdicts: list[tuple[bool, bool]] = []
        for case in cases:
            try:
                render.CaptionRenderStyle(**case)
            except fonts.CaptionFontRejected:
                here = False
            else:
                here = True
            try:
                upstream.CaptionStyle(**case)
            except upstream.InvalidEditingProjectModel:
                there = False
            else:
                there = True
            assert here is there, f"the two layers disagree about {case}"
            verdicts.append((here, there))

        # Premise: a matrix that accepted everything, or refused everything,
        # would agree with any implementation at all.
        assert any(here for here, _ in verdicts)
        assert any(not here for here, _ in verdicts)


class TestLoadFace:
    def test_a_registered_key_loads_at_the_requested_size(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stage_face(tmp_path, monkeypatch)

        face = render._load_face(_REGISTERED_KEY, 48)

        assert face.size == 48
        assert face.getname()[0] == "Synth"

    def test_each_load_returns_its_own_face(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Faces are mutable, and loading one is cheap, so they are not shared.

        `size` and the variation instance live on the object, so handing the
        same face to two callers means one caller's settings silently follow
        the other. Measured at 0.6-0.7 ms for both packaged formats including
        the 17 MB CJK face -- FreeType maps the file rather than reading it --
        so there is nothing to buy by memoising this the way
        `glyph_coverage` memoises its 25 ms cmap read.
        """
        _stage_face(tmp_path, monkeypatch)

        assert render._load_face(_REGISTERED_KEY, 48) is not render._load_face(_REGISTERED_KEY, 96)

    def test_a_static_face_is_left_as_it_is(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Both packaged Noto faces are static, and asking them about axes raises."""
        path = _stage_face(tmp_path, monkeypatch)
        with pytest.raises(OSError):
            ImageFont.truetype(path, 48).get_variation_names()

        face = render._load_face(_REGISTERED_KEY, 48)

        assert face.getname()[1] == "Thin"

    def test_a_variable_face_is_pinned_to_a_weight_that_reads(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A variable face left alone renders at its default instance.

        For the packaged latin face that default is `wght` 100 -- Thin -- and
        a Thin caption is the same class of failure as tofu: it has ink, it
        has the right dimensions, every downstream check passes, and the
        viewer cannot read it. Measured on that face at 48 px, "ABCDEFG"
        covers 663 px unpinned against 2984 px at Bold.

        Both the instance named and the weight actually reached are asserted,
        and the name is spelled out rather than read from
        `render.PINNED_WEIGHT_INSTANCE`. Comparing against that constant is
        what the first draft did, and repointing it at "Regular" was measured
        to survive: an assertion that reads the value it is pinning moves with
        it. The ink comparison says the substantive part -- the caption is
        drawn heavier than Regular, matching the packaged CJK face a latin
        fallback sits beside, so a chain does not change weight mid-line.
        """
        path = _stage_face(
            tmp_path,
            monkeypatch,
            instances=[("Thin", 100), ("Regular", 400), ("Bold", 700)],
            widen_at_maximum=True,
        )
        unpinned = ImageFont.truetype(path, 64)
        assert unpinned.getname()[1] == "Thin"
        regular = ImageFont.truetype(path, 64)
        regular.set_variation_by_name("Regular")

        face = render._load_face(_REGISTERED_KEY, 64)

        assert face.getname()[1] == "Bold"
        assert _ink(face) > _ink(regular) > _ink(unpinned)

    def test_a_variable_face_keeps_the_size_it_was_asked_for(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The size assertion above only ever reaches the static path.

        A static face returns from `_pin_variable_weight` before any variation
        call, so `test_a_registered_key_loads_at_the_requested_size` says
        nothing about what pinning a weight does to the size. Measured,
        `set_variation_by_name` leaves it alone -- so there is no bug here
        today, only an unpinned property on the path that actually mutates the
        face.
        """
        _stage_face(tmp_path, monkeypatch, instances=[("Thin", 100), ("Bold", 700)])

        assert render._load_face(_REGISTERED_KEY, 64).size == 64

    def test_a_face_that_faults_on_the_variation_query_is_not_taken_for_a_static_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`OSError` is the one signal that means "this face has no axes".

        That reading is narrow on purpose: a face with no `fvar` answers
        `OSError` to every variation call, and nothing else does. Widening it
        was measured to survive the suite as it stood before this case existed
        -- `except OSError` changed to `except BaseException` left all 158
        cases green -- and what widening costs is a face handed back at its
        axis defaults, which for the
        packaged variable face is Thin. Ink, right dimensions, every
        downstream check green, and a caption the viewer cannot read: the
        exact failure `PINNED_WEIGHT_INSTANCE` exists to prevent.
        """
        _stage_face(tmp_path, monkeypatch, instances=[("Thin", 100), ("Bold", 700)])

        def _fault(face: ImageFont.FreeTypeFont) -> list[bytes]:
            raise RuntimeError("FreeType answered something other than OSError")

        monkeypatch.setattr(ImageFont.FreeTypeFont, "get_variation_names", _fault)

        with pytest.raises(fonts.CaptionFontUnavailable, match=_REGISTERED_KEY):
            render._load_face(_REGISTERED_KEY, 48)

    def test_a_face_that_refuses_to_be_pinned_is_reported_as_unavailable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Naming the instance is not the last FreeType call on this path.

        `set_variation_by_name` re-reads the named instances and then calls
        `setvarname`, and PIL carries a workaround inside that method for a
        FreeType "unknown error" bug -- so a refusal there is measured
        behaviour of the library, not a hypothetical. It surfaces as a bare
        `OSError`, which without this would leave `_load_face` answering with
        something that is neither a font problem nor a style problem: the
        shape every other handler in this module exists to convert.
        """
        _stage_face(tmp_path, monkeypatch, instances=[("Thin", 100), ("Bold", 700)])

        def _refuse(face: ImageFont.FreeTypeFont, name: str | bytes) -> None:
            raise OSError("unknown freetype error")

        monkeypatch.setattr(ImageFont.FreeTypeFont, "set_variation_by_name", _refuse)

        with pytest.raises(fonts.CaptionFontUnavailable, match=_REGISTERED_KEY):
            render._load_face(_REGISTERED_KEY, 48)

    @pytest.mark.parametrize("method", ["get_variation_names", "set_variation_by_name"])
    def test_an_interrupt_during_pinning_is_not_turned_into_a_font_problem(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method: str
    ) -> None:
        """Both envelopes catch `Exception`, which is the point of the width.

        Catching `BaseException` instead was measured to leave the whole suite
        green, and what it would cost is a Ctrl-C during a render answered as
        "caption font unavailable" and swallowed -- in a loop over a caption
        track, a run that will not stop. The width exists to convert what a
        third-party font call raises, not to take the process's own exits.
        """
        _stage_face(tmp_path, monkeypatch, instances=[("Thin", 100), ("Bold", 700)])

        def _interrupt(*_args: object, **_kwargs: object) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(ImageFont.FreeTypeFont, method, _interrupt)

        with pytest.raises(KeyboardInterrupt):
            render._load_face(_REGISTERED_KEY, 48)

    def test_a_variable_face_without_that_instance_is_reported_as_unavailable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Today's register cannot produce this; LE-20's job is adding faces.

        PIL answers a bare `ValueError` here, which would surface inside the
        renderer as neither a font problem nor a style problem -- the same
        shape as the `TypeError` that a cmap-less face used to raise out of
        coverage judgement.

        The message is asserted, not just the type, because the envelope round
        `set_variation_by_name` would answer this case too: deleting the check
        above it was measured to leave a type-only assertion green. What is
        lost then is which of the two things went wrong -- "this face has no
        such instance" is a gap in the packaged asset an operator can act on,
        "this face refused to be pinned" is a fault in the library. The phrase
        matched names the branch without naming the weight, so it does not
        turn into an assertion that reads the constant it is pinning.
        """
        _stage_face(tmp_path, monkeypatch, instances=[("Thin", 100), ("Light", 300)])

        with pytest.raises(fonts.CaptionFontUnavailable, match="variable face with no") as refusal:
            render._load_face(_REGISTERED_KEY, 48)

        assert _REGISTERED_KEY in str(refusal.value)

    @pytest.mark.parametrize(
        ("label", "contents"),
        [
            ("garbage", b"not a font at all" * 8),
            ("empty", b""),
        ],
    )
    def test_a_face_that_will_not_load_is_reported_as_unavailable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, label: str, contents: bytes
    ) -> None:
        path = _stage_face(tmp_path, monkeypatch, contents=contents)
        _redirect_installed_font_search(tmp_path, monkeypatch)

        # Premise: with the user font directory redirected away, PIL really
        # does refuse rather than answering with some other file of the same
        # name. That is what makes this the case covering the `OSError`
        # handler; the substitution it would otherwise take is the case below.
        #
        # This line has no hold on production code -- no mutant is killed by
        # it, and deleting it leaves the suite green. What it guards is the
        # environment: the macOS system font directories cannot be redirected
        # (see `_redirect_installed_font_search`), so if one of them ever
        # holds this base name, this goes red and says which case stopped
        # meaning what it says, instead of the branch silently changing.
        with pytest.raises(OSError):
            ImageFont.truetype(path, 48)

        with pytest.raises(fonts.CaptionFontUnavailable, match=_REGISTERED_KEY) as refusal:
            render._load_face(_REGISTERED_KEY, 48)

        # The key names the setting an operator can fix; the path is a local
        # filesystem detail and stays out of the message (CLAUDE.md 7).
        assert str(path) not in str(refusal.value)

    def test_a_face_that_will_not_load_is_not_replaced_by_an_installed_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A refused packaged face must not be answered from the user's disk.

        `ImageFont.truetype` catches its own `OSError` and searches the
        platform's font directories for a file of the same base name, so a
        packaged face FreeType will not open does not fail closed: PIL returns
        an unrelated face from the machine and the caption is drawn with it.
        Three ways of breaking the packaged file -- corrupt bytes, mode 000,
        truncation -- were each measured to come back as a different family.

        Two things make that worse than a wrong-looking caption.
        `REGISTERED_CAPTION_FONTS` is also the rights list
        (`asset-rights-policy.v1.json`, `defaultDecision: "deny"`), so the
        substitute is a face the product has no clearance to print into a
        customer's video. And it is CLAUDE.md 5's shape exactly: a packaged
        resource that cannot be verified is not to be replaced by whatever the
        system happens to have.

        The collision is staged rather than hoped for. The case has to mean
        the same thing on a machine with no fonts installed and on one with
        hundreds, and the two cases above were passing only because this
        machine has none of the packaged names.
        """
        path = _stage_face(tmp_path, monkeypatch, contents=b"not a font at all" * 8)
        installed = _redirect_installed_font_search(tmp_path, monkeypatch)
        installed.mkdir(parents=True)
        _synthesise_face(installed / path.name, family_name="SomeoneElsesFont")

        # Premise: the staged collision is really what PIL reaches for. If the
        # decoy were never found this case would pass on the `OSError` handler
        # instead, and prove nothing about substitution.
        substituted = ImageFont.truetype(path, 48)
        assert Path(str(substituted.path)) != path

        with pytest.raises(fonts.CaptionFontUnavailable, match=_REGISTERED_KEY) as refusal:
            render._load_face(_REGISTERED_KEY, 48)

        assert str(path) not in str(refusal.value)
        assert str(installed) not in str(refusal.value)

    def test_a_missing_face_is_reported_as_unavailable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(fonts, "bundle_root", lambda bundle: tmp_path / bundle)

        with pytest.raises(fonts.CaptionFontUnavailable):
            render._load_face(_REGISTERED_KEY, 48)

    @pytest.mark.parametrize("font_key", ["../../etc/passwd", "Noto", "", "unregistered-key"])
    def test_an_unresolvable_key_never_reaches_the_filesystem(
        self, monkeypatch: pytest.MonkeyPatch, font_key: str
    ) -> None:
        """The loader's reason for existing: a key is looked up, never joined.

        Mirrors `test_an_unresolvable_key_never_reaches_the_filesystem` in the
        fonts tests one layer up, because this is the function that actually
        opens a file.
        """

        def _explode(bundle: str) -> Path:
            raise AssertionError(f"the filesystem was reached for bundle {bundle}")

        monkeypatch.setattr(fonts, "bundle_root", _explode)

        with pytest.raises(fonts.CaptionFontRejected):
            render._load_face(font_key, 48)

    @pytest.mark.parametrize("font_px", [0, *_FONT_PX_REFUSED])
    def test_a_font_size_outside_the_style_range_is_a_style_problem(
        self, monkeypatch: pytest.MonkeyPatch, font_px: int
    ) -> None:
        """Not a font problem, and not PIL's `ValueError` either.

        PIL refuses a size of 0 with a bare `ValueError`, and a size of 300
        not at all -- so without this the two ways a bad size can arrive are
        an uncaught non-domain exception and a silently oversized caption.
        Refusing before the face is resolved is what the exploding
        `bundle_root` pins: the size is the caller's mistake, so the answer
        must not depend on whether the face happens to be installed.
        """

        def _explode(bundle: str) -> Path:
            raise AssertionError(f"the filesystem was reached for bundle {bundle}")

        monkeypatch.setattr(fonts, "bundle_root", _explode)

        with pytest.raises(render.CaptionStyleRejected, match="font_px"):
            render._load_face(_REGISTERED_KEY, font_px)


# The three registered keys, named for the part each plays below. One face
# draws the CJK characters and the blanks, another the latin ones, and neither
# draws the other's -- which is what makes a mixed line need the chain rather
# than merely have one.
_CJK_KEY: Final = _REGISTERED_KEY
_LATIN_KEY: Final = "big-shoulders-display"
_SPARE_KEY: Final = "noto-sans-cjk-sc-regular"

_CJK_CHARACTERS: Final = "中文第三行字幕"
_LATIN_CHARACTERS: Final = "ABC"
# Covered, and with no contours: this is what a space is, and judging coverage
# by ink would push every one of them onto the fallback chain.
_BLANK_CHARACTERS: Final = " \u00a0\u200b"
# Mapped by no face at all, so it is the character that fails a render closed.
_UNCOVERED_CHARACTER: Final = "𠮷"

_DEFAULT_COVERAGE: Final[Mapping[str, str]] = {
    _CJK_KEY: _CJK_CHARACTERS,
    _LATIN_KEY: _LATIN_CHARACTERS,
    # A private-use character no case below asks for: a face still has to map
    # something, and the chain walks every registered key.
    _SPARE_KEY: "\ue000",
}
_DEFAULT_BLANK: Final[Mapping[str, str]] = {_CJK_KEY: _BLANK_CHARACTERS}

# A frame that is deliberately not square, so a guard that reads the wrong
# side of it is not the same expression as one that reads the right side. The
# sibling task's `font_px > output.height` guard survived a `height -> width`
# mutation for exactly that reason: its fixture was square.
_FRAME_WIDTH: Final = 400
_FRAME_HEIGHT: Final = 300

_RENDER_STYLE: Final[dict[str, Any]] = {
    "font_key": _CJK_KEY,
    "font_px": 50,
    "stroke_px": 4,
    "line_spacing": 1.5,
}

# Read off `editing_project.OutputSpec` by eye, as literals, for the same
# reason the font size endpoints above are literals: a case computed from the
# constant it pins moves with the constant.
_FRAME_ACCEPTED: Final = (128, 4096)
_FRAME_REFUSED: Final = (127, 4097)

# The vertical metrics the builder above defaults to, restated here so a
# case that gives one face metrics of its own still gets the usual ones for
# the rest of the chain.
_DEFAULT_METRICS: Final = (900, -300)

_WHITE: Final = (255, 255, 255, 255)
_BLACK: Final = (0, 0, 0, 255)


@pytest.fixture(autouse=True)
def _clear_coverage_cache() -> Iterator[None]:
    """Coverage is memoised per key, and these cases point one key at many files.

    Same fixture as the fonts tests, and needed here for the same reason: the
    cache is keyed on the font key, not on the bytes behind it, so without
    this the second case to use a key would read the first case's face.
    """
    fonts.glyph_coverage.cache_clear()
    yield
    fonts.glyph_coverage.cache_clear()


def _stage_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    coverage: Mapping[str, str] | None = None,
    blank: Mapping[str, str] | None = None,
    stem: tuple[int, int] = (100, 200),
    metrics: Mapping[str, tuple[int, int]] | None = None,
) -> None:
    """Put a synthetic face at every registered key's resolved location.

    Every key, not only the ones a case draws with: the chain a style implies
    reaches all of them, and asking a key for its coverage resolves its file.
    """
    coverage = _DEFAULT_COVERAGE if coverage is None else coverage
    blank = _DEFAULT_BLANK if blank is None else blank
    for font_key, registered in fonts.REGISTERED_CAPTION_FONTS.items():
        root = tmp_path / registered.bundle
        root.mkdir(exist_ok=True)
        _synthesise_face(
            root / registered.packaged_name,
            covers=coverage.get(font_key, "\ue000"),
            blank=blank.get(font_key, ""),
            stem=stem,
            ascent=(metrics or {}).get(font_key, _DEFAULT_METRICS)[0],
            descent=(metrics or {}).get(font_key, _DEFAULT_METRICS)[1],
            notdef_inked=True,
        )
    monkeypatch.setattr(fonts, "bundle_root", lambda bundle: tmp_path / bundle)


def _render(
    tmp_path: Path,
    text: str,
    *,
    style: render.CaptionRenderStyle | None = None,
    frame_width: int = _FRAME_WIDTH,
    frame_height: int = _FRAME_HEIGHT,
    destination: Path | None = None,
    **overrides: Any,
) -> Path:
    return render.render_caption(
        text,
        style if style is not None else _style(**{**_RENDER_STYLE, **overrides}),
        frame_width=frame_width,
        frame_height=frame_height,
        destination=tmp_path / "caption.png" if destination is None else destination,
    )


def _spans(flags: Sequence[bool]) -> list[tuple[int, int]]:
    """Start and end of every run of True."""
    spans: list[tuple[int, int]] = []
    start: int | None = None
    for index, flag in enumerate([*flags, False]):
        if flag and start is None:
            start = index
        elif not flag and start is not None:
            spans.append((start, index))
            start = None
    return spans


def _ink_rows(image: Image.Image) -> list[tuple[int, int]]:
    """The row ranges that carry ink -- one per drawn line of the caption."""
    alpha = image.getchannel("A").tobytes()
    width, height = image.size
    return _spans(
        [any(alpha[row * width + column] for column in range(width)) for row in range(height)]
    )


def _glyph_clusters(image: Image.Image) -> list[bytes]:
    """Every blob of ink, cropped to itself, in reading order.

    Rows first and columns second, so a two-line caption comes back as one
    blob per glyph rather than one per column.
    """
    alpha = image.getchannel("A").tobytes()
    width, _ = image.size
    clusters: list[bytes] = []
    for top, bottom in _ink_rows(image):
        columns = [
            any(alpha[row * width + column] for row in range(top, bottom))
            for column in range(width)
        ]
        for left, right in _spans(columns):
            band = image.crop((left, top, right, bottom))
            box = band.getchannel("A").getbbox()
            assert box is not None
            clusters.append(band.crop(box).tobytes())
    return clusters


def _notdef_clusters(style: render.CaptionRenderStyle) -> list[bytes]:
    """What each registered face draws for a character it cannot draw.

    The acceptance criterion the ledger fixes for this task: not "the PNG has
    ink", which was measured to catch nothing in either direction -- a tofu
    box has ink and a missing latin glyph has none -- but a difference against
    the `.notdef` bitmap. U+10FFFF is guaranteed to be in no cmap.

    Drawn here with one `ImageDraw.text` call rather than through the
    renderer, and with the colours spelled out rather than read off the module
    under test, so the reference does not move when the thing it is a
    reference for does.
    """
    clusters: list[bytes] = []
    for font_key in fonts.REGISTERED_CAPTION_FONTS:
        face = render._load_face(font_key, style.font_px)
        size = style.font_px * 4
        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        ImageDraw.Draw(canvas).text(
            (size // 4, size * 3 // 4),
            "\U0010ffff",
            font=face,
            anchor="ls",
            fill=_WHITE,
            stroke_width=style.stroke_px,
            stroke_fill=_BLACK,
        )
        box = canvas.getchannel("A").getbbox()
        assert box is not None, f"{font_key} drew nothing for U+10FFFF; the reference is broken"
        clusters.append(canvas.crop(box).tobytes())
    return clusters


def _opaque_pixels(image: Image.Image, colour: tuple[int, int, int, int]) -> int:
    """How many pixels are exactly this colour, alpha included."""
    data = image.tobytes()
    return sum(1 for index in range(0, len(data), 4) if tuple(data[index : index + 4]) == colour)


class TestFallbackChain:
    def test_the_chain_starts_at_the_style_key_and_reaches_every_registered_face(self) -> None:
        """A style names one face; a caption needs the rest of them.

        The style's own key first, because that is what the user asked for,
        and then every other registered face, because the alternative is for
        each caller to invent its own fallback policy.
        """
        chain = render._fallback_chain(_LATIN_KEY)

        assert chain.keys[0] == _LATIN_KEY
        assert set(chain.keys) == set(fonts.REGISTERED_CAPTION_FONTS)
        assert len(chain.keys) == len(fonts.REGISTERED_CAPTION_FONTS)

    def test_an_unregistered_key_is_refused_by_the_chain(self) -> None:
        with pytest.raises(fonts.CaptionFontRejected):
            render._fallback_chain("not-registered-anywhere")


class TestRenderedFile:
    def test_a_caption_is_written_as_a_transparent_rgba_png(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ffmpeg overlays this file, so the background has to be nothing."""
        _stage_chain(tmp_path, monkeypatch)

        written = _render(tmp_path, "中文")

        assert written == tmp_path / "caption.png"
        with Image.open(written) as image:
            assert image.format == "PNG"
            assert image.mode == "RGBA"
            assert image.convert("RGBA").getpixel((0, 0)) == (0, 0, 0, 0)

    def test_a_caption_is_white_with_a_black_stroke(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Not settings: footage is arbitrary, and this pair reads on all of it."""
        _stage_chain(tmp_path, monkeypatch)

        with Image.open(_render(tmp_path, "中")) as image:
            caption = image.convert("RGBA")

        assert _opaque_pixels(caption, _WHITE) > 0
        assert _opaque_pixels(caption, _BLACK) > 0

    @pytest.mark.parametrize(
        "text, drawn_glyphs, lines",
        [
            ("中文\n第三行", 5, 2),  # multi-line CJK
            ("A中", 2, 1),  # mixed scripts: the fallback chain has to segment
            ("中 文", 2, 1),  # a space is covered, and draws nothing
            ("中\u00a0文", 2, 1),  # so is a non-breaking space
        ],
    )
    def test_every_drawn_glyph_differs_from_the_notdef_box(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        text: str,
        drawn_glyphs: int,
        lines: int,
    ) -> None:
        """The task's acceptance criterion, applied to every glyph on the page.

        A caption drawn with the wrong face for a run is the failure this
        catches: it renders, it has ink, its dimensions are right, and the
        viewer sees boxes. Comparing each blob of ink against what each face
        draws for an unmapped codepoint is the only assertion in this file
        that would notice.
        """
        _stage_chain(tmp_path, monkeypatch)

        with Image.open(_render(tmp_path, text)) as opened:
            image = opened.convert("RGBA")

        clusters = _glyph_clusters(image)
        assert len(_ink_rows(image)) == lines
        assert len(clusters) == drawn_glyphs
        tofu = _notdef_clusters(_style(**_RENDER_STYLE))
        for cluster in clusters:
            assert cluster not in tofu

    def test_a_blank_character_advances_the_line_without_drawing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Coverage is cmap membership, so a space is drawn, not skipped.

        Dropping it would shorten the caption by one advance and still produce
        a perfectly plausible PNG.
        """
        _stage_chain(tmp_path, monkeypatch)

        with Image.open(_render(tmp_path, "中 文")) as spaced:
            spaced_width = spaced.width
        with Image.open(_render(tmp_path, "中文")) as tight:
            tight_width = tight.width

        advance = render._load_face(_CJK_KEY, _RENDER_STYLE["font_px"]).getlength(" ")
        assert advance > 0
        assert spaced_width - tight_width == advance


class TestCaptionLayout:
    def test_lines_are_split_the_way_str_splitlines_splits_them(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Derived from `str.splitlines()` itself, not from a list of escapes.

        `fonts` argues its refused set and this splitter are aligned by
        construction, and that argument holds only while the splitter is the
        same function. A hand-rolled `\\n` split would leave U+000B, U+001C or
        U+2028 inside a line, where they come back as refused control
        characters rather than as line breaks.
        """
        _stage_chain(tmp_path, monkeypatch)
        separators = [
            chr(codepoint)
            for codepoint in range(0x110000)
            if len(f"a{chr(codepoint)}b".splitlines()) > 1
        ]
        assert separators, "str.splitlines() split at nothing; the probe is broken"

        for separator in [*separators, "\r\n"]:
            with Image.open(_render(tmp_path, f"中{separator}文")) as image:
                assert len(_ink_rows(image.convert("RGBA"))) == 2, (
                    f"U+{ord(separator[0]):04X} did not start a new line"
                )

    def test_a_tab_is_expanded_rather_than_refusing_the_caption(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`segment_runs` refuses a tab and `splitlines` does not split on one.

        So resolving it is this layer's own obligation, and the choice made
        here is to expand rather than refuse: a tab pasted in from a document
        is not a caption anybody meant to break, and a space carries the
        separation the tab was asking for. Dropping it would be the third
        option and is the one this package refuses everywhere -- it would
        silently shorten the line.
        """
        _stage_chain(tmp_path, monkeypatch)

        with Image.open(_render(tmp_path, "A\tB")) as tabbed:
            tabbed_width = tabbed.width
        # Three spaces, spelled out. Deriving the reference from
        # `CAPTION_TAB_WIDTH` would make it move with the value it is here to
        # pin, and a tab stop of any width at all would then pass.
        with Image.open(_render(tmp_path, "A   B")) as reference:
            reference_width = reference.width

        assert tabbed_width == reference_width

    def test_a_tab_stop_is_counted_from_the_start_of_its_own_line(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Expansion happens per line, after the split, not over the whole text.

        `str.expandtabs` resets its column at `\\n` and `\\r` only, so a text
        expanded whole would keep counting across U+2028 -- a line separator
        `splitlines` does break at -- and the second line would come out one
        tab stop wider than it should be.
        """
        _stage_chain(tmp_path, monkeypatch)

        with Image.open(_render(tmp_path, "AB\u2028A\tC")) as image:
            separated_width = image.width
        with Image.open(_render(tmp_path, "AB\nA\tC")) as image:
            newline_width = image.width

        assert separated_width == newline_width

    def test_a_line_is_wrapped_at_the_frame_width_less_the_margins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stage_chain(tmp_path, monkeypatch)
        advance = render._load_face(_CJK_KEY, _RENDER_STYLE["font_px"]).getlength("中")
        wrap = _FRAME_WIDTH - 2 * render.CAPTION_SIDE_MARGIN_PX
        fits = int(wrap // advance)
        assert fits == 8, "the fixture no longer wraps where this case says it does"

        with Image.open(_render(tmp_path, "中" * fits)) as image:
            assert len(_ink_rows(image.convert("RGBA"))) == 1
            assert image.width == fits * advance + 2 * _RENDER_STYLE["stroke_px"]
        with Image.open(_render(tmp_path, "中" * (fits + 1))) as image:
            assert len(_ink_rows(image.convert("RGBA"))) == 2

    def test_a_line_exactly_as_wide_as_the_wrap_width_is_not_wrapped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The endpoint of the wrap decision, which no round number reaches.

        With the frame above, eight glyphs measure 280 and the wrap width is
        304, so `>` and `>=` agree on every case there. This frame is chosen
        so eight glyphs land exactly on the limit, where they disagree.
        """
        _stage_chain(tmp_path, monkeypatch)
        advance = render._load_face(_CJK_KEY, _RENDER_STYLE["font_px"]).getlength("中")
        frame_width = int(2 * render.CAPTION_SIDE_MARGIN_PX + 8 * advance)

        with Image.open(_render(tmp_path, "中" * 8, frame_width=frame_width)) as image:
            assert len(_ink_rows(image.convert("RGBA"))) == 1

    def test_a_line_one_pixel_over_the_wrap_width_is_wrapped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The other side of the same endpoint, and the one that was missing.

        The case above pins that the limit itself still fits. Without this one
        the comparison could be `> wrap_width + 1` and stay green, because no
        other frame here puts a line exactly one pixel over -- the glyphs are
        35 px wide and every wrap width was a whole glyph away from the edge.
        Measured: that mutant survived all 118 cases.
        """
        _stage_chain(tmp_path, monkeypatch)
        advance = render._load_face(_CJK_KEY, _RENDER_STYLE["font_px"]).getlength("中")
        frame_width = int(2 * render.CAPTION_SIDE_MARGIN_PX + 9 * advance - 1)

        with Image.open(_render(tmp_path, "中" * 9, frame_width=frame_width)) as image:
            assert len(_ink_rows(image.convert("RGBA"))) == 2

    def test_line_spacing_scales_the_gap_between_baselines(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stage_chain(tmp_path, monkeypatch)
        ascent, descent = render._load_face(_CJK_KEY, _RENDER_STYLE["font_px"]).getmetrics()

        heights = {}
        for spacing in (1.0, 3.0):
            with Image.open(_render(tmp_path, "中\n文", line_spacing=spacing)) as image:
                heights[spacing] = image.height

        assert heights[3.0] - heights[1.0] == round((ascent + descent) * 3.0) - round(
            (ascent + descent) * 1.0
        )

    def test_a_blank_line_keeps_its_place_in_the_stack(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An empty line draws nothing and still takes its turn.

        Collapsing it would close up a gap the caller asked for, and the
        caption would come back one line shorter with nothing to say so. It
        has no runs, so its height is the one the style's own face gives it.
        """
        _stage_chain(tmp_path, monkeypatch)
        ascent, descent = render._load_face(_CJK_KEY, _RENDER_STYLE["font_px"]).getmetrics()

        with Image.open(_render(tmp_path, "中\n\n文")) as opened:
            spaced = opened.convert("RGBA")
            spaced_height = spaced.height
            assert len(_ink_rows(spaced)) == 2
        with Image.open(_render(tmp_path, "中\n文")) as image:
            tight_height = image.height

        step = round((ascent + descent) * _RENDER_STYLE["line_spacing"])
        assert spaced_height - tight_height == step

    def test_a_short_face_on_the_last_line_is_not_clipped_by_a_taller_first_line(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The canvas is measured from the first line down, so it ends there too.

        The first baseline is `stroke + lines[0].ascent` below the top edge and
        every later one is a fixed step below that, so what the canvas has to
        hold is the *first* line's ascent plus the steps plus the *last* line's
        descent. Taking the last line's ascent instead is short by the
        difference between the two, and the bottom of the caption is quietly
        cut off -- no exception, no wrong dimension, just a line missing its
        descenders.

        Every other multi-line case here puts the same face on both lines,
        where the two expressions are the same number. This one does not.

        The second face is given an ascent that still covers its own
        letterform -- a face whose glyphs poke above its ascender line is not
        a face, and a reference line that is itself clipped would prove
        nothing -- while leaving `ascent + descent` of the last line smaller
        than the ascent of the first, which is the condition under which the
        wrong expression loses pixels.
        """
        _stage_chain(tmp_path, monkeypatch, metrics={_CJK_KEY: (900, -300), _LATIN_KEY: (720, -40)})

        with Image.open(_render(tmp_path, "A")) as opened:
            alone = opened.convert("RGBA")
            top, bottom = _ink_rows(alone)[0]
            alone_ink = bottom - top
            alone_below = alone.height - bottom
        with Image.open(_render(tmp_path, "\u4e2d\nA")) as opened:
            stacked = opened.convert("RGBA")
            first_bottom = _ink_rows(stacked)[0][1]
            last_top, last_bottom = _ink_rows(stacked)[-1]

        # The top of the stack, for the same reason `sits_on_its_baseline`
        # pins it for one line: the first baseline is the first line's ascent
        # down, and reading any other line's ascent there moves the whole
        # stack without changing a single dimension asserted anywhere else.
        tall_ascent, _ = render._load_face(_CJK_KEY, _RENDER_STYLE["font_px"]).getmetrics()
        assert first_bottom == 2 * _RENDER_STYLE["stroke_px"] + tall_ascent
        assert last_bottom - last_top == alone_ink
        assert last_bottom < stacked.height
        # And the bottom edge sits where the *last* line's descent puts it, not
        # where the first line's would: reading the wrong one here does not
        # clip anything, it pads the canvas, and a caption positioned against
        # the bottom of its own PNG then rides that padding up the frame.
        assert stacked.height - last_bottom == alone_below

    def test_the_lines_are_drawn_in_the_order_they_were_written(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Nothing else here can tell a caption from the same caption upside down.

        Centring is symmetric about the widest line, a blank line in the
        middle is symmetric by construction, and the `.notdef` comparison is
        over a set of blobs rather than a sequence. Reversing the stack was
        measured to pass all 116 of them, and it is a layout fault the viewer
        reads immediately.
        """
        _stage_chain(tmp_path, monkeypatch)

        with Image.open(_render(tmp_path, "\u4e2d\u6587\n\u7b2c")) as opened:
            image = opened.convert("RGBA")
        alpha = image.getchannel("A").tobytes()
        per_line = []
        for top, bottom in _ink_rows(image):
            per_line.append(
                len(
                    _spans(
                        [
                            any(alpha[row * image.width + column] for row in range(top, bottom))
                            for column in range(image.width)
                        ]
                    )
                )
            )

        assert per_line == [2, 1]

    def test_the_letterform_sits_on_its_baseline(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Where a glyph is put vertically, stated as a number rather than assumed.

        Every run of a line is drawn from one baseline, and the baseline is
        the ascent below the top of the canvas -- so the ink of a face that
        sits on its baseline ends there too, with the stroke below it and the
        descent left empty under that. Told to draw from the ascender line
        instead, the same call puts the letterform a whole ascent lower and
        the canvas clips it, which changes no dimension this file asserts and
        no cluster it compares.
        """
        _stage_chain(tmp_path, monkeypatch)
        style = _style(**_RENDER_STYLE)
        ascent, _ = render._load_face(_CJK_KEY, style.font_px).getmetrics()

        with Image.open(_render(tmp_path, "\u4e2d")) as opened:
            image = opened.convert("RGBA")

        _, bottom = _ink_rows(image)[0]
        assert bottom == 2 * style.stroke_px + ascent
        assert bottom < image.height

    def test_a_line_stands_as_tall_as_the_tallest_face_on_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two faces on one line rarely agree about how tall a line is.

        Measured at 48 px, the packaged CJK face asks for an ascent of 56 and
        the packaged latin face for 48. Taking the shorter of them would crop
        the taller face's letterform on every mixed line, and there is nothing
        downstream that would notice a caption whose tops are shaved off.
        """
        _stage_chain(
            tmp_path, monkeypatch, metrics={_LATIN_KEY: (500, -100), _CJK_KEY: (800, -200)}
        )
        style = _style(**_RENDER_STYLE)
        tall = render._load_face(_CJK_KEY, style.font_px).getmetrics()
        short = render._load_face(_LATIN_KEY, style.font_px).getmetrics()
        assert tall > short, "the fixture no longer gives the two faces different metrics"

        with Image.open(_render(tmp_path, "A\u4e2d")) as image:
            assert image.height == tall[0] + tall[1] + 2 * style.stroke_px

    def test_a_short_line_is_centred_against_the_long_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Captions are centred; a stack flushed left reads as a layout fault.

        The canvas is as wide as the widest line, so this is the only place
        the alignment of the other lines is decided.

        The glyph is staged sitting in the middle of its own advance, which
        the default one does not: ink centred against the frame and an advance
        centred against the frame are the same thing only for a symmetric
        letterform, and the assertion below is written on the ink.
        """
        _stage_chain(tmp_path, monkeypatch, stem=(300, 400))

        with Image.open(_render(tmp_path, "\u4e2d\u6587\n\u4e2d")) as opened:
            image = opened.convert("RGBA")
        alpha = image.getchannel("A").tobytes()
        top, bottom = _ink_rows(image)[1]
        columns = _spans(
            [
                any(alpha[row * image.width + column] for row in range(top, bottom))
                for column in range(image.width)
            ]
        )

        left, right = columns[0][0], columns[-1][1]
        assert abs((image.width - right) - left) <= 1

    def test_a_single_line_is_as_tall_as_the_face_plus_the_stroke(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The stroke is drawn outside the outline, measured, so it needs room.

        `textbbox` grows by exactly `stroke_width` on each of the four sides,
        and the drawn ink was measured to do the same -- so a canvas sized to
        the letterform alone would clip the stroke at every edge.
        """
        _stage_chain(tmp_path, monkeypatch)
        style = _style(**_RENDER_STYLE)
        ascent, descent = render._load_face(_CJK_KEY, style.font_px).getmetrics()

        with Image.open(_render(tmp_path, "中")) as image:
            assert image.height == ascent + descent + 2 * style.stroke_px
            assert (
                image.width
                == render._load_face(_CJK_KEY, style.font_px).getlength("中") + 2 * style.stroke_px
            )

    def test_a_run_boundary_does_not_eat_the_neighbouring_fill(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Every stroke is drawn before any fill, because runs are drawn apart.

        Measured on the packaged faces: drawing run by run with the stroke and
        the fill together lets the next run's stroke paint over the previous
        run's letterform -- "AV" at stroke 20 kept 640 of its 909 fill pixels.
        A caption is the product's visible output, so the letterform coming
        back thinner wherever the face changes is not cosmetic.
        """
        _stage_chain(tmp_path, monkeypatch, stem=(100, 650))
        style = _style(**{**_RENDER_STYLE, "stroke_px": 12})

        with Image.open(_render(tmp_path, "A中", style=style)) as image:
            together = _opaque_pixels(image.convert("RGBA"), _WHITE)
        apart = 0
        for character in "A中":
            with Image.open(_render(tmp_path, character, style=style)) as image:
                apart += _opaque_pixels(image.convert("RGBA"), _WHITE)

        assert together == apart

    def test_a_caption_without_a_stroke_puts_nothing_dark_on_the_page(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The stroke pass has to be skipped, not drawn zero wide.

        A zero-width stroke pass still paints the letterform in the stroke
        colour, and the fill drawn over it blends with what is underneath:
        measured, 73 dark pixels appear along the edges and 271 fill pixels
        stop being fill.

        The stem is staged half a pixel off the grid, because a letterform
        whose edges land on whole pixels has no antialiasing to blend and
        would hide exactly the fault this case is looking for.
        """
        _stage_chain(tmp_path, monkeypatch, stem=(110, 215))

        with Image.open(_render(tmp_path, "中文", stroke_px=0)) as opened:
            image = opened.convert("RGBA")

        data = image.tobytes()
        dark = sum(1 for index in range(0, len(data), 4) if data[index + 3] and data[index] < 128)
        assert _opaque_pixels(image, _WHITE) > 0
        assert dark == 0


class TestFrameGuards:
    """The frame is not decoration here: it bounds the caption in both axes."""

    @pytest.mark.parametrize("side", _FRAME_ACCEPTED)
    def test_the_frame_dimension_endpoints_are_accepted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, side: int
    ) -> None:
        _stage_chain(tmp_path, monkeypatch)

        assert _render(tmp_path, "中", font_px=12, frame_width=side).exists()
        assert _render(tmp_path, "中", font_px=12, frame_height=side).exists()

    @pytest.mark.parametrize("side", _FRAME_REFUSED)
    def test_a_frame_dimension_one_step_outside_the_range_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, side: int
    ) -> None:
        _stage_chain(tmp_path, monkeypatch)

        with pytest.raises(render.CaptionStyleRejected, match="frame_width"):
            _render(tmp_path, "中", font_px=12, frame_width=side)
        with pytest.raises(render.CaptionStyleRejected, match="frame_height"):
            _render(tmp_path, "中", font_px=12, frame_height=side)

    @pytest.mark.parametrize("side", [True, 400.0, "400", None])
    def test_a_frame_dimension_that_is_not_an_int_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, side: object
    ) -> None:
        """`True` among them: `bool` is a subclass of `int` and 1 is not a frame."""
        _stage_chain(tmp_path, monkeypatch)

        with pytest.raises(render.CaptionStyleRejected):
            _render(tmp_path, "中", frame_width=side)  # type: ignore[arg-type]

    def test_a_font_taller_than_the_frame_is_refused_before_a_face_is_opened(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The obligation the style could not carry, because it holds no frame.

        Booked to this step by `docs/development/LE-09.md`: upstream's project
        aggregate makes this comparison, but LE-10 assembles a style locally
        and never reaches that aggregate, so without it here nothing makes it
        at all.

        The pair is read off an exploding `bundle_root` rather than off a
        message, which is what makes the boundary observable: at the endpoint
        the guard lets the render through and the missing face is what
        answers, one step past it the guard answers first. The frame is 400 by
        150, so a guard that compared against the width would let 151 through
        and be caught by the same pair.
        """
        monkeypatch.setattr(fonts, "bundle_root", lambda bundle: tmp_path / "nothing-here")

        with pytest.raises(fonts.CaptionFontUnavailable):
            _render(tmp_path, "中", font_px=150, frame_width=400, frame_height=150)
        with pytest.raises(render.CaptionStyleRejected, match="taller than"):
            _render(tmp_path, "中", font_px=151, frame_width=400, frame_height=150)

    def test_a_caption_taller_than_the_frame_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A font that fits says nothing about a block of four lines that does not.

        The endpoints are read off a render into a frame with room to spare,
        so neither of them is computed the way the renderer computes it.
        """
        _stage_chain(tmp_path, monkeypatch)
        text = "中\n文\n第\n三"
        with Image.open(_render(tmp_path, text, frame_height=_FRAME_ACCEPTED[1])) as image:
            needed = image.height
        assert needed > 128, "the fixture no longer clears the smallest frame"

        assert _render(tmp_path, text, frame_height=needed).exists()
        with pytest.raises(render.CaptionStyleRejected, match="taller than"):
            _render(tmp_path, text, frame_height=needed - 1)

    def test_a_glyph_wider_than_the_wrap_width_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Wrapping cannot save a character that does not fit on a line at all.

        Left to run, it would sit on a line of its own and put the PNG wider
        than the frame it is meant to be overlaid on. The two frame widths are
        literals so that widening the margin moves the boundary and is caught
        here rather than being cancelled out on both sides.
        """
        _stage_chain(tmp_path, monkeypatch)
        advance = render._load_face(_CJK_KEY, _RENDER_STYLE["font_px"]).getlength("中")
        assert advance == 35, "the fixture no longer puts the boundary at 131 px"

        assert _render(tmp_path, "中", frame_width=131).exists()
        with pytest.raises(render.CaptionStyleRejected, match="wider than"):
            _render(tmp_path, "中", frame_width=130)


class TestTextGuards:
    @pytest.mark.parametrize("text", [b"bytes", None, 12, ["a"]])
    def test_text_that_is_not_a_string_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: object
    ) -> None:
        _stage_chain(tmp_path, monkeypatch)

        with pytest.raises(fonts.CaptionTextRejected):
            _render(tmp_path, text)  # type: ignore[arg-type]

    def test_an_empty_caption_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`"".splitlines()` is empty, and a PNG of no lines is not a caption.

        PIL answers `ValueError: cannot write empty image` for the canvas that
        would follow, which is neither a font problem nor a style one.
        """
        _stage_chain(tmp_path, monkeypatch)

        with pytest.raises(fonts.CaptionTextRejected):
            _render(tmp_path, "")

    @pytest.mark.parametrize("text", [" ", "\u00a0", "\u200b", " \n "])
    def test_a_caption_that_puts_no_ink_on_the_page_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str
    ) -> None:
        """Blank characters are covered, so nothing upstream of this notices.

        A sized, inkless PNG is the shape this package exists to refuse: it
        overlays cleanly, ffprobe counts the frames, and the viewer sees no
        caption.
        """
        _stage_chain(tmp_path, monkeypatch)

        with pytest.raises(fonts.CaptionTextRejected):
            _render(tmp_path, text)
        assert list((tmp_path / "caption.png").parent.glob("caption*")) == []

    def test_a_control_character_inside_a_line_is_refused_as_text(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reaching `segment_runs` through the front door, not by calling it."""
        _stage_chain(tmp_path, monkeypatch)

        with pytest.raises(fonts.CaptionTextRejected):
            _render(tmp_path, "中\x00文")

    def test_a_character_no_face_draws_fails_the_whole_caption_closed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """And leaves nothing behind: half a caption is worse than none.

        A partial PNG at the destination passes every downstream check --
        ffmpeg overlays it, ffprobe agrees about the frames -- which is the
        shape of the incident this whole line is written against.
        """
        _stage_chain(tmp_path, monkeypatch)

        with pytest.raises(fonts.CaptionGlyphUnavailable) as refusal:
            _render(tmp_path, f"中{_UNCOVERED_CHARACTER}文")

        assert f"U+{ord(_UNCOVERED_CHARACTER):04X}" in str(refusal.value)
        assert _UNCOVERED_CHARACTER not in str(refusal.value)
        assert not (tmp_path / "caption.png").exists()


class TestWritingTheFile:
    def test_a_successful_render_leaves_no_working_file_behind(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stage_chain(tmp_path, monkeypatch)
        destination = tmp_path / "out" / "caption.png"
        destination.parent.mkdir()

        _render(tmp_path, "中文", destination=destination)

        assert [path.name for path in destination.parent.iterdir()] == ["caption.png"]

    def test_a_write_that_runs_out_of_space_leaves_the_previous_file_alone(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Measured: PIL leaves the truncated bytes where they fell.

        Saving a 600x600 image through a stream that stops at 200 bytes leaves
        a 200-byte file that `Image.open` rejects as truncated -- so a render
        that fails halfway would replace a good caption with an unreadable
        one. The rendered image is therefore written beside the destination
        and moved onto it, which is one atomic step.
        """
        _stage_chain(tmp_path, monkeypatch)
        destination = tmp_path / "out" / "caption.png"
        destination.parent.mkdir()
        destination.write_bytes(b"the caption from the last run")

        def _out_of_space(self: Image.Image, fp: Any, format: str | None = None, **_: Any) -> None:
            fp.write(b"\x89PNG\r\n\x1a\n truncated")
            raise OSError(errno.ENOSPC, "No space left on device")

        monkeypatch.setattr(Image.Image, "save", _out_of_space)

        with pytest.raises(render.CaptionOutputUnavailable):
            _render(tmp_path, "中文", destination=destination)

        assert destination.read_bytes() == b"the caption from the last run"
        assert [path.name for path in destination.parent.iterdir()] == ["caption.png"]

    def test_a_write_that_fails_some_other_way_is_still_an_output_problem(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The catch is broad because an encoder's failures are not enumerable.

        PIL answers `ValueError` for a canvas it will not write and for a name
        whose extension it does not know; fontTools was measured one layer
        down to answer `AssertionError` and `struct.error` on truncated input,
        neither of which anybody had listed. A caller that has one `except` at
        its boundary should not have to grow a second one for whichever type
        the next release picks.
        """
        _stage_chain(tmp_path, monkeypatch)
        destination = tmp_path / "out" / "caption.png"
        destination.parent.mkdir()

        def _refuses(self: Image.Image, fp: Any, format: str | None = None, **_: Any) -> None:
            raise ValueError("cannot write empty image")

        monkeypatch.setattr(Image.Image, "save", _refuses)

        with pytest.raises(render.CaptionOutputUnavailable):
            _render(tmp_path, "\u4e2d\u6587", destination=destination)

        assert list(destination.parent.iterdir()) == []

    def test_an_interrupt_during_the_write_is_not_turned_into_an_output_problem(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A render that cannot be stopped is a worse fault than one that fails.

        Same promise `glyph_coverage` makes one layer down, and the same
        reason it needs a case: a `BaseException` swallowed here would report
        Ctrl-C as "the caption could not be written".
        """
        _stage_chain(tmp_path, monkeypatch)
        destination = tmp_path / "out" / "caption.png"
        destination.parent.mkdir()

        def _interrupted(self: Image.Image, fp: Any, format: str | None = None, **_: Any) -> None:
            fp.write(b"\x89PNG\r\n\x1a\n")
            raise KeyboardInterrupt

        monkeypatch.setattr(Image.Image, "save", _interrupted)

        with pytest.raises(KeyboardInterrupt):
            _render(tmp_path, "中文", destination=destination)

        assert list(destination.parent.iterdir()) == []

    def test_a_missing_destination_directory_is_reported_as_an_output_problem(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stage_chain(tmp_path, monkeypatch)

        with pytest.raises(render.CaptionOutputUnavailable):
            _render(tmp_path, "中文", destination=tmp_path / "no-such-directory" / "caption.png")

    def test_a_destination_that_is_a_directory_is_reported_as_an_output_problem(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stage_chain(tmp_path, monkeypatch)
        destination = tmp_path / "out" / "caption.png"
        destination.mkdir(parents=True)

        with pytest.raises(render.CaptionOutputUnavailable):
            _render(tmp_path, "中文", destination=destination)

        assert [path.name for path in destination.parent.iterdir()] == ["caption.png"]

    @pytest.mark.skipif(sys.platform == "win32", reason="mode bits are not how Windows says no")
    def test_an_unwritable_destination_directory_is_reported_as_an_output_problem(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stage_chain(tmp_path, monkeypatch)
        directory = tmp_path / "read-only"
        directory.mkdir()
        os.chmod(directory, 0o500)
        try:
            with pytest.raises(render.CaptionOutputUnavailable):
                _render(tmp_path, "中文", destination=directory / "caption.png")
        finally:
            os.chmod(directory, 0o700)

    def test_the_failure_does_not_name_the_destination(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A local path is not something an error message may carry."""
        _stage_chain(tmp_path, monkeypatch)
        destination = tmp_path / "no-such-directory" / "caption.png"

        with pytest.raises(render.CaptionOutputUnavailable) as refusal:
            _render(tmp_path, "中文", destination=destination)

        assert str(destination) not in str(refusal.value)
        assert str(tmp_path) not in str(refusal.value)
