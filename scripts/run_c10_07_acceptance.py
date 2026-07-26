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


ISOLATED_ACCOUNT_INSTANCE_ENVIRONMENT = "AUTOMATION_TOOL_ISOLATED_PRODUCT_ACCOUNT_INSTANCE"
ACCOUNT_REQUIREMENT_TEST = (
    "the_deployment_configuration_decides_whether_a_product_account_is_required"
)


def test_command(*, compiled_only: bool, only: str | None = None) -> list[str]:
    command = [
        "cargo",
        "test",
        "--manifest-path",
        "src-tauri/Cargo.toml",
        "--test",
        "deployment_profiles",
        "--locked",
    ]
    if only is not None:
        command.append(only)
    elif compiled_only:
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
    for name in (
        *PROFILE_ENVIRONMENT,
        "AUTOMATION_TOOL_C10_07_PROFILE_ACCEPTANCE",
        ISOLATED_ACCOUNT_INSTANCE_ENVIRONMENT,
    ):
        local_environment.pop(name, None)
    run(test_command(compiled_only=False), environment=local_environment)

    # The login screen ships in every build; whether it stands in the way is a
    # deployment configuration value. Compiling the same assertion both ways is
    # what makes that switch provable: the U9-04 and U9-06 acceptance Apps run
    # on loopback with no signed demo profile, so if the switch were read but
    # ignored they would silently reach the workbench without ever logging in,
    # and their whole subject would go untested.
    isolated_environment = local_environment.copy()
    isolated_environment[ISOLATED_ACCOUNT_INSTANCE_ENVIRONMENT] = "1"
    run(
        test_command(compiled_only=False, only=ACCOUNT_REQUIREMENT_TEST),
        environment=isolated_environment,
    )
    print(
        json.dumps(
            {
                "allowedHosts": MANIFEST["allowedHosts"],
                "baseUrl": MANIFEST["baseUrl"],
                "compiledDemoProfile": True,
                "isolatedInstanceRequiresAccount": True,
                "localFallback": True,
                "localRequiresAccount": False,
                "profileId": MANIFEST["profileId"],
                "tamperRejected": True,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
