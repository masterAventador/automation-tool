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

    `start` and `end` bound the run in the *source* string, which is not the
    same length as `text`: with `convert_charrefs` on, `Maya &amp; Chen` is
    reported as the 11-character `Maya & Chen` but occupies 15 source
    characters. Substitution needs the source span, and it has to come from
    this walker rather than from a search: two runs can read the same thing,
    and a search would edit whichever came first.

    `parent_open_start` and `parent_open_end` bound the *opening tag* of the
    element holding this run, so a caller can put an attribute on that element
    without parsing the document a second time. PC-14 needs it: overflow is a
    property of the box the text sits in, and the browser has to be able to find
    that box. Marking it here rather than counting text nodes again in
    JavaScript keeps one enumeration — the reason §6 of `PC-03.md` gives for
    this walker being shared with the freezing gate in the first place.

    Both are `-1` for a run with no open element above it (text before any tag),
    which cannot be a slot.
    """

    index: int
    text: str
    parent_tag: str
    visible: bool
    start: int
    end: int
    parent_open_start: int
    parent_open_end: int


class _TextWalker(HTMLParser):
    """Walks the document, numbering text runs and bounding them in the source.

    A run's end offset is the start of whatever token follows it, so every
    handler closes the pending run before doing its own work. `getpos()` was
    measured to report the *start* of the token being handled, including for
    the merged run `convert_charrefs` produces, which is what makes this work.
    """

    def __init__(self, source: str) -> None:
        # convert_charrefs resolves `&amp;` into `&` and hands each text run
        # over whole, so a slot's frozen original is what the document says
        # rather than how it happens to spell it.
        super().__init__(convert_charrefs=True)
        self._source = source
        self._line_starts = _line_starts(source)
        # Tag name plus the source span of its opening tag. The span is what
        # lets a caller mark the element (PC-14) without walking the document
        # again; the name is what the original stack held.
        self._stack: list[tuple[str, int, int]] = []
        self._pending: int | None = None
        self.nodes: list[TextNode] = []

    def _offset(self) -> int:
        line, column = self.getpos()
        return self._line_starts[line - 1] + column

    def _close_pending(self) -> None:
        if self._pending is None:
            return
        node = self.nodes[self._pending]
        self.nodes[self._pending] = TextNode(
            index=node.index,
            text=node.text,
            parent_tag=node.parent_tag,
            visible=node.visible,
            start=node.start,
            end=self._offset(),
            parent_open_start=node.parent_open_start,
            parent_open_end=node.parent_open_end,
        )
        self._pending = None

    def handle_starttag(self, tag: str, attrs: object) -> None:
        self._close_pending()
        start = self._offset()
        # The literal text of the tag as it appears in the source, so the span
        # covers whatever attributes and whitespace it happens to carry rather
        # than an idealised `<div>`.
        literal = self.get_starttag_text() or f"<{tag}>"
        self._stack.append((tag, start, start + len(literal)))

    def handle_startendtag(self, tag: str, attrs: object) -> None:
        # Self-closing: it opens and closes without ever containing text.
        self._close_pending()

    def handle_endtag(self, tag: str) -> None:
        self._close_pending()
        # An unbalanced close (upstream documents contain a few) must not
        # unwind past the element that actually opened, or every later node
        # would be attributed to the wrong parent.
        if any(open_tag == tag for open_tag, _, _ in self._stack):
            while self._stack and self._stack.pop()[0] != tag:
                continue

    def handle_comment(self, data: str) -> None:
        self._close_pending()

    def handle_decl(self, decl: str) -> None:
        self._close_pending()

    def handle_pi(self, data: str) -> None:
        self._close_pending()

    def unknown_decl(self, data: str) -> None:
        self._close_pending()

    def handle_data(self, data: str) -> None:
        self._close_pending()
        parent, open_start, open_end = self._stack[-1] if self._stack else ("", -1, -1)
        painted = not any(tag in NON_RENDERING_TAGS for tag, _, _ in self._stack)
        self._pending = len(self.nodes)
        self.nodes.append(
            TextNode(
                index=len(self.nodes),
                text=data,
                parent_tag=parent,
                visible=painted and bool(data.strip()),
                start=self._offset(),
                # Replaced by `_close_pending`; a run that reaches end of input
                # is closed against the source length in `enumerate_text_nodes`.
                end=len(self._source),
                parent_open_start=open_start,
                parent_open_end=open_end,
            )
        )


def _line_starts(source: str) -> tuple[int, ...]:
    """Absolute offset of each line, so `getpos()` can be made absolute."""
    starts = [0]
    for offset, character in enumerate(source):
        if character == "\n":
            starts.append(offset + 1)
    return tuple(starts)


def enumerate_text_nodes(html: str) -> tuple[TextNode, ...]:
    """Every text run in document order, numbered densely from zero."""
    walker = _TextWalker(html)
    walker.feed(html)
    walker.close()
    # A trailing run has no following token to bound it; its `end` is already
    # the source length from construction, so nothing more is needed here.
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
