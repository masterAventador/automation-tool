"""Same-session overflow comparison: the original document versus this film's.

Why the baseline is re-measured instead of trusted (PC-14, decision 3)
----------------------------------------------------------------------
The frozen baseline in `motion-part-slot-budget.v1.json` was measured by a Node
probe; a Playwright-driven Chromium reads the same slot a few pixels
differently, systematically. Comparing a runtime reading against a frozen
reading would fail parts whose copy changed nothing. So both documents are
measured in the *same* browser session and only the difference matters — the
driver bias cancels itself. The frozen numbers survive as the hint the model
gets back: slot N is this wide at this type size.

The probe finds slots by the `data-motion-slot` marks the working-copy writer
stamps (`part_workspace._slot_marks`); numbering text runs again in JavaScript
would be the same misplacement risk that module refuses everywhere else.
"""

from __future__ import annotations

from typing import Final, Mapping, Sequence

from .slot_budget import SlotBudget, SlotOverflow, require_within_budget

# Returns {"<index>": [overflowsX, overflowsY]} for every marked slot. A mark
# listing several indices (slots sharing one box) reports the same box for each
# of them — they share its overflow, as `_slot_marks` already states.
SLOT_PROBE_JS: Final = """
() => {
  const measured = {};
  for (const element of document.querySelectorAll("[data-motion-slot]")) {
    const overflowsX = element.scrollWidth > element.clientWidth;
    const overflowsY = element.scrollHeight > element.clientHeight;
    for (const index of element.getAttribute("data-motion-slot").split(" ")) {
      measured[index] = [overflowsX, overflowsY];
    }
  }
  return measured;
}
"""


class SlotProbeRejected(RuntimeError):
    """The film's copy overflows where the part's own copy did not.

    Also raised when a measurement is missing: a slot the probe could not see
    must not pass as "does not overflow" — that would turn a broken mark or a
    failed page load into a green result.
    """


class SlotProbeUnmeasured(SlotProbeRejected):
    """A slot the probe should have seen was not in its reading.

    Distinct from an overflow because the two have different next steps: an
    overflow is copy the model can shorten in a repair round, while a missing
    measurement is a broken mark or a failed page load — spending a model round
    on it would ask for a rewrite of copy that was never the problem.
    """


def session_budgets(
    frozen: Sequence[SlotBudget],
    original: Mapping[int, tuple[bool, bool]],
) -> tuple[SlotBudget, ...]:
    """The frozen budgets with their baselines replaced by this session's.

    Width and type size stay frozen — they are the hint the model gets — while
    the overflow baseline comes from measuring the *original* document in the
    same browser session that will measure the substituted one.
    """
    effective = []
    for budget in frozen:
        measurement = original.get(budget.index)
        if measurement is None:
            raise SlotProbeUnmeasured(
                f"the original document's probe did not measure slot {budget.index}; "
                "a missing measurement must not pass as a fitting one"
            )
        overflows_x, overflows_y = measurement
        effective.append(
            SlotBudget(
                index=budget.index,
                usable_width_px=budget.usable_width_px,
                font_size_px=budget.font_size_px,
                baseline_overflows_x=bool(overflows_x),
                baseline_overflows_y=bool(overflows_y),
            )
        )
    return tuple(effective)


def require_no_new_overflow(
    budgets: Sequence[SlotBudget],
    substituted: Mapping[int, tuple[bool, bool]],
) -> None:
    """Every slot at once: the model repairs from the full list, not the first.

    `require_within_budget` decides one slot; this collects every offender so
    the repair round carries all of them — retrying per-slot would spend one
    model round per slot for information this probe already has.
    """
    offences: list[str] = []
    for budget in budgets:
        measurement = substituted.get(budget.index)
        if measurement is None:
            raise SlotProbeUnmeasured(
                f"the substituted document's probe did not measure slot "
                f"{budget.index}; a missing measurement must not pass as a "
                "fitting one"
            )
        overflows_x, overflows_y = measurement
        try:
            require_within_budget(
                budget, overflows_x=bool(overflows_x), overflows_y=bool(overflows_y)
            )
        except SlotOverflow as overflow:
            offences.append(str(overflow))
    if offences:
        raise SlotProbeRejected("; ".join(offences))


__all__ = [
    "SLOT_PROBE_JS",
    "SlotProbeRejected",
    "SlotProbeUnmeasured",
    "require_no_new_overflow",
    "session_budgets",
]
