"""Turn a catalog component into a complete, renderable film document.

Sixteen of the twenty-five component entries already are complete HTML
documents. The remaining nine are deliberately reusable CSS/JavaScript
fragments: opening them as a page has no content to affect. A film therefore
needs a small, deterministic host for those fragments. The host below is the
production counterpart of the BM-16 visual sweep contract, but fills its
content from the current beat instead of acceptance labels.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from .composition_template import escape_untrusted_text


class ComponentHostRejected(RuntimeError):
    """A component fragment has no reviewed production host contract."""


@dataclass(frozen=True, slots=True)
class ComponentFilmMetadata:
    """The capture window a complete document or reviewed fragment publishes."""

    duration_seconds: float
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class _FragmentHost:
    kind: str
    target_class: str
    setup: str
    probe: str


_GSAP_DEPENDENCY_MARKUP: Final = (
    '<script src="../../offline-deps/js/gsap-3.14.2/gsap.min.js"></script>'
)


_FRAGMENT_HOSTS: Final[dict[str, _FragmentHost]] = {
    "caption-blend-difference": _FragmentHost(
        kind="hero",
        target_class="blend-difference",
        setup="",
        probe=(
            "getComputedStyle(document.querySelector('.blend-difference'))"
            ".mixBlendMode === 'difference'"
        ),
    ),
    "grain-overlay": _FragmentHost(
        kind="hero",
        target_class="",
        setup="",
        probe=(
            "document.querySelector('.grain-texture') !== null && "
            "getComputedStyle(document.querySelector('.grain-texture'))"
            ".backgroundImage !== 'none'"
        ),
    ),
    "grid-pixelate-wipe": _FragmentHost(
        kind="hero",
        target_class="",
        setup=(
            "document.querySelectorAll('#grid-pixelate-overlay .grid-cell')"
            ".forEach((cell, index) => {"
            "cell.style.transform = index % 3 === 0 ? 'scale(1)' : 'scale(.35)';"
            "});"
        ),
        probe=(
            "document.querySelectorAll('#grid-pixelate-overlay .grid-cell')"
            ".length === 144 && "
            "getComputedStyle(document.querySelector("
            "'#grid-pixelate-overlay .grid-cell')).transform !== 'none'"
        ),
    ),
    "motion-blur": _FragmentHost(
        kind="motion",
        target_class="motion-blur-target",
        setup=(
            "const timeline = window.gsap.timeline({paused:true});"
            "timeline.fromTo('.motion-blur-target',{x:-500},"
            "{x:500,duration:3,ease:'power2.inOut'},0);"
            "timeline.set(document.body,{},3);"
            "window.attachMotionBlur('.motion-blur-target', timeline,{axis:'x'});"
            "window.__timelines = window.__timelines || {};"
            "window.__timelines['motion-component-host'] = timeline;"
            "timeline.pause(1.5);"
        ),
        probe=(
            "typeof window.attachMotionBlur === 'function' && "
            "document.querySelector('filter[id^=\"hf-mb-\"]') !== null"
        ),
    ),
    "parallax-unzoom": _FragmentHost(
        kind="cards",
        target_class="parallax-unzoom-grid",
        setup=(
            "document.querySelectorAll("
            "'.parallax-unzoom-card:not([data-pu-focus])').forEach("
            "(card,index)=>{"
            "card.style.setProperty('--pu-dx',`${(index-1)*720}px`);"
            "card.style.setProperty('--pu-dy',`${(index%2===0?-1:1)*520}px`);"
            "});"
        ),
        probe=(
            "document.querySelector('.parallax-unzoom-card:not([data-pu-focus])')"
            ".style.getPropertyValue('--pu-dx') !== ''"
        ),
    ),
    "parallax-zoom": _FragmentHost(
        kind="cards",
        target_class="parallax-zoom-grid",
        setup=(
            "document.querySelectorAll("
            "'.parallax-zoom-card:not([data-pz-focus])').forEach("
            "(card,index)=>{"
            "card.style.setProperty('--pz-dx',`${(index-1)*720}px`);"
            "card.style.setProperty('--pz-dy',`${(index%2===0?-1:1)*520}px`);"
            "});"
        ),
        probe=(
            "document.querySelector('.parallax-zoom-card:not([data-pz-focus])')"
            ".style.getPropertyValue('--pz-dx') !== ''"
        ),
    ),
    "shimmer-sweep": _FragmentHost(
        kind="hero",
        target_class="shimmer-sweep-target",
        setup="",
        probe=(
            "document.querySelector('.shimmer-mask') !== null && "
            "getComputedStyle(document.querySelector('.shimmer-mask'))"
            ".backgroundImage !== 'none'"
        ),
    ),
    "texture-mask-text": _FragmentHost(
        kind="hero",
        target_class="hf-texture-text hf-texture-lava",
        setup="",
        probe=(
            "getComputedStyle(document.querySelector('.hf-texture-text'))"
            ".webkitMaskImage !== 'none'"
        ),
    ),
    "vignette": _FragmentHost(
        kind="hero",
        target_class="",
        setup="",
        probe=(
            "document.querySelector('#hf-vignette') !== null && "
            "getComputedStyle(document.querySelector('#hf-vignette'))"
            ".backgroundImage !== 'none'"
        ),
    ),
}


# These blocks are transition demonstrations upstream, but BM-15 exposes each
# one as a selectable film shot. Their transition timelines are useful; their
# SCENE A/SCENE B and prompt panels are not. Keep the reviewed ids explicit so
# a new upstream demo never starts accepting production copy by accident.
_TRANSITION_FILM_BLOCKS: Final = frozenset(
    {
        "chromatic-radial-split",
        "cinematic-zoom",
        "cross-warp-morph",
        "domain-warp-dissolve",
        "flash-through-white",
        "glitch",
        "gravitational-lens",
        "light-leak",
        "ridged-burn",
        "ripple-waves",
        "sdf-iris",
        "swirl-vortex",
        "thermal-distortion",
        "transitions-3d",
        "transitions-blur",
        "transitions-cover",
        "transitions-destruction",
        "transitions-dissolve",
        "transitions-distortion",
        "transitions-grid",
        "transitions-light",
        "transitions-mechanical",
        "transitions-other",
        "transitions-push",
        "transitions-radial",
        "transitions-scale",
        "whip-pan",
    }
)


def reviewed_visual_part_copy_mode(name: str) -> str:
    """Describe how an unanchored part treats the current beat's visible copy."""
    if name in _FRAGMENT_HOSTS or name in _TRANSITION_FILM_BLOCKS:
        return "beat_host"
    return "visual_only"


