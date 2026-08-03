"""How long each shot is, and whether the film fits — decided before rendering.

The product owner corrected the model on 2026-07-27: total length is not the sum
of part durations. Motion is a garnish — it makes content arrive without a jolt,
then holds on its last frame. What it must not do is get cut off mid-play, which
looks wrong. So:

    shot = max(as long as this line takes to say, as long as the motion runs)
    film = Σ shots

Content leads, motion backstops. Voice at 8s with 5.5s of motion is an 8s shot
(the motion holds); voice at 3s with 5.5s of motion is a 5.5s shot (the shot
waits for the motion).

Why the budget is checked here and not after
---------------------------------------------
A render that exceeds the sandbox is killed by the stall guard, and what the
person waiting sees is a failure with no reason attached — measured on the frame
ceiling in `motion-render-sandbox-budget.v1.json`. The plan is arithmetic on
numbers already known before a browser starts, so the refusal belongs here,
naming the shot and the amount.
"""

from __future__ import annotations

import pytest

from automation_tool.executor.motion_authoring.film_timeline import (
    FilmOverBudget,
    RenderCost,
    Shot,
    estimate_render_cost,
    plan_film,
    shot_seconds,
)

FPS = 30
SEGMENT_FRAMES_MAXIMUM = 600


def test_a_long_line_over_short_motion_is_as_long_as_the_line() -> None:
    """The motion holds its last frame rather than being stretched.

    Stretching drags; holding reads as deliberate. Measured decision, recorded
    in the roadmap under P0-1d.
    """
    assert shot_seconds(Shot(part="lt-bold-block", motion_seconds=5.5, voice_seconds=8.0)) == 8.0


def test_a_short_line_over_long_motion_waits_for_the_motion() -> None:
    assert shot_seconds(Shot(part="data-chart", motion_seconds=5.5, voice_seconds=3.0)) == 5.5


def test_a_shot_with_no_narration_is_as_long_as_its_motion() -> None:
    """A part can carry a beat on its own — a logo outro says nothing."""
    assert shot_seconds(Shot(part="logo-outro", motion_seconds=6.0, voice_seconds=None)) == 6.0


def test_a_part_with_no_declared_motion_is_as_long_as_the_line() -> None:
    """The 25 components declare no duration; upstream gives them none."""
    assert (
        shot_seconds(Shot(part="caption-kinetic-slam", motion_seconds=None, voice_seconds=4.0))
        == 4.0
    )


def test_a_shot_with_neither_is_refused() -> None:
    with pytest.raises(FilmOverBudget):
        shot_seconds(Shot(part="nothing", motion_seconds=None, voice_seconds=None))


def test_the_film_is_the_sum_of_its_shots() -> None:
    plan = plan_film(
        [
            Shot(part="a", motion_seconds=4.8, voice_seconds=2.0),
            Shot(part="b", motion_seconds=6.0, voice_seconds=7.5),
        ],
        frames_per_second=FPS,
        segment_frames_maximum=SEGMENT_FRAMES_MAXIMUM,
    )

    assert [shot.frames for shot in plan.shots] == [144, 225]
    assert plan.total_frames == 369
    assert plan.total_seconds == pytest.approx(12.3)


def test_frames_round_up_so_the_last_word_is_not_clipped() -> None:
    """A shot 4.81s long needs 145 frames; 144 would cut the line short.

    Rounding down loses up to a frame of speech per shot, which accumulates
    across a film and is heard rather than seen.
    """
    plan = plan_film(
        [Shot(part="a", motion_seconds=None, voice_seconds=4.81)],
        frames_per_second=FPS,
        segment_frames_maximum=SEGMENT_FRAMES_MAXIMUM,
    )

    assert plan.shots[0].frames == 145


def test_a_shot_the_sandbox_cannot_render_is_refused_by_name() -> None:
    """Route A lifts the film ceiling, not the per-segment one.

    One segment is one render, so it still meets `SANDBOX_FRAMES_MAXIMUM`. Five
    of the 134 parts already declare durations past it — all transitions, all
    deferred — and a long narration can push any shot there.
    """
    with pytest.raises(FilmOverBudget) as failure:
        plan_film(
            [
                Shot(part="short", motion_seconds=2.0, voice_seconds=None),
                Shot(part="transitions-dissolve", motion_seconds=24.0, voice_seconds=None),
            ],
            frames_per_second=FPS,
            segment_frames_maximum=SEGMENT_FRAMES_MAXIMUM,
        )

    assert "transitions-dissolve" in str(failure.value)
    assert "720" in str(failure.value)


