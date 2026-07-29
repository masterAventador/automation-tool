"""How a caption is drawn: the style, the faces, the layout and the PNG.

`render_caption` is the whole of what a caller needs. It takes the text and
the settings, resolves every character to a face through the chain, wraps and
stacks the lines inside the output frame, and leaves one transparent PNG for
ffmpeg to overlay -- or refuses, and leaves nothing at all.

Two obligations sit on this module rather than on `fonts`, because they are
things the caller of the segmenter must do rather than things the segmenter
can do for it:

* the line splitter is `str.splitlines()` itself. `fonts` derives its
  refused-codepoint set from that function and argues the two are aligned by
  construction; a hand-rolled `\\n` split makes that argument false without
  making any test fail;
* tabs are resolved here. `segment_runs()` refuses `\\t` and
  `str.splitlines()` does not break on it, so splitting into lines does not
  hand the segmenter tab-free text. Nothing in `fonts` will catch that for the
  caller, because at that level a tab is indistinguishable from any other
  character a face cannot draw.
"""

from __future__ import annotations

import math
import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Never

from PIL import Image, ImageDraw, ImageFont

from automation_tool.executor.captions import fonts

# Copied from `control_plane/domain/editing_project.py` rather than imported:
# the Executor is a separate deployment unit and may not depend on a Control
# Plane domain module (CLAUDE.md 4.3). `TestCrossLayerContract` holds the two
# copies together, and its verdict matrix -- not the constant comparison --
# is what pins the stroke floor, which upstream spells as a literal `0`.
MIN_CAPTION_FONT_PX: Final = 12
MAX_CAPTION_FONT_PX: Final = 200
MIN_CAPTION_STROKE_PX: Final = 0
MAX_CAPTION_STROKE_PX: Final = 20
MIN_CAPTION_LINE_SPACING: Final = 1.0
MAX_CAPTION_LINE_SPACING: Final = 3.0

# The frame the caption has to fit inside, copied from `OutputSpec` for the
# same reason as the values above. Its "both sides must be even" rule is
# deliberately not copied: that is the encoder's constraint -- h264 in yuv420p
# halves the chroma planes -- and nothing here encodes anything. Refusing an
# odd frame at this layer would refuse a caption this layer can draw perfectly
# well, which is the opposite of the agreement the bounds above exist to keep.
MIN_OUTPUT_DIMENSION: Final = 128
MAX_OUTPUT_DIMENSION: Final = 4096

# White letterform, black outline, nothing behind it. Not settings: the
# footage underneath is arbitrary, and this is the pair that stays readable on
# all of it. Making it configurable would mean a caption can be configured
# into invisibility, which is the same class of failure as tofu.
CAPTION_FILL_COLOUR: Final = (255, 255, 255, 255)
CAPTION_STROKE_COLOUR: Final = (0, 0, 0, 255)
TRANSPARENT: Final = (0, 0, 0, 0)

# How far the caption stays clear of the frame's left and right edges, and so
# how wide a line may be before it wraps. A fixed value for now: the wrap
# width belongs in `CaptionStyle`, which is the Control Plane's to change, and
# `docs/development/LE-09.md` books that back-fill rather than inventing a
# second place for it to live here. Wider than `MAX_CAPTION_STROKE_PX` on
# purpose -- that is what keeps a wrapped line, stroke included, narrower than
# the frame it will be overlaid on.
CAPTION_SIDE_MARGIN_PX: Final = 48

# Tab stops every four columns rather than the eight `str.expandtabs()`
# defaults to. A caption line is a dozen characters, not a source file, and
# eight columns of a 48 px face is most of a frame.
CAPTION_TAB_WIDTH: Final = 4

# Named because PIL picks the codec off the file extension, and the file this
# writes is a working file with a name it was handed by `mkstemp`. Saving
# without it was measured to answer `ValueError: unknown file extension`.
PNG_FORMAT: Final = "PNG"

# The registry's pattern, aliased rather than copied a third time: a style's
# font key and a resolved font key are the same value, and the whole point of
# checking the shape is that `fonts` can then look it up in a closed set.
FONT_KEY_PATTERN: Final = fonts.FONT_KEY_PATTERN

