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

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from check_third_party_notice_ui_projection import (  # noqa: E402
    CheckError,
    _scan_for_leakage,
)

BUILD = ROOT / "scripts/build_third_party_notice_ui_projection.py"
CHECK = ROOT / "scripts/check_third_party_notice_ui_projection.py"
PROJECTION = ROOT / "contracts/quality/third-party-notice-ui.v1.json"
SOURCES = ROOT / "contracts/quality/third-party-sources.v1.json"
ASSET_RIGHTS = ROOT / "contracts/quality/asset-rights-policy.v1.json"
MOTION_RIGHTS = ROOT / "contracts/quality/motion-catalog-rights.v1.json"

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
    """Serialize the projection without the two legitimate repository URLs."""
    displayed = json.loads(json.dumps(projection))
    for project in displayed["upstreamProjects"]:
        del project["sourceUrl"]
    return json.dumps(displayed, ensure_ascii=False)


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

    print("third-party notice ui projection tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
