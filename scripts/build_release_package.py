#!/usr/bin/env python3
"""Build one distributable package, with every release gate on the path.

Until now no such command existed. The steps that assemble a complete package —
staging the digest-locked Chromium, building the signed Local Executor, building
the three video runtime resources, installing all five into the built bundle and
re-sealing it — lived inside `scripts/run_eb_16_acceptance.py` and had no other
caller. Producing a shippable artifact meant running an acceptance suite, and no
workflow ran that suite, so nothing anywhere refused a package with resources
missing. On 2026-07-26 three of them were missing and a user found out.

This is that path as a command. It reuses the same assembler
(`scripts/release_assembly.py`), the same resource builder
(`scripts/prepare_video_runtime.py`) and the same contract-driven configuration
writer (`scripts/release_configuration.py`) — nothing here is a second
implementation of any of them. The acceptance script now calls into this module
for the steps it used to own, so the verified path and the shipped path cannot
drift apart again.

What this command does *not* do is the acceptance work: it does not install the
package, launch it, probe it or uninstall it. That is `run_eb_16_acceptance.py`,
which wraps this command and adds those checks.

Usage:
    python3 scripts/build_release_package.py --platform macos
    python3 scripts/build_release_package.py --platform macos --work-dir DIR
"""

from __future__ import annotations

import argparse
import json
import os
import platform as platform_module
import shutil
import sys
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"

sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))
sys.path.insert(0, str(BACKEND_ROOT / "src"))

from build_embedded_browser_distribution import (  # noqa: E402
    build_distribution_manifest,
)
from build_embedded_chromium_staging import (  # noqa: E402
    build_staging,
    load_staging_contract,
    sha256_file,
)
from check_embedded_browser_package import (  # noqa: E402
    audit_embedded_browser_package,
    browser_resource_root,
)
from prepare_video_runtime import prepare as prepare_video_runtime  # noqa: E402
from production_assets import (  # noqa: E402
    AUDITED_DISTRIBUTION_NAME,
    snapshot_production_assets,
)
from release_assembly import (  # noqa: E402
    install_and_seal,
    install_video_runtime,
    require_packaged_browser,
    require_packaged_video_runtime,
)
from release_configuration import (  # noqa: E402
    effective_configuration,
    write_macos_release_configuration,
)
from run_p9_03_acceptance import (  # noqa: E402
    CARGO_MANIFEST,
    EXECUTOR_RESOURCE,
    app_binary,
    executor_signing_material,
    one_directory,
    pnpm_executable,
    release_environment,
    run_checked,
    verify_manifest_signature,
)

STAGING_CONTRACT = REPOSITORY_ROOT / "contracts/browser/embedded-chromium-staging.v1.json"
_EB_03_CACHE = ".local/embedded-browser-video-studio/eb-03-cache/chrome-mac-arm64.zip"
DEFAULT_WORK_DIRECTORY = REPOSITORY_ROOT / ".local/release"
RELEASE_CONFIGURATION_NAME = "tauri.release.generated.json"
EFFECTIVE_CONFIGURATION_NAME = "tauri.release.effective.json"


class ReleaseFailed(RuntimeError):
    """The release package could not be produced."""


def announce(message: str) -> None:
    print(f"[release] {message}", flush=True)


def _first_existing(*candidates: Path) -> Path:
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


DEFAULT_ARCHIVES = {
    # The EB-03 archive cache lives in the primary checkout's .local; resolve
    # it both from the primary checkout itself and from a wt/<task> worktree.
    "macos-arm64": _first_existing(
        REPOSITORY_ROOT / _EB_03_CACHE,
        REPOSITORY_ROOT.parent.parent / _EB_03_CACHE,
    ),
    "macos-x86_64": _first_existing(
        REPOSITORY_ROOT / ".local/eb-mac-x64/chrome-mac-x64.zip",
        REPOSITORY_ROOT.parent.parent / ".local/eb-mac-x64/chrome-mac-x64.zip",
    ),
}


def require_macos_target() -> tuple[str, str]:
    if platform_module.system() != "Darwin":
        raise ReleaseFailed("the macOS release package must be built on macOS")
    machine = platform_module.machine().lower()
    if machine in {"arm64", "aarch64"}:
        return "macos-arm64", "aarch64"
    if machine in {"x86_64", "amd64"}:
        return "macos-x86_64", "x86_64"
    raise ReleaseFailed("this macOS architecture is unsupported")


