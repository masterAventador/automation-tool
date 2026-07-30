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

Why the judgement compares pixel *excesses*, with a grace on the difference
----------------------------------------------------------------------------
Two rounds of real measurement on 2026-07-30, both against the packaged
Chromium and the frozen release tree:

1. A CJK line box is about 11–12% of the type size taller than the Latin line
   box the parts were designed around: lt-clean-bar's body slot is 32px tall
   around its own Latin copy, and a four-character Chinese line measures 35px
   in it. Under a boolean `scrollHeight > clientHeight` judgement *any* Chinese
   copy — the product's primary language — reads as new overflow in 5 of the
   37 anchored parts, and the repair round then asks the model for something
   shortening can never deliver.
2. The baseline often carries a few pixels of excess of its own (lt-clean-bar's
   headline slot reads 59/55 around its Latin original). A grace applied to the
   substituted document's *absolute* excess is eaten by that baseline excess,
   and the CJK line-box difference stacked on top misjudges again (measured:
   4px baseline excess + 6px script difference at 52px type).

So the rule is a difference rule: the substituted document's excess may exceed
the original document's excess by at most a grace —

* horizontally: 1px, the same rounding allowance the frozen budget probe uses
  (`measure-motion-part-slots.mjs` judges `> clientWidth + 1`);
* vertically: max(1, round(15% × font size)) — covers the measured script
  line-box difference, while an extra wrapped line (≥120% of the font size)
  is far past it and still fails.

The probe finds slots by the `data-motion-slot` marks the working-copy writer
stamps (`part_workspace._slot_marks`); numbering text runs again in JavaScript
would be the same misplacement risk that module refuses everywhere else.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Final, Mapping, Sequence

from .slot_budget import SlotBudget, SlotOverflow, require_within_budget

# Returns `{"slots": {"<index>": [scrollWidth, clientWidth, scrollHeight,
# clientHeight]}, "stage": [documentWidth, documentHeight]}`. A mark listing
# several indices (slots sharing one box) reports the same box for each of
# them — they share its overflow, as `_slot_marks` already states. Pixels, not
# booleans: the grace depends on the slot's type size, which only the
# budget-holding side knows. The document extent is what catches the parts
# whose boxes auto-grow — their box-level numbers never move while the copy
# pushes the whole part past the stage edge (measured: lt-clean-bar's bar at
# 3019px on a 1920px stage, scrollWidth == clientWidth throughout).
SLOT_PROBE_JS: Final = """
() => {
  const measured = {};
  for (const element of document.querySelectorAll("[data-motion-slot]")) {
    const reading = [
      element.scrollWidth, element.clientWidth,
      element.scrollHeight, element.clientHeight,
    ];
    for (const index of element.getAttribute("data-motion-slot").split(" ")) {
      measured[index] = reading;
    }
  }
  return {
    slots: measured,
    stage: [
      document.documentElement.scrollWidth,
      document.documentElement.scrollHeight,
    ],
  };
}
"""


@dataclass(frozen=True, slots=True)
class ProbeReading:
    """One document, as the probe measured it: every marked box, plus the whole.

    `stage` is the document's own extent, not the declared canvas: both
    documents are compared against each other, so the judgement stays relative
    and driver-independent — the same reason the slot readings are.
    """

    slots: Mapping[int, Sequence[int]]
    stage: tuple[int, int]

# The rounding grace the frozen budget probe already applies horizontally.
_WIDTH_GRACE_PX: Final = 1
# The measured CJK-versus-Latin line-box difference is 11–12% of the type
# size; 15% covers it with margin while staying far below one wrapped line.
_LINE_BOX_GRACE_RATIO: Final = 0.15


class SlotProbeRejected(RuntimeError):
    """The film's copy overflows meaningfully more than the part's own copy.

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


def _excesses(measurement: Sequence[int]) -> tuple[int, int]:
    scroll_width, client_width, scroll_height, client_height = measurement
    return (
        max(0, int(scroll_width) - int(client_width)),
        max(0, int(scroll_height) - int(client_height)),
    )


def _measurement(
    reading: ProbeReading, budget: SlotBudget, document: str
) -> Sequence[int]:
    measurement = reading.slots.get(budget.index)
    if measurement is None:
        raise SlotProbeUnmeasured(
            f"the {document} document's probe did not measure slot "
            f"{budget.index}; a missing measurement must not pass as a "
            "fitting one"
        )
    return measurement


def _stage_escapes(
    budgets: Sequence[SlotBudget],
    original: ProbeReading,
    substituted: ProbeReading,
) -> list[str]:
    """Did the copy push the whole part further past the stage than its own did?

    Judged relatively, like everything else here: the original document's
    extent *is* the stage for a well-formed part (measured 1920×1080 for every
    baseline today), and a baseline that already runs beyond it stays the
    baseline. The vertical grace follows the document's largest judged type
    size — the CJK line-box difference propagates to the document extent the
    same way it does to a box.
    """
    grace_y = max(
        1,
        round(max(budget.font_size_px for budget in budgets) * _LINE_BOX_GRACE_RATIO),
    )
    findings = []
    for axis, direction, grace in ((0, "horizontally", _WIDTH_GRACE_PX), (1, "vertically", grace_y)):
        if substituted.stage[axis] > original.stage[axis] + grace:
            findings.append(
                f"copy pushes the part beyond its own stage {direction}; the "
                f"part's own copy ends at {original.stage[axis]}px and this "
                f"copy reaches {substituted.stage[axis]}px"
            )
    return findings


def require_no_new_overflow(
    budgets: Sequence[SlotBudget],
    original: ProbeReading,
    substituted: ProbeReading,
) -> None:
    """Every slot at once: the model repairs from the full list, not the first.

    `require_within_budget` decides one slot and words the finding; this
    collects every offender so the repair round carries all of them — retrying
    per-slot would spend one model round per slot for information this probe
    already has.
    """
    offences: list[str] = []
    for budget in budgets:
        original_x, original_y = _excesses(_measurement(original, budget, "original"))
        substituted_x, substituted_y = _excesses(
            _measurement(substituted, budget, "substituted")
        )
        vertical_grace = max(1, round(budget.font_size_px * _LINE_BOX_GRACE_RATIO))
        try:
            require_within_budget(
                # The frozen baseline booleans came off another driver; the
                # comparison here is same-session by construction, so the
                # judged budget's baseline is "no worse than the original".
                replace(budget, baseline_overflows_x=False, baseline_overflows_y=False),
                overflows_x=substituted_x > original_x + _WIDTH_GRACE_PX,
                overflows_y=substituted_y > original_y + vertical_grace,
            )
        except SlotOverflow as overflow:
            offences.append(str(overflow))
    offences.extend(_stage_escapes(budgets, original, substituted))
    if offences:
        raise SlotProbeRejected("; ".join(offences))


__all__ = [
    "SLOT_PROBE_JS",
    "ProbeReading",
    "SlotProbeRejected",
    "SlotProbeUnmeasured",
    "require_no_new_overflow",
]
