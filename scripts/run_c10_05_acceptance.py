#!/usr/bin/env python3
"""Verify C10-05 file-only runtime secret delivery and restart rotation."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import subprocess
import time
import tomllib
from collections.abc import Mapping, Sequence
from http import HTTPStatus
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
POSTGRES_IMAGE = "postgres:18.4-bookworm"
RUNTIME_UID = 65532
SECRET_NAMES = frozenset(
    {
        "database-url",
        "account-password-pepper",
        "account-fingerprint-key",
        "account-operations-capability-digest",
        "action-authorization-private-key",
    }
)
SECRET_ENVIRONMENT_NAMES = frozenset(
    {
        "AUTOMATION_TOOL_DATABASE_URL",
        "AUTOMATION_TOOL_ACCOUNT_PASSWORD_PEPPER",
        "AUTOMATION_TOOL_ACCOUNT_FINGERPRINT_KEY",
        "AUTOMATION_TOOL_ACCOUNT_OPERATIONS_CAPABILITY_DIGEST",
        "AUTOMATION_TOOL_ACTION_AUTHORIZATION_PRIVATE_KEY",
    }
)

VOLUME_WRITER = r"""import json
import os
import sys

allowed = {
    "database-url",
    "account-password-pepper",
    "account-fingerprint-key",
    "account-operations-capability-digest",
    "action-authorization-private-key",
    "postgres-password",
}
payload = json.load(sys.stdin)
if not isinstance(payload, dict) or not set(payload) <= allowed:
    raise SystemExit(2)
