from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
BUNDLE_NAME = "automation-tool-executor"
PRIVATE_SESSION = "private-packaged-session"


def bootstrap() -> bytes:
    return (
        json.dumps(
            {
                "bootstrap_version": "1",
                "websocket_url": "ws://127.0.0.1:9/api/v1/executors/connect",
                "session_token": PRIVATE_SESSION,
                "installation_id": "123e4567-e89b-42d3-a456-426614174003",
                "executor_id": "123e4567-e89b-42d3-a456-426614174004",
                "heartbeat_interval_seconds": 1,
            },
            separators=(",", ":"),
        )
        + "\n"
    ).encode()


def test_pyinstaller_onedir_bundle_starts_without_python_or_playwright(tmp_path: Path) -> None:
    distribution_root = tmp_path / "dist"
    work_root = tmp_path / "build"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            os.fspath(distribution_root),
            "--workpath",
            os.fspath(work_root),
            os.fspath(BACKEND_ROOT / "automation-tool-executor.spec"),
        ],
        cwd=BACKEND_ROOT,
        capture_output=True,
        check=False,
        text=True,
        timeout=180,
    )

    assert completed.returncode == 0, completed.stderr
    suffix = ".exe" if sys.platform == "win32" else ""
    executable = distribution_root / BUNDLE_NAME / f"{BUNDLE_NAME}{suffix}"
    assert executable.is_file()

    startup = subprocess.run(
        [os.fspath(executable)],
        input=b"",
        capture_output=True,
        check=False,
        timeout=20,
        env={"PATH": os.defpath},
    )

    assert startup.returncode == 2
    assert startup.stdout == b""
    assert startup.stderr == b"Local Executor bootstrap is rejected\n"

    unavailable = subprocess.run(
        [os.fspath(executable)],
        input=bootstrap(),
        capture_output=True,
        check=False,
        timeout=20,
        env={"PATH": os.defpath},
    )
    assert unavailable.returncode == 1
    assert unavailable.stdout == b""
    assert unavailable.stderr == b"Local Executor process is unavailable\n"
    assert PRIVATE_SESSION.encode() not in unavailable.stderr

    assert not any("playwright" in path.name.lower() for path in distribution_root.rglob("*"))
    analysis_text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore") for path in work_root.rglob("*.toc")
    )
    assert "playwright" not in analysis_text.lower()