def stage_browser_distribution(target_id: str, archive: Path, output: Path) -> None:
    announce(f"Staging the digest-locked {target_id} Chromium from {archive.name}")
    if not archive.is_file():
        raise ReleaseFailed(f"locked archive is not downloaded yet: {archive}")
    contract = load_staging_contract(STAGING_CONTRACT)
    build_staging(
        contract=contract,
        target_id=target_id,
        archive_path=archive,
        archive_sha256=sha256_file(archive),
        output=output,
    )
    build_distribution_manifest(staging=output, target_id=target_id)


def build_executor_candidate(
    output: Path, architecture: str, build_id: str
) -> tuple[Path, str, Any]:
    from automation_tool.executor.macos_candidate import build_macos_executor_candidate
    from automation_tool.executor.package_manifest import (
        write_signed_executor_manifest,
    )

    announce("Building the real signed Local Executor candidate")
    build_macos_executor_candidate(backend_root=BACKEND_ROOT, output_directory=output)
    seed, public_key, private_key = executor_signing_material()
    write_signed_executor_manifest(
        bundle_directory=output,
        executor_version="0.1.0",
        build_id=build_id,
        target_platform="macos",
        target_architecture=architecture,
        signing_private_key=seed,
    )
    return output, public_key, private_key


def build_release_package(
    *, configuration: Path, environment: dict[str, str], target: Path
) -> Path:
    announce("Building one production-mode .app (no test features)")
    bundle_root = target / "release/bundle"
    if bundle_root.exists():
        shutil.rmtree(bundle_root)
    run_checked(
        [
            pnpm_executable(),
            "exec",
            "tauri",
            "build",
            "--bundles",
            "app",
            "--config",
            os.fspath(configuration),
            "--ci",
        ],
        environment=environment,
    )
    return one_directory(target / "release/bundle/macos", ".app")


def install_runtime_resources_and_sign(
    application: Path, staging: Path, target_id: str, video_runtime: Path
) -> None:
    """Run the shared release assembly step, the same one a release uses.

    A release must not keep its own copy of this: when the acceptance script
    did, the verified path and the shipped path were different paths, and the
    shipped one had no browser in it at all.

    Ordering matters twice over. The video runtime is installed *before*
    `install_and_seal`, because that call seals the bundle at the end and a
    signature taken before a resource lands does not cover it. The browser is
    installed last for the same reason it is installed here at all: the macOS
    bundler destroys its symlinked framework, so it cannot be declared under
    `bundle.resources` and has to arrive afterwards.
    """
    announce("Installing the video runtime resources into the built bundle")
    installed = install_video_runtime(
        application=application, staging=video_runtime, platform="macos"
    )
    announce(f"Video runtime installed: {sorted(installed)}")
    announce("Installing the embedded browser, verifying it, then re-sealing")
    install_and_seal(
        application=application,
        staging=staging,
        target_id=target_id,
        platform="macos",
        seal=lambda bundle: run_checked(
            ["codesign", "--force", "--sign", "-", os.fspath(bundle)]
        ),
    )


def create_disk_image(application: Path, output: Path, target_id: str) -> Path:
    # A bundle without a verified browser must not reach a distributable
    # artifact; this is the gate an ordinary candidate build fails.
    require_packaged_browser(
        application=application, target_id=target_id, platform="macos"
    )
    # The same gate for the video runtime. Without it a package ships whose
    # video features fail on the user's machine while every acceptance run
    # stays green, which is exactly what happened.
    require_packaged_video_runtime(application=application, platform="macos")
    announce("Creating the release disk image from the final App bundle")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    run_checked(
        [
            "hdiutil",
            "create",
            "-volname",
            application.stem,
            "-srcfolder",
            os.fspath(application),
            "-fs",
            "HFS+",
            "-format",
            "UDZO",
            "-quiet",
            os.fspath(output),
        ]
    )
    return output


