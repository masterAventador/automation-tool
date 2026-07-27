"""Which typefaces a part asks for, answered with bytes we are allowed to ship.

A part in the release tree names its typefaces in its own CSS and is read-only,
so the renderer cannot edit the request — it can only answer it. Answering means
declaring, for each `(family, weight)` the part writes down, an `@font-face`
under that exact name.

This module is the *emission* half of PC-13. It lives in the Executor package
rather than beside the gate that discovered these facts, because emission runs
on the user's machine: `automation-tool-executor.spec` builds the frozen package
from the import graph rooted at `executor/__main__.py` with
`pathex=[backend/src]`, so nothing under `scripts/` reaches the artifact at all.
An emitter imported from there would work in every repository test and be absent
from the package a customer installs. `scripts/motion_part_typography.py` keeps
the scanning, the contract and the policies — all developer-machine work — and
imports these rules from here, the same arrangement `part_document.py` uses for
the text-node enumerator.

Three measured facts from PC-13 shape the rules themselves:

* **Weight is part of the key.** Chromium buckets a family's faces by weight
  before it consults `unicode-range`, so a Chinese face declared at 400 is never
  consulted for an element asking for 700 and that text drops to the host font.
* **A missing weight is silent.** Nothing reports it; the page renders.
* **The requested name is what gets declared.** `SF Pro` and `Menlo` cannot be
  redistributed, so a stand-in carries their bytes — but under the name the part
  wrote, because the part cannot be edited.

(The docstrings here stay in English for the reason `part_document.py` gives:
`check_user_facing_branding.py` reads Chinese-bearing literals in a `.py` source
as operator copy.)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Final, Sequence

# The code points a Chinese face must own. Latin, Latin-1 punctuation and the
# middle dot are deliberately absent: those stay with the part's own typeface,
# which is the whole point of the split.
CJK_RANGES: Final[tuple[tuple[int, int], ...]] = (
    (0x2E80, 0x2EFF),  # CJK radicals supplement
    (0x2F00, 0x2FDF),  # Kangxi radicals
    (0x3000, 0x303F),  # CJK symbols and punctuation
    (0x3040, 0x30FF),  # Hiragana and Katakana (present in the SC fonts)
    (0x3100, 0x312F),  # Bopomofo
    (0x31C0, 0x31EF),  # CJK strokes
    (0x3200, 0x33FF),  # Enclosed CJK letters, CJK compatibility
    (0x3400, 0x4DBF),  # CJK unified ideographs extension A
    (0x4E00, 0x9FFF),  # CJK unified ideographs
    (0xF900, 0xFAFF),  # CJK compatibility ideographs
    (0xFE10, 0xFE1F),  # Vertical forms
    (0xFE30, 0xFE4F),  # CJK compatibility forms
    (0xFF00, 0xFFEF),  # Halfwidth and fullwidth forms
)

CHINESE_UNICODE_RANGE: Final = ", ".join(
    f"U+{start:04X}-{end:04X}" for start, end in CJK_RANGES
)

# Everything the part's own typeface keeps. Copied from the range Google Fonts
# uses for its `latin` + `latin-ext` subsets, which is what the packaged woff2
# files actually contain.
LATIN_UNICODE_RANGE: Final = (
    "U+0000-00FF, U+0100-02BA, U+02BD-02C5, U+02C7-02CC, U+02CE-02D7, U+02DD-02FF, "
    "U+0304, U+0308, U+0329, U+1D00-1DBF, U+1E00-1E9F, U+1EF2-1EFF, U+2000-206F, "
    "U+20A0-20C0, U+2113, U+2122, U+2191, U+2193, U+2212, U+2215, U+2C60-2C7F, "
    "U+A720-A7FF, U+FEFF, U+FFFD"
)


@dataclass(frozen=True, slots=True)
class ResolvedFace:
    """One `@font-face` rule: the requested name, served by shippable bytes."""

    css_family: str
    source_family: str
    weight: int


def cjk_codepoints() -> frozenset[int]:
    """Every code point the Chinese face claims, as one set."""
    return frozenset(code for start, end in CJK_RANGES for code in range(start, end + 1))


def part_font_css(
    faces: Sequence[ResolvedFace],
    *,
    chinese_artifact: str,
    latin_artifact: Callable[[ResolvedFace], str],
) -> str:
    """The `<style>` block injected into a part's render-time working copy.

    Two rules per requested pair, split by `unicode-range`: the part's own
    Latin typeface keeps the Latin code points, and the Chinese face takes the
    Han ones. Both rules carry the *requested* weight rather than a range,
    because PC-13 measured that Chromium picks the weight bucket first and
    never falls back to a lighter face inside the same family.
    """
    blocks: list[str] = []
    for face in faces:
        blocks.append(
            f"@font-face{{font-family:'{face.css_family}';font-style:normal;"
            f"font-weight:{face.weight};font-display:block;"
            f"src:url({latin_artifact(face)}) format('woff2');"
            f"unicode-range:{LATIN_UNICODE_RANGE};}}"
        )
        blocks.append(
            f"@font-face{{font-family:'{face.css_family}';font-style:normal;"
            f"font-weight:{face.weight};font-display:block;"
            f"src:url({chinese_artifact}) format('woff2');"
            f"unicode-range:{CHINESE_UNICODE_RANGE};}}"
        )
    return "\n".join(blocks)


__all__ = [
    "CHINESE_UNICODE_RANGE",
    "CJK_RANGES",
    "LATIN_UNICODE_RANGE",
    "ResolvedFace",
    "cjk_codepoints",
    "part_font_css",
]
