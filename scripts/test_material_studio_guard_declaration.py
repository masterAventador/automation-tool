#!/usr/bin/env python3
"""IM-06's window guard declaration must keep up with the guard it declares.

Why this exists
---------------
`run_im_06_acceptance.py` pins the embedded studio window's guard by naming
markers that must appear in `material_video_studio_init.js`. That direction is
already checked, and loudly: a marker that disappears from the script fails the
driver. The direction nothing checked is the other one — **the script growing a
guard the declaration never hears about**.

That asymmetry is what makes a hand-copied list dangerous. Seven markers were
copied out of a script that runs seven pipeline steps and can fail closed three
ways; the declaration covered two steps and two reasons. Add an eighth guard
tomorrow and every check stays green while covering less of the script than the
day before. Under-checking and passing look identical from outside.

What is derived, and from where
-------------------------------
The full set is re-read from the script on every run:

* **Pipeline steps** — the file-level functions `reconcile()` actually invokes.
  Two of them are plumbing rather than guards and are excluded *by role*, not by
  name: the failure sink (the function that receives the fail-closed reason
  literals) and the DOM accessor (a one-expression function returning a
  `document` member). Excluding by role means a rename cannot smuggle plumbing
  into the guard set or a guard out of it.
* **Fail-closed reasons** — every literal handed to that failure sink.

The declaration is imported from the driver, not re-typed here. So this file
holds no marker list of its own; adding a step or a reason to the script changes
what it demands without anyone editing it.

The second copy
---------------
`material_video_studio.rs`'s `theme_tests` pins the same script with its own
hand-written list. Two lists over one source drift apart silently, so every
marker the Rust side pins must also appear in the driver's declaration — the
driver stays the superset, and a marker cannot exist that only one side knows
about.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Final

REPOSITORY_ROOT: Final = Path(__file__).resolve().parent.parent
INIT_SCRIPT: Final = REPOSITORY_ROOT / "frontend/src-tauri/src/material_video_studio_init.js"
RUST_MODULE: Final = REPOSITORY_ROOT / "frontend/src-tauri/src/material_video_studio.rs"

_FILE_LEVEL_FUNCTION: Final = re.compile(r"^  const ([A-Za-z_$][\w$]*) = \(", re.M)
_RECONCILE_BODY: Final = re.compile(r"^  const reconcile = \(\) => \{$(.*?)^  \};$", re.M | re.S)
_CALL: Final = re.compile(r"\b([A-Za-z_$][\w$]*)\(")
# A call whose first argument opens with a string or template literal. The
# function on the receiving end of such literals is the failure sink.
_LITERAL_CALL: Final = re.compile(r"\b([A-Za-z_$][\w$]*)\(\s*[`\"']([^`\"'$]*)")
# `const name = () => document.…;` on one line: an accessor, not a guard step.
_DOM_ACCESSOR: Final = re.compile(r"^  const ([A-Za-z_$][\w$]*) = \(\) => document\.[^;]*;$", re.M)
_RUST_MARKER_BLOCK: Final = re.compile(r"for required in \[(.*?)\] \{", re.S)
_RUST_STRING: Final = re.compile(r'"((?:[^"\\]|\\.)*)"')

# Floors, not expectations: they catch a parser that stopped matching, which
# would otherwise make this file demand nothing and report success. Far below the
# real counts (7 steps, 3 reasons, 6 Rust markers).
_MINIMUM_STEPS: Final = 4
_MINIMUM_REASONS: Final = 2
_MINIMUM_RUST_MARKERS: Final = 3


def _reconcile_body(source: str) -> str:
    body = _RECONCILE_BODY.search(source)
    if body is None:
        raise AssertionError(
            f"{INIT_SCRIPT.name} has no reconcile pipeline to read; this check "
            "would demand nothing and pass"
        )
    return body.group(1)


def failure_sink(source: str) -> str:
    """The function that receives fail-closed reason literals."""
    functions = set(_FILE_LEVEL_FUNCTION.findall(source))
    sinks: set[str] = {name for name, _ in _LITERAL_CALL.findall(source) if name in functions}
    if len(sinks) != 1:
        raise AssertionError(
            f"expected exactly one fail-closed sink in {INIT_SCRIPT.name}, found {sorted(sinks)}"
        )
    return sinks.pop()


def guard_pipeline_steps(source: str) -> set[str]:
    """Guard steps the reconcile pipeline runs, minus the sink and the accessor."""
    functions = set(_FILE_LEVEL_FUNCTION.findall(source))
    plumbing = {failure_sink(source), *_DOM_ACCESSOR.findall(source)}
    return {
        name
        for name in _CALL.findall(_reconcile_body(source))
        if name in functions and name not in plumbing
    }


def fail_closed_reasons(source: str) -> set[str]:
    """Every reason the guard can fail closed with, template prefixes included."""
    sink = failure_sink(source)
    return {reason for name, reason in _LITERAL_CALL.findall(source) if name == sink and reason}


def rust_declared_markers() -> set[str]:
    """The second hand-written list, read out of the Rust theme test."""
    block = _RUST_MARKER_BLOCK.search(RUST_MODULE.read_text(encoding="utf-8"))
    if block is None:
        raise AssertionError(f"{RUST_MODULE.name} no longer declares a theme marker list")
    return {value.replace("\\\\", "\\") for value in _RUST_STRING.findall(block.group(1))}


def main() -> int:
    sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))
    from run_im_06_acceptance import INITIALIZATION_GUARD_MARKERS

    source = INIT_SCRIPT.read_text(encoding="utf-8")
    steps = guard_pipeline_steps(source)
    reasons = fail_closed_reasons(source)
    rust_markers = rust_declared_markers()
    if len(steps) < _MINIMUM_STEPS or len(reasons) < _MINIMUM_REASONS:
        raise AssertionError(
            f"read only {len(steps)} guard steps and {len(reasons)} fail-closed reasons "
            f"from {INIT_SCRIPT.name}; the derivation is broken, so this run proves nothing"
        )
    if len(rust_markers) < _MINIMUM_RUST_MARKERS:
        raise AssertionError(
            f"read only {len(rust_markers)} markers from {RUST_MODULE.name}; the "
            "derivation is broken, so this run proves nothing"
        )

    declared = set(INITIALIZATION_GUARD_MARKERS)
    uncovered = sorted((steps | reasons) - declared)
    if uncovered:
        raise AssertionError(
            f"{INIT_SCRIPT.name} guards the window with mechanisms IM-06 never pins, so "
            f"IM-06 now covers less of the guard than the script implements: {', '.join(uncovered)}"
        )

    stale = sorted(marker for marker in declared if marker not in source)
    if stale:
        raise AssertionError(
            f"IM-06 pins markers {INIT_SCRIPT.name} no longer contains, so those pins "
            f"assert nothing about the shipped guard: {', '.join(stale)}"
        )

    orphaned = sorted(rust_markers - declared)
    if orphaned:
        raise AssertionError(
            f"{RUST_MODULE.name} pins markers IM-06 does not, so the two lists over one "
            f"script have started to drift: {', '.join(orphaned)}"
        )

    print(
        f"IM-06 pins all {len(steps)} guard steps, all {len(reasons)} fail-closed reasons "
        f"and all {len(rust_markers)} markers of the Rust list"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
