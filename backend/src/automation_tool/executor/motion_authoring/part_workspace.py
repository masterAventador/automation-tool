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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .component_host import build_component_film_html, build_visual_part_film_html
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
            raise SlotAnchorRejected(f"copy addresses text run {index}, which no slot declares")

    for slot in slots:
        node = nodes.get(slot.index)
        if node is None:
            raise SlotAnchorRejected(
                f"the document has no text run {slot.index}; it has {len(nodes)}"
            )
        if node.text.strip() != slot.original:
            raise SlotAnchorRejected(f"text run {slot.index} no longer reads the frozen original")
        if node.parent_tag != slot.parent_tag:
            raise SlotAnchorRejected(
                f"text run {slot.index} moved to a <{node.parent_tag}> element"
            )

    # Two kinds of edit, applied as one pass from the end of the document
    # backwards: an earlier offset stays valid while later ones are rewritten,
    # so nothing has to be adjusted for edits already applied. They have to
    # share the pass — the mark sits in the opening tag, which is *before* the
    # run it belongs to, and running the two passes separately would leave the
    # second one working against offsets the first had already moved.
    edits = [
        (
            nodes[index].start,
            nodes[index].end,
            _substituted(html[nodes[index].start : nodes[index].end], copy[index]),
        )
        for index in copy
    ]
    edits.extend(_slot_marks(nodes, declared, copy, html))
    result = html
    for start, end, replacement in sorted(edits, reverse=True):
        result = result[:start] + replacement + result[end:]
    return _with_font_rules(result, font_css)


