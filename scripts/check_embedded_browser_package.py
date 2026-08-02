#!/usr/bin/env python3
"""EB-16: fail-closed gate for the first-release package payload.

One built release bundle must ship exactly one complete embedded Chromium for
its own target and nothing else that can launch or drive a browser:

* the distribution sits at the single resource location the Rust resolver
  (EB-06) reads, and every file is re-verified against the EB-05 manifest;
* no second target root, second browser executable or WebDriver binary exists
  anywhere else in the bundle;
* nothing outside the digest-locked browser tree is a symlink or a special
  file;
* the packaged browser tree and the whole bundle stay inside the release size
  bounds, so a second architecture or a stripped tree is rejected by weight.

Content markers (test harness, WebDriver plugin, dev origin, hidden test
window) stay in the existing `audit-release-bundle.mjs` and
`audit-production-package.mjs` gates, which this module deliberately does not
duplicate; the acceptance runner executes all three over the same real bundle.
"""

from __future__ import annotations

import argparse
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_embedded_browser_distribution import (
    DistributionRejected,
    verify_distribution,
)
from build_embedded_chromium_staging import load_staging_contract

BROWSER_RESOURCE_NAME: Final = "embedded-browser"
MACOS_RESOURCE_PREFIX: Final = ("Contents", "Resources")

_REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[1]
_STAGING_CONTRACT: Final = (
    _REPOSITORY_ROOT / "contracts/browser/embedded-chromium-staging.v1.json"
)

_PLATFORM_TARGET_PREFIX: Final = {"macos": "macos-", "windows": "windows-"}

# Only file basenames are matched. Directory names such as Playwright's
# `server/firefox/` are legitimate library layout and must not be rejected.
_FORBIDDEN_EXECUTABLE_NAMES: Final = frozenset(
    {
        # A loose `Chromium.app` or `chrome` binary outside the digest-locked
        # tree is a second browser even though it is not a WebDriver.
        "chrome",
        "chrome-headless-shell",
        "chrome-headless-shell.exe",
        "chrome.exe",
        "chromium",
        "chromedriver",
        "chromedriver.exe",
        "firefox",
        "firefox-bin",
        "firefox.exe",
        "geckodriver",
        "geckodriver.exe",
        "google chrome",
        "google chrome for testing",
        "headless_shell",
        "headless_shell.exe",
        "iedriverserver.exe",
        "msedge.exe",
        "msedgedriver",
        "msedgedriver.exe",
        "safaridriver",
        "tauri-driver",
        "tauri-driver.exe",
        "webkitwebdriver",
    }
)

_FORBIDDEN_PATH_SEGMENTS: Final = frozenset({"ms-playwright"})

_MEBIBYTE: Final = 1024 * 1024

# The smallest required video runtime on each platform is the media toolchain.
# The macOS value is measured after the release path re-signs ffmpeg/ffprobe;
# the Windows value is measured from the installed package recorded in PC-16.md.
# The whole-package drift window is derived from these facts below; it must
# never be wide enough to hide a second copy.
MINIMUM_COMPLETE_RUNTIME_BYTES: Final = {
    "macos": 43_950_318,
    "windows": 51_168_139,
}


@dataclass(frozen=True)
class PackageSizeBounds:
    """Inclusive byte bounds for the packaged browser tree and the bundle."""

    min_browser_bytes: int
    max_browser_bytes: int
    min_package_bytes: int
    max_package_bytes: int


# Declared composition of one single-architecture release bundle. This remains
# a reviewable list of every payload; platform-specific measured baselines below
# set the actual size envelope because summing each platform's larger component
# creates a package that has never existed and leaves enough room for a duplicate
# runtime.
RELEASE_PAYLOAD_PARTS_MIB: Final = {
    # Locked Chrome for Testing 149.0.7827.55 after Widevine exclusion
    # (Windows x86_64: 310 files, 435,703,601 bytes).
    "embedded-chromium": 416,
    # Frozen Executor with the minimal Silero/ONNX inference runtime. The
    # measured macOS arm64 payload (349 files, 257,004,128 bytes) is larger
    # than the Windows x86_64 payload (284 files, 184,686,384 bytes).
    "local-executor": 246,
    # Frozen intelligent-material worker after the unreachable-module trim, the
    # unlicensed background-music removal and the replacement of the four
    # proprietary system fonts with two open-licensed ones (Windows x86_64:
    # 2,775 files, 484,087,573 bytes).
    "material-video-worker": 462,
    # Frozen brand-motion worker with its private Node runtime (113,124,957 bytes).
    "motion-video-worker": 108,
    # Packaged ffmpeg/ffprobe plus the GPL source archive. Exact target values
    # live in MINIMUM_COMPLETE_RUNTIME_BYTES because they define the gate.
    "media-toolchain": 49,
    # Frozen 134-part catalog including its manifest and offline assets
    # (338 files, 47,671,952 bytes).
    "motion-catalog": 46,
    # Tauri shell, WebView assets, icons and manifests.
    "app-shell-and-web-assets": 24,
}