# Which named instance a variable face is pinned to.
#
# A variable face renders at its axis defaults unless told otherwise, and for
# the one packaged variable face that default is `wght` 100 -- Thin. Measured
# at 48 px, "ABCDEFG" covers 663 pixels there against 2984 at Bold. A caption
# too faint to read is the same class of failure as tofu: there is ink, the
# dimensions are right, every downstream check passes, and the viewer cannot
# read it. Bold rather than Regular because the face a latin fallback most
# often sits beside is `fonts.DEFAULT_CAPTION_FONT_KEY`, itself a Bold face,
# and a chain that changes weight mid-line reads as a rendering fault.
PINNED_WEIGHT_INSTANCE: Final = "Bold"


class CaptionStyleRejected(fonts.CaptionFontRejected):
    """A setting the renderer draws by is outside what it will draw.

    The style's own fields, and the frame they have to fit inside: both arrive
    the same way -- from what the user configured -- and both fail the same
    way, with a caption nobody asked for rather than a missing file.

    Subclasses the package's rejection root so a caller keeps one `except` at
    its boundary (LE-10 is the first), while still separating this cause from
    the two `fonts` already names: the installation is fine and the text is
    fine, the settings are not.
    """


class CaptionOutputUnavailable(fonts.CaptionFontRejected):
    """The caption was drawn and the file could not be written.

    Separate from the three causes above because it is the only one the
    caller can do something about without changing the request: a full disk, a
    directory that is not there, a destination something else is holding.

    The message names none of it: a destination is a local path, and
    CLAUDE.md 7 keeps those out of logs and error responses.

    The original error is kept as `__cause__`, and that is a deliberate trade
    rather than a free one. Measured: `logging.exception()` formats the whole
    chain, and the chain carries the directory, the user's name in it and the
    working file's name. Dropping the cause would take the only description of
    what actually went wrong with it, so it stays -- and the obligation moves
    to the caller, which must log `str(error)` rather than `exc_info`. That is
    written down for LE-10 in `docs/development/LE-09.md`.
    """


def _refuse(detail: str) -> Never:
    raise CaptionStyleRejected(f"caption style rejected: {detail}")


def _validate_whole_number(name: str, value: object, *, minimum: int, maximum: int) -> None:
    """`type(value) is int`, then range -- in that order, and never both at once.

    `type(...) is` rather than `isinstance(...)` because `bool` is a subclass
    of `int`: `isinstance(False, int)` is true, and zero is inside the stroke
    range, so a loose check would accept `stroke_px=False` and carry it all
    the way through. Upstream makes the same choice and the two layers have to
    reach the same verdict.

    The value is only interpolated into the message once it is known to be an
    int. A style is assembled from user settings, so an unvalidated field is
    caller text and copying it into an error copies it into a log
    (CLAUDE.md 7) -- the same reason `fonts._registered_font` checks the
    pattern before it names a key.
    """
    if type(value) is not int:
        _refuse(f"{name} must be an int, not {type(value).__name__}")
    if not minimum <= value <= maximum:
        _refuse(f"{name} {value} is outside [{minimum}, {maximum}]")


