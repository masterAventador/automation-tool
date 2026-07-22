#!/usr/bin/env python3
"""Rehearse C10-08 serial deployment in isolated Docker resources."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
COMPOSE_FILE = REPOSITORY_ROOT / "deploy" / "customer-demo" / "compose.v1.json"
DEPLOYMENT_RUNNER = REPOSITORY_ROOT / "scripts" / "deploy_customer_demo.py"
POSTGRES_IMAGE = "postgres:18.4-bookworm"
DEMO_HOST = "api.automation-tool.test"

VOLUME_WRITER = r"""import json
import os
import sys

payload = json.load(sys.stdin)
if not isinstance(payload, dict) or not payload:
    raise SystemExit(2)
for name, specification in payload.items():
    if (
        not isinstance(name, str)
        or not isinstance(specification, dict)
        or set(specification) != {"value", "uid", "mode"}
    ):
        raise SystemExit(2)
    value = specification["value"]
    uid = specification["uid"]
    mode = specification["mode"]
    if not isinstance(value, str) or not value or not isinstance(uid, int) or not isinstance(mode, int):
        raise SystemExit(2)
    descriptor = os.open(f"/target/{name}", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, value.encode("utf-8"))
        os.fchown(descriptor, uid, uid)
        os.fchmod(descriptor, mode)
    finally:
        os.close(descriptor)
