#!/usr/bin/env python3
"""Produce the video runtime resources the production build needs.

The production video code resolves ffmpeg and both Workers from the packaged
resource directory. Nothing used to put them there: each BM/IM acceptance
script built its own copy into a temporary directory, handed the paths to a
`video-studio-e2e` build through environment variables, and deleted them
afterwards. Acceptance stayed green while the shipped package had no video
runtime at all.

This module is the missing production step. It builds each resource exactly
once per pinned-input change (see `video_runtime_cache`) and lays them out
under a single staging root whose directory names are what
`release_assembly.install_video_runtime` expects.

Staging is not installing. The staging root is the build cache, which is not
where anything reads these resources from at runtime: a packaged App reads them
from its resource directory, and a debug binary reads them from the directory
holding the executable. `frontend/src-tauri/tests/motion_authoring_runtime.rs`
told its reader to run this script, and running it did not help, because until
`--install-into` existed this script had no way to put anything there. A remedy
that exits 0 while leaving the failure in place is worse than no remedy.

Usage:
    python3 scripts/prepare_video_runtime.py            # ensure all resources
    python3 scripts/prepare_video_runtime.py --print    # ensure and print root
    python3 scripts/prepare_video_runtime.py \\
        --only motion-video-worker \\
        --install-into frontend/src-tauri/target/debug   # for cargo test
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from process_diagnostics import builder_diagnostic  # noqa: E402
from release_assembly import (  # noqa: E402
    MOTION_CATALOG_RESOURCES,
    VIDEO_RUNTIME_RESOURCES,
    _VideoResource,
)
from subtitle_font_assets import ensure_subtitle_fonts  # noqa: E402
from video_runtime_cache import cache_root, ensure_cached  # noqa: E402

ASSET_RIGHTS_CONTRACT = ROOT / "contracts/quality/asset-rights-policy.v1.json"
MEDIA_TOOLCHAIN_CONTRACT = ROOT / "contracts/video/ffmpeg-toolchain.v1.json"
MOTION_WORKER_CONTRACT = ROOT / "contracts/quality/motion-video-worker-package.v1.json"
MATERIAL_WORKER_CONTRACT = (
    ROOT / "contracts/quality/material-video-worker-package.v1.json"
)
SILERO_VAD_CONTRACT = ROOT / "contracts/quality/silero-vad-runtime.v1.json"
BAILIAN_MODEL_CATALOG = ROOT / "contracts/video/bailian-model-catalog.v1.json"
OFFLINE_MOTION_LOCK = ROOT / "contracts/video/offline-motion-dependencies.v1.json"
THIRD_PARTY_SOURCES_CONTRACT = ROOT / "contracts/quality/third-party-sources.v1.json"
MEDIA_TOOLCHAIN_BUILDER = ROOT / "scripts/build_video_media_toolchain.sh"
MEDIA_TOOLCHAIN_MANIFEST_WRITER = (
    ROOT / "scripts/write_video_media_toolchain_manifest.py"
)
MOTION_WORKER_BUILDER = ROOT / "scripts/build_motion_video_worker_candidate.py"
MOTION_WORKER_SOURCE = ROOT / "workers/motion_composition/worker.mjs"
MATERIAL_WORKER_BUILDER = ROOT / "scripts/build_material_video_worker_candidate.py"
MATERIAL_WORKER_SOURCE = ROOT / "workers/material_montage"
BACKEND_PACKAGE_SOURCE = ROOT / "backend/src/automation_tool"
BACKEND_SOURCE_ROOT = ROOT / "backend/src"
BACKEND_PROJECT = ROOT / "backend/pyproject.toml"
SUBTITLE_FONT_ASSETS = ROOT / "scripts/subtitle_font_assets.py"
SILERO_VAD_ASSETS = ROOT / "scripts/silero_vad_assets.py"
WINDOWS_MSYS2_ROOT = Path("C:/msys64")

# What each artifact is made of, in full. A cache key is a promise that nothing
# outside this list can change the bytes, and the promise is only as good as the
# list: `material-video-worker` used to name its two contracts and nothing else,
# so T32's fix to the Worker's web UI was committed, cached over, and shipped as
# the binary built before it. Every entry below is either a file that pins a
# version or a directory the build compiles;
# `test_no_build_driver_reads_a_repository_path_outside_its_cache_key` refuses a
# build input that is in neither these lists nor its own recorded exemption.

MEDIA_TOOLCHAIN_INPUTS: tuple[Path, ...] = (
    MEDIA_TOOLCHAIN_CONTRACT,
    # Carries the ffmpeg and x264 versions, their source digests and every
    # configure flag, so it is the pin as much as the builder.
    MEDIA_TOOLCHAIN_BUILDER,
    # Runs last and writes manifest.json into the toolchain; the release
    # verifies the package against that file.
    MEDIA_TOOLCHAIN_MANIFEST_WRITER,
)

MOTION_WORKER_INPUTS: tuple[Path, ...] = (
    MOTION_WORKER_CONTRACT,
    MOTION_WORKER_SOURCE,
    MOTION_WORKER_BUILDER,
    # Pins the digest and origin of the animation runtime that is written into
    # the package, which the Worker contract does not restate.
    OFFLINE_MOTION_LOCK,
)

MATERIAL_WORKER_INPUTS: tuple[Path, ...] = (
    MATERIAL_WORKER_CONTRACT,
    SILERO_VAD_CONTRACT,
    BAILIAN_MODEL_CATALOG,
    ASSET_RIGHTS_CONTRACT,
    # The package PyInstaller freezes, spec file included: this is the input
    # whose absence shipped a fixed Worker as its unfixed predecessor.
    MATERIAL_WORKER_SOURCE,
    MATERIAL_WORKER_BUILDER,
    BACKEND_PACKAGE_SOURCE,
    BACKEND_SOURCE_ROOT,
    BACKEND_PROJECT,
    ROOT / "contracts/quality",
    ROOT / "contracts/video",
    # Imported by the spec; decides which font bytes land in the package and
    # under what name.
    SUBTITLE_FONT_ASSETS,
    SILERO_VAD_ASSETS,
    # Stands in for `vendor/moneyprinterturbo`, whose dependency lock decides
    # every frozen distribution. The checkout is ~900 MB, far too large to
    # digest on each cache lookup, and this contract pins its exact commit --
    # `scripts/check_third_party_sources.py` is what holds the two together.
    THIRD_PARTY_SOURCES_CONTRACT,
)

MEDIA_TOOLCHAIN_TARGETS = {
    "macos": "macos-arm64",
    "windows": "windows-x86_64",
}

# The resource names, taken from the release resource contract rather than
# spelled out again here. These three are the ones this module *builds*.
RESOURCE_NAMES: tuple[str, ...] = tuple(
    resource.staging_name for resource in VIDEO_RUNTIME_RESOURCES
)

# Everything that can be *installed* into a resource root, which is a larger set
# than what is built here. The frozen catalog of animation parts is produced by
# `build_motion_catalog_release.py` with its own locked digest rather than
# cached per machine — but landing it where the resolver reads it is the same
# operation as landing a Worker, and a second copy of that operation is a second
# place for the layout to drift from the contract.
INSTALLABLE_RESOURCES: tuple[_VideoResource, ...] = (
    VIDEO_RUNTIME_RESOURCES + MOTION_CATALOG_RESOURCES
)
INSTALLABLE_NAMES: tuple[str, ...] = tuple(
    resource.staging_name for resource in INSTALLABLE_RESOURCES
)


class VideoRuntimeUnavailable(RuntimeError):
    """A video runtime resource could not be produced."""


def host_platform() -> str:
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("win"):
        return "windows"
    raise VideoRuntimeUnavailable(
        f"the video runtime is only built for macOS and Windows, not {sys.platform}"
    )


def media_toolchain_bash(*, platform: str) -> str:
    if platform != "windows":
        return "bash"

    discovered = shutil.which("bash")
    candidates = [WINDOWS_MSYS2_ROOT / "usr" / "bin" / "bash.exe"]
    if discovered is not None:
        candidates.insert(0, Path(discovered))
    for candidate in candidates:
        if candidate.is_file() and (candidate.parent / "pacman.exe").is_file():
            return str(candidate)
    raise VideoRuntimeUnavailable(
        "MSYS2 MINGW64 is required to build the Windows media toolchain"
    )


def _build_media_toolchain(destination: Path, *, platform: str) -> None:
    target = MEDIA_TOOLCHAIN_TARGETS[platform]
    bash = media_toolchain_bash(platform=platform)
    environment = None
    if platform == "windows":
        msys2_root = Path(bash).parents[2]
        environment = os.environ.copy()
        environment["MSYSTEM"] = "MINGW64"
        environment["CHERE_INVOKING"] = "1"
        environment["PATH"] = ";".join(
            (
                str(msys2_root / "mingw64" / "bin"),
                str(msys2_root / "usr" / "bin"),
                environment.get("PATH", ""),
            )
        )
    # The builder creates the directory itself and refuses to reuse one.
    completed = subprocess.run(
        [
            bash,
            str(MEDIA_TOOLCHAIN_BUILDER),
            target,
            str(destination),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )
    if completed.returncode != 0:
        # Not `splitlines()[-8:]` on one stream. That is how the Windows
        # acceptance machine reported `No working C compiler found.` as eight
        # lines of curl progress bar on 2026-07-27: carriage returns count as
        # line breaks, so one redrawn line fills any tail.
        raise VideoRuntimeUnavailable(
            "the media toolchain build failed:\n" + builder_diagnostic(completed)
        )


def _build_motion_worker(destination: Path) -> None:
    from build_motion_video_worker_candidate import build_candidate

    build_candidate(destination)


def _build_material_worker(destination: Path) -> None:
    from build_material_video_worker_candidate import build_candidate

    build_candidate(destination)


def selected_resources(
    only: Sequence[str] | None = None,
    *,
    names: tuple[str, ...] = RESOURCE_NAMES,
) -> tuple[str, ...]:
    """Which resources this invocation covers, defaulting to all of them.

    A misspelt name is refused rather than quietly selecting nothing: a run that
    installs zero resources and exits 0 is the same failure shape as the remedy
    that sent the reader here in the first place.

    `names` is what the caller can act on — the three this module builds, or the
    four that can be installed. A name is validated against every declared
    resource either way, so asking to build the catalog selects nothing to build
    rather than being reported as a typo it is not.
    """
    if only is None:
        return names
    requested = tuple(only)
    unknown = [name for name in requested if name not in INSTALLABLE_NAMES]
    if unknown:
        raise VideoRuntimeUnavailable(
            f"unknown video runtime resource(s): {', '.join(unknown)}; "
            f"declared: {', '.join(INSTALLABLE_NAMES)}"
        )
    return tuple(name for name in names if name in set(requested))


def prepare(
    *,
    platform: str | None = None,
    root: Path | None = None,
    only: Sequence[str] | None = None,
) -> Path:
    """Ensure the selected resources exist under one staging root and return it.

    `only` exists so a caller that needs one resource does not pay for the other
    two: building ffmpeg from source costs minutes, and freezing the material
    Worker costs a PyInstaller run, neither of which a `cargo test` needs. It
    selects which resources are built, never where any of them is looked up.
    """
    resolved = platform or host_platform()
    if resolved not in MEDIA_TOOLCHAIN_TARGETS:
        raise VideoRuntimeUnavailable(f"unsupported platform: {resolved}")
    # Only the three this module builds. Naming the catalog here selects nothing
    # to build, which is right: it is produced by
    # `build_motion_catalog_release.py` and merely installed from here.
    wanted = set(selected_resources(only, names=RESOURCE_NAMES))
    staging = cache_root() if root is None else Path(root)
    if "media-toolchain" in wanted:
        ensure_cached(
            name="media-toolchain",
            contracts=MEDIA_TOOLCHAIN_INPUTS,
            build=lambda destination: _build_media_toolchain(
                destination, platform=resolved
            ),
            root=staging,
        )
    if "motion-video-worker" in wanted:
        ensure_cached(
            name="motion-video-worker",
            contracts=MOTION_WORKER_INPUTS,
            build=_build_motion_worker,
            root=staging,
        )
    if "material-video-worker" in wanted:
        # The cleared subtitle fonts are the fourth locked artifact fetched
        # rather than committed. They must exist before the material Worker is
        # frozen, because its PyInstaller spec packages them, and the Worker's
        # own cache key includes the rights register so a re-pinned font
        # rebuilds the Worker too.
        ensure_subtitle_fonts(root=staging)
        ensure_cached(
            name="material-video-worker",
            contracts=MATERIAL_WORKER_INPUTS,
            build=_build_material_worker,
            root=staging,
        )
    return staging


def install(
    *,
    staging: Path,
    resource_root: Path,
    only: Sequence[str] | None = None,
    platform: str | None = None,
) -> dict[str, Path]:
    """Copy staged resources to where the production resolver reads them.

    The layout comes from `release_assembly.VIDEO_RUNTIME_RESOURCES`, which is
    derived from `contracts/quality/release-package-resources.v1.json` -- the
    same file `motion_authoring_runtime.rs` reads to decide where to look. One
    declaration, so the two cannot drift into disagreeing about the path.

    Unlike `release_assembly.install_video_runtime`, an existing tree is
    replaced rather than refused. That function assembles a package exactly
    once and a pre-existing tree there means something went wrong; this one
    serves a developer loop where reinstalling after a rebuild is the normal
    case, and refusing would only teach people to `rm -rf` first.

    Every declared required file is checked after the copy. The bug this whole
    path exists to fix was a producer that exited 0 without producing, so exit
    status alone is not accepted as evidence here either.
    """
    resolved = platform or host_platform()
    wanted = set(selected_resources(only, names=INSTALLABLE_NAMES))
    installed: dict[str, Path] = {}
    resource_root = Path(resource_root)
    if resource_root.is_symlink():
        raise VideoRuntimeUnavailable(
            f"the resource root itself may not be a symlink: {resource_root}"
        )
    for resource in INSTALLABLE_RESOURCES:
        if resource.staging_name not in wanted:
            continue
        source = Path(staging) / resource.staging_name
        if not source.is_dir():
            raise VideoRuntimeUnavailable(
                f"the staging tree carries no {resource.staging_name} at {source}"
            )
        top = resource_root / resource.installed_parts[0]
        if top.is_symlink() or top.is_file():
            top.unlink()
        destination = resource_root.joinpath(*resource.installed_parts)
        if destination.is_symlink() or destination.is_file():
            destination.unlink()
        elif destination.is_dir():
            shutil.rmtree(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination, symlinks=True)
        for name in resource.required_for(resolved):
            payload = destination / name
            if not payload.is_file() or payload.stat().st_size == 0:
                raise VideoRuntimeUnavailable(
                    f"{resource.staging_name} was installed to {destination} but "
                    f"{name} is missing or empty, so a reader would find the "
                    "directory and still fail"
                )
        installed[resource.staging_name] = destination
    return installed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=sorted(MEDIA_TOOLCHAIN_TARGETS))
    parser.add_argument("--root", type=Path)
    parser.add_argument("--print", action="store_true", dest="print_root")
    parser.add_argument(
        "--only",
        action="append",
        metavar="RESOURCE",
        help=(
            "restrict to one resource; repeatable. One of: "
            + ", ".join(INSTALLABLE_NAMES)
        ),
    )
    parser.add_argument(
        "--install-into",
        type=Path,
        metavar="DIRECTORY",
        help=(
            "after staging, copy the resources to the directory a reader "
            "resolves them from (for cargo test: "
            "frontend/src-tauri/target/debug)"
        ),
    )
    arguments = parser.parse_args()
    staging = prepare(
        platform=arguments.platform, root=arguments.root, only=arguments.only
    )
    if arguments.install_into is not None:
        installed = install(
            staging=staging,
            resource_root=arguments.install_into,
            only=arguments.only,
            platform=arguments.platform,
        )
        for name, destination in sorted(installed.items()):
            print(f"installed {name} -> {destination}")
    if arguments.print_root:
        print(staging)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
