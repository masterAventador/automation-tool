#!/usr/bin/env python3
"""Run the complete release-bundle audit through the current platform candidate."""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = REPOSITORY_ROOT / "scripts"


def platform_runner() -> Path:
    system = platform.system()
    if system == "Darwin" and sys.platform == "darwin":
        return SCRIPTS_ROOT / "run_p9_03_acceptance.py"
    if system == "Windows" and sys.platform == "win32":
        return SCRIPTS_ROOT / "run_p9_04_acceptance.py"
    raise RuntimeError("P9-05 release bundle acceptance requires macOS or Windows")


def main() -> int:
    completed = subprocess.run(
        [sys.executable, os.fspath(platform_runner())],
        cwd=REPOSITORY_ROOT,
        check=False,
        timeout=3600,
    )
    if completed.returncode != 0:
        raise RuntimeError("P9-05 platform candidate bundle audit failed")
    print("[P9-05] Complete platform release bundle audit passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
