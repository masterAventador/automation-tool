#!/usr/bin/env python3
"""Gate: the measured slot budget stays in step with the frozen slot table.

The budget is produced by a browser and cannot be re-derived without one, so
this gate does not re-measure. What it does check is the pair of ways the file
can rot: a slot that no longer exists in `motion-part-slots.v1.json`, and a slot
in that table with no budget measured for it. Either one leaves the model
writing copy against a limit that describes a different slot.

Run `frontend/scripts/measure-motion-part-slots.mjs` after changing the slot
table or the release tree, then run this.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SLOTS = REPOSITORY_ROOT / "contracts/video/motion-part-slots.v1.json"
BUDGET = REPOSITORY_ROOT / "contracts/video/motion-part-slot-budget.v1.json"
PROBE = REPOSITORY_ROOT / "frontend/scripts/measure-motion-part-slots.mjs"


def fail(message: str) -> None:
    raise SystemExit(f"motion part slot budget check failed: {message}")


def main() -> int:
    if not PROBE.is_file():
        fail(f"the probe that produces this budget is missing: {PROBE.name}")
    slots = json.loads(SLOTS.read_text(encoding="utf-8"))
    budget = json.loads(BUDGET.read_text(encoding="utf-8"))

    declared = {
        (part["name"], slot["index"]) for part in slots["parts"] for slot in part["slots"]
    }
    measured = {
        (part["name"], slot["index"]) for part in budget["parts"] for slot in part["slots"]
    }
    missing = sorted(declared - measured)
    if missing:
        fail(
            f"{len(missing)} frozen slot(s) have no measured budget, first {missing[0]}; "
            "re-run frontend/scripts/measure-motion-part-slots.mjs"
        )
    stale = sorted(measured - declared)
    if stale:
        fail(f"{len(stale)} measured slot(s) no longer exist, first {stale[0]}")

    originals = {
        (part["name"], slot["index"]): slot["original"]
        for part in slots["parts"]
        for slot in part["slots"]
    }
    for part in budget["parts"]:
        for slot in part["slots"]:
            key = (part["name"], slot["index"])
            if slot["original"] != originals[key]:
                fail(
                    f"{key} was measured against different copy than the slot table "
                    "froze; the budget describes another slot"
                )
            for field in ("usableWidthPx", "fontSizePx"):
                if not isinstance(slot[field], int) or slot[field] <= 0:
                    fail(f"{key} has a non-positive {field}, which no container has")

    counts = budget["counts"]
    if counts["slots"] != len(measured) or counts["parts"] != len(budget["parts"]):
        fail("the declared counts do not match the measured entries")
    baseline = sum(
        1 for part in budget["parts"] for slot in part["slots"] if slot["baselineOverflowsY"]
    )
    if counts["baselineOverflowingY"] != baseline:
        fail("the baseline overflow count does not match the measured entries")

    print(
        "motion part slot budget is valid: "
        f"{counts['parts']} parts / {counts['slots']} slots, "
        f"{baseline} already clipping vertically with their own copy"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
