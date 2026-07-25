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
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_third_party_notice_ui_projection import (  # noqa: E402
    ASSET_RIGHTS_PATH,
    FFMPEG_TOOLCHAIN_PATH,
    LICENSE_TEXT_ROOT,
    MOTION_RIGHTS_PATH,
    PACKAGED_PATH_PRODUCERS,
    PROJECTION_PATH,
    REPOSITORY_ROOT,
    SOURCES_PATH,
    ProjectionError,
    _media_toolchain_path,
    _normalized_bytes,
    compose_projection,
)
from release_assembly import VIDEO_RUNTIME_RESOURCES  # noqa: E402

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
    components = displayed.get("distributedComponents")
    _require(
        isinstance(components, list) and components, "no distributed component is disclosed"
    )
    # A copyleft component has to publish where its corresponding source is, so
    # that one address is as legitimate as a repository URL. Everything else in
    # the payload is still held to the no-address rule.
    for component in components:
        _require(isinstance(component, dict), "a distributed component is not an object")
        component.pop("upstreamSourceUrl", None)
    serialized = json.dumps(displayed, ensure_ascii=False)
    for marker in INTERNAL_REVIEW_MARKERS:
        _require(marker not in serialized, f"projection leaks review detail: {marker}")
    for marker in LEAK_MARKERS:
        _require(marker not in serialized, f"projection leaks an address or path: {marker}")


def _installed_resource_prefixes() -> tuple[str, ...]:
    """Return the resource roots the release assembly actually installs.

    Anchoring published paths to the production layout is what stops this page
    from telling a user to look inside a directory no installer ever writes.
    """
    return tuple(
        "/".join(resource.installed_parts) + "/" for resource in VIDEO_RUNTIME_RESOURCES
    )


def _contract_declared_paths() -> frozenset[str]:
    """Paths the media toolchain contract itself declares."""
    try:
        contract = json.loads(FFMPEG_TOOLCHAIN_PATH.read_text(encoding="utf-8"))
        layout = contract["package_layout"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise CheckError(f"cannot read the media toolchain layout: {error}") from error
    return frozenset(
        _media_toolchain_path(layout, key)
        for key in ("license", "notice", "build_info", "source_archive", "x264_source_archive")
    )


def _require_packaged_path(path: object, owner: str, declared: frozenset[str]) -> None:
    """Refuse an in-package location a user could not actually find."""
    _require(
        isinstance(path, str) and path and not path.startswith("/"),
        f"{owner}: in-package location must be a relative path",
    )
    assert isinstance(path, str)
    parts = path.split("/")
    _require(
        ".." not in parts and "." not in parts and "" not in parts,
        f"{owner}: in-package location {path} is not canonical",
    )
    _require(
        path.startswith(_installed_resource_prefixes()),
        f"{owner}: {path} is not inside any resource the release installs",
    )
    if path in declared:
        return
    producer = PACKAGED_PATH_PRODUCERS.get(path)
    _require(
        producer is not None,
        f"{owner}: {path} is declared by no contract and no build step",
    )
    assert producer is not None
    source = REPOSITORY_ROOT / producer
    try:
        text = source.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise CheckError(f"{owner}: cannot read {producer}: {error}") from error
    _require(
        parts[-1] in text,
        f"{owner}: {producer} no longer writes {parts[-1]}, so {path} is a dead pointer",
    )


def _require_license_obligations(candidate: dict) -> None:
    """Fail closed unless every disclosure carries what its licence demands.

    Three rules, each one a licence term rather than a style preference:

    1. every redistributed component publishes a way to actually read its
       licence — the text the App carries, a verbatim file inside the installed
       package, or the component's own notice screen;
    2. every copyleft component publishes where its complete corresponding
       source is, both inside the package and upstream (GPL-3.0 section 6);
    3. every upstream project reproduces its copyright line and ships the
       licence text itself, which is the whole of what MIT and Apache-2.0
       section 4 ask a distributor to carry forward.

    Naming a licence is none of those things, which is exactly how this page
    passed every gate while satisfying none of the three licences it exists for.
    """
    texts = candidate.get("licenseTexts")
    _require(isinstance(texts, list) and texts, "the App ships no licence text at all")
    assert isinstance(texts, list)
    shipped: dict[str, dict] = {}
    for text in texts:
        _require(isinstance(text, dict), "a licence text entry is not an object")
        identifier = text.get("id")
        _require(isinstance(identifier, str) and identifier, "a licence text has no id")
        assert isinstance(identifier, str)
        path = LICENSE_TEXT_ROOT / f"{identifier}.txt"
        _require(
            path.is_file() and not path.is_symlink(),
            f"{identifier}: the App ships no copy of this licence",
        )
        payload = _normalized_bytes(path)
        _require(
            hashlib.sha256(payload).hexdigest() == text.get("sha256"),
            f"{identifier}: the shipped licence text no longer matches its digest",
        )
        _require(
            len(payload) == text.get("bytes"),
            f"{identifier}: the shipped licence text no longer matches its length",
        )
        shipped[identifier] = text

    declared = _contract_declared_paths()

    projects = candidate.get("upstreamProjects")
    _require(isinstance(projects, list) and projects, "no upstream project is disclosed")
    assert isinstance(projects, list)
    for project in projects:
        owner = project.get("id", "<unknown project>")
        notice = project.get("copyright")
        _require(
            isinstance(notice, str) and notice.startswith("Copyright"),
            f"{owner}: the upstream copyright notice is not reproduced",
        )
        identifier = project.get("licenseTextId")
        _require(
            isinstance(identifier, str) and identifier in shipped,
            f"{owner}: the App ships no copy of this project's licence",
        )
        packaged = project.get("packagedNoticePath")
        if packaged is not None:
            _require_packaged_path(packaged, str(owner), declared)

    components = candidate.get("distributedComponents")
    _require(
        isinstance(components, list) and components, "no distributed component is disclosed"
    )
    assert isinstance(components, list)
    for component in components:
        owner = str(component.get("id", "<unknown component>"))
        identifier = component.get("licenseTextId")
        packaged = component.get("packagedNoticePath")
        channel = component.get("noticeChannelId")
        if identifier is not None:
            _require(
                isinstance(identifier, str) and identifier in shipped,
                f"{owner}: points at a licence text the App does not ship",
            )
        if packaged is not None:
            _require_packaged_path(packaged, owner, declared)
        _require(
            bool(identifier) or bool(packaged) or bool(channel),
            f"{owner}: publishes no way for a user to read its licence",
        )
        sources = component.get("packagedSourcePaths")
        _require(isinstance(sources, list), f"{owner}: packagedSourcePaths must be a list")
        assert isinstance(sources, list)
        for path in sources:
            _require_packaged_path(path, owner, declared)
        if component.get("copyleft"):
            _require(
                bool(sources),
                f"{owner}: is copyleft and publishes no corresponding source in the "
                "package, so the licence is not satisfied",
            )
            upstream = component.get("upstreamSourceUrl")
            _require(
                isinstance(upstream, str) and upstream.startswith("https://"),
                f"{owner}: is copyleft and publishes no upstream source address",
            )


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
    _require_license_obligations(candidate)
    _scan_frontend_imports()

    names = sorted(project["name"] for project in candidate["upstreamProjects"])
    components = candidate["distributedComponents"]
    copyleft = [component["id"] for component in components if component["copyleft"]]
    return (
        f"{len(names)} upstream projects ({', '.join(names)}), "
        f"{len(components)} distributed components "
        f"({len(copyleft)} copyleft: {', '.join(sorted(copyleft))}), "
        f"{len(candidate['licenseTexts'])} licence texts shipped in the App, "
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
