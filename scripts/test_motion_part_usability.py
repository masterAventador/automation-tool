#!/usr/bin/env python3
"""PC-02 fixed-boundary tests for the 134-item part usability grading.

What this grading is for
------------------------
A catalog entry says what a part *is*. It does not say whether this product can
put the user's words into it — and measured across all 134, that is the fact
that decides everything downstream. Three shapes were found by reading the
sources rather than the manifests:

* 30 transition parts are **demo pages**, not shots. Rendered as-is they read
  "SCENE A | SCENE B / Chromatic Radial Split / Prompt / use chromatic radial
  split shader transition". Using one means moving the technique into your own
  two scenes, which is a code-writing job, not a copy-substitution job.
* Some parts keep their copy in **JavaScript**, with per-word timestamps
  (`caption-kinetic-slam` carries 28 words each with start/end plus a keyword
  set addressed by index). Substituting copy there means re-timing an
  animation, not replacing a string.
* The rest keep it in the **DOM**, where a slot table can address it.

An earlier plan batched parts by "how many text nodes they have", counted with
a regex over the whole file. That put the 15 caption parts in the easy batch —
their only match was the `<title>`, and their bodies contain no addressable
copy at all. This grading is computed with the shipped parser instead, so the
count and the substitution agree by construction.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "scripts/check_motion_part_usability.py"
CONTRACT = ROOT / "contracts/video/motion-part-usability.v1.json"
CATALOG = ROOT / "contracts/quality/motion-catalog.v1.json"

COPY_LOCATIONS = {"dom", "mixed", "script", "none"}
BATCHES = {"first", "second", "deferred"}


def run_check(contract: Path = CONTRACT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECK), "--contract", str(contract)],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=ROOT,
    )


def expect_failure(name: str, contract: dict) -> None:
    with tempfile.TemporaryDirectory(prefix="automation-tool-pc02-test-") as temporary:
        path = Path(temporary) / "usability.json"
        path.write_text(json.dumps(contract, ensure_ascii=False), encoding="utf-8")
        result = run_check(path)
        assert result.returncode != 0, f"{name}: tampered contract must fail"
        assert "part usability check failed" in result.stderr, f"{name}: {result.stderr}"


def main() -> int:
    assert CHECK.is_file(), "scripts/check_motion_part_usability.py is missing"
    assert CONTRACT.is_file(), "contracts/video/motion-part-usability.v1.json is missing"

    green = run_check()
    assert green.returncode == 0, f"real contract must pass: {green.stderr}"

    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))

    assert contract["schemaVersion"] == 1
    assert contract["policy"] == "fail_closed"
    assert contract["source"]["commit"] == catalog["source"]["commit"], (
        "the grading must be pinned to the same upstream commit as the catalog"
    )

    items = contract["items"]
    names = [item["name"] for item in items]
    assert len(items) == 134
    assert names == [item["name"] for item in catalog["items"]], (
        "the grading must cover exactly the catalog items, in the same order"
    )

    for item in items:
        assert item["copyLocation"] in COPY_LOCATIONS, item["name"]
        assert item["batch"] in BATCHES, item["name"]
        for flag in ("demoPage", "lengthSensitive", "autoFitsFontSize", "latinDisplayDesign"):
            assert type(item[flag]) is bool, f"{item['name']} {flag} must be a boolean"
        assert type(item["visibleTextNodes"]) is int and item["visibleTextNodes"] >= 0

    # The batch is a conclusion, not an independent opinion: it must follow from
    # the observations beside it. Anything else lets a part be quietly promoted.
    for item in items:
        if item["demoPage"] or item["copyLocation"] == "script":
            expected = "deferred"
        elif item["copyLocation"] == "mixed":
            expected = "second"
        else:
            expected = "first"
        assert item["batch"] == expected, (
            f"{item['name']} batch {item['batch']} does not follow from its observations"
        )

    counts = contract["counts"]
    assert counts["total"] == 134
    for location in COPY_LOCATIONS:
        assert counts["copyLocation"][location] == sum(
            item["copyLocation"] == location for item in items
        )
    for batch in BATCHES:
        assert counts["batch"][batch] == sum(item["batch"] == batch for item in items)
    assert sum(counts["batch"].values()) == 134

    # A part with no copy anywhere cannot be length sensitive *about copy*, but
    # it may still measure its own layout — so the only invariant asserted here
    # is the one that would be a contradiction.
    for item in items:
        if item["copyLocation"] == "none":
            assert item["visibleTextNodes"] == 0, item["name"]
        else:
            assert (item["visibleTextNodes"] > 0) == (
                item["copyLocation"] in ("dom", "mixed")
            ), item["name"]

    # Every transition is a demo page; this is the observation that removed 30
    # parts from the first batch and it should not silently stop holding.
    transitions = {
        item["name"] for item in catalog["items"] if item["category"] == "转场"
    }
    assert transitions, "the catalog no longer has a 转场 category"
    for item in items:
        assert item["demoPage"] == (item["name"] in transitions), item["name"]

    def clone() -> dict:
        return json.loads(json.dumps(contract))

    tampered = clone()
    tampered["items"][0]["copyLocation"] = (
        "dom" if tampered["items"][0]["copyLocation"] != "dom" else "script"
    )
    expect_failure("copy location drift", tampered)

    tampered = clone()
    promoted = next(item for item in tampered["items"] if item["batch"] == "deferred")
    promoted["batch"] = "first"
    expect_failure("part promoted past its observations", tampered)

    tampered = clone()
    tampered["counts"]["batch"]["first"] += 1
    expect_failure("count drift", tampered)

    tampered = clone()
    tampered["items"].pop()
    expect_failure("missing item", tampered)

    tampered = clone()
    tampered["items"][0]["visibleTextNodes"] += 1
    expect_failure("text node count drift", tampered)

    tampered = clone()
    tampered["items"][0]["latinDisplayDesign"] = not tampered["items"][0][
        "latinDisplayDesign"
    ]
    expect_failure("font design flag drift", tampered)

    print(
        "motion part usability tests passed: "
        f"{counts['batch']['first']} first / {counts['batch']['second']} second / "
        f"{counts['batch']['deferred']} deferred"
    )
    print("executed checks: 6")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
