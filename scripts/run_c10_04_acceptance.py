#!/usr/bin/env python3
"""Verify C10-04 through a private TLS -> Control Plane -> PostgreSQL stack."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import tempfile
import time
import tomllib
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from runtime_secret_volume import writer_command, writer_payload

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
INGRESS_ROOT = REPOSITORY_ROOT / "deploy" / "ingress"
POSTGRES_IMAGE = "postgres:18.4-bookworm"
DEMO_HOST = "api.automation-tool.test"
INVALID_HOST = "api.demo.test;include"

PROBE_SOURCE = r"""import concurrent.futures
import http.client
import json
import ssl
from http import HTTPStatus

HOST = "api.automation-tool.test"
CA_FILE = "/probe/ca.crt"


def tls_context():
    context = ssl.create_default_context(cafile=CA_FILE)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def https_request(method, path, *, body=None, host_header=None):
    connection = http.client.HTTPSConnection(HOST, 8443, context=tls_context(), timeout=10)
    if host_header is None:
        connection.request(method, path, body=body)
    else:
        connection.putrequest(method, path, skip_host=True)
        connection.putheader("Host", host_header)
        if body is not None:
            connection.putheader("Content-Length", str(len(body)))
        connection.endheaders(body)
    response = connection.getresponse()
    payload = response.read()
    headers = {name.lower(): value for name, value in response.getheaders()}
    status = response.status
    connection.close()
    return status, headers, payload


status, headers, payload = https_request("GET", "/api/v1/health")
if status != HTTPStatus.OK:
    raise RuntimeError("health status is invalid")
health = json.loads(payload)
if health.get("status") != "ok":
    raise RuntimeError("health projection is invalid")

required_headers = {
    "strict-transport-security": "max-age=31536000; includeSubDomains",
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "permissions-policy": "camera=(), microphone=(), geolocation=()",
    "content-security-policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
}
for name, expected in required_headers.items():
    if headers.get(name) != expected:
        raise RuntimeError("security header is invalid")
if headers.get("server") != "nginx":
    raise RuntimeError("server version is not minimized")

plain = http.client.HTTPConnection(HOST, 8080, timeout=10)
plain.request("GET", "/api/v1/health?probe=redirect")
redirect = plain.getresponse()
redirect.read()
if redirect.status != HTTPStatus.PERMANENT_REDIRECT:
    raise RuntimeError("plaintext request was not redirected")
if redirect.getheader("Location") != (
    "https://api.automation-tool.test/api/v1/health?probe=redirect"
):
    raise RuntimeError("redirect target is not canonical")
plain.close()

wrong_host, _, _ = https_request(
    "GET", "/api/v1/health", host_header="wrong.automation-tool.test"
)
if wrong_host != HTTPStatus.MISDIRECTED_REQUEST:
    raise RuntimeError("unknown host was not rejected")

too_large, _, _ = https_request(
    "POST", "/api/v1/account-sessions", body=b"x" * (1024 * 1024 + 1)
)
if too_large != HTTPStatus.REQUEST_ENTITY_TOO_LARGE:
    raise RuntimeError("oversized body was not rejected")

legacy = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
legacy.load_verify_locations(CA_FILE)
legacy.minimum_version = ssl.TLSVersion.TLSv1_1
legacy.maximum_version = ssl.TLSVersion.TLSv1_1
try:
    connection = http.client.HTTPSConnection(HOST, 8443, context=legacy, timeout=5)
    connection.request("GET", "/api/v1/health")
    response = connection.getresponse()
    response.read()
    connection.close()
except (OSError, ssl.SSLError):
    legacy_rejected = True
else:
    legacy_rejected = False
if not legacy_rejected:
    raise RuntimeError("TLS 1.1 was accepted")


def rate_probe(_index):
    try:
        return https_request("GET", "/api/v1/health")[0]
    except OSError:
        return 0


with concurrent.futures.ThreadPoolExecutor(max_workers=64) as executor:
    rate_statuses = list(executor.map(rate_probe, range(64)))
if HTTPStatus.OK not in rate_statuses:
    raise RuntimeError("rate probe did not reach the upstream")
