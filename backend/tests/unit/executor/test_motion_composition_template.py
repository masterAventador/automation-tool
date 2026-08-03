"""The composition HTML is built here, on this machine, not written by a model.

Measured on 2026-07-26 (`docs/development/T83.md`): one authoring round asked the
model for four keys, and the fourth — a whole standalone animated HTML document —
was 5,315 of the 7,543 answered bytes. Downstream throughput was a constant
4.5 KB/s across seven runs, so the wall clock of "一句话生成动效视频" was almost
exactly the size of that document divided by a fixed rate.

This module renders the same document locally from the structured beats, which
leaves the model writing only copy. The gates the model used to have to satisfy
by hand (`lint_composition` / `check_composition`) are asserted here against the
template's own output, because they are now *our* obligation rather than the
model's: nothing downstream re-authors the file if the template gets it wrong.

The other reason these tests are strict about text: both gates scan the document
as *source text* (`"http://" in lowered`, `"websocket" in lowered`), so any model
copy that reached the file literally could trip a gate the model can no longer be
asked to repair — a brief about WebSocket would refuse itself. Untrusted copy is
therefore escaped until it can contribute no literal ASCII at all.
"""

from __future__ import annotations

import re

import pytest

from automation_tool.executor.motion_authoring.agent import (
    RENDER_CANVAS_HEIGHT,
    RENDER_CANVAS_WIDTH,
    check_composition,
    lint_composition,
)
from automation_tool.executor.motion_authoring.composition_template import (
    AUTHORING_RUNTIME_ASSET,
    MAX_SCENE_ITEMS,
    SCENE_LAYOUTS,
    TemplateScene,
    escape_untrusted_text,
    render_composition,
)

DURATION = 6
COMPOSITION_PATH = "composition.html"
ALLOWED = frozenset({AUTHORING_RUNTIME_ASSET})


def _scenes(layout: str = "points") -> tuple[TemplateScene, ...]:
    return (
        TemplateScene(
            clip_id="clip-1",
            layout="title",
            headline="本周销售增长",
            body="三个要点带你看完",
            items=(),
            start_seconds=0.0,
            duration_seconds=2.0,
        ),
        TemplateScene(
            clip_id="clip-2",
            layout=layout,
            headline="新客户转化率显著提升",
            body="渠道投放与私域承接同时发力",
            items=("投放", "承接", "复购"),
            start_seconds=2.0,
            duration_seconds=2.5,
        ),
        TemplateScene(
            clip_id="clip-3",
            layout="stat",
            headline="+30%",
            body="环比增长",
            items=(),
            start_seconds=4.5,
            duration_seconds=1.5,
        ),
    )


def _render(scenes: tuple[TemplateScene, ...] | None = None) -> str:
    return render_composition(
        primary_color="#1e3a8a",
        secondary_color="#38bdf8",
        scenes=scenes if scenes is not None else _scenes(),
        duration_seconds=DURATION,
        stage_width=RENDER_CANVAS_WIDTH,
        stage_height=RENDER_CANVAS_HEIGHT,
        runtime_asset=AUTHORING_RUNTIME_ASSET,
    )


def _gate_codes(html: str) -> frozenset[str]:
    lint = lint_composition(
        html,
        allowed_assets=ALLOWED,
        max_bytes=512_000,
        entry_path=COMPOSITION_PATH,
    )
    check = check_composition(html, duration_seconds=DURATION)
    return lint.codes() | check.codes()


def test_the_template_output_passes_the_static_gates_it_used_to_ask_the_model_for() -> None:
    """The whole premise: the local document is admissible without a model round."""
    assert _gate_codes(_render()) == frozenset()


@pytest.mark.parametrize("layout", SCENE_LAYOUTS)
def test_every_published_layout_passes_the_static_gates(layout: str) -> None:
    """A layout that cannot pass the gates is a layout the product cannot ship."""
    assert _gate_codes(_render(_scenes(layout))) == frozenset()


def test_the_stage_is_exactly_the_captured_viewport() -> None:
    html = _render()
    assert f'data-width="{RENDER_CANVAS_WIDTH}"' in html
    assert f'data-height="{RENDER_CANVAS_HEIGHT}"' in html
    assert f"{RENDER_CANVAS_WIDTH}px" in html and f"{RENDER_CANVAS_HEIGHT}px" in html


def test_clip_intervals_come_from_the_storyboard_and_tile_the_film() -> None:
    """Timing is arithmetic now, so `clip_overlap` / `clip_coverage` cannot recur."""
    html = _render()
    declared = re.findall(
        r'data-start="([\d.]+)"\s+data-duration="([\d.]+)"\s+data-track-index="(\d+)"', html
    )
    assert [(float(a), float(b), int(c)) for a, b, c in declared] == [
        (0.0, 2.0, 0),
        (2.0, 2.5, 1),
        (4.5, 1.5, 2),
    ]


def test_the_copy_the_model_wrote_reaches_the_frame() -> None:
    """Templating must not quietly drop the one thing the model still authors."""
    html = _render()
    for phrase in ("本周销售增长", "新客户转化率显著提升", "环比增长", "投放", "承接", "复购"):
        assert phrase in html, f"scene copy vanished from the frame: {phrase}"


