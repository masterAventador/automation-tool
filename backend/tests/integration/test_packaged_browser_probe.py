from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_ROOT = BACKEND_ROOT.parent
PROBE_NAME = "automation-tool-browser-probe"
PROBE_ENVIRONMENT = "AUTOMATION_TOOL_B507_PACKAGED_PROBE"


def test_frozen_runtime_launches_a_rust_authorized_system_browser(tmp_path: Path) -> None:
    if sys.platform not in {"darwin", "win32"}:
        pytest.skip("B5-07 supports only the two desktop target platforms")
    distribution_root = tmp_path / "dist"
    work_root = tmp_path / "build"
    spec_root = tmp_path / "spec"
    build = subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onedir",
            "--name",
            PROBE_NAME,
            "--paths",
            os.fspath(BACKEND_ROOT / "src"),
            "--collect-all",
            "playwright",
            "--distpath",
            os.fspath(distribution_root),
            "--workpath",
            os.fspath(work_root),
            "--specpath",
            os.fspath(spec_root),
            os.fspath(BACKEND_ROOT / "tests/fixtures/packaged_browser_probe.py"),
        ],
        cwd=BACKEND_ROOT,
        capture_output=True,
        check=False,
        text=True,
        timeout=180,
    )
    assert build.returncode == 0, build.stderr

    suffix = ".exe" if sys.platform == "win32" else ""
    probe = distribution_root / PROBE_NAME / f"{PROBE_NAME}{suffix}"
    assert probe.is_file()
    inventory = tuple(path.relative_to(probe.parent).as_posix() for path in probe.parent.rglob("*"))
    directory_names = tuple(path.name.lower() for path in probe.parent.rglob("*") if path.is_dir())
    assert any("playwright" in path.lower() for path in inventory)
    assert not any(
        name == ".local-browsers"
        or name.startswith(("chromium-", "firefox-", "webkit-", "ffmpeg-"))
        for name in directory_names
    )

    environment = os.environ.copy()
    environment[PROBE_ENVIRONMENT] = os.fspath(probe)
    acceptance = subprocess.run(
        [
            "cargo",
            "test",
            "--locked",
            "--manifest-path",
            os.fspath(REPOSITORY_ROOT / "frontend/src-tauri/Cargo.toml"),
            "--test",
            "browser_packaged_runtime",
            "--",
            "--ignored",
            "--exact",
            "packaged_runtime_launches_a_trusted_browser_with_a_locked_private_profile",
            "--nocapture",
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
        timeout=180,
    )
    assert acceptance.returncode == 0, acceptance.stdout + acceptance.stderr
    assert os.fspath(probe) not in acceptance.stdout
    assert os.fspath(probe) not in acceptance.stderr
