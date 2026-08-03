"""The `@font-face` rules a part's working copy carries, emitted where they ship.

PC-13 built this rule generator in `scripts/motion_part_typography.py`, next to
the gate that scans the submodule for typeface requests. That is the right home
for the *scanning*, which only ever runs on a developer machine — but not for
the *emission*, which has to run on the user's machine, inside the frozen
Executor.

`backend/automation-tool-executor.spec` sets `pathex=[backend/src]` and the
package is whatever the import graph from `executor/__main__.py` reaches, so
nothing under `scripts/` is in the built artifact at all. A renderer importing
the emitter from there would work in the repository checkout and be missing
from the package the customer installs — the failure mode CLAUDE.md §9.2 names
and T32 already paid for once.

So the emitter lives here and the gate imports it, exactly the arrangement
`part_document.py` documents for the text-node enumerator: the code that freezes
a fact and the code that acts on it have to be one implementation, or they are
two implementations waiting for their first disagreement.
"""

from __future__ import annotations

import pytest

from automation_tool.executor.motion_authoring.part_typography import (
    CHINESE_UNICODE_RANGE,
    LATIN_UNICODE_RANGE,
    ResolvedFace,
    part_font_css,
)


def test_every_requested_pair_gets_a_latin_rule_and_a_chinese_one() -> None:
    """The measured PC-13 rule: a Chinese face at 400 never serves a 700 request.

    Chromium buckets a family's faces by weight before it consults
    `unicode-range`, so the unit of work is the (family, weight) pair. Declaring
    one Chinese face for the family drops every element asking for another
    weight back to the host font, silently.
    """
    faces = (
        ResolvedFace(css_family="Space Mono", source_family="Space Mono", weight=400),
        ResolvedFace(css_family="Space Mono", source_family="Space Mono", weight=700),
    )
    css = part_font_css(
        faces,
        chinese_artifact="assets/noto-sans-sc.woff2",
        latin_artifact=lambda face: f"assets/space-mono-{face.weight}.woff2",
    )

    assert css.count("@font-face") == 4
    assert css.count("unicode-range") == 4
    assert "font-weight:400" in css
    assert "font-weight:700" in css


def test_the_part_keeps_the_family_name_it_wrote() -> None:
    """A substituted typeface is declared under the name the part asked for.

    `SF Pro` and `Menlo` cannot be redistributed, so their bytes are a stand-in
    — but the part is read-only and still writes `font-family: 'SF Pro'`. The
    rule has to answer that name or the substitution does not take effect.
    """
    faces = (ResolvedFace(css_family="SF Pro", source_family="Inter", weight=400),)
    css = part_font_css(
        faces,
        chinese_artifact="assets/noto-sans-sc.woff2",
        latin_artifact=lambda face: f"assets/{face.source_family}.woff2",
    )

    assert "font-family:'SF Pro'" in css
    assert "assets/Inter.woff2" in css


def test_the_chinese_range_actually_covers_han_characters() -> None:
    """Guards the constant itself, not just the shape of the rule around it.

    A range that parses but omits the CJK block would render every rule valid
    and every Chinese glyph in the host font — the exact failure PC-13 exists to
    remove, and one that no structural assertion about rule counts can see.
    """
    assert "U+4E00-9FFF" in CHINESE_UNICODE_RANGE.upper().replace(" ", "")
    # The Latin range must not claim the Han block, or the part's own Latin
    # face would win the bucket and the Chinese face would never be consulted.
    assert "4E00" not in LATIN_UNICODE_RANGE.upper()


def test_no_faces_means_no_rules() -> None:
    assert part_font_css((), chinese_artifact="a.woff2", latin_artifact=lambda _: "b") == ""


def test_the_gate_script_and_the_renderer_share_one_implementation() -> None:
    """`scripts/motion_part_typography.py` must not keep a second copy.

    Two emitters that agree today are a drift waiting to happen, and the drift
    shows up as characters silently rendering in the host font.
    """
    source = _gate_script_source()
    # No second emitter, and no second copy of the range literal behind it.
    assert "def part_font_css(" not in source
    assert "U+0000-00FF" not in source
    assert "0x4E00" not in source
    assert "automation_tool.executor.motion_authoring" in source


def _gate_script_source() -> str:
    from pathlib import Path

    repository_root = Path(__file__).resolve().parents[4]
    script = repository_root / "scripts" / "motion_part_typography.py"
    if not script.is_file():
        pytest.skip("the gate script is not present in this checkout")
    return script.read_text(encoding="utf-8")
