"""Enumerating a catalog part's text nodes — the anchor every slot table rests on.

Why a shipped module rather than a script helper
------------------------------------------------
A slot table says "the 7th text node of `lt-bold-block` currently reads
`Maya Chen`". That sentence is only meaningful if the thing counting text nodes
at freeze time and the thing counting them at render time are the same code.
Two implementations that agree today are a silent mis-substitution waiting for
the first disagreement — and the failure mode is copy landing in the wrong
place, which no gate downstream can see. So the enumerator lives in the
Executor package, and the freezing gate imports it.

Why the index counts everything, including script and style
-----------------------------------------------------------
It is tempting to number only the visible nodes, since those are the only ones
a slot can point at. That makes the index depend on our *policy* about what
counts as visible — and the day that policy changes (a `<template>` starts
being skipped, `<title>` stops being skipped) every frozen index silently
shifts by an unknown amount. Numbering every `handle_data` callback instead
makes the index depend only on the parser walking the document, which is a
property of the document rather than of our opinion about it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from automation_tool.executor.motion_authoring.part_document import (
    TextNode,
    enumerate_text_nodes,
    visible_text_nodes,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
VENDOR_ROOT = REPOSITORY_ROOT / "vendor/hyperframes"
CATALOG_PATH = REPOSITORY_ROOT / "contracts/quality/motion-catalog.v1.json"


def part_html(name: str) -> str:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    item = next(entry for entry in catalog["items"] if entry["name"] == name)
    document = next(record for record in item["files"] if record["path"].endswith(".html"))
    return str((VENDOR_ROOT / item["path"] / document["path"]).read_text(encoding="utf-8"))


def test_the_index_is_dense_and_ordered() -> None:
    nodes = enumerate_text_nodes("<p>a</p><p>b</p>")
    assert [node.index for node in nodes] == list(range(len(nodes)))


def test_script_text_is_numbered_but_not_visible() -> None:
    """The numbering must not depend on our opinion of what is visible."""
    nodes = enumerate_text_nodes("<p>shown</p><script>var hidden = 1;</script><p>also</p>")
    by_text = {node.text.strip(): node for node in nodes}
    assert by_text["shown"].visible is True
    assert by_text["also"].visible is True
    assert by_text["var hidden = 1;"].visible is False
    # `also` keeps its position behind the script rather than closing the gap.
    assert by_text["also"].index > by_text["var hidden = 1;"].index


@pytest.mark.parametrize("container", ["script", "style", "title"])
def test_text_inside_non_rendering_containers_is_not_visible(container: str) -> None:
    nodes = enumerate_text_nodes(f"<{container}>text</{container}>")
    assert [node.visible for node in nodes] == [False]


def test_whitespace_only_nodes_are_never_visible() -> None:
    nodes = enumerate_text_nodes("<div>\n   \n</div>")
    assert all(node.visible is False for node in nodes)


def test_the_parent_tag_is_recorded() -> None:
    node = visible_text_nodes("<div><span>hello</span></div>")[0]
    assert node.parent_tag == "span"
    assert isinstance(node, TextNode)


def test_a_dom_copy_part_exposes_its_copy_as_visible_nodes() -> None:
    """`lt-bold-block` is the shape a slot table can address directly."""
    visible = [node.text.strip() for node in visible_text_nodes(part_html("lt-bold-block"))]
    assert "Maya Chen" in visible
    assert "Host · Neuroscientist" in visible


def test_a_script_driven_part_exposes_no_copy_in_the_dom() -> None:
    """`caption-kinetic-slam` builds its words in JavaScript.

    This is the case that invalidated an earlier plan to batch parts by "how
    many text nodes they have": measured with a regex over the whole file it
    looked like a one-slot part, and the one string found was the `<title>`.
    Nothing addressable by a slot table is in its body at all.
    """
    document = part_html("caption-kinetic-slam")
    visible = [node.text.strip() for node in visible_text_nodes(document)]
    assert visible == [], f"expected no addressable copy, found {visible}"
    # The words are there, in a script the enumerator numbers but never offers.
    assert "Kinetic Slam" in document


def test_entity_references_are_resolved_in_the_recorded_text() -> None:
    """A slot compares its frozen original against this text, so it must be
    what the document says rather than how the document spells it."""
    node = visible_text_nodes("<p>a &amp; b</p>")[0]
    assert node.text.strip() == "a & b"


def test_enumeration_is_stable_across_calls() -> None:
    document = part_html("lt-bold-block")
    assert enumerate_text_nodes(document) == enumerate_text_nodes(document)
