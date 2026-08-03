from __future__ import annotations

import re

import pytest

from automation_tool.executor.motion_authoring.component_host import (
    ComponentHostRejected,
    build_component_film_html,
    build_visual_part_film_html,
    component_film_metadata,
    reviewed_visual_part_copy_mode,
)


def test_a_fragment_component_uses_the_beat_copy_in_its_real_effect_host() -> None:
    source = "<style>.blend-difference{mix-blend-mode:difference}</style>"

    document = build_component_film_html(
        name="caption-blend-difference",
        source=source,
        headline="本周增长 <12%>",
        body="三个关键指标",
        items=("收入", "转化"),
    )

    assert source in document
    assert "本周增长 &#60;&#49;&#50;&#37;&#62;" in document
    assert "本周增长 <12%>" not in document
    assert "三个关键指标" in document
    assert "mixBlendMode === 'difference'" in document
    assert 'data-composition-id="motion-component-host"' in document
    assert "http://" not in document
    assert "https://" not in document


def test_a_complete_component_document_keeps_its_timeline_and_gets_a_backdrop() -> None:
    source = """<!doctype html><html><head></head><body>
<div data-composition-id="caption" data-duration="3">VISIBLE</div>
<script>window.__timelines = {caption: "sentinel"};</script>
</body></html>"""

    document = build_component_film_html(
        name="caption-clip-wipe",
        source=source,
        headline="标题",
        body="正文",
        items=(),
    )

    assert "VISIBLE" in document
    assert 'window.__timelines = {caption: "sentinel"};' in document
    assert "background:#10151d!important" in document
    assert "motion-component-host" not in document


def test_a_complete_component_uses_its_declared_timeline_and_canvas() -> None:
    source = """<!doctype html><html><body>
<div data-composition-id="caption" data-duration="8"
     data-width="1920" data-height="1080"></div>
</body></html>"""

    metadata = component_film_metadata(name="caption-clip-wipe", source=source)

    assert metadata.duration_seconds == 8
    assert (metadata.width, metadata.height) == (1920, 1080)


def test_a_fragment_component_uses_the_reviewed_three_second_host_stage() -> None:
    metadata = component_film_metadata(
        name="motion-blur",
        source="<style>.motion-blur-target{filter:blur(3px)}</style>",
    )

    assert metadata.duration_seconds == 3
    assert (metadata.width, metadata.height) == (1920, 1080)


def test_motion_blur_fragment_builds_a_real_offline_gsap_timeline() -> None:
    source = """<script>
window.attachMotionBlur = function (selector, timeline) {
  if (!selector || !timeline) throw new Error("missing motion blur input");
};
</script>"""

    document = build_component_film_html(
        name="motion-blur",
        source=source,
        headline="速度",
        body="方向性拖影",
        items=(),
    )

    assert "../../offline-deps/js/gsap-3.14.2/gsap.min.js" in document
    assert "window.gsap.timeline({paused:true})" in document
    assert "window.attachMotionBlur('.motion-blur-target', timeline" in document
    assert "window.__timelines['motion-component-host'] = timeline" in document


@pytest.mark.parametrize(
    ("name", "source"),
    (
        (
            "caption-blend-difference",
            "<style>.blend-difference{mix-blend-mode:difference}</style>",
        ),
        ("motion-blur", "<script>window.attachMotionBlur=()=>{}</script>"),
        ("parallax-zoom", "<style>.parallax-zoom-grid{--pz-progress:0}</style>"),
    ),
)
def test_each_fragment_host_preserves_every_nonempty_beat_copy_field(
    name: str,
    source: str,
) -> None:
    document = build_component_film_html(
        name=name,
        source=source,
        headline="季度增长标题",
        body="这是本镜头的说明",
        items=("收入提升", "转化提升"),
    )

    assert "季度增长标题" in document
    assert "这是本镜头的说明" in document
    assert "收入提升" in document
    assert "转化提升" in document


