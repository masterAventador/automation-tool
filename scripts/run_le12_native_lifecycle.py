"""Run LE-12's real-process lifecycle acceptance on macOS or Windows."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
TAURI_ROOT = REPOSITORY_ROOT / "frontend" / "src-tauri"


def _regular_executable(candidate: Path) -> Path:
    resolved = candidate.resolve(strict=True)
    if not resolved.is_file() or (os.name != "nt" and not os.access(resolved, os.X_OK)):
        raise SystemExit("LE-12 native lifecycle executable rejected")
    return resolved


def _second_executable(python: Path) -> Path:
    names = ("cmd.exe", "powershell.exe") if os.name == "nt" else ("true", "sh")
    for name in names:
        located = shutil.which(name)
        if located is None:
            continue
        candidate = _regular_executable(Path(located))
        if candidate != python:
            return candidate
    raise SystemExit("LE-12 native lifecycle needs a second executable fixture")


def main() -> int:
    python = _regular_executable(Path(sys.executable))
    second = _second_executable(python)
    cargo = shutil.which("cargo")
    if cargo is None:
        raise SystemExit("LE-12 native lifecycle needs Cargo")
    environment = os.environ.copy()
    environment.update(
        {
            "LE12_PYTHON_EXECUTABLE": os.fspath(python),
            "LE12_MEDIA_TOOL_A": os.fspath(python),
            "LE12_MEDIA_TOOL_B": os.fspath(second),
            "LE12_PARENT_SECRET": "must-not-reach-worker",
        }
    )
    completed = subprocess.run(
        [
            cargo,
            "test",
            "--test",
            "local_editing_lifecycle_platform",
            "--",
            "--nocapture",
        ],
        cwd=TAURI_ROOT,
        env=environment,
        check=False,
    )
    if completed.returncode == 0:
        print("LE-12 native lifecycle acceptance passed")
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
