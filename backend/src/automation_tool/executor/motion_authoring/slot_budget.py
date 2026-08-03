"""What one slot can hold, and whether this film's copy made it worse.

A slot's limit is a container width in CSS pixels and the font size drawn in it.
Not a character count: nine characters is about 9em of Han, 2.3em of
`iiiiiiiii` and 12em of `WWWWWWWWW`, so a count is three different limits
depending on what gets written into it.

Why the overflow test is relative
----------------------------------
Measured 2026-07-28 on the frozen release tree with the packaged Chromium, after
seeking each part's own timeline to its end: 14 of the 48 slots already overflow
vertically with their *original* copy. That is the design — a masked reveal
clips its text deliberately. A test asking "does this overflow" would refuse a
third of the catalog before a word was replaced, so the question here is only
whether the copy made it worse than the part shipped.

(English docstring for the reason `part_document.py` gives:
`check_user_facing_branding.py` reads Chinese-bearing literals in a `.py` source
as operator copy.)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SlotBudget:
    """One slot as `frontend/scripts/measure-motion-part-slots.mjs` measured it."""

    index: int
    usable_width_px: int
    font_size_px: int
    baseline_overflows_x: bool
    baseline_overflows_y: bool


class SlotOverflow(RuntimeError):
    """This film's copy overflows a slot the original copy did not.

    Carries the room the slot has, because a refusal the model can act on has to
    say how much shorter to write, not only that it was too long.
    """


def require_within_budget(budget: SlotBudget, *, overflows_x: bool, overflows_y: bool) -> None:
    """Accept copy that is no worse than what the part shipped with."""
    introduced = []
    if overflows_x and not budget.baseline_overflows_x:
        introduced.append("horizontally")
    if overflows_y and not budget.baseline_overflows_y:
        introduced.append("vertically")
    if not introduced:
        return
    raise SlotOverflow(
        f"copy overflows slot {budget.index} {' and '.join(introduced)}; the slot "
        f"is {budget.usable_width_px}px wide at {budget.font_size_px}px type, and "
        "the part's own copy fits"
    )


__all__ = ["SlotBudget", "SlotOverflow", "require_within_budget"]
