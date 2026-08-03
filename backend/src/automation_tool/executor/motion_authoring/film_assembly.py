"""Turn a storyboard into the list of renders that makes the film.

`catalog_parts` has been on every beat since PC-04 and nothing read it. The
composition was still drawn from the four built-in layouts, so the 134 parts
were validated against the frozen catalog and then ignored — the model could
choose, and its choice reached nothing. Everything route A needs was built
around that hole: the slot table (PC-03), the packaged catalog (PC-16), the
per-part render (PC-05), the join (PC-06), the timeline (PC-08). This module is
what connects them.

One beat becomes one shot:

* a beat that named a part is rendered from that part's working copy, on the
  stage the part declares — 1920x1080 for most of the catalog, 1080x1920 for
  three, and a part drawn on the template's stage is the top-left corner of
  itself;
* a beat that named none falls back to the template composition on the
  template's stage, whose type scale is written for it.

That mixture is what the roadmap asks for under PC-06 — template segments and
part segments in one film — once it is something the code does rather than
something a document says.

Nothing is written until the whole film is planned. A shot that cannot be
rendered fails the plan, and a workspace half full of parts for a film that was
never going to render is a state somebody later has to reason about.

(English docstring for the reason `part_document.py` gives:
`check_user_facing_branding.py` reads Chinese-bearing literals in a `.py` source
as operator copy.)
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

from .film_timeline import (
    RenderCost,
    Shot,
    estimate_render_cost,
    plan_film,
)
from .part_workspace import PartSlot, write_part_working_copy
from .slot_budget import SlotBudget

# From `motion-storyboard-duration.v1.json`. Passed to the timeline rather than
# read there, so the contract has one reader.
RENDER_COST = RenderCost(wall_seconds_base=30, wall_millis_per_frame=400)


@dataclass(frozen=True, slots=True)
class BeatPlan:
    """One beat as the model left it, before it is a render."""

    beat_id: str
    part: str | None
    copy: Mapping[int, str]
    voice_seconds: float | None
    # The length the storyboard gave this beat. Only decides a shot nothing else
    # can — a template beat with no part and, until the narration exists, no
    # line.
    declared_seconds: float | None = None
    # Where this beat sits on the composition's own timeline. Only template
    # beats need it — they all load the same document and differ by nothing
    # else — but every beat carries it so the two kinds are described the same
    # way.
    start_seconds: float = 0.0
    headline: str = ""
    body: str = ""
    items: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FilmSegment:
    """One render: what to load, on what stage, for how many frames.

    `source_start_millis` and `source_end_millis` say which stretch of the
    loaded document this render covers. Whole milliseconds because the render
    command's HMAC binds to a canonical JSON three languages must produce byte
    for byte, and a float does not survive that trip. Without them a segment was only "this
    page, this many frames", and the Worker's only remaining choice was to
    spread the page's whole timeline across those frames — so every template
    beat re-rendered the entire film. See
    `test_each_template_shot_samples_its_own_stretch_of_the_composition`.
    """

    beat_id: str
    part: str | None
    entry_html: str
    canvas: dict[str, int]
    frames: int
    seconds: float
    slot_budgets: tuple[SlotBudget, ...]
    source_start_millis: int
    source_end_millis: int


@dataclass(frozen=True, slots=True)
class AssembledFilm:
    """Every render this film needs, and what it will cost."""

    segments: tuple[FilmSegment, ...]
    total_frames: int
    total_seconds: float
    estimated_render_seconds: float


class AssemblyRejected(RuntimeError):
    """The storyboard cannot be turned into renders.

    Raised before anything is written into the workspace, so a refusal never
    leaves a half-assembled film behind.
    """


class _FrozenSlot(TypedDict):
    index: int
    original: str
    parentTag: str


class _FrozenBudget(TypedDict):
    index: int
    usableWidthPx: int
    fontSizePx: int
    baselineOverflowsX: bool
    baselineOverflowsY: bool


def _slot_records(contract: Mapping[str, object]) -> dict[str, tuple[_FrozenSlot, ...]]:
    parts = contract.get("parts")
    if not isinstance(parts, list):
        raise AssemblyRejected("the part slot contract is malformed")
    records: dict[str, tuple[_FrozenSlot, ...]] = {}
    for part in parts:
        if not isinstance(part, dict):
            raise AssemblyRejected("the part slot contract is malformed")
        name = part.get("name")
        slots = part.get("slots")
        if not isinstance(name, str) or not isinstance(slots, list) or name in records:
            raise AssemblyRejected("the part slot contract is malformed")
        normalized: list[_FrozenSlot] = []
        for slot in slots:
            if not isinstance(slot, dict):
                raise AssemblyRejected("the part slot contract is malformed")
            index = slot.get("index")
            original = slot.get("original")
            parent_tag = slot.get("parentTag")
            if (
                type(index) is not int
                or not isinstance(original, str)
                or not isinstance(parent_tag, str)
            ):
                raise AssemblyRejected("the part slot contract is malformed")
            normalized.append(_FrozenSlot(index=index, original=original, parentTag=parent_tag))
        records[name] = tuple(normalized)
    return records


def _budget_records(contract: Mapping[str, object]) -> dict[str, tuple[_FrozenBudget, ...]]:
    parts = contract.get("parts")
    if not isinstance(parts, list):
        raise AssemblyRejected("the part slot budget contract is malformed")
    records: dict[str, tuple[_FrozenBudget, ...]] = {}
    for part in parts:
        if not isinstance(part, dict):
            raise AssemblyRejected("the part slot budget contract is malformed")
        name = part.get("name")
        slots = part.get("slots")
        if not isinstance(name, str) or not isinstance(slots, list) or name in records:
            raise AssemblyRejected("the part slot budget contract is malformed")
        normalized: list[_FrozenBudget] = []
        for slot in slots:
            if not isinstance(slot, dict):
                raise AssemblyRejected("the part slot budget contract is malformed")
            index = slot.get("index")
            width = slot.get("usableWidthPx")
            font_size = slot.get("fontSizePx")
            overflows_x = slot.get("baselineOverflowsX")
            overflows_y = slot.get("baselineOverflowsY")
            if (
                type(index) is not int
                or type(width) is not int
                or type(font_size) is not int
                or type(overflows_x) is not bool
                or type(overflows_y) is not bool
            ):
                raise AssemblyRejected("the part slot budget contract is malformed")
            normalized.append(
                _FrozenBudget(
                    index=index,
                    usableWidthPx=width,
                    fontSizePx=font_size,
                    baselineOverflowsX=overflows_x,
                    baselineOverflowsY=overflows_y,
                )
            )
        records[name] = tuple(normalized)
    return records


def assemble_film(
    *,
    beats: Sequence[BeatPlan],
    workspace: object,
    catalog_root: Path,
    slot_table: Mapping[str, object],
    slot_budget: Mapping[str, object],
    part_durations: Mapping[str, float],
    part_dimensions: Mapping[str, tuple[int, int]],
    part_types: Mapping[str, str],
    template_canvas: Mapping[str, int],
    template_entry: str = "composition.html",
    frames_per_second: int,
    segment_frames_maximum: int,
    font_css_for: Callable[[str], str],
) -> AssembledFilm:
    """Plan every shot, then write the parts the plan needs."""
    if not beats:
        raise AssemblyRejected("a film needs at least one beat")

    slots_by_part = _slot_records(slot_table)
    budget_by_part = _budget_records(slot_budget)

    for beat in beats:
        if beat.part is None:
            continue
        if beat.part not in part_durations or beat.part not in part_dimensions:
            raise AssemblyRejected(
                f"beat {beat.beat_id!r} names {beat.part!r}, which the catalog does not carry"
            )
        if beat.part not in part_types:
            raise AssemblyRejected(
                f"beat {beat.beat_id!r} names {beat.part!r}, which has no declared type"
            )
        if beat.copy and beat.part not in slots_by_part:
            raise AssemblyRejected(
                f"beat {beat.beat_id!r} supplies copy for {beat.part!r}, which has no "
                "frozen text slots"
            )

    # Planned first, in full. A shot that cannot render fails here, before a
    # single part is copied into the workspace.
    plan = plan_film(
        [
            Shot(
                part=beat.part or "template",
                # A few complete transitions publish 21–24s timelines while
                # one Worker capture is 20s. Sample their complete source
                # window into the maximum legal frame count instead of cutting
                # off their final action or rejecting a locked catalog choice.
                motion_seconds=(
                    min(
                        part_durations[beat.part],
                        segment_frames_maximum / frames_per_second,
                    )
                    if beat.part
                    else None
                ),
                voice_seconds=beat.voice_seconds,
                declared_seconds=beat.declared_seconds,
            )
            for beat in beats
        ],
        frames_per_second=frames_per_second,
        segment_frames_maximum=segment_frames_maximum,
    )

    segments: list[FilmSegment] = []
    for beat, planned in zip(beats, plan.shots, strict=True):
        if beat.part is None:
            segments.append(
                FilmSegment(
                    beat_id=beat.beat_id,
                    part=None,
                    entry_html=template_entry,
                    canvas=dict(template_canvas),
                    frames=planned.frames,
                    seconds=planned.seconds,
                    slot_budgets=(),
                    # The window the storyboard drew this beat into. The
                    # composition holds every beat on one timeline, so this is
                    # the only thing telling two template renders apart.
                    #
                    # Ended at the beat's *declared* length, not at the shot's.
                    # They are numbers from two different timelines: the
                    # declared one is where `_compose` drew this card, while the
                    # shot is max(line, motion) and can be longer. Adding them
                    # would run the window into the next beat's card — the last
                    # quarter of shot one showing shot two's card, then a cut,
                    # then that card again. The extra frames instead resample
                    # this beat's own stretch, which is the rule a part segment
                    # already gets.
                    source_start_millis=round(beat.start_seconds * 1000),
                    source_end_millis=round(
                        (beat.start_seconds + (beat.declared_seconds or planned.seconds)) * 1000
                    ),
                )
            )
            continue
        frozen = slots_by_part.get(beat.part, ())
        entry = write_part_working_copy(
            workspace=workspace,
            catalog_root=catalog_root,
            name=beat.part,
            slots=tuple(
                PartSlot(
                    index=slot["index"],
                    original=slot["original"],
                    parent_tag=slot["parentTag"],
                )
                for slot in frozen
            ),
            copy=beat.copy,
            # Only a part with frozen copy receives replacement copy and the
            # matching packaged font rules. Visual-only parts retain their own
            # typography; asking the resolver about it can reject an upstream
            # demo weight even though this film changes no text.
            font_css=font_css_for(beat.part) if frozen else "",
            component=part_types[beat.part] == "component",
            headline=beat.headline,
            body=beat.body,
            items=beat.items,
            instance_key=beat.beat_id,
            # The locked visual-only set carries a few inert sample references
            # (placeholder videos, wallpaper paths and CSS examples) that the
            # per-part Worker allowlist intentionally blocks. Copy every real
            # dependency while leaving those demo placeholders absent.
            allow_missing_references=not frozen,
        )
        width, height = part_dimensions[beat.part]
        segments.append(
            FilmSegment(
                beat_id=beat.beat_id,
                part=beat.part,
                entry_html=entry,
                # Factor 1: the part's stage already is the output resolution.
                # Sharpening it further buys nothing and costs every frame.
                canvas={"width": width, "height": height, "deviceScaleFactor": 1},
                frames=planned.frames,
                seconds=planned.seconds,
                # A part is its own document with its own timeline, so the
                # window is that timeline. When the shot outlasts the part —
                # the line is longer than the motion — the part still spans the
                # shot, which is how it has always been rendered; what changes
                # here is only that the span is now stated rather than inferred
                # from whatever the page happened to declare.
                source_start_millis=0,
                source_end_millis=round((part_durations.get(beat.part) or planned.seconds) * 1000),
                slot_budgets=tuple(
                    SlotBudget(
                        index=entry_["index"],
                        usable_width_px=entry_["usableWidthPx"],
                        font_size_px=entry_["fontSizePx"],
                        baseline_overflows_x=entry_["baselineOverflowsX"],
                        baseline_overflows_y=entry_["baselineOverflowsY"],
                    )
                    for entry_ in budget_by_part.get(beat.part, ())
                    if any(slot["index"] == entry_["index"] for slot in frozen)
                ),
            )
        )

    return AssembledFilm(
        segments=tuple(segments),
        total_frames=plan.total_frames,
        total_seconds=plan.total_seconds,
        estimated_render_seconds=estimate_render_cost(plan, RENDER_COST),
    )


__all__ = [
    "RENDER_COST",
    "AssembledFilm",
    "AssemblyRejected",
    "BeatPlan",
    "FilmSegment",
    "assemble_film",
]
