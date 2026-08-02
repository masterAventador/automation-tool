"""The packaged faces themselves: what they cover, how they load, what they draw.

Every other caption test draws with a synthetic face, and deliberately so: a
730-byte TTF built per case is the only way to say "this face covers exactly
these codepoints" without depending on what a 17 MB font happens to contain.
What that buys in control it gives up in reach -- a synthetic face is a
TrueType glyf face, so neither of the two formats the product actually ships
(OpenType/CFF and WOFF2) is parsed by any of those cases, and none of the
real-face readings recorded in `docs/development/LE-09.md` has anything
holding it in place.

This file is the other half. It uses the five faces the product ships and
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
_RARE_CJK_P1: Final = "plangothic-p1-regular"
_RARE_CJK_P2: Final = "plangothic-p2-regular"
_LATIN: Final = "big-shoulders-display"

# What each packaged file is, as bytes rather than as a file name. The two
# Noto faces are named `.ttf` and are OpenType/CFF inside -- the fetch step
# renamed the official `.otf` -- so the suffix says nothing about which
# parsing path a face takes. These four bytes do.
_SFNT_MAGIC: Final[Mapping[str, bytes]] = {
    _CJK_BOLD: b"OTTO",
    _CJK_REGULAR: b"OTTO",
    _RARE_CJK_P1: b"\x00\x01\x00\x00",
    _RARE_CJK_P2: b"\x00\x01\x00\x00",
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
def _reading(noto: bool, p1: bool, p2: bool, latin: bool) -> Mapping[str, bool]:
    return {
        _CJK_BOLD: noto,
        _CJK_REGULAR: noto,
        _RARE_CJK_P1: p1,
        _RARE_CJK_P2: p2,
        _LATIN: latin,
    }


_COVERAGE_READINGS: Final[tuple[tuple[str, Mapping[str, bool]], ...]] = (
    ("\u0020", _reading(True, True, True, True)),  # SPACE
    ("\u00a0", _reading(True, True, True, True)),  # NO-BREAK SPACE
    ("\u00ad", _reading(True, True, True, True)),  # SOFT HYPHEN
    ("\u200b", _reading(False, False, False, True)),  # ZERO WIDTH SPACE
    ("\u3000", _reading(True, False, False, False)),  # IDEOGRAPHIC SPACE
    ("\u2764", _reading(False, False, False, False)),  # HEAVY BLACK HEART
    ("\ufe0f", _reading(False, False, True, False)),  # VARIATION SELECTOR-16
    ("\u4e2d", _reading(True, False, True, False)),  # CJK IDEOGRAPH "zhong"
    ("\u0041", _reading(True, True, True, True)),  # LATIN CAPITAL LETTER A
)

# Codepoints above the basic plane in the CJK face's character map. Reached
# only through a format 12 subtable -- format 4 cannot encode them -- so this
# count is also what says the format 12 path is really being read.
_CJK_ASTRAL_CODEPOINTS: Final = 2590

# U+20BB7, an Extension B ideograph: the single codepoint the roadmap's
# premise for LE-20 turns on, since the design document had assumed the
# packaged Chinese face stopped short of it.
_EXTENSION_B_IDEOGRAPH: Final = "\U00020bb7"

# LATIN SMALL LETTER DOTLESS I: covered by the latin face and by neither
# Chinese one, which makes it the fallback chain's own case. Twelve codepoints
# have that property on the packaged set and ten of them put down ink. This
# one is among the heaviest (198 pixels at 54 px; the heaviest is U+2044
# FRACTION SLASH at 253), so a comparison against the face's own rendering has
# something to compare. Chosen over U+2044 because it is a real letter rather
# than punctuation, which is the shape a caption is actually made of.
_LATIN_ONLY_CHARACTER: Final = "\u0131"  # LATIN SMALL LETTER DOTLESS I

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
# 49 written characters, with the tab expanded to the next four-column stop.
_ACCEPTANCE_CHARACTERS: Final = 51

# A Chinese line with a latin line under it, and the latin line carries a
# descender on purpose.
#
# The defect this shape exists to catch shrinks the canvas by the difference
# between the two faces' ascents, so it is only visible if the caption's ink
# reaches into the bottom of the canvas. With "AV" the last line leaves 3 to
# 11 empty rows below it -- more than that difference at most sizes, so the
# shortened canvas would clip nothing and the case would pass on the defect.
# A descender closes the gap.
_MIXED_METRIC_CAPTION: Final = "中文\nAy"

# The sizes at which the defect has no room at all to hide in.
#
# The quantity that matters is the one the T4b round recorded in
# `docs/development/LE-09.md`: `aₙ + dₙ - a₀`, the last line's own line box
# less the first line's ascent, which is exactly the slack the shortened
# canvas has to eat before it starts cutting ink. At 48 px it is +3 px.
# Swept over 12-200 px on the packaged faces, **13 and 14 px are the only two
# sizes where it is 0**.
#
# Deliberately not "the ink's last row is the canvas's last row", which was
# the first way this was written: that is true at ten sizes between 12 and 48
# and so says nothing about 13 and 14 in particular.
_ZERO_MARGIN_SIZES: Final = (13, 14)
_MIXED_METRIC_SIZES: Final = (*_ZERO_MARGIN_SIZES, 48)

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
            "`scripts/subtitle_font_assets.py` (the two Noto and two Plangothic "
            "faces) and check "
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


def _drawn_alone(face: ImageFont.FreeTypeFont, text: str, font_px: int, stroke_px: int) -> bytes:
    """One character on its own transparent canvas, cropped to its ink.

    Drawn with a bare `ImageDraw.text` call and literal colours rather than
    through the renderer, so a reference built from this does not move when
    the thing it is a reference for moves. Cropping to the ink discards where
    it landed and keeps its shape, which is what the comparisons here are
    about.

    Answers `b""` for a character that puts down nothing -- a space, a
    zero-width joiner. That is a real answer rather than a missing one: what
    the callers ask is whether a character came out as the face's "I cannot
    draw this" glyph, and drawing nothing is not that.
    """
    size = font_px * 4
    canvas = Image.new("RGBA", (size, size), _TRANSPARENT)
    ImageDraw.Draw(canvas).text(
        (size // 4, size * 3 // 4),
        text,
        font=face,
        anchor="ls",
        fill=_WHITE,
        stroke_width=stroke_px,
        stroke_fill=_BLACK,
    )
    box = canvas.getchannel("A").getbbox()
    return b"" if box is None else canvas.crop(box).tobytes()


def _notdef_clusters(font_px: int, stroke_px: int) -> list[bytes]:
    """What each packaged face draws for a character it cannot draw.

    Measured on the three packaged faces: **all of them draw a filled box**,
    the Chinese ones at 52x62 and the latin one at 38x52 (54 px, 4 px stroke).
    That is the whole reason "the PNG has ink" was rejected as an acceptance
    criterion -- a caption made entirely of these satisfies it. Ink is blind
    in the other direction too, since a face that had no such glyph would
    answer a missing codepoint with nothing at all and the criterion would
    then read a blank caption as a failure to draw rather than as tofu; but
    that second case is a fact about other fonts, not about these three.
    U+10FFFF is guaranteed to be in no character map.

    The empty answer is therefore **not reachable with today's register** and
    is skipped rather than collected, as a guard for a face that a later
    round adds: a hypothetical no-tofu face contributes no reference, and the
    callers must not compare against `b""` -- every blank character in a
    caption would match it.
    """
    clusters: list[bytes] = []
    for font_key in fonts.REGISTERED_CAPTION_FONTS:
        drawn = _drawn_alone(render._load_face(font_key, font_px), "\U0010ffff", font_px, stroke_px)
        if drawn:
            clusters.append(drawn)
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

    def test_a_character_the_chinese_faces_lack_is_drawn_by_the_latin_one(
        self, tmp_path: Path
    ) -> None:
        """The fallback chain doing its job, on real faces and through the entry.

        The plan books this as T5's main case and until now it was covered
        only on synthetic faces plus, indirectly, by mixed-metric layout. What
        makes it worth a real-face case of its own is that the substitution is
        invisible in the output: the caption has ink, the right size and no
        tofu whichever face drew it, so nothing else here would notice the
        chain silently collapsing to the style's own face.

        U+0131, dotless i, is in the latin face's character map and in neither
        Chinese face's -- one of twelve such codepoints, and among those the
        ones with ink are chosen from. The style names the **Chinese** face,
        so drawing it at all means the chain fell through.

        Drawn on its own so the page carries exactly one blob, which is what
        lets the drawn ink be compared with the latin face's own rendering
        rather than merely asserted to exist.
        """
        assert ord(_LATIN_ONLY_CHARACTER) in fonts.glyph_coverage(_LATIN)
        for font_key in (_CJK_BOLD, _CJK_REGULAR, _RARE_CJK_P1, _RARE_CJK_P2):
            assert ord(_LATIN_ONLY_CHARACTER) not in fonts.glyph_coverage(font_key)

        style = render.CaptionRenderStyle(
            font_key=_CJK_BOLD, font_px=_INK_SAMPLE_PX, stroke_px=0, line_spacing=1.0
        )
        assert render._fallback_chain(style.font_key).face_for(_LATIN_ONLY_CHARACTER) == _LATIN

        destination = render.render_caption(
            _LATIN_ONLY_CHARACTER,
            style,
            frame_width=_ACCEPTANCE_FRAME[0],
            frame_height=_ACCEPTANCE_FRAME[1],
            destination=tmp_path / "caption.png",
        )
        with Image.open(destination) as opened:
            image = opened.convert("RGBA")
        clusters = _glyph_clusters(image)
        assert len(clusters) == 1

        latin = render._load_face(_LATIN, style.font_px)
        assert clusters[0] == _drawn_alone(latin, _LATIN_ONLY_CHARACTER, style.font_px, 0)
        assert clusters[0] != _drawn_alone(latin, "\U0010ffff", style.font_px, 0)
        assert clusters[0] != _drawn_alone(
            render._load_face(_CJK_BOLD, style.font_px), "\U0010ffff", style.font_px, 0
        )

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

    @pytest.mark.parametrize(
        "font_key", [_CJK_BOLD, _CJK_REGULAR, _RARE_CJK_P1, _RARE_CJK_P2]
    )
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

    @pytest.mark.parametrize("font_px", _ZERO_MARGIN_SIZES)
    def test_these_sizes_leave_the_defect_nowhere_to_hide(self, font_px: int) -> None:
        """`aₙ + dₙ - a₀` is zero here, and only here.

        That quantity is the slack: the canvas-height defect shortens the page
        by `a₀ - aₙ`, and what stands between the shortened page and the
        caption's ink is the last line's own descent. At 48 px the sum leaves
        +3 px of room. Swept across 12-200 px on the packaged faces, these two
        sizes are the only ones where it leaves none, which is why the case
        above is parametrised on them.

        Asserted rather than left as a comment, because it is a fact about the
        packaged faces: a face swap or a metrics change moves it, and the
        sizes above would then quietly stop being the boundary they were
        chosen for.
        """
        chinese_ascent, _ = render._load_face(_CJK_BOLD, font_px).getmetrics()
        latin_ascent, latin_descent = render._load_face(_LATIN, font_px).getmetrics()
        assert latin_ascent + latin_descent - chinese_ascent == 0


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

    def test_the_drawn_page_is_not_a_page_of_tofu(self, tmp_path: Path) -> None:
        """Layer three, on the page as it was actually written to disk.

        Ink says nothing: the Chinese face answers a codepoint it does not
        have with a filled box carrying thousands of ink pixels, and every
        downstream check -- dimensions, ffprobe frame counts, "the PNG is not
        empty" -- passes on a caption made entirely of them. So each blob of
        ink on the page is compared against what each packaged face draws for
        a codepoint no face has.

        **This reaches a caption made of tofu and not a caption with tofu in
        it**, and the limit is the clustering rather than the criterion: a
        blob is a run of ink columns, and at this style's 4 px stroke the
        glyphs touch, so 45 drawn characters come back as 12 blobs. A blob of
        several glyphs can never equal a single-glyph reference.

        Measured, by replacing each character of the caption in turn with an
        uncovered codepoint: of the 48 placements, **46 pass here unnoticed**.
        The 2 that are caught are the two where a space sits beside the box
        and leaves it a blob of its own. Dropping the stroke does not fix it
        either -- at stroke 0 the same caption yields 46 blobs for 45
        characters, because a glyph whose ink has a vertical gap splits in
        two.

        The case below is the one that reaches a single bad glyph. This one is
        kept because it is the only check that reads the bytes that were
        written to disk rather than re-deriving what should have been.
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

    def test_no_single_character_of_the_caption_is_drawn_as_tofu(self) -> None:
        """The differential at the granularity a defect actually appears at.

        One wrong glyph in a line of good ones is the likely shape -- a cmap
        that disagrees with the glyph data, or a face added by LE-20 that
        covers less than it claims -- and the page-level check above cannot
        see it, because neighbouring glyphs merge into one blob.

        So the comparison is made per character, through the same chain the
        renderer segments with: for every character, the face that will draw
        it is asked what it draws, and that has to differ from what the same
        face draws for a codepoint it does not have. This is the renderer's
        own decision being checked -- `segment_runs` picks the face, and
        `draw.text` then draws exactly these glyphs with exactly that face --
        rather than a second opinion about it.

        The tab is expanded first and the text split with `str.splitlines()`,
        for the same reason `render_caption` does both: `segment_runs` refuses
        a tab, and doing it differently here would be testing a different
        input from the one the renderer sees.
        """
        style = self._style()
        chain = render._fallback_chain(style.font_key)
        checked = 0
        for line in _ACCEPTANCE_TEXT.splitlines():
            for run in fonts.segment_runs(line.expandtabs(render.CAPTION_TAB_WIDTH), chain):
                face = render._load_face(run.font_key, style.font_px)
                notdef = _drawn_alone(face, "\U0010ffff", style.font_px, style.stroke_px)
                assert notdef, f"{run.font_key} drew nothing for U+10FFFF; the reference is broken"
                for character in run.text:
                    drawn = _drawn_alone(face, character, style.font_px, style.stroke_px)
                    assert drawn != notdef, f"U+{ord(character):04X} came out as tofu"
                    checked += 1
        # Every character of the caption, tab expanded to four columns. Pinned
        # so a segmentation that quietly dropped most of the text would not
        # leave this case passing on whatever survived.
        assert checked == _ACCEPTANCE_CHARACTERS

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
