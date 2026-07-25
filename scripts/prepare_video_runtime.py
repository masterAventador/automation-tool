#!/usr/bin/env python3
"""Produce the three video runtime resources the production build needs.

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

Usage:
    python3 scripts/prepare_video_runtime.py            # ensure all three
    python3 scripts/prepare_video_runtime.py --print    # ensure and print root
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from video_runtime_cache import cache_root, ensure_cached  # noqa: E402

MEDIA_TOOLCHAIN_CONTRACT = ROOT / "contracts/video/ffmpeg-toolchain.v1.json"
MOTION_WORKER_CONTRACT = ROOT / "contracts/quality/motion-video-worker-package.v1.json"
MATERIAL_WORKER_CONTRACT = (
    ROOT / "contracts/quality/material-video-worker-package.v1.json"
)
MEDIA_TOOLCHAIN_BUILDER = ROOT / "scripts/build_video_media_toolchain.sh"
MOTION_WORKER_SOURCE = ROOT / "workers/motion_composition/worker.mjs"

MEDIA_TOOLCHAIN_TARGETS = {
    "macos": "macos-arm64",
    "windows": "windows-x86_64",
}


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


def _build_media_toolchain(destination: Path, *, platform: str) -> None:
    target = MEDIA_TOOLCHAIN_TARGETS[platform]
    # The builder creates the directory itself and refuses to reuse one.
    completed = subprocess.run(
        ["bash", str(MEDIA_TOOLCHAIN_BUILDER), target, str(destination)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        tail = (completed.stderr or completed.stdout or "").strip().splitlines()[-8:]
        raise VideoRuntimeUnavailable(
            "the media toolchain build failed:\n" + "\n".join(tail)
        )


def _build_motion_worker(destination: Path) -> None:
    from build_motion_video_worker_candidate import build_candidate

    build_candidate(destination)


def _build_material_worker(destination: Path) -> None:
    from build_material_video_worker_candidate import build_candidate

    build_candidate(destination)


def prepare(*, platform: str | None = None, root: Path | None = None) -> Path:
    """Ensure all three resources exist under one staging root and return it."""
    resolved = platform or host_platform()
    if resolved not in MEDIA_TOOLCHAIN_TARGETS:
        raise VideoRuntimeUnavailable(f"unsupported platform: {resolved}")
    staging = cache_root() if root is None else Path(root)
    ensure_cached(
        name="media-toolchain",
        contracts=[MEDIA_TOOLCHAIN_CONTRACT, MEDIA_TOOLCHAIN_BUILDER],
        build=lambda destination: _build_media_toolchain(
            destination, platform=resolved
        ),
        root=staging,
    )
    ensure_cached(
        name="motion-video-worker",
        contracts=[MOTION_WORKER_CONTRACT, MOTION_WORKER_SOURCE],
        build=_build_motion_worker,
        root=staging,
    )
    ensure_cached(
        name="material-video-worker",
        contracts=[MATERIAL_WORKER_CONTRACT],
        build=_build_material_worker,
        root=staging,
    )
    return staging


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=sorted(MEDIA_TOOLCHAIN_TARGETS))
    parser.add_argument("--root", type=Path)
    parser.add_argument("--print", action="store_true", dest="print_root")
    arguments = parser.parse_args()
    staging = prepare(platform=arguments.platform, root=arguments.root)
    if arguments.print_root:
        print(staging)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