def component_film_metadata(*, name: str, source: str) -> ComponentFilmMetadata:
    """Read a complete component's own stage; use defaults only for fragments."""
    if not re.search(r"<!doctype\s+html", source, flags=re.IGNORECASE):
        if name not in _FRAGMENT_HOSTS:
            raise ComponentHostRejected(f"{name}: component fragment has no host contract")
        return ComponentFilmMetadata(duration_seconds=3.0, width=1920, height=1080)

    duration = re.search(
        r"data-duration=[\"']([0-9]+(?:\.[0-9]+)?)",
        source,
        flags=re.IGNORECASE,
    )
    width = re.search(r"data-width=[\"']([0-9]+)", source, flags=re.IGNORECASE)
    height = re.search(r"data-height=[\"']([0-9]+)", source, flags=re.IGNORECASE)
    if duration is None or width is None or height is None:
        raise ComponentHostRejected(f"{name}: complete component has no declared capture metadata")
    duration_seconds = float(duration.group(1))
    canvas_width = int(width.group(1))
    canvas_height = int(height.group(1))
    if (
        duration_seconds <= 0
        or not (16 <= canvas_width <= 7680)
        or not (16 <= canvas_height <= 4320)
    ):
        raise ComponentHostRejected(f"{name}: complete component capture metadata is out of range")
    return ComponentFilmMetadata(
        duration_seconds=duration_seconds,
        width=canvas_width,
        height=canvas_height,
    )


def _complete_document(source: str) -> str:
    backdrop = (
        "<style data-motion-component-backdrop>html,body{background:#10151d!important}</style>"
    )
    if not re.search(r"</head\s*>", source, flags=re.IGNORECASE):
        raise ComponentHostRejected("complete component document has no closing head")
    return re.sub(
        r"</head\s*>",
        backdrop + "</head>",
        source,
        count=1,
        flags=re.IGNORECASE,
    )


