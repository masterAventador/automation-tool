#!/usr/bin/env python3
"""Build and verify the C10-02 Control Plane image through its real entry point."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import tempfile
import time
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from runtime_secret_volume import writer_command, writer_payload

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
POSTGRES_IMAGE = "postgres:18.4-bookworm"
SOURCE_URL = "https://github.com/masterAventador/automation-tool"


class AcceptanceFailure(RuntimeError):
    """Fixed failure without reflecting a credential or container log."""


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
        raise AcceptanceFailure("C10-02 container command failed")
    return result


def write_private_environment(path: Path, values: Mapping[str, str]) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        for name, value in values.items():
            if "\n" in name or "\n" in value:
                raise AcceptanceFailure("C10-02 environment value is invalid")
            stream.write(f"{name}={value}\n")


def inspect(kind: str, identity: str) -> dict[str, Any]:
    payload = json.loads(run(["docker", kind, "inspect", identity]).stdout)
    if (
        not isinstance(payload, list)
        or len(payload) != 1
        or not isinstance(payload[0], dict)
    ):
        raise AcceptanceFailure("C10-02 Docker inspection is invalid")
    return cast(dict[str, Any], payload[0])


def wait_for_healthy(container_name: str, *, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        state = inspect("container", container_name).get("State")
        if not isinstance(state, dict):
            raise AcceptanceFailure("C10-02 container state is invalid")
        health = state.get("Health")
        if isinstance(health, dict) and health.get("Status") == "healthy":
            return
        if state.get("Running") is False:
            raise AcceptanceFailure("C10-02 container exited before becoming healthy")
        time.sleep(0.5)
    raise AcceptanceFailure("C10-02 container health timed out")


def remove_container(container_name: str) -> None:
    run(["docker", "container", "rm", "--force", container_name], check=False)


def main() -> None:
    suffix = uuid4().hex[:12]
    network_name = f"automation-tool-c10-02-{suffix}"
    postgres_name = f"automation-tool-c10-02-postgres-{suffix}"
    control_plane_name = f"automation-tool-c10-02-control-plane-{suffix}"
    secret_volume = f"automation-tool-c10-02-secrets-{suffix}"
    image = f"automation-tool-control-plane:c10-02-{suffix}"
    password = secrets.token_urlsafe(24)
    project = tomllib.loads(
        (BACKEND_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    app_version = cast(str, project["project"]["version"])
    revision = run(["git", "rev-parse", "HEAD"]).stdout.strip()

    with tempfile.TemporaryDirectory(
        prefix=f"automation-tool-c10-02-{suffix}-"
    ) as temporary:
        temporary_root = Path(temporary)
        postgres_environment = temporary_root / "postgres.env"
        write_private_environment(
            postgres_environment,
            {
                "POSTGRES_USER": "c10_app",
                "POSTGRES_PASSWORD": password,
                "POSTGRES_DB": "c10_demo",
            },
        )
        database_url = f"postgresql+asyncpg://c10_app:{password}@postgres:5432/c10_demo"

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
            image_inspection = inspect("image", image)
            image_config = image_inspection.get("Config")
            if (
                not isinstance(image_config, dict)
                or image_config.get("User") != "65532:65532"
            ):
                raise AcceptanceFailure("C10-02 image user is invalid")
            labels = image_config.get("Labels")
            if not isinstance(labels, dict) or labels != {
                "org.opencontainers.image.description": (
                    "Automation Tool customer Demo Control Plane"
                ),
                "org.opencontainers.image.revision": revision,
                "org.opencontainers.image.source": SOURCE_URL,
                "org.opencontainers.image.title": "automation-tool-control-plane",
                "org.opencontainers.image.version": app_version,
            }:
                raise AcceptanceFailure("C10-02 OCI labels are invalid")
            if image_config.get("Healthcheck") is None:
                raise AcceptanceFailure("C10-02 image healthcheck is absent")

            run(["docker", "network", "create", network_name])
            run(["docker", "volume", "create", secret_volume])
            run(
                writer_command(image=image, volume=secret_volume),
                input_text=writer_payload({"database-url": database_url}),
            )
            run(
                [
                    "docker",
                    "run",
                    "--detach",
                    "--name",
                    postgres_name,
                    "--network",
                    network_name,
                    "--network-alias",
                    "postgres",
                    "--env-file",
                    str(postgres_environment),
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
            wait_for_healthy(postgres_name, timeout_seconds=45)

            run(
                [
                    "docker",
                    "run",
                    "--detach",
                    "--name",
                    control_plane_name,
                    "--network",
                    network_name,
                    "--mount",
                    f"type=volume,source={secret_volume},target=/run/secrets,readonly",
                    "--read-only",
                    "--tmpfs",
                    "/tmp:rw,noexec,nosuid,size=16m",
                    "--security-opt",
                    "no-new-privileges=true",
                    "--cap-drop",
                    "ALL",
                    image,
                ]
            )
            wait_for_healthy(control_plane_name, timeout_seconds=60)
            container = inspect("container", control_plane_name)
            host_config = container.get("HostConfig")
            if (
                not isinstance(host_config, dict)
                or host_config.get("ReadonlyRootfs") is not True
            ):
                raise AcceptanceFailure("C10-02 root filesystem is writable")
            if host_config.get("PortBindings") not in ({}, None):
                raise AcceptanceFailure("C10-02 published an unexpected host port")

            probe = run(
                [
                    "docker",
                    "exec",
                    control_plane_name,
                    "python",
                    "-c",
                    (
                        "import importlib.util,json,os,pathlib,urllib.request;"
                        "health=json.loads(urllib.request.urlopen("
                        "'http://127.0.0.1:8000/api/v1/health',timeout=2).read());"
                        "version=json.loads(urllib.request.urlopen("
                        "'http://127.0.0.1:8000/api/v1/version',timeout=2).read());"
                        "print(json.dumps({'health':health,'version':version,'uid':os.getuid(),"
                        "'pytestInstalled':importlib.util.find_spec('pytest') is not None,"
                        "'browserCachePresent':any(pathlib.Path(path).exists() for path in ("
                        "'/ms-playwright','/home/pwuser/.cache/ms-playwright',"
                        "'/app/ms-playwright'))},sort_keys=True))"
                    ),
                ],
                check=False,
            )
            if probe.returncode != 0:
                categories = (
                    "SyntaxError",
                    "HTTPError",
                    "PermissionError",
                    "ModuleNotFoundError",
                    "AssertionError",
                )
                category = next(
                    (
                        candidate
                        for candidate in categories
                        if candidate in probe.stderr
                    ),
                    "unknown",
                )
                raise AcceptanceFailure(
                    f"C10-02 container runtime probe failed: {category}"
                )
            projection = json.loads(probe.stdout)
            if projection["health"] != {
                "service": "control-plane",
                "status": "ok",
                "version": app_version,
            }:
                raise AcceptanceFailure("C10-02 health response is invalid")
            if projection["version"].get("version") != app_version:
                raise AcceptanceFailure("C10-02 version response is invalid")
            if projection["uid"] != 65532:
                raise AcceptanceFailure("C10-02 runtime user is invalid")
            if projection["pytestInstalled"] is not False:
                raise AcceptanceFailure("C10-02 image contains a test dependency")
            if projection["browserCachePresent"] is not False:
                raise AcceptanceFailure("C10-02 image contains a browser download")

            write_attempt = run(
                [
                    "docker",
                    "exec",
                    control_plane_name,
                    "python",
                    "-c",
                    "from pathlib import Path; Path('/c10-02-write-probe').write_text('x')",
                ],
                check=False,
            )
            if write_attempt.returncode == 0:
                raise AcceptanceFailure("C10-02 read-only root filesystem was bypassed")

            stopped_at = time.monotonic()
            run(["docker", "stop", "--time", "30", control_plane_name])
            stop_elapsed = time.monotonic() - stopped_at
            stopped = inspect("container", control_plane_name).get("State")
            if not isinstance(stopped, dict) or stopped.get("ExitCode") != 0:
                raise AcceptanceFailure("C10-02 Control Plane did not stop gracefully")
            if stop_elapsed > 31:
                raise AcceptanceFailure("C10-02 graceful stop exceeded its deadline")
            logs = run(["docker", "logs", control_plane_name]).stdout
            if password in logs or "postgresql+asyncpg://" in logs:
                raise AcceptanceFailure(
                    "C10-02 container logs exposed a database secret"
                )

            print(
                "[C10-02] locked non-root image, real health/version, read-only rootfs, "
                "and graceful SIGTERM verified"
            )
        finally:
            remove_container(control_plane_name)
            remove_container(postgres_name)
            run(["docker", "network", "rm", network_name], check=False)
            run(["docker", "volume", "rm", "--force", secret_volume], check=False)
            run(["docker", "image", "rm", "--force", image], check=False)


if __name__ == "__main__":
    main()