def _slot_marks(
    nodes: Mapping[int, object],
    declared: Mapping[int, PartSlot],
    copy: Mapping[int, str],
    html: str,
) -> list[tuple[int, int, str]]:
    """Mark the box each filled slot sits in, so a browser can measure it.

    PC-14 measures overflow in the packaged Chromium, which has to find the
    slots. It must not find them by numbering text runs in JavaScript: the
    numbering is `part_document.enumerate_text_nodes`, and a second
    implementation of it in another language is the same misplacement risk this
    module refuses everywhere else (`PC-03.md` §6). So the element is marked
    here, by the code that already knows where it is.

    Only slots this film wrote are marked. An untouched slot still carries the
    part's own copy, whose overflow *is* the baseline the budget was measured
    against, so measuring it would compare a reading against itself.

    Slots sharing one element get one mark listing both indices: they share a
    box, so they share its overflow, and saying so is more honest than picking
    one of them to blame.
    """
    by_element: dict[tuple[int, int], list[int]] = {}
    for index in sorted(copy):
        node = nodes[index]
        span = (node.parent_open_start, node.parent_open_end)  # type: ignore[attr-defined]
        if span[0] < 0:
            raise SlotAnchorRejected(
                f"text run {index} has no element to mark; a slot always sits inside one"
            )
        by_element.setdefault(span, []).append(index)

    marks: list[tuple[int, int, str]] = []
    for (start, end), indices in by_element.items():
        opening = html[start:end]
        # Always ends in one: the span is `get_starttag_text()`'s length, and the
        # parser only emits a start tag once it has seen the closing bracket
        # (its fallback spelling carries one too). The only way this slice could
        # disagree is if `getpos()` and the real character offset drifted --
        # measured against CRLF, bare CR, form feed, vertical tab, U+2028, U+0085
        # and U+2029, they do not. Asserted rather than raised, because a raise
        # here is a branch nothing can take, and a future parser change that did
        # break the offsets must fail loudly rather than mark the wrong element.
        assert opening.endswith(">"), (
            f"the element holding slot {indices[0]} has no closing angle "
            "bracket; this is not the document the slots were frozen against"
        )
        # Self-closing (`<img/>`) cannot hold a run, so the only shape here is a
        # normal open tag; the mark goes just inside its closing bracket.
        listed = " ".join(str(index) for index in indices)
        marks.append((start, end, f'{opening[:-1]} data-motion-slot="{listed}">'))
    return marks


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
# Where PC-14's probe stages the *baseline* working copies: the same parts
# carrying their own frozen copy, marked the same way, so overflow can be
# compared between two documents measured in one browser session. Beside the
# render tree, never inside it — nothing here is renderable film material.
BASELINE_COPY_DIRECTORY: Final = "catalog-baseline"
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
    component: bool = False,
    headline: str = "",
    body: str = "",
    items: Sequence[str] = (),
    instance_key: str | None = None,
    directory: str = WORKING_COPY_DIRECTORY,
    allow_missing_references: bool = False,
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
        raise SlotAnchorRejected(f"the catalog carries no single document for part {name!r}")
    document = documents[0]

    source = document.read_text(encoding="utf-8")
    if component:
        source = build_component_film_html(
            name=name,
            source=source,
            headline=headline,
            body=body,
            items=items,
        )
    else:
        source = build_visual_part_film_html(
            name=name,
            source=source,
            headline=headline,
            body=body,
            items=items,
        )
    rendered = render_part_working_copy(
        source,
        slots=slots,
        copy=copy,
        font_css=font_css,
    )
    if instance_key is not None and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", instance_key) is None:
        raise SlotAnchorRejected("part working-copy instance key is malformed")
    entry_name = f"{instance_key}-{document.name}" if instance_key is not None else document.name
    entry = f"{directory}/items/{name}/{entry_name}"
    workspace.write_text(entry, rendered)  # type: ignore[attr-defined]
    _copy_referenced(
        workspace,
        catalog_root=catalog_root,
        origin=part_directory,
        text=rendered,
        directory=directory,
        on_missing="skip" if allow_missing_references else "refuse",
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


def referenced_assets(
    text: str,
    *,
    catalog_root: Path,
    origin: Path,
    on_missing: str = "refuse",
) -> dict[str, Path]:
    """Everything the document reaches for, and what that reaches, by reference.

    Returns catalog-relative posix path → resolved file. One traversal for two
    consumers: the working-copy writer copies exactly this set (PC-05 — the
    sandbox caps at 128 allowed assets and the shared tree alone outgrew that
    when PC-13 added the typefaces), and BM-16's per-part sweep builds its
    allowlist from the same set for the same reason. A second implementation in
    the sweep would be one drift away from "the sweep allows what the product
    never ships".

    A reference that leaves the catalog is refused rather than skipped: the
    release tree is closed by construction, so one pointing outside means this
    is not the tree the manifest describes.
    """
    pending: list[tuple[Path, str]] = [(origin, text)]
    seen: set[Path] = set()
    collected: dict[str, Path] = {}
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
                raise SlotAnchorRejected(f"a reference leaves the catalog: {reference}") from None
            if target in seen:
                continue
            seen.add(target)
            if target.is_symlink() or not target.is_file():
                # The sweep opts into lenience: the release tree carries dead
                # references (measured: `video.mp4`), which the sandbox blocks
                # and counts at render time. The working copy keeps the refusal
                # — a closed tree is the product's guarantee.
                if on_missing == "skip":
                    continue
                raise SlotAnchorRejected(f"a reference resolves to nothing: {reference}")
            collected[relative.as_posix()] = target
            if target.suffix.lower() in _COPIED_TEXT_SUFFIXES:
                pending.append((target.parent, target.read_text(encoding="utf-8", errors="ignore")))
    return collected


def _copy_referenced(
    workspace: object,
    *,
    catalog_root: Path,
    origin: Path,
    text: str,
    directory: str,
    on_missing: str,
) -> None:
    """Copy exactly what `referenced_assets` collects, through the workspace."""
    for relative, target in referenced_assets(
        text, catalog_root=catalog_root, origin=origin, on_missing=on_missing
    ).items():
        workspace.write_bytes(  # type: ignore[attr-defined]
            f"{directory}/{relative}", target.read_bytes()
        )


def working_copy_assets(entry_html: str, workspace: object) -> tuple[str, ...]:
    """Return only the files this one part document reaches inside the job."""
    prefix = f"{WORKING_COPY_DIRECTORY}/"
    if not entry_html.startswith(prefix):
        return ()
    root = workspace.root / WORKING_COPY_DIRECTORY  # type: ignore[attr-defined]
    document = workspace.resolve(entry_html)  # type: ignore[attr-defined]
    assets = referenced_assets(
        workspace.read_text(entry_html),  # type: ignore[attr-defined]
        catalog_root=root,
        origin=document.parent,
        # Visual-only source documents may retain inert upstream demo URLs.
        # Their requests are absent from the sandbox allowlist and therefore
        # blocked; they must not pull another shot's files into this segment.
        on_missing="skip",
    )
    return tuple(sorted(f"{WORKING_COPY_DIRECTORY}/{relative}" for relative in assets))


__all__ = [
    "BASELINE_COPY_DIRECTORY",
    "PART_TO_CATALOG_ROOT",
    "SHARED_DEPENDENCIES",
    "WORKING_COPY_DIRECTORY",
    "PartSlot",
    "SlotAnchorRejected",
    "referenced_assets",
    "render_part_working_copy",
    "working_copy_assets",
    "write_part_working_copy",
]