"""


class AcceptanceFailure(RuntimeError):
    """Fixed failure without reflecting a generated credential or path."""


@dataclass(frozen=True)
class DeploymentRecoveryContext:
    """Non-secret handles for an in-lifecycle deployment recovery probe."""

    project: str
    compose_environment: Mapping[str, str]
    application_network: str
    control_container: str
    ingress_container: str
    health_address: str
    https_port: int
    demo_host: str
    ca_file: Path


RecoveryProbe = Callable[[DeploymentRecoveryContext], Mapping[str, object]]


def run(
    arguments: Sequence[str],
    *,
    input_text: str | None = None,
    environment: Mapping[str, str] | None = None,
    check: bool = True,
    timeout_seconds: float = 300,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            list(arguments),
            cwd=REPOSITORY_ROOT,
            env=None if environment is None else dict(environment),
            input=input_text,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        raise AcceptanceFailure("C10-08 command timed out") from None
    if check and result.returncode != 0:
        raise AcceptanceFailure("C10-08 command failed")
    return result


def run_binary(arguments: Sequence[str]) -> bytes:
    result = subprocess.run(
        list(arguments),
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        raise AcceptanceFailure("C10-08 binary command failed")
    return result.stdout


def inspect(kind: str, identity: str) -> dict[str, Any]:
    payload = json.loads(run(["docker", kind, "inspect", identity]).stdout)
    if (
        not isinstance(payload, list)
        or len(payload) != 1
        or not isinstance(payload[0], dict)
    ):
        raise AcceptanceFailure("C10-08 Docker inspection is invalid")
    return cast(dict[str, Any], payload[0])


def wait_for_healthy(container: str, *, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        state = inspect("container", container).get("State")
        if not isinstance(state, dict):
            raise AcceptanceFailure("C10-08 container state is invalid")
        health = state.get("Health")
        if isinstance(health, dict) and health.get("Status") == "healthy":
            return
        if state.get("Running") is False:
            raise AcceptanceFailure("C10-08 container exited before health")
        time.sleep(0.5)
    raise AcceptanceFailure("C10-08 container health timed out")


def base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def write_volume(
    *,
    image: str,
    volume: str,
    values: Mapping[str, tuple[str, int, int]],
) -> None:
    payload = {
        name: {"value": value, "uid": uid, "mode": mode}
        for name, (value, uid, mode) in values.items()
    }
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
        input_text=json.dumps(payload, separators=(",", ":")),
        timeout_seconds=60,
    )


def generate_certificate() -> tuple[str, str, str]:
    now = datetime.now(UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "C10-08 rehearsal CA")]
    )
    ca_certificate = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    server_key = ec.generate_private_key(ec.SECP256R1())
    server_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, DEMO_HOST)])
    server_certificate = (
        x509.CertificateBuilder()
        .subject_name(server_name)
        .issuer_name(ca_name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(minutes=30))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(DEMO_HOST)]), critical=False
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    ca_pem = ca_certificate.public_bytes(serialization.Encoding.PEM).decode("ascii")
    certificate_pem = server_certificate.public_bytes(
        serialization.Encoding.PEM
    ).decode("ascii")
    key_pem = server_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    return ca_pem, certificate_pem, key_pem


def write_private(path: Path, value: str | bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    mode = "wb" if isinstance(value, bytes) else "w"
    with os.fdopen(
        descriptor, mode, encoding=None if isinstance(value, bytes) else "utf-8"
    ) as stream:
        stream.write(value)


def provision_database(postgres: str, passwords: Mapping[str, str]) -> None:
    run(
        [
            "docker",
            "exec",
            "--interactive",
            postgres,
            "psql",
            "-U",
            "postgres",
            "-d",
            "postgres",
        ],
        input_text=(REPOSITORY_ROOT / "deploy/postgresql/roles.sql").read_text(
            encoding="utf-8"
        ),
    )
    sql = "\n".join(
        [
            *(
                f"ALTER ROLE {role} PASSWORD '{password}';"
                for role, password in passwords.items()
            ),
            "CREATE DATABASE automation_tool_demo OWNER automation_tool_migrator;",
            "REVOKE ALL ON DATABASE automation_tool_demo FROM PUBLIC;",
            "GRANT CONNECT ON DATABASE automation_tool_demo TO automation_tool_migrator, automation_tool_app, automation_tool_backup;",
        ]
    )
    run(
        [
            "docker",
            "exec",
            "--interactive",
            postgres,
            "psql",
            "-U",
            "postgres",
            "-d",
            "postgres",
        ],
        input_text=sql,
    )
    run(
        [
            "docker",
            "exec",
            "--interactive",
            postgres,
            "psql",
            "-U",
            "postgres",
            "-d",
            "automation_tool_demo",
        ],
        input_text=(REPOSITORY_ROOT / "deploy/postgresql/privileges.sql").read_text(
            encoding="utf-8"
        ),
    )


def environment_text(values: Mapping[str, str]) -> str:
    if any("\n" in name or "\n" in value for name, value in values.items()):
        raise AcceptanceFailure("C10-08 environment is invalid")
    return "".join(f"{name}={value}\n" for name, value in values.items())


def remove_container(name: str) -> None:
    run(["docker", "container", "rm", "--force", name], check=False)


def main(recovery_probe: RecoveryProbe | None = None) -> None:
    suffix = uuid4().hex[:12]
    project = f"c10-08-{suffix}"
    application_network = f"automation-tool-c10-08-app-{suffix}"
    database_network = f"automation-tool-c10-08-db-{suffix}"
    postgres = f"automation-tool-c10-08-postgres-{suffix}"
    runtime_volume = f"automation-tool-c10-08-runtime-{suffix}"
    migration_volume = f"automation-tool-c10-08-migration-{suffix}"
    tls_volume = f"automation-tool-c10-08-tls-{suffix}"
    postgres_volume = f"automation-tool-c10-08-postgres-{suffix}"
    control_image = f"automation-tool-control-plane:c10-08-{suffix}"
    ingress_image = f"automation-tool-ingress:c10-08-{suffix}"
    postgres_password = secrets.token_urlsafe(32)
    passwords = {
        "automation_tool_migrator": secrets.token_urlsafe(32),
        "automation_tool_app": secrets.token_urlsafe(32),
        "automation_tool_backup": secrets.token_urlsafe(32),
    }
    project_data = tomllib.loads(
        (BACKEND_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    app_version = cast(str, project_data["project"]["version"])
    revision = run(["git", "rev-parse", "HEAD"]).stdout.strip()
    ca_pem, certificate_pem, key_pem = generate_certificate()
    bootstrap_key = (
        ed25519.Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    )
    generated_secrets = {
        "databasePassword": postgres_password,
        **passwords,
        "pepper": base64url(secrets.token_bytes(32)),
        "fingerprint": base64url(secrets.token_bytes(32)),
        "operationsDigest": base64url(secrets.token_bytes(32)),
        "actionKey": base64url(secrets.token_bytes(32)),
        "tlsKey": key_pem,
    }

    try:
        run(
            [
                "docker",
                "build",
                "--file",
                str(BACKEND_ROOT / "Dockerfile"),
                "--tag",
                control_image,
                "--build-arg",
                f"APP_VERSION={app_version}",
                "--build-arg",
                f"VCS_REF={revision}",
                str(BACKEND_ROOT),
            ]
        )
        run(
            [
                "docker",
                "build",
                "--file",
                str(REPOSITORY_ROOT / "deploy/ingress/Dockerfile"),
                "--tag",
                ingress_image,
                "--build-arg",
                f"DEMO_HOST={DEMO_HOST}",
                str(REPOSITORY_ROOT / "deploy/ingress"),
            ]
        )
        control_id = cast(str, inspect("image", control_image)["Id"])
        ingress_id = cast(str, inspect("image", ingress_image)["Id"])
        control_config = inspect("image", control_image).get("Config")
        if not isinstance(
            control_config, dict
        ) or "AUTOMATION_TOOL_RUNTIME_SECRET_MODE=files" not in control_config.get(
            "Env", []
        ):
            raise AcceptanceFailure("C10-08 Control Plane image is not file-only")

        run(["docker", "network", "create", application_network])
        run(["docker", "network", "create", database_network])
        for volume in (runtime_volume, migration_volume, tls_volume, postgres_volume):
            run(["docker", "volume", "create", volume])
        write_volume(
            image=control_image,
            volume=postgres_volume,
            values={"postgres-password": (postgres_password, 65532, 0o400)},
        )
        run(
            [
                "docker",
                "run",
                "--detach",
                "--name",
                postgres,
                "--network",
                database_network,
                "--network-alias",
                "postgres",
                "--mount",
                f"type=volume,source={postgres_volume},target=/run/secrets,readonly",
                "--env",
                "POSTGRES_USER=postgres",
                "--env",
                "POSTGRES_DB=postgres",
                "--env",
                "POSTGRES_PASSWORD_FILE=/run/secrets/postgres-password",
                "--tmpfs",
                "/var/lib/postgresql:rw,noexec,nosuid,size=512m",
                "--health-cmd",
                "pg_isready --username postgres --dbname postgres",
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
        provision_database(postgres, passwords)
        migration_url = (
            "postgresql+asyncpg://automation_tool_migrator:"
            f"{passwords['automation_tool_migrator']}@postgres:5432/automation_tool_demo"
        )
        runtime_url = (
            "postgresql+asyncpg://automation_tool_app:"
            f"{passwords['automation_tool_app']}@postgres:5432/automation_tool_demo"
        )
        write_volume(
            image=control_image,
            volume=migration_volume,
            values={"database-url": (migration_url, 65532, 0o400)},
        )
        write_volume(
            image=control_image,
            volume=runtime_volume,
            values={
                "database-url": (runtime_url, 65532, 0o400),
                "account-password-pepper": (generated_secrets["pepper"], 65532, 0o400),
                "account-fingerprint-key": (
                    generated_secrets["fingerprint"],
                    65532,
                    0o400,
                ),
                "account-operations-capability-digest": (
                    generated_secrets["operationsDigest"],
                    65532,
                    0o400,
                ),
                "action-authorization-private-key": (
                    generated_secrets["actionKey"],
                    65532,
                    0o400,
                ),
            },
        )
        write_volume(
            image=control_image,
            volume=tls_volume,
            values={
                "tls.crt": (certificate_pem, 101, 0o400),
                "tls.key": (key_pem, 101, 0o400),
            },
        )

        backup = run_binary(
            [
                "docker",
                "exec",
                postgres,
                "pg_dump",
                "-U",
                "postgres",
                "-d",
                "automation_tool_demo",
                "-Fc",
            ]
        )
        with tempfile.TemporaryDirectory(prefix="automation-tool-c10-08-") as temporary:
            root = Path(temporary)
            environment_file = root / "deployment.env"
            backup_file = root / "backup.dump"
            receipt_file = root / "backup-receipt.json"
            ca_file = root / "ca.crt"
            state_directory = root / "state"
            values = {
                "CONTROL_PLANE_IMAGE": control_id,
                "INGRESS_IMAGE": ingress_id,
                "APPLICATION_NETWORK": application_network,
                "DATABASE_NETWORK": database_network,
                "RUNTIME_SECRETS_VOLUME": runtime_volume,
                "MIGRATION_SECRETS_VOLUME": migration_volume,
                "TLS_SECRETS_VOLUME": tls_volume,
                "DEMO_BIND_ADDRESS": "127.0.0.1",
                "DEMO_HTTP_PORT": "0",
                "DEMO_HTTPS_PORT": "0",
                "DEMO_HOST": DEMO_HOST,
                "ACCOUNT_PASSWORD_PEPPER_VERSION": "1",
                "ACCOUNT_OPERATIONS_ACTOR_ID": str(uuid4()),
                "DEMO_ENVIRONMENT_ID": f"demo-{suffix}",
                "DEMO_BOOTSTRAP_PUBLIC_KEY": base64url(bootstrap_key),
                "ACTION_MINIMUM_INTERVAL_SECONDS": "5",
                "ACTION_TASK_LIMIT": "20",
                "ACTION_DAILY_LIMIT": "100",
                "ACTION_CONSECUTIVE_FAILURE_THRESHOLD": "3",
            }
            write_private(environment_file, environment_text(values))
            write_private(backup_file, backup)
            receipt = {
                "version": "customer-demo-backup-receipt.v1",
                "status": "verified",
                "database": "automation_tool_demo",
                "createdAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "sha256": hashlib.sha256(backup).hexdigest(),
            }
            write_private(receipt_file, json.dumps(receipt, separators=(",", ":")))
            write_private(ca_file, ca_pem)
            deployed = run(
                [
                    sys.executable,
                    str(DEPLOYMENT_RUNNER),
                    "--environment-file",
                    str(environment_file),
                    "--backup-receipt",
                    str(receipt_file),
                    "--backup-artifact",
                    str(backup_file),
                    "--expected-app-version",
                    app_version,
                    "--expected-vcs-ref",
                    revision,
                    "--project",
                    project,
                    "--state-directory",
                    str(state_directory),
                    "--ca-file",
                    str(ca_file),
                    "--health-address",
                    "127.0.0.1",
                    "--rehearsal",
                ],
                check=False,
            )
            if deployed.returncode != 0:
                detail = deployed.stderr.strip()
                if not detail.startswith("Customer Demo ") or len(detail) > 160:
                    detail = "Customer Demo deployment failed"
                raise AcceptanceFailure(f"C10-08 runner failed: {detail}")
            projection = json.loads(deployed.stdout)
            if (
                not isinstance(projection, dict)
                or projection.get("healthStatus") != 200
                or projection.get("versionStatus") != 200
                or projection.get("replicas") != {"controlPlane": 1, "ingress": 1}
            ):
                raise AcceptanceFailure("C10-08 deployment projection is invalid")
            environment = os.environ.copy()
            environment.update(values)
            control_container = run(
                [
                    "docker",
                    "compose",
                    "--file",
                    str(COMPOSE_FILE),
                    "--project-name",
                    project,
                    "ps",
                    "--quiet",
                    "control-plane",
                ],
                environment=environment,
            ).stdout.strip()
            control = inspect("container", control_container)
            host_config = control.get("HostConfig")
            if not isinstance(host_config, dict) or host_config.get(
                "PortBindings"
            ) not in ({}, None):
                raise AcceptanceFailure("C10-08 Control Plane published a host port")
            ingress_container = run(
                [
                    "docker",
                    "compose",
                    "--file",
                    str(COMPOSE_FILE),
                    "--project-name",
                    project,
                    "ps",
                    "--quiet",
                    "ingress",
                ],
                environment=environment,
            ).stdout.strip()
            recovery_result: Mapping[str, object] | None = None
            if recovery_probe is not None:
                recovery_result = recovery_probe(
                    DeploymentRecoveryContext(
                        project=project,
                        compose_environment=environment,
                        application_network=application_network,
                        control_container=control_container,
                        ingress_container=ingress_container,
                        health_address="127.0.0.1",
                        https_port=cast(int, projection["hostPorts"]["https"]),
                        demo_host=DEMO_HOST,
                        ca_file=ca_file,
                    )
                )
                if not isinstance(recovery_result, Mapping):
                    raise AcceptanceFailure("C10-08 recovery probe is invalid")
            revision_result = run(
                [
                    "docker",
                    "exec",
                    postgres,
                    "psql",
                    "-U",
                    "postgres",
                    "-d",
                    "automation_tool_demo",
                    "-tAc",
                    "SELECT version_num FROM alembic_version",
                ]
            ).stdout.strip()
            if not revision_result:
                raise AcceptanceFailure("C10-08 alembic_version is missing")
            logs = run(
                [
                    "docker",
                    "compose",
                    "--file",
                    str(COMPOSE_FILE),
                    "--project-name",
                    project,
                    "logs",
                    "--no-color",
                ],
                environment=environment,
            ).stdout
            if any(secret in logs for secret in generated_secrets.values()):
                raise AcceptanceFailure(
                    "C10-08 service log contains a generated secret"
                )
            summary: dict[str, object] = {
                "alembicRevision": revision_result,
                "healthStatus": projection["healthStatus"],
                "hostPorts": projection["hostPorts"],
                "replicas": projection["replicas"],
                "versionStatus": projection["versionStatus"],
            }
            if recovery_result is not None:
                summary["recovery"] = dict(recovery_result)
            print(json.dumps(summary, sort_keys=True))
    finally:
        fallback_environment = os.environ.copy()
        if "values" in locals():
            fallback_environment.update(values)
        run(
            [
                "docker",
                "compose",
                "--file",
                str(COMPOSE_FILE),
                "--project-name",
                project,
                "down",
                "--remove-orphans",
            ],
            environment=fallback_environment,
            check=False,
        )
        remove_container(postgres)
        for network in (application_network, database_network):
            run(["docker", "network", "rm", network], check=False)
        for volume in (runtime_volume, migration_volume, tls_volume, postgres_volume):
            run(["docker", "volume", "rm", "--force", volume], check=False)
        for image in (control_image, ingress_image):
            run(["docker", "image", "rm", "--force", image], check=False)


if __name__ == "__main__":
    main()
