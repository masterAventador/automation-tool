#!/usr/bin/env python3
"""Boundary tests for the shot-structure judgement used in T36 acceptance.

Why this exists
---------------
Two different defects hid behind a film whose every mechanical reading was
correct, and neither was caught by anything that reads the container:

* PC-19 — a 12-second film that was one six-second stretch played twice.
  Codec, canvas, frame count, duration and the still-image gate were all green,
  because each asks about the *file* and none asks whether the picture moves
  on. Every template shot loaded the same composition and rendered all of it.
* PC-10 — the evidence file described a film with eight 2.5-second shots while
  the artifact has seven shots of 3/3/3/3/3/15/2. Nobody noticed for a day,
  because the shot table was typed by hand from memory of a different run.

The first needs a check the product runs; the second needs the shot table to be
*printed by the acceptance script* rather than transcribed by a person. Both are
served by the same small module.

Why the judgement is separated from the measurement
---------------------------------------------------
Sampling frames needs the packaged ffmpeg, which only exists after a release
staging step — a test that depends on it would have to skip when it is absent,
and a test that skips is a test that cannot fail. So the arithmetic and the
refusal live here as pure functions and are always exercised; the thin wrapper
that shells out to ffmpeg is the part the real acceptance run covers.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from motion_shot_structure import (  # noqa: E402
    ShotStructureRejected,
    describe_shots,
    plan_shots,
    require_declared_shot_boundaries,
    refuse_duplicate_shots,
)

CHECKS = 0


def check(condition: bool, message: str) -> None:
    global CHECKS
    CHECKS += 1
    assert condition, message


def expect_rejected(name: str, action) -> str:
    global CHECKS
    CHECKS += 1
    try:
        action()
    except ShotStructureRejected as rejected:
        return str(rejected)
    raise AssertionError(f"{name}: expected a refusal")


def test_shots_are_laid_out_end_to_end() -> None:
    """The film's own layout: each shot starts where the previous one ended."""
    shots = plan_shots([3.0, 3.0, 15.0, 2.0])
    check([shot.start_seconds for shot in shots] == [0.0, 3.0, 6.0, 21.0], "starts")
    check([shot.seconds for shot in shots] == [3.0, 3.0, 15.0, 2.0], "durations")
    # The point of a midpoint rather than a start: every shot begins with its
    # entry animation, and in the measured film all six template shots start on
    # the identical background-only frame. Sampling starts would call a healthy
    # film a duplicate.
    check([shot.midpoint_seconds for shot in shots] == [1.5, 4.5, 13.5, 22.0], "midpoints")


def test_a_film_with_no_shots_is_refused() -> None:
    message = expect_rejected("no shots", lambda: plan_shots([]))
    check("no shots" in message, f"message should say what is wrong: {message}")


def test_a_zero_length_shot_is_refused() -> None:
    expect_rejected("zero-length shot", lambda: plan_shots([3.0, 0.0]))


def test_distinct_shots_pass() -> None:
    shots = plan_shots([3.0, 3.0, 15.0, 2.0])
    refuse_duplicate_shots(shots, ["a", "b", "c", "d"])


def test_two_shots_showing_the_same_picture_are_refused() -> None:
    """Two beats that render the same thing — named, so the log says which."""
    shots = plan_shots([3.0, 3.0, 3.0])
    message = expect_rejected(
        "duplicate shots",
        lambda: refuse_duplicate_shots(shots, ["a", "b", "a"]),
    )
    check("1" in message and "3" in message, f"both shots must be named: {message}")


def test_the_pc19_shape_is_refused() -> None:
    """Every shot re-rendering the whole composition: all midpoints coincide.

    This is the defect that survived nine acceptance runs. It is refused here
    without any threshold to tune — two frames are either byte-identical or they
    are not.
    """
    shots = plan_shots([2.0, 2.0, 2.0, 2.0, 2.0, 2.0])
    expect_rejected(
        "whole composition re-rendered per shot",
        lambda: refuse_duplicate_shots(shots, ["same"] * 6),
    )


def test_a_digest_per_shot_is_required() -> None:
    """Fewer samples than shots means part of the film was never looked at."""
    shots = plan_shots([3.0, 3.0])
    expect_rejected("short sample list", lambda: refuse_duplicate_shots(shots, ["a"]))


def test_the_table_states_what_a_reader_can_check() -> None:
    table = describe_shots(plan_shots([3.0, 15.0, 2.0]))
    check("shot 1" in table and "shot 3" in table, f"every shot is listed: {table}")
    check("15.000" in table, f"durations are exact, not rounded away: {table}")
    # 3 + 15 = 18: the third shot starts after the long one, not after a
    # nominal beat length. Getting this wrong on paper is what produced the
    # PC-10 mis-transcription in the first place.
    check("18.000" in table, f"starts accumulate: {table}")
    check("20.000s total" in table, f"the total is stated: {table}")


def test_declared_and_decoded_shot_boundaries_may_differ_by_one_frame() -> None:
    """Container rounding may move one boundary, but never the next one too."""
    shots = require_declared_shot_boundaries(
        declared_frames=[90, 450, 60],
        rendered_frames=[90, 451, 59],
        frames_per_second=30,
        tolerance_frames=1,
    )
    expected_starts = [0.0, 3.0, 541 / 30]
    check(
        all(
            abs(actual.start_seconds - expected) < 1e-9
            for actual, expected in zip(shots, expected_starts)
        ),
        f"decoded starts are returned for the acceptance table: {shots}",
    )
    expected_seconds = [3.0, 451 / 30, 59 / 30]
    check(
        all(
            abs(actual.seconds - expected) < 1e-9
            for actual, expected in zip(shots, expected_seconds)
        ),
        f"decoded lengths are returned for the acceptance table: {shots}",
    )


def test_cumulative_boundary_drift_is_refused_even_if_each_shot_is_only_one_frame_off() -> None:
    """Two +1 shot errors make the third boundary +2; per-shot checks miss it."""
    message = expect_rejected(
        "cumulative boundary drift",
        lambda: require_declared_shot_boundaries(
            declared_frames=[90, 450, 60],
            rendered_frames=[91, 451, 58],
            frames_per_second=30,
            tolerance_frames=1,
        ),
    )
    check("shot 2" in message and "2 frames" in message, message)


def main() -> int:
    for name, function in sorted(globals().items()):
        if name.startswith("test_") and callable(function):
            function()
    print("motion shot structure tests passed")
    print(f"executed checks: {CHECKS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
