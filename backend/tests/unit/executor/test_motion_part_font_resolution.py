"""Resolving a part's typeface requests at render time, inside the package.

`part_font_css` only emits rules; something has to decide *which* rules. That
decision reads the part document itself — the document is right there at render
time, being copied into the workspace — and answers each `(family, weight)` it
names against the policies PC-13 froze and the weights the offline package
actually carries.

The whole chain has to live in the Executor package for the reason
`test_motion_part_font_rules.py` states: `scripts/` is not in the frozen
artifact. A scanner that stayed behind would leave the packaged renderer able
to emit rules and unable to work out what to emit.

Why an unmet request is a hard failure here
--------------------------------------------
PC-13 measured that a missing face is silent: the page renders, and the text
appears in whatever the host machine has. `check_motion_part_typography.py`
already proves no part has an unmet request, so meeting one at render time means
the document and the contract disagree — the installation is not the one the
gate checked. Continuing would produce a film that looks subtly wrong on one
machine and fine on another, which is the class of drift BM-16 exists to remove.
"""

from __future__ import annotations

import pytest

from automation_tool.executor.motion_authoring.part_typography import (
    FamilyPolicy,
    FontRequestUnmet,
    document_font_css,
    packaged_weights,
    requested_faces,
    resolve_faces,
)

LOCK = {
    "stylesheets": [
        {
            "faces": [
                {
                    "family": "Inter",
                    "weight": "400",
                    "artifactPath": "fonts/inter-400.woff2",
                    "unicodeRange": "U+0000-00FF",
                },
                {
                    "family": "Inter",
                    "weight": "700",
                    "artifactPath": "fonts/inter-700.woff2",
                    "unicodeRange": "U+0000-00FF",
                },
            ]
        }
    ]
}

CHINESE_ARTIFACT = "fonts/noto-sans-sc.woff2"


def contract(families: list[dict[str, object]]) -> dict[str, object]:
    return {"chineseFace": {"artifactPath": CHINESE_ARTIFACT}, "families": families}


def test_the_scanner_reads_a_font_stack_written_as_entities() -> None:
    """One upstream part writes `&quot;Inter&quot;` inside a style block.

    Scanning the raw bytes reads the entity as part of the family name, so the
    request is missed and the text silently falls back.
    """
    pairs = requested_faces("<style>.a{font-family:&quot;Inter&quot;;font-weight:700}</style>")
    assert ("Inter", 700) in pairs


def test_a_declaration_without_a_weight_is_the_default_weight() -> None:
    pairs = requested_faces("<style>.a{font-family:'Inter'}</style>")
    assert pairs == frozenset({("Inter", 400)})


def test_each_requested_pair_becomes_a_latin_rule_and_a_chinese_one() -> None:
    css = document_font_css(
        "<style>.a{font-family:'Inter';font-weight:400}"
        ".b{font-family:'Inter';font-weight:700}</style>",
        typography_contract=contract(
            [{"family": "Inter", "policy": "packaged", "replacement": None}]
        ),
        offline_lock=LOCK,
    )

    assert css.count("@font-face") == 4
    assert "fonts/inter-400.woff2" in css
    assert "fonts/inter-700.woff2" in css
    assert css.count(CHINESE_ARTIFACT) == 2


def test_each_latin_subset_keeps_its_locked_unicode_range() -> None:
    """ASCII and Latin Extended must both use the packaged family."""
    lock = {
        "stylesheets": [
            {
                "faces": [
                    {
                        "family": "Inter",
                        "weight": "400",
                        "subset": "latin-ext",
                        "artifactPath": "fonts/inter-latin-ext.woff2",
                        "unicodeRange": "U+0100-02BA",
                    },
                    {
                        "family": "Inter",
                        "weight": "400",
                        "subset": "latin",
                        "artifactPath": "fonts/inter-latin.woff2",
                        "unicodeRange": "U+0000-00FF",
                    },
                ]
            }
        ]
    }
    css = document_font_css(
        "<style>.a{font-family:'Inter';font-weight:400}</style>",
        typography_contract=contract(
            [{"family": "Inter", "policy": "packaged", "replacement": None}]
        ),
        offline_lock=lock,
    )

    assert "fonts/inter-latin.woff2" in css
    assert "fonts/inter-latin-ext.woff2" in css
    assert css.count("unicode-range:U+0000-00FF;") == 1
    assert css.count("unicode-range:U+0100-02BA;") == 1