@pytest.mark.parametrize(
    ("name", "source", "effect_contract"),
    (
        (
            "shimmer-sweep",
            "<style>.shimmer-sweep-target{--shimmer-pos:-20%}</style>",
            "'--shimmer-pos':'120%'",
        ),
        (
            "parallax-zoom",
            "<style>.parallax-zoom-grid{--pz-progress:0}</style>",
            "'--pz-progress':1",
        ),
        (
            "parallax-unzoom",
            "<style>.parallax-unzoom-grid{--pu-progress:0}</style>",
            "'--pu-progress':1",
        ),
        (
            "grid-pixelate-wipe",
            """<div id="grid-pixelate-overlay"><div class="grid-cell"></div></div>
<style>.grid-cell{transform:scale(0)}</style>""",
            "stagger:{amount:.6,from:'center'}",
        ),
    ),
)
def test_a_fragment_host_drives_the_selected_effect_not_only_its_panel(
    name: str,
    source: str,
    effect_contract: str,
) -> None:
    document = build_component_film_html(
        name=name,
        source=source,
        headline="真实标题",
        body="真实正文",
        items=("要点一", "要点二"),
    )

    assert effect_contract in document
    assert "data-motion-component-effect-timeline" in document
    assert "timeline.fromTo" in document or "timeline.to" in document


@pytest.mark.parametrize(
    ("name", "prefix"),
    (("parallax-zoom", "pz"), ("parallax-unzoom", "pu")),
)
def test_a_parallax_focus_card_never_falls_back_to_empty_copy(
    name: str,
    prefix: str,
) -> None:
    document = build_component_film_html(
        name=name,
        source=f"<style>.parallax-{name.removeprefix('parallax-')}-grid{{}}</style>",
        headline="唯一可用标题",
        body="",
        items=(),
    )

    focus = re.search(
        rf'<div class="parallax-[^"]+-card"[^>]*data-{prefix}-focus="true"[^>]*>'
        r"(?P<label>.*?)</div>",
        document,
    )
    assert focus is not None
    assert re.sub(r"<[^>]+>", "", focus.group("label")) == "唯一可用标题"


@pytest.mark.parametrize(
    ("name", "source", "maximum_font_size"),
    (
        (
            "caption-blend-difference",
            "<style>.blend-difference{mix-blend-mode:difference}</style>",
            106,
        ),
        (
            "motion-blur",
            "<script>window.attachMotionBlur=()=>{}</script>",
            46,
        ),
    ),
)
def test_a_fragment_host_fits_the_longest_legal_headline_inside_its_stage(
    name: str,
    source: str,
    maximum_font_size: int,
) -> None:
    document = build_component_film_html(
        name=name,
        source=source,
        headline="增长" * 30,
        body="说明" * 60,
        items=(),
    )

    match = re.search(
        r'data-motion-copy-boundary style="font-size:(?P<size>\d+)px"',
        document,
    )
    assert match is not None
    assert int(match.group("size")) <= maximum_font_size
    assert "overflow-wrap:anywhere" in document
    assert "max-width:1600px" in document


def test_a_fragment_host_measures_ascii_before_escaping_it() -> None:
    document = build_component_film_html(
        name="motion-blur",
        source="<script>window.attachMotionBlur=()=>{}</script>",
        headline="A" * 60,
        body="",
        items=(),
    )

    match = re.search(
        r'data-motion-copy-boundary style="font-size:(?P<size>\d+)px"',
        document,
    )
    assert match is not None
    assert int(match.group("size")) == 46


