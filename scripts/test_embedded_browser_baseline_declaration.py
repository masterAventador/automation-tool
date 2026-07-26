#!/usr/bin/env python3
"""AV-01 must quote every browser rule it claims to hold the line on.

Why this exists
---------------
AV-01's job is a tripwire: the embedded-Chromium decision is written into
CLAUDE.md, the ADR and four architecture documents, and `run_av_01_acceptance.py`
asserts the wording is still there. Against a rule being *deleted* that works and
it is loud.

It does nothing against the baseline being *extended*. The driver quoted five
fragments of CLAUDE.md, of which one landed inside the eleven mandatory browser
rules. Add a twelfth rule tomorrow, or weaken an unquoted one, and AV-01 stays
green — it was only ever holding the line on the fragment it happened to copy.
A tripwire across one of fourteen rules and a tripwire across all fourteen give
the same answer on a clean tree, which is why nobody noticed which one this was.

What is derived, and from where
-------------------------------
The rules are re-read from CLAUDE.md on every run: the fenced baseline block and
the mandatory bullets of the browser section, located by heading text rather than
by section number so renumbering cannot quietly empty the set. The quotes come
from the driver. This file holds neither, so it cannot become the copy that falls
behind.

Two directions are checked:

* **Every rule is quoted.** A rule nothing quotes is a rule AV-01 does not guard.
* **Every quote belongs to exactly one rule.** A fragment matching two rules —
  `内置 Chromium` matched both the baseline block and the first bullet — pins
  neither: it survives either one being rewritten, so it reads like coverage
  while guaranteeing nothing.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Final

REPOSITORY_ROOT: Final = Path(__file__).resolve().parent.parent
CLAUDE_MD: Final = REPOSITORY_ROOT / "CLAUDE.md"

# Located by heading text, not by number: sections get renumbered, and anchoring
# on `## 5.` would silently find nothing the day one is inserted above it.
_BROWSER_SECTION: Final = re.compile(r"^## \d+\.[^\n]*浏览器[^\n]*$(.*?)^## ", re.M | re.S)
_FENCED_BASELINE: Final = re.compile(r"^```text$(.*?)^```$", re.M | re.S)

# A floor, not an expectation: a parser that stopped matching would derive an
# empty rule set, demand nothing of the declaration and report success. The real
# count is 14.
_MINIMUM_RULES: Final = 8


def browser_baseline_rules(document: str) -> list[str]:
    """The mandatory browser rules, read out of CLAUDE.md itself."""
    section = _BROWSER_SECTION.search(document)
    if section is None:
        raise AssertionError(
            "CLAUDE.md has no browser section to read; this check would demand "
            "nothing of AV-01 and pass"
        )
    body = section.group(1)
    rules: list[str] = []
    fenced = _FENCED_BASELINE.search(body)
    if fenced is not None:
        rules.extend(
            line.strip()
            for line in fenced.group(1).splitlines()
            if line.strip() and line.strip() != "+"
        )
    rules.extend(line[2:].strip() for line in body.splitlines() if line.startswith("- "))
    return rules


def main() -> int:
    sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))
    from run_av_01_acceptance import CLAUDE_MD_BROWSER_RULE_QUOTES

    document = CLAUDE_MD.read_text(encoding="utf-8")
    rules = browser_baseline_rules(document)
    if len(rules) < _MINIMUM_RULES:
        raise AssertionError(
            f"only {len(rules)} browser rules were read from CLAUDE.md; the "
            "derivation is broken, so this run proves nothing"
        )

    quotes = tuple(CLAUDE_MD_BROWSER_RULE_QUOTES)
    ambiguous = {
        quote: [rule for rule in rules if quote in rule]
        for quote in quotes
        if len([rule for rule in rules if quote in rule]) != 1
    }
    if ambiguous:
        detail = "; ".join(
            f"{quote!r} matches {len(matched)} rules" for quote, matched in sorted(ambiguous.items())
        )
        raise AssertionError(
            "an AV-01 quote does not pin exactly one browser rule, so rewriting that "
            f"rule would not make AV-01 fail: {detail}"
        )

    unquoted = [rule for rule in rules if not any(quote in rule for quote in quotes)]
    if unquoted:
        detail = "; ".join(f"{rule[:42]}…" for rule in unquoted)
        raise AssertionError(
            f"AV-01 quotes {len(quotes)} of the {len(rules)} mandatory browser rules in "
            f"CLAUDE.md, so it holds the line on only part of the baseline: {detail}"
        )

    print(
        f"AV-01 pins all {len(rules)} CLAUDE.md browser rules with "
        f"{len(quotes)} quotes"
    )
    print("executed checks: 1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