# Windows is the 2026-07-31 installed-package audit. The macOS baseline is the
# 2026-08-03 complete signed candidate after the material Worker trim, LE-14
# Executor and PC-16 catalog all landed. Both are target-shaped packages, never
# a sum of unrelated cross-platform maxima.
RELEASE_PACKAGE_BASELINE_BYTES: Final = {
    "macos": 1_301_102_222,
    "windows": 1_289_130_572,
}


def release_size_bounds(platform: str) -> PackageSizeBounds:
    """Return the size envelope for one real target-shaped package."""
    baseline = RELEASE_PACKAGE_BASELINE_BYTES.get(platform)
    minimum_runtime = MINIMUM_COMPLETE_RUNTIME_BYTES.get(platform)
    if baseline is None or minimum_runtime is None:
        _reject("unsupported package platform")
        raise AssertionError("unreachable")
    # Keep the *whole* symmetric window strictly below the smallest required
    # runtime on this target. From either inclusive endpoint, adding or removing
    # a complete runtime must therefore cross the opposite endpoint.
    drift = (minimum_runtime - 1) // 2
    return PackageSizeBounds(
        min_browser_bytes=320 * _MEBIBYTE,
        max_browser_bytes=420 * _MEBIBYTE,
        min_package_bytes=baseline - drift,
        max_package_bytes=baseline + drift,
    )


class PackageRejected(RuntimeError):
    """The built release bundle is not a valid first-release package."""


@dataclass(frozen=True)
class PackageAuditReport:
    """Successful audit facts for one built release bundle."""

    target_id: str
    platform: str
    browser_files: int
    browser_bytes: int
    package_files: int
    package_bytes: int


def _reject(message: str) -> None:
    raise PackageRejected(f"release package rejected: {message}")


def browser_resource_root(bundle_root: Path, platform: str) -> Path:
    """Return the single location the production resolver reads."""
    if platform == "macos":
        return bundle_root.joinpath(*MACOS_RESOURCE_PREFIX, BROWSER_RESOURCE_NAME)
    if platform == "windows":
        return bundle_root / BROWSER_RESOURCE_NAME
    _reject("unsupported package platform")
    raise AssertionError("unreachable")


def _other_root_entries(target_id: str) -> frozenset[str]:
    contract = load_staging_contract(_STAGING_CONTRACT)
    return frozenset(
        target.root_entry
        for name, target in contract.targets.items()
        if name != target_id
    )


def _require_contained_symlink(
    link: Path, bundle_root: Path, browser_root: Path
) -> None:
    """Allow a symlink only when it resolves to another file in this package.

    Rejecting every symlink outside the browser cost nothing while the executor
    was the only other payload — it has none. The material video Worker has 53,
    all of them PyInstaller's own layout (`_internal/libarrow.2300.dylib` →
    `pyarrow/libarrow.2300.dylib`), and its loader will not start without them.

    So the check is narrowed to the property the rule actually protects: a link
    may not reach outside the package, may not reach into the verified browser
    tree (which would give the browser a second path that never passed the
    manifest check), and may not dangle.
    """
    try:
        target = link.resolve(strict=True)
    except (OSError, RuntimeError):
        # Missing target, or a symlink loop. Either way it cannot be shown to
        # stay inside, and at runtime it is an unexplained failure.
        _reject("package contains a symlink that does not resolve")
        raise AssertionError("unreachable") from None
    package = bundle_root.resolve()
    if not (target == package or package in target.parents):
        _reject("package contains a symlink that resolves outside the package")
    browser = browser_root.resolve()
    if target == browser or browser in target.parents:
        _reject("package contains a symlink into the browser distribution")
    # Only file links are legitimate. A directory link gives one tree two paths,
    # which would let a payload sit somewhere the "this resource lives here"
    # checks never look. PyInstaller only ever links individual libraries.
    if not target.is_file():
        _reject("package contains a directory symlink")


