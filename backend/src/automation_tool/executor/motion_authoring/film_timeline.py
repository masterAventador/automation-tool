"""How long each shot runs, and whether the film fits, decided before rendering.

The product owner's correction, 2026-07-27: a film is not as long as the sum of
its parts' animations. Motion is a garnish — it lets content arrive without a
jolt and then holds on its last frame. What it must not do is get cut off
mid-play, which reads as a mistake. So a shot is as long as whichever takes
longer, the line or the motion, and the film is the sum of its shots:

    shot = max(voice seconds, motion seconds)
    film = Σ shots

Content leads, motion backstops.

Why the budget is checked here
------------------------------
Route A lifts the ceiling on a *film*, not on a *render*: one segment is one
render and still meets `SANDBOX_FRAMES_MAXIMUM`. A render that exceeds it is
killed by the stall guard, and what the person waiting sees is a failure with no
reason attached. Everything needed to know that in advance — the part's declared
duration and the measured length of the synthesized narration — exists before a
browser starts, so the refusal belongs here, naming the shot and the number.

(English docstring for the reason `part_document.py` gives:
`check_user_facing_branding.py` reads Chinese-bearing literals in a `.py` source
as operator copy.)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True, slots=True)
class Shot:
    """One beat: a part, how long its motion runs, how long its line takes.

    Both durations are optional and for different reasons. 25 of the catalog's
    parts are components and declare no duration at all — upstream gives blocks
    a stage and a timeline, components neither. And a shot can carry no
    narration: a logo outro says nothing.
    """

    part: str
    motion_seconds: float | None
    voice_seconds: float | None


@dataclass(frozen=True, slots=True)
class PlannedShot:
    """One shot with its length settled, in the unit a render is measured in."""

    part: str
    seconds: float
    frames: int


@dataclass(frozen=True, slots=True)
class FilmPlan:
    """Every shot, and what the whole film comes to."""

    shots: tuple[PlannedShot, ...]
    total_frames: int
    total_seconds: float


@dataclass(frozen=True, slots=True)
class RenderCost:
    """What one render costs in wall clock, from the storyboard contract.

    `wall_seconds_base` is the fixed part — launching the browser, loading the
    page — and `wall_millis_per_frame` the part that scales. Both live in
    `motion-storyboard-duration.v1.json`; they are passed in rather than read
    here so the caller reads the contract once.
    """

    wall_seconds_base: int
    wall_millis_per_frame: int


class FilmOverBudget(RuntimeError):
    """The film cannot be rendered as planned.

    Raised before anything is rendered, and it names which shot and by how much
    — a refusal that only says "too long" leaves the caller to guess which line
    to shorten.
    """


def shot_seconds(shot: Shot) -> float:
    """Whichever takes longer, the line or the motion."""
    candidates = [value for value in (shot.motion_seconds, shot.voice_seconds) if value]
    if not candidates:
        raise FilmOverBudget(
            f"shot {shot.part!r} has neither narration nor motion, so nothing "
            "decides how long it is"
        )
    return max(candidates)


def plan_film(
    shots: Sequence[Shot], *, frames_per_second: int, segment_frames_maximum: int
) -> FilmPlan:
    """Settle every shot's length, or refuse and say which one does not fit."""
    if not shots:
        raise FilmOverBudget("a film needs at least one shot")
    planned: list[PlannedShot] = []
    for shot in shots:
        seconds = shot_seconds(shot)
        # Rounded up: a shot of 4.81s needs 145 frames at 30fps, and 144 would
        # clip the end of the line. Losing up to a frame of speech per shot
        # accumulates across a film and is heard rather than seen.
        frames = math.ceil(seconds * frames_per_second)
        if frames > segment_frames_maximum:
            raise FilmOverBudget(
                f"shot {shot.part!r} runs {seconds:g}s, which is {frames} frames — "
                f"one render may capture {segment_frames_maximum}"
            )
        planned.append(PlannedShot(part=shot.part, seconds=seconds, frames=frames))
    return FilmPlan(
        shots=tuple(planned),
        total_frames=sum(shot.frames for shot in planned),
        total_seconds=sum(shot.seconds for shot in planned),
    )


def estimate_render_cost(plan: FilmPlan, cost: RenderCost) -> float:
    """Roughly how long this film will take to render, in seconds.

    The fixed cost is per *segment*, not per film. Each shot is its own render
    with its own browser launch, so a twenty shot film pays it twenty times —
    ten minutes before a frame is captured. Charging it once would tell the
    person waiting a number less than half the truth, which is worse than not
    telling them at all.

    This is the price route A pays for an unbounded film length, and it is the
    number the submit screen should show.
    """
    return sum(
        cost.wall_seconds_base + shot.frames * cost.wall_millis_per_frame / 1000
        for shot in plan.shots
    )


__all__ = [
    "FilmOverBudget",
    "RenderCost",
    "estimate_render_cost",
    "FilmPlan",
    "PlannedShot",
    "Shot",
    "plan_film",
    "shot_seconds",
]