def audit_release_artifact(
    *,
    application: Path,
    target_id: str,
    environment: dict[str, str],
    configuration: Path,
    audited_assets: Path,
) -> None:
    """Every content gate the release has, run against the finished bundle."""
    announce("Auditing the built binary, configuration and whole bundle content")
    report = audit_embedded_browser_package(
        bundle_root=application, target_id=target_id, platform="macos"
    )
    announce(
        f"Package payload verified: {report.browser_files} browser files "
        f"({report.browser_bytes} bytes) inside {report.package_files} package "
        f"files ({report.package_bytes} bytes)"
    )
    run_checked(
        [
            "node",
            "scripts/audit-production-package.mjs",
            "--binary",
            os.fspath(app_binary(application)),
            "--cargo-manifest",
            os.fspath(CARGO_MANIFEST),
            "--tauri-config",
            os.fspath(configuration),
            "--dist",
            os.fspath(audited_assets),
            # Without this the audit only ever sees a binary, and every
            # statement it makes about the resources a package carries is
            # vacuous — which is how an empty `bundle.resources` passed.
            "--package-root",
            os.fspath(application),
            "--package-platform",
            "macos",
        ],
        environment=environment,
    )
    run_checked(
        [
            "node",
            "scripts/audit-release-bundle.mjs",
            "--bundle-root",
            os.fspath(application),
            "--executor-package",
            os.fspath(application / "Contents/Resources" / EXECUTOR_RESOURCE),
            "--embedded-browser",
            os.fspath(browser_resource_root(application, "macos")),
            "--platform",
            "macos",
        ]
    )


def build_macos_release(
    *, work_directory: Path, archive: Path | None, build_id: str
) -> dict[str, object]:
    """Produce one distributable macOS package and pass every release gate."""
    target_id, architecture = require_macos_target()
    resolved_archive = archive or DEFAULT_ARCHIVES[target_id]
    build_directory = work_directory / "build"
    cargo_target = work_directory / "cargo-target"
    work_directory.mkdir(parents=True, exist_ok=True)
    if build_directory.exists():
        shutil.rmtree(build_directory)
    build_directory.mkdir(parents=True)

    browser = build_directory / "embedded-browser"
    stage_browser_distribution(target_id, resolved_archive, browser)
    executor = build_directory / "executor" / "automation-tool-executor"
    _, public_key, private_key = build_executor_candidate(
        executor, architecture, build_id
    )
    (build_directory / "executor-verifying-key").write_text(
        public_key, encoding="utf-8"
    )
    configuration = write_macos_release_configuration(
        directory=build_directory, executor=executor, name=RELEASE_CONFIGURATION_NAME
    )
    effective = effective_configuration(
        overlay=configuration,
        directory=build_directory,
        name=EFFECTIVE_CONFIGURATION_NAME,
    )
    environment = release_environment(cargo_target, public_key)
    application = build_release_package(
        configuration=configuration, environment=environment, target=cargo_target
    )
    # Frozen next to the artifact it belongs to: the shared `frontend/dist` can
    # be rewritten by any concurrent build while the audits are still running.
    audited_assets = snapshot_production_assets(
        build_directory / AUDITED_DISTRIBUTION_NAME
    )
    announce("Preparing the pinned video runtime resources (cached per machine)")
    video_runtime = prepare_video_runtime(platform="macos")
    install_runtime_resources_and_sign(application, browser, target_id, video_runtime)
    disk_image = create_disk_image(
        application,
        cargo_target / "release/bundle/dmg" / f"{application.stem}_0.1.0.dmg",
        target_id,
    )
    audit_release_artifact(
        application=application,
        target_id=target_id,
        environment=environment,
        configuration=effective,
        audited_assets=audited_assets,
    )
    verify_manifest_signature(
        application / "Contents/Resources" / EXECUTOR_RESOURCE, private_key
    )
    return {
        "application": os.fspath(application),
        "architecture": architecture,
        "disk_image": os.fspath(disk_image),
        "disk_image_bytes": disk_image.stat().st_size,
        "target": target_id,
    }


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=("macos", "windows"), default="macos")
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIRECTORY)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--build-id", default="macos-release")
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    if arguments.platform == "windows":
        # Refused rather than half-implemented: the Windows release still runs
        # through `scripts/run_eb_16_windows_acceptance.py`, which owns the NSIS
        # payload layout and the installer round trip.
        raise ReleaseFailed(
            "the Windows release still runs through "
            "scripts/run_eb_16_windows_acceptance.py; this command builds macOS "
            "packages only"
        )
    result = build_macos_release(
        work_directory=arguments.work_dir,
        archive=arguments.archive,
        build_id=arguments.build_id,
    )
    (arguments.work_dir / "release-package.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    announce(f"Built application: {result['application']}")
    announce(f"Built disk image: {result['disk_image']} ({result['disk_image_bytes']} bytes)")
    announce("Release package built and every release gate passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ReleaseFailed as error:
        print(f"release failed: {error}")
        raise SystemExit(1) from error
