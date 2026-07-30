#!/usr/bin/env python3
"""PC-13 tests: what typefaces a part asks for, and what it actually gets.

The renderer has to answer two questions per part before it can draw a single
character: which `(family, weight)` combinations the part's own CSS requests,
and which packaged font file serves each one. Getting the first wrong means a
face is never declared; getting the second wrong means it is declared against
bytes that do not exist. Both failures look identical from the outside -- the
text silently falls back to whatever the operating system has -- so both are
derived here and re-derived by the gate rather than written down by hand.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "scripts/motion_part_typography.py"


def load_module(path: Path):
    assert path.is_file(), f"{path.relative_to(ROOT)} is missing"
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    # Registered before execution: a dataclass in the module resolves its own
    # annotations through `sys.modules`, and an unregistered module makes that
    # lookup fail with an error that says nothing about the real cause.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_pairs_read_css_declarations_with_their_weight() -> None:
    typography = load_module(MODULE)
    pairs = typography.part_typography(
        """
        .a { font-family: "Archivo Black", sans-serif; font-size: 58px; font-weight: 400; }
        .b { font-family: 'Space Mono', monospace; font-weight: 700; }
        .c { font-family: Inter; }
        """
    )
    # A rule with no `font-weight` is a request for 400: that is the CSS
    # initial value, and the face has to be declared at the weight the browser
    # will actually ask for.
    assert pairs == frozenset(
        {("Archivo Black", 400), ("Space Mono", 700), ("Inter", 400)}
    ), pairs


def test_pairs_survive_html_escaping_and_data_attributes() -> None:
    """Two real parts hide their typeface outside a plain CSS declaration.

    `vfx-liquid-background` writes `&quot;Inter&quot;` inside a style block and
    `morph-text` carries `data-font` attributes that its script assigns to
    `style.fontFamily`. A scanner that only reads `font-family:` misses both,
    and the miss is silent.
    """
    typography = load_module(MODULE)
    escaped = typography.part_typography(
        "<style>.x{font-family:&quot;Inter&quot;,sans-serif;font-weight:600}</style>"
    )
    assert escaped == frozenset({("Inter", 600)}), escaped
    attribute = typography.part_typography(
        "<li data-font=\"'Figtree', sans-serif\" data-color=\"#000\">Do more</li>"
    )
    assert attribute == frozenset({("Figtree", 400)}), attribute


def test_pairs_ignore_generics_and_custom_properties() -> None:
    typography = load_module(MODULE)
    pairs = typography.part_typography(
        ".x{font-family:system-ui,-apple-system,ui-monospace,var(--brand),sans-serif}"
    )
    assert pairs == frozenset(), pairs


def test_every_catalog_part_resolves_against_the_declared_policies() -> None:
    """The whole point of the gate: no part may name a typeface nobody planned for."""
    typography = load_module(MODULE)
    contract = typography.load_typography_contract()
    catalog = typography.scan_catalog_parts()
    assert len(catalog) == 134, len(catalog)
    unknown = typography.families_without_policy(catalog, contract)
    assert unknown == frozenset(), unknown


def test_resolution_reports_a_weight_the_package_cannot_serve() -> None:
    """A declared face pointing at bytes that do not exist is the silent failure.

    Chromium buckets a family's faces by weight before it consults
    `unicode-range`, so a request for 700 that resolves to a 400-only family is
    not a near miss -- the text drops to the host font. The resolver has to say
    so instead of emitting a face that cannot load.
    """
    typography = load_module(MODULE)
    policies = {"Ghost Sans": typography.FamilyPolicy("packaged", None, "", "")}
    packaged = {"Ghost Sans": frozenset({400})}
    faces, unmet = typography.resolve_faces(
        [("Ghost Sans", 400), ("Ghost Sans", 900)],
        policies=policies,
        packaged_weights=packaged,
    )
    assert [face.weight for face in faces] == [400], faces
    assert unmet == (("Ghost Sans", 900),), unmet


def test_substitution_redirects_to_the_replacement_family() -> None:
    typography = load_module(MODULE)
    policies = {
        "Menlo": typography.FamilyPolicy("substituted", "JetBrains Mono", "why", "how"),
        "JetBrains Mono": typography.FamilyPolicy("packaged", None, "", ""),
    }
    packaged = {"JetBrains Mono": frozenset({400, 700})}
    faces, unmet = typography.resolve_faces(
        [("Menlo", 400)], policies=policies, packaged_weights=packaged
    )
    assert unmet == (), unmet
    assert len(faces) == 1
    # The rule is keyed on the name the part wrote, and served by the
    # replacement's bytes -- the part is never edited.
    assert faces[0].css_family == "Menlo"
    assert faces[0].source_family == "JetBrains Mono"


def test_host_policy_declares_nothing_and_is_not_an_unmet_request() -> None:
    """Some names are deliberately left to the operating system.

    Colour emoji is the case: every target OS has a full emoji font, so the
    text renders correctly rather than as tofu, and packaging one costs 1.96 MB
    for a single part. That is a decision, not an oversight, so it is declared
    per family with a reason -- never inferred, and never a wildcard.
    """
    typography = load_module(MODULE)
    policies = {
        "Apple Color Emoji": typography.FamilyPolicy("host", None, "OS emoji", "vendor art style")
    }
    faces, unmet = typography.resolve_faces(
        [("Apple Color Emoji", 400)], policies=policies, packaged_weights={}
    )
    assert faces == (), faces
    assert unmet == (), unmet


def test_generated_css_declares_one_rule_per_pair_plus_the_chinese_face() -> None:
    typography = load_module(MODULE)
    policies = {"Space Mono": typography.FamilyPolicy("packaged", None, "", "")}
    packaged = {"Space Mono": frozenset({400, 700})}
    faces, _ = typography.resolve_faces(
        [("Space Mono", 400), ("Space Mono", 700)],
        policies=policies,
        packaged_weights=packaged,
    )
    css = typography.part_font_css(
        faces,
        chinese_artifact="assets/noto-sans-sc.woff2",
        latin_artifact=lambda face: f"assets/space-mono-{face.weight}.woff2",
    )
    # Two Latin faces, and one Chinese face per (family, weight) -- the measured
    # rule from PC-13 section 3.1: a Chinese face at 400 does not serve a
    # request for 700.
    assert css.count("@font-face") == 4, css
    assert css.count("unicode-range") == 4, css
    assert "font-weight:400" in css and "font-weight:700" in css


def main() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
    print("motion part typography tests passed")
    print(f"executed checks: {len(tests)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