for name, specification in payload.items():
    if not isinstance(specification, dict) or set(specification) != {"value", "uid", "mode"}:
        raise SystemExit(2)
    value = specification["value"]
    uid = specification["uid"]
    mode = specification["mode"]
    if not isinstance(value, str) or not isinstance(uid, int) or not isinstance(mode, int):
        raise SystemExit(2)
    temporary = f"/target/.{name}.new"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(descriptor, value.encode("utf-8"))
        os.fchown(descriptor, uid, uid)
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, f"/target/{name}")
"""


class AcceptanceFailure(RuntimeError):
    """Fixed failure that never reflects command output or secret material."""


def run(
    arguments: Sequence[str],
    *,
    input_text: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(arguments),
        cwd=REPOSITORY_ROOT,
        input=input_text,
        check=False,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        raise AcceptanceFailure("C10-05 container command failed")
    return result


def inspect(kind: str, identity: str) -> dict[str, Any]:
    payload = json.loads(run(["docker", kind, "inspect", identity]).stdout)
    if (
        not isinstance(payload, list)
        or len(payload) != 1
        or not isinstance(payload[0], dict)
    ):
        raise AcceptanceFailure("C10-05 Docker inspection is invalid")
    return cast(dict[str, Any], payload[0])


def wait_for_healthy(container_name: str, *, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        state = inspect("container", container_name).get("State")
        if not isinstance(state, dict):
            raise AcceptanceFailure("C10-05 container state is invalid")
        health = state.get("Health")
        if isinstance(health, dict) and health.get("Status") == "healthy":
            return
        if state.get("Running") is False:
            raise AcceptanceFailure("C10-05 container exited before health")
        time.sleep(0.5)
    raise AcceptanceFailure("C10-05 container health timed out")


def wait_for_failure(container_name: str) -> None:
    for _attempt in range(40):
        state = inspect("container", container_name).get("State")
        if isinstance(state, dict) and state.get("Running") is False:
            if state.get("ExitCode") in (None, 0):
                raise AcceptanceFailure(
                    "C10-05 unsafe configuration exited successfully"
                )
            return
        time.sleep(0.25)
    raise AcceptanceFailure("C10-05 unsafe configuration did not fail closed")


def base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def secret_payload(values: Mapping[str, str], *, mode: int = 0o400) -> str:
    if not set(values) <= SECRET_NAMES | {"postgres-password"}:
        raise AcceptanceFailure("C10-05 secret name is invalid")
    return json.dumps(
        {
            name: {"value": value, "uid": RUNTIME_UID, "mode": mode}
            for name, value in values.items()
        },
        separators=(",", ":"),
    )


def write_volume(
    *,
    image: str,
    volume: str,
    values: Mapping[str, str],
    mode: int = 0o400,
) -> None:
    run(
        [
            "docker",
            "run",
            "--rm",
            "--interactive",
            "--user",
            "0:0",
            "--mount",
            f"type=volume,source={volume},target=/target",
            "--entrypoint",
            "python",
            image,
            "-c",
            VOLUME_WRITER,
        ],
        input_text=secret_payload(values, mode=mode),
    )


def start_control_plane(
    *,
    name: str,
    network: str,
    volume: str,
    image: str,
    actor_id: UUID,
    extra_environment: Sequence[str] = (),
) -> None:
    arguments = [
        "docker",
        "run",
        "--detach",
        "--name",
        name,
        "--network",
        network,
        "--network-alias",
        "control-plane",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=16m",
        "--security-opt",
        "no-new-privileges=true",
        "--cap-drop",
        "ALL",
        "--mount",
        f"type=volume,source={volume},target=/run/secrets,readonly",
        "--env",
        "AUTOMATION_TOOL_ACCOUNT_PASSWORD_PEPPER_VERSION=1",
        "--env",
        f"AUTOMATION_TOOL_ACCOUNT_OPERATIONS_ACTOR_ID={actor_id}",
        "--env",
        "AUTOMATION_TOOL_ACTION_MINIMUM_INTERVAL_SECONDS=5",
        "--env",
        "AUTOMATION_TOOL_ACTION_TASK_LIMIT=20",
        "--env",
        "AUTOMATION_TOOL_ACTION_DAILY_LIMIT=100",
        "--env",
        "AUTOMATION_TOOL_ACTION_CONSECUTIVE_FAILURE_THRESHOLD=3",
    ]
    for assignment in extra_environment:
        arguments.extend(("--env", assignment))
    arguments.append(image)
    run(arguments)


def remove_container(name: str) -> None:
    run(["docker", "container", "rm", "--force", name], check=False)


def main() -> None:
    suffix = uuid4().hex[:12]
    network = f"automation-tool-c10-05-{suffix}"
    postgres = f"automation-tool-c10-05-postgres-{suffix}"
    control_plane = f"automation-tool-c10-05-control-plane-{suffix}"
    rotated_probe = f"automation-tool-c10-05-rotated-{suffix}"
    environment_probe = f"automation-tool-c10-05-environment-{suffix}"
    permission_probe = f"automation-tool-c10-05-permission-{suffix}"
    control_plane_volume = f"automation-tool-c10-05-control-plane-{suffix}"
    postgres_volume = f"automation-tool-c10-05-postgres-{suffix}"
    unsafe_volume = f"automation-tool-c10-05-unsafe-{suffix}"
    image = f"automation-tool-control-plane:c10-05-{suffix}"
    database_password = secrets.token_urlsafe(32)
    database_url = (
        f"postgresql+asyncpg://c10_app:{database_password}@postgres:5432/c10_demo"
    )
    operator_capability = f"atoc1.{base64url(secrets.token_bytes(32))}"
    secret_values = {
        "database-url": database_url,
        "account-password-pepper": base64url(secrets.token_bytes(32)),
        "account-fingerprint-key": base64url(secrets.token_bytes(32)),
        "account-operations-capability-digest": base64url(
            hashlib.sha256(operator_capability.encode("ascii")).digest()
        ),
        "action-authorization-private-key": base64url(secrets.token_bytes(32)),
    }
    actor_id = uuid4()
    project = tomllib.loads(
        (BACKEND_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    app_version = cast(str, project["project"]["version"])
    revision = run(["git", "rev-parse", "HEAD"]).stdout.strip()

    try:
        run(
            [
                "docker",
                "build",
                "--file",
                str(BACKEND_ROOT / "Dockerfile"),
                "--tag",
                image,
                "--build-arg",
                f"APP_VERSION={app_version}",
                "--build-arg",
                f"VCS_REF={revision}",
                str(BACKEND_ROOT),
            ]
        )
        image_config = inspect("image", image).get("Config")
        if not isinstance(image_config, dict):
            raise AcceptanceFailure("C10-05 image configuration is invalid")
        image_environment = image_config.get("Env")
        if not isinstance(image_environment, list) or not any(
            value == "AUTOMATION_TOOL_RUNTIME_SECRET_MODE=files"
            for value in image_environment
        ):
            raise AcceptanceFailure("C10-05 image is not file-only")
        if any(secret in json.dumps(image_config) for secret in secret_values.values()):
            raise AcceptanceFailure("C10-05 image contains a generated secret")

        run(["docker", "network", "create", network])
        for volume in (control_plane_volume, postgres_volume, unsafe_volume):
            run(["docker", "volume", "create", volume])
        write_volume(image=image, volume=control_plane_volume, values=secret_values)
        write_volume(
            image=image,
            volume=postgres_volume,
            values={"postgres-password": database_password},
        )
        write_volume(
            image=image, volume=unsafe_volume, values=secret_values, mode=0o444
        )

        run(
            [
                "docker",
                "run",
                "--detach",
                "--name",
                postgres,
                "--network",
                network,
                "--network-alias",
                "postgres",
                "--mount",
                f"type=volume,source={postgres_volume},target=/run/secrets,readonly",
                "--env",
                "POSTGRES_USER=c10_app",
                "--env",
                "POSTGRES_DB=c10_demo",
                "--env",
                "POSTGRES_PASSWORD_FILE=/run/secrets/postgres-password",
                "--tmpfs",
                "/var/lib/postgresql:rw,noexec,nosuid,size=512m",
                "--health-cmd",
                "pg_isready --username c10_app --dbname c10_demo",
                "--health-interval",
                "1s",
                "--health-timeout",
                "3s",
                "--health-retries",
                "30",
                POSTGRES_IMAGE,
            ]
        )
        wait_for_healthy(postgres, timeout_seconds=45)
        run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                network,
                "--mount",
                (
                    f"type=volume,source={control_plane_volume},"
                    "target=/run/secrets,readonly"
                ),
                "--entrypoint",
                "alembic",
                image,
                "-c",
                "/app/alembic.ini",
                "upgrade",
                "head",
            ]
        )
        start_control_plane(
            name=control_plane,
            network=network,
            volume=control_plane_volume,
            image=image,
            actor_id=actor_id,
        )
        wait_for_healthy(control_plane, timeout_seconds=60)

        container = inspect("container", control_plane)
        host_config = container.get("HostConfig")
        config = container.get("Config")
        mounts = container.get("Mounts")
        if (
            not isinstance(host_config, dict)
            or host_config.get("ReadonlyRootfs") is not True
        ):
            raise AcceptanceFailure("C10-05 Control Plane root filesystem is writable")
        if host_config.get("PortBindings") not in ({}, None):
            raise AcceptanceFailure("C10-05 Control Plane published a host port")
        if not isinstance(config, dict) or not isinstance(config.get("Env"), list):
            raise AcceptanceFailure("C10-05 Control Plane environment is invalid")
        process_environment = cast(list[str], config["Env"])
        if any(
            entry.partition("=")[0] in SECRET_ENVIRONMENT_NAMES
            for entry in process_environment
        ):
            raise AcceptanceFailure(
                "C10-05 Control Plane received a secret environment"
            )
        if not isinstance(mounts, list) or not any(
            isinstance(mount, dict)
            and mount.get("Destination") == "/run/secrets"
            and mount.get("RW") is False
            for mount in mounts
        ):
            raise AcceptanceFailure("C10-05 Secret volume is not read-only")

        health = run(
            [
                "docker",
                "exec",
                control_plane,
                "python",
                "-c",
                (
                    "import json,urllib.request;"
                    "response=urllib.request.urlopen("
                    "'http://127.0.0.1:8000/api/v1/health',timeout=3);"
                    "print(json.dumps({'status':response.status,'body':json.load(response)}))"
                ),
            ]
        )
        health_projection = json.loads(health.stdout)
        if health_projection != {
            "status": HTTPStatus.OK,
            "body": {
                "status": "ok",
                "service": "control-plane",
                "version": app_version,
            },
        }:
            raise AcceptanceFailure("C10-05 Control Plane health is invalid")
        raw_environ = run(
            [
                "docker",
                "exec",
                control_plane,
                "python",
                "-c",
                "import pathlib;print(pathlib.Path('/proc/1/environ').read_bytes().hex())",
            ]
        ).stdout.strip()
        environ_bytes = bytes.fromhex(raw_environ)
        if any(value.encode() in environ_bytes for value in secret_values.values()):
            raise AcceptanceFailure("C10-05 process environment contains a secret")
        if any(
            name.encode() + b"=" in environ_bytes for name in SECRET_ENVIRONMENT_NAMES
        ):
            raise AcceptanceFailure("C10-05 process environment has a secret key")

        start_control_plane(
            name=environment_probe,
            network=network,
            volume=control_plane_volume,
            image=image,
            actor_id=actor_id,
            extra_environment=(
                "AUTOMATION_TOOL_DATABASE_URL=forbidden-environment-secret",
            ),
        )
        wait_for_failure(environment_probe)
        environment_logs = run(["docker", "logs", environment_probe]).stdout
        if "forbidden-environment-secret" in environment_logs:
            raise AcceptanceFailure("C10-05 rejected environment secret was logged")

        start_control_plane(
            name=permission_probe,
            network=network,
            volume=unsafe_volume,
            image=image,
            actor_id=actor_id,
        )
        wait_for_failure(permission_probe)

        write_volume(
            image=image,
            volume=control_plane_volume,
            values={"database-url": "invalid-rotated-secret"},
        )
        still_healthy = run(
            [
                "docker",
                "exec",
                control_plane,
                "python",
                "-c",
                (
                    "import urllib.request;"
                    "response=urllib.request.urlopen("
                    "'http://127.0.0.1:8000/api/v1/health',timeout=3);"
                    "assert response.status==200"
                ),
            ]
        )
        if still_healthy.returncode != 0:
            raise AcceptanceFailure("C10-05 live process reloaded a rotated secret")
        remove_container(control_plane)
        start_control_plane(
            name=rotated_probe,
            network=network,
            volume=control_plane_volume,
            image=image,
            actor_id=actor_id,
        )
        wait_for_failure(rotated_probe)
        write_volume(
            image=image,
            volume=control_plane_volume,
            values={"database-url": database_url},
        )
        start_control_plane(
            name=control_plane,
            network=network,
            volume=control_plane_volume,
            image=image,
            actor_id=actor_id,
        )
        wait_for_healthy(control_plane, timeout_seconds=60)

        all_logs = "".join(
            run(["docker", "logs", name]).stdout
            for name in (control_plane, rotated_probe, permission_probe)
        )
        forbidden_values = (
            *secret_values.values(),
            database_password,
            operator_capability,
        )
        if any(value in all_logs for value in forbidden_values):
            raise AcceptanceFailure("C10-05 container log contains a secret")
        projection = {
            "file_only": True,
            "host_ports": 0,
            "permission_rejected": True,
            "secret_environment_rejected": True,
            "process_environment_secret_count": 0,
            "rotation_requires_restart": True,
            "restart_recovered": True,
        }
        print(json.dumps(projection, sort_keys=True))
    finally:
        for container_name in (
            control_plane,
            rotated_probe,
            environment_probe,
            permission_probe,
            postgres,
        ):
            remove_container(container_name)
        run(["docker", "network", "rm", network], check=False)
        for volume_name in (control_plane_volume, postgres_volume, unsafe_volume):
            run(["docker", "volume", "rm", "--force", volume_name], check=False)
        run(["docker", "image", "rm", "--force", image], check=False)


if __name__ == "__main__":
    main()
