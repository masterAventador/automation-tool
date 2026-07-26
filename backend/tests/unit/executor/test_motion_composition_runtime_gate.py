"""A composition that calls an animation runtime it never loads must be refused.

The packaged authoring reference demonstrates the animation runtime as a CDN
tag (`vendor/hyperframes/.../minimal-composition.md` line 12 is
`<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js">`).
The render sandbox is offline, so the model is told to strip remote references
— and the cheapest way to satisfy `remote_reference` is to delete the tag and
keep the `gsap.timeline(...)` calls that depended on it.

The result passes every other static gate: the text `window.__timelines`, the
paused timeline, the clip intervals and the canvas are all still there. Only at
render time is `gsap` undefined, the setup script throws, no timeline registers
and every frame comes out identical. So the repair instruction itself steered the
model into the silent failure, which is why this gate exists next to the others.

T92 moved the document to a local template, so the model no longer writes either
the tag or the calls and the repair loop is gone. That closes the original route
but not the shape: the same unrunnable document is one bad edit to
`composition_template.py` away, and this gate is what would catch it. The
template's own output is checked against it in
`test_motion_composition_template.py`; what stays here is the gate itself, held
against the exact shape and against the two innocent shapes it must not touch.
"""

from __future__ import annotations

from automation_tool.executor.motion_authoring.agent import (
    MotionBrief,
    _first_message_contract,
    check_composition,
)
from automation_tool.executor.motion_authoring.composition_template import (
    AUTHORING_RUNTIME_ASSET,
)

DURATION = 6

_HEAD = """<!doctype html>
<html lang="zh">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=640, height=360" />
    <title>Composition</title>
"""

_BODY = """    <style>
      body { margin: 0; background: #0b1f3a; color: #ffffff; }
      #root { position: relative; width: 640px; height: 360px; overflow: hidden; }
      .clip { position: absolute; inset: 0; display: grid; place-items: center; }
    </style>
  </head>
  <body>
    <div id="root" data-composition-id="main" data-start="0"
         data-width="640" data-height="360" data-duration="6">
      <section id="hook" class="clip" data-start="0" data-duration="6" data-track-index="1">
        <h1 id="title">本周销售增长</h1>
      </section>
    </div>
    <script>
      window.__timelines = window.__timelines || {};
      const tl = gsap.timeline({ paused: true });
      tl.from("#title", { y: 48, opacity: 0, duration: 0.6 }, 0.2);
      window.__timelines["main"] = tl;
    </script>
  </body>
</html>
"""

LOADS_LOCAL_RUNTIME = f'{_HEAD}    <script src="./runtime/gsap.min.js"></script>\n{_BODY}'
LOADS_NOTHING = f"{_HEAD}{_BODY}"


def test_composition_calling_an_unloaded_runtime_is_refused() -> None:
    """The exact survivor of the repair loop: uses gsap, loads no script."""
    result = check_composition(LOADS_NOTHING, duration_seconds=DURATION)
    assert "missing_animation_runtime" in result.codes(), (
        "a composition that calls gsap while loading no script renders as a "
        f"still image; findings were {sorted(result.codes())}"
    )


def test_composition_loading_the_local_runtime_passes() -> None:
    """The shape the authoring contract teaches must not be caught by the gate."""
    result = check_composition(LOADS_LOCAL_RUNTIME, duration_seconds=DURATION)
    assert "missing_animation_runtime" not in result.codes(), (
        f"the local runtime path is the fix, not a defect: {sorted(result.codes())}"
    )


def test_the_authoring_prompt_no_longer_hands_the_model_the_tag_at_all() -> None:
    """The pinned reference teaches the wrong thing and cannot be edited.

    `vendor/hyperframes` is a read-only, digest-verified submodule, so the CDN
    `<script src="https://...gsap...">` on line 12 of its minimal-composition
    reference stays exactly as it is, and it is still shipped in the system
    message. The prompt used to have to argue with that example; it now removes
    the job the example belongs to instead — the model is told it writes no
    markup, so there is no tag for it to copy, keep or delete.
    """
    brief = MotionBrief(
        text="用蓝色商务风做一段本周销售增长说明",
        aspect_ratio="16:9",
        duration_seconds=DURATION,
        language="zh",
    )
    message = _first_message_contract(brief)
    assert AUTHORING_RUNTIME_ASSET not in message, (
        f"the model has no use for the runtime path any more: {message}"
    )
    assert "<script" not in message, f"the prompt must not hand back a tag: {message}"


def test_composition_defining_the_runtime_inline_passes() -> None:
    """An inlined runtime needs no script tag, so the gate must not fire."""
    inlined = LOADS_NOTHING.replace(
        "window.__timelines = window.__timelines || {};",
        "const gsap = buildRuntime();\n      window.__timelines = window.__timelines || {};",
    )
    result = check_composition(inlined, duration_seconds=DURATION)
    assert "missing_animation_runtime" not in result.codes(), (
        f"a composition that defines its own runtime is not broken: {sorted(result.codes())}"
    )
