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
and every frame comes out identical. So the repair instruction itself steers the
model into the silent failure, which is why this gate exists next to the others.
"""

from __future__ import annotations

from automation_tool.executor.motion_authoring.agent import (
    LintResult,
    MotionBrief,
    _first_message_contract,
    _fix_message_contract,
    check_composition,
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


def test_the_repair_instruction_explains_how_to_load_the_runtime() -> None:
    """Rejecting without saying what to do would only stall the fix loop.

    The model reaches this state by obeying the previous round's "remove every
    remote reference", so the next round has to name the local script path as
    the replacement rather than repeat the instruction that caused it.
    """
    brief = MotionBrief(
        text="用蓝色商务风做一段本周销售增长说明",
        aspect_ratio="16:9",
        duration_seconds=DURATION,
        language="zh",
    )
    message = _fix_message_contract(
        LintResult(()),
        check_composition(LOADS_NOTHING, duration_seconds=DURATION),
        brief,
    )
    assert "missing_animation_runtime" in message, (
        f"the repair round must address the code it reported: {message}"
    )
    assert "script" in message, (
        f"the repair round must name the local script tag as the fix: {message}"
    )


def test_the_authoring_prompt_overrides_the_reference_cdn_example() -> None:
    """The pinned reference teaches the wrong thing and cannot be edited.

    `vendor/hyperframes` is a read-only, digest-verified submodule, so the CDN
    `<script src="https://...gsap...">` on line 12 of its minimal-composition
    reference stays exactly as it is. A concrete worked example outweighs a
    prose rule, so the prompt that ships that reference has to name the tag and
    tell the model to replace it rather than merely mention local assets.
    """
    brief = MotionBrief(
        text="用蓝色商务风做一段本周销售增长说明",
        aspect_ratio="16:9",
        duration_seconds=DURATION,
        language="zh",
    )
    message = _first_message_contract(brief, ("runtime/gsap.min.js",))
    assert "cdn" in message.lower(), (
        f"the prompt must name the CDN tag the reference demonstrates: {message}"
    )
    assert "runtime/gsap.min.js" in message, (
        f"the prompt must offer the local path as the replacement: {message}"
    )


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
