#!/usr/bin/env python3
"""Verify C10-07 compiled Demo Profile signing and local/demo isolation."""

from __future__ import annotations

import base64
import json
import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_ROOT = REPOSITORY_ROOT / "frontend"
MANIFEST = {
    "version": "customer-demo-profile.v1",
    "profile": "demo",
    "profileId": "demo-acceptance",
    "baseUrl": "https://api.automation-tool.test",
    "allowedHosts": ["api.automation-tool.test"],
}
PROFILE_ENVIRONMENT = (
    "AUTOMATION_TOOL_DEPLOYMENT_PROFILE_PAYLOAD",
    "AUTOMATION_TOOL_DEPLOYMENT_PROFILE_SIGNATURE",
    "AUTOMATION_TOOL_DEPLOYMENT_PROFILE_VERIFYING_KEY",
)


class AcceptanceFailure(RuntimeError):
    """Fixed failure that does not reflect profile material."""


def base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def run(
    arguments: Sequence[str],
    *,
    environment: Mapping[str, str],
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(arguments),
        cwd=FRONTEND_ROOT,
        env=dict(environment),
        check=False,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        raise AcceptanceFailure("C10-07 compiled profile command failed")
    return result


def test_command(*, compiled_only: bool) -> list[str]:
    command = [
        "cargo",
        "test",
        "--manifest-path",
        "src-tauri/Cargo.toml",
        "--test",
        "deployment_profiles",
        "--locked",
    ]
    if compiled_only:
        command.append("compiled_profile_matches_build_contract")
    return command


def main() -> None:
    payload = json.dumps(MANIFEST, separators=(",", ":")).encode("utf-8")
    signing_key = Ed25519PrivateKey.from_private_bytes(bytes([7]) * 32)
    signature = signing_key.sign(payload)
    verifying_key = signing_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    demo_environment = os.environ.copy()
    demo_environment.update(
        {
            "AUTOMATION_TOOL_DEPLOYMENT_PROFILE_PAYLOAD": base64url(payload),
            "AUTOMATION_TOOL_DEPLOYMENT_PROFILE_SIGNATURE": base64url(signature),
            "AUTOMATION_TOOL_DEPLOYMENT_PROFILE_VERIFYING_KEY": base64url(
                verifying_key
            ),
            "AUTOMATION_TOOL_C10_07_PROFILE_ACCEPTANCE": "1",
        }
    )
    run(test_command(compiled_only=True), environment=demo_environment)

    rejected_environment = demo_environment.copy()
    rejected_environment["AUTOMATION_TOOL_DEPLOYMENT_PROFILE_SIGNATURE"] = base64url(
        bytes([0]) * 64
    )
    rejected = run(
        [
            "cargo",
            "check",
            "--manifest-path",
            "src-tauri/Cargo.toml",
            "--lib",
            "--locked",
        ],
        environment=rejected_environment,
        check=False,
    )
    if rejected.returncode == 0:
        raise AcceptanceFailure("C10-07 tampered compiled profile was accepted")

    local_environment = os.environ.copy()
    for name in (*PROFILE_ENVIRONMENT, "AUTOMATION_TOOL_C10_07_PROFILE_ACCEPTANCE"):
        local_environment.pop(name, None)
    run(test_command(compiled_only=False), environment=local_environment)
    print(
        json.dumps(
            {
                "allowedHosts": MANIFEST["allowedHosts"],
                "baseUrl": MANIFEST["baseUrl"],
                "compiledDemoProfile": True,
                "localFallback": True,
                "profileId": MANIFEST["profileId"],
                "tamperRejected": True,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
