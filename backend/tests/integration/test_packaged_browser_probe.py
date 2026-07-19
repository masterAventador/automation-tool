from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_ROOT = BACKEND_ROOT.parent
PROBE_NAME = "automation-tool-executor"
PROBE_ENVIRONMENT = "AUTOMATION_TOOL_B507_PACKAGED_PROBE"
VISIBLE_BROWSER_ENVIRONMENT = "AUTOMATION_TOOL_ALLOW_VISIBLE_BROWSER_TESTS"
TEST_SIGNING_SEED = bytes(range(32))


@pytest.mark.skipif(
    os.environ.get(VISIBLE_BROWSER_ENVIRONMENT) != "1",
    reason="explicit visible-browser acceptance; routine local tests stay headless",
)
def test_frozen_runtime_launches_a_rust_authorized_system_browser(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    def cleanup() -> None:
        if tmp_path.exists():
            shutil.rmtree(tmp_path)

    request.addfinalizer(cleanup)
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

    architecture = "x86_64" if platform.machine().lower() in {"x86_64", "amd64"} else "aarch64"
    manifest = subprocess.run(
        [
            sys.executable,
            "-m",
            "automation_tool.executor.package_manifest",
            "--bundle-dir",
            os.fspath(probe.parent),
            "--executor-version",
            "0.1.0",
            "--build-id",
            "b5-08-browser-runtime",
            "--platform",
            "windows" if sys.platform == "win32" else "macos",
            "--architecture",
            architecture,
        ],
        cwd=BACKEND_ROOT,
        input=TEST_SIGNING_SEED,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert manifest.returncode == 0, manifest.stderr
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
