#!/usr/bin/env python3
"""Grade every catalog part by whether this product can put the user's words in it.

A catalog entry says what a part *is*. It does not say whether the one-sentence
path can make it carry the user's copy, and reading all 134 sources shows three
different answers to that question:

* **Demo pages.** All 30 transitions render as "SCENE A | SCENE B / Cinematic
  Zoom / Prompt / use cinematic zoom shader transition" — they demonstrate a
  technique against placeholder panels. Using one means moving that technique
  between your own two scenes, which is writing code, not replacing a string.
* **Copy in JavaScript.** `caption-kinetic-slam` holds 28 words in an array,
  each with its own start/end, plus a keyword set addressed by index. Its
  `data-duration` is derived from those timestamps, so shortening the copy
  leaves a still frame at the end and lengthening it cuts words off — and
  nothing downstream can see either, because the declared duration still
  matches. Substituting there is re-timing an animation.
* **Copy in the DOM**, where a slot table can address it directly.

Why the grading is recomputed rather than curated
-------------------------------------------------
An earlier attempt batched parts by "how many text nodes they have", counted
with a regex over the whole file. It put the 15 caption parts in the *easy*
batch: their only match was the `<title>`, while their bodies contain nothing
addressable at all. So the count here comes from
`automation_tool.executor.motion_authoring.part_document` — the same parser the
substitution will use — and this gate re-derives every field from the sources,
so a hand-edited grading is rejected.

`--write` regenerates the contract from those same rules.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Final

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "backend/src"))

from automation_tool.executor.motion_authoring.part_document import (  # noqa: E402
    visible_text_nodes,
)

CONTRACT_PATH: Final = REPOSITORY_ROOT / "contracts/video/motion-part-usability.v1.json"
CATALOG_PATH: Final = REPOSITORY_ROOT / "contracts/quality/motion-catalog.v1.json"
SUBMODULE_ROOT: Final = REPOSITORY_ROOT / "vendor/hyperframes"

# The curated category that marks a part as a technique demonstration rather
# than a shot. It is the whole 转场 group and nothing else — kept as the
# category rather than a per-name list so a new upstream transition inherits
# the judgement instead of silently landing in the usable batch.
DEMO_PAGE_CATEGORY: Final = "转场"

_SCRIPT: Final = re.compile(r"<script\b[^>]*>(.*?)</script>", re.S | re.I)
# Assignment only. A read of `.textContent` says nothing about where the copy
# lives; an assignment is the part building its own text.
_INJECTS_TEXT: Final = re.compile(
    r"\.(?:textContent|innerHTML|innerText)\s*=[^=]"
    r"|createTextNode\s*\("
    r"|insertAdjacentHTML\s*\("
)
# The animation reads its own laid-out size, so copy length changes the motion —
# travel distances, wipe widths, scale origins — with nothing raising an error.
_MEASURES_LAYOUT: Final = re.compile(
    r"offsetWidth|offsetHeight|scrollWidth|getBoundingClientRect|measureText|clientWidth"
)
# Some parts shrink their type until it fits. That bounds the damage from long
# copy; it does not remove it, since every observed implementation has a floor.
_FITS_FONT_SIZE: Final = re.compile(r"fitFontSize|fontSize\s*=|clamp\s*\(")
_FONT_FAMILY: Final = re.compile(r"font-family\s*:\s*([^;}\n]+)", re.I)
# Display faces with no CJK coverage: substituting Chinese falls back to a
# system face and the part's whole typographic identity goes with it.
_LATIN_DISPLAY_FACES: Final[frozenset[str]] = frozenset(
    {
        "anton",
        "archivo black",
        "bebas neue",
        "big shoulders display",
        "instrument serif",
        "libre baskerville",
        "playfair display",
    }
)


class UsabilityError(ValueError):
    """Raised when the grading cannot be derived or has drifted."""


def fail(message: str) -> None:
    raise UsabilityError(message)


def load_json(path: Path) -> dict:
    if not path.is_file() or path.is_symlink():
        fail(f"required regular file is missing: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        fail(f"{path.name} is not valid JSON: {error}")
    if not isinstance(value, dict):
        fail(f"{path.name} must contain an object")
    return value


def named_font_families(html: str) -> set[str]:
    families: set[str] = set()
    for declaration in _FONT_FAMILY.findall(html):
        for candidate in declaration.split(","):
            name = candidate.strip().strip("\"'").lower()
            if name and not name.startswith(
                ("sans", "serif", "monospace", "cursive", "system", "-apple", "ui-")
            ):
                families.add(name)
    return families


def grade_part(item: dict) -> dict[str, object]:
    documents = [record for record in item["files"] if record["path"].endswith(".html")]
    if not documents:
        fail(f"{item['name']} has no HTML document to grade")
    source = SUBMODULE_ROOT / item["path"] / documents[0]["path"]
    if not source.is_file() or source.is_symlink():
        fail(f"{item['name']} document is missing or not a regular file")
    html = source.read_text(encoding="utf-8")

    visible = len(visible_text_nodes(html))
    injects = bool(_INJECTS_TEXT.search("".join(_SCRIPT.findall(html))))
    if visible == 0:
        copy_location = "script" if injects else "none"
    else:
        copy_location = "mixed" if injects else "dom"

    demo_page = item["category"] == DEMO_PAGE_CATEGORY
    if demo_page or copy_location == "script":
        batch = "deferred"
    elif copy_location == "mixed":
        batch = "second"
    else:
        batch = "first"

    return {
        "name": item["name"],
        "copyLocation": copy_location,
        "visibleTextNodes": visible,
        "demoPage": demo_page,
        "lengthSensitive": bool(_MEASURES_LAYOUT.search(html)),
        "autoFitsFontSize": bool(_FITS_FONT_SIZE.search(html)),
        "latinDisplayDesign": bool(named_font_families(html) & _LATIN_DISPLAY_FACES),
        "batch": batch,
    }


def build_contract() -> dict[str, object]:
    catalog = load_json(CATALOG_PATH)
    items = [grade_part(item) for item in catalog["items"]]
    if len(items) != 134:
        fail(f"expected 134 graded parts, derived {len(items)}")
    return {
        "schemaVersion": 1,
        "id": "motion-part-usability.v1",
        "policy": "fail_closed",
        "source": dict(catalog["source"]),
        "counts": {
            "total": len(items),
            "copyLocation": {
                location: sum(item["copyLocation"] == location for item in items)
                for location in ("dom", "mixed", "script", "none")
            },
            "batch": {
                batch: sum(item["batch"] == batch for item in items)
                for batch in ("first", "second", "deferred")
            },
        },
        "items": items,
    }


def first_difference(expected: object, actual: object, path: str) -> str | None:
    if type(expected) is not type(actual):
        return f"{path}: type differs ({type(expected).__name__} vs {type(actual).__name__})"
    if isinstance(expected, dict):
        for key in sorted(set(expected) | set(actual)):
            if key not in expected:
                return f"{path}.{key}: unexpected key"
            if key not in actual:
                return f"{path}.{key}: missing key"
            difference = first_difference(expected[key], actual[key], f"{path}.{key}")
            if difference is not None:
                return difference
        return None
    if isinstance(expected, list):
        if len(expected) != len(actual):
            return f"{path}: length differs ({len(expected)} vs {len(actual)})"
        for index, (left, right) in enumerate(zip(expected, actual)):
            difference = first_difference(left, right, f"{path}[{index}]")
            if difference is not None:
                return difference
        return None
    if expected != actual:
        return f"{path}: {actual!r} != {expected!r}"
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=CONTRACT_PATH)
    parser.add_argument("--write", action="store_true")
    arguments = parser.parse_args()

    try:
        expected = build_contract()
        if arguments.write:
            arguments.contract.parent.mkdir(parents=True, exist_ok=True)
            arguments.contract.write_text(
                json.dumps(expected, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(f"motion part usability contract written: {arguments.contract}")
            return
        difference = first_difference(expected, load_json(arguments.contract), "usability")
        if difference is not None:
            fail(difference)
    except UsabilityError as error:
        print(f"part usability check failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error

    counts = expected["counts"]
    print(
        "motion part usability is valid: 134 parts, "
        f"copy {counts['copyLocation']}, batches {counts['batch']}"
    )


if __name__ == "__main__":
    main()