def test_a_transition_block_binds_the_beat_to_both_real_scenes_before_capture() -> None:
    source = """<!doctype html><html><head></head><body>
<div data-composition-id="main" data-duration="4" data-width="1920" data-height="1080">
  <div id="s1" class="scene"><div class="scene-label">SCENE A</div></div>
  <div id="s2" class="scene"><div class="scene-label">SCENE B</div></div>
  <div class="bp-right"><div class="bp-plabel">Prompt</div>
    <div class="bp-prompt">use glitch shader transition</div></div>
</div>
<script data-original-transition-timeline>captureScene("s1");</script>
</body></html>"""

    document = build_visual_part_film_html(
        name="glitch",
        source=source,
        headline="新品发布",
        body="从旧方案切换到新方案",
        items=("更快", "更稳"),
    )

    binding = document.index("data-motion-transition-content")
    capture = document.index("data-original-transition-timeline")
    assert binding < capture
    assert "新品发布" in document
    assert "从旧方案切换到新方案" in document
    assert "data-motion-transition-production" in document
    assert ".info-bar,#info-bar{display:none!important}" in document
    assert "setAll('.scene-label', sceneA, sizes.sceneA)" in document
    assert (
        "#s2 .scene-label,#scene2 .scene-label').forEach((element) => {\n"
        "    fitScene(element, sceneB, sizes.sceneB);"
    ) in document
    assert "data-motion-transition-copy-line" in document
    assert (
        "setAll('.bp-prompt,#title-count,#title-sub,#outro-tag,.card-sub', sceneB, sizes.sceneB)"
    ) in document


def test_a_transition_block_fits_the_longest_legal_copy_inside_scene_nodes() -> None:
    source = """<!doctype html><html><head></head><body>
<div data-composition-id="main" data-duration="4" data-width="1920" data-height="1080">
  <div id="s1"><div class="scene-label">SCENE A</div></div>
  <div id="s2"><div class="scene-label">SCENE B</div></div>
</div><script>window.__timelines={main:{seek(){}}}</script></body></html>"""

    document = build_visual_part_film_html(
        name="glitch",
        source=source,
        headline="增长" * 30,
        body="说明" * 60,
        items=("要点" * 12,),
    )

    scene_a = re.search(r"sceneA:\s*(?P<size>\d+)", document)
    scene_b = re.search(r"sceneB:\s*(?P<size>\d+)", document)
    assert scene_a is not None and int(scene_a.group("size")) == 55
    assert scene_b is not None and int(scene_b.group("size")) == 27
    assert "data-motion-transition-copy" in document
    assert "overflow-wrap:anywhere" in document
    assert "word-break:break-word" in document


def test_transition_detail_keeps_its_role_size_when_it_matches_scene_b() -> None:
    """An empty items list makes detail == body; equality must not promote it to a title."""
    source = """<!doctype html><html><head></head><body>
<div data-composition-id="main" data-duration="4" data-width="1920" data-height="1080">
  <div id="s1"><div class="scene-label">SCENE A</div></div>
  <div id="s2"><div class="scene-label">SCENE B</div></div>
  <div class="bp-desc">DETAIL</div>
</div><script>window.__timelines={main:{seek(){}}}</script></body></html>"""

    document = build_visual_part_film_html(
        name="glitch",
        source=source,
        headline="标题",
        body="正文" * 30,
        items=(),
    )

    assert "const fit = (element, value, size)" in document
    assert re.search(
        r"setAll\(\s*'\.bp-desc,\.info-desc,#title-label,#outro-label,"
        r"\.subtitle,\.info-cat,#info-text',\s*detail,\s*sizes\.detail\s*\)",
        document,
    )


def test_an_unknown_fragment_component_is_rejected_fail_closed() -> None:
    with pytest.raises(ComponentHostRejected, match="host contract"):
        build_component_film_html(
            name="unknown-component",
            source="<style>.unknown{opacity:.5}</style>",
            headline="标题",
            body="正文",
            items=(),
        )


def test_a_fragment_no_host_contract_covers_is_refused() -> None:
    """Fragments get a reviewed stage each; one nobody reviewed has no stage to get."""
    with pytest.raises(ComponentHostRejected):
        component_film_metadata(name="not-a-known-fragment", source="<div>fragment</div>")


def test_a_complete_component_that_declares_no_stage_is_refused() -> None:
    """The declaration is where the capture size and length come from; nothing guesses."""
    complete = (
        '<!doctype html><html><body><div data-composition-id="caption" '
        "{duration}{width}{height}></div></body></html>"
    )

    duration = 'data-duration="3" '
    width = 'data-width="1920" '
    height = 'data-height="1080"'
    for label, fields in [
        ("no duration", {"duration": "", "width": width, "height": height}),
        ("no width", {"duration": duration, "width": "", "height": height}),
        ("no height", {"duration": duration, "width": width, "height": ""}),
    ]:
        with pytest.raises(ComponentHostRejected):
            component_film_metadata(name="caption-clip-wipe", source=complete.format(**fields))
        assert label


