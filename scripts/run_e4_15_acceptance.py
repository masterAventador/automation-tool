#!/usr/bin/env python3
"""Build and audit one ephemeral production-mode desktop artifact for E4-15."""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_ROOT = REPOSITORY_ROOT / "frontend"
CARGO_MANIFEST = FRONTEND_ROOT / "src-tauri" / "Cargo.toml"
TAURI_CONFIG = FRONTEND_ROOT / "src-tauri" / "tauri.conf.json"
PRODUCTION_ASSETS = FRONTEND_ROOT / "dist"
VERIFYING_KEY_ENVIRONMENT = "AUTOMATION_TOOL_EXECUTOR_VERIFYING_KEY"
UPDATE_ENDPOINT_ENVIRONMENT = "AUTOMATION_TOOL_UPDATE_ENDPOINT"
UPDATE_PUBLIC_KEY_ENVIRONMENT = "AUTOMATION_TOOL_UPDATE_PUBLIC_KEY"

# Public key derived from an acceptance-only seed. The seed is not stored or used to sign a
# distributable package, and this temporary artifact is deleted after the audit.
ACCEPTANCE_VERIFYING_KEY = "GX9rI-FshTLGq8g4-s1ep4m-DHaykgM0A5v6iz02jWE"
WEAK_VERIFYING_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
REQUIRED_KEY_ERROR = "release Executor verification key is required"
INVALID_KEY_ERROR = "release Executor verification key is invalid"
REQUIRED_UPDATE_ERROR = "release update configuration is required"
INVALID_UPDATE_ERROR = "release update configuration is invalid"
ACCEPTANCE_UPDATE_ENDPOINT = (
    "https://updates.acceptance.invalid/desktop-updates/v1/stable/"
    "{{target}}/{{arch}}/{{current_version}}"
)
ACCEPTANCE_UPDATE_PUBLIC_KEY = base64.b64encode(
    (
        "untrusted comment: minisign public key E7620F1842B4E81F\n"
        "RWQf6LRCGA9i53mlYecO4IzT51TGPpvWucNSCh1CBM0QTaLn73Y7GFO3"
    ).encode()
).decode()


def isolated_environment(target_directory: Path) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("AUTOMATION_TOOL_") and key != "CARGO_TARGET_DIR"
    }
    environment["CARGO_TARGET_DIR"] = os.fspath(target_directory)
    return environment


def expect_release_key_failure(environment: dict[str, str], expected_error: str) -> None:
    completed = subprocess.run(
        [
            "cargo",
            "check",
            "--manifest-path",
            os.fspath(CARGO_MANIFEST),
            "--locked",
            "--release",
            "--no-default-features",
        ],
        cwd=FRONTEND_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    output = f"{completed.stdout}\n{completed.stderr}"
    if completed.returncode == 0 or expected_error not in output:
        raise RuntimeError(
            f"E4-15 release key boundary failed safely but for the wrong reason:\n{output[-4000:]}"
        )


def release_binary(target_directory: Path) -> Path:
    name = "automation-tool-desktop.exe" if sys.platform == "win32" else "automation-tool-desktop"
    binary = target_directory / "release" / name
    if not binary.is_file():
        raise RuntimeError("E4-15 production release binary was not created")
    return binary


def pnpm_executable() -> str:
    name = "pnpm.cmd" if sys.platform == "win32" else "pnpm"
    executable = shutil.which(name)
    if executable is None:
        raise RuntimeError("E4-15 pnpm executable is unavailable")
    return executable


def run_checked(command: list[str], *, environment: dict[str, str]) -> None:
    subprocess.run(
        command,
        cwd=FRONTEND_ROOT,
        env=environment,
        timeout=1800,
        check=True,
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="automation-tool-e415-target-") as temporary:
        target_directory = Path(temporary)
        environment = isolated_environment(target_directory)

        print("[E4-15] Verifying that a release build without a trust root fails closed")
        expect_release_key_failure(environment, REQUIRED_KEY_ERROR)

        print("[E4-15] Verifying that a malformed release trust root fails closed")
        invalid_environment = dict(environment)
        invalid_environment[VERIFYING_KEY_ENVIRONMENT] = "not-a-valid-release-key"
        expect_release_key_failure(invalid_environment, INVALID_KEY_ERROR)

        print("[E4-15] Verifying that a weak Ed25519 release trust root fails closed")
        weak_environment = dict(environment)
        weak_environment[VERIFYING_KEY_ENVIRONMENT] = WEAK_VERIFYING_KEY
        expect_release_key_failure(weak_environment, INVALID_KEY_ERROR)

        print("[E4-15] Verifying that a release build requires an update trust root")
        missing_update_environment = dict(environment)
        missing_update_environment[VERIFYING_KEY_ENVIRONMENT] = ACCEPTANCE_VERIFYING_KEY
        expect_release_key_failure(missing_update_environment, REQUIRED_UPDATE_ERROR)

        print("[E4-15] Verifying that an invalid release update origin fails closed")
        invalid_update_environment = dict(missing_update_environment)
        invalid_update_environment[UPDATE_ENDPOINT_ENVIRONMENT] = (
            "http://updates.acceptance.invalid/{{target}}/{{arch}}/{{current_version}}"
        )
        invalid_update_environment[UPDATE_PUBLIC_KEY_ENVIRONMENT] = ACCEPTANCE_UPDATE_PUBLIC_KEY
        expect_release_key_failure(invalid_update_environment, INVALID_UPDATE_ERROR)

        print("[E4-15] Building one ephemeral production-mode Tauri binary")
        release_environment = dict(environment)
        release_environment[VERIFYING_KEY_ENVIRONMENT] = ACCEPTANCE_VERIFYING_KEY
        release_environment[UPDATE_ENDPOINT_ENVIRONMENT] = ACCEPTANCE_UPDATE_ENDPOINT
        release_environment[UPDATE_PUBLIC_KEY_ENVIRONMENT] = ACCEPTANCE_UPDATE_PUBLIC_KEY
        run_checked(
            [pnpm_executable(), "tauri", "build", "--no-bundle"],
            environment=release_environment,
        )

        binary = release_binary(target_directory)
        print("[E4-15] Auditing the real release binary and production dependency tree")
        run_checked(
            [
                "node",
                "scripts/audit-production-package.mjs",
                "--binary",
                os.fspath(binary),
                "--cargo-manifest",
                os.fspath(CARGO_MANIFEST),
                "--tauri-config",
                os.fspath(TAURI_CONFIG),
                "--dist",
                os.fspath(PRODUCTION_ASSETS),
            ],
            environment=release_environment,
        )

    print("[E4-15] Ephemeral release package audit passed and exact build resources were removed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
