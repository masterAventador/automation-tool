"""Writing one catalog part's working copy: copy substituted, typefaces answered.

A part ships in the read-only release tree. Rendering a film with it means
copying that document into the RenderJob workspace and changing two things in
the copy: the frozen slots get this film's copy, and the typefaces the part
names get `@font-face` rules backed by packaged bytes. PC-03 and PC-13 land in
the same write on purpose — they are two edits to one document, and splitting
them would mean opening the file twice and inventing a second place where the
release tree could be mutated by accident.

Why the slot anchor has to be verified rather than trusted
----------------------------------------------------------
A slot says "the 15th text run currently reads `人物创作平台`". If the document
underneath ever stops saying that, substituting positionally would put this
film's copy into some *other* run — and nothing downstream can see that. There
is no rendering error, no gate failure, just a video with the wrong words in the
wrong place. So a mismatch is a hard stop here, where it is still cheap.

The enumeration is `part_document.enumerate_text_nodes`, the same code the
freezing gate counted with (`docs/development/PC-03.md` §6). These tests state
that dependency explicitly: an index addresses a run in the dense enumeration
that includes `<script>` and `<style>`, not the nth *visible* run.
"""

from __future__ import annotations

import pytest

from automation_tool.executor.motion_authoring.part_workspace import (
    PartSlot,
    SlotAnchorRejected,
    render_part_working_copy,
)

# Two runs read "Maya Chen": one is the slot, one is a caption that must not
# move. A substitution that searched by text instead of by index would change
# both, and the test would be green for the wrong reason if only one existed.
#
# The `lt-tag` run is indented on purpose. The freezing gate stores
# `node.text.strip()` as the anchor, and measured against the real release tree
# 3 of the 48 frozen slots sit in runs that carry surrounding whitespace. A
# verbatim anchor comparison would refuse those three, and replacing the whole
# raw run would swallow the document's own indentation.
PART_HTML = """<!doctype html>
<html>
<head><style>.lt-name{font-size:58px}</style></head>
<body>
<div class="lt-name">Maya Chen</div>
<div class="lt-tag">
  Host · Neuroscientist
</div>
<figcaption>Maya Chen</figcaption>
<script>var WORDS = ["Maya Chen"];</script>
</body>
</html>
"""

FONT_CSS = "@font-face{font-family:'Archivo Black';font-weight:400;}"


def slot_index_of(html: str, text: str) -> int:
    """The index the freezing gate would have written down for this copy.

    Matching on the stripped run is what `check_motion_part_slots.py` does when
    it freezes an anchor, so the test addresses slots the same way the contract
    does.
    """
    from automation_tool.executor.motion_authoring.part_document import (
        enumerate_text_nodes,
    )

    matches = [
        node.index for node in enumerate_text_nodes(html) if node.text.strip() == text
    ]
    return matches[0]


def test_copy_lands_in_the_run_the_table_froze_and_nowhere_else() -> None:
    index = slot_index_of(PART_HTML, "Maya Chen")
    result = render_part_working_copy(
        PART_HTML,
        slots=(PartSlot(index=index, original="Maya Chen", parent_tag="div"),),
        copy={index: "张三"},
        font_css=FONT_CSS,
    )

    assert "张三" in result
    # The caption and the script array still say what they said.
    assert result.count("Maya Chen") == 2
    assert '<script>var WORDS = ["Maya Chen"];</script>' in result


def test_a_slot_whose_original_moved_fails_closed() -> None:
    index = slot_index_of(PART_HTML, "Maya Chen")
    with pytest.raises(SlotAnchorRejected):
        render_part_working_copy(
            PART_HTML,
            slots=(PartSlot(index=index, original="Someone Else", parent_tag="div"),),
            copy={index: "张三"},
            font_css=FONT_CSS,
        )


def test_a_slot_addressing_a_run_the_document_does_not_have_fails_closed() -> None:
    with pytest.raises(SlotAnchorRejected):
        render_part_working_copy(
            PART_HTML,
            slots=(PartSlot(index=9_999, original="Maya Chen", parent_tag="div"),),
            copy={9_999: "张三"},
            font_css=FONT_CSS,
        )


def test_untrusted_copy_cannot_contribute_a_literal_ascii_run() -> None:
    index = slot_index_of(PART_HTML, "Maya Chen")
    result = render_part_working_copy(
        PART_HTML,
        slots=(PartSlot(index=index, original="Maya Chen", parent_tag="div"),),
        copy={index: "<script>alert('x')</script>"},
        font_css=FONT_CSS,
    )

    # The copy reached the document, but as numeric character references only:
    # it cannot have added a second script element.
    assert result.count("<script>") == 1
    assert "alert" not in result


def test_the_font_rules_ride_along_in_the_same_working_copy() -> None:
    index = slot_index_of(PART_HTML, "Maya Chen")
    result = render_part_working_copy(
        PART_HTML,
        slots=(PartSlot(index=index, original="Maya Chen", parent_tag="div"),),
        copy={index: "张三"},
        font_css=FONT_CSS,
    )

    assert result.count(FONT_CSS) == 1
    assert result.index(FONT_CSS) < result.index("</head>")


def test_a_slot_left_without_copy_keeps_its_original() -> None:
    index = slot_index_of(PART_HTML, "Maya Chen")
    result = render_part_working_copy(
        PART_HTML,
        slots=(PartSlot(index=index, original="Maya Chen", parent_tag="div"),),
        copy={},
        font_css=FONT_CSS,
    )

    assert result.count("Maya Chen") == 3


def test_a_run_that_carries_indentation_keeps_it() -> None:
    """Only the copy changes; the document's own whitespace is not the copy.

    The anchor is the stripped run because that is what the gate froze, but the
    edit has to leave the surrounding whitespace alone — swallowing it would
    join this run to whatever inline element sits beside it.
    """
    index = slot_index_of(PART_HTML, "Host · Neuroscientist")
    result = render_part_working_copy(
        PART_HTML,
        slots=(
            PartSlot(index=index, original="Host · Neuroscientist", parent_tag="div"),
        ),
        copy={index: "主播·神经科学家"},
        font_css=FONT_CSS,
    )

    assert '<div class="lt-tag">\n  主播·神经科学家\n</div>' in result


def test_copy_addressed_at_a_run_no_slot_declared_fails_closed() -> None:
    index = slot_index_of(PART_HTML, "Maya Chen")
    other = slot_index_of(PART_HTML, "Host · Neuroscientist")
    with pytest.raises(SlotAnchorRejected):
        render_part_working_copy(
            PART_HTML,
            slots=(PartSlot(index=index, original="Maya Chen", parent_tag="div"),),
            copy={other: "主播"},
            font_css=FONT_CSS,
        )