def test_a_complete_component_declaring_a_stage_nothing_can_capture_is_refused() -> None:
    def source(duration: str, width: str, height: str) -> str:
        return (
            '<!doctype html><html><body><div data-composition-id="caption" '
            f'data-duration="{duration}" data-width="{width}" data-height="{height}">'
            "</div></body></html>"
        )

    for label, arguments in [
        ("a length of zero", ("0", "1920", "1080")),
        ("a width below the floor", ("3", "8", "1080")),
        ("a width past the ceiling", ("3", "7681", "1080")),
        ("a height below the floor", ("3", "1920", "8")),
        ("a height past the ceiling", ("3", "1920", "4321")),
    ]:
        with pytest.raises(ComponentHostRejected):
            component_film_metadata(name="caption-clip-wipe", source=source(*arguments))
        assert label


def test_a_complete_component_with_no_closing_head_cannot_be_given_a_backdrop() -> None:
    """The backdrop is what keeps a transparent component from capturing as noise."""
    with pytest.raises(ComponentHostRejected):
        build_component_film_html(
            name="caption-clip-wipe",
            source=(
                "<!doctype html><html><body>"
                '<div data-composition-id="caption">x</div></body></html>'
            ),
            headline="标题",
            body="",
            items=(),
        )


def test_a_static_overlay_without_a_complete_page_is_refused() -> None:
    """`lower-third-bild` is hosted by rewriting both ends; half a page has one."""
    for label, source in [
        ("no closing head", "<!doctype html><html><body><div id='lb-root'></div></body></html>"),
        ("no closing body", "<!doctype html><html><head></head><div id='lb-root'></div></html>"),
    ]:
        with pytest.raises(ComponentHostRejected):
            build_visual_part_film_html(name="lower-third-bild", source=source)
        assert label


def test_a_static_overlay_is_hosted_with_its_own_backdrop_and_timeline() -> None:
    """The one catalog part that is static as an overlay gets an honest standalone shot."""
    document = build_visual_part_film_html(
        name="lower-third-bild",
        source=(
            "<!doctype html><html><head></head><body>"
            "<div id='lb-root'><div id='lb-main-outer'></div>"
            "<div id='lb-sub-outer'></div></div></body></html>"
        ),
    )

    assert "data-motion-static-overlay-host" in document
    assert "lower-third-bild-film-host" in document
    assert document.index("data-motion-static-overlay-host") < document.index(
        "data-motion-static-overlay-timeline"
    ), "the backdrop is placed in the head and the timeline at the end of the body"


def test_a_visual_part_with_no_host_of_its_own_is_handed_back_unchanged() -> None:
    source = "<!doctype html><html><head></head><body><div>part</div></body></html>"

    assert build_visual_part_film_html(name="lt-bold-block", source=source) == source


def test_a_transition_film_block_needs_a_body_and_a_timeline_to_host() -> None:
    """Its own script is what gets driven; without one there is nothing to capture."""
    for label, source in [
        ("no body", "<!doctype html><html><head></head></html>"),
        (
            "no timeline script",
            "<!doctype html><html><head></head><body><div>no script</div></body></html>",
        ),
    ]:
        with pytest.raises(ComponentHostRejected):
            build_visual_part_film_html(name="glitch", source=source)
        assert label


def test_the_copy_mode_says_which_parts_take_the_beats_words() -> None:
    """A part with a host shows the beat's copy; one without keeps its own visuals."""
    assert reviewed_visual_part_copy_mode("glitch") == "beat_host"
    assert reviewed_visual_part_copy_mode("motion-blur") == "beat_host"
    assert reviewed_visual_part_copy_mode("lt-bold-block") == "visual_only"
