#!/usr/bin/env python3
"""Build and audit one disposable native Windows Executor candidate."""

from __future__ import annotations

import base64
import os
import platform
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from automation_tool.executor.package_manifest import write_signed_executor_manifest
from automation_tool.executor.windows_candidate import build_windows_executor_candidate

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
JOB_OBJECT_ACCEPTANCE = REPOSITORY_ROOT / "scripts" / "run_e4_09_acceptance.py"


def require_windows() -> str:
    if sys.platform != "win32" or platform.system() != "Windows":
        raise RuntimeError("P9-02 acceptance requires Windows")
    machine = platform.machine().lower()
    if machine in {"amd64", "x86_64"}:
        return "x86_64"
    if machine in {"aarch64", "arm64"}:
        return "aarch64"
    raise RuntimeError("P9-02 Windows architecture is unsupported")


def clean_runtime_environment() -> dict[str, str]:
    allowed = ("COMSPEC", "PATH", "SYSTEMROOT", "TEMP", "TMP", "WINDIR")
    return {name: os.environ[name] for name in allowed if name in os.environ}


def probe_uia_runtime() -> None:
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if powershell is None:
        raise RuntimeError("P9-02 Windows PowerShell is unavailable")
    script = (
        "$ErrorActionPreference='Stop';"
        "Add-Type -AssemblyName UIAutomationClient;"
        "$root=[System.Windows.Automation.AutomationElement]::RootElement;"
        "if($null -eq $root){exit 3}"
    )
    completed = subprocess.run(
        [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ],
        capture_output=True,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0:
        raise RuntimeError("P9-02 Windows UIAutomation runtime probe failed")


def verify_job_object_boundary() -> None:
    completed = subprocess.run(
        [sys.executable, os.fspath(JOB_OBJECT_ACCEPTANCE)],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )
    if completed.returncode != 0 or "Windows Job cleaned every packaged process tree" not in (
        completed.stdout
    ):
        diagnostic = f"{completed.stdout}\n{completed.stderr}"[-4000:]
        raise RuntimeError(f"P9-02 Windows Job Object acceptance failed\n{diagnostic}")


def main() -> int:
    architecture = require_windows()
    with tempfile.TemporaryDirectory(prefix="automation-tool-p902-acceptance-") as raw:
        temporary = Path(raw).resolve(strict=True)
        bundle = temporary / "executor" / "automation-tool-executor"
        print("[P9-02] Building the isolated Windows Executor candidate")
        raw_audit = build_windows_executor_candidate(
            backend_root=BACKEND_ROOT,
            output_directory=bundle,
        )

        seed = secrets.token_bytes(32)
        signed = write_signed_executor_manifest(
            bundle_directory=bundle,
            executor_version="0.1.0",
            build_id="p9-02-windows-candidate",
            target_platform="windows",
            target_architecture=architecture,
            signing_private_key=seed,
        )
        public_key = Ed25519PrivateKey.from_private_bytes(seed).public_key()
        public_key.verify(signed.signature_bytes, signed.manifest_bytes)
        encoded_public_key = base64.urlsafe_b64encode(public_key.public_bytes_raw()).rstrip(b"=")
        if len(encoded_public_key) != 43:
            raise RuntimeError("P9-02 Executor public key encoding is inconsistent")

        entrypoint = bundle / "automation-tool-executor.exe"
        startup = subprocess.run(
            [os.fspath(entrypoint)],
            input=b"",
            capture_output=True,
            check=False,
            env=clean_runtime_environment(),
            timeout=30,
        )
        if (
            startup.returncode != 2
            or startup.stdout != b""
            or startup.stderr != b"Local Executor bootstrap is rejected\n"
        ):
            raise RuntimeError("P9-02 frozen startup boundary failed")

        print("[P9-02] Probing the read-only Windows UIAutomation runtime")
        probe_uia_runtime()
        print("[P9-02] Reusing the native Windows Job Object descendant cleanup acceptance")
        verify_job_object_boundary()

    print(
        "[P9-02] Windows Executor candidate passed PE, dependency, startup, UIA, Manifest, "
        f"and Job Object audits: {raw_audit.file_count} files, {raw_audit.pe_file_count} "
        f"PE files, {raw_audit.package_size} bytes"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
