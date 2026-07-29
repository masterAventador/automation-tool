"""Caption font registry: a closed key set mapped to rights-registered faces."""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Final

from fontTools.ttLib import TTFont

# The bundles carrying the faces the caption renderer draws with. A bundle is
# the unit the rights register packs a face into, and it is also the namespace
# its file name lives in: `packagedName` is unique inside a bundle, not across
# the product.
MATERIAL_VIDEO_WORKER_BUNDLE: Final = "material-video-worker"
MOTION_CATALOG_OVERLAY_BUNDLE: Final = "motion-catalog-overlay"


@dataclass(frozen=True, slots=True)
class RegisteredCaptionFont:
    """One face the caption renderer may draw with.

    Both fields come from a single entry of
    `contracts/quality/asset-rights-policy.v1.json`, which declares
    `defaultDecision: "deny"` and therefore doubles as the packing list. The
    bundle has to travel with the name because the two are only meaningful
    together: the Noto faces are fetched at build time into the material video
    Worker's bundle, while the Big Shoulders face is committed under the
    motion overlay's, so a bare file name does not say where the face lives.
    """

    packaged_name: str
    bundle: str


# font_key -> the cleared face it names. The key arrives from user settings,
# so this map is the whole of what a caller may ask for: a face it does not
# name is a face the product does not ship, and an unregistered key therefore
# never reaches the filesystem.
#
# A read-only mapping rather than a plain dict: `Final` stops the name being
# rebound but not the contents being edited, and a closed set that can be
# added to at runtime is not closed.
REGISTERED_CAPTION_FONTS: Final[Mapping[str, RegisteredCaptionFont]] = MappingProxyType(
    {
        "noto-sans-cjk-sc-bold": RegisteredCaptionFont(
            packaged_name="NotoSansCJKsc-Bold.ttf",
            bundle=MATERIAL_VIDEO_WORKER_BUNDLE,
        ),
        "noto-sans-cjk-sc-regular": RegisteredCaptionFont(
            packaged_name="NotoSansCJKsc-Regular.ttf",
            bundle=MATERIAL_VIDEO_WORKER_BUNDLE,
        ),
        "big-shoulders-display": RegisteredCaptionFont(
            packaged_name="big-shoulders-display-latin.woff2",
            bundle=MOTION_CATALOG_OVERLAY_BUNDLE,
        ),
    }
)

# Noto Sans CJK SC, not one of the latin faces: a latin default renders every
# Chinese caption as boxes while the render still reports success.
DEFAULT_CAPTION_FONT_KEY: Final = "noto-sans-cjk-sc-bold"

# The Control Plane's CaptionStyle validates font keys with this same pattern.
# The Executor is a separate deployment unit and may not import that domain
# (CLAUDE.md 4.3), so the pattern is copied here and pinned by test instead.
#
# `\Z` rather than `$` so the guard does not depend on the calling verb.
# Under `fullmatch` the two are equivalent -- both refuse
# "noto-sans-cjk-sc-bold\n" -- but under `match` a `$` accepts it, because
# `$` also matches just before a trailing newline. `\Z` refuses it either
# way, which is why the domain layer moved to it in e53ef70.
FONT_KEY_PATTERN: Final = re.compile(r"^[a-z][a-z0-9-]{0,63}\Z")

CACHE_DIRECTORY_NAME: Final = "automation-tool-build"
FETCHED_FONT_CACHE_NAME: Final = "subtitle-fonts"
PACKAGED_FONT_DIRECTORY_NAME: Final = "fonts"
BUILD_CACHE_OVERRIDE_VARIABLE: Final = "AUTOMATION_TOOL_BUILD_CACHE"

# Where the motion overlay keeps its committed faces, relative to the
# repository root. Its one entry in the rights register carries a matching
# `path`, and a test holds the two together.
MOTION_CATALOG_OVERLAY_SOURCE_DIRECTORY: Final = "assets/motion-catalog-overlay/fonts"


class CaptionFontRejected(RuntimeError):
    """The package's rejection root: a caption could not be drawn as asked.

    The name is narrower than what it covers. `render.CaptionStyleRejected`
    is a settings problem rather than a font one, and later steps will add
    more; the class is still the right root because what a caller needs is one
    type at its boundary and a subclass to tell the causes apart.
    `TestRefusalHierarchy` enforces that over every exception class the
    package defines, not over a list.
    """


