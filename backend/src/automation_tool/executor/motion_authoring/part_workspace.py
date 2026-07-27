"""Write one catalog part's working copy: this film's copy, this film's fonts.

The parts ship in a read-only release tree. Rendering with one means copying
its document into the RenderJob workspace and making exactly two edits to the
copy:

* the frozen slots take this film's copy (PC-03);
* the typefaces the part names get `@font-face` rules backed by packaged bytes
  (PC-13).

They are one action on purpose. Both edit the same document, and giving them
separate passes would mean opening the file twice and inventing a second place
where a mistake could write back into the release tree.

Why an anchor is verified instead of trusted
--------------------------------------------
A slot says "the 15th text run currently reads `人物创作平台`". If the document
underneath stops saying that, substituting by position alone puts this film's
copy into some *other* run. There is no exception, no failed gate, no visual
error — just a video with the right words in the wrong place. So a mismatch
stops here, where it is still cheap and still explainable.

The anchor is compared against the *stripped* run because that is the form
`check_motion_part_slots.py` froze; measured against the release tree, 3 of the
48 frozen slots sit in runs that carry surrounding whitespace. The edit itself
is bounded by the source span so that whitespace survives: it belongs to the
document's layout, not to the copy.

(The docstrings here stay in English for the same reason `part_document.py`
gives: `check_user_facing_branding.py` reads Chinese-bearing literals in a `.py`
source as operator copy.)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Mapping, Sequence

from .composition_template import escape_untrusted_text
from .part_document import enumerate_text_nodes

HEAD_CLOSE: Final = "</head>"


@dataclass(frozen=True, slots=True)
class PartSlot:
    """One frozen anchor from `motion-part-slots.v1.json`."""

    index: int
    original: str
    parent_tag: str


class SlotAnchorRejected(RuntimeError):
    """The document is not the one the slot table was frozen against.

    Raised before anything is written. A caller that sees this must not fall
    back to an unsubstituted part: the copy the model wrote would be missing
    with nothing on screen to say so.
    """


def render_part_working_copy(
    html: str,
    *,
    slots: Sequence[PartSlot],
    copy: Mapping[int, str],
    font_css: str,
) -> str:
    """The part's document as this film needs it, or a refusal."""
    nodes = {node.index: node for node in enumerate_text_nodes(html)}
    declared = {slot.index: slot for slot in slots}

    for index in copy:
        if index not in declared:
            raise SlotAnchorRejected(
                f"copy addresses text run {index}, which no slot declares"
            )

    for slot in slots:
        node = nodes.get(slot.index)
        if node is None:
            raise SlotAnchorRejected(
                f"the document has no text run {slot.index}; it has {len(nodes)}"
            )
        if node.text.strip() != slot.original:
            raise SlotAnchorRejected(
                f"text run {slot.index} no longer reads the frozen original"
            )
        if node.parent_tag != slot.parent_tag:
            raise SlotAnchorRejected(
                f"text run {slot.index} moved to a <{node.parent_tag}> element"
            )

    # Right to left: an earlier run's span stays valid while later ones are
    # rewritten, so no offset has to be adjusted for edits already applied.
    result = html
    for index in sorted(copy, reverse=True):
        node = nodes[index]
        result = (
            result[: node.start]
            + _substituted(html[node.start : node.end], copy[index])
            + result[node.end :]
        )
    return _with_font_rules(result, font_css)


def _substituted(raw: str, text: str) -> str:
    """Replace the run's copy, leaving the document's own whitespace in place.

    The whitespace is measured on the source rather than on the decoded run:
    a decoded `\\xa0` counts as whitespace to `str.strip` while occupying six
    source characters, and trusting the decoded length there would shift every
    following offset.
    """
    leading = len(raw) - len(raw.lstrip())
    trailing = len(raw) - len(raw.rstrip())
    tail = raw[len(raw) - trailing :] if trailing else ""
    return raw[:leading] + escape_untrusted_text(text) + tail


def _with_font_rules(html: str, font_css: str) -> str:
    """Put the `@font-face` rules where the part's own styles already are.

    Before `</head>`, so the rules are parsed before any element that asks for
    the family. A part without a head is refused rather than repaired: every
    document in the release tree has one, so a missing head means this is not
    the document we think it is.
    """
    if not font_css:
        return html
    position = html.find(HEAD_CLOSE)
    if position < 0:
        raise SlotAnchorRejected("the document has no <head> to answer its typefaces")
    style = f"<style>{font_css}</style>"
    return html[:position] + style + html[position:]


