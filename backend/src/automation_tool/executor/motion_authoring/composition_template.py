"""Build the animated composition document here, from structured beats.

Why this module exists
----------------------
The authoring agent used to ask the model for a fourth key, ``composition_html``:
a whole standalone animated document, DOM plus CSS plus a complete GSAP timeline.
Measured on 2026-07-26 (``docs/development/T83.md``) it was 5,315 of the 7,543
answered bytes, and the model's downstream rate held constant at ~4.5 KB/s across
seven runs — so most of the wall clock of the one-sentence path was the model
typing out markup this machine could have produced instantly.

(The docstrings here stay in English on purpose: `check_user_facing_branding.py`
reads any Chinese-bearing literal in a `.py` source as operator copy, and would
report this file's own notes as unexplained jargon in the product.)

So the markup is produced here and the model writes only copy. That trade is not
free, and the cost is named in ``docs/development/T92.md``: the film's structure
is now fixed by the four layouts below instead of being invented per film.

Two obligations moved with it
-----------------------------
1. **The gates are ours now.** ``lint_composition`` / ``check_composition`` used
   to judge model output that a repair round could fix. Nothing re-authors this
   document, so a template that trips a gate is a hard failure for the user's
   brief. ``test_motion_composition_template.py`` runs both gates over every
   layout for exactly that reason.

2. **Copy must not be able to write source.** Both gates scan the document as
   *text* (``"http://" in lowered``, ``"websocket" in lowered``), and the model
   still authors the copy. A brief about WebSocket, or a headline quoting a URL,
   would otherwise trip a gate that no longer has a repair round — the user's own
   sentence would be refused for containing an ordinary word. ``escape_untrusted_text``
   removes the class rather than blacklisting: after it, copy contributes no
   literal ASCII to the document at all, so no ASCII token can be formed from it.

The T86 still-frame hole changed shape too. It used to be reachable by the model
deleting the packaged reference's CDN ``<script src>`` while keeping its ``gsap.``
calls; the model no longer writes either. The same shape could now only come from
this file, so the local runtime tag is unconditional here and the check runs over
the template's own output for every layout.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

# Where the seeded animation runtime sits inside a RenderJob workspace. The App
# writes it there under digest verification before this process starts
# (`motion_video_studio.rs::seed_authoring_runtime`); this is the same string on
# the Python side of that hand-off.
AUTHORING_RUNTIME_ASSET: Final = "runtime/gsap.min.js"

# The composition root id. Fixed, never model supplied: it is written into the
# document, read back by the render worker, and is not a place for untrusted text.
COMPOSITION_ID: Final = "main"

# The stage the render sandbox captures. Imported from the shared contract by the
# agent; restated as a call argument rather than a second source here.
SCENE_LAYOUTS: Final[tuple[str, ...]] = ("title", "points", "flow", "stat")

MAX_SCENE_ITEMS: Final = 6


@dataclass(frozen=True)
class TemplateScene:
    """One beat as the template needs it: when it plays and what it says."""

    clip_id: str
    layout: str
    headline: str
    body: str
    items: tuple[str, ...]
    start_seconds: float
    duration_seconds: float


def escape_untrusted_text(text: str) -> str:
    """Render text so it cannot contribute a literal ASCII token to the source.

    Every ASCII character becomes a numeric character reference, which the
    browser renders identically. Whitespace collapses to a plain space so words
    stay separated; C0/C7F controls are dropped. Non-ASCII passes through: the
    structural characters of HTML and every banned token in the static gates are
    ASCII, so nothing above U+007F can form one.
    """
    rendered: list[str] = []
    for character in text:
        code = ord(character)
        if character.isspace():
            rendered.append(" ")
        elif code < 0x20 or code == 0x7F:
            continue
        elif code < 0x80:
            rendered.append(f"&#{code};")
        else:
            rendered.append(character)
    return "".join(rendered)


def _number(value: float) -> str:
    return f"{float(value)}"


def _scene_markup(scene: TemplateScene, index: int) -> str:
    headline = escape_untrusted_text(scene.headline)
    body = escape_untrusted_text(scene.body)
    items = "".join(f"<li>{escape_untrusted_text(item)}</li>" for item in scene.items)
    eyebrow = f'<span class="eyebrow">{index + 1:02d}</span>'
    caption = f"<p>{body}</p>" if body else ""

    if scene.layout == "title":
        inner = f"<h1>{headline}</h1>" + (f'<p class="lede">{body}</p>' if body else "")
    elif scene.layout == "points":
        inner = eyebrow + f"<h2>{headline}</h2>" + caption
        if items:
            inner += f'<ul class="chips">{items}</ul>'
    elif scene.layout == "flow":
        inner = eyebrow + f"<h2>{headline}</h2>" + caption
        if items:
            inner += f'<ol class="flow">{items}</ol>'
    elif scene.layout == "stat":
        inner = f'<strong class="figure">{headline}</strong>' + (
            f'<p class="figure-label">{body}</p>' if body else ""
        )
    else:  # pragma: no cover - guarded in render_composition
        raise ValueError(f"unknown layout: {scene.layout}")

    return (
        f'<section id="{scene.clip_id}" class="clip clip-{scene.layout}"'
        f' data-start="{_number(scene.start_seconds)}"'
        f' data-duration="{_number(scene.duration_seconds)}"'
        f' data-track-index="{index}">'
        f'<div class="stage">{inner}</div>'
        f'<div class="meter"><i></i></div>'
        f"</section>"
    )


def _scene_timeline(scene: TemplateScene, *, is_last: bool) -> str:
    """The tweens for one beat, all placed on the seeked timeline.

    Every beat gets a meter tween spanning its whole length. That is not
    decoration: the render worker seeks ``data-duration * fps`` times and T86's
    exit gate fails a render whose frames are all identical, so a beat with no
    tween across it is a stretch of frames the composition cannot distinguish.
    An entrance animation alone leaves the tail of a long beat frozen.
    """
    start = _number(scene.start_seconds)
    end = _number(scene.start_seconds + scene.duration_seconds)
    selector = f"#{scene.clip_id}"
    lines = [f'tl.set("{selector}",{{autoAlpha:1}},{start});']
    if not is_last:
        lines.append(f'tl.set("{selector}",{{autoAlpha:0}},{end});')

    if scene.layout == "title":
        lines.append(
            f'tl.from("{selector} h1",{{y:34,opacity:0,duration:0.55,'
            f'ease:"power3.out"}},{_number(scene.start_seconds + 0.1)});'
        )
        if scene.body:
            lines.append(
                f'tl.from("{selector} .lede",{{y:20,opacity:0,duration:0.45,'
                f'ease:"power2.out"}},{_number(scene.start_seconds + 0.35)});'
            )
    elif scene.layout == "stat":
        lines.append(
            f'tl.from("{selector} .figure",{{scale:0.66,opacity:0,duration:0.5,'
            f'ease:"back.out(1.6)"}},{_number(scene.start_seconds + 0.1)});'
        )
        if scene.body:
            lines.append(
                f'tl.from("{selector} .figure-label",{{y:18,opacity:0,duration:0.4,'
                f'ease:"power2.out"}},{_number(scene.start_seconds + 0.4)});'
            )
    else:
        lines.append(
            f'tl.from("{selector} .eyebrow",{{scale:0,opacity:0,duration:0.32,'
            f'ease:"back.out(2)"}},{_number(scene.start_seconds + 0.08)});'
        )
        lines.append(
            f'tl.from("{selector} h2",{{y:22,opacity:0,duration:0.45,'
            f'ease:"power2.out"}},{_number(scene.start_seconds + 0.24)});'
        )
        if scene.body:
            lines.append(
                f'tl.from("{selector} p",{{y:16,opacity:0,duration:0.4,'
                f'ease:"power2.out"}},{_number(scene.start_seconds + 0.46)});'
            )
        if scene.items:
            child = ".chips li" if scene.layout == "points" else ".flow li"
            lines.append(
                f'tl.from("{selector} {child}",{{y:22,opacity:0,duration:0.34,'
                f'stagger:0.1,ease:"power2.out"}},{_number(scene.start_seconds + 0.66)});'
            )

    # Placed last so the span the beat is measured by is the one a reader finds
    # first when asking "what moves for the whole beat".
    lines.append(
        f'tl.to("{selector} .meter i",{{scaleX:1,ease:"none",'
        f"duration:{_number(scene.duration_seconds)}}},{start});"
    )
    return "".join(lines)


def _stylesheet(*, primary: str, secondary: str, width: int, height: int) -> str:
    """The whole look of the film, in one place, sized for the capture stage.

    The stage is 640x360 in production, so the type scale here is deliberately
    small: a 1920x1080 scale captured at this size is the crop failure
    `_canvas_findings` exists to reject, and it reads as an empty frame.
    """
    return (
        "*{box-sizing:border-box}"
        "html,body{margin:0;padding:0;overflow:hidden}"
        f"body{{background:{primary};"
        "font-family:system-ui,-apple-system,'Helvetica Neue',Arial,sans-serif}"
        f"#root{{position:relative;width:{width}px;height:{height}px;overflow:hidden;"
        f"background:linear-gradient(135deg,{primary} 0%,#0b1220 62%,{primary} 100%);"
        "color:#f8fafc}"
        f"#root:before{{content:'';position:absolute;right:-90px;top:-110px;"
        f"width:300px;height:300px;border-radius:50%;background:{secondary};opacity:.18}}"
        f"#root:after{{content:'';position:absolute;left:-70px;bottom:-120px;"
        f"width:260px;height:260px;border-radius:50%;background:{secondary};opacity:.12}}"
        ".clip{position:absolute;inset:0;opacity:0;visibility:hidden}"
        ".stage{position:absolute;inset:0;padding:40px 46px 52px;display:flex;"
        "flex-direction:column;align-items:flex-start;justify-content:center}"
        ".clip-title .stage,.clip-stat .stage{align-items:center;text-align:center}"
        "h1{margin:0;font-size:42px;line-height:1.12;font-weight:800;letter-spacing:1px}"
        "h2{margin:0;font-size:27px;line-height:1.2;font-weight:800;"
        f"color:{secondary}}}"
        "p{margin:10px 0 0;font-size:15px;line-height:1.62;font-weight:400;"
        "color:#cbd5e1;max-width:520px}"
        ".lede{margin-top:14px;font-size:17px;color:#e2e8f0}"
        ".eyebrow{display:inline-block;margin-bottom:12px;padding:3px 11px;"
        f"border-radius:999px;background:{secondary};color:#0b1220;"
        "font-size:12px;font-weight:800;letter-spacing:.14em}"
        ".chips,.flow{display:flex;align-items:center;gap:10px;margin:16px 0 0;"
        "padding:0;list-style:none}"
        ".chips li{padding:7px 13px;border-radius:9px;font-size:13px;font-weight:600;"
        f"color:#e2e8f0;border:1px solid {secondary};background:rgba(248,250,252,.08)}}"
        ".flow li{position:relative;padding:8px 14px;border-radius:9px;font-size:13px;"
        f"font-weight:600;color:#0b1220;background:{secondary}}}"
        ".flow li+li{margin-left:22px}"
        ".flow li+li:before{content:'→';position:absolute;left:-20px;top:50%;"
        f"transform:translateY(-50%);color:{secondary};font-size:16px}}"
        ".figure{font-size:66px;line-height:1;font-weight:800;letter-spacing:-1px;"
        f"color:{secondary}}}"
        ".figure-label{margin-top:12px;font-size:17px;color:#e2e8f0}"
        ".meter{position:absolute;left:46px;right:46px;bottom:26px;height:4px;"
        "border-radius:99px;background:rgba(248,250,252,.16);overflow:hidden}"
        ".meter i{display:block;height:100%;width:100%;transform:scaleX(0);"
        f"transform-origin:left center;background:{secondary}}}"
    )


def render_composition(
    *,
    primary_color: str,
    secondary_color: str,
    scenes: Sequence[TemplateScene],
    duration_seconds: int,
    stage_width: int,
    stage_height: int,
    runtime_asset: str = AUTHORING_RUNTIME_ASSET,
) -> str:
    """Return the seekable composition document for these beats.

    Raises ``ValueError`` rather than emitting an unreviewed frame: a layout this
    module does not publish has no styling and no timeline, and would reach the
    user as a blank stretch of film that every static gate happily accepts.
    """
    if not scenes:
        raise ValueError("a composition needs at least one scene")
    for scene in scenes:
        if scene.layout not in SCENE_LAYOUTS:
            raise ValueError(f"unknown scene layout: {scene.layout}")
        if len(scene.items) > MAX_SCENE_ITEMS:
            raise ValueError("too many scene items")

    body = "".join(_scene_markup(scene, index) for index, scene in enumerate(scenes))
    timeline = "".join(
        _scene_timeline(scene, is_last=index == len(scenes) - 1)
        for index, scene in enumerate(scenes)
    )
    stylesheet = _stylesheet(
        primary=primary_color,
        secondary=secondary_color,
        width=stage_width,
        height=stage_height,
    )
    return (
        "<!doctype html>\n"
        '<html lang="zh">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        f'<meta name="viewport" content="width={stage_width}, height={stage_height}">\n'
        "<title>composition</title>\n"
        f'<script src="{runtime_asset}"></script>\n'
        f"<style>{stylesheet}</style>\n"
        "</head>\n"
        "<body>\n"
        f'<div id="root" data-composition-id="{COMPOSITION_ID}"'
        f' data-duration="{duration_seconds}" data-width="{stage_width}"'
        f' data-height="{stage_height}" data-start="0">\n'
        f"{body}\n"
        "</div>\n"
        "<script>\n"
        "window.__timelines = window.__timelines || {};\n"
        "const tl = gsap.timeline({ paused: true });\n"
        f"{timeline}\n"
        f'window.__timelines["{COMPOSITION_ID}"] = tl;\n'
        "</script>\n"
        "</body>\n"
        "</html>\n"
    )


__all__ = [
    "AUTHORING_RUNTIME_ASSET",
    "COMPOSITION_ID",
    "MAX_SCENE_ITEMS",
    "SCENE_LAYOUTS",
    "TemplateScene",
    "escape_untrusted_text",
    "render_composition",
]
