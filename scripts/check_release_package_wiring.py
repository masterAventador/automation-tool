#!/usr/bin/env python3
"""Refuse a release path that would ship a package with a resource missing.

This gate exists because of a specific failure. The production code resolves
six resource trees out of the packaged resource directory. Three of
them were absent from a shipped macOS package, and nothing objected: the only
path that assembled them was a step inside an acceptance script no workflow
ran, and the package audit's only statement about `bundle.resources` was a
negative one, so an empty declaration and a complete one looked the same to it.

What this gate can check, it checks for real — by calling the release
configuration writer and comparing what it produces against
`contracts/quality/release-package-resources.v1.json`. What it cannot check is
stated plainly rather than faked: it does not build a package. A full macOS
release needs a 340 MB Chromium archive, a PyInstaller Executor, an ffmpeg
build and two video Workers; that is not a CI job. So this gate proves the
*wiring* — that every declared resource has an owner on every release path,
that the configuration a release builds with declares each bundler-owned tree,
and that a payload missing one is refused rather than quietly written smaller.
Whether the built artifact really carries them is proven by
`release_assembly.require_packaged_*` and by
`frontend/scripts/audit-production-package.mjs`, both of which run on the
release path itself.

Usage:
    python3 scripts/check_release_package_wiring.py
    python3 scripts/check_release_package_wiring.py --self-test
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import release_configuration  # noqa: E402
from release_assembly import (  # noqa: E402
    ASSEMBLER_INSTALLED_RESOURCES,
    RELEASE_PACKAGE_RESOURCES,
)

PLATFORMS = ("macos", "windows")
# The paths that can produce a distributable artifact. The macOS one is a
# command in its own right; the Windows one still lives inside its acceptance
# script, which is recorded here rather than pretended away.
RELEASE_PATHS = (
    ROOT / "scripts/build_release_package.py",
    ROOT / "scripts/run_eb_16_windows_acceptance.py",
)
AUDIT_PATH = ROOT / "frontend/scripts/audit-production-package.mjs"
VIDEO_RUNTIME_BUILDER = ROOT / "scripts/prepare_video_runtime.py"
# Which script produces a staged tree, per category. A resource nobody builds is
# a release that fails at assembly time, twenty minutes in — so the producer is
# declared here alongside the resource rather than discovered then.
CATEGORY_BUILDERS = {
    "video": VIDEO_RUNTIME_BUILDER,
    "catalog": ROOT / "scripts/build_motion_catalog_release.py",
}
REQUIRED_RELEASE_CALLS = (
    "install_video_runtime(",
    "require_packaged_video_runtime(",
    "install_motion_catalog(",
    "require_packaged_motion_catalog(",
    "install_and_seal(",
    "require_packaged_browser(",
    '"--package-root"',
)


class WiringRejected(RuntimeError):
    """A release path would ship a package with a declared resource missing."""


def _reject(message: str) -> None:
    raise WiringRejected(f"release package wiring rejected: {message}")


def check_every_resource_has_an_owner() -> None:
    """Each declared resource must be shipped by the bundler or the assembler."""
    declared = {
        str(resource["name"])
        for resource in release_configuration.RELEASE_PACKAGE_RESOURCES
    }
    if not declared:
        _reject("the release resource contract declares nothing")
    for platform in PLATFORMS:
        bundler = set(release_configuration.bundler_declared_resources(platform))
        assembler = set(release_configuration.assembler_installed_resources(platform))
        if bundler & assembler:
            _reject(f"{platform}: {sorted(bundler & assembler)} has two owners")
        if bundler | assembler != declared:
            _reject(
                f"{platform}: {sorted(declared - (bundler | assembler))} is declared "
                "but nothing ships it"
            )


def check_the_assembler_installs_what_the_bundler_will_not() -> None:
    """Everything the macOS bundler cannot carry must be installed by the assembler."""
    assembler = set(release_configuration.assembler_installed_resources("macos"))
    installed = {
        resource.staging_name for resource in ASSEMBLER_INSTALLED_RESOURCES
    }
    # The browser has its own installer (`install_and_seal`) because its
    # symlinked framework needs one. Everything else the assembler owns goes in
    # through `install_packaged_resources`, so this set is derived per category
    # rather than naming the video trees — a new category that nobody installs
    # has to be caught here, not at the end of a twenty-minute release run.
    installed.add("embedded-browser")
    if assembler != installed:
        _reject(
            "the macOS assembler does not install what the bundler leaves out: "
            f"missing {sorted(assembler - installed)}, "
            f"unexpected {sorted(installed - assembler)}"
        )


def check_every_video_resource_has_a_builder() -> None:
    """A resource nobody builds is a release that fails at assembly time.

    The single declaration makes the assembler and the configuration writer pick
    up a new resource automatically, which is the point — but it also means both
    would happily agree on a resource no build step produces. `install_video_runtime`
    would then reject the staging tree, at the end of a long release run. The
    producer has to be declared alongside the resource, not discovered later.
    """
    for resource in release_configuration.RELEASE_PACKAGE_RESOURCES:
        category = str(resource["category"])
        builder = CATEGORY_BUILDERS.get(category)
        if builder is None:
            continue
        if f'name="{resource["name"]}"' not in builder.read_text(encoding="utf-8"):
            _reject(
                f"{resource['name']} is declared as a packaged {category} resource "
                f"but {builder.name} never builds it"
            )


def check_the_release_configuration_declares_every_bundled_resource() -> None:
    """Write both release configurations and read back what they declare."""
    with tempfile.TemporaryDirectory(prefix="release-wiring-") as raw:
        base = Path(raw)
        executor = base / "executor"
        executor.mkdir()
        payload = base / "payload"
        payload.mkdir()
        written = {
            "macos": release_configuration.write_macos_release_configuration(
                directory=base, executor=executor, name="macos.json"
            ),
            "windows": release_configuration.write_windows_release_configuration(
                directory=base, executor=executor, payload=payload, name="windows.json"
            ),
        }
        for platform, path in written.items():
            declared = json.loads(path.read_text(encoding="utf-8"))["bundle"]["resources"]
            destinations = {value.rstrip("/") for value in declared.values()}
            expected = {
                release_configuration.installed_destination(name)
                for name in release_configuration.bundler_declared_resources(platform)
            }
            if destinations != expected:
                _reject(
                    f"the {platform} release configuration declares {sorted(destinations)}, "
                    f"but the contract requires {sorted(expected)}"
                )


def check_an_incomplete_payload_is_refused() -> None:
    """A release must not be able to write out a smaller package silently."""
    with tempfile.TemporaryDirectory(prefix="release-wiring-") as raw:
        base = Path(raw)
        for platform in PLATFORMS:
            owned = release_configuration.bundler_declared_resources(platform)
            if len(owned) < 2:
                # macOS declares one resource, so there is no proper subset to
                # drop; the refusal is exercised on the target that has one.
                continue
            try:
                release_configuration.write_release_configuration(
                    directory=base,
                    platform=platform,
                    sources={name: base / name for name in owned[:-1]},
                    name=f"incomplete-{platform}.json",
                    relative_sources=False,
                )
            except release_configuration.ReleaseConfigurationRejected:
                continue
            _reject(
                f"the {platform} release configuration writer accepted a payload "
                f"without {owned[-1]}"
            )


def check_release_paths_run_the_gates(paths: tuple[Path, ...] = RELEASE_PATHS) -> None:
    """Every path that can ship must assemble all five resources and gate them."""
    for path in paths:
        if not path.is_file():
            _reject(f"the release path {path.name} does not exist")
        source = path.read_text(encoding="utf-8")
        for call in REQUIRED_RELEASE_CALLS:
            if call not in source:
                _reject(f"{path.name} never calls {call}")
        if 'configuration["bundle"]["resources"]' in source:
            _reject(
                f"{path.name} writes bundle.resources by hand instead of deriving "
                "it from the release resource contract"
            )


def check_the_audit_reads_the_contract() -> None:
    """The package audit must ask the contract, not a second hand-written list."""
    source = AUDIT_PATH.read_text(encoding="utf-8")
    if "release-package-resources.v1.json" not in source:
        _reject("the package audit does not read the release resource contract")
    for resource in RELEASE_PACKAGE_RESOURCES:
        if f'"{resource["name"]}"' in source:
            _reject(
                f"the package audit restates {resource['name']} instead of reading "
                "it from the contract"
            )


CHECKS = (
    check_every_resource_has_an_owner,
    check_the_assembler_installs_what_the_bundler_will_not,
    check_every_video_resource_has_a_builder,
    check_the_release_configuration_declares_every_bundled_resource,
    check_an_incomplete_payload_is_refused,
    check_release_paths_run_the_gates,
    check_the_audit_reads_the_contract,
)


def run_checks() -> None:
    for check in CHECKS:
        check()


def _expect_rejection(description: str, action) -> None:
    try:
        action()
    except (WiringRejected, release_configuration.ReleaseConfigurationRejected):
        # Either refusal is a pass: the writer refusing an incomplete payload is
        # a stronger rejection than this gate reporting one after the fact.
        return
    raise SystemExit(
        f"release package wiring self-test failed: {description} was accepted"
    )


def self_test() -> None:
    """Prove the gate can fail. A gate that cannot is not a gate."""
    with tempfile.TemporaryDirectory(prefix="release-wiring-self-test-") as raw:
        mutilated = Path(raw) / "build_release_package.py"
        source = (ROOT / "scripts/build_release_package.py").read_text(encoding="utf-8")
        mutilated.write_text(
            source.replace("install_video_runtime(", "skipped_video_runtime("),
            encoding="utf-8",
        )
        _expect_rejection(
            "a release path that stopped installing the video runtime",
            lambda: check_release_paths_run_the_gates((mutilated,)),
        )

        blind = Path(raw) / "blind_release.py"
        blind.write_text(
            source.replace('"--package-root"', '"--nothing"'), encoding="utf-8"
        )
        _expect_rejection(
            "a release path that hides the package from the audit",
            lambda: check_release_paths_run_the_gates((blind,)),
        )

    original = release_configuration.RELEASE_PACKAGE_RESOURCES
    try:
        # A sixth resource nobody ships: the exact shape of the 2026-07-26
        # failure, where the product resolved trees the release never installed.
        release_configuration.RELEASE_PACKAGE_RESOURCES = (
            *original,
            {
                "name": "unshipped-resource",
                "category": "video",
                "installedParts": ["unshipped-resource"],
                "requiredFiles": [],
                "windowsExecutables": [],
                "bundlerDeclared": {"macos": False, "windows": False},
            },
        )
        _expect_rejection(
            "a declared resource with no owner on macOS",
            check_the_assembler_installs_what_the_bundler_will_not,
        )
        release_configuration.RELEASE_PACKAGE_RESOURCES = (
            *original,
            {
                "name": "unshipped-resource",
                "category": "video",
                "installedParts": ["unshipped-resource"],
                "requiredFiles": [],
                "windowsExecutables": [],
                "bundlerDeclared": {"macos": True, "windows": True},
            },
        )
        _expect_rejection(
            "a bundler-owned resource the release configuration never declares",
            check_the_release_configuration_declares_every_bundled_resource,
        )
        _expect_rejection(
            "a declared video resource no build step produces",
            check_every_video_resource_has_a_builder,
        )
    finally:
        release_configuration.RELEASE_PACKAGE_RESOURCES = original
    print("release package wiring self-test passed: the gate rejects all five mutations")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", dest="run_self_test")
    arguments = parser.parse_args()
    if arguments.run_self_test:
        self_test()
        return 0
    run_checks()
    print(
        "release package wiring passed: "
        f"{len(RELEASE_PACKAGE_RESOURCES)} declared resources, "
        f"{len(RELEASE_PATHS)} release paths, every resource owned and gated"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except WiringRejected as error:
        print(error)
        raise SystemExit(1) from error
