#!/usr/bin/env python3
"""Validate the C10-13 operations runbook and run its isolated drills."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    REPOSITORY_ROOT / "contracts/operations/customer-demo-runbook-v1.json"
)
PROCEDURES = (
    "preflight",
    "backup",
    "migration_and_deploy",
    "health_verification",
    "account_and_device_revocation",
    "isolated_restore",
    "application_rollback",
    "emergency_stop",
    "recovery_and_closeout",
)
DRILLS = (
    ("backup and isolated restore", "scripts/run_c10_03_acceptance.py", 1200),
    ("deployment and recovery", "scripts/run_c10_10_acceptance.py", 1800),
    ("account and device revocation", "scripts/run_c10_11_acceptance.py", 600),
)


class AcceptanceFailure(RuntimeError):
    """Fixed runbook acceptance failure without reflecting sensitive output."""


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="C10-13 operations runbook acceptance")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--full-rehearsal", action="store_true")
    mode.add_argument("--print-checklist", action="store_true")
    return parser.parse_args()


def read_contract() -> dict[str, Any]:
    try:
        contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise AcceptanceFailure("C10-13 runbook contract is invalid") from None
    invariants = contract.get("invariants") if isinstance(contract, dict) else None
    if (
        contract.get("version") != "customer-demo-operations-runbook.v1"
        or tuple(contract.get("procedures", ())) != PROCEDURES
        or contract.get("secretInputs") != ["read_only_secret_file", "stdin"]
        or not isinstance(invariants, dict)
        or invariants.get("databaseDowngrade") != "forbidden"
        or invariants.get("restoreDestination") != "new_isolated_database"
        or invariants.get("maximumControlPlaneReplicas") != 1
        or invariants.get("emergencyStopPreservesDatabase") is not True
    ):
        raise AcceptanceFailure("C10-13 runbook contract drifted")
    drills = contract.get("requiredDrills")
    if not isinstance(drills, list) or len(drills) != len(DRILLS):
        raise AcceptanceFailure("C10-13 drill evidence is incomplete")
    for drill in drills:
        if not isinstance(drill, dict):
            raise AcceptanceFailure("C10-13 drill evidence is invalid")
        path = drill.get("path")
        anchor = drill.get("anchor")
        if not isinstance(path, str) or not isinstance(anchor, str):
            raise AcceptanceFailure("C10-13 drill evidence is invalid")
        try:
            source = (REPOSITORY_ROOT / path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            raise AcceptanceFailure("C10-13 drill evidence is unavailable") from None
        if anchor not in source:
            raise AcceptanceFailure("C10-13 drill evidence drifted")
    return cast(dict[str, Any], contract)


def run(command: Sequence[str], *, label: str, timeout: int) -> None:
    try:
        completed = subprocess.run(
            list(command), cwd=REPOSITORY_ROOT, check=False, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        raise AcceptanceFailure(f"C10-13 {label} timed out") from None
    if completed.returncode != 0:
        raise AcceptanceFailure(f"C10-13 {label} failed")


def full_rehearsal(contract: dict[str, Any]) -> None:
    run(
        ["node", "--test", "frontend/tests/customer-demo-operations-runbook.test.mjs"],
        label="runbook boundary",
        timeout=60,
    )
    for label, path, timeout in DRILLS:
        run([sys.executable, path], label=label, timeout=timeout)
    print(
        json.dumps(
            {
                "databaseDowngrade": contract["invariants"]["databaseDowngrade"],
                "drills": len(DRILLS),
                "emergencyStopPreservesDatabase": True,
                "profile": "isolated_rehearsal",
                "status": "passed",
            },
            sort_keys=True,
        )
    )


def main() -> None:
    parsed = arguments()
    contract = read_contract()
    if parsed.full_rehearsal:
        full_rehearsal(contract)
        return
    print(
        json.dumps(
            {
                "namedCloudTargetRequired": contract["invariants"][
                    "namedCloudTargetRequired"
                ],
                "procedures": list(PROCEDURES),
                "secretInputs": contract["secretInputs"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
