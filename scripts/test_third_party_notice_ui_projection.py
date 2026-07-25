#!/usr/bin/env python3
"""Third-party software notice UI projection contract tests.

Proves ``contracts/quality/third-party-notice-ui.v1.json`` is a deterministic
projection of the locked source, asset-rights and motion-rights contracts that
carries only what the legal disclosure page renders. The internal review detail
those source contracts hold — trademark indicators, bundled sample asset paths,
CDN addresses, per-item verification markers — must never reach the frontend
bundle, and the projection must go red the moment it drifts from its sources.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from check_third_party_notice_ui_projection import (  # noqa: E402
    CheckError,
    _require_license_obligations,
    _scan_for_leakage,
)

BUILD = ROOT / "scripts/build_third_party_notice_ui_projection.py"
CHECK = ROOT / "scripts/check_third_party_notice_ui_projection.py"
PROJECTION = ROOT / "contracts/quality/third-party-notice-ui.v1.json"
SOURCES = ROOT / "contracts/quality/third-party-sources.v1.json"
ASSET_RIGHTS = ROOT / "contracts/quality/asset-rights-policy.v1.json"
MOTION_RIGHTS = ROOT / "contracts/quality/motion-catalog-rights.v1.json"
FFMPEG_TOOLCHAIN = ROOT / "contracts/video/ffmpeg-toolchain.v1.json"
CHROMIUM_STAGING = ROOT / "contracts/browser/embedded-chromium-staging.v1.json"
MOTION_WORKER = ROOT / "contracts/quality/motion-video-worker-package.v1.json"
MATERIAL_WORKER = ROOT / "contracts/quality/material-video-worker-package.v1.json"
LICENSE_TEXT_ROOT = ROOT / "frontend/src/features/legal/third-party-software/license-texts"

# Field names and values that only exist for the internal rights review. None
# of them is anything a user reads, so none may reach the shipped payload.
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
# Asset paths and network addresses from the review. ``sourceUrl`` is the one
# legitimate address on the page and is excluded before this scan runs.
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


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict), f"{path.name} must contain an object"
    return value


def run_check(projection: Path = PROJECTION) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECK), "--projection", str(projection)],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=ROOT,
    )


def expect_check_failure(name: str, projection: dict) -> None:
    with tempfile.TemporaryDirectory(
        prefix="automation-tool-legal-projection-test-"
    ) as temporary:
        path = Path(temporary) / "projection.json"
        path.write_text(json.dumps(projection, ensure_ascii=False), encoding="utf-8")
        result = run_check(path)
        assert result.returncode != 0, f"{name}: tampered projection must fail"
        assert "third-party notice ui projection check failed" in result.stderr, (
            f"{name}: {result.stderr}"
        )


def strip_addresses(projection: dict) -> str:
    """Serialize the projection without the legitimate source addresses.

    A repository URL and an upstream source archive URL are the addresses a
    licence notice is obliged to publish; every other address is leakage.
    """
    displayed = json.loads(json.dumps(projection))
    for project in displayed["upstreamProjects"]:
        del project["sourceUrl"]
    for component in displayed["distributedComponents"]:
        del component["upstreamSourceUrl"]
    return json.dumps(displayed, ensure_ascii=False)


def digest(path: Path) -> str:
    """Hash a licence text with line endings normalised, as the gate does."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def main() -> int:
    assert BUILD.is_file(), "scripts/build_third_party_notice_ui_projection.py is missing"
    assert CHECK.is_file(), "scripts/check_third_party_notice_ui_projection.py is missing"
    assert PROJECTION.is_file(), (
        "contracts/quality/third-party-notice-ui.v1.json is missing"
    )

    passing = run_check()
    assert passing.returncode == 0, f"committed projection must pass: {passing.stderr}"

    # Determinism: rebuilding into a scratch path reproduces identical bytes.
    with tempfile.TemporaryDirectory(
        prefix="automation-tool-legal-projection-test-"
    ) as temporary:
        rebuilt = Path(temporary) / "projection.json"
        build = subprocess.run(
            [sys.executable, str(BUILD), "--output", str(rebuilt)],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=ROOT,
        )
        assert build.returncode == 0, f"builder must succeed: {build.stderr}"
        assert rebuilt.read_bytes() == PROJECTION.read_bytes(), (
            "rebuilt projection must be byte-identical to the committed contract"
        )

    projection = load(PROJECTION)
    sources = load(SOURCES)
    asset_rights = load(ASSET_RIGHTS)
    motion_rights = load(MOTION_RIGHTS)

    assert set(projection) == {
        "schemaVersion",
        "id",
        "upstreamProjects",
        "distributedComponents",
        "licenseTexts",
        "assetRights",
        "motionAssetRights",
    }, "projection has a closed top-level key set"

    # Every locked source is disclosed, verbatim, in the lock's own order.
    projects = projection["upstreamProjects"]
    assert len(projects) == len(sources["sources"]), "every locked source is disclosed"
    for project, source in zip(projects, sources["sources"]):
        assert set(project) == {
            "id",
            "name",
            "repository",
            "sourceUrl",
            "version",
            "commit",
            "license",
            "copyright",
            "licenseTextId",
            "packagedNoticePath",
        }, f"{project.get('id')}: closed key set"
        assert project["id"] == source["id"]
        assert project["version"] == source["tag"]
        assert project["commit"] == source["commit"]
        assert project["license"] == source["license"]["spdx"]
        assert project["sourceUrl"] == source["url"]
        # Attribution is derived from the locked address, never restated.
        assert source["url"].endswith(f"{project['repository']}.git")
        assert project["repository"].endswith(f"/{project['name']}")

    # A legal notice that lost the names it exists to publish is worthless.
    names = {project["name"] for project in projects}
    assert "MoneyPrinterTurbo" in names, "the notice must name MoneyPrinterTurbo"
    assert "hyperframes" in names, "the notice must name hyperframes"
    assert {project["license"] for project in projects} == {"MIT", "Apache-2.0"}

    # MIT and Apache-2.0 both oblige the distributor to reproduce the upstream
    # copyright line and to hand over the licence itself. Naming the licence is
    # not doing either, so both are derived from the locked LICENSE blob.
    for project, source in zip(projects, sources["sources"]):
        licence_file = ROOT / source["path"] / source["license"]["path"]
        licence_text = licence_file.read_text(encoding="utf-8")
        assert project["copyright"] in licence_text, (
            f"{project['id']}: copyright line is not taken from the locked LICENSE"
        )
        assert project["copyright"].startswith("Copyright"), (
            f"{project['id']}: copyright line must be the upstream notice"
        )
        assert project["licenseTextId"], (
            f"{project['id']}: the notice ships no copy of the licence itself"
        )

    # Every component the installer redistributes, with the way a user reaches
    # its licence and — for copyleft — its corresponding source.
    components = projection["distributedComponents"]
    ffmpeg_contract = load(FFMPEG_TOOLCHAIN)
    chromium = load(CHROMIUM_STAGING)
    motion_worker = load(MOTION_WORKER)
    material_worker = load(MATERIAL_WORKER)
    layout = ffmpeg_contract["package_layout"]
    by_id = {component["id"]: component for component in components}

    assert set(by_id) == {
        "embedded-browser",
        "ffmpeg",
        "x264",
        "nodejs",
        "material-video-worker-python",
    }, "every redistributed runtime component is disclosed"

    for component in components:
        assert set(component) == {
            "id",
            "name",
            "version",
            "license",
            "copyleft",
            "licenseTextId",
            "packagedNoticePath",
            "noticeChannelId",
            "packagedSourcePaths",
            "upstreamSourceUrl",
        }, f"{component['id']}: closed key set"
        assert component["name"] and component["version"] and component["license"]
        reachable = (
            component["licenseTextId"]
            or component["packagedNoticePath"]
            or component["noticeChannelId"]
        )
        assert reachable, f"{component['id']}: no way to read its licence is published"
        if component["copyleft"]:
            assert component["packagedSourcePaths"], (
                f"{component['id']}: copyleft component publishes no corresponding source"
            )
            assert component["upstreamSourceUrl"], (
                f"{component['id']}: copyleft component publishes no upstream source address"
            )

    # The GPL entries are the ones this notice was silently missing, so they are
    # pinned to the media toolchain contract rather than restated.
    ffmpeg = by_id["ffmpeg"]
    assert ffmpeg["version"] == ffmpeg_contract["ffmpeg"]["version"]
    assert ffmpeg["license"] == ffmpeg_contract["ffmpeg"]["license"] == "GPL-3.0-or-later"
    assert ffmpeg["copyleft"] is True
    assert ffmpeg["upstreamSourceUrl"] == ffmpeg_contract["ffmpeg"]["source_url"]
    assert ffmpeg["packagedNoticePath"] == f"{layout['root']}/{layout['license']}"
    assert ffmpeg["packagedSourcePaths"] == [f"{layout['root']}/{layout['source_archive']}"]

    x264 = by_id["x264"]
    assert x264["version"] == ffmpeg_contract["x264"]["revision"]
    assert x264["license"] == ffmpeg_contract["x264"]["license"] == "GPL-2.0-or-later"
    assert x264["copyleft"] is True
    assert x264["upstreamSourceUrl"] == ffmpeg_contract["x264"]["source_url"]
    assert x264["packagedSourcePaths"] == [
        f"{layout['root']}/{layout['x264_source_archive']}"
    ]
    # The conveyed binary is one GPL-3.0 work, so both entries point at the one
    # licence text the package and the App carry.
    assert ffmpeg["licenseTextId"] == x264["licenseTextId"] == "gpl-3.0"

    assert by_id["embedded-browser"]["version"] == chromium["chromium"]["browser_version"]
    assert by_id["nodejs"]["version"] == motion_worker["runtime"]["version"]
    assert (
        by_id["material-video-worker-python"]["version"]
        == material_worker["python"]["version"]
    )

    # A published in-package path has to be one the production resource layout
    # actually has, and one the build that writes it actually writes.
    sys.path.insert(0, str(ROOT / "scripts"))
    from release_assembly import VIDEO_RUNTIME_RESOURCES  # noqa: PLC0415

    prefixes = tuple(
        "/".join(resource.installed_parts) + "/" for resource in VIDEO_RUNTIME_RESOURCES
    )
    packaged = [
        path
        for entry in list(components) + list(projects)
        for path in ([entry.get("packagedNoticePath")] + list(entry.get("packagedSourcePaths", [])))
        if path
    ]
    assert packaged, "no in-package licence or source location is published at all"
    for path in packaged:
        assert path.startswith(prefixes), (
            f"{path} is not inside any resource the release actually installs"
        )
        assert ".." not in path.split("/") and not path.startswith("/")

    # The licence texts the App itself carries, bound to their bytes.
    texts = projection["licenseTexts"]
    assert {text["id"] for text in texts} == {"mit", "apache-2.0", "gpl-3.0"}
    for text in texts:
        assert set(text) == {"id", "spdx", "sha256", "bytes"}, (
            f"{text['id']}: closed key set"
        )
        shipped = LICENSE_TEXT_ROOT / f"{text['id']}.txt"
        assert shipped.is_file(), f"{text['id']}: the App ships no copy of this licence"
        assert digest(shipped) == text["sha256"], f"{text['id']}: shipped text drifted"
        assert text["bytes"] > 0

    # The two vendor texts must be the locked LICENSE blobs, byte for byte: a
    # hand-retyped licence is not the licence.
    for source in sources["sources"]:
        expected = {"MIT": "mit", "Apache-2.0": "apache-2.0"}[source["license"]["spdx"]]
        licence_file = ROOT / source["path"] / source["license"]["path"]
        assert digest(LICENSE_TEXT_ROOT / f"{expected}.txt") == digest(licence_file), (
            f"{source['id']}: the shipped licence text is not the locked LICENSE"
        )

    gpl = (LICENSE_TEXT_ROOT / "gpl-3.0.txt").read_text(encoding="utf-8")
    assert "GNU GENERAL PUBLIC LICENSE" in gpl and "Version 3, 29 June 2007" in gpl
    assert len(gpl) > 30000, "the GPL-3.0 text is truncated"

    # Every licence text the projection binds must be reachable from something
    # the product actually distributes; an orphan text is dead weight.
    referenced = {entry["licenseTextId"] for entry in list(components) + list(projects)}
    assert {text["id"] for text in texts} <= referenced, "a shipped licence text is unused"

    rights = projection["assetRights"]
    assert set(rights) == {
        "deniedByDefault",
        "sharedRequiredFieldCount",
        "registeredEntryCount",
        "categories",
    }, "asset rights block has a closed key set"
    assert rights["deniedByDefault"] is (asset_rights["defaultDecision"] == "deny")
    assert rights["sharedRequiredFieldCount"] == len(
        asset_rights["distributionRequiredFields"]
    )
    assert rights["registeredEntryCount"] == len(asset_rights["entries"])
    assert len(rights["categories"]) == len(asset_rights["requiredCategories"])
    for category in rights["categories"]:
        assert set(category) == {"id", "requiredFieldCount"}
        assert category["requiredFieldCount"] == len(
            asset_rights["requiredCategories"][category["id"]]
        )

    motion = projection["motionAssetRights"]
    assert set(motion) == {
        "codeLicense",
        "version",
        "totalPartCount",
        "clearedPartCount",
        "partsNeedingWorkCount",
        "webFontFamilyCount",
        "bundledSampleAssetPartCount",
        "networkDependentPartCount",
        "dependencies",
    }, "motion rights block has a closed key set"
    stats = motion_rights["stats"]
    counts = stats["conclusionCounts"]
    assert motion["codeLicense"] == motion_rights["codeLicense"]["spdx"]
    assert motion["version"] == motion_rights["source"]["tag"]
    assert motion["totalPartCount"] == sum(counts.values())
    assert motion["clearedPartCount"] == counts["cleared"]
    assert motion["partsNeedingWorkCount"] == sum(counts.values()) - counts["cleared"]
    assert motion["webFontFamilyCount"] == stats["googleFontFamilyCount"]
    assert motion["bundledSampleAssetPartCount"] == stats["itemsWithBundledSampleAssets"]
    assert (
        motion["networkDependentPartCount"] == stats["itemsWithRuntimeRemoteDependencies"]
    )
    assert len(motion["dependencies"]) == len(motion_rights["remoteDependencyPackages"])
    for dependency, package in zip(
        motion["dependencies"], motion_rights["remoteDependencyPackages"]
    ):
        assert set(dependency) == {"name", "license", "partCount"}
        assert dependency["name"] == package["package"]
        assert dependency["license"] == package["assumedLicense"]
        assert dependency["partCount"] == package["itemCount"]

    # The whole point: the per-item review detail stays out of the payload.
    serialized = strip_addresses(projection)
    for marker in INTERNAL_REVIEW_MARKERS:
        assert marker not in serialized, f"projection leaks review detail: {marker}"
    for marker in LEAK_MARKERS:
        assert marker not in serialized, f"projection leaks an address or path: {marker}"
    assert len(PROJECTION.read_bytes()) < 8192, (
        "the projection must stay a summary, not a copy of the review"
    )

    # The frontend may import the projection and nothing heavier.
    for source_path in (SOURCES, ASSET_RIGHTS, MOTION_RIGHTS):
        relative = source_path.relative_to(ROOT).as_posix()
        for candidate in (ROOT / "frontend/src").rglob("*.ts*"):
            assert relative not in candidate.read_text(encoding="utf-8"), (
                f"{candidate.relative_to(ROOT)} imports {relative} instead of the projection"
            )

    def clone() -> dict:
        return json.loads(json.dumps(projection))

    # Source drift: a stale projection must go red rather than publish an old
    # version, licence or count.
    tampered = clone()
    tampered["upstreamProjects"][0]["version"] = "v0.0.1"
    expect_check_failure("stale version", tampered)

    tampered = clone()
    tampered["upstreamProjects"][0]["license"] = "GPL-3.0"
    expect_check_failure("wrong licence", tampered)

    tampered = clone()
    tampered["upstreamProjects"].pop()
    expect_check_failure("undisclosed upstream project", tampered)

    tampered = clone()
    tampered["assetRights"]["registeredEntryCount"] += 1
    expect_check_failure("stale registered entry count", tampered)

    tampered = clone()
    tampered["assetRights"]["deniedByDefault"] = False
    expect_check_failure("policy no longer denies by default", tampered)

    tampered = clone()
    tampered["motionAssetRights"]["clearedPartCount"] += 1
    expect_check_failure("stale cleared part count", tampered)

    tampered = clone()
    tampered["motionAssetRights"]["dependencies"].pop()
    expect_check_failure("undisclosed borrowed package", tampered)

    tampered = clone()
    tampered["distributedComponents"] = [
        component
        for component in tampered["distributedComponents"]
        if component["id"] != "ffmpeg"
    ]
    expect_check_failure("undisclosed GPL component", tampered)

    tampered = clone()
    tampered["upstreamProjects"][0]["copyright"] = ""
    expect_check_failure("missing upstream copyright line", tampered)

    tampered = clone()
    tampered["licenseTexts"] = [
        text for text in tampered["licenseTexts"] if text["id"] != "gpl-3.0"
    ]
    expect_check_failure("GPL licence text no longer shipped", tampered)

    tampered = clone()
    for text in tampered["licenseTexts"]:
        if text["id"] == "gpl-3.0":
            text["sha256"] = "0" * 64
    expect_check_failure("shipped licence text digest drifted", tampered)

    # Leakage: re-adding review detail or an address must go red even if every
    # other value still matches.
    tampered = clone()
    tampered["motionAssetRights"]["dependencies"][0]["trademarkIndicators"] = ["apple"]
    expect_check_failure("internal review field reintroduced", tampered)

    tampered = clone()
    tampered["motionAssetRights"]["dependencies"][0]["name"] = (
        "https://cdn.jsdelivr.net/npm/gsap"
    )
    expect_check_failure("CDN address reintroduced", tampered)

    # The tamper cases above all trip the equality check first, so the leakage
    # scan is exercised directly. It is the only rule that still catches a
    # builder which starts emitting review detail — in that case the recomputed
    # expectation would agree with the candidate and equality would say nothing.
    _scan_for_leakage(projection)
    for name, payload in (
        ("internal review field", {"trademarkIndicators": ["apple"]}),
        ("CDN address", {"origin": "https://cdn.jsdelivr.net/npm/gsap"}),
        ("bundled asset path", {"sample": "assets/sfx-production.wav"}),
    ):
        leaking = clone()
        leaking["motionAssetRights"]["dependencies"][0].update(payload)
        try:
            _scan_for_leakage(leaking)
        except CheckError:
            continue
        raise AssertionError(f"leakage scan missed a {name}")

    # Same reasoning for the obligation rules: every tamper above trips the
    # equality check first, so the rules that decide whether a disclosure is
    # legally sufficient are exercised against the payload directly. These are
    # the only checks that still bite when the builder itself starts emitting an
    # insufficient disclosure.
    _require_license_obligations(projection)

    def obligation_failure(name: str, mutate) -> None:
        broken = clone()
        mutate(broken)
        try:
            _require_license_obligations(broken)
        except CheckError:
            return
        raise AssertionError(f"obligation check missed a {name}")

    def drop_source(payload: dict) -> None:
        for component in payload["distributedComponents"]:
            if component["id"] == "ffmpeg":
                component["packagedSourcePaths"] = []

    def drop_source_address(payload: dict) -> None:
        for component in payload["distributedComponents"]:
            if component["id"] == "x264":
                component["upstreamSourceUrl"] = None

    def drop_notice(payload: dict) -> None:
        for component in payload["distributedComponents"]:
            if component["id"] == "embedded-browser":
                component["licenseTextId"] = None
                component["packagedNoticePath"] = None
                component["noticeChannelId"] = None

    def unshipped_text(payload: dict) -> None:
        for component in payload["distributedComponents"]:
            if component["id"] == "ffmpeg":
                component["licenseTextId"] = "gpl-4.0"

    def escaping_path(payload: dict) -> None:
        for component in payload["distributedComponents"]:
            if component["id"] == "ffmpeg":
                component["packagedNoticePath"] = "media-toolchain/../../COPYING.GPLv3"

    def foreign_path(payload: dict) -> None:
        for component in payload["distributedComponents"]:
            if component["id"] == "ffmpeg":
                component["packagedNoticePath"] = "somewhere-else/COPYING.GPLv3"

    def drop_copyright(payload: dict) -> None:
        payload["upstreamProjects"][0]["copyright"] = ""

    def wrong_digest(payload: dict) -> None:
        for text in payload["licenseTexts"]:
            if text["id"] == "mit":
                text["sha256"] = "1" * 64

    obligation_failure("GPL component with no corresponding source", drop_source)
    obligation_failure("GPL component with no source address", drop_source_address)
    obligation_failure("component with no readable licence", drop_notice)
    obligation_failure("licence text the App does not ship", unshipped_text)
    obligation_failure("in-package path escaping the resource root", escaping_path)
    obligation_failure("in-package path outside every shipped resource", foreign_path)
    obligation_failure("upstream project with no copyright line", drop_copyright)
    obligation_failure("shipped licence text whose bytes drifted", wrong_digest)

    print("third-party notice ui projection tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