def test_a_substituted_family_keeps_the_name_the_part_wrote() -> None:
    """`SF Pro` cannot be redistributed; the part still asks for it by name."""
    css = document_font_css(
        "<style>.a{font-family:'SF Pro';font-weight:400}</style>",
        typography_contract=contract(
            [{"family": "SF Pro", "policy": "substituted", "replacement": "Inter"}]
        ),
        offline_lock=LOCK,
    )

    assert "font-family:'SF Pro'" in css
    assert "fonts/inter-400.woff2" in css


def test_a_host_policy_family_gets_no_rule_at_all() -> None:
    """Some families are deliberately left to the host — a generic, or emoji."""
    css = document_font_css(
        "<style>.a{font-family:'Apple Color Emoji';font-weight:400}</style>",
        typography_contract=contract(
            [{"family": "Apple Color Emoji", "policy": "host", "replacement": None}]
        ),
        offline_lock=LOCK,
    )

    assert css == ""


def test_a_request_the_package_cannot_serve_fails_closed() -> None:
    """Weight 900 is not in the lock; declaring nothing would be silent."""
    with pytest.raises(FontRequestUnmet):
        document_font_css(
            "<style>.a{font-family:'Inter';font-weight:900}</style>",
            typography_contract=contract(
                [{"family": "Inter", "policy": "packaged", "replacement": None}]
            ),
            offline_lock=LOCK,
        )


def test_a_family_nobody_decided_about_fails_closed() -> None:
    with pytest.raises(FontRequestUnmet):
        document_font_css(
            "<style>.a{font-family:'Comic Sans MS';font-weight:400}</style>",
            typography_contract=contract([]),
            offline_lock=LOCK,
        )


def test_resolve_reports_unmet_rather_than_dropping_them() -> None:
    faces, unmet = resolve_faces(
        [("Inter", 400), ("Inter", 900)],
        policies={"Inter": FamilyPolicy("packaged", None, "", "")},
        packaged_weights=packaged_weights(LOCK),
    )

    assert [face.weight for face in faces] == [400]
    assert unmet == (("Inter", 900),)


def test_the_rules_can_be_built_from_the_packaged_contracts_alone() -> None:
    """At render time nobody is there to hand the renderer its own contracts.

    The Executor reads them from where they ship, through the same resolution
    the authoring agent already uses — `sys._MEIPASS` when frozen, the
    repository from a checkout. Requiring a caller to pass them would mean the
    only code that could render is code that knows where the package put its
    own files, which is the build-time switch this project already paid for.
    """
    # Emoji are the only families PC-13 left to the host, so this asks the real
    # contract a question whose answer is fixed: no rule, and no refusal.
    assert document_font_css("<style>.a{font-family:'Apple Color Emoji'}</style>") == ""

    # And a real packaged family produces real rules pointing at real files.
    css = document_font_css("<style>.a{font-family:'Anton';font-weight:400}</style>")
    assert css.count("@font-face") == 3
    assert "U+0100-02BA" in css
    assert "noto-sans-sc-variable-full.woff2" in css


def test_a_family_the_shipped_contract_never_heard_of_still_fails_closed() -> None:
    with pytest.raises(FontRequestUnmet):
        document_font_css("<style>.a{font-family:'Comic Sans MS'}</style>")


def test_the_gate_script_keeps_no_second_scanner() -> None:
    from pathlib import Path

    repository_root = Path(__file__).resolve().parents[4]
    script = repository_root / "scripts" / "motion_part_typography.py"
    if not script.is_file():
        pytest.skip("the gate script is not present in this checkout")
    source = script.read_text(encoding="utf-8")
    assert "def resolve_faces(" not in source
    assert "def packaged_weights(" not in source


def test_the_rules_point_at_where_the_document_can_reach_them() -> None:
    """The contract records artifacts from the catalog root; the document is not there.

    A part document sits at `items/<name>/`, so an unprefixed `url(...)` in the
    injected rules resolves two levels above the file it should. The browser
    reports that by drawing in the host font — the exact silence PC-13 removes.
    Measured on the real tree: every one of the 18 first-batch parts produced
    rules that resolved to nothing before this argument existed.
    """
    css = document_font_css(
        "<style>.a{font-family:'Anton';font-weight:400}</style>",
        artifact_prefix="../../",
    )

    assert "url(../../offline-deps/" in css
    assert "url(offline-deps/" not in css