WORKING_COPY_DIRECTORY: Final = "catalog"
# How a part document reaches the catalog root: it sits at `items/<name>/`.
# Font rules are injected into that document while the contract records their
# artifacts from the root, so the prefix has to be applied by whoever generates
# them — see `part_typography.document_font_css(artifact_prefix=...)`.
PART_TO_CATALOG_ROOT: Final = "../../"
SHARED_DEPENDENCIES: Final = "offline-deps"


def write_part_working_copy(
    *,
    workspace: object,
    catalog_root: Path,
    name: str,
    slots: Sequence[PartSlot],
    copy: Mapping[int, str],
    font_css: str,
    directory: str = WORKING_COPY_DIRECTORY,
) -> str:
    """Put one part into the RenderJob workspace, ready to render.

    The layout mirrors the catalog exactly — `<directory>/items/<name>/…` beside
    `<directory>/offline-deps/…` — because every part reaches GSAP, Draco and
    its typefaces through `../../offline-deps/…`. Flattening the part into the
    workspace would leave all of that unresolved, and a browser reports a
    stylesheet that did not load by rendering without it: no error, no failed
    gate, just a film in the wrong font with no animation.

    Everything is written through the workspace rather than copied with
    `shutil`, so containment, the symlink refusal and the rollback on a partial
    write keep covering the largest thing the workspace ever receives.

    The shared dependencies are written once per workspace, not once per part:
    they are 12 MB and identical for every part in the film.

    Returns the workspace-relative path of the document to render.
    """
    part_directory = catalog_root / "items" / name
    documents = sorted(part_directory.glob("*.html"))
    if len(documents) != 1:
        raise SlotAnchorRejected(
            f"the catalog carries no single document for part {name!r}"
        )
    document = documents[0]

    rendered = render_part_working_copy(
        document.read_text(encoding="utf-8"),
        slots=slots,
        copy=copy,
        font_css=font_css,
    )
    entry = f"{directory}/items/{name}/{document.name}"
    workspace.write_text(entry, rendered)  # type: ignore[attr-defined]
    _copy_referenced(
        workspace,
        catalog_root=catalog_root,
        origin=part_directory,
        text=rendered,
        directory=directory,
    )
    return entry


# What a reference can look like in the documents this catalog ships. Kept
# deliberately small: anything these three miss does not travel, and the
# sandbox's allowlist then refuses the render rather than letting the browser
# quietly draw without it.
_REFERENCE: Final = re.compile(
    r"""(?:src|href)\s*=\s*["']([^"']+)["']|url\(\s*['"]?([^'")]+)['"]?\s*\)"""
)
_COPIED_TEXT_SUFFIXES: Final = frozenset({".css", ".js"})


def _copy_referenced(
    workspace: object,
    *,
    catalog_root: Path,
    origin: Path,
    text: str,
    directory: str,
) -> None:
    """Copy exactly what the finished document reaches for, and what that reaches.

    The sandbox accepts at most 128 allowed assets, and the shared dependency
    tree alone holds 125 files — copying it wholesale spends the budget before
    the part's own assets are counted, and five of the 134 parts already exceed
    it. Reading the references out of the document that will actually be
    rendered keeps the copy to what is used and needs no list to maintain.

    A reference that leaves the catalog is refused rather than skipped: the
    release tree is closed by construction, so one pointing outside means this
    is not the tree the manifest describes.
    """
    pending: list[tuple[Path, str]] = [(origin, text)]
    seen: set[Path] = set()
    while pending:
        base, source = pending.pop()
        for match in _REFERENCE.finditer(source):
            reference = match.group(1) or match.group(2)
            if reference.startswith(("http:", "https:", "data:", "#")):
                continue
            target = (base / reference.split("?", 1)[0].split("#", 1)[0]).resolve()
            try:
                relative = target.relative_to(catalog_root.resolve())
            except ValueError:
                raise SlotAnchorRejected(
                    f"a reference leaves the catalog: {reference}"
                ) from None
            if target in seen:
                continue
            seen.add(target)
            if target.is_symlink() or not target.is_file():
                raise SlotAnchorRejected(f"a reference resolves to nothing: {reference}")
            workspace.write_bytes(  # type: ignore[attr-defined]
                f"{directory}/{relative.as_posix()}", target.read_bytes()
            )
            if target.suffix.lower() in _COPIED_TEXT_SUFFIXES:
                pending.append((target.parent, target.read_text(encoding="utf-8", errors="ignore")))


__all__ = [
    "PART_TO_CATALOG_ROOT",
    "PartSlot",
    "SHARED_DEPENDENCIES",
    "SlotAnchorRejected",
    "WORKING_COPY_DIRECTORY",
    "render_part_working_copy",
    "write_part_working_copy",
]