class CaptionFontUnavailable(CaptionFontRejected):
    """A registered face is missing from disk or cannot be located."""


class CaptionTextRejected(CaptionFontRejected):
    """Text arrived in a shape no face could be asked to draw.

    Subclasses the package's rejection root so a caller can still catch one
    type at its boundary, while separating the two causes: a face problem is
    something wrong with the installation, this is something wrong with what
    the caller passed in.
    """


class CaptionGlyphUnavailable(CaptionFontRejected):
    """No face in the chain draws a character, so rendering fails closed.

    The alternative is to draw whatever the face puts at `.notdef`, which for
    Noto Sans CJK SC is a filled box. That box leaves every downstream check
    green -- the PNG has ink, its dimensions are right, ffprobe counts the
    frames -- while the viewer sees tofu. This is the last point at which the
    problem is still detectable, so it is refused here.
    """


def _build_cache_root() -> Path:
    """This machine's project-scoped build cache.

    Same rule as `scripts/video_runtime_cache.py::cache_root`, which cannot be
    imported here because `scripts/` is not part of the frozen package. The
    two are kept in step by hand and point at each other in comment.
    """
    override = os.environ.get(BUILD_CACHE_OVERRIDE_VARIABLE)
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library/Caches" / CACHE_DIRECTORY_NAME
    # `sys.platform` where the script says `os.name == "nt"`. The two agree on
    # every platform this product targets, and `os.name` cannot be faked in a
    # test: pathlib dispatches on it, so setting it to "nt" would make every
    # Path in the process try to become a WindowsPath and fail.
    if sys.platform == "win32":
        # No extra `cache` leaf: the root's own name carries the project scope
        # on every platform, so a stray directory is attributable from its name.
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / CACHE_DIRECTORY_NAME
        return Path.home() / "AppData/Local" / CACHE_DIRECTORY_NAME
    xdg_cache_home = os.environ.get("XDG_CACHE_HOME")
    if xdg_cache_home:
        return Path(xdg_cache_home) / CACHE_DIRECTORY_NAME
    return Path.home() / ".cache" / CACHE_DIRECTORY_NAME


def _repository_root() -> Path:
    """The checkout this module was imported from."""
    return Path(__file__).resolve().parents[5]


# How each bundle is found in a source checkout. The split is not arbitrary:
# in `asset-rights-policy.v1.json` the Big Shoulders entry carries a `path`
# and the two Noto entries do not, and that field is the discriminant. An
# entry with `path` is committed in the tree and is read from there; an entry
# without one is fetched at build time by digest into the build cache and is
# never in the tree at all -- a regression test in
# `scripts/test_material_video_worker.py` keeps it that way.
_SOURCE_BUNDLE_ROOTS: Final[Mapping[str, Callable[[], Path]]] = MappingProxyType(
    {
        MATERIAL_VIDEO_WORKER_BUNDLE: lambda: _build_cache_root() / FETCHED_FONT_CACHE_NAME,
        MOTION_CATALOG_OVERLAY_BUNDLE: lambda: (
            _repository_root() / MOTION_CATALOG_OVERLAY_SOURCE_DIRECTORY
        ),
    }
)


def _packaged_bundle_directory(bundle: str) -> PurePosixPath:
    """The bundle's directory inside the package, relative to its root."""
    return PurePosixPath(PACKAGED_FONT_DIRECTORY_NAME) / bundle


def packaged_relative_path(font_key: str) -> PurePosixPath:
    """Where a registered face is expected to sit inside the Executor package.

    This is the handover to LE-20, which owns assembly: its factory gate can
    walk `REGISTERED_CAPTION_FONTS`, ask this for each key and assert the file
    is present under the package root, without needing to know what a bundle
    is or how the layout is composed. Re-deriving the layout there would put
    the same fact in two places, which is the fault the registry itself was
    already corrected for.

    Nothing populates this location yet: the Executor spec does not ship any
    face today, so a frozen build currently resolves to a missing file and
    fails closed. Wiring that up, and the gate that refuses a package without
    them, is LE-20's deliverable.
    """
    registered = _registered_font(font_key)
    return _packaged_bundle_directory(registered.bundle) / registered.packaged_name