def _transition_film_content(
    source: str,
    *,
    headline: str,
    body: str,
    items: Sequence[str],
) -> str:
    """Bind a transition demo's two animated scenes to this storyboard beat.

    The binding executes before the upstream timeline script. That ordering is
    essential for the WebGL transitions: their script captures ``#s1`` and
    ``#s2`` into textures immediately, so changing the DOM afterwards would
    leave the demo labels baked into every rendered frame.

    User copy lives in a template rather than a JavaScript string. The existing
    HTML escaper therefore remains the only text boundary, including for a
    headline containing a closing ``script`` token.
    """
    body_open = re.search(r"<body\b[^>]*>", source, flags=re.IGNORECASE)
    if body_open is None:
        raise ComponentHostRejected("transition film document has no body")
    original_script = re.search(
        r"<script\b",
        source[body_open.end() :],
        flags=re.IGNORECASE,
    )
    if original_script is None:
        raise ComponentHostRejected("transition film document has no timeline script")

    scene_a = headline or body
    scene_b = body or headline
    detail = " · ".join(item for item in items[:4] if item) or scene_b
    safe_a = escape_untrusted_text(scene_a)
    safe_b = escape_untrusted_text(scene_b)
    safe_detail = escape_untrusted_text(detail)
    scene_a_size = _fitted_font_size(scene_a, maximum=120, width=1100, lines=3)
    scene_b_size = _fitted_font_size(scene_b, maximum=120, width=1100, lines=3)
    detail_size = _fitted_font_size(detail, maximum=44, width=1600, lines=3)
    binding = f"""<style data-motion-transition-production>
  .info-bar,#info-bar{{display:none!important}}
  [data-motion-transition-copy]{{max-width:100%!important;overflow-wrap:anywhere!important;
    word-break:break-word!important;white-space:normal!important;line-height:1.05!important}}
</style>
<template data-motion-transition-copy>
  <span data-scene-a>{safe_a}</span>
  <span data-scene-b>{safe_b}</span>
  <span data-detail>{safe_detail}</span>
</template>
<script data-motion-transition-content>
(() => {{
  const copy = document.querySelector('template[data-motion-transition-copy]').content;
  const read = (name) => copy.querySelector(`[data-${{name}}]`).textContent;
  const sceneA = read('scene-a');
  const sceneB = read('scene-b');
  const detail = read('detail');
  const sizes = {{sceneA: {scene_a_size}, sceneB: {scene_b_size}, detail: {detail_size}}};
  const fit = (element, value, size) => {{
    element.textContent = value;
    element.setAttribute('data-motion-transition-copy', '');
    element.style.setProperty('font-size', `${{size}}px`, 'important');
  }};
  const fitScene = (element, value, size) => {{
    fit(element, value, size);
    const characters = Array.from(value);
    const perLine = Math.max(1, Math.ceil(characters.length / 3));
    const lines = [];
    for (let index = 0; index < characters.length; index += perLine) {{
      const line = document.createElement('span');
      line.setAttribute('data-motion-transition-copy-line', '');
      line.style.display = 'block';
      line.textContent = characters.slice(index, index + perLine).join('');
      lines.push(line);
    }}
    if (lines.length) element.replaceChildren(...lines);
  }};
  const setAll = (selector, value, size) =>
    document.querySelectorAll(selector).forEach((element) => fit(element, value, size));
  const setScene = (selector, value, size) => {{
    const scene = document.querySelector(selector);
    if (!scene) return;
    scene.querySelectorAll('*').forEach((element) => {{
      if (element.childElementCount === 0 && element.textContent.trim() !== '') {{
        fit(element, value, size);
      }}
    }});
  }};
  document.querySelectorAll('body *').forEach((element) => {{
    if (element.childElementCount !== 0) return;
    const demo = element.textContent.trim().toUpperCase();
    if (demo === 'ONE' || demo === 'SCENE A') fit(element, sceneA, sizes.sceneA);
    if (demo === 'TWO' || demo === 'SCENE B') fit(element, sceneB, sizes.sceneB);
  }});
  setScene('#s1', sceneA, sizes.sceneA);
  setScene('#scene1', sceneA, sizes.sceneA);
  setScene('#s2', sceneB, sizes.sceneB);
  setScene('#scene2', sceneB, sizes.sceneB);
  setAll('.scene-label', sceneA, sizes.sceneA);
  document.querySelectorAll('#s1 .scene-label,#scene1 .scene-label').forEach((element) => {{
    fitScene(element, sceneA, sizes.sceneA);
  }});
  document.querySelectorAll('#s2 .scene-label,#scene2 .scene-label').forEach((element) => {{
    fitScene(element, sceneB, sizes.sceneB);
  }});
  setAll('.scene-number', '', sizes.detail);
  setAll('.bp-number,.bp-plabel,.info-num', '', sizes.detail);
  setAll('.bp-name,#title-main,#outro-main,#title-h1,#outro-h2,.card-title', sceneA, sizes.sceneA);
  setAll('#title-card .category,#outro-card .category', sceneA, sizes.sceneA);
  setAll('.bp-prompt,#title-count,#title-sub,#outro-tag,.card-sub', sceneB, sizes.sceneB);
  setAll(
    '.bp-desc,.info-desc,#title-label,#outro-label,.subtitle,.info-cat,#info-text',
    detail,
    sizes.detail
  );
  setAll('.info-name', sceneA, sizes.sceneA);
}})();
</script>
"""
    position = body_open.end() + original_script.start()
    return source[:position] + binding + source[position:]