if HTTPStatus.TOO_MANY_REQUESTS not in rate_statuses:
    raise RuntimeError("rate limit was not enforced")

print(json.dumps({
    "health_status": status,
    "redirect_status": redirect.status,
    "unknown_host_status": wrong_host,
    "oversized_status": too_large,
    "tls_1_1_rejected": legacy_rejected,
    "rate_limited": rate_statuses.count(HTTPStatus.TOO_MANY_REQUESTS),
    "security_headers": sorted(required_headers),
}, sort_keys=True))
"""


class AcceptanceFailure(RuntimeError):
    """Fixed failure that never reflects a secret or raw container log."""


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
        raise AcceptanceFailure("C10-04 container command failed")
    return result


def write_private_text(path: Path, value: str, *, mode: int = 0o600) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(value)


def write_private_bytes(path: Path, value: bytes, *, mode: int = 0o600) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(value)


def write_environment(path: Path, values: Mapping[str, str]) -> None:
    if any("\n" in name or "\n" in value for name, value in values.items()):
        raise AcceptanceFailure("C10-04 environment value is invalid")
    write_private_text(
        path, "".join(f"{name}={value}\n" for name, value in values.items())
    )


def inspect(kind: str, identity: str) -> dict[str, Any]:
    payload = json.loads(run(["docker", kind, "inspect", identity]).stdout)
    if (
        not isinstance(payload, list)
        or len(payload) != 1
        or not isinstance(payload[0], dict)
    ):
        raise AcceptanceFailure("C10-04 Docker inspection is invalid")
    return cast(dict[str, Any], payload[0])


def wait_for_healthy(container_name: str, *, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        state = inspect("container", container_name).get("State")
        if not isinstance(state, dict):
            raise AcceptanceFailure("C10-04 container state is invalid")
        health = state.get("Health")
        if isinstance(health, dict) and health.get("Status") == "healthy":
            return
        if state.get("Running") is False:
            raise AcceptanceFailure("C10-04 container exited before health")
        time.sleep(0.5)
    raise AcceptanceFailure("C10-04 container health timed out")


def generate_certificate(root: Path) -> tuple[Path, Path, Path]:
    now = datetime.now(UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "C10-04 temporary CA")]
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
    ca_path = root / "ca.crt"
    certificate_path = root / "tls.crt"
    key_path = root / "tls.key"
    write_private_bytes(
        ca_path, ca_certificate.public_bytes(serialization.Encoding.PEM), mode=0o444
    )
    write_private_bytes(
        certificate_path,
        server_certificate.public_bytes(serialization.Encoding.PEM),
        mode=0o444,
    )
    write_private_bytes(
        key_path,
        server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )
    return ca_path, certificate_path, key_path


def remove_container(name: str) -> None:
    run(["docker", "container", "rm", "--force", name], check=False)


def main() -> None:
    suffix = uuid4().hex[:12]
    network = f"automation-tool-c10-04-{suffix}"
    postgres = f"automation-tool-c10-04-postgres-{suffix}"
    control_plane = f"automation-tool-c10-04-control-plane-{suffix}"
    ingress = f"automation-tool-c10-04-ingress-{suffix}"
    secret_volume = f"automation-tool-c10-04-secrets-{suffix}"
    control_plane_secret_volume = (
        f"automation-tool-c10-04-control-plane-secrets-{suffix}"
    )
    control_plane_image = f"automation-tool-control-plane:c10-04-{suffix}"
    ingress_image = f"automation-tool-ingress:c10-04-{suffix}"
    invalid_image = f"automation-tool-ingress:c10-04-invalid-{suffix}"
    database_password = secrets.token_urlsafe(32)
    project = tomllib.loads(
        (BACKEND_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    app_version = cast(str, project["project"]["version"])
    revision = run(["git", "rev-parse", "HEAD"]).stdout.strip()

    local_root = REPOSITORY_ROOT / ".local"
    local_root.mkdir(mode=0o700, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f"automation-tool-c10-04-{suffix}-",
        dir=local_root,
    ) as temporary:
        root = Path(temporary)
        postgres_environment = root / "postgres.env"
        probe_path = root / "probe.py"
        ca_path, _certificate_path, _key_path = generate_certificate(root)
        write_environment(
            postgres_environment,
            {
                "POSTGRES_USER": "c10_app",
                "POSTGRES_PASSWORD": database_password,
                "POSTGRES_DB": "c10_demo",
            },
        )
        database_url = (
            f"postgresql+asyncpg://c10_app:{database_password}@postgres:5432/c10_demo"
        )
        write_private_text(probe_path, PROBE_SOURCE, mode=0o444)

        try:
            run(
                [
                    "docker",
                    "build",
                    "--file",
                    str(BACKEND_ROOT / "Dockerfile"),
                    "--tag",
                    control_plane_image,
                    "--build-arg",
                    f"APP_VERSION={app_version}",
                    "--build-arg",
                    f"VCS_REF={revision}",
                    str(REPOSITORY_ROOT),
                ]
            )
            run(
                [
                    "docker",
                    "build",
                    "--file",
                    str(INGRESS_ROOT / "Dockerfile"),
                    "--tag",
                    ingress_image,
                    "--build-arg",
                    f"DEMO_HOST={DEMO_HOST}",
                    str(INGRESS_ROOT),
                ]
            )
            invalid_build = run(
                [
                    "docker",
                    "build",
                    "--file",
                    str(INGRESS_ROOT / "Dockerfile"),
                    "--tag",
                    invalid_image,
                    "--build-arg",
                    f"DEMO_HOST={INVALID_HOST}",
                    str(INGRESS_ROOT),
                ],
                check=False,
            )
            if invalid_build.returncode == 0:
                raise AcceptanceFailure("C10-04 invalid hostname build succeeded")

            image_config = inspect("image", ingress_image).get("Config")
            if (
                not isinstance(image_config, dict)
                or image_config.get("User") != "101:101"
            ):
                raise AcceptanceFailure("C10-04 ingress image user is invalid")
            if image_config.get("Env") and any(
                DEMO_HOST in value or "tls.key" in value
                for value in cast(list[str], image_config["Env"])
            ):
                raise AcceptanceFailure(
                    "C10-04 ingress image environment leaked configuration"
                )

            run(["docker", "network", "create", network])
            run(["docker", "volume", "create", secret_volume])
            run(["docker", "volume", "create", control_plane_secret_volume])
            run(
                writer_command(
                    image=control_plane_image,
                    volume=control_plane_secret_volume,
                ),
                input_text=writer_payload({"database-url": database_url}),
            )
            run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--user",
                    "0:0",
                    "--mount",
                    (
                        f"type=bind,source={root / 'tls.crt'},"
                        "target=/source/tls.crt,readonly"
                    ),
                    "--mount",
                    (
                        f"type=bind,source={root / 'tls.key'},"
                        "target=/source/tls.key,readonly"
                    ),
                    "--mount",
                    f"type=volume,source={secret_volume},target=/run/secrets",
                    "--entrypoint",
                    "/bin/sh",
                    ingress_image,
                    "-c",
                    (
                        "cp /source/tls.crt /run/secrets/tls.crt && "
                        "cp /source/tls.key /run/secrets/tls.key && "
                        "chown 101:101 /run/secrets/tls.crt /run/secrets/tls.key && "
                        "chmod 0444 /run/secrets/tls.crt && chmod 0400 /run/secrets/tls.key"
                    ),
                ]
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
                    "--env-file",
                    str(postgres_environment),
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
                        f"type=volume,source={control_plane_secret_volume},"
                        "target=/run/secrets,readonly"
                    ),
                    "--entrypoint",
                    "alembic",
                    control_plane_image,
                    "-c",
                    "/app/alembic.ini",
                    "upgrade",
                    "head",
                ]
            )
            run(
                [
                    "docker",
                    "run",
                    "--detach",
                    "--name",
                    control_plane,
                    "--network",
                    network,
                    "--network-alias",
                    "control-plane",
                    "--mount",
                    (
                        f"type=volume,source={control_plane_secret_volume},"
                        "target=/run/secrets,readonly"
                    ),
                    "--read-only",
                    "--tmpfs",
                    "/tmp:rw,noexec,nosuid,size=16m",
                    "--security-opt",
                    "no-new-privileges=true",
                    "--cap-drop",
                    "ALL",
                    control_plane_image,
                ]
            )
            wait_for_healthy(control_plane, timeout_seconds=60)
            run(
                [
                    "docker",
                    "run",
                    "--detach",
                    "--name",
                    ingress,
                    "--network",
                    network,
                    "--network-alias",
                    DEMO_HOST,
                    "--read-only",
                    "--tmpfs",
                    "/tmp:rw,noexec,nosuid,size=32m,uid=101,gid=101,mode=0700",
                    "--security-opt",
                    "no-new-privileges=true",
                    "--cap-drop",
                    "ALL",
                    "--mount",
                    f"type=volume,source={secret_volume},target=/run/secrets,readonly",
                    ingress_image,
                ]
            )
            time.sleep(1)
            ingress_inspection = inspect("container", ingress)
            ingress_host = ingress_inspection.get("HostConfig")
            ingress_state = ingress_inspection.get("State")
            if not isinstance(ingress_host, dict):
                raise AcceptanceFailure("C10-04 ingress HostConfig is invalid")
            if ingress_host.get("ReadonlyRootfs") is not True:
                raise AcceptanceFailure("C10-04 ingress root filesystem is writable")
            if ingress_host.get("PortBindings") not in ({}, None):
                raise AcceptanceFailure("C10-04 ingress published a host port")
            if (
                not isinstance(ingress_state, dict)
                or ingress_state.get("Running") is not True
            ):
                raise AcceptanceFailure("C10-04 ingress did not remain running")

            probe = run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    network,
                    "--read-only",
                    "--tmpfs",
                    "/tmp:rw,noexec,nosuid,size=16m",
                    "--security-opt",
                    "no-new-privileges=true",
                    "--cap-drop",
                    "ALL",
                    "--mount",
                    f"type=bind,source={probe_path},target=/probe/probe.py,readonly",
                    "--mount",
                    f"type=bind,source={ca_path},target=/probe/ca.crt,readonly",
                    "--entrypoint",
                    "python",
                    control_plane_image,
                    "/probe/probe.py",
                ]
            )
            projection = json.loads(probe.stdout)
            if projection.get("health_status") != HTTPStatus.OK:
                raise AcceptanceFailure("C10-04 real health projection is invalid")
            if projection.get("redirect_status") != HTTPStatus.PERMANENT_REDIRECT:
                raise AcceptanceFailure("C10-04 redirect projection is invalid")
            if projection.get("unknown_host_status") != HTTPStatus.MISDIRECTED_REQUEST:
                raise AcceptanceFailure("C10-04 host projection is invalid")
            if (
                projection.get("oversized_status")
                != HTTPStatus.REQUEST_ENTITY_TOO_LARGE
            ):
                raise AcceptanceFailure("C10-04 request bound projection is invalid")
            if projection.get("tls_1_1_rejected") is not True:
                raise AcceptanceFailure("C10-04 TLS projection is invalid")
            if (
                not isinstance(projection.get("rate_limited"), int)
                or projection["rate_limited"] < 1
            ):
                raise AcceptanceFailure("C10-04 rate limit projection is invalid")

            logs = run(["docker", "logs", ingress]).stdout
            if (
                database_password in logs
                or "PRIVATE KEY" in logs
                or database_url in logs
            ):
                raise AcceptanceFailure("C10-04 ingress log leaked a secret")
            print(json.dumps(projection, ensure_ascii=False, sort_keys=True))
        finally:
            remove_container(ingress)
            remove_container(control_plane)
            remove_container(postgres)
            run(["docker", "network", "rm", network], check=False)
            run(["docker", "volume", "rm", "--force", secret_volume], check=False)
            run(
                ["docker", "volume", "rm", "--force", control_plane_secret_volume],
                check=False,
            )
            run(["docker", "image", "rm", "--force", invalid_image], check=False)
            run(["docker", "image", "rm", "--force", ingress_image], check=False)
            run(["docker", "image", "rm", "--force", control_plane_image], check=False)


if __name__ == "__main__":
    main()
