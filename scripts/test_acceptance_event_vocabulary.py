#!/usr/bin/env python3
"""Every event name an acceptance driver spells must exist in the contracts.

Why this exists
---------------
`run_d6_11_acceptance.py`, `run_d6_12_acceptance.py` and `run_h8_16f_acceptance.py`
assert on Task timelines by writing the event names out as string literals — four,
five and seven of them. Those literals are a hand-made copy of a vocabulary that
lives somewhere else, and nothing connected the copy to the original.

The copy going stale is not a loud failure. These three drivers need Docker, a
real PostgreSQL, a built Tauri App and minutes of wall clock; they are not in the
aggregate suite and not in CI. So renaming an event in the contract leaves the
drivers spelling a name the product no longer emits, and the only thing that
would say so is a run nobody performs. Meanwhile the literal reads exactly like a
name that works.

What is derived, and from where
-------------------------------
Both sides are re-derived on every run, so neither can fall behind:

* **The vocabulary** comes from the two authorities themselves — the closed
  `TaskEventType` enum (Python is the single source for Task events, project rule
  §3) and the `message_type` const/enum values in the Executor protocol schema.
* **The declarations** come from globbing `scripts/run_*.py` and reading the
  literals out of the source. No driver is named here, so a driver added
  tomorrow is covered the day it is written.

This file therefore holds no list of event names and no list of drivers. It
cannot become the fourth hand-maintained copy of the thing it checks.

What it does not check
----------------------
Whether a driver expects the *right* events in the *right order* — that is what
the drivers themselves prove when they run. This check answers the cheaper
question that nothing else asks: does every name a driver spells still exist?
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Final

REPOSITORY_ROOT: Final = Path(__file__).resolve().parent.parent
EXECUTOR_SCHEMA: Final = REPOSITORY_ROOT / "contracts/protocol/executor-v1.schema.json"

# `task.`/`step.`/`action.` prefixed literals are the Task timeline and Executor
# protocol vocabularies. Anything else in a driver is a different kind of string.
#
# The suffix character class is deliberately wider than the names in use, which
# are all lowercase and underscored. A narrow `[a-z_]+` makes this check blind in
# exactly the case it exists for: rename an event to `task.targets_confirmed_v2`
# and the literal stops matching, the name drops out of the scan, and the run
# reports success while checking one name fewer. Measured — the first mutation
# run of this file passed green against a driver that named a nonexistent event.
_EVENT_LITERAL: Final = re.compile(r'"((?:task|step|action)\.[A-Za-z0-9_.\-]+)"')

# A vocabulary this small cannot be right if either authority reads as empty; an
# empty authority would make every declaration "unknown", and an empty scan would
# make this file pass while checking nothing at all. Both are treated as a broken
# run rather than a result. The floors are deliberately far below the real counts
# (22 events, 31 message types, 18 names in use) — they catch a parser that
# stopped working, not growth or pruning of the vocabulary.
_MINIMUM_TIMELINE_EVENTS: Final = 8
_MINIMUM_PROTOCOL_MESSAGES: Final = 8
_MINIMUM_DECLARED_NAMES: Final = 5


def timeline_event_vocabulary() -> set[str]:
    """The closed Task event vocabulary, read from the enum that defines it."""
    from automation_tool.control_plane.domain.task_events import TaskEventType

    return {member.value for member in TaskEventType}


def protocol_message_vocabulary(schema_path: Path) -> set[str]:
    """Every `message_type` the Executor protocol schema admits."""
    discovered: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "message_type" and isinstance(value, dict):
                    constant = value.get("const")
                    if isinstance(constant, str):
                        discovered.add(constant)
                    for choice in value.get("enum") or []:
                        if isinstance(choice, str):
                            discovered.add(choice)
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(json.loads(schema_path.read_text(encoding="utf-8")))
    return discovered


def declared_event_names(repository_root: Path) -> dict[str, set[str]]:
    """Event names spelled by each acceptance driver, derived from the sources."""
    declarations: dict[str, set[str]] = {}
    for driver in sorted((repository_root / "scripts").glob("run_*.py")):
        names = set(_EVENT_LITERAL.findall(driver.read_text(encoding="utf-8")))
        if names:
            declarations[driver.name] = names
    return declarations


def main() -> int:
    timeline = timeline_event_vocabulary()
    protocol = protocol_message_vocabulary(EXECUTOR_SCHEMA)
    if len(timeline) < _MINIMUM_TIMELINE_EVENTS:
        raise AssertionError(
            f"only {len(timeline)} Task event types were read from the enum; the "
            "vocabulary cannot be that small, so this run proves nothing"
        )
    if len(protocol) < _MINIMUM_PROTOCOL_MESSAGES:
        raise AssertionError(
            f"only {len(protocol)} message types were read from {EXECUTOR_SCHEMA.name}; "
            "the schema walk is broken, so this run proves nothing"
        )

    vocabulary = timeline | protocol
    declarations = declared_event_names(REPOSITORY_ROOT)
    declared = {name for names in declarations.values() for name in names}
    if len(declared) < _MINIMUM_DECLARED_NAMES:
        raise AssertionError(
            f"only {len(declared)} event names were found across {len(declarations)} "
            "drivers; the source scan is broken, so this run proves nothing"
        )

    unknown = {
        driver: sorted(names - vocabulary)
        for driver, names in declarations.items()
        if names - vocabulary
    }
    if unknown:
        detail = "; ".join(
            f"{driver} spells {', '.join(names)}" for driver, names in sorted(unknown.items())
        )
        raise AssertionError(
            "an acceptance driver names events that no contract defines, so it "
            f"asserts on a vocabulary the product does not emit: {detail}"
        )

    print(
        f"{len(declared)} event names across {len(declarations)} acceptance drivers "
        f"all exist in the {len(vocabulary)} name contract vocabulary"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
