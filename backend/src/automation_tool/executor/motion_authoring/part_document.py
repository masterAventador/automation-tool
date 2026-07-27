"""Walk a catalog part's document and number its text.

A slot table says "the 7th text node of `lt-bold-block` currently reads
`Maya Chen`, and copy written there must stay under 9 characters". That
sentence only means something if whatever counts text nodes when the table is
frozen and whatever counts them when a film is rendered are the same code.
Two implementations that agree today are a mis-substitution waiting for their
first disagreement, and the failure is copy landing in the wrong place — which
nothing downstream can see. So this lives in the Executor package: the freezing
gate imports it, and so will the substitution.

Why the index counts every text run, including script and style
---------------------------------------------------------------
Numbering only the visible runs is tempting — they are the only ones a slot can
point at. But then the index depends on our *policy* about what counts as
visible, and the day that policy grows a case (`<template>` starts being
skipped, `<title>` stops being skipped) every frozen index shifts by an unknown
amount, silently. Numbering every run the parser reports makes the index a
property of the document instead of a property of our opinion, so the policy
below can grow without invalidating a single frozen slot.

(The docstrings here stay in English on purpose: `check_user_facing_branding.py`
reads any Chinese-bearing literal in a `.py` source as operator copy, and would
report this file's own notes as unexplained jargon in the product.)
"""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Final

# Elements whose text the browser does not paint. Growing this set is safe:
# it changes which nodes are offered as slots, never how they are numbered.
NON_RENDERING_TAGS: Final[frozenset[str]] = frozenset(
    {"script", "style", "title", "head", "noscript", "template"}
)


@dataclass(frozen=True, slots=True)
class TextNode:
    """One run of text as the parser reported it.

    `text` is verbatim, including the surrounding whitespace the document had:
    a slot compares its frozen original against this value, and trimming here
    would make two different documents compare equal.
    """

    index: int
    text: str
    parent_tag: str
    visible: bool


class _TextWalker(HTMLParser):
    def __init__(self) -> None:
        # convert_charrefs resolves `&amp;` into `&` and hands each text run
        # over whole, so a slot's frozen original is what the document says
        # rather than how it happens to spell it.
        super().__init__(convert_charrefs=True)
        self._stack: list[str] = []
        self.nodes: list[TextNode] = []

    def handle_starttag(self, tag: str, attrs: object) -> None:
        self._stack.append(tag)

    def handle_startendtag(self, tag: str, attrs: object) -> None:
        # Self-closing: it opens and closes without ever containing text.
        return None

    def handle_endtag(self, tag: str) -> None:
        # An unbalanced close (upstream documents contain a few) must not
        # unwind past the element that actually opened, or every later node
        # would be attributed to the wrong parent.
        if tag in self._stack:
            while self._stack and self._stack.pop() != tag:
                continue

    def handle_data(self, data: str) -> None:
        parent = self._stack[-1] if self._stack else ""
        painted = not any(tag in NON_RENDERING_TAGS for tag in self._stack)
        self.nodes.append(
            TextNode(
                index=len(self.nodes),
                text=data,
                parent_tag=parent,
                visible=painted and bool(data.strip()),
            )
        )


def enumerate_text_nodes(html: str) -> tuple[TextNode, ...]:
    """Every text run in document order, numbered densely from zero."""
    walker = _TextWalker()
    walker.feed(html)
    walker.close()
    return tuple(walker.nodes)


def visible_text_nodes(html: str) -> tuple[TextNode, ...]:
    """Only the runs a viewer can read — the ones a slot may address.

    The indices are the ones from `enumerate_text_nodes`, not a fresh count,
    so a slot frozen against this view stays valid when the visibility policy
    changes.
    """
    return tuple(node for node in enumerate_text_nodes(html) if node.visible)


__all__ = [
    "NON_RENDERING_TAGS",
    "TextNode",
    "enumerate_text_nodes",
    "visible_text_nodes",
]