def test_a_film_of_many_short_shots_is_not_refused() -> None:
    """Route A's whole point: the total is unbounded while each segment is not."""
    plan = plan_film(
        [Shot(part=f"p{n}", motion_seconds=6.0, voice_seconds=None) for n in range(20)],
        frames_per_second=FPS,
        segment_frames_maximum=SEGMENT_FRAMES_MAXIMUM,
    )

    assert plan.total_frames == 3600
    assert plan.total_seconds == pytest.approx(120.0)


def test_an_empty_film_is_refused() -> None:
    with pytest.raises(FilmOverBudget):
        plan_film([], frames_per_second=FPS, segment_frames_maximum=SEGMENT_FRAMES_MAXIMUM)


# --- PC-09：片长上限解除，并说得出要等多久 -----------------------------------

COST = RenderCost(wall_seconds_base=30, wall_millis_per_frame=400)


def test_one_segment_costs_the_base_plus_its_frames() -> None:
    plan = plan_film(
        [Shot(part="a", motion_seconds=12.0, voice_seconds=None)],
        frames_per_second=FPS,
        segment_frames_maximum=SEGMENT_FRAMES_MAXIMUM,
    )

    # 30 + 360 * 0.4
    assert estimate_render_cost(plan, COST) == pytest.approx(174.0)


def test_the_fixed_cost_is_paid_once_per_segment_not_once_per_film() -> None:
    """Route A's price, stated rather than discovered.

    Each segment is its own render: its own browser launch, its own page load.
    `renderWallSecondsBase` is that fixed part, so a film of twenty shots pays
    it twenty times — ten minutes before a single frame is captured. A caller
    that estimated one base per film would tell the user a number less than half
    the real wait.
    """
    shots = [Shot(part=f"p{n}", motion_seconds=6.0, voice_seconds=None) for n in range(20)]
    plan = plan_film(shots, frames_per_second=FPS, segment_frames_maximum=SEGMENT_FRAMES_MAXIMUM)

    # 20 * (30 + 180 * 0.4) = 20 * 102
    assert estimate_render_cost(plan, COST) == pytest.approx(2040.0)
    # And the same frame total rendered as one segment would be far cheaper —
    # which is the cost route A trades for an unbounded film length.
    assert plan.total_frames == 3600


def test_a_film_far_past_the_old_ceiling_is_planned_rather_than_refused() -> None:
    """The 20 second ceiling was one render's frame budget, not a film's.

    `totalSecondsMaximum: 20` in `motion-storyboard-duration.v1.json` is
    600 frames at 30fps — the sandbox's per-render limit. Segmenting removes it
    from the film while leaving it on the segment, which is the whole point of
    route A.
    """
    shots = [Shot(part=f"p{n}", motion_seconds=10.0, voice_seconds=None) for n in range(9)]
    plan = plan_film(shots, frames_per_second=FPS, segment_frames_maximum=SEGMENT_FRAMES_MAXIMUM)

    assert plan.total_seconds == pytest.approx(90.0)
    assert plan.total_frames == 2700
    assert all(shot.frames <= SEGMENT_FRAMES_MAXIMUM for shot in plan.shots)


def test_a_beat_with_no_part_and_no_line_runs_for_the_length_it_declared() -> None:
    """A shot nothing else can measure falls back to what the storyboard said.

    Measured 2026-07-28 against the real model through the real App: with the
    parts catalog finally reaching the agent, the first film it planned died on
    `shot 'template' has neither narration nor motion`. A beat the model left
    without a part has no animation to time, and until the narration is
    synthesized it has no line either — so one such beat failed the whole film,
    and a storyboard is free to contain one.
    """
    shot = Shot(part="template", motion_seconds=None, voice_seconds=None, declared_seconds=4.0)

    assert shot_seconds(shot) == pytest.approx(4.0)


def test_the_declared_length_never_overrides_the_content() -> None:
    """It is a fallback, not a third candidate for the maximum.

    The model's own durations were measured overshooting the sandbox budget by
    more than 70%, which is why a shot is as long as its line or its motion and
    not as long as the model guessed. Letting the declaration win anywhere would
    put that back.
    """
    assert shot_seconds(
        Shot(part="p", motion_seconds=4.8, voice_seconds=None, declared_seconds=20.0)
    ) == pytest.approx(4.8)
    assert shot_seconds(
        Shot(part="p", motion_seconds=None, voice_seconds=2.0, declared_seconds=20.0)
    ) == pytest.approx(2.0)


def test_a_shot_with_nothing_at_all_is_still_refused() -> None:
    with pytest.raises(FilmOverBudget):
        shot_seconds(Shot(part="template", motion_seconds=None, voice_seconds=None))