def bundle_root(bundle: str) -> Path:
    """The directory holding one bundle's faces, for either way of running.

    Both modes look a face up the same way -- bundle, then root, then file
    name -- and only the root differs. Keeping the shape identical is what
    makes the difference a configuration value rather than a second code path;
    a build-time switch on where the product looks for its files is the shape
    that has already cost this project a release.
    """
    frozen = getattr(sys, "_MEIPASS", None)
    if isinstance(frozen, str):
        return Path(frozen) / _packaged_bundle_directory(bundle)
    locate = _SOURCE_BUNDLE_ROOTS.get(bundle)
    if locate is None:
        raise CaptionFontUnavailable(
            f"caption font unavailable: no source location for bundle {bundle}"
        )
    return locate()


def _registered_font(font_key: str) -> RegisteredCaptionFont:
    """Look a key up in the closed register, refusing anything else.

    The key arrives from user settings, so it is matched against the pattern
    and then looked up: the file name that reaches the filesystem always comes
    from the register, never from the caller.

    The pattern runs first for a second reason. The unregistered-key message
    below names the key so an operator can tell which setting is wrong, and
    that is only safe once the key is known to be `[a-z][a-z0-9-]{0,63}` --
    otherwise arbitrary caller text would be copied into a log, which
    CLAUDE.md 7 forbids.
    """
    if not isinstance(font_key, str) or FONT_KEY_PATTERN.fullmatch(font_key) is None:
        raise CaptionFontRejected("caption font rejected: malformed font key")
    registered = REGISTERED_CAPTION_FONTS.get(font_key)
    if registered is None:
        raise CaptionFontRejected(f"caption font rejected: unregistered key {font_key}")
    return registered


def resolve_font_file(font_key: str) -> Path:
    """Map a registered key to the face's file, never joining caller input."""
    registered = _registered_font(font_key)
    path = bundle_root(registered.bundle) / registered.packaged_name
    if not path.is_file():
        raise CaptionFontUnavailable(
            f"caption font unavailable: {font_key} is not in the packaged font directory"
        )
    return path


# One entry per registered face is enough: a chain is a handful of faces and
# the register is closed, so nothing benefits from a larger table.
COVERAGE_CACHE_SIZE: Final = len(REGISTERED_CAPTION_FONTS)

# Control characters and the separators that end a line. No face maps any of
# them, so without naming them here a line break comes back as a missing glyph
# and blames the fonts for a caller that had not split its text into lines.
#
# The set has to contain every codepoint `str.splitlines()` treats as a line
# boundary, because that is what the layout step splits on: anything it breaks
# at but this does not refuse could still reach a face. Three of those sit
# outside the C0 range -- U+0085, U+2028, U+2029 -- and none of the packaged
# faces covers them. `test_the_refused_set_covers_every_line_boundary` derives
# the boundary set from `str.splitlines()` itself and checks the containment,
# so the two cannot drift apart.
#
# Which printable codepoints the packaged faces do and do not cover is a
# separate question, and one this module makes no claim about; the real-face
# tests settle it.
_C0_CONTROLS: Final = frozenset(range(0x00, 0x20))
_DELETE: Final = frozenset({0x7F})
_C1_CONTROLS: Final = frozenset(range(0x80, 0xA0))
_UNICODE_LINE_SEPARATORS: Final = frozenset({0x2028, 0x2029})
UNDRAWABLE_CODEPOINTS: Final[frozenset[int]] = (
    _C0_CONTROLS | _DELETE | _C1_CONTROLS | _UNICODE_LINE_SEPARATORS
)