def build_visual_part_film_html(
    *,
    name: str,
    source: str,
    headline: str = "",
    body: str = "",
    items: Sequence[str] = (),
) -> str:
    """Give the catalog's one static overlay an honest standalone shot.

    `lower-third-bild` is intentionally static when used as an overlay track;
    the locked BM-16 exclusion records that exact fact. BM-15 also exposes it
    as a complete shot, where the renderer's static-frame refusal applies. A
    restrained entrance and exit turns that frozen overlay into the standalone
    film the user selected without changing any other catalog document.
    """
    if name in _TRANSITION_FILM_BLOCKS:
        return _transition_film_content(
            source,
            headline=headline,
            body=body,
            items=items,
        )
    if name != "lower-third-bild":
        return source
    if not re.search(r"</head\s*>", source, flags=re.IGNORECASE) or not re.search(
        r"</body\s*>", source, flags=re.IGNORECASE
    ):
        raise ComponentHostRejected("static overlay document has no complete page")
    backdrop = (
        "<style data-motion-static-overlay-host>"
        "html,body,#lb-root{background:linear-gradient(135deg,#061525,#164e63 55%,"
        "#0f766e)!important}"
        "</style>"
    )
    hosted = re.sub(
        r"</head\s*>",
        backdrop + "</head>",
        source,
        count=1,
        flags=re.IGNORECASE,
    )
    timeline = """<script data-motion-static-overlay-timeline>
(() => {
  const root = document.querySelector('#lb-root');
  const main = document.querySelector('#lb-main-outer');
  const sub = document.querySelector('#lb-sub-outer');
  if (!window.gsap || !root || !main || !sub) {
    document.body.replaceChildren();
    return;
  }
  const timeline = window.gsap.timeline({paused:true});
  timeline.fromTo(root, {filter:'brightness(.72) saturate(.82)'},
    {filter:'brightness(1) saturate(1)',duration:2,ease:'sine.inOut'}, 0);
  timeline.fromTo([main, sub], {x:-180,opacity:0},
    {x:0,opacity:1,duration:.8,stagger:.12,ease:'power3.out'}, 0);
  timeline.to([main, sub],
    {x:120,opacity:0,duration:.6,stagger:.08,ease:'power2.in'}, 7.2);
  window.__timelines = window.__timelines || {};
  window.__timelines['lower-third-bild-film-host'] = timeline;
})();
</script>"""
    return re.sub(
        r"</body\s*>",
        timeline + "</body>",
        hosted,
        count=1,
        flags=re.IGNORECASE,
    )


