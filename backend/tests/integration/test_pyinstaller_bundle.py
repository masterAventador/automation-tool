from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from pathlib import Path

from automation_tool.executor.package_manifest import (
    EXECUTOR_MANIFEST_FILE_NAME,
    EXECUTOR_SIGNATURE_FILE_NAME,
)

BACKEND_ROOT = Path(__file__).resolve().parents[2]
BUNDLE_NAME = "automation-tool-executor"
PRIVATE_SESSION = "private-packaged-session"
LOCAL_SESSION_TOKEN = "06" * 32
TEST_SIGNING_KEY = bytes(range(32))


def bootstrap(state_directory: Path) -> bytes:
    return (
        json.dumps(
            {
                "bootstrap_version": "1",
                "websocket_url": "ws://127.0.0.1:9/api/v1/executors/connect",
                "local_session_token": LOCAL_SESSION_TOKEN,
                "session_token": PRIVATE_SESSION,
                "installation_id": "123e4567-e89b-42d3-a456-426614174003",
                "executor_id": "123e4567-e89b-42d3-a456-426614174004",
                "heartbeat_interval_seconds": 1,
                "state_directory": str(state_directory),
            },
            separators=(",", ":"),
        )
        + "\n"
    ).encode()


def test_pyinstaller_onedir_bundle_starts_without_python_and_contains_playwright(
    tmp_path: Path,
) -> None:
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

    manifest = subprocess.run(
        [
            sys.executable,
            "-m",
            "automation_tool.executor.package_manifest",
            "--bundle-dir",
            os.fspath(executable.parent),
            "--executor-version",
            "0.1.0",
            "--build-id",
            "pyinstaller-integration",
            "--platform",
            "windows" if sys.platform == "win32" else "macos",
            "--architecture",
            "x86_64" if platform.machine().lower() in {"x86_64", "amd64"} else "aarch64",
        ],
        input=TEST_SIGNING_KEY,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert manifest.returncode == 0, manifest.stderr
    manifest_document = json.loads((executable.parent / EXECUTOR_MANIFEST_FILE_NAME).read_bytes())
    assert manifest_document["entrypoint"] == executable.name
    assert manifest_document["package_size"] > executable.stat().st_size
    assert (executable.parent / EXECUTOR_SIGNATURE_FILE_NAME).read_bytes().startswith(b"atems1.")

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
        input=bootstrap(tmp_path / "executor-state"),
        capture_output=True,
        check=False,
        timeout=45,
        env={"PATH": os.defpath},
    )
    assert unavailable.returncode == 1
    assert unavailable.stdout == b""
    assert unavailable.stderr == b"Local Executor process is unavailable\n"
    assert PRIVATE_SESSION.encode() not in unavailable.stderr

    inventory = tuple(
        path.relative_to(executable.parent).as_posix() for path in executable.parent.rglob("*")
    )
    directory_names = tuple(
        path.name.lower() for path in executable.parent.rglob("*") if path.is_dir()
    )
    assert any("playwright" in path.lower() for path in inventory)
    assert not any(
        name == ".local-browsers"
        or name.startswith(("chromium-", "firefox-", "webkit-", "ffmpeg-"))
        for name in directory_names
    )
    analysis_text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore") for path in work_root.rglob("*.toc")
    )
    assert "playwright" in analysis_text.lower()
    assert "automation_tool.executor.browser_runtime" in analysis_text