def audit_embedded_browser_package(
    *,
    bundle_root: Path,
    target_id: str,
    platform: str,
    enforce_archive_lock: bool = True,
    size_bounds: PackageSizeBounds | None = None,
) -> PackageAuditReport:
    """Audit one built release bundle; reject anything that is not the one."""
    prefix = _PLATFORM_TARGET_PREFIX.get(platform)
    if prefix is None:
        _reject("unsupported package platform")
    if not target_id.startswith(str(prefix)):
        _reject("package target does not belong to the package platform")
    effective_size_bounds = size_bounds or release_size_bounds(platform)
    if bundle_root.is_symlink() or not bundle_root.is_dir():
        _reject("package root is not a real directory")

    browser_root = browser_resource_root(bundle_root, platform)
    if browser_root.is_symlink() or not browser_root.is_dir():
        _reject("packaged browser resource is missing")
    try:
        distribution = verify_distribution(
            staging=browser_root,
            target_id=target_id,
            enforce_archive_lock=enforce_archive_lock,
        )
    except DistributionRejected as error:
        raise PackageRejected(f"release package rejected: {error}") from error

    forbidden_roots = _other_root_entries(target_id) | _FORBIDDEN_PATH_SEGMENTS
    package_files = 0
    package_bytes = 0
    for path in bundle_root.rglob("*"):
        metadata = path.lstat()
        inside_browser = path == browser_root or browser_root in path.parents
        if stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
            if not inside_browser and path.name.lower() in forbidden_roots:
                _reject("package contains a second browser distribution root")
            continue
        if inside_browser:
            continue
        if stat.S_ISLNK(metadata.st_mode):
            _require_contained_symlink(path, bundle_root, browser_root)
            continue
        if not stat.S_ISREG(metadata.st_mode):
            _reject("package contains a special file")
        name = path.name.lower()
        if name in _FORBIDDEN_EXECUTABLE_NAMES:
            _reject("package contains a second browser or WebDriver executable")
        if name in forbidden_roots:
            _reject("package contains a second browser distribution root")
        package_files += 1
        package_bytes += metadata.st_size

    package_files += distribution.verified_files
    package_bytes += distribution.total_bytes
    if not (
        effective_size_bounds.min_browser_bytes
        <= distribution.total_bytes
        <= effective_size_bounds.max_browser_bytes
    ):
        _reject("packaged browser tree is outside the release size bounds")
    if not (
        effective_size_bounds.min_package_bytes
        <= package_bytes
        <= effective_size_bounds.max_package_bytes
    ):
        _reject("release package is outside the release size bounds")

    return PackageAuditReport(
        target_id=target_id,
        platform=platform,
        browser_files=distribution.verified_files,
        browser_bytes=distribution.total_bytes,
        package_files=package_files,
        package_bytes=package_bytes,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--platform", required=True, choices=("macos", "windows"))
    arguments = parser.parse_args(argv)
    try:
        report = audit_embedded_browser_package(
            bundle_root=arguments.bundle_root,
            target_id=arguments.target,
            platform=arguments.platform,
        )
    except PackageRejected as error:
        print(error)
        return 1
    print(
        f"EB-16 package audit passed: {report.target_id} ships "
        f"{report.browser_files} browser files ({report.browser_bytes} bytes) "
        f"inside {report.package_files} package files ({report.package_bytes} bytes)"
    )
    return 0


__all__ = [
    "MINIMUM_COMPLETE_RUNTIME_BYTES",
    "RELEASE_PACKAGE_BASELINE_BYTES",
    "RELEASE_PAYLOAD_PARTS_MIB",
    "PackageAuditReport",
    "PackageRejected",
    "PackageSizeBounds",
    "audit_embedded_browser_package",
    "browser_resource_root",
    "release_size_bounds",
]


if __name__ == "__main__":
    raise SystemExit(main())
