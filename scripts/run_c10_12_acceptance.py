#!/usr/bin/env python3
"""Run the C10-12 Customer Demo journey or emit its device-package handoff."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = REPOSITORY_ROOT / "contracts/quality/customer-demo-acceptance-v1.json"
JOURNEY = (
    "install",
    "account_login",
    "automatic_device_binding",
    "workbench",
    "platform_scan",
    "target_preview",
    "controlled_action",
    "structured_results",
    "manual_handoff",
)
FOCUSED_TESTS = (
    "backend/tests/integration/test_douyin_qr_login_browser.py::"
    "test_real_system_chrome_uses_one_dedicated_window_for_the_complete_qr_flow",
    "backend/tests/integration/test_task_discovery_lifecycle.py::"
    "test_discovery_repository_convergence_input_and_terminal_outcome_matrix",
    "backend/tests/integration/test_task_target_result_lifecycle.py::"
    "test_target_results_project_success_skip_failure_and_uncertain_from_postgresql",
)


class AcceptanceFailure(RuntimeError):
    """Fixed C10-12 failure that does not reflect subprocess output or credentials."""


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="C10-12 Customer Demo acceptance")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--full-isolated-app", action="store_true")
    mode.add_argument("--device-package-handoff", action="store_true")
    return parser.parse_args()


def read_contract() -> dict[str, Any]:
    try:
        contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise AcceptanceFailure("C10-12 acceptance contract is invalid") from None
    if (
        not isinstance(contract, dict)
        or contract.get("version") != "customer-demo-acceptance.v1"
        or contract.get("profiles") != ["isolated_full", "device_package"]
        or contract.get("safety", {}).get("externalPlatformWrites") != 0
        or contract.get("safety", {}).get("automaticChallengeBypass") is not False
    ):
        raise AcceptanceFailure("C10-12 acceptance contract drifted")
    journey = contract.get("journey")
    if not isinstance(journey, list) or tuple(
        step.get("id") for step in journey if isinstance(step, dict)
    ) != JOURNEY:
        raise AcceptanceFailure("C10-12 journey order drifted")
    for step in journey:
        evidence = step.get("evidence") if isinstance(step, dict) else None
        if not isinstance(evidence, list) or not evidence:
            raise AcceptanceFailure("C10-12 journey evidence is incomplete")
        for item in evidence:
            if not isinstance(item, dict):
                raise AcceptanceFailure("C10-12 journey evidence is invalid")
            path = item.get("path")
            anchor = item.get("anchor")
            if not isinstance(path, str) or not isinstance(anchor, str):
                raise AcceptanceFailure("C10-12 journey evidence is invalid")
            try:
                source = (REPOSITORY_ROOT / path).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                raise AcceptanceFailure("C10-12 journey evidence is unavailable") from None
            if anchor not in source:
                raise AcceptanceFailure("C10-12 journey evidence drifted")
    return cast(dict[str, Any], contract)


def run(command: Sequence[str], *, label: str, timeout: int) -> None:
    try:
        completed = subprocess.run(
            list(command),
            cwd=REPOSITORY_ROOT,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise AcceptanceFailure(f"C10-12 {label} timed out") from None
    if completed.returncode != 0:
        raise AcceptanceFailure(f"C10-12 {label} failed")


def full_isolated_acceptance(contract: dict[str, Any]) -> None:
    run(
        ["node", "--test", "frontend/tests/customer-demo-acceptance.test.mjs"],
        label="contract boundary",
        timeout=60,
    )
    run(
        [
            "uv",
            "run",
            "--project",
            "backend",
            "--locked",
            "pytest",
            "-q",
            *FOCUSED_TESTS,
        ],
        label="scan preview result and handoff matrix",
        timeout=600,
    )
    run(
        [sys.executable, "scripts/run_u9_06_acceptance.py"],
        label="account login and automatic device binding App",
        timeout=1800,
    )
    run(
        [sys.executable, "scripts/run_h8_16f_acceptance.py"],
        label="workbench preview action and result App",
        timeout=1800,
    )
    print(
        json.dumps(
            {
                "externalPlatformWrites": contract["safety"]["externalPlatformWrites"],
                "journey": list(JOURNEY),
                "profile": "isolated_full",
                "realCustomerCredentials": False,
                "status": "passed",
            },
            sort_keys=True,
        )
    )


def device_package_handoff(contract: dict[str, Any]) -> None:
    handoff = contract["devicePackageHandoff"]
    print(
        json.dumps(
            {
                "macOS": (
                    "backend/.venv/bin/python scripts/run_p9_06_acceptance.py "
                    "--interactive-device-acceptance --dmg <absolute.dmg> "
                    "--evidence <new-absolute.json>"
                ),
                "status": "interactive_device_acceptance_required",
                "windows": (
                    "backend/.venv/bin/python scripts/run_p9_07_acceptance.py "
                    "--interactive-device-acceptance --installer <absolute.exe> "
                    "--evidence <new-absolute.json>"
                ),
                "authorizedTestAccountRequired": handoff[
                    "requiresAuthorizedTestAccount"
                ],
            },
            sort_keys=True,
        )
    )


def main() -> None:
    parsed = arguments()
    contract = read_contract()
    if parsed.full_isolated_app:
        full_isolated_acceptance(contract)
    else:
        device_package_handoff(contract)


if __name__ == "__main__":
    main()