def test_every_beat_carries_motion_for_its_whole_length() -> None:
    """A beat with no tween across it renders as a still run of frames.

    The exit-side guard added by T86 fails a render whose frames are all byte
    identical. That guard is the last line, not the design: each beat is given a
    tween that spans it so a still stretch cannot be produced in the first place.
    """
    html = _render()
    for scene in _scenes():
        span = re.search(
            r"#" + re.escape(scene.clip_id) + r"\s+\.meter[^)]*?"
            r"duration:\s*" + re.escape(f"{scene.duration_seconds}") + r"\b",
            html,
        )
        assert span is not None, f"{scene.clip_id} has no tween spanning the beat"


def test_the_template_can_never_produce_the_still_frame_shape_t86_found() -> None:
    """T86: calling the runtime while loading no script renders a still film.

    The model used to be able to reach that shape by deleting the CDN tag the
    packaged reference demonstrates. It no longer writes the document at all, so
    the same hole would now have to come from this template — which is why the
    check runs against the template's own output for every layout.
    """
    for layout in SCENE_LAYOUTS:
        html = _render(_scenes(layout))
        assert f'src="{AUTHORING_RUNTIME_ASSET}"' in html
        assert (
            "missing_animation_runtime"
            not in check_composition(html, duration_seconds=DURATION).codes()
        )


def test_untrusted_copy_cannot_contribute_a_single_literal_ascii_run() -> None:
    """Both gates scan source text, so copy must not be able to write source.

    `lint_composition` fails a document containing the substring `websocket` or
    `http://` anywhere — including inside a headline. With the model no longer
    able to repair the document, a brief about WebSocket or a URL in the copy
    would refuse the user's own sentence. Escaping every ASCII character to a
    numeric reference removes the class: the copy renders identically and
    contributes no letters, digits or punctuation to the source at all.
    """
    hostile = TemplateScene(
        clip_id="clip-1",
        layout="points",
        headline="WebSocket 与 https://example.com 实时通信",
        body="new Date() 与 Math.random() 的用法, fetch( 与 setTimeout(",
        items=("</script><script>alert(1)</script>", "repeat: -1", "addEventListener"),
        start_seconds=0.0,
        duration_seconds=float(DURATION),
    )
    html = _render((hostile,))
    assert _gate_codes(html) == frozenset()
    assert "alert(1)" not in html
    assert "<script>alert" not in html
    for banned in ("websocket", "http://", "math.random", "settimeout", "addeventlistener"):
        assert banned not in html.lower()
    # The CJK the user actually reads is untouched; only ASCII is neutralised.
    assert "实时通信" in html


def test_escaping_leaves_no_ascii_except_the_references_it_writes() -> None:
    escaped = escape_untrusted_text("A1 <b>&amp;</b> 中文")
    assert "中文" in escaped
    assert re.fullmatch(r"(?:&#\d+;| |中|文)+", escaped), escaped
    assert "<b>" not in escaped


def test_a_scene_that_declares_no_layout_the_template_knows_is_refused() -> None:
    """Fail closed rather than render an unstyled frame nobody reviewed."""
    with pytest.raises(ValueError):
        _render(
            (
                TemplateScene(
                    clip_id="clip-1",
                    layout="cinematic-zoom",
                    headline="标题",
                    body="",
                    items=(),
                    start_seconds=0.0,
                    duration_seconds=float(DURATION),
                ),
            )
        )


def _scene(**overrides: object) -> TemplateScene:
    values: dict[str, object] = {
        "clip_id": "clip-1",
        "layout": "points",
        "headline": "标题",
        "body": "正文",
        "items": ("一", "二"),
        "start_seconds": 0.0,
        "duration_seconds": float(DURATION),
    }
    values.update(overrides)
    return TemplateScene(**values)  # type: ignore[arg-type]


def test_a_composition_with_no_scenes_is_refused() -> None:
    """An empty film is a blank stretch every static gate would happily accept."""
    with pytest.raises(ValueError):
        _render(())


def test_a_scene_carrying_more_items_than_the_frame_holds_is_refused() -> None:
    with pytest.raises(ValueError):
        _render((_scene(items=tuple(f"要点{index}" for index in range(MAX_SCENE_ITEMS + 1))),))


def test_control_characters_in_untrusted_copy_are_dropped_rather_than_escaped() -> None:
    """They render as nothing and can terminate markup; escaping would keep them."""
    rendered = _render((_scene(headline="标题\x00\x07\x1f\x7f收尾", items=(), body=""),))

    assert "\x00" not in rendered
    assert "&#0;" not in rendered
    assert "&#127;" not in rendered
    assert "标题" in rendered


@pytest.mark.parametrize("layout", ["title", "points", "flow", "stat"])
def test_a_scene_may_omit_every_optional_part_of_its_layout(layout: str) -> None:
    """Body and items are optional; a layout missing them still renders and animates."""
    rendered = _render((_scene(layout=layout, body="", items=()),))

    assert f"clip-{layout}" in rendered
    # The optional parts animate only when they exist; the stylesheet mentions
    # their class names regardless, so the timeline is what gets asserted.
    assert 'tl.from("#clip-1 .lede"' not in rendered
    assert 'tl.from("#clip-1 p"' not in rendered
    assert 'tl.from("#clip-1 .figure-label"' not in rendered
    assert "<ul" not in rendered
    assert "<ol" not in rendered
    assert "tl.to(" in rendered, "the beat still carries motion for its whole length"