def _copy_markup(
    host: _FragmentHost,
    *,
    headline: str,
    body: str,
    items: Sequence[str],
) -> str:
    safe_body = escape_untrusted_text(body)
    item_markup = (
        '<ul class="motion-component-list">'
        + "".join(f"<li>{escape_untrusted_text(item)}</li>" for item in items if item)
        + "</ul>"
        if any(items)
        else ""
    )
    target = f" {host.target_class}" if host.target_class else ""
    if host.kind == "cards":
        labels = list(items[:4]) or [copy for copy in (headline, body, headline, body) if copy]
        labels = labels[:4]
        while len(labels) < 4:
            labels.append(labels[-1] if labels else headline)
        prefix = "pu" if "unzoom" in host.target_class else "pz"
        effect = "unzoom" if prefix == "pu" else "zoom"
        cards = "".join(
            f'<div class="parallax-{effect}-card" data-{prefix}-row="{index // 2}" '
            f'data-{prefix}-col="{index % 2}"'
            f"{' data-' + prefix + '-focus="true"' if index == 3 else ''}>"
            f'<span data-motion-copy-boundary style="font-size:'
            f'{_fitted_font_size(label, maximum=52, width=320, lines=4)}px">'
            f"{escape_untrusted_text(label)}</span></div>"
            for index, label in enumerate(labels)
        )
        heading_size = _fitted_font_size(
            headline,
            maximum=64,
            width=1500,
            lines=1,
        )
        heading = (
            '<div class="motion-component-cards-heading motion-component-copy">'
            f'<div style="font-size:{heading_size}px">'
            f"{escape_untrusted_text(headline)}</div>"
            + (f'<div class="motion-component-cards-body">{safe_body}</div>' if body else "")
            + "</div>"
        )
        return (
            f'<div class="motion-component-panel motion-component-cards{target}" '
            f'style="--{prefix}-progress:.62">{heading}{cards}</div>'
        )
    if host.kind == "motion":
        font_size = _fitted_font_size(
            headline,
            maximum=132,
            width=1400,
            lines=2,
        )
        return (
            '<div class="motion-component-panel motion-component-cool">'
            '<div class="motion-component-track">'
            f'<div class="motion-component-motion motion-component-copy{target}" '
            f'data-motion-copy-boundary style="font-size:{font_size}px">'
            f"{escape_untrusted_text(headline)}</div>"
            "</div>"
            + (
                f'<div class="motion-component-supporting motion-component-copy">{safe_body}</div>'
                if body
                else ""
            )
            + item_markup
            + "</div>"
        )
    font_size = _fitted_font_size(
        headline,
        maximum=150,
        width=1600,
        lines=4,
    )
    return (
        '<div class="motion-component-panel motion-component-dark">'
        f'<div class="motion-component-kicker motion-component-copy">{safe_body}</div>'
        f'<div class="motion-component-hero motion-component-copy{target}" '
        f'data-motion-copy-boundary style="font-size:{font_size}px">'
        f"{escape_untrusted_text(headline)}</div>" + item_markup + "</div>"
    )


