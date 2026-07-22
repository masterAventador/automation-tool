#!/usr/bin/env python3
"""Serial, digest-bound deployment runner for one Customer Demo environment."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import re
import socket
import ssl
import stat
import subprocess
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import UUID

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = REPOSITORY_ROOT / "deploy" / "customer-demo" / "compose.v1.json"
PLAN_FILE = REPOSITORY_ROOT / "deploy" / "customer-demo" / "release-plan.v1.json"
MAXIMUM_FILE_BYTES = 16 * 1024
IMMUTABLE_IMAGE = re.compile(r"^[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}$", re.ASCII)
LOCAL_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$", re.ASCII)
SAFE_RESOURCE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$", re.ASCII)
SAFE_HOST = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$",
    re.ASCII,
)
SAFE_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$", re.ASCII)
SAFE_REVISION = re.compile(r"^[0-9a-f]{40}$", re.ASCII)
ENVIRONMENT_NAMES = frozenset(
    {
        "CONTROL_PLANE_IMAGE",
        "INGRESS_IMAGE",
        "APPLICATION_NETWORK",
        "DATABASE_NETWORK",
        "RUNTIME_SECRETS_VOLUME",
        "MIGRATION_SECRETS_VOLUME",
        "TLS_SECRETS_VOLUME",
        "DEMO_BIND_ADDRESS",
        "DEMO_HTTP_PORT",
        "DEMO_HTTPS_PORT",
        "DEMO_HOST",
        "ACCOUNT_PASSWORD_PEPPER_VERSION",
        "ACCOUNT_OPERATIONS_ACTOR_ID",
        "DEMO_ENVIRONMENT_ID",
        "DEMO_BOOTSTRAP_PUBLIC_KEY",
        "ACTION_MINIMUM_INTERVAL_SECONDS",
        "ACTION_TASK_LIMIT",
        "ACTION_DAILY_LIMIT",
        "ACTION_CONSECUTIVE_FAILURE_THRESHOLD",
    }
)


class DeploymentFailure(RuntimeError):
    """Fixed deployment failure that cannot reflect configuration or secrets."""


def run(
    arguments: Sequence[str],
    *,
    environment: Mapping[str, str],
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(arguments),
        cwd=REPOSITORY_ROOT,
        env=dict(environment),
        check=False,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        raise DeploymentFailure("Customer Demo deployment command failed")
    return result


def read_bounded_regular_file(path: Path) -> bytes:
    try:
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size <= 0
            or metadata.st_size > MAXIMUM_FILE_BYTES
        ):
            raise DeploymentFailure("Customer Demo deployment input is invalid")
        with path.open("rb") as stream:
            encoded = stream.read(MAXIMUM_FILE_BYTES + 1)
    except OSError:
        raise DeploymentFailure("Customer Demo deployment input is invalid") from None
    if len(encoded) > MAXIMUM_FILE_BYTES:
        raise DeploymentFailure("Customer Demo deployment input is invalid")
    return encoded


def read_environment(path: Path) -> dict[str, str]:
    try:
        text = read_bounded_regular_file(path).decode("utf-8")
    except UnicodeDecodeError:
        raise DeploymentFailure(
            "Customer Demo deployment environment syntax is invalid"
        ) from None
    values: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.startswith("#") or "=" not in line:
            raise DeploymentFailure(
                "Customer Demo deployment environment syntax is invalid"
            )
        name, value = line.split("=", 1)
        if (
            name not in ENVIRONMENT_NAMES
            or name in values
            or not value
            or value != value.strip()
        ):
            raise DeploymentFailure(
                "Customer Demo deployment environment value is invalid"
            )
        values[name] = value
    if set(values) != ENVIRONMENT_NAMES:
        raise DeploymentFailure("Customer Demo deployment environment is incomplete")
    return values


def require_uint(value: str, *, minimum: int, maximum: int) -> int:
    if not value.isascii() or not value.isdigit():
        raise DeploymentFailure("Customer Demo deployment environment number is invalid")
    parsed = int(value)
    if parsed < minimum or parsed > maximum:
        raise DeploymentFailure("Customer Demo deployment environment number is invalid")
    return parsed


def validate_environment(values: Mapping[str, str], *, rehearsal: bool) -> None:
    image_pattern = LOCAL_IMAGE_ID if rehearsal else IMMUTABLE_IMAGE
    if any(
        image_pattern.fullmatch(values[name]) is None
        for name in ("CONTROL_PLANE_IMAGE", "INGRESS_IMAGE")
    ):
        raise DeploymentFailure("Customer Demo image identity is invalid")
    for name in (
        "APPLICATION_NETWORK",
        "DATABASE_NETWORK",
        "RUNTIME_SECRETS_VOLUME",
        "MIGRATION_SECRETS_VOLUME",
        "TLS_SECRETS_VOLUME",
        "DEMO_ENVIRONMENT_ID",
    ):
        if SAFE_RESOURCE.fullmatch(values[name]) is None:
            raise DeploymentFailure("Customer Demo resource identity is invalid")
    if SAFE_HOST.fullmatch(values["DEMO_HOST"]) is None:
        raise DeploymentFailure("Customer Demo hostname is invalid")
    try:
        actor_id = UUID(values["ACCOUNT_OPERATIONS_ACTOR_ID"])
    except ValueError:
        raise DeploymentFailure(
            "Customer Demo non-secret configuration is invalid"
        ) from None
    if actor_id.version != 4 or str(actor_id) != values["ACCOUNT_OPERATIONS_ACTOR_ID"]:
        raise DeploymentFailure("Customer Demo non-secret configuration is invalid")
    if not re.fullmatch(
        r"[A-Za-z0-9_-]{43}", values["DEMO_BOOTSTRAP_PUBLIC_KEY"], re.ASCII
    ):
        raise DeploymentFailure("Customer Demo non-secret configuration is invalid")
    require_uint(
        values["ACCOUNT_PASSWORD_PEPPER_VERSION"], minimum=1, maximum=2**31 - 1
    )
    require_uint(values["ACTION_MINIMUM_INTERVAL_SECONDS"], minimum=1, maximum=3600)
    require_uint(values["ACTION_TASK_LIMIT"], minimum=1, maximum=1000)
    require_uint(values["ACTION_DAILY_LIMIT"], minimum=1, maximum=100_000)
    require_uint(values["ACTION_CONSECUTIVE_FAILURE_THRESHOLD"], minimum=1, maximum=100)
    http_port = require_uint(
        values["DEMO_HTTP_PORT"], minimum=0 if rehearsal else 1, maximum=65535
    )
    https_port = require_uint(
        values["DEMO_HTTPS_PORT"], minimum=0 if rehearsal else 1, maximum=65535
    )
    if http_port == https_port and http_port != 0:
        raise DeploymentFailure("Customer Demo published ports are invalid")
    allowed_bind = {"127.0.0.1"} if rehearsal else {"0.0.0.0", "127.0.0.1"}
    if values["DEMO_BIND_ADDRESS"] not in allowed_bind:
        raise DeploymentFailure("Customer Demo bind address is invalid")


def validate_backup_receipt(path: Path, artifact: Path | None) -> dict[str, str]:
    try:
        receipt = json.loads(read_bounded_regular_file(path))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise DeploymentFailure("Customer Demo backup receipt is invalid") from None
    if not isinstance(receipt, dict) or set(receipt) != {
        "version",
        "status",
        "database",
        "createdAt",
        "sha256",
    }:
        raise DeploymentFailure("Customer Demo backup receipt is invalid")
    if (
        receipt.get("version") != "customer-demo-backup-receipt.v1"
        or receipt.get("status") != "verified"
        or not isinstance(receipt.get("database"), str)
        or SAFE_RESOURCE.fullmatch(cast(str, receipt["database"])) is None
        or not isinstance(receipt.get("sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", cast(str, receipt["sha256"]), re.ASCII) is None
        or not isinstance(receipt.get("createdAt"), str)
    ):
        raise DeploymentFailure("Customer Demo backup receipt is invalid")
    try:
        created_at = datetime.fromisoformat(
            cast(str, receipt["createdAt"]).replace("Z", "+00:00")
        )
    except ValueError:
        raise DeploymentFailure("Customer Demo backup receipt is invalid") from None
    now = datetime.now(UTC)
    if (
        created_at.tzinfo is None
        or created_at > now + timedelta(minutes=5)
        or created_at < now - timedelta(hours=24)
    ):
        raise DeploymentFailure("Customer Demo backup receipt is stale")
    if artifact is not None:
        digest = hashlib.sha256(read_bounded_regular_file(artifact)).hexdigest()
        if digest != receipt["sha256"]:
            raise DeploymentFailure("Customer Demo backup artifact is invalid")
    return cast(dict[str, str], receipt)


def compose_arguments(project: str, *arguments: str) -> list[str]:
    return [
        "docker",
        "compose",
        "--file",
        str(COMPOSE_FILE),
        "--project-name",
        project,
        *arguments,
    ]


def inspect(kind: str, identity: str, environment: Mapping[str, str]) -> dict[str, Any]:
    payload = json.loads(
        run(["docker", kind, "inspect", identity], environment=environment).stdout
    )
    if (
        not isinstance(payload, list)
        or len(payload) != 1
        or not isinstance(payload[0], dict)
    ):
        raise DeploymentFailure("Customer Demo Docker inspection is invalid")
    return cast(dict[str, Any], payload[0])


def validate_image_identity(
    image: str,
    *,
    expected_version: str,
    expected_revision: str,
    control_plane: bool,
    environment: Mapping[str, str],
) -> None:
    record = inspect("image", image, environment)
    if not control_plane:
        return
    config = record.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    if (
        not isinstance(labels, dict)
        or labels.get("org.opencontainers.image.version") != expected_version
        or labels.get("org.opencontainers.image.revision") != expected_revision
    ):
        raise DeploymentFailure("Customer Demo Control Plane OCI identity is invalid")


def service_container(
    project: str,
    service: str,
    environment: Mapping[str, str],
) -> str:
    result = run(
        compose_arguments(project, "ps", "--quiet", service), environment=environment
    )
    identities = [line for line in result.stdout.splitlines() if line]
    if len(identities) != 1:
        raise DeploymentFailure("Customer Demo service replica count is invalid")
    return identities[0]


def wait_for_control_plane(
    project: str,
    environment: Mapping[str, str],
    *,
    timeout_seconds: float,
) -> tuple[str, dict[str, Any]]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        identity = service_container(project, "control-plane", environment)
        record = inspect("container", identity, environment)
        state = record.get("State")
        if not isinstance(state, dict):
            raise DeploymentFailure("Customer Demo Control Plane state is invalid")
        health = state.get("Health")
        if isinstance(health, dict) and health.get("Status") == "healthy":
            return identity, record
        if state.get("Running") is False:
            break
        time.sleep(0.5)
    raise DeploymentFailure("Customer Demo Control Plane health failed")


def validate_control_plane_container(record: Mapping[str, Any]) -> None:
    host = record.get("HostConfig")
    mounts = record.get("Mounts")
    if (
        not isinstance(host, dict)
        or host.get("ReadonlyRootfs") is not True
        or host.get("PortBindings") not in ({}, None)
        or host.get("CapDrop") != ["ALL"]
    ):
        raise DeploymentFailure("Customer Demo Control Plane isolation is invalid")
    if not isinstance(mounts, list) or not any(
        isinstance(mount, dict)
        and mount.get("Destination") == "/run/secrets"
        and mount.get("RW") is False
        for mount in mounts
    ):
        raise DeploymentFailure("Customer Demo Control Plane Secret mount is invalid")


def ingress_port(record: Mapping[str, Any], target: str) -> int:
    network = record.get("NetworkSettings")
    bindings = network.get("Ports") if isinstance(network, dict) else None
    selected = bindings.get(target) if isinstance(bindings, dict) else None
    if (
        not isinstance(selected, list)
        or len(selected) != 1
        or not isinstance(selected[0], dict)
    ):
        raise DeploymentFailure("Customer Demo ingress port binding is invalid")
    value = selected[0].get("HostPort")
    if not isinstance(value, str) or not value.isascii() or not value.isdigit():
        raise DeploymentFailure("Customer Demo ingress port binding is invalid")
    port = int(value)
    if port < 1 or port > 65535:
        raise DeploymentFailure("Customer Demo ingress port binding is invalid")
    return port


def https_json(
    *,
    address: str,
    port: int,
    host: str,
    path: str,
    ca_file: Path | None,
) -> tuple[int, dict[str, Any]]:
    context = ssl.create_default_context(cafile=os.fspath(ca_file) if ca_file else None)
    try:
        plain = socket.create_connection((address, port), timeout=10)
        tls = context.wrap_socket(plain, server_hostname=host)
        tls.sendall(
            f"GET {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\nAccept: application/json\r\n\r\n".encode(
                "ascii"
            )
        )
        response = http.client.HTTPResponse(tls)
        response.begin()
        encoded = response.read(64 * 1024 + 1)
        status = response.status
        response.close()
    except (OSError, ssl.SSLError, http.client.HTTPException):
        raise DeploymentFailure("Customer Demo HTTPS verification failed") from None
    if len(encoded) > 64 * 1024:
        raise DeploymentFailure("Customer Demo HTTPS response is invalid")
    try:
        payload = json.loads(encoded)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise DeploymentFailure("Customer Demo HTTPS response is invalid") from None
    if not isinstance(payload, dict):
        raise DeploymentFailure("Customer Demo HTTPS response is invalid")
    return status, cast(dict[str, Any], payload)


def acquire_lock(state_directory: Path) -> tuple[int, Path]:
    try:
        state_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = state_directory.lstat()
        if not stat.S_ISDIR(metadata.st_mode):
            raise DeploymentFailure("Customer Demo deployment state is invalid")
        lock_path = state_directory / "deployment.lock"
        descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.write(descriptor, b"customer-demo-deployment-v1\n")
        os.fsync(descriptor)
    except (FileExistsError, OSError):
        raise DeploymentFailure("Customer Demo deployment is already active") from None
    return descriptor, lock_path


def stop_new_services(project: str, environment: Mapping[str, str]) -> None:
    run(
        compose_arguments(project, "stop", "ingress", "control-plane"),
        environment=environment,
        check=False,
    )


def deploy(arguments: argparse.Namespace) -> dict[str, Any]:
    if SAFE_RESOURCE.fullmatch(arguments.project) is None:
        raise DeploymentFailure("Customer Demo project identity is invalid")
    if SAFE_VERSION.fullmatch(arguments.expected_app_version) is None:
        raise DeploymentFailure("Customer Demo expected version is invalid")
    if SAFE_REVISION.fullmatch(arguments.expected_vcs_ref) is None:
        raise DeploymentFailure("Customer Demo expected revision is invalid")
    values = read_environment(arguments.environment_file)
    validate_environment(values, rehearsal=arguments.rehearsal)
    validate_backup_receipt(arguments.backup_receipt, arguments.backup_artifact)
    if not PLAN_FILE.is_file() or not COMPOSE_FILE.is_file():
        raise DeploymentFailure("Customer Demo deployment contract is unavailable")
    environment = os.environ.copy()
    environment.update(values)
    descriptor, lock_path = acquire_lock(arguments.state_directory)
    started = False
    try:
        validate_image_identity(
            values["CONTROL_PLANE_IMAGE"],
            expected_version=arguments.expected_app_version,
            expected_revision=arguments.expected_vcs_ref,
            control_plane=True,
            environment=environment,
        )
        validate_image_identity(
            values["INGRESS_IMAGE"],
            expected_version=arguments.expected_app_version,
            expected_revision=arguments.expected_vcs_ref,
            control_plane=False,
            environment=environment,
        )
        rendered = run(
            compose_arguments(
                arguments.project,
                "--profile",
                "migration",
                "config",
                "--format",
                "json",
            ),
            environment=environment,
        )
        configuration = json.loads(rendered.stdout)
        services = (
            configuration.get("services") if isinstance(configuration, dict) else None
        )
        if not isinstance(services, dict) or set(services) != {
            "migration",
            "control-plane",
            "ingress",
        }:
            raise DeploymentFailure("Customer Demo rendered deployment is invalid")
        run(
            compose_arguments(
                arguments.project, "--profile", "migration", "run", "--rm", "migration"
            ),
            environment=environment,
        )
        run(
            compose_arguments(
                arguments.project, "up", "--detach", "--no-deps", "control-plane"
            ),
            environment=environment,
        )
        started = True
        _control_identity, control_record = wait_for_control_plane(
            arguments.project,
            environment,
            timeout_seconds=90,
        )
        validate_control_plane_container(control_record)
        run(
            compose_arguments(
                arguments.project, "up", "--detach", "--no-deps", "ingress"
            ),
            environment=environment,
        )
        ingress_identity = service_container(arguments.project, "ingress", environment)
        ingress_record = inspect("container", ingress_identity, environment)
        https_port = ingress_port(ingress_record, "8443/tcp")
        http_port = ingress_port(ingress_record, "8080/tcp")
        address = arguments.health_address or (
            "127.0.0.1"
            if values["DEMO_BIND_ADDRESS"] == "0.0.0.0"
            else values["DEMO_BIND_ADDRESS"]
        )
        health_status, health = https_json(
            address=address,
            port=https_port,
            host=values["DEMO_HOST"],
            path="/api/v1/health",
            ca_file=arguments.ca_file,
        )
        version_status, version = https_json(
            address=address,
            port=https_port,
            host=values["DEMO_HOST"],
            path="/api/v1/version",
            ca_file=arguments.ca_file,
        )
        if (
            health_status != 200
            or health
            != {
                "status": "ok",
                "service": "control-plane",
                "version": arguments.expected_app_version,
            }
            or version_status != 200
            or version.get("service") != "control-plane"
            or version.get("version") != arguments.expected_app_version
            or version.get("apiVersion") != "v1"
        ):
            raise DeploymentFailure(
                "Customer Demo health or version projection is invalid"
            )
        return {
            "backupReceipt": "verified",
            "healthStatus": health_status,
            "hostPorts": {"http": http_port, "https": https_port},
            "migration": "head",
            "replicas": {"controlPlane": 1, "ingress": 1},
            "versionStatus": version_status,
        }
    except Exception:
        if started:
            stop_new_services(arguments.project, environment)
        raise
    finally:
        os.close(descriptor)
        try:
            lock_path.unlink()
        except OSError:
            pass


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Deploy one Customer Demo release serially"
    )
    result.add_argument("--environment-file", type=Path, required=True)
    result.add_argument("--backup-receipt", type=Path, required=True)
    result.add_argument("--backup-artifact", type=Path)
    result.add_argument("--expected-app-version", required=True)
    result.add_argument("--expected-vcs-ref", required=True)
    result.add_argument("--project", required=True)
    result.add_argument("--state-directory", type=Path, required=True)
    result.add_argument("--ca-file", type=Path)
    result.add_argument("--health-address")
    result.add_argument("--rehearsal", action="store_true")
    return result


def main(arguments: Sequence[str] | None = None) -> None:
    try:
        result = deploy(parser().parse_args(arguments))
    except DeploymentFailure as error:
        raise SystemExit(str(error)) from None
    except Exception:
        raise SystemExit("Customer Demo deployment failed") from None
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    main()
