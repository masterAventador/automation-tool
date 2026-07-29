"""The packaged faces themselves: what they cover, how they load, what they draw.

Every other caption test draws with a synthetic face, and deliberately so: a
730-byte TTF built per case is the only way to say "this face covers exactly
these codepoints" without depending on what a 17 MB font happens to contain.
What that buys in control it gives up in reach -- a synthetic face is a
TrueType glyf face, so neither of the two formats the product actually ships
(OpenType/CFF and WOFF2) is parsed by any of those cases, and none of the
real-face readings recorded in `docs/development/LE-09.md` has anything
holding it in place.

This file is the other half. It uses the three faces the product ships and
pins the readings those earlier rounds measured, so that a face swap, a
Pillow upgrade or a FreeType change shows up as a red test rather than as a
line in a document nobody re-runs.

A missing face is a **failure, not a skip**. Skipping would make this file
disappear on precisely the machine where it matters -- one where the build
cache was never populated -- and the earlier rounds' readings would go back
to being unguarded prose while the suite stayed green.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, Final

import pytest
from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont, features

from automation_tool.executor.captions import fonts, render

_CJK_BOLD: Final = "noto-sans-cjk-sc-bold"
_CJK_REGULAR: Final = "noto-sans-cjk-sc-regular"
_LATIN: Final = "big-shoulders-display"

# What each packaged file is, as bytes rather than as a file name. The two
# Noto faces are named `.ttf` and are OpenType/CFF inside -- the fetch step
# renamed the official `.otf` -- so the suffix says nothing about which
# parsing path a face takes. These four bytes do.
_SFNT_MAGIC: Final[Mapping[str, bytes]] = {
    _CJK_BOLD: b"OTTO",
    _CJK_REGULAR: b"OTTO",
    _LATIN: b"wOF2",
}

# Coverage readings from the T3 and T4a rounds, recorded in
# `docs/development/LE-09.md` and until now held by nothing.
#
# The four interesting ones are interesting for different reasons:
#
# * U+200B and U+3000 disagree across the packaged faces in opposite
#   directions, which is what makes them fallback-chain cases rather than
#   coverage trivia -- a caption containing either one is drawn by a
#   different face depending on which one it is;
# * U+00AD is covered everywhere, so it is drawn rather than treated as a
#   hyphenation instruction;
# * U+2764 with U+FE0F is the emoji case: the base codepoint and the
#   variation selector are both uncovered by every packaged face, so a heart
#   pasted from a phone keyboard fails the whole caption closed. That is the
#   registered cost of drawing no substitute, and it is pinned here so the
#   cost cannot change silently in either direction.
#
# U+0020 and U+00A0 are the "one step outside" controls for the refused
# control-character ranges: `fonts` refuses U+0000-U+001F and U+0080-U+009F,
# and the case for those bounds being right is that the codepoints just past
# them are ones every packaged face really draws. The synthetic cases assert
# they are not refused as control characters; these assert the other half.
# Written as escapes rather than as the characters themselves: four of these
# are invisible, and two of those are invisible in different ways.
_COVERAGE_READINGS: Final[tuple[tuple[str, Mapping[str, bool]], ...]] = (
    ("\u0020", {_CJK_BOLD: True, _CJK_REGULAR: True, _LATIN: True}),  # SPACE
    ("\u00a0", {_CJK_BOLD: True, _CJK_REGULAR: True, _LATIN: True}),  # NO-BREAK SPACE
    ("\u00ad", {_CJK_BOLD: True, _CJK_REGULAR: True, _LATIN: True}),  # SOFT HYPHEN
    ("\u200b", {_CJK_BOLD: False, _CJK_REGULAR: False, _LATIN: True}),  # ZERO WIDTH SPACE
    ("\u3000", {_CJK_BOLD: True, _CJK_REGULAR: True, _LATIN: False}),  # IDEOGRAPHIC SPACE
    ("\u2764", {_CJK_BOLD: False, _CJK_REGULAR: False, _LATIN: False}),  # HEAVY BLACK HEART
    ("\ufe0f", {_CJK_BOLD: False, _CJK_REGULAR: False, _LATIN: False}),  # VARIATION SELECTOR-16
    ("\u4e2d", {_CJK_BOLD: True, _CJK_REGULAR: True, _LATIN: False}),  # CJK IDEOGRAPH "zhong"
    ("\u0041", {_CJK_BOLD: True, _CJK_REGULAR: True, _LATIN: True}),  # LATIN CAPITAL LETTER A
)

# Codepoints above the basic plane in the CJK face's character map. Reached
# only through a format 12 subtable -- format 4 cannot encode them -- so this
# count is also what says the format 12 path is really being read.
_CJK_ASTRAL_CODEPOINTS: Final = 2590

# U+20BB7, an Extension B ideograph: the single codepoint the roadmap's
# premise for LE-20 turns on, since the design document had assumed the
# packaged Chinese face stopped short of it.
_EXTENSION_B_IDEOGRAPH: Final = "\U00020bb7"

# Ink counts for "ABCDEFG" at 48 px from the one packaged variable face,
# measured in the T4a round. The gap is the point: at its default axis
# position the face is Thin, and a caption drawn there has ink, has the right
# dimensions, passes every downstream check and cannot be read.
_VARIABLE_FACE_INK: Final[Mapping[str, int]] = {
    "Thin": 663,
    "Regular": 1979,
    "Bold": 2984,
}
_VARIABLE_FACE_INK_TOLERANCE: Final = 0.05
_INK_SAMPLE: Final = "ABCDEFG"
_INK_SAMPLE_PX: Final = 48

# The caption the end-to-end case draws, and the frame it is drawn for. Long
# enough to wrap, mixed enough to need two faces, and carrying a tab so the
# layout step's own obligations are exercised from the public entry rather
# than from a unit case.
#
# The fullwidth commas are the point rather than an oversight: this is the
# punctuation a Chinese caption is actually written with, and it is drawn by
# the Chinese face while the latin words beside it are not.
_ACCEPTANCE_TEXT: Final = (
    "这条字幕会自动换行，因为它比一帧画面还要长得多，中间还夹着 latin words 和一个\t制表符"  # noqa: RUF001
)
_ACCEPTANCE_FRAME: Final = (1080, 1920)
_ACCEPTANCE_LINES: Final = 3

# A Chinese line with a latin line under it, and the latin line carries a
# descender on purpose.
#
# The defect this shape exists to catch shrinks the canvas by the difference
# between the two faces' ascents, so it is only visible if the caption's ink
# reaches into the bottom of the canvas. Measured on the packaged faces at
# every size from 12 to 48 px: with "AV" the last line leaves 3 to 11 empty
# rows below it -- more than the ascent difference at most sizes, so the
# shortened canvas would clip nothing and the case would pass on the defect.
# With a descender the gap closes to 1 row, and at 13 and 14 px it closes
# **exactly to zero** -- the ink's last row is the canvas's last row. Those
# two sizes are therefore the natural boundary for this shape, and are
# parametrised alongside an ordinary working size.
_MIXED_METRIC_CAPTION: Final = "中文\nAy"
_ZERO_SLACK_SIZES: Final = (13, 14)
_MIXED_METRIC_SIZES: Final = (*_ZERO_SLACK_SIZES, 48)

_TRANSPARENT: Final = (0, 0, 0, 0)
_WHITE: Final = (255, 255, 255, 255)
_BLACK: Final = (0, 0, 0, 255)


@pytest.fixture(autouse=True)
def _clear_coverage_cache() -> Iterator[None]:
    """The same guard the other two caption files carry, for the same reason.

    `glyph_coverage` is memoised on the font key alone, and the synthetic
    cases point those same keys at whatever file they staged. Without this a
    reading here could come back from a 730-byte fixture built by another
    file, which would make every coverage assertion below meaningless while
    leaving them green.
    """
    fonts.glyph_coverage.cache_clear()
    yield
    fonts.glyph_coverage.cache_clear()


@pytest.fixture(autouse=True)
def _require_the_packaged_faces() -> None:
    """Fail the file rather than skip it when a face is not on this machine.

    `pytest.skip` is the reflex here and it is the wrong one. The faces are
    fetched at build time into a cache outside the repository, so the machine
    that has not run that step is exactly the machine where these cases would
    have caught something -- and a skipped file reports green.
    """
    missing = []
    for font_key in fonts.REGISTERED_CAPTION_FONTS:
        try:
            fonts.resolve_font_file(font_key)
        except fonts.CaptionFontRejected as error:
            missing.append(f"{font_key}: {error}")
    if missing:
        raise AssertionError(
            "the packaged caption faces are not on this machine, so the "
            "real-face acceptance cases cannot run. Fetch them with "
            "`scripts/subtitle_font_assets.py` (the two Noto faces) and check "
            "the checkout is complete (the Big Shoulders face is committed). " + "; ".join(missing)
        )


def _coverage_parameters() -> list[Any]:
    return [
        pytest.param(character, font_key, covered, id=f"U+{ord(character):04X}-{font_key}")
        for character, readings in _COVERAGE_READINGS
        for font_key, covered in readings.items()
    ]


def _ink(face: ImageFont.FreeTypeFont, text: str) -> int:
    """How many pixels the face puts on the page for this text."""
    mask = face.getmask(text, mode="L")
    width, height = mask.size
    return sum(
        1 for index in range(width * height) if mask.getpixel((index % width, index // width))
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
    """The row ranges carrying ink -- one per drawn line of the caption."""
    alpha = image.getchannel("A").tobytes()
    width, height = image.size
    return _spans(
        [any(alpha[row * width + column] for column in range(width)) for row in range(height)]
    )


def _glyph_clusters(image: Image.Image) -> list[bytes]:
    """Every blob of ink, cropped to itself, in reading order."""
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


def _notdef_clusters(font_px: int, stroke_px: int) -> list[bytes]:
    """What each packaged face draws for a character it cannot draw.

    The acceptance criterion this task was given, and the reason "the PNG has
    ink" was rejected as one: the Chinese face answers a missing codepoint
    with a filled box that has plenty of ink, and a latin face answers it with
    nothing at all, so ink alone is blind in both directions. U+10FFFF is
    guaranteed to be in no character map.

    Drawn with a bare `ImageDraw.text` call and literal colours rather than
    through the renderer, so this reference does not move when the thing it is
    a reference for moves.
    """
    clusters: list[bytes] = []
    for font_key in fonts.REGISTERED_CAPTION_FONTS:
        face = render._load_face(font_key, font_px)
        size = font_px * 4
        canvas = Image.new("RGBA", (size, size), _TRANSPARENT)
        ImageDraw.Draw(canvas).text(
            (size // 4, size * 3 // 4),
            "\U0010ffff",
            font=face,
            anchor="ls",
            fill=_WHITE,
            stroke_width=stroke_px,
            stroke_fill=_BLACK,
        )
        box = canvas.getchannel("A").getbbox()
        if box is not None:
            clusters.append(canvas.crop(box).tobytes())
    return clusters


class TestThePackagedFacesArePresent:
    def test_every_registered_face_is_on_this_machine(self) -> None:
        """The premise every other case in this file rests on.

        Stated as its own case so a machine without the faces reports one
        legible failure about the fetch step, rather than a dozen assertion
        errors about coverage readings.
        """
        for font_key in fonts.REGISTERED_CAPTION_FONTS:
            assert fonts.resolve_font_file(font_key).is_file()

    @pytest.mark.parametrize(("font_key", "magic"), sorted(_SFNT_MAGIC.items()))
    def test_each_face_is_the_format_its_parsing_path_assumes(
        self, font_key: str, magic: bytes
    ) -> None:
        """Two real formats, and neither of them is what the synthetic cases build.

        The synthetic faces are TrueType `glyf`, so the OpenType/CFF path and
        the WOFF2 path -- the two the product actually ships -- are exercised
        by nothing else in the suite. This says which is which, in bytes,
        because both Noto files are named `.ttf` while being CFF inside.
        """
        assert fonts.resolve_font_file(font_key).read_bytes()[:4] == magic

    def test_the_coverage_table_names_exactly_the_registered_faces(self) -> None:
        """A face added to the register must not slip past this file unread.

        Every reading below is a per-face expectation, so a fourth face would
        otherwise be shipped with no coverage reading at all and every case
        here would stay green.
        """
        for _, readings in _COVERAGE_READINGS:
            assert set(readings) == set(fonts.REGISTERED_CAPTION_FONTS)
        assert set(_SFNT_MAGIC) == set(fonts.REGISTERED_CAPTION_FONTS)


class TestRealFaceCoverage:
    @pytest.mark.parametrize(("character", "font_key", "covered"), _coverage_parameters())
    def test_the_packaged_faces_cover_what_was_measured(
        self, character: str, font_key: str, covered: bool
    ) -> None:
        assert (ord(character) in fonts.glyph_coverage(font_key)) is covered

    def test_a_heart_from_a_phone_keyboard_fails_the_caption_closed(self, tmp_path: Path) -> None:
        """The registered cost of refusing to draw a substitute, end to end.

        The readings above say the two codepoints are uncovered; this says
        what that means for a caller. It is here rather than beside the other
        refusals because the pair is the case a user is most likely to
        produce, and the decision to fail rather than draw a box is the one
        most likely to be revisited.
        """
        with pytest.raises(fonts.CaptionGlyphUnavailable) as refusal:
            render.render_caption(
                "❤️",
                render.CaptionRenderStyle(
                    font_key=_CJK_BOLD, font_px=48, stroke_px=2, line_spacing=1.5
                ),
                frame_width=_ACCEPTANCE_FRAME[0],
                frame_height=_ACCEPTANCE_FRAME[1],
                destination=tmp_path / "caption.png",
            )
        assert "U+2764" in str(refusal.value)
        assert list(tmp_path.iterdir()) == []

    def test_the_chinese_face_reaches_past_the_basic_plane(self) -> None:
        """Format 12 is being read, and Extension B is inside it.

        The count is the evidence for the first half: format 4 cannot encode a
        codepoint above U+FFFF, so a non-zero astral count can only have come
        through the format 12 subtable. The named ideograph is the evidence
        for the second, and it is the fact LE-20's premise turns on -- the
        design document had assumed this face stopped below Extension B.
        """
        coverage = fonts.glyph_coverage(_CJK_BOLD)
        assert sum(1 for codepoint in coverage if codepoint > 0xFFFF) == _CJK_ASTRAL_CODEPOINTS
        assert ord(_EXTENSION_B_IDEOGRAPH) in coverage

    def test_the_latin_face_has_only_a_format_4_subtable(self) -> None:
        """Which is why the case above has to be made on the Chinese face.

        Also the reason the latin face covers no astral codepoint at all: it
        has no subtable that could carry one.
        """
        with TTFont(str(fonts.resolve_font_file(_LATIN)), lazy=True) as face:
            assert {table.format for table in face["cmap"].tables} == {4}
        assert not any(codepoint > 0xFFFF for codepoint in fonts.glyph_coverage(_LATIN))

    def test_the_chinese_face_carries_a_subtable_that_is_not_unicode(self) -> None:
        """The shape that made `getBestCmap() is None` reachable at all.

        `glyph_coverage` handles a face whose character map has no Unicode
        subtable, and that branch reads as defensive until you notice the
        packaged Chinese face already carries a non-Unicode subtable next to
        its Unicode ones -- platform 1, encoding 25. A face carrying only that
        kind is an ordinary icon font, which is exactly what LE-20 might add.
        """
        with TTFont(str(fonts.resolve_font_file(_CJK_BOLD)), lazy=True) as face:
            identifiers = {(table.platformID, table.platEncID) for table in face["cmap"].tables}
        assert (1, 25) in identifiers

    @pytest.mark.parametrize("font_key", [_CJK_BOLD, _LATIN])
    def test_no_unicode_subtable_is_dropped_by_taking_the_best_one(self, font_key: str) -> None:
        """`getBestCmap()` picks one subtable; on these faces it loses nothing.

        A face can carry several Unicode subtables that disagree, and the
        coverage judgement takes only one of them. On a face where they
        disagreed, that would silently under-report coverage and push
        characters onto the fallback chain that the face can draw perfectly
        well. Measured here rather than defended against: on both packaged
        faces the union of every Unicode subtable is exactly what
        `getBestCmap()` answers, with no conflicting mapping between them.
        """
        with TTFont(str(fonts.resolve_font_file(font_key)), lazy=True) as face:
            best = face.getBestCmap()
            union: dict[int, str] = {}
            conflicts = 0
            for table in face["cmap"].tables:
                if table.format == 14 or not table.isUnicode():
                    continue
                for codepoint, name in table.cmap.items():
                    if codepoint in union and union[codepoint] != name:
                        conflicts += 1
                    union[codepoint] = name
        assert conflicts == 0
        assert set(union) == set(best)


class TestRealFaceLoading:
    @pytest.mark.parametrize("font_key", sorted(_SFNT_MAGIC))
    def test_every_packaged_face_loads_from_its_own_file(self, font_key: str) -> None:
        """Both real parsing paths, through the function production uses.

        The `face.path` comparison is the guard added in the T4a fix round:
        `ImageFont.truetype` answers a file it cannot read by searching the
        machine's font directories for the same base name, and would hand back
        a face the rights register never cleared. Nothing before this case
        exercised that guard on a face that really is packaged.
        """
        face = render._load_face(font_key, _INK_SAMPLE_PX)
        assert face.path == fonts.resolve_font_file(font_key)
        assert face.size == _INK_SAMPLE_PX

    @pytest.mark.parametrize("font_key", [_CJK_BOLD, _CJK_REGULAR])
    def test_a_packaged_static_face_has_no_axes_to_pin(self, font_key: str) -> None:
        """The static branch of the weight pinning, on the real static faces.

        It is reached by catching `OSError` from FreeType rather than by
        asking a flag, and that reading is only as good as the faces it was
        taken from.
        """
        face = ImageFont.truetype(fonts.resolve_font_file(font_key), _INK_SAMPLE_PX)
        with pytest.raises(OSError):
            face.get_variation_names()

    def test_the_variable_face_is_drawn_at_a_weight_that_reads(self) -> None:
        """Thin, Regular and Bold, and which one the loader hands back.

        The ordering is the assertion that matters -- it is what says the
        default axis position really is the faint one and that the pinning
        moves off it -- but the three counts are pinned too, within a stated
        tolerance, because a face swap that kept the ordering while halving
        the weight would otherwise pass. The tolerance is 5%: FreeType's
        rasteriser can move a handful of edge pixels between patch releases,
        and the reading being defended is a factor of four rather than a
        pixel.
        """
        path = fonts.resolve_font_file(_LATIN)
        face = ImageFont.truetype(path, _INK_SAMPLE_PX)
        measured = {"Thin": _ink(face, _INK_SAMPLE)}
        for instance in ("Regular", "Bold"):
            face.set_variation_by_name(instance)
            measured[instance] = _ink(face, _INK_SAMPLE)

        assert measured["Thin"] < measured["Regular"] < measured["Bold"]
        for instance, expected in _VARIABLE_FACE_INK.items():
            assert abs(measured[instance] - expected) <= expected * _VARIABLE_FACE_INK_TOLERANCE

        assert _ink(render._load_face(_LATIN, _INK_SAMPLE_PX), _INK_SAMPLE) == measured["Bold"]

    def test_the_face_registered_as_bold_really_is_the_bolder_one(self) -> None:
        """The two Chinese faces are told apart by ink and by nothing else.

        Found by swapping them: with the Regular face's bytes written under
        the Bold face's name, every other case in this file still passed.
        They carry identical metrics -- ascent 56, descent 14 at 48 px -- and
        identical character maps, 44810 entries each, so coverage, layout,
        wrapping and the canvas geometry are all blind to the substitution.

        That substitution is not hypothetical: the two are fetched at build
        time into the same directory and told apart only by file name, and the
        bolder of them is `DEFAULT_CAPTION_FONT_KEY`, which is what every
        caption is drawn with unless a setting says otherwise. Drawing at the
        wrong weight is the failure this package exists to prevent -- ink is
        present, dimensions are right, every downstream check passes, and the
        caption is harder to read than it was meant to be.
        """
        bold, regular = (
            _ink(render._load_face(font_key, _INK_SAMPLE_PX), "中文永")
            for font_key in (_CJK_BOLD, _CJK_REGULAR)
        )
        assert bold > regular

    def test_the_coverage_of_the_variable_face_does_not_move_with_its_weight(self) -> None:
        """Pinning a weight changes outlines, not the character map.

        Recorded in the T3 round as the reason the fallback chain can be
        decided once per caption rather than once per weight.
        """
        before = fonts.glyph_coverage(_LATIN)
        render._load_face(_LATIN, _INK_SAMPLE_PX)
        fonts.glyph_coverage.cache_clear()
        assert fonts.glyph_coverage(_LATIN) == before


class TestTextLayoutPreconditions:
    def test_this_pillow_lays_text_out_without_libraqm(self) -> None:
        """An environment assertion, and the premise under two other verdicts.

        Two mutants were cleared as true equivalents on the strength of this
        being false, and one defence was kept on the strength of it possibly
        becoming true:

        * `math.ceil(text_width)` versus `int(text_width)` are the same
          function while every advance is a whole number, which they are only
          because Pillow is laying text out itself;
        * the wrap decision sums per-character advances, and argues that the
          sum equals the width of the whole string for the same reason.

        Both verdicts are premises about this machine rather than about the
        code, so they are asserted rather than assumed. **If this case goes
        red on a Pillow built with libraqm, those two verdicts have to be
        re-examined, not this assertion relaxed.**
        """
        assert features.check("raqm") is False

    @pytest.mark.parametrize("text", ["AV", "To", "中文", "A中", _INK_SAMPLE])
    @pytest.mark.parametrize("font_key", sorted(_SFNT_MAGIC))
    def test_measuring_a_string_equals_measuring_its_characters(
        self, font_key: str, text: str
    ) -> None:
        """What the wrap step assumes, checked on the faces it will assume it of.

        "AV" and "To" are the classic kerning pairs: a stack that kerned would
        make the whole narrower than the sum of its parts, and the wrap
        decision would then disagree with the drawn width.
        """
        face = render._load_face(font_key, _INK_SAMPLE_PX)
        assert face.getlength(text) == sum(face.getlength(character) for character in text)

    @pytest.mark.parametrize("font_key", sorted(_SFNT_MAGIC))
    def test_every_advance_is_a_whole_number(self, font_key: str) -> None:
        """The other half of the same premise, over a real span of codepoints.

        `getlength` is typed as a float and the canvas width rounds it up. On
        this build the rounding never has anything to do, which is why that
        `math.ceil` is recorded as an unguarded defence rather than as a
        tested one -- this case says why it is unguarded, on the faces it
        would guard.
        """
        face = render._load_face(font_key, _INK_SAMPLE_PX)
        coverage = sorted(fonts.glyph_coverage(font_key))[:400]
        for codepoint in coverage:
            if codepoint in fonts.UNDRAWABLE_CODEPOINTS:
                continue
            advance = face.getlength(chr(codepoint))
            assert advance == int(advance)


class TestRealMixedMetricLayout:
    """A latin line standing under a Chinese one, drawn by two real faces.

    Every multi-line case elsewhere draws both lines with one synthetic face,
    which is how the canvas-height defect survived: reading the last line's
    ascent instead of the first's is exactly right whenever the two lines use
    the same face, and cuts the bottom off the caption whenever they do not.
    The packaged faces disagree about ascent by a wide margin, so this is the
    shape the defect actually appears in.

    Reaching the latin face for a whole line takes a style that names it: the
    Chinese faces cover latin themselves, so with a Chinese style key a latin
    line never falls through to the fallback.
    """

    @staticmethod
    def _draw(tmp_path: Path, font_px: int) -> tuple[Image.Image, render.CaptionRenderStyle]:
        style = render.CaptionRenderStyle(
            font_key=_LATIN, font_px=font_px, stroke_px=0, line_spacing=1.0
        )
        destination = render.render_caption(
            _MIXED_METRIC_CAPTION,
            style,
            frame_width=_ACCEPTANCE_FRAME[0],
            frame_height=_ACCEPTANCE_FRAME[1],
            destination=tmp_path / "caption.png",
        )
        with Image.open(destination) as image:
            return image.convert("RGBA"), style

    @pytest.mark.parametrize("font_px", _MIXED_METRIC_SIZES)
    def test_the_two_faces_really_disagree_about_how_tall_a_line_is(self, font_px: int) -> None:
        """The premise, stated first so the case below cannot pass vacuously.

        A fixture in which both faces happen to agree would make every
        assertion about mixed metrics true by accident -- the same fault as
        the square frame that let a width/height swap survive.
        """
        chinese = render._load_face(_CJK_BOLD, font_px).getmetrics()
        latin = render._load_face(_LATIN, font_px).getmetrics()
        assert chinese[0] != latin[0]

    @pytest.mark.parametrize("font_px", _MIXED_METRIC_SIZES)
    def test_a_latin_line_under_a_chinese_one_is_not_clipped(
        self, tmp_path: Path, font_px: int
    ) -> None:
        """The canvas is measured from the first line's ascent, on real faces.

        Four assertions, and the last is the one with teeth. It spells out the
        height the defect would have produced -- the last line's ascent in
        place of the first's -- and requires the caption's ink to reach past
        it. Without that, the case passes on any canvas merely large enough,
        which is how the defect survived a full round of multi-line cases.
        """
        image, style = self._draw(tmp_path, font_px)
        chinese_ascent, chinese_descent = render._load_face(_CJK_BOLD, font_px).getmetrics()
        latin_ascent, latin_descent = render._load_face(_LATIN, font_px).getmetrics()
        # Each line steps by its own line box, so the first line's step is the
        # Chinese face's and the last line contributes only its descent.
        step = round((chinese_ascent + chinese_descent) * style.line_spacing)
        height_if_measured_from_the_last_line = latin_ascent + step + latin_descent

        rows = _ink_rows(image)
        assert len(rows) == 2
        assert image.height == chinese_ascent + step + latin_descent
        assert rows[-1][1] <= image.height
        assert rows[-1][1] > height_if_measured_from_the_last_line

    @pytest.mark.parametrize("font_px", _ZERO_SLACK_SIZES)
    def test_the_caption_ends_exactly_where_the_canvas_does(
        self, tmp_path: Path, font_px: int
    ) -> None:
        """At these two sizes the canvas has no room to spare at all.

        Which is what makes them worth naming: at every other size measured
        there is a row or more of empty canvas under the last line, and a
        height that came out one short would still look fine. Here it would
        not. If a face swap or a metrics change moves this, the sizes above
        stop being the boundary they were chosen for -- so the property is
        asserted rather than left as a comment on the choice.
        """
        image, _ = self._draw(tmp_path, font_px)
        assert _ink_rows(image)[-1][1] == image.height


class TestTheDrawnCaption:
    """The public entry, the packaged faces, and a file on disk."""

    @staticmethod
    def _style() -> render.CaptionRenderStyle:
        return render.CaptionRenderStyle(
            font_key=_CJK_BOLD, font_px=54, stroke_px=4, line_spacing=1.4
        )

    def _render(self, tmp_path: Path) -> Path:
        return render.render_caption(
            _ACCEPTANCE_TEXT,
            self._style(),
            frame_width=_ACCEPTANCE_FRAME[0],
            frame_height=_ACCEPTANCE_FRAME[1],
            destination=tmp_path / "caption.png",
        )

    def test_the_caption_is_a_transparent_png_of_the_right_shape(self, tmp_path: Path) -> None:
        """Layer one of the criterion: the file, its mode and its size.

        The wrap width is `frame_width` less both margins, and a wrapped
        caption has to come out no wider than that or it runs off the frame it
        will be overlaid on.
        """
        destination = self._render(tmp_path)
        with Image.open(destination) as opened:
            assert opened.format == "PNG"
            image = opened.convert("RGBA")
        assert image.mode == "RGBA"
        assert image.width <= _ACCEPTANCE_FRAME[0] - 2 * render.CAPTION_SIDE_MARGIN_PX
        assert image.height < _ACCEPTANCE_FRAME[1]
        assert len(_ink_rows(image)) == _ACCEPTANCE_LINES
        for corner in ((0, 0), (image.width - 1, 0), (0, image.height - 1)):
            assert image.getpixel(corner) == _TRANSPARENT

    def test_the_caption_is_white_on_black_and_nothing_else(self, tmp_path: Path) -> None:
        """Layer two: there is ink, and it is the pair that stays readable.

        On its own this is the assertion the ledger rejected -- a caption of
        tofu boxes satisfies it completely -- which is why the case below
        exists. It is still worth making, because a caption drawn in the
        stroke colour alone would satisfy the differential.
        """
        with Image.open(self._render(tmp_path)) as opened:
            image = opened.convert("RGBA")
        colours = {colour for _, colour in image.getcolors(maxcolors=1 << 20) or []}
        assert _WHITE in colours
        assert _BLACK in colours

    def test_no_drawn_glyph_is_the_face_saying_it_cannot_draw(self, tmp_path: Path) -> None:
        """Layer three, and the only one of the three with any teeth.

        Ink says nothing: the Chinese face answers a codepoint it does not
        have with a filled box carrying about a thousand ink pixels, and every
        downstream check -- dimensions, ffprobe frame counts, "the PNG is not
        empty" -- passes on a caption made entirely of them. So each blob of
        ink is compared against what each packaged face draws for a codepoint
        no face has.
        """
        with Image.open(self._render(tmp_path)) as opened:
            image = opened.convert("RGBA")
        style = self._style()
        notdef = _notdef_clusters(style.font_px, style.stroke_px)
        assert notdef, "no packaged face drew anything for U+10FFFF; the reference is broken"
        clusters = _glyph_clusters(image)
        assert clusters
        for cluster in clusters:
            assert cluster not in notdef

    @pytest.mark.parametrize(
        ("text", "refusal", "detail"),
        [
            pytest.param("emoji \U0001f600", fonts.CaptionGlyphUnavailable, "U+1F600", id="emoji"),
            pytest.param("", fonts.CaptionTextRejected, "nothing to draw", id="empty"),
            pytest.param("   ", fonts.CaptionTextRejected, "no ink on the page", id="spaces"),
        ],
    )
    def test_a_caption_that_cannot_be_drawn_leaves_nothing_behind(
        self, tmp_path: Path, text: str, refusal: type[Exception], detail: str
    ) -> None:
        """Fail closed, on the real faces, through the entry a caller uses.

        The directory listing is the assertion that matters. A refusal that
        left a half-written PNG, or the working file the writer creates beside
        it, would hand the overlay step a file to find -- and the overlay step
        has no way to tell it apart from a caption that was drawn.
        """
        destination = tmp_path / "caption.png"
        with pytest.raises(refusal) as raised:
            render.render_caption(
                text,
                self._style(),
                frame_width=_ACCEPTANCE_FRAME[0],
                frame_height=_ACCEPTANCE_FRAME[1],
                destination=destination,
            )
        assert detail in str(raised.value)
        assert list(tmp_path.iterdir()) == []
