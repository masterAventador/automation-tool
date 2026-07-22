#!/usr/bin/env python3
"""Replay one immutable App protocol corpus under local and signed Demo profiles."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_ROOT = REPOSITORY_ROOT / "frontend"
CONTRACT_PATH = (
    REPOSITORY_ROOT
    / "contracts/deployment/customer-demo-protocol-regression.v1.json"
)
PROFILE_ENVIRONMENT = frozenset(
    {
        "AUTOMATION_TOOL_DEPLOYMENT_PROFILE_PAYLOAD",
        "AUTOMATION_TOOL_DEPLOYMENT_PROFILE_SIGNATURE",
        "AUTOMATION_TOOL_DEPLOYMENT_PROFILE_VERIFYING_KEY",
    }
)
EXPECTED_CONTRACT: dict[str, Any] = {
    "version": "customer-demo-protocol-regression.v1",
    "profiles": ["local", "demo"],
    "openapi": "contracts/openapi/control-plane.v1.json",
    "generatedDto": "frontend/src/api/generated/control-plane.ts",
    "nativeTransport": "frontend/src-tauri/src/control_plane.rs",
    "controlPlaneFixtures": [
        "contracts/fixtures/control-plane-v1/health.json",
        "contracts/fixtures/control-plane-v1/version.json",
    ],
    "executorSchema": "contracts/protocol/executor-v1.schema.json",
    "executorFixtures": "contracts/fixtures/executor-v1",
    "allowedBuildDifferences": ["compiledDeploymentProfile"],
    "businessCodeDifferences": [],
    "requiredReplays": [
        "backend_openapi_and_control_plane_fixtures",
        "generated_typescript_drift_check",
        "python_executor_fixtures",
        "typescript_executor_fixtures",
        "rust_executor_fixtures",
        "rust_control_plane_allowlist_and_parsers",
    ],
}
SHARED_COMMANDS = (
    (
        REPOSITORY_ROOT,
        (
            "backend/.venv/bin/pytest",
            "backend/tests/contract/test_openapi_snapshot.py",
            "backend/tests/contract/test_executor_protocol_schema.py",
            "-q",
        ),
    ),
    (FRONTEND_ROOT, ("pnpm", "check:api")),
    (
        FRONTEND_ROOT,
        (
            "pnpm",
            "exec",
            "vitest",
            "run",
            "src/api/protocol/executor-envelope.test.ts",
        ),
    ),
)
APP_PROTOCOL_COMMANDS = (
    (
        "cargo",
        "test",
        "--manifest-path",
        "src-tauri/Cargo.toml",
        "--lib",
        "control_plane::tests",
        "--locked",
    ),
    (
        "cargo",
        "test",
        "--manifest-path",
        "src-tauri/Cargo.toml",
        "--test",
        "executor_protocol_fixtures",
        "--locked",
    ),
)


class AcceptanceFailure(RuntimeError):
    """Fixed failure that does not reflect environment or command output."""


def base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def run(
    arguments: Sequence[str],
    *,
    working_directory: Path,
    environment: Mapping[str, str],
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(arguments),
        cwd=working_directory,
        env=dict(environment),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AcceptanceFailure("C10-09 protocol replay command failed")
    return result


def read_contract() -> dict[str, Any]:
    try:
        metadata = CONTRACT_PATH.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 16 * 1024:
            raise AcceptanceFailure("C10-09 protocol contract is invalid")
        contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise AcceptanceFailure("C10-09 protocol contract is invalid") from None
    if contract != EXPECTED_CONTRACT:
        raise AcceptanceFailure("C10-09 protocol contract drifted")
    return cast(dict[str, Any], contract)


def corpus_files(contract: Mapping[str, Any]) -> list[Path]:
    fixed = [
        CONTRACT_PATH,
        REPOSITORY_ROOT / cast(str, contract["openapi"]),
        REPOSITORY_ROOT / cast(str, contract["generatedDto"]),
        REPOSITORY_ROOT / cast(str, contract["nativeTransport"]),
        REPOSITORY_ROOT / cast(str, contract["executorSchema"]),
    ]
    fixed.extend(
        REPOSITORY_ROOT / path
        for path in cast(list[str], contract["controlPlaneFixtures"])
    )
    executor_root = REPOSITORY_ROOT / cast(str, contract["executorFixtures"])
    try:
        fixture_files = sorted(
            path
            for path in executor_root.rglob("*")
            if path.is_file() and not path.is_symlink()
        )
    except OSError:
        raise AcceptanceFailure("C10-09 protocol corpus is unavailable") from None
    files = [*fixed, *fixture_files]
    if not fixture_files or any(path.is_symlink() or not path.is_file() for path in files):
        raise AcceptanceFailure("C10-09 protocol corpus is unavailable")
    return files


def source_digest(files: Sequence[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(files):
        relative = path.relative_to(REPOSITORY_ROOT).as_posix().encode("utf-8")
        encoded = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def environments() -> tuple[dict[str, str], dict[str, str]]:
    local = os.environ.copy()
    for name in PROFILE_ENVIRONMENT:
        local.pop(name, None)
    manifest = json.dumps(
        {
            "version": "customer-demo-profile.v1",
            "profile": "demo",
            "profileId": "demo-protocol-regression",
            "baseUrl": "https://api.automation-tool.test",
            "allowedHosts": ["api.automation-tool.test"],
        },
        separators=(",", ":"),
    ).encode("utf-8")
    signing_key = Ed25519PrivateKey.from_private_bytes(bytes([9]) * 32)
    demo = local.copy()
    demo.update(
        {
            "AUTOMATION_TOOL_DEPLOYMENT_PROFILE_PAYLOAD": base64url(manifest),
            "AUTOMATION_TOOL_DEPLOYMENT_PROFILE_SIGNATURE": base64url(
                signing_key.sign(manifest)
            ),
            "AUTOMATION_TOOL_DEPLOYMENT_PROFILE_VERIFYING_KEY": base64url(
                signing_key.public_key().public_bytes(
                    encoding=serialization.Encoding.Raw,
                    format=serialization.PublicFormat.Raw,
                )
            ),
        }
    )
    differences = {
        name for name in set(local) | set(demo) if local.get(name) != demo.get(name)
    }
    if differences != PROFILE_ENVIRONMENT:
        raise AcceptanceFailure("C10-09 profile environment delta is invalid")
    return local, demo


def replay_count(result: subprocess.CompletedProcess[str]) -> int:
    summaries = re.findall(r"(\d+) passed", f"{result.stdout}\n{result.stderr}")
    return sum(int(value) for value in summaries)


def main() -> None:
    contract = read_contract()
    files = corpus_files(contract)
    before = source_digest(files)
    local, demo = environments()

    shared_replays = 0
    for working_directory, shared_command in SHARED_COMMANDS:
        shared_replays += replay_count(
            run(shared_command, working_directory=working_directory, environment=local)
        )

    profile_replays: dict[str, int] = {}
    for profile, environment in (("local", local), ("demo", demo)):
        passed = 0
        for app_command in APP_PROTOCOL_COMMANDS:
            passed += replay_count(
                run(app_command, working_directory=FRONTEND_ROOT, environment=environment)
            )
        if passed == 0:
            raise AcceptanceFailure("C10-09 App protocol replay was empty")
        profile_replays[profile] = passed

    after = source_digest(files)
    if after != before or profile_replays["local"] != profile_replays["demo"]:
        raise AcceptanceFailure("C10-09 profile protocol corpus diverged")
    print(
        json.dumps(
            {
                "businessCodeDifferences": [],
                "profileReplays": profile_replays,
                "sharedReplays": shared_replays,
                "sourceDigest": after,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