@dataclass(frozen=True, slots=True)
class CaptionRenderStyle:
    """How captions are drawn, checked again on this side of the protocol.

    The same values as `CaptionStyle` in the Control Plane, with the same
    bounds and the same strictness about types, because a style can arrive
    here two ways: over the execution protocol, where the Executor is a
    separate deployment unit that has to validate what reaches it
    (CLAUDE.md 4.3), and assembled locally by LE-10, which never passes
    through the Control Plane at all.

    What deliberately differs is the refusal: upstream answers one fixed
    `InvalidEditingProjectModel` because it sits on an API boundary, where
    naming the offending field describes the model to whoever is probing it.
    Here the caller is one process away and inside the product, so the message
    names the field -- otherwise locating a bad setting means bisecting it.

    One guard is deliberately not made here: nothing compares a font size
    against the output frame. That comparison needs the frame, and a style is
    handed no frame; inventing one would mean guessing, so it belongs where
    the two values meet. Which is to say it is made nowhere yet -- the project
    aggregate on the LE-04 line carries such a check on a branch this tree
    does not descend from, and LE-10 assembles a style locally without
    reaching that aggregate at all. `docs/development/LE-09.md` books it to
    T4b, the first step that holds a frame.

    Whether the key names a face that is actually installed is likewise not
    asked here -- `fonts.resolve_font_file` answers that at the point where
    the answer is known, and upstream draws the line in the same place, so
    both layers accept and refuse exactly the same settings values.
    """

    font_key: str
    font_px: int
    stroke_px: int
    line_spacing: float

    def __post_init__(self) -> None:
        if not isinstance(self.font_key, str) or FONT_KEY_PATTERN.fullmatch(self.font_key) is None:
            # Never echoed: until the pattern clears it, the key is caller text.
            _refuse("malformed font key")
        _validate_whole_number(
            "font_px", self.font_px, minimum=MIN_CAPTION_FONT_PX, maximum=MAX_CAPTION_FONT_PX
        )
        _validate_whole_number(
            "stroke_px",
            self.stroke_px,
            minimum=MIN_CAPTION_STROKE_PX,
            maximum=MAX_CAPTION_STROKE_PX,
        )
        if type(self.line_spacing) is not float:
            _refuse(
                "line_spacing must be a float, not "
                f"{type(self.line_spacing).__name__} (1 is not 1.0)"
            )
        if not MIN_CAPTION_LINE_SPACING <= self.line_spacing <= MAX_CAPTION_LINE_SPACING:
            _refuse(
                f"line_spacing {self.line_spacing} is outside "
                f"[{MIN_CAPTION_LINE_SPACING}, {MAX_CAPTION_LINE_SPACING}]"
            )
        # The stroke sits on both sides of the glyph outline, so it eats twice
        # its width out of the letterform.
        if self.stroke_px * 2 >= self.font_px:
            _refuse(f"stroke_px {self.stroke_px} leaves no letterform at font_px {self.font_px}")


def _pin_variable_weight(face: ImageFont.FreeTypeFont, font_key: str) -> ImageFont.FreeTypeFont:
    """Pin a variable face to a legible instance; leave a static face alone.

    Asked by name rather than by axis coordinate, which is the opposite of
    what the plan predicted, because the two APIs fail differently. Measured:
    `set_variation_by_axes([])` and `set_variation_by_axes([700, 700])` are
    both accepted in silence on a one-axis face -- the first leaves the weight
    exactly where it was -- while `set_variation_by_name` raises when the
    instance is not there. A correction that can quietly not happen is worth
    less than one that says so.

    The static case is a refusal from FreeType rather than a flag: a face with
    no `fvar` answers `OSError` to every variation call, which is how both
    packaged Noto faces behave. Reaching the name check at all therefore means
    the face does have axes, so a missing instance is a real gap in the
    packaged asset and is reported as such. Today's closed register cannot
    produce it -- the one variable face carries the instance -- but adding
    faces is exactly LE-20's deliverable, and PIL's own answer there is a bare
    `ValueError` that would surface as neither a font nor a style problem.

    The instance is matched byte for byte, so a face naming its weights
    `"bold"`, `"SemiBold"` or `"Bold Italic"` is refused rather than drawn
    with. That fails closed and says why, which is the right way round, but it
    is a live constraint on LE-20 rather than a theoretical one and is
    recorded as such in that handover.
    """
    try:
        instances = face.get_variation_names()
    except OSError:
        # The one signal that means "no axes here", and read narrowly on
        # purpose: widening it hands back a face at its axis defaults, which
        # for the packaged variable face is Thin -- ink, right dimensions,
        # every downstream check green, and a caption nobody can read. That is
        # what this function exists to prevent, so it must not be reachable
        # from a signal that means something else.
        return face
    except Exception as error:
        raise fonts.CaptionFontUnavailable(
            f"caption font unavailable: {font_key} could not be asked for its named instances"
        ) from error
    if PINNED_WEIGHT_INSTANCE.encode() not in instances:
        raise fonts.CaptionFontUnavailable(
            f"caption font unavailable: {font_key} is a variable face with no "
            f"{PINNED_WEIGHT_INSTANCE} instance"
        )
    try:
        face.set_variation_by_name(PINNED_WEIGHT_INSTANCE)
    except Exception as error:
        # Naming the instance is not the last call into FreeType: PIL re-reads
        # the names and then calls `setvarname`, and it carries a workaround
        # in that method for a FreeType "unknown error" bug, so a refusal here
        # is the library's measured behaviour. It arrives as a bare `OSError`,
        # which is the shape this module converts everywhere else.
        raise fonts.CaptionFontUnavailable(
            f"caption font unavailable: {font_key} could not be pinned to its "
            f"{PINNED_WEIGHT_INSTANCE} instance"
        ) from error
    return face


