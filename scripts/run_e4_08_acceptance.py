#!/usr/bin/env python3
"""Run the bounded Rust Executor supervisor against a real signed Windows package."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import tempfile
from pathlib import Path

from run_e4_07_acceptance import (
    EXECUTOR_ID,
    INSTALLATION_ID,
    RUST_ROOT,
    RunningControlPlane,
    build_signed_executor,
    start_control_plane,
)


def run_rust_supervisor(
    package_root: Path, control_plane: RunningControlPlane, workspace: Path
) -> None:
    configuration_path = workspace / "executor-manager.json"
    configuration_path.write_text(
        json.dumps(
            {
                "packageRoot": os.fspath(package_root),
                "websocketUrl": (f"ws://127.0.0.1:{control_plane.port}/api/v1/executors/connect"),
                "sessionToken": control_plane.session_token,
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
            "real_packaged_executor_enforces_bounded_restart_policy",
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
    if (
        control_plane.session_token in completed.stdout
        or control_plane.session_token in completed.stderr
    ):
        raise RuntimeError("E4-08 acceptance reflected the Control Plane session")
    if completed.returncode != 0 or "1 passed; 0 failed" not in completed.stdout:
        diagnostic = (completed.stdout + "\n" + completed.stderr).replace(
            control_plane.session_token,
            "[REDACTED]",
        )[-4000:]
        raise RuntimeError(f"E4-08 Rust supervisor acceptance failed\n{diagnostic}")


def main() -> None:
    if platform.system() not in {"Darwin", "Windows"}:
        raise RuntimeError("E4-08 local acceptance requires macOS or Windows")
    control_plane = start_control_plane()
    try:
        with tempfile.TemporaryDirectory(prefix="automation-tool-e4-08-") as directory:
            workspace = Path(directory).resolve(strict=True)
            package_root = build_signed_executor(workspace, build_id="e4-08-real")
            run_rust_supervisor(package_root, control_plane, workspace)
        observed = [control_plane.registry.events.get(timeout=10) for _ in range(9)]
        expected = ["registered", "heartbeat", "unregistered"] * 3
        if observed != expected:
            raise RuntimeError("E4-08 bounded restart facts did not converge")
    except Exception as error:
        observed = control_plane.registry.drain_event_names()
        raise RuntimeError(f"{error}\nControl Plane events: {observed!r}") from None
    finally:
        control_plane.stop()
    print("E4-08 acceptance passed: two restarts then stable stop")


if __name__ == "__main__":
    main()