@lru_cache(maxsize=COVERAGE_CACHE_SIZE)
def glyph_coverage(font_key: str) -> frozenset[int]:
    """The codepoints a face draws, taken from its character map.

    Coverage is cmap membership and nothing more. It deliberately does not
    look at whether the glyph paints anything: a space is mapped to a glyph
    with no contours, and judging by ink would report every space as missing
    and push it onto the fallback chain.

    Nor is there a filter for entries pointing at `.notdef`. Such an entry
    cannot exist in a file -- in cmap format 4 "maps to glyph 0" and "not
    mapped" are the same bytes -- and `TestFontToolsNotdefAssumption` pins
    that rather than defending against it with a branch no test could reach.

    Memoised because reading a real face's cmap costs about 25 ms, and this is
    consulted once per character per face.
    """
    path = resolve_font_file(font_key)
    try:
        with TTFont(str(path), lazy=True) as face:
            character_map = face.getBestCmap()
    # Every `Exception`, not the causes that were known when this was written:
    #
    #   TTLibError    not an sfnt, or a damaged one
    #   KeyError      parses, but carries no cmap table
    #   OSError       unreadable, or removed since it was resolved
    #   ImportError   a WOFF2 face with no brotli to decompress it
    #
    # A truncated face -- the ordinary product of a partial download or an
    # interrupted build, and so the failure LE-20's assembly step can actually
    # produce -- escaped all four. It reaches `SFNTDirectoryEntry.loadData`,
    # whose length check is a bare `assert`, and arrives as `AssertionError`;
    # cut further back it arrives as `struct.error` out of `sstruct.unpack`.
    # What a font parser raises on malformed input is not enumerable from the
    # outside, so the honest shape is to catch broadly and answer in this
    # module's own type. The block is two third-party calls and nothing else,
    # which is what keeps the width from swallowing a fault of our own;
    # `BaseException` is deliberately not caught, so an interrupt still gets
    # out.
    except Exception as error:
        raise CaptionFontUnavailable(
            f"caption font unavailable: {font_key} could not be read"
        ) from error
    # `getBestCmap` answers None when the face has a cmap table but no Unicode
    # subtable in it -- a symbol face, or a Mac Roman-only one. That is a
    # stated outcome rather than an exception, so it needs its own check;
    # iterating it would raise TypeError from inside here instead.
    if character_map is None:
        raise CaptionFontUnavailable(
            f"caption font unavailable: {font_key} has no Unicode character map"
        )
    return frozenset(character_map)


@dataclass(frozen=True, slots=True)
class FontChain:
    """Faces in preference order; the first that covers a character draws it."""

    keys: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.keys:
            raise CaptionFontRejected("caption font chain rejected: the chain is empty")
        if len(set(self.keys)) != len(self.keys):
            raise CaptionFontRejected("caption font chain rejected: a face is repeated")
        # Validate every key up front. Checking lazily would let a chain build
        # cleanly and then fail on whichever character first needed the bad
        # face, which points the blame at the caption rather than the setting.
        for font_key in self.keys:
            _registered_font(font_key)

    def face_for(self, character: str) -> str:
        """The first face covering this character, or refuse."""
        codepoint = ord(character)
        if codepoint in UNDRAWABLE_CODEPOINTS:
            raise CaptionTextRejected(
                f"caption text rejected: U+{codepoint:04X} is a control character, "
                "not something a face draws; line breaks and tabs have to be "
                "resolved by layout before text is segmented"
            )
        for font_key in self.keys:
            if codepoint in glyph_coverage(font_key):
                return font_key
        # The codepoint, never the character: caption text is user content and
        # must stay out of logs (CLAUDE.md 7). A codepoint is still enough for
        # a caller to say exactly which character it could not draw.
        raise CaptionGlyphUnavailable(
            f"caption glyph unavailable: no packaged face draws U+{codepoint:04X}"
        )


@dataclass(frozen=True, slots=True)
class TextRun:
    """A maximal stretch of text drawn by a single face."""

    font_key: str
    text: str


def segment_runs(text: str, chain: FontChain) -> tuple[TextRun, ...]:
    """Split one line into per-face runs, merging neighbours that share a face.

    One line: the caller resolves line breaks and tabs first, and a control
    character reaching here is refused rather than dropped. Dropping would
    turn "a\\nb" into "ab" -- a wrong caption that still renders, still sizes
    correctly and still passes every check downstream of it.
    """
    runs: list[TextRun] = []
    for character in text:
        font_key = chain.face_for(character)
        if runs and runs[-1].font_key == font_key:
            runs[-1] = TextRun(font_key, runs[-1].text + character)
        else:
            runs.append(TextRun(font_key, character))
    return tuple(runs)