def _load_face(font_key: str, font_px: int) -> ImageFont.FreeTypeFont:
    """Turn a registered key into a face ready to draw with, or refuse.

    Takes a key and a size rather than a whole style because the fallback
    chain draws with faces the style never names: every face in the chain is
    loaded at the style's size, and only the first of them is `style.font_key`.

    The size is checked first, and against the same bounds the style uses. It
    is not defensive duplication -- it is the only enforcement on this path,
    since the fallback keys arrive from a chain rather than from a style. PIL
    answers a bare `ValueError` for a size of 0 and accepts 3000 without
    comment -- measured, it draws a 3000x2817 mask -- so leaving it unchecked
    here means a caller's mistake surfaces
    either as a non-domain exception out of the renderer or as a caption
    nobody asked for. Checking before the face is resolved keeps the answer
    from depending on whether that face happens to be installed.

    Faces are not memoised. Measured at 0.6-0.7 ms for both packaged formats,
    including the 17 MB CJK face, because FreeType maps the file instead of
    reading it -- there is nothing to buy, unlike `glyph_coverage`, whose 25 ms
    cmap read is paid once per character per face. Sharing them would cost:
    size and variation instance live on the face object, so two callers
    holding one face silently share one set of settings.
    """
    _validate_whole_number(
        "font_px", font_px, minimum=MIN_CAPTION_FONT_PX, maximum=MAX_CAPTION_FONT_PX
    )
    # The path comes from the register, never from the key: `resolve_font_file`
    # matches the pattern, looks the key up in a closed set and joins only the
    # file name it finds there. That is this module's whole reason for taking
    # a key rather than a path -- but choosing the path is only half of it, and
    # the check below is the other half.
    path = fonts.resolve_font_file(font_key)
    try:
        face = ImageFont.truetype(path, font_px)
    except OSError as error:
        # Names the key, which is the setting an operator can act on, and not
        # the path, which is a local filesystem detail (CLAUDE.md 7).
        raise fonts.CaptionFontUnavailable(
            f"caption font unavailable: {font_key} could not be loaded"
        ) from error
    # `ImageFont.truetype` catches its own `OSError` and walks the platform's
    # font directories for a file of the same base name, so the handler above
    # fires only when that search comes up empty: a packaged face FreeType
    # refuses is otherwise answered with whatever the machine has installed.
    # Measured on three ways of breaking the packaged file -- corrupt bytes,
    # mode 000, truncation -- each of which came back as a different family.
    #
    # That is not a cosmetic substitution. `REGISTERED_CAPTION_FONTS` doubles
    # as the rights list, so the replacement is a face with no clearance to be
    # printed into a customer's video, and falling back to a system resource
    # when a packaged one cannot be verified is what CLAUDE.md 5 forbids.
    #
    # Comparing the paths is exact and costs no filesystem call: PIL stores the
    # argument it was constructed with, so a face that came out of the search
    # carries the string it walked to rather than the `Path` asked for here.
    if face.path != path:
        raise fonts.CaptionFontUnavailable(
            f"caption font unavailable: {font_key} did not load from the packaged file"
        )
    return _pin_variable_weight(face, font_key)


