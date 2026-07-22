#!/usr/bin/env python3
"""Exercise Customer Demo restart/network recovery and protocol continuation."""

from __future__ import annotations

import ipaddress
import json
import os
import subprocess
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import run_c10_08_acceptance as deployment
from deploy_customer_demo import DeploymentFailure, https_json

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = REPOSITORY_ROOT / "deploy/customer-demo/recovery-plan.v1.json"
EXPECTED_PLAN: dict[str, Any] = {
    "version": "customer-demo-recovery-plan.v1",
    "controlPlaneRestarts": 1,
    "applicationNetworkFlaps": 2,
    "maximumControlPlaneReplicas": 1,
    "maximumIngressReplicas": 1,
    "automaticScaling": False,
    "databaseRestart": False,
    "databaseDowngrade": False,
    "eventResumeHeader": "Last-Event-ID",
    "protocolRecovery": [
        "executor_reconnect",
        "durable_outbox_replay",
        "last_event_id_continuation",
    ],
}
PROTOCOL_RECOVERY_TESTS = (
    "backend/tests/unit/executor/test_process_client.py::"
    "test_control_plane_restart_reconnects_and_replays_exact_durable_outbox",
    "backend/tests/unit/executor/test_process_client.py::"
    "test_abnormal_network_disconnect_reconnects_and_replays_exact_durable_outbox",
    "backend/tests/unit/executor/test_process_client.py::"
    "test_gap_between_heartbeat_iterations_uses_the_same_safe_reconnect",
    "backend/tests/contract/test_task_event_stream_api.py::"
    "test_failure_after_stream_start_closes_for_safe_last_event_reconnect",
    "backend/tests/integration/test_task_event_stream_lifecycle.py::"
    "test_repository_reads_bounded_ordered_public_events_and_terminal_catchup",
)


class AcceptanceFailure(RuntimeError):
    """Fixed C10-10 failure without reflecting private deployment data."""


def run(
    arguments: Sequence[str],
    *,
    environment: Mapping[str, str] | None = None,
    timeout_seconds: float = 300,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            list(arguments),
            cwd=REPOSITORY_ROOT,
            env=None if environment is None else dict(environment),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        raise AcceptanceFailure("C10-10 recovery command timed out") from None
    if result.returncode != 0:
        raise AcceptanceFailure("C10-10 recovery command failed")
    return result


def read_plan() -> dict[str, Any]:
    try:
        plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise AcceptanceFailure("C10-10 recovery plan is invalid") from None
    if plan != EXPECTED_PLAN:
        raise AcceptanceFailure("C10-10 recovery plan drifted")
    return cast(dict[str, Any], plan)


def health(context: deployment.DeploymentRecoveryContext) -> bool:
    try:
        status, payload = https_json(
            address=context.health_address,
            port=context.https_port,
            host=context.demo_host,
            path="/api/v1/health",
            ca_file=context.ca_file,
        )
    except DeploymentFailure:
        return False
    return status == 200 and payload.get("status") == "ok"


def wait_for_health(
    context: deployment.DeploymentRecoveryContext, *, timeout_seconds: float
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if health(context):
            return
        time.sleep(0.5)
    raise AcceptanceFailure("C10-10 HTTPS recovery timed out")


def service_identity(
    context: deployment.DeploymentRecoveryContext, service: str
) -> str:
    return run(
        [
            "docker",
            "compose",
            "--file",
            os.fspath(deployment.COMPOSE_FILE),
            "--project-name",
            context.project,
            "ps",
            "--quiet",
            service,
        ],
        environment=context.compose_environment,
    ).stdout.strip()


def state_started_at(container: str) -> str:
    state = deployment.inspect("container", container).get("State")
    if not isinstance(state, dict) or not isinstance(state.get("StartedAt"), str):
        raise AcceptanceFailure("C10-10 container state is invalid")
    return cast(str, state["StartedAt"])


def network_ip(container: str, network: str) -> str:
    settings = deployment.inspect("container", container).get("NetworkSettings")
    networks = settings.get("Networks") if isinstance(settings, dict) else None
    attached = networks.get(network) if isinstance(networks, dict) else None
    value = attached.get("IPAddress") if isinstance(attached, dict) else None
    if not isinstance(value, str):
        raise AcceptanceFailure("C10-10 network attachment is invalid")
    try:
        parsed = ipaddress.ip_address(value)
    except ValueError:
        raise AcceptanceFailure("C10-10 network attachment is invalid") from None
    if parsed.version != 4 or parsed.is_loopback or str(parsed) != value:
        raise AcceptanceFailure("C10-10 network attachment is invalid")
    return value


def recovery_probe(
    context: deployment.DeploymentRecoveryContext,
) -> Mapping[str, object]:
    if not health(context):
        raise AcceptanceFailure("C10-10 initial HTTPS health is unavailable")
    original_control_start = state_started_at(context.control_container)
    original_ingress_start = state_started_at(context.ingress_container)
    original_control_ip = network_ip(
        context.control_container, context.application_network
    )

    run(["docker", "restart", context.control_container])
    deployment.wait_for_healthy(context.control_container, timeout_seconds=90)
    wait_for_health(context, timeout_seconds=45)
    if (
        service_identity(context, "control-plane") != context.control_container
        or state_started_at(context.control_container) == original_control_start
    ):
        raise AcceptanceFailure("C10-10 Control Plane restart identity is invalid")

    completed_flaps = 0
    for _ in range(2):
        disconnected = False
        try:
            run(
                [
                    "docker",
                    "network",
                    "disconnect",
                    context.application_network,
                    context.control_container,
                ]
            )
            disconnected = True
            if health(context):
                raise AcceptanceFailure("C10-10 network outage was not observed")
        finally:
            if disconnected:
                run(
                    [
                        "docker",
                        "network",
                        "connect",
                        "--ip",
                        original_control_ip,
                        "--alias",
                        "control-plane",
                        context.application_network,
                        context.control_container,
                    ]
                )
        wait_for_health(context, timeout_seconds=45)
        if (
            network_ip(context.control_container, context.application_network)
            != original_control_ip
        ):
            raise AcceptanceFailure("C10-10 network identity changed after recovery")
        completed_flaps += 1

    if (
        service_identity(context, "control-plane") != context.control_container
        or service_identity(context, "ingress") != context.ingress_container
        or state_started_at(context.ingress_container) != original_ingress_start
    ):
        raise AcceptanceFailure("C10-10 recovery created a replacement replica")
    return {
        "applicationNetworkFlaps": completed_flaps,
        "controlPlaneContainerPreserved": True,
        "controlPlaneRestarts": 1,
        "httpsRecovered": True,
        "ingressContainerPreserved": True,
        "replicas": {"controlPlane": 1, "ingress": 1},
    }


def main() -> None:
    plan = read_plan()
    deployment.main(recovery_probe=recovery_probe)
    run(
        [
            "uv",
            "run",
            "--project",
            "backend",
            "--locked",
            "pytest",
            "-q",
            *PROTOCOL_RECOVERY_TESTS,
        ]
    )
    print(
        json.dumps(
            {
                "eventResumeHeader": plan["eventResumeHeader"],
                "protocolRecoveryTests": len(PROTOCOL_RECOVERY_TESTS),
                "recoveryPlan": plan["version"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
