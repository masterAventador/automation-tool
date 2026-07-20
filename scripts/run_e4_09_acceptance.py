#!/usr/bin/env python3
"""Verify Windows Job Object cleanup with a real packaged descendant process."""

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
PROCESS_TREE_PROBE = (
    REPOSITORY_ROOT / "backend" / "tests" / "fixtures" / "executor_process_tree_probe.py"
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
            os.fspath(PROCESS_TREE_PROBE),
        ],
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("E4-09 PyInstaller process-tree probe build failed")
    package_root = distribution / "automation-tool-executor"
    architecture = "x86_64" if platform.machine().lower() in {"x86_64", "amd64"} else "aarch64"
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
            "e4-09-windows-job",
            "--platform",
            "windows",
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
        raise RuntimeError("E4-09 process-tree probe signing failed")
    return package_root


def run_rust_job_test(package_root: Path, workspace: Path) -> None:
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
            "real_packaged_executor_cleans_its_windows_job_tree",
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
        raise RuntimeError(f"E4-09 Windows Job acceptance failed\n{diagnostic}")


def main() -> None:
    if platform.system() != "Windows":
        raise RuntimeError("E4-09 local Job Object acceptance requires Windows")
    with tempfile.TemporaryDirectory(prefix="automation-tool-e4-09-") as directory:
        workspace = Path(directory).resolve(strict=True)
        package_root = build_signed_probe(workspace)
        run_rust_job_test(package_root, workspace)
    print("E4-09 acceptance passed: Windows Job cleaned every packaged process tree")


if __name__ == "__main__":
    main()
