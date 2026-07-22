#!/usr/bin/env python3
"""VF-04 deterministic acceptance entrypoint."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(*command: str) -> None:
    subprocess.run(command, cwd=ROOT, check=True)


def main() -> int:
    required = (
        ROOT / "contracts/video/ffmpeg-toolchain.v1.json",
        ROOT / "scripts/check_video_media_toolchain.py",
        ROOT / "frontend/src-tauri/src/video_media_toolchain.rs",
        ROOT / "frontend/src-tauri/tests/video_media_toolchain.rs",
    )
    missing = [
        path.relative_to(ROOT).as_posix() for path in required if not path.is_file()
    ]
    if missing:
        raise SystemExit(f"VF-04 missing deliverables: {', '.join(missing)}")

    run(sys.executable, "scripts/check_video_media_toolchain.py", "--self-test")
    run(
        "cargo",
        "test",
        "--manifest-path",
        "frontend/src-tauri/Cargo.toml",
        "--test",
        "video_media_toolchain",
    )
    print("VF-04 video media toolchain acceptance passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
