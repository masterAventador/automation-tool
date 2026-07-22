#!/usr/bin/env python3
"""Run the Customer Demo account/device revocation isolation matrix."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = REPOSITORY_ROOT / "contracts/security/customer-demo-revocation-v1.json"
REVOCATION_TESTS = (
    "backend/tests/integration/test_demo_account_emergency_operations.py::"
    "test_emergency_revoke_is_atomic_scoped_audited_and_single_winner",
    "backend/tests/integration/test_account_device_lifecycle.py::"
    "test_current_account_lists_and_revokes_only_one_owned_device",
    "backend/tests/integration/test_device_session_lifecycle.py::"
    "test_revoked_installation_rejects_exchange_and_existing_session",
    "backend/tests/integration/test_device_session_lifecycle.py::"
    "test_parent_credential_change_immediately_revokes_existing_sessions",
    "backend/tests/contract/test_account_device_api.py::"
    "test_account_device_routes_fail_closed_without_access_session",
    "backend/tests/contract/test_customer_demo_revocation_surface.py::"
    "test_customer_demo_has_no_anonymous_business_write_operation",
)


class AcceptanceFailure(RuntimeError):
    """Fixed revocation acceptance failure without credential reflection."""


def run(arguments: Sequence[str]) -> None:
    try:
        result = subprocess.run(
            list(arguments),
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        raise AcceptanceFailure("C10-11 revocation matrix timed out") from None
    if result.returncode != 0:
        raise AcceptanceFailure("C10-11 revocation matrix failed")


def read_contract() -> dict[str, Any]:
    try:
        contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise AcceptanceFailure("C10-11 revocation contract is invalid") from None
    if (
        not isinstance(contract, dict)
        or contract.get("version") != "customer-demo-revocation.v1"
        or contract.get("anonymousMutationAllowlist") != ["loginAccountSession"]
        or contract.get("anonymousBusinessWrites") != 0
        or contract.get("auditRequired") is not True
    ):
        raise AcceptanceFailure("C10-11 revocation contract drifted")
    return cast(dict[str, Any], contract)


def main() -> None:
    contract = read_contract()
    run(
        [
            "uv",
            "run",
            "--project",
            "backend",
            "--locked",
            "pytest",
            "-q",
            *REVOCATION_TESTS,
        ]
    )
    print(
        json.dumps(
            {
                "accountDisableImmediate": True,
                "anonymousBusinessWrites": contract["anonymousBusinessWrites"],
                "foreignSessionsPreserved": True,
                "matrixTests": len(REVOCATION_TESTS),
                "singleDeviceImmediate": True,
                "siblingDevicePreserved": True,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
