"""How a caption is drawn: the style it is drawn with, and the face it uses.

The layout and PNG output that consume these arrive with T4b. Two obligations
this module hands to that step, both of them things the caller must do rather
than things the font layer can do for it:

* the line splitter has to be `str.splitlines()` itself. `fonts` derives its
  refused-codepoint set from that function and argues the two are aligned by
  construction; a hand-rolled `\\n` split makes that argument false without
  making any test fail;
* tabs are T4b's own problem. `segment_runs()` refuses `\\t` and
  `str.splitlines()` does not break on it, so splitting into lines does not
  hand the segmenter tab-free text -- one tab in a caption fails the whole
  render closed. Nothing here will catch that for the caller, because at this
  level a tab is indistinguishable from any other character a face cannot draw.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Never

from PIL import ImageFont

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
    """A caption style value is outside what the renderer will draw.

    Subclasses the package's rejection root so a caller keeps one `except` at
    its boundary (LE-10 is the first), while still separating this cause from
    the two `fonts` already names: the installation is fine and the text is
    fine, the settings are not.
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
