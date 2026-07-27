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

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

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


@dataclass(frozen=True, slots=True)
class FilmSegment:
    """One render: what to load, on what stage, for how many frames."""

    beat_id: str
    part: str | None
    entry_html: str
    canvas: dict[str, int]
    frames: int
    seconds: float
    slot_budgets: tuple[SlotBudget, ...]


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


def assemble_film(
    *,
    beats: Sequence[BeatPlan],
    workspace: object,
    catalog_root: Path,
    slot_table: Mapping[str, object],
    slot_budget: Mapping[str, object],
    part_durations: Mapping[str, float],
    part_dimensions: Mapping[str, tuple[int, int]],
    template_canvas: Mapping[str, int],
    template_entry: str = "composition.html",
    frames_per_second: int,
    segment_frames_maximum: int,
    font_css_for: Callable[[str], str],
) -> AssembledFilm:
    """Plan every shot, then write the parts the plan needs."""
    if not beats:
        raise AssemblyRejected("a film needs at least one beat")

    slots_by_part = {
        part["name"]: part["slots"] for part in slot_table["parts"]  # type: ignore[index]
    }
    budget_by_part = {
        part["name"]: part["slots"] for part in slot_budget["parts"]  # type: ignore[index]
    }

    for beat in beats:
        if beat.part is None:
            continue
        if beat.part not in part_durations or beat.part not in part_dimensions:
            raise AssemblyRejected(
                f"beat {beat.beat_id!r} names {beat.part!r}, which the catalog does "
                "not carry"
            )
        if beat.part not in slots_by_part:
            raise AssemblyRejected(
                f"beat {beat.beat_id!r} names {beat.part!r}, which has no frozen "
                "slots — nothing measured where its copy would go"
            )

    # Planned first, in full. A shot that cannot render fails here, before a
    # single part is copied into the workspace.
    plan = plan_film(
        [
            Shot(
                part=beat.part or "template",
                motion_seconds=part_durations.get(beat.part) if beat.part else None,
                voice_seconds=beat.voice_seconds,
            )
            for beat in beats
        ],
        frames_per_second=frames_per_second,
        segment_frames_maximum=segment_frames_maximum,
    )

    segments: list[FilmSegment] = []
    for beat, planned in zip(beats, plan.shots):
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
                )
            )
            continue
        frozen = slots_by_part[beat.part]
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
            font_css=font_css_for(beat.part),
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
                slot_budgets=tuple(
                    SlotBudget(
                        index=entry_["index"],
                        usable_width_px=entry_["usableWidthPx"],
                        font_size_px=entry_["fontSizePx"],
                        baseline_overflows_x=entry_["baselineOverflowsX"],
                        baseline_overflows_y=entry_["baselineOverflowsY"],
                    )
                    for entry_ in budget_by_part.get(beat.part, ())
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
