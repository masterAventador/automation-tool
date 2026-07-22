#!/usr/bin/env python3
"""Verify C10-03 roles, private migration, backup, and isolated restore."""

from __future__ import annotations

import json
import os
import re
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
ROLES_SQL = REPOSITORY_ROOT / "deploy/postgresql/roles.sql"
PRIVILEGES_SQL = REPOSITORY_ROOT / "deploy/postgresql/privileges.sql"
POSTGRES_IMAGE = "postgres:18.4-bookworm"
DATABASE_NAME = "automation_tool_demo"
ROLE_NAMES = (
    "automation_tool_migrator",
    "automation_tool_app",
    "automation_tool_backup",
)
SAFE_PASSWORD = re.compile(r"^[A-Za-z0-9_-]{32,}$", re.ASCII)


class AcceptanceFailure(RuntimeError):
    """Fixed failure without reflecting SQL, credentials, or database output."""


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
        raise AcceptanceFailure("C10-03 command failed")
    return result


def run_binary(arguments: Sequence[str]) -> bytes:
    result = subprocess.run(
        list(arguments),
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        raise AcceptanceFailure("C10-03 binary command failed")
    return result.stdout


def write_private_text(path: Path, value: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(value)


def write_private_bytes(path: Path, value: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(value)


def write_environment(path: Path, values: Mapping[str, str]) -> None:
    for name, value in values.items():
        if "\n" in name or "\n" in value:
            raise AcceptanceFailure("C10-03 environment value is invalid")
    write_private_text(
        path, "".join(f"{name}={value}\n" for name, value in values.items())
    )


def inspect_container(name: str) -> dict[str, Any]:
    payload = json.loads(run(["docker", "container", "inspect", name]).stdout)
    if (
        not isinstance(payload, list)
        or len(payload) != 1
        or not isinstance(payload[0], dict)
    ):
        raise AcceptanceFailure("C10-03 container inspection is invalid")
    return cast(dict[str, Any], payload[0])


def poll_health(name: str) -> None:
    for _attempt in range(60):
        container = inspect_container(name)
        host = container.get("HostConfig")
        state = container.get("State")
        if not isinstance(host, dict) or host.get("PortBindings") not in ({}, None):
            raise AcceptanceFailure("C10-03 PostgreSQL exposed a host port")
        if not isinstance(state, dict):
            raise AcceptanceFailure("C10-03 PostgreSQL state is invalid")
        health = state.get("Health")
        if isinstance(health, dict) and health.get("Status") == "healthy":
            return
        if state.get("Running") is False:
            raise AcceptanceFailure("C10-03 PostgreSQL exited before health")
        time.sleep(0.5)
    raise AcceptanceFailure("C10-03 PostgreSQL health timed out")


def start_postgresql(
    *,
    name: str,
    alias: str,
    network: str,
    environment_file: Path,
) -> None:
    run(
        [
            "docker",
            "run",
            "--detach",
            "--name",
            name,
            "--network",
            network,
            "--network-alias",
            alias,
            "--env-file",
            str(environment_file),
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
    poll_health(name)


def provision_database(name: str, passwords: Mapping[str, str]) -> None:
    for password in passwords.values():
        if SAFE_PASSWORD.fullmatch(password) is None:
            raise AcceptanceFailure("C10-03 generated password is invalid")
    run(
        [
            "docker",
            "exec",
            "--interactive",
            name,
            "psql",
            "-U",
            "postgres",
            "-d",
            "postgres",
        ],
        input_text=ROLES_SQL.read_text(encoding="utf-8"),
    )
    bootstrap_sql = "\n".join(
        [
            *(
                f"ALTER ROLE {role} PASSWORD '{passwords[role]}';"
                for role in ROLE_NAMES
            ),
            f"CREATE DATABASE {DATABASE_NAME} OWNER automation_tool_migrator;",
            f"REVOKE ALL ON DATABASE {DATABASE_NAME} FROM PUBLIC;",
            f"GRANT CONNECT ON DATABASE {DATABASE_NAME} TO {', '.join(ROLE_NAMES)};",
        ]
    )
    run(
        [
            "docker",
            "exec",
            "--interactive",
            name,
            "psql",
            "-U",
            "postgres",
            "-d",
            "postgres",
        ],
        input_text=bootstrap_sql,
    )


def psql(
    *,
    network: str,
    host: str,
    role: str,
    password_file: Path,
    sql: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = run(
        [
            "docker",
            "run",
            "--rm",
            "--interactive",
            "--network",
            network,
            "--mount",
            f"type=bind,source={password_file},target=/run/secrets/pgpass,readonly",
            "--env",
            "PGPASSFILE=/run/secrets/pgpass",
            "--entrypoint",
            "psql",
            POSTGRES_IMAGE,
            "--host",
            host,
            "--username",
            role,
            "--dbname",
            DATABASE_NAME,
            "--set",
            "ON_ERROR_STOP=1",
            "--tuples-only",
            "--no-align",
        ],
        input_text=f"{sql}\n\\q\n",
        check=False,
    )
    if check and result.returncode != 0:
        categories = {
            "password authentication failed": "authentication",
            "Permission denied": "secret-file-permission",
            "permission denied for schema": "schema-permission",
            "could not translate host name": "network-name",
            "no password supplied": "password-file-ignored",
            "password file": "password-file-permission",
            "bind source path does not exist": "bind-source-unavailable",
        }
        category = next(
            (safe for marker, safe in categories.items() if marker in result.stderr),
            "unknown",
        )
        raise AcceptanceFailure(f"C10-03 psql command failed: {category}")
    return result


def remove_container(name: str) -> None:
    run(["docker", "container", "rm", "--force", name], check=False)


def main() -> None:
    suffix = uuid4().hex[:12]
    network = f"automation-tool-c10-03-{suffix}"
    primary = f"automation-tool-c10-03-primary-{suffix}"
    restore = f"automation-tool-c10-03-restore-{suffix}"
    image = f"automation-tool-control-plane:c10-03-{suffix}"
    secret_volume = f"automation-tool-c10-03-secrets-{suffix}"
    passwords = {role: secrets.token_urlsafe(32) for role in ROLE_NAMES}
    postgres_password = secrets.token_urlsafe(32)
    project = tomllib.loads(
        (BACKEND_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    app_version = cast(str, project["project"]["version"])
    revision = run(["git", "rev-parse", "HEAD"]).stdout.strip()

    local_root = REPOSITORY_ROOT / ".local"
    local_root.mkdir(mode=0o700, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f"automation-tool-c10-03-{suffix}-",
        dir=local_root,
    ) as temporary:
        root = Path(temporary)
        postgres_environment = root / "postgres.env"
        password_file = root / "pgpass"
        backup_file = root / "backup.dump"
        write_environment(
            postgres_environment,
            {
                "POSTGRES_USER": "postgres",
                "POSTGRES_PASSWORD": postgres_password,
                "POSTGRES_DB": "postgres",
                "POSTGRES_INITDB_ARGS": "--auth-host=scram-sha-256 --data-checksums",
            },
        )
        migration_database_url = (
            "postgresql+asyncpg://automation_tool_migrator:"
            f"{passwords['automation_tool_migrator']}@primary:5432/{DATABASE_NAME}"
        )
        password_lines = []
        for host in ("primary", "restore"):
            for role in ROLE_NAMES:
                password_lines.append(
                    f"{host}:5432:{DATABASE_NAME}:{role}:{passwords[role]}\n"
                )
        write_private_text(password_file, "".join(password_lines))

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
            run(["docker", "volume", "create", secret_volume])
            run(
                writer_command(image=image, volume=secret_volume),
                input_text=writer_payload({"database-url": migration_database_url}),
            )
            run(["docker", "network", "create", network])
            start_postgresql(
                name=primary,
                alias="primary",
                network=network,
                environment_file=postgres_environment,
            )
            provision_database(primary, passwords)

            run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    network,
                    "--mount",
                    f"type=volume,source={secret_volume},target=/run/secrets,readonly",
                    "--entrypoint",
                    "alembic",
                    image,
                    "-c",
                    "/app/alembic.ini",
                    "upgrade",
                    "head",
                ]
            )
            psql(
                network=network,
                host="primary",
                role="automation_tool_migrator",
                password_file=password_file,
                sql=(
                    "CREATE TABLE c10_role_probe (id integer PRIMARY KEY, note text NOT NULL);"
                    "INSERT INTO c10_role_probe VALUES (1, 'migration-owned');"
                ),
            )
            psql(
                network=network,
                host="primary",
                role="automation_tool_migrator",
                password_file=password_file,
                sql=PRIVILEGES_SQL.read_text(encoding="utf-8"),
            )
            psql(
                network=network,
                host="primary",
                role="automation_tool_app",
                password_file=password_file,
                sql=(
                    "INSERT INTO c10_role_probe VALUES (2, 'app-write');"
                    "SELECT count(*) FROM c10_role_probe;"
                ),
            )
            if (
                psql(
                    network=network,
                    host="primary",
                    role="automation_tool_app",
                    password_file=password_file,
                    sql="CREATE TABLE forbidden_app_ddl (id integer);",
                    check=False,
                ).returncode
                == 0
            ):
                raise AcceptanceFailure("C10-03 application role obtained DDL")
            psql(
                network=network,
                host="primary",
                role="automation_tool_backup",
                password_file=password_file,
                sql="SELECT count(*) FROM c10_role_probe;",
            )
            if (
                psql(
                    network=network,
                    host="primary",
                    role="automation_tool_backup",
                    password_file=password_file,
                    sql="INSERT INTO c10_role_probe VALUES (3, 'forbidden');",
                    check=False,
                ).returncode
                == 0
            ):
                raise AcceptanceFailure("C10-03 backup role obtained write access")

            primary_revision = psql(
                network=network,
                host="primary",
                role="automation_tool_app",
                password_file=password_file,
                sql="SELECT version_num FROM alembic_version;",
            ).stdout.strip()
            dump = run_binary(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    network,
                    "--mount",
                    f"type=bind,source={password_file},target=/run/secrets/pgpass,readonly",
                    "--env",
                    "PGPASSFILE=/run/secrets/pgpass",
                    "--entrypoint",
                    "pg_dump",
                    POSTGRES_IMAGE,
                    "--format=custom",
                    "--no-owner",
                    "--no-acl",
                    "--host",
                    "primary",
                    "--username",
                    "automation_tool_backup",
                    "--dbname",
                    DATABASE_NAME,
                ]
            )
            if len(dump) < 1024 or any(
                password.encode() in dump for password in passwords.values()
            ):
                raise AcceptanceFailure("C10-03 backup archive is invalid")
            write_private_bytes(backup_file, dump)

            start_postgresql(
                name=restore,
                alias="restore",
                network=network,
                environment_file=postgres_environment,
            )
            provision_database(restore, passwords)
            run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    network,
                    "--mount",
                    f"type=bind,source={password_file},target=/run/secrets/pgpass,readonly",
                    "--mount",
                    f"type=bind,source={backup_file},target=/run/backup/archive.dump,readonly",
                    "--env",
                    "PGPASSFILE=/run/secrets/pgpass",
                    "--entrypoint",
                    "pg_restore",
                    POSTGRES_IMAGE,
                    "--exit-on-error",
                    "--no-owner",
                    "--no-acl",
                    "--role=automation_tool_migrator",
                    "--host",
                    "restore",
                    "--username",
                    "automation_tool_migrator",
                    "--dbname",
                    DATABASE_NAME,
                    "/run/backup/archive.dump",
                ]
            )
            psql(
                network=network,
                host="restore",
                role="automation_tool_migrator",
                password_file=password_file,
                sql=PRIVILEGES_SQL.read_text(encoding="utf-8"),
            )
            restored = (
                psql(
                    network=network,
                    host="restore",
                    role="automation_tool_app",
                    password_file=password_file,
                    sql=(
                        "SELECT version_num FROM alembic_version;"
                        "SELECT string_agg(note, ',' ORDER BY id) FROM c10_role_probe;"
                    ),
                )
                .stdout.strip()
                .splitlines()
            )
            if restored != [primary_revision, "migration-owned,app-write"]:
                raise AcceptanceFailure("C10-03 isolated restore facts differ")

            print(
                "[C10-03] private roles, Alembic migration, read-only backup, "
                "and isolated PostgreSQL restore verified"
            )
        finally:
            remove_container(restore)
            remove_container(primary)
            run(["docker", "network", "rm", network], check=False)
            run(["docker", "volume", "rm", "--force", secret_volume], check=False)
            run(["docker", "image", "rm", "--force", image], check=False)


if __name__ == "__main__":
    main()
