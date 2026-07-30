"""Whether copy fits a slot, decided against what the slot was measured to be.

A slot's limit is a container width in CSS pixels and the font size drawn in it,
not a character count: nine characters is about 9em of Han, 2.3em of
`iiiiiiiii` and 12em of `WWWWWWWWW`, so a count is three different limits
depending on what gets written.

Why the test is relative rather than absolute
----------------------------------------------
Measured 2026-07-28 on the frozen release tree with the packaged Chromium, after
seeking each part's timeline to its end: **14 of the 48 slots already overflow
vertically with their own original copy**. That is the design — a masked reveal
clips its text on purpose. An overflow test that asked "does this overflow"
would refuse a third of the catalog before a single word was replaced. So the
question is whether the copy made it worse.
"""

from __future__ import annotations

import pytest

from automation_tool.executor.motion_authoring.slot_budget import (
    SlotBudget,
    SlotOverflow,
    require_within_budget,
)

FITS = SlotBudget(
    index=13,
    usable_width_px=366,
    font_size_px=58,
    baseline_overflows_x=False,
    baseline_overflows_y=True,
)


def test_copy_that_did_not_make_it_worse_is_accepted() -> None:
    """The baseline already overflows vertically; matching it is not a failure."""
    require_within_budget(FITS, overflows_x=False, overflows_y=True)


def test_copy_that_introduced_a_horizontal_overflow_is_refused() -> None:
    with pytest.raises(SlotOverflow) as failure:
        require_within_budget(FITS, overflows_x=True, overflows_y=True)

    assert "13" in str(failure.value)


def test_copy_that_introduced_a_vertical_overflow_is_refused() -> None:
    baseline = SlotBudget(
        index=21,
        usable_width_px=334,
        font_size_px=24,
        baseline_overflows_x=False,
        baseline_overflows_y=False,
    )

    with pytest.raises(SlotOverflow):
        require_within_budget(baseline, overflows_x=False, overflows_y=True)


def test_copy_that_fixed_a_baseline_overflow_is_accepted() -> None:
    """Shorter copy than the original cannot be a failure."""
    require_within_budget(FITS, overflows_x=False, overflows_y=False)


def test_the_refusal_says_what_the_slot_can_hold() -> None:
    """A refusal the model can act on names the room, not just the verdict."""
    with pytest.raises(SlotOverflow) as failure:
        require_within_budget(FITS, overflows_x=True, overflows_y=True)

    assert "366" in str(failure.value)
    assert "58" in str(failure.value)
