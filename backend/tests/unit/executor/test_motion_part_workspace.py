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

from pathlib import Path

import pytest

from automation_tool.executor.motion_authoring.part_workspace import (
    PartSlot,
    SlotAnchorRejected,
    render_part_working_copy,
    write_part_working_copy,
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

    # The opening tag now carries PC-14's measurement mark; the whitespace this
    # test is about is the part after it, asserted verbatim.
    assert (
        f'<div class="lt-tag" data-motion-slot="{index}">\n  主播·神经科学家\n</div>'
        in result
    )


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


# --- PC-03 接线：把零件写进 RenderJob 工作区 ---------------------------------


class _RecordingWorkspace:
    """The write surface `AuthoringWorkspace` offers, with the writes recorded.

    The real one enforces containment, refuses symlinks and rolls back on a
    partial write. The part writer must go through it rather than copying trees
    with `shutil`, or those three guarantees stop covering the largest thing the
    workspace ever receives — so this stand-in records what was asked of it and
    the test asserts the shape, not the disk.
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        self.written: dict[str, bytes] = {}

    def write_text(self, relative: str, text: str) -> Path:
        self.written[relative] = text.encode("utf-8")
        return self._root / relative

    def write_bytes(self, relative: str, payload: bytes) -> Path:
        self.written[relative] = bytes(payload)
        return self._root / relative


# The document as it sits in the catalog: same part, plus the relative
# references every real part carries. Text-node indices are counted from *this*
# text, not from `PART_HTML` — inserting two head elements moves them.
CATALOG_PART_HTML = PART_HTML.replace(
    "</head>",
    '<script src="../../offline-deps/js/gsap.min.js"></script>'
    '<style>.b{background:url(assets/grain.png)}</style></head>',
)


def _catalog(tmp_path: Path) -> Path:
    """A catalog laid out the way the release tree is."""
    root = tmp_path / "motion-catalog"
    part = root / "items" / "lt-bold-block"
    part.mkdir(parents=True)
    # A part reaches its shared runtime and its own assets by relative path, the
    # way every document in the release tree does.
    (part / "lt-bold-block.html").write_text(CATALOG_PART_HTML, encoding="utf-8")
    (part / "assets").mkdir()
    (part / "assets" / "grain.png").write_bytes(b"grain")
    fonts = root / "offline-deps" / "fonts" / "woff2" / "anton"
    fonts.mkdir(parents=True)
    (fonts / "anton-400.woff2").write_bytes(b"font bytes")
    js = root / "offline-deps" / "js"
    js.mkdir(parents=True)
    (js / "gsap.min.js").write_text("// gsap", encoding="utf-8")
    return root


def test_the_working_copy_keeps_the_layout_the_part_references(tmp_path) -> None:
    """`../../offline-deps/...` has to keep resolving.

    Every part reaches its shared dependencies with a path relative to the
    catalog root. A copy that flattened the part into the workspace would load
    no fonts, no GSAP and no Draco — and would do it silently, because a missing
    stylesheet is not an error to a browser.
    """
    catalog = _catalog(tmp_path)
    workspace = _RecordingWorkspace(tmp_path / "job")
    index = slot_index_of(CATALOG_PART_HTML, "Maya Chen")

    entry = write_part_working_copy(
        workspace=workspace,
        catalog_root=catalog,
        name="lt-bold-block",
        slots=(PartSlot(index=index, original="Maya Chen", parent_tag="div"),),
        copy={index: "张三"},
        font_css=FONT_CSS,
    )

    assert entry == "catalog/items/lt-bold-block/lt-bold-block.html"
    # Same relative position as in the catalog, so `../../offline-deps/…` and
    # `assets/…` both resolve from the written document without rewriting a
    # single URL.
    assert "catalog/offline-deps/js/gsap.min.js" in workspace.written
    assert "catalog/items/lt-bold-block/assets/grain.png" in workspace.written


def test_the_written_document_carries_the_copy_and_the_font_rules(tmp_path) -> None:
    catalog = _catalog(tmp_path)
    workspace = _RecordingWorkspace(tmp_path / "job")
    index = slot_index_of(CATALOG_PART_HTML, "Maya Chen")

    entry = write_part_working_copy(
        workspace=workspace,
        catalog_root=catalog,
        name="lt-bold-block",
        slots=(PartSlot(index=index, original="Maya Chen", parent_tag="div"),),
        copy={index: "张三"},
        font_css=FONT_CSS,
    )

    document = workspace.written[entry].decode("utf-8")
    assert "张三" in document
    assert document.count(FONT_CSS) == 1


def test_a_transition_working_copy_replaces_demo_scenes_with_the_beat(
    tmp_path,
) -> None:
    catalog = tmp_path / "motion-catalog"
    part = catalog / "items" / "glitch"
    part.mkdir(parents=True)
    (part / "glitch.html").write_text(
        """<!doctype html><html><head></head><body>
<div data-composition-id="main" data-duration="4" data-width="1920" data-height="1080">
  <div id="s1"><div class="scene-label">SCENE A</div></div>
  <div id="s2"><div class="scene-label">SCENE B</div></div>
  <div class="bp-prompt">use glitch shader transition</div>
</div><script>window.__timelines={main:{seek(){}}}</script></body></html>""",
        encoding="utf-8",
    )
    workspace = _RecordingWorkspace(tmp_path / "job")

    entry = write_part_working_copy(
        workspace=workspace,
        catalog_root=catalog,
        name="glitch",
        slots=(),
        copy={},
        font_css="",
        headline="新品发布",
        body="旧方案切换到新方案",
        items=("更快", "更稳"),
        allow_missing_references=True,
    )

    document = workspace.written[entry].decode("utf-8")
    assert document.index("data-motion-transition-content") < document.index(
        "window.__timelines"
    )
    assert "新品发布" in document
    assert "旧方案切换到新方案" in document


def test_a_part_the_catalog_does_not_carry_fails_closed(tmp_path) -> None:
    catalog = _catalog(tmp_path)
    workspace = _RecordingWorkspace(tmp_path / "job")

    with pytest.raises(SlotAnchorRejected):
        write_part_working_copy(
            workspace=workspace,
            catalog_root=catalog,
            name="not-a-part",
            slots=(),
            copy={},
            font_css="",
        )


def test_the_read_only_catalog_is_never_written_to(tmp_path) -> None:
    """The whole design rests on the release tree staying byte-identical."""
    catalog = _catalog(tmp_path)
    before = {
        path: path.read_bytes() for path in sorted(catalog.rglob("*")) if path.is_file()
    }
    workspace = _RecordingWorkspace(tmp_path / "job")
    index = slot_index_of(CATALOG_PART_HTML, "Maya Chen")

    write_part_working_copy(
        workspace=workspace,
        catalog_root=catalog,
        name="lt-bold-block",
        slots=(PartSlot(index=index, original="Maya Chen", parent_tag="div"),),
        copy={index: "张三"},
        font_css=FONT_CSS,
    )

    after = {
        path: path.read_bytes() for path in sorted(catalog.rglob("*")) if path.is_file()
    }
    assert after == before


def test_only_the_shared_files_the_document_asks_for_are_copied(tmp_path) -> None:
    """The sandbox allowlist has 128 slots and the shared tree has 125 files.

    Copying `offline-deps` wholesale spends the whole budget before the part's
    own assets are counted — measured on the real tree, `nyc-paris-flight` lands
    on exactly 128 and five parts of the 134 already exceed it. So the working
    copy carries what the finished document actually references, resolved from
    the document itself rather than from a list someone maintains.

    A dependency reached only from script therefore does not travel — and that
    is the loud failure the allowlist exists to produce, rather than a silent
    404 inside the render.
    """
    catalog = _catalog(tmp_path)
    unused = catalog / "offline-deps" / "draco"
    unused.mkdir(parents=True)
    (unused / "draco_decoder.wasm").write_bytes(b"unused")
    workspace = _RecordingWorkspace(tmp_path / "job")
    index = slot_index_of(CATALOG_PART_HTML, "Maya Chen")

    write_part_working_copy(
        workspace=workspace,
        catalog_root=catalog,
        name="lt-bold-block",
        slots=(PartSlot(index=index, original="Maya Chen", parent_tag="div"),),
        copy={index: "张三"},
        font_css="@font-face{src:url(../../offline-deps/fonts/woff2/anton/anton-400.woff2);}",
    )

    written = set(workspace.written)
    assert "catalog/offline-deps/fonts/woff2/anton/anton-400.woff2" in written
    assert "catalog/offline-deps/draco/draco_decoder.wasm" not in written
    # Well inside the 128 the sandbox will accept.
    assert len(written) < 10


def test_a_reference_pointing_outside_the_catalog_is_refused(tmp_path) -> None:
    catalog = _catalog(tmp_path)
    part = catalog / "items" / "lt-bold-block"
    (part / "lt-bold-block.html").write_text(
        CATALOG_PART_HTML.replace(
            "</head>", '<script src="../../../escape.js"></script></head>'
        ),
        encoding="utf-8",
    )
    workspace = _RecordingWorkspace(tmp_path / "job")

    with pytest.raises(SlotAnchorRejected):
        write_part_working_copy(
            workspace=workspace,
            catalog_root=catalog,
            name="lt-bold-block",
            slots=(),
            copy={},
            font_css="",
        )


# PC-14: the overflow measurement happens in the browser, and the browser has to
# be able to find the slots. It must not find them by counting text nodes in
# JavaScript — the enumeration is `part_document.enumerate_text_nodes`, and a
# second implementation of it in another language is exactly the错位替换 this
# whole module refuses to risk (PC-03 §6). So the write marks them instead.
def test_each_filled_slot_is_marked_so_a_browser_can_measure_it() -> None:
    name = slot_index_of(PART_HTML, "Maya Chen")
    tag = slot_index_of(PART_HTML, "Host · Neuroscientist")
    rendered = render_part_working_copy(
        PART_HTML,
        slots=(
            PartSlot(index=name, original="Maya Chen", parent_tag="div"),
            PartSlot(index=tag, original="Host · Neuroscientist", parent_tag="div"),
        ),
        copy={name: "张三", tag: "主播"},
        font_css=FONT_CSS,
    )
    assert f'data-motion-slot="{name}"' in rendered
    assert f'data-motion-slot="{tag}"' in rendered


def test_a_slot_left_without_copy_is_not_marked() -> None:
    """Only what this film wrote can overflow beyond what the part shipped.

    An untouched slot still carries the part's own copy, whose overflow is the
    baseline the budget was measured against — measuring it would compare a
    reading against itself.
    """
    name = slot_index_of(PART_HTML, "Maya Chen")
    tag = slot_index_of(PART_HTML, "Host · Neuroscientist")
    rendered = render_part_working_copy(
        PART_HTML,
        slots=(
            PartSlot(index=name, original="Maya Chen", parent_tag="div"),
            PartSlot(index=tag, original="Host · Neuroscientist", parent_tag="div"),
        ),
        copy={name: "张三"},
        font_css=FONT_CSS,
    )
    assert f'data-motion-slot="{name}"' in rendered
    assert f'data-motion-slot="{tag}"' not in rendered


def test_the_mark_lands_on_the_element_that_holds_the_run() -> None:
    """`div class="lt-name"`, not the document root and not a wrapper.

    Overflow is a property of the box the text sits in, so a mark on anything
    else would measure the wrong box and report a healthy slot as clipped or
    the reverse.
    """
    name = slot_index_of(PART_HTML, "Maya Chen")
    rendered = render_part_working_copy(
        PART_HTML,
        slots=(PartSlot(index=name, original="Maya Chen", parent_tag="div"),),
        copy={name: "张三"},
        font_css=FONT_CSS,
    )
    assert f'<div class="lt-name" data-motion-slot="{name}">张三</div>' in rendered


def test_the_caption_sharing_the_text_is_never_marked() -> None:
    """The mark follows the index, not the words — same rule as the substitution."""
    name = slot_index_of(PART_HTML, "Maya Chen")
    rendered = render_part_working_copy(
        PART_HTML,
        slots=(PartSlot(index=name, original="Maya Chen", parent_tag="div"),),
        copy={name: "张三"},
        font_css=FONT_CSS,
    )
    assert "<figcaption>Maya Chen</figcaption>" in rendered
    assert rendered.count("data-motion-slot=") == 1


# BM-16's sweep needs the same reference-driven asset list the working copy is
# built from: PC-13 grew the shared dependency tree past what a wholesale
# allowlist can carry (the sandbox caps at 128), which is the exact problem
# reference-driven copying already solved for the product (PC-05). One traversal,
# used by both — a second implementation in the sweep would be the错位替换 this
# module refuses everywhere else.
def test_referenced_assets_walks_the_same_graph_the_copy_does(tmp_path) -> None:
    from automation_tool.executor.motion_authoring.part_workspace import (
        referenced_assets,
    )

    catalog = tmp_path / "catalog"
    part = catalog / "items" / "demo"
    deps = catalog / "offline-deps"
    part.mkdir(parents=True)
    deps.mkdir(parents=True)
    (deps / "runtime.js").write_text("// runtime", encoding="utf-8")
    (deps / "theme.css").write_text(
        '@font-face { src: url("fonts/face.woff2"); }', encoding="utf-8"
    )
    (deps / "fonts").mkdir()
    (deps / "fonts" / "face.woff2").write_bytes(b"\x00")
    (deps / "unused.js").write_text("// never referenced", encoding="utf-8")
    html = (
        '<link rel="stylesheet" href="../../offline-deps/theme.css">'
        '<script src="../../offline-deps/runtime.js"></script>'
    )

    assets = referenced_assets(html, catalog_root=catalog, origin=part)

    # Transitive through the stylesheet, and nothing that is not reached.
    assert set(assets) == {
        "offline-deps/theme.css",
        "offline-deps/runtime.js",
        "offline-deps/fonts/face.woff2",
    }


def test_referenced_assets_refuses_a_reference_leaving_the_catalog(tmp_path) -> None:
    from automation_tool.executor.motion_authoring.part_workspace import (
        referenced_assets,
    )

    catalog = tmp_path / "catalog"
    part = catalog / "items" / "demo"
    part.mkdir(parents=True)
    with pytest.raises(SlotAnchorRejected):
        referenced_assets(
            '<script src="../../../outside.js"></script>',
            catalog_root=catalog,
            origin=part,
        )


def test_referenced_assets_can_skip_a_dead_reference_when_told_to(tmp_path) -> None:
    """The sweep's policy, stated as a parameter rather than a second traversal.

    The release tree carries parts whose documents reference files that never
    shipped (measured 2026-07-29: `video.mp4`). At render time the sandbox
    blocks such a request and counts it — the render succeeds without the file.
    The working copy must still refuse (a closed tree is the product's
    guarantee), so lenience is opt-in and the default stays refusal.
    """
    from automation_tool.executor.motion_authoring.part_workspace import (
        referenced_assets,
    )

    catalog = tmp_path / "catalog"
    part = catalog / "items" / "demo"
    part.mkdir(parents=True)
    (part / "poster.png").write_bytes(b"\x00")
    html = '<img src="poster.png"><video src="video.mp4"></video>'

    with pytest.raises(SlotAnchorRejected):
        referenced_assets(html, catalog_root=catalog, origin=part)

    assets = referenced_assets(
        html, catalog_root=catalog, origin=part, on_missing="skip"
    )
    assert set(assets) == {"items/demo/poster.png"}


def test_a_visual_only_working_copy_skips_upstream_demo_placeholders(tmp_path) -> None:
    """A dead sample URL must not make a visual-only catalog choice crash."""
    catalog = _catalog(tmp_path)
    part = catalog / "items" / "lt-bold-block"
    (part / "lt-bold-block.html").write_text(
        CATALOG_PART_HTML.replace(
            "</body>", '<video src="missing-demo.mp4"></video></body>'
        ),
        encoding="utf-8",
    )
    workspace = _RecordingWorkspace(tmp_path / "job")

    entry = write_part_working_copy(
        workspace=workspace,
        catalog_root=catalog,
        name="lt-bold-block",
        slots=(),
        copy={},
        font_css="",
        allow_missing_references=True,
    )

    assert entry in workspace.written
    assert not any(path.endswith("missing-demo.mp4") for path in workspace.written)