def _fallback_chain(font_key: str) -> fonts.FontChain:
    """The style's own face first, then every other face the product ships.

    A style names one face and a caption needs as many as its characters do,
    so somebody has to decide the order. Deciding it here rather than taking a
    chain as an argument keeps that policy in one place: a caller that had to
    supply the chain would be inventing the fallback order in the pipeline,
    which is not where a question about fonts belongs.

    The rest of the register follows in registration order, which is a real
    order rather than an incidental one -- a dict preserves insertion order,
    and the register is written CJK first because that is the script a caption
    is mostly made of. Nothing is filtered out: a face further down the chain
    is only ever consulted for a character the ones before it do not cover.
    """
    others = tuple(key for key in fonts.REGISTERED_CAPTION_FONTS if key != font_key)
    return fonts.FontChain((font_key, *others))


@dataclass(slots=True)
class _Faces:
    """The chain's faces at one size, opened once for the length of a render.

    `_load_face` deliberately does not memoise, because size and variation
    instance live on the face object and two callers holding one face share
    one set of settings. Within a single render there is only one size and one
    instance, which is exactly the case where sharing is safe -- and the case
    where it is worth something, since a line is measured before it is drawn
    and drawn twice after that.
    """

    font_px: int
    _opened: dict[str, ImageFont.FreeTypeFont] = field(default_factory=dict)

    def of(self, font_key: str) -> ImageFont.FreeTypeFont:
        face = self._opened.get(font_key)
        if face is None:
            face = _load_face(font_key, self.font_px)
            self._opened[font_key] = face
        return face


@dataclass(frozen=True, slots=True)
class _LaidOutLine:
    """One drawn line: its runs, how wide they are, and how tall it stands."""

    runs: tuple[fonts.TextRun, ...]
    width: float
    ascent: int
    descent: int


def _wrap(
    lines: Sequence[str], chain: fonts.FontChain, faces: _Faces, wrap_width: int
) -> list[str]:
    """Break each line where it stops fitting, one character at a time.

    Per character rather than per word: a Chinese caption has no spaces to
    break at, and the product's captions are mostly Chinese. The cost is that
    a long latin word can be split across two lines; that is recorded as a
    known limitation rather than solved here, because word-aware breaking is a
    second policy and this task has no measurement to choose one by.

    Measuring by summing per-character advances is exact rather than
    approximate, which is not true of every text stack: measured on all three
    packaged faces, `getlength` of a string equals the sum of `getlength` of
    its characters, kerning pairs like "AV" and "To" included, because Pillow
    is built here without libraqm and lays out horizontally. A face that
    kerned would make the wrap decision disagree with the drawn width by a
    pixel or two, never by enough to overflow the frame -- the canvas is sized
    from the drawn width, not from this one.
    """
    wrapped: list[str] = []
    for line in lines:
        current = ""
        width = 0.0
        for character in line:
            advance = faces.of(chain.face_for(character)).getlength(character)
            if advance > wrap_width:
                # No line break can rescue this one: it would sit alone on a
                # line and still push the PNG wider than the frame it is to be
                # overlaid on, which is a caption running off the screen.
                _refuse(
                    f"font_px {faces.font_px} draws a character {advance:.0f} px wide, "
                    f"wider than the {wrap_width} px a caption line is given"
                )
            if current and width + advance > wrap_width:
                wrapped.append(current)
                current, width = "", 0.0
            current += character
            width += advance
        wrapped.append(current)
    return wrapped


def _lay_out(line: str, chain: fonts.FontChain, faces: _Faces, primary_key: str) -> _LaidOutLine:
    """Segment one line into runs and measure what they need.

    A run is a stretch one face draws; the line's height is the tallest of the
    faces that appear on it, so a latin fallback with different metrics cannot
    crop the CJK face standing next to it.
    """
    runs = fonts.segment_runs(line, chain)
    width = 0.0
    ascents: list[int] = []
    descents: list[int] = []
    for run in runs:
        face = faces.of(run.font_key)
        width += face.getlength(run.text)
        ascent, descent = face.getmetrics()
        ascents.append(ascent)
        descents.append(descent)
    if not runs:
        # A blank line is a line: it draws nothing and still takes its turn,
        # and the height it takes is the one the style's own face would give
        # it. Collapsing it would silently close up a gap the caller asked for.
        ascent, descent = faces.of(primary_key).getmetrics()
        ascents.append(ascent)
        descents.append(descent)
    return _LaidOutLine(runs, width, max(ascents), max(descents))