def _fitted_font_size(text: str, *, maximum: int, width: int, lines: int) -> int:
    """Conservatively fit a closed-boundary beat string into a fixed stage."""
    units = max(1, len(text))
    return max(16, min(maximum, (width * lines) // units))


def _setup_script(name: str, host: _FragmentHost) -> str:
    if name == "motion-blur":
        return host.setup
    effect = {
        "shimmer-sweep": (
            "timeline.fromTo('.shimmer-sweep-target',"
            "{'--shimmer-pos':'-20%'},{'--shimmer-pos':'120%',duration:1.2,"
            "ease:'power2.inOut'},.6);"
        ),
        "parallax-zoom": (
            "timeline.fromTo('.parallax-zoom-grid',{'--pz-progress':0},"
            "{'--pz-progress':1,duration:2.4,ease:'power2.inOut'},.3);"
        ),
        "parallax-unzoom": (
            "timeline.fromTo('.parallax-unzoom-grid',{'--pu-progress':0},"
            "{'--pu-progress':1,duration:2.4,ease:'power2.inOut'},.3);"
        ),
        "grid-pixelate-wipe": (
            "const cells='#grid-pixelate-overlay .grid-cell';"
            "timeline.set(cells,{scale:0},0);"
            "timeline.to(cells,{scale:1,duration:.6,"
            "stagger:{amount:.6,from:'center'},ease:'power2.inOut'},.3);"
            "timeline.to(cells,{scale:0,duration:.6,"
            "stagger:{amount:.6,from:'edges'},ease:'power2.inOut'},1.5);"
        ),
    }.get(name, "")
    return (
        host.setup
        + "const timeline = window.gsap.timeline({paused:true});"
        + effect
        + "timeline.fromTo('.motion-component-panel',"
        "{scale:.94,rotation:-.6,opacity:.72},"
        "{scale:1,rotation:0,opacity:1,duration:1,ease:'power2.out'},0);"
        "timeline.to('.motion-component-panel',"
        "{y:-24,duration:1,ease:'sine.inOut'},1);"
        "timeline.to('.motion-component-panel',"
        "{y:0,duration:1,ease:'sine.inOut'},2);"
        "window.__timelines = window.__timelines || {};"
        "window.__timelines['motion-component-host'] = timeline;"
        "timeline.pause(1.5);"
    )


def build_component_film_html(
    *,
    name: str,
    source: str,
    headline: str,
    body: str,
    items: Sequence[str],
) -> str:
    """Return a complete offline component document for one storyboard beat."""
    if re.search(r"<!doctype\s+html", source, flags=re.IGNORECASE):
        return _complete_document(source)
    host = _FRAGMENT_HOSTS.get(name)
    if host is None:
        raise ComponentHostRejected(f"{name}: component fragment has no host contract")
    markup = _copy_markup(host, headline=headline, body=body, items=items)
    setup = _setup_script(name, host)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=1920, height=1080">
  <title>Motion component</title>
  <style>
    *{{box-sizing:border-box}}
    html,body{{margin:0;width:1920px;height:1080px;overflow:hidden;background:#10151d}}
    .motion-component-panel{{position:absolute;inset:0;display:flex;flex-direction:column;
      align-items:center;justify-content:center;isolation:isolate;
      padding:80px;font-family:Arial,"PingFang SC",sans-serif;color:#fff}}
    .motion-component-dark{{background:linear-gradient(135deg,#050816,#283149 55%,#8b5cf6)}}
    .motion-component-cool{{background:linear-gradient(145deg,#071a2b,#16697a 52%,#82c0cc)}}
    .motion-component-copy{{max-width:1600px;overflow-wrap:anywhere;word-break:break-word;
      text-align:center}}
    .motion-component-kicker{{font-size:28px;line-height:1.3;letter-spacing:.2em;
      font-weight:700;margin-bottom:34px}}
    .motion-component-hero{{line-height:.95;font-weight:900;letter-spacing:.04em}}
    .motion-component-track{{width:1500px;height:260px;border:6px solid #e9c46a;
      display:flex;align-items:center;padding:40px}}
    .motion-component-motion{{line-height:.95;font-weight:900;letter-spacing:.08em;
      color:#f4a261;text-shadow:-18px 0 rgba(244,162,97,.18),-36px 0 rgba(244,162,97,.1)}}
    .motion-component-cards{{display:grid;grid-template-columns:repeat(2,360px);
      grid-template-rows:repeat(2,260px);gap:38px;padding:220px 580px}}
    .motion-component-cards-heading{{position:absolute;top:54px;left:160px;right:160px;
      z-index:2;font-weight:900;line-height:1.05;text-align:center}}
    .motion-component-cards-body{{margin-top:14px;font-size:26px;line-height:1.3;
      font-weight:600}}
    .motion-component-cards>.parallax-zoom-card,
    .motion-component-cards>.parallax-unzoom-card{{display:flex;align-items:center;justify-content:center;
      min-width:360px;min-height:260px;border-radius:28px;
      padding:20px;background:linear-gradient(135deg,#e76f51,#f4a261);font-weight:900;
      box-shadow:0 28px 70px rgba(0,0,0,.35)}}
    .motion-component-cards>.parallax-zoom-card>span,
    .motion-component-cards>.parallax-unzoom-card>span{{max-width:320px;line-height:1.05;
      overflow-wrap:anywhere;word-break:break-word;text-align:center}}
    .motion-component-supporting{{max-width:1500px;margin-top:34px;font-size:30px;
      line-height:1.35;font-weight:650;text-align:center;overflow-wrap:anywhere}}
    .motion-component-list{{display:flex;flex-wrap:wrap;justify-content:center;gap:18px;
      max-width:1500px;margin:28px 0 0;padding:0;list-style:none;font-size:26px;
      line-height:1.25;font-weight:700}}
    .motion-component-list>li{{padding:12px 22px;border:2px solid rgba(255,255,255,.5);
      border-radius:999px;overflow-wrap:anywhere}}
  </style>
  {_GSAP_DEPENDENCY_MARKUP}
</head>
<body>
  <div data-composition-id="motion-component-host" data-start="0"
       data-duration="3" data-width="1920" data-height="1080">
{markup}
{source}
  </div>
  <script data-motion-component-effect-timeline>
    (() => {{
      let ok = false;
      try {{
        {setup}
        ok = Boolean(({host.probe}) &&
          window.__timelines?.['motion-component-host'] === timeline);
      }} catch (_error) {{
        ok = false;
      }}
      if (!ok) {{
        document.body.replaceChildren();
        document.body.style.cssText =
          "margin:0;width:1920px;height:1080px;background:#10151d!important";
      }}
    }})();
  </script>
</body>
</html>
"""


__all__ = [
    "ComponentFilmMetadata",
    "ComponentHostRejected",
    "build_component_film_html",
    "build_visual_part_film_html",
    "component_film_metadata",
    "reviewed_visual_part_copy_mode",
]
