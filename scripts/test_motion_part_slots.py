#!/usr/bin/env python3
"""PC-03: the frozen slot table, and the two ways it can silently be wrong.

What a slot is
--------------
"The 13th visible text node of `lt-bold-block` currently reads `Maya Chen`."
Copy written by the model replaces that node and nothing else. The pair
(index, original) is a double anchor: if the original no longer matches, this
process has misunderstood the document and must refuse rather than write copy
into whatever happens to sit at that index.

Why the anchor is taken from the release tree, not the submodule
----------------------------------------------------------------
Substitution happens on the copy written into a RenderJob workspace, and that
copy comes from the read-only release tree — which is the submodule *after*
BM-12's offline rewrite and BM-13's trademark overlay. Both change what a slot
would anchor to, and measured on `spotify-card` they change both halves of the
anchor at once:

    submodule    idx=24 'HyperFrames'   idx=26 'HeyGen'      idx=36 'Spotify'
    release      idx=22 '动效画布'       idx=24 '人物创作平台'  idx=34 '音频平台'

The text differs because the overlay replaced it; the index differs because the
offline rewrite deleted the Google Fonts `<link>` elements ahead of it. Freezing
against the submodule would therefore fail at runtime for every one of the 70
parts the overlay touches — and fail by writing copy into the wrong node, which
no downstream gate can see.

Why not every visible node becomes a slot
-----------------------------------------
Measured across the 36 first-batch parts: 419 visible nodes, but 303 of them
(72%) sit in five parts — `ui-3d-reveal` alone has 136. Those are mock
interfaces and shattered page effects; their text is scenery, not copy anyone
would want rewritten. The 26 parts with five nodes or fewer hold 47 nodes
between them, and each of those is genuine copy.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "scripts/check_motion_part_slots.py"
CONTRACT = ROOT / "contracts/video/motion-part-slots.v1.json"
USABILITY = ROOT / "contracts/video/motion-part-usability.v1.json"


def run_check(contract: Path = CONTRACT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECK), "--contract", str(contract)],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=ROOT,
    )


def expect_failure(name: str, contract: dict) -> None:
    with tempfile.TemporaryDirectory(prefix="automation-tool-pc03-test-") as temporary:
        path = Path(temporary) / "slots.json"
        path.write_text(json.dumps(contract, ensure_ascii=False), encoding="utf-8")
        result = run_check(path)
        assert result.returncode != 0, f"{name}: tampered contract must fail"
        assert "part slots check failed" in result.stderr, f"{name}: {result.stderr}"


def main() -> int:
    assert CHECK.is_file(), "scripts/check_motion_part_slots.py is missing"
    assert CONTRACT.is_file(), "contracts/video/motion-part-slots.v1.json is missing"

    green = run_check()
    assert green.returncode == 0, f"the real contract must pass: {green.stderr}"

    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert contract["schemaVersion"] == 1
    assert contract["policy"] == "fail_closed"

    parts = contract["parts"]
    names = [part["name"] for part in parts]
    assert names == sorted(names), "parts must be sorted by name"
    assert len(names) == len(set(names)), "a part may appear once"

    usability = json.loads(USABILITY.read_text(encoding="utf-8"))
    # PC-12 added the second batch. `deferred` stays out: those 58 are the 30
    # transition demo pages and the 28 script-injected parts, and neither has
    # copy a slot can address.
    gradeable = {
        item["name"]
        for item in usability["items"]
        if item["batch"] in ("first", "second")
    }
    assert set(names) <= gradeable, (
        "a slot table may only cover parts the usability grading made available"
    )

    total = 0
    for part in parts:
        slots = part["slots"]
        assert slots, f"{part['name']} declares no slots; omit the part instead"
        indices = [slot["index"] for slot in slots]
        assert indices == sorted(indices), f"{part['name']} slots must be in document order"
        assert len(indices) == len(set(indices)), f"{part['name']} has a duplicated index"
        for slot in slots:
            assert type(slot["index"]) is int and slot["index"] >= 0
            assert type(slot["original"]) is str and slot["original"].strip()
            assert type(slot["parentTag"]) is str and slot["parentTag"]
        total += len(slots)
    assert contract["counts"] == {"parts": len(parts), "slots": total}

    def clone() -> dict:
        return json.loads(json.dumps(contract))

    # The anchor's whole purpose: if the document moved, refuse rather than
    # write copy into whatever now sits at that index.
    tampered = clone()
    tampered["parts"][0]["slots"][0]["original"] = "something the document does not say"
    expect_failure("original drifted from the release tree", tampered)

    tampered = clone()
    tampered["parts"][0]["slots"][0]["index"] += 1
    expect_failure("index points at a different node", tampered)

    tampered = clone()
    tampered["parts"][0]["slots"][0]["parentTag"] = "marquee"
    expect_failure("parent tag drifted", tampered)

    tampered = clone()
    tampered["counts"]["slots"] += 1
    expect_failure("count drift", tampered)

    tampered = clone()
    tampered["parts"].append(
        {
            "name": "zz-not-a-part",
            "documentPath": "zz.html",
            "slots": [{"index": 0, "original": "x", "parentTag": "div"}],
        }
    )
    expect_failure("unknown part", tampered)

    # A part the grading deferred (a transition demo page, or a part whose copy
    # lives in JavaScript) must not acquire a slot table by hand.
    deferred = next(
        item["name"] for item in usability["items"] if item["batch"] == "deferred"
    )
    tampered = clone()
    tampered["parts"].append(
        {
            "name": deferred,
            "documentPath": f"{deferred}.html",
            "slots": [{"index": 0, "original": "x", "parentTag": "div"}],
        }
    )
    expect_failure("deferred part given a slot table", tampered)

    print(
        f"motion part slot tests passed: {len(parts)} parts / {total} slots"
    )
    print("executed checks: 6")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
