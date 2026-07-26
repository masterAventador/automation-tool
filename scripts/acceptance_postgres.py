#!/usr/bin/env python3
"""Cross-platform isolated PostgreSQL lifecycle for acceptance scripts."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


def _required_postgres_tool(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        raise RuntimeError(f"{name} is required for native Windows PostgreSQL acceptance")
    return executable


@contextmanager
def _native_windows_postgres(
    *,
    database_port: int,
    environment: dict[str, str],
) -> Iterator[None]:
    initdb = _required_postgres_tool("initdb")
    pg_ctl = _required_postgres_tool("pg_ctl")
    createdb = _required_postgres_tool("createdb")
    username = environment["AUTOMATION_TOOL_TEST_DB_USER"]
    password = environment["AUTOMATION_TOOL_TEST_DB_PASSWORD"]
    database = environment["AUTOMATION_TOOL_TEST_DB_NAME"]
    process_environment = environment.copy()
    process_environment["PGPASSWORD"] = password

    with tempfile.TemporaryDirectory(
        prefix="automation-tool-postgres-",
        ignore_cleanup_errors=True,
    ) as directory:
        root = Path(directory)
        data_directory = root / "data"
        password_path = root / "postgres-password"
        server_log = root / "postgres.log"
        password_path.write_text(f"{password}\n", encoding="utf-8")
        try:
            subprocess.run(
                [
                    initdb,
                    "--pgdata",
                    os.fspath(data_directory),
                    "--username",
                    username,
                    "--pwfile",
                    os.fspath(password_path),
                    "--encoding",
                    "UTF8",
                    "--auth-host",
                    "scram-sha-256",
                    "--auth-local",
                    "trust",
                    "--data-checksums",
                    "--no-sync",
                ],
                check=True,
                env=process_environment,
                capture_output=True,
                text=True,
            )
        finally:
            password_path.unlink(missing_ok=True)

        configuration_path = data_directory / "postgresql.conf"
        with configuration_path.open("a", encoding="utf-8", newline="\n") as file:
            file.write("\nlisten_addresses = '127.0.0.1'\n")
            file.write(f"port = {database_port}\n")
            file.write("max_connections = 20\n")

        started = False
        try:
            subprocess.run(
                [
                    pg_ctl,
                    "--pgdata",
                    os.fspath(data_directory),
                    "--log",
                    os.fspath(server_log),
                    "--wait",
                    "start",
                ],
                check=True,
                env=process_environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            started = True
            subprocess.run(
                [
                    createdb,
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(database_port),
                    "--username",
                    username,
                    database,
                ],
                check=True,
                env=process_environment,
                capture_output=True,
                text=True,
            )
            yield
        finally:
            if started:
                subprocess.run(
                    [
                        pg_ctl,
                        "--pgdata",
                        os.fspath(data_directory),
                        "--mode",
                        "fast",
                        "--wait",
                        "stop",
                    ],
                    check=False,
                    env=process_environment,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )


@contextmanager
def managed_test_postgres(
    *,
    compose: list[str],
    database_port: int,
    environment: dict[str, str],
    repository_root: Path,
) -> Iterator[None]:
    """Start one isolated test database and always remove its resources."""

    if platform.system() == "Windows":
        with _native_windows_postgres(
            database_port=database_port,
            environment=environment,
        ):
            yield
        return

    try:
        subprocess.run(
            [*compose, "up", "--detach", "--wait", "postgres-test"],
            check=True,
            cwd=repository_root,
            env=environment,
        )
        yield
    finally:
        subprocess.run(
            [*compose, "down", "--volumes", "--remove-orphans"],
            check=False,
            cwd=repository_root,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