def _draw_runs(
    draw: ImageDraw.ImageDraw,
    line: _LaidOutLine,
    faces: _Faces,
    left: float,
    baseline: float,
    colour: tuple[int, int, int, int],
    stroke_px: int,
) -> None:
    """Put one line on the page, one run at a time, from a common baseline."""
    x = left
    for run in line.runs:
        face = faces.of(run.font_key)
        draw.text(
            (x, baseline),
            run.text,
            font=face,
            anchor="ls",
            fill=colour,
            stroke_width=stroke_px,
            stroke_fill=colour,
        )
        x += face.getlength(run.text)


def _draw(
    lines: Sequence[_LaidOutLine], faces: _Faces, style: CaptionRenderStyle, frame_height: int
) -> Image.Image:
    """Compose the lines onto a transparent canvas of exactly their size.

    The canvas is the text and nothing else, because where a caption sits in
    the frame is the overlay step's question and it holds the frame anyway.
    What the frame decides here is whether the block fits at all: a caption
    taller than the video is one the viewer will only see part of, and that is
    a silent failure rather than an obvious one.

    The stroke was measured to grow outward -- `textbbox` and the drawn ink
    both expand by exactly `stroke_width` on each of the four sides -- so the
    canvas carries that much margin all round or the outline is clipped.
    """
    stroke = style.stroke_px
    text_width = max(line.width for line in lines)
    # Baseline to baseline: the face's own line box, scaled by the setting.
    steps = [round((line.ascent + line.descent) * style.line_spacing) for line in lines]
    # Measured from the first baseline down, because that is where the drawing
    # below starts: the first line's ascent sets the top, the steps carry each
    # baseline to the next, and the last line's descent sets the bottom. The
    # last line's *ascent* is not part of that sum, and reading it here instead
    # of the first line's is short by the difference between the two -- which
    # is zero whenever both lines use the same face, and is the bottom of the
    # caption silently cut off whenever they do not.
    height = 2 * stroke + lines[0].ascent + sum(steps[:-1]) + lines[-1].descent
    if height > frame_height:
        _refuse(f"the caption needs {height} px, taller than the {frame_height} px frame")

    image = Image.new("RGBA", (math.ceil(text_width) + 2 * stroke, height), TRANSPARENT)
    draw = ImageDraw.Draw(image)
    baseline = float(stroke + lines[0].ascent)
    for line, step in zip(lines, steps, strict=True):
        left = stroke + (text_width - line.width) / 2
        if stroke:
            # Every stroke before any fill. Measured: drawing a run's stroke
            # and fill together lets the next run's stroke paint over the
            # previous run's letterform, and where the face changes mid-line
            # that is where it happens -- "AV" at stroke 20 kept 640 of its
            # 909 fill pixels. The pass is skipped rather than run at width
            # zero, because a zero-width stroke pass paints the letterform in
            # the stroke colour and the fill then blends with it: 73 dark
            # pixels along the edges, measured, and 271 fill pixels lost.
            _draw_runs(draw, line, faces, left, baseline, CAPTION_STROKE_COLOUR, stroke)
        _draw_runs(draw, line, faces, left, baseline, CAPTION_FILL_COLOUR, 0)
        baseline += step

    if image.getchannel("A").getbbox() is None:
        # Every character was covered, laid out and drawn, and the page is
        # still blank -- a caption of spaces, or of zero-width characters. The
        # PNG that would follow overlays cleanly and shows the viewer nothing,
        # which is the failure this package is written against, so it is
        # refused here where it is still detectable.
        raise fonts.CaptionTextRejected(
            "caption text rejected: the caption would put no ink on the page"
        )
    return image


