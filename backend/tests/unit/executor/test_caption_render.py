"""Caption style values, and turning a registered key into a PIL face."""

from __future__ import annotations

import dataclasses
import math
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib.tables.TupleVariation import TupleVariation
from PIL import ImageFont

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

    `instances` turns the face into a variable one carrying those named
    instances on a `wght` axis; empty leaves it static, which is the shape
    both packaged Noto faces have. `widen_at_maximum` adds real `gvar` deltas
    so the outline actually thickens towards the top of the axis -- without
    them every instance would draw identical ink and a test could only observe
    that a name was set, not that anything happened.

    `family_name` exists so a face staged somewhere other than the packaged
    location can be told apart from the packaged one by name alone.
    """
    builder = FontBuilder(unitsPerEm=1000, isTTF=True)
    builder.setupGlyphOrder([".notdef", "A"])
    builder.setupCharacterMap({ord("A"): "A"})

    stem = TTGlyphPen(None)
    stem.moveTo((100, 0))
    stem.lineTo((100, 700))
    stem.lineTo((200, 700))
    stem.lineTo((200, 0))
    stem.closePath()
    builder.setupGlyf({".notdef": TTGlyphPen(None).glyph(), "A": stem.glyph()})

    builder.setupHorizontalMetrics({".notdef": (700, 100), "A": (700, 100)})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
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
            builder.setupGvar({"A": [TupleVariation({"wght": (0.0, 1.0, 1.0)}, deltas)]})

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
    """Point PIL's basename search at a directory of our own, and name it.

    When FreeType refuses a file, `ImageFont.truetype` catches its own
    `OSError` and walks the platform's font directories for a file of the same
    base name. Every platform it searches takes its user-level directory from
    the environment -- `HOME` on macOS, `WINDIR` on Windows, `XDG_DATA_HOME`
    and `XDG_DATA_DIRS` on Linux -- so redirecting those is what lets a case
    below decide for itself whether a collision exists.

    Without this the two "will not load" cases were green only because no font
    of the packaged name happened to sit in the search path of the machine
    they ran on; this one has 267 faces in `~/Library/Fonts` alone, and adding
    faces is LE-20's deliverable.

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

        # Premise: with the search path emptied, PIL really does refuse rather
        # than answering with some other file of the same name. That is what
        # makes this the case covering the `OSError` handler; the substitution
        # it would otherwise take is the case below.
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
