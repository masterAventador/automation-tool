#!/usr/bin/env python3
"""BM-15 UI projection black-box gate.

Recomputes the expected projection from the locked BM-11/BM-13 contracts and
compares it against the candidate file, then independently re-validates the
closed label sets and scans the payload for trademark indicator forms, domains
and URLs. Any drift exits non-zero with the fixed failure prefix.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_motion_catalog_ui_projection import (
    APPLICABILITY_LABELS,
    ASCII_LETTER_PATTERN,
    DEVICE_LABELS,
    PERFORMANCE_LABELS,
    PROJECTION_PATH,
    PROVENANCE_LABELS,
    RELEASE_LOCK_PATH,
    TYPE_LABELS,
    ProjectionError,
    compose_projection,
)

URL_MARKERS = ("http://", "https://", "://", "www.", ".com", ".net", ".org", ".dev")


class CheckError(RuntimeError):
    """Raised when the candidate projection violates the gate."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CheckError(message)


def _boundary_pattern(literal: str) -> re.Pattern[str]:
    return re.compile(
        r"(?<![0-9A-Za-z])" + re.escape(literal) + r"(?![0-9A-Za-z])", re.IGNORECASE
    )


def require_localized_titles(items: list[dict]) -> None:
    """Reject any part name an operator would read as English or as a twin.

    Re-validated here rather than trusted from the builder: the drift check
    only proves the candidate matches whatever the builder produced today, so
    without this rule a builder regression would pass the gate unnoticed.
    """
    owners: dict[str, str] = {}
    for item in items:
        title = item.get("displayTitle")
        _require(isinstance(title, str) and title != "", "part name is missing")
        _require(
            ASCII_LETTER_PATTERN.search(str(title)) is None,
            f"part name is not localized into Chinese: {title}",
        )
        _require(
            str(title) not in owners,
            f"duplicated part name {title} on {item.get('id')} "
            f"and {owners.get(str(title))}",
        )
        owners[str(title)] = str(item.get("id"))


def check_projection(candidate_path: Path) -> str:
    try:
        candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CheckError(f"candidate projection is unreadable: {error}") from error
    _require(isinstance(candidate, dict), "candidate projection must be an object")

    expected = compose_projection()
    _require(
        candidate == expected,
        "candidate projection drifted from the locked catalog composition",
    )

    items = candidate["items"]
    _require(len(items) == candidate["counts"]["total"] == 134, "item count drift")
    require_localized_titles(items)
    for item in items:
        _require(
            "officialPreview" not in item,
            "projection must not ship the upstream preview flag to the frontend",
        )
        _require(item["typeLabel"] in TYPE_LABELS.values(), "type label drift")
        _require(
            item["performanceLabel"] in PERFORMANCE_LABELS.values(),
            "performance label drift",
        )
        _require(
            item["deviceRequirementLabel"] in DEVICE_LABELS.values(),
            "device label drift",
        )
        _require(
            item["provenanceLabel"] in PROVENANCE_LABELS.values(),
            "provenance label drift",
        )
        _require(
            item["applicabilityLabel"] in APPLICABILITY_LABELS.values(),
            "applicability label drift",
        )

    # Item ids are the locked catalog reference keys (kept verbatim by the
    # BM-14 release as well) and are never rendered to users; every other
    # field is user-visible text and must stay free of indicator forms.
    displayed = json.loads(json.dumps(candidate))
    for item in displayed["items"]:
        del item["id"]
    serialized = json.dumps(displayed, ensure_ascii=False)
    release_lock = json.loads(RELEASE_LOCK_PATH.read_text(encoding="utf-8"))
    for literals in release_lock["trademarkScan"]["forms"].values():
        for literal in literals:
            _require(
                _boundary_pattern(literal).search(serialized) is None,
                f"projection contains a trademark indicator form: {literal}",
            )
    for marker in URL_MARKERS:
        _require(marker not in serialized, f"projection contains a URL marker: {marker}")

    return (
        f"{len(items)} items, {len(candidate['categories'])} categories, "
        "labels closed, names fully localized, no indicator or URL leakage"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--projection", type=Path, default=PROJECTION_PATH)
    arguments = parser.parse_args()
    try:
        summary = check_projection(arguments.projection)
    except (CheckError, ProjectionError, KeyError, TypeError) as error:
        print(f"motion catalog ui projection check failed: {error}", file=sys.stderr)
        return 1
    print(f"motion catalog ui projection check passed: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