def _write_atomically(image: Image.Image, destination: Path) -> Path:
    """Write beside the destination, then move onto it in one step.

    Measured: when a save runs out of room part way through, PIL leaves the
    bytes that made it -- a 200-byte file that `Image.open` rejects as
    truncated -- and deletes nothing. Writing straight to the destination
    would therefore replace last run's good caption with an unreadable one, or
    leave half a PNG for the overlay step to find. `os.replace` is atomic on
    both target platforms, and the working file is created in the destination's
    own directory so the move never crosses a filesystem.

    The working file is removed on every way out, including the ones that are
    not errors: an interrupt leaves no debris either, and is re-raised rather
    than reported as an output problem, because a render that cannot be
    stopped is a worse fault than one that fails.
    """
    try:
        handle, name = tempfile.mkstemp(dir=destination.parent, prefix=".caption-", suffix=".png")
    except OSError as error:
        raise CaptionOutputUnavailable(
            "caption output unavailable: the destination directory could not be opened"
        ) from error
    working = Path(name)
    try:
        with os.fdopen(handle, "wb") as stream:
            image.save(stream, format=PNG_FORMAT)
        os.replace(working, destination)
    # Every `Exception`, for the reason `glyph_coverage` catches broadly one
    # layer down: what an encoder answers on the way to a file is not
    # enumerable from outside it, and the block holds nothing but the calls
    # that write. `BaseException` is deliberately not converted.
    except Exception as error:
        working.unlink(missing_ok=True)
        raise CaptionOutputUnavailable(
            "caption output unavailable: the caption could not be written"
        ) from error
    except BaseException:
        working.unlink(missing_ok=True)
        raise
    return destination


def render_caption(
    text: str,
    style: CaptionRenderStyle,
    *,
    frame_width: int,
    frame_height: int,
    destination: Path,
) -> Path:
    """Draw one caption into a transparent PNG, or refuse and leave nothing.

    The frame is the video the caption will be overlaid on. It is taken as two
    integers rather than as a project's output spec because the Executor may
    not import the Control Plane's domain (CLAUDE.md 4.3), and keyword-only
    because two adjacent integers of the same type are the easiest pair in the
    world to hand over the wrong way round.

    It is checked here even though upstream checks it too, on the same footing
    as `_load_face` checking a size upstream has already bounded: LE-10
    assembles a style and a frame locally and never passes through the Control
    Plane, so for that path this is not a second opinion, it is the only one.

    Everything about the caption's own geometry is measured from the faces
    rather than assumed: where it wraps, how tall a line is, how far apart the
    baselines sit. What is not decided here is where the caption sits in the
    frame -- that is the overlay step's, which has the frame anyway.
    """
    if type(text) is not str:
        raise fonts.CaptionTextRejected(
            f"caption text rejected: text must be a str, not {type(text).__name__}"
        )
    _validate_whole_number(
        "frame_width", frame_width, minimum=MIN_OUTPUT_DIMENSION, maximum=MAX_OUTPUT_DIMENSION
    )
    _validate_whole_number(
        "frame_height", frame_height, minimum=MIN_OUTPUT_DIMENSION, maximum=MAX_OUTPUT_DIMENSION
    )
    if style.font_px > frame_height:
        # The guard `CaptionRenderStyle` could not make, because a style is
        # handed no frame. Checked before anything is opened, so the answer to
        # a size that cannot work does not depend on whether the face it names
        # happens to be installed.
        _refuse(f"font_px {style.font_px} is taller than the {frame_height} px frame")

    # `str.splitlines()` itself, and tabs expanded per line rather than over
    # the whole text: `str.expandtabs` restarts its column count at `\n` and
    # `\r` only, so a text expanded whole would keep counting across U+0085,
    # U+2028 and U+2029 -- three line boundaries `splitlines` does break at --
    # and put the following line's tab stops in the wrong place.
    lines = [line.expandtabs(CAPTION_TAB_WIDTH) for line in text.splitlines()]
    if not lines:
        raise fonts.CaptionTextRejected("caption text rejected: there is nothing to draw")

    chain = _fallback_chain(style.font_key)
    faces = _Faces(style.font_px)
    wrap_width = frame_width - 2 * CAPTION_SIDE_MARGIN_PX
    laid_out = [
        _lay_out(line, chain, faces, style.font_key)
        for line in _wrap(lines, chain, faces, wrap_width)
    ]
    return _write_atomically(_draw(laid_out, faces, style, frame_height), destination)
