#!/usr/bin/env python3
"""Verify bounded and redacted stderr from a real packaged macOS/Windows Executor."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

from run_e4_07_acceptance import (
    BACKEND_ROOT,
    EXECUTOR_ID,
    INSTALLATION_ID,
    RUST_ROOT,
    TEST_SIGNING_SEED,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DIAGNOSTIC_PROBE = (
    REPOSITORY_ROOT / "backend" / "tests" / "fixtures" / "executor_diagnostics_probe.py"
)


def build_signed_probe(workspace: Path) -> Path:
    distribution = workspace / "dist"
    work = workspace / "build"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onedir",
            "--name",
            "automation-tool-executor",
            "--distpath",
            os.fspath(distribution),
            "--workpath",
            os.fspath(work),
            os.fspath(DIAGNOSTIC_PROBE),
        ],
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("E4-10 PyInstaller diagnostic probe build failed")
    package_root = distribution / "automation-tool-executor"
    architecture = "x86_64" if platform.machine().lower() in {"x86_64", "amd64"} else "aarch64"
    target_platform = "windows" if platform.system() == "Windows" else "macos"
    manifest = subprocess.run(
        [
            sys.executable,
            "-m",
            "automation_tool.executor.package_manifest",
            "--bundle-dir",
            os.fspath(package_root),
            "--executor-version",
            "0.1.0",
            "--build-id",
            f"e4-10-{target_platform}-stderr",
            "--platform",
            target_platform,
            "--architecture",
            architecture,
        ],
        cwd=BACKEND_ROOT,
        input=TEST_SIGNING_SEED,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if manifest.returncode != 0:
        raise RuntimeError("E4-10 diagnostic probe signing failed")
    return package_root


def run_rust_diagnostic_test(package_root: Path, workspace: Path) -> None:
    configuration_path = workspace / "executor-manager.json"
    configuration_path.write_text(
        json.dumps(
            {
                "packageRoot": os.fspath(package_root),
                "websocketUrl": "ws://127.0.0.1:9/api/v1/executors/connect",
                "sessionToken": "atds1.private-control-plane-session",
                "installationId": str(INSTALLATION_ID),
                "executorId": str(EXECUTOR_ID),
                "stateDirectory": os.fspath(workspace / "executor-state"),
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    configuration_path.chmod(0o600)
    environment = os.environ.copy()
    environment["AUTOMATION_TOOL_E407_CONFIGURATION"] = os.fspath(configuration_path)
    completed = subprocess.run(
        [
            "cargo",
            "test",
            "--locked",
            "--features",
            "control-plane-e2e",
            "--test",
            "executor_manager_packaged",
            "real_packaged_executor_bounds_and_redacts_stderr",
            "--",
            "--ignored",
            "--exact",
        ],
        cwd=RUST_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if completed.returncode != 0 or "1 passed; 0 failed" not in completed.stdout:
        diagnostic = (completed.stdout + "\n" + completed.stderr)[-4000:]
        raise RuntimeError(f"E4-10 packaged stderr acceptance failed\n{diagnostic}")


def main() -> None:
    if platform.system() not in {"Darwin", "Windows"}:
        raise RuntimeError("E4-10 local stderr acceptance requires macOS or Windows")
    with tempfile.TemporaryDirectory(prefix="automation-tool-e4-10-") as directory:
        workspace = Path(directory).resolve(strict=True)
        package_root = build_signed_probe(workspace)
        run_rust_diagnostic_test(package_root, workspace)
    print("E4-10 acceptance passed: packaged stderr is redacted and bounded")


if __name__ == "__main__":
    main()
