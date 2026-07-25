#!/usr/bin/env python3
"""Third-party software notice UI projection black-box gate.

Recomputes the expected projection from the locked source, asset-rights and
motion-rights contracts and compares it against the candidate file, so a source
contract that moved on without its projection goes red instead of publishing a
stale version, licence or count. It then independently re-scans the payload for
the internal review detail that must never reach the frontend bundle, and
confirms no frontend source imports a raw rights contract behind the
projection's back.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_third_party_notice_ui_projection import (  # noqa: E402
    ASSET_RIGHTS_PATH,
    MOTION_RIGHTS_PATH,
    PROJECTION_PATH,
    REPOSITORY_ROOT,
    SOURCES_PATH,
    ProjectionError,
    compose_projection,
)

# Field names and values that exist only for the internal rights review.
INTERNAL_REVIEW_MARKERS = (
    "trademarkIndicators",
    "bundledAssets",
    "embeddedProvenanceMarkers",
    "remoteDependencies",
    "gpuRequirement",
    "sampleAssetRights",
    "pending_bm12_localization",
    "needs_localization",
    "cloudflare_cdn",
    "google_fonts_css",
    "jsdelivr",
)
# Addresses and asset paths. The repository URLs are the one legitimate address
# on the page and are removed before this scan.
LEAK_MARKERS = (
    "http://",
    "https://",
    "://",
    "www.",
    ".com",
    ".net",
    ".org",
    ".dev",
    ".png",
    ".jpeg",
    ".jpg",
    ".wav",
    ".mp3",
    "assets/",
)
# A summary of a 130 KB review, not a copy of it.
MAXIMUM_PROJECTION_BYTES = 8192
FRONTEND_SOURCE_ROOT = REPOSITORY_ROOT / "frontend/src"


class CheckError(RuntimeError):
    """Raised when the candidate projection violates the gate."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CheckError(message)


def _scan_for_leakage(candidate: dict) -> None:
    displayed = json.loads(json.dumps(candidate))
    projects = displayed.get("upstreamProjects")
    _require(isinstance(projects, list) and projects, "no upstream project is disclosed")
    for project in projects:
        _require(isinstance(project, dict), "an upstream project is not an object")
        project.pop("sourceUrl", None)
    serialized = json.dumps(displayed, ensure_ascii=False)
    for marker in INTERNAL_REVIEW_MARKERS:
        _require(marker not in serialized, f"projection leaks review detail: {marker}")
    for marker in LEAK_MARKERS:
        _require(marker not in serialized, f"projection leaks an address or path: {marker}")


def _scan_frontend_imports() -> None:
    """The projection is the only rights payload the frontend may import."""
    for source_path in (SOURCES_PATH, ASSET_RIGHTS_PATH, MOTION_RIGHTS_PATH):
        relative = source_path.relative_to(REPOSITORY_ROOT).as_posix()
        for candidate in FRONTEND_SOURCE_ROOT.rglob("*.ts*"):
            try:
                text = candidate.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as error:
                raise CheckError(f"cannot read {candidate}: {error}") from error
            _require(
                relative not in text,
                f"{candidate.relative_to(REPOSITORY_ROOT)} imports {relative} "
                "instead of the projection",
            )


def check_projection(candidate_path: Path) -> str:
    try:
        raw = candidate_path.read_bytes()
        candidate = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CheckError(f"candidate projection is unreadable: {error}") from error
    _require(isinstance(candidate, dict), "candidate projection must be an object")
    _require(
        len(raw) < MAXIMUM_PROJECTION_BYTES,
        f"projection exceeds {MAXIMUM_PROJECTION_BYTES} bytes; it must stay a summary",
    )

    expected = compose_projection()
    _require(
        candidate == expected,
        "candidate projection drifted from the locked source, asset rights and "
        "motion rights contracts",
    )

    _scan_for_leakage(candidate)
    _scan_frontend_imports()

    names = sorted(project["name"] for project in candidate["upstreamProjects"])
    return (
        f"{len(names)} upstream projects ({', '.join(names)}), "
        f"{len(candidate['motionAssetRights']['dependencies'])} borrowed packages, "
        "no review detail or address leakage"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--projection", type=Path, default=PROJECTION_PATH)
    arguments = parser.parse_args()
    try:
        summary = check_projection(arguments.projection)
    except (CheckError, ProjectionError, KeyError, TypeError) as error:
        print(f"third-party notice ui projection check failed: {error}", file=sys.stderr)
        return 1
    print(f"third-party notice ui projection check passed: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
