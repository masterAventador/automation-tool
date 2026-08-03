"""Cross-platform isolated PostgreSQL lifecycle for acceptance scripts."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from pathlib import Path

WINDOWS_POSTGRES_ROOT_ENVIRONMENT = "AUTOMATION_TOOL_ACCEPTANCE_WINDOWS_POSTGRES_ROOT"
WINDOWS_NATIVE_POSTGRES_TOOLS = ("initdb", "pg_ctl", "createdb")


def _native_windows_postgres_available() -> bool:
    discovered = tuple(shutil.which(name) for name in WINDOWS_NATIVE_POSTGRES_TOOLS)
    if any(discovered) and not all(discovered):
        raise RuntimeError("native Windows PostgreSQL toolchain is partially installed")
    return all(discovered)


def _required_postgres_tool(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        raise RuntimeError(
            f"{name} is required for native Windows PostgreSQL acceptance"
        )
    return executable


def _run_captured_postgres_command(
    command: list[str],
    *,
    environment: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=True,
            env=environment,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as error:
        diagnostic = (
            error.stderr or error.stdout or "no PostgreSQL diagnostic"
        ).strip()
        name = Path(command[0]).stem.lower()
        raise RuntimeError(f"{name} failed: {diagnostic}") from error


@contextmanager
def _windows_postgres_root(
    environment: dict[str, str],
) -> Iterator[Path]:
    parent_owned = environment.get(WINDOWS_POSTGRES_ROOT_ENVIRONMENT)
    if parent_owned is None:
        with tempfile.TemporaryDirectory(
            prefix="automation-tool-postgres-",
            ignore_cleanup_errors=True,
        ) as directory:
            yield Path(directory)
        return
    root = Path(parent_owned)
    if not root.is_absolute():
        raise RuntimeError("Windows PostgreSQL acceptance root must be absolute")
    try:
        # Let Windows inherit the current user's native ACL. Python's special
        # handling of mode=0o700 creates a DACL that the PostgreSQL child tools
        # cannot traverse to read the short-lived pwfile.
        root.mkdir()
    except OSError as error:
        raise RuntimeError(
            "Windows PostgreSQL acceptance root is unavailable"
        ) from error
    yield root


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

    with _windows_postgres_root(environment) as root:
        data_directory = root / "data"
        password_path = root / "postgres-password"
        server_log = root / "postgres.log"
        password_path.write_text(f"{password}\n", encoding="utf-8")
        try:
            _run_captured_postgres_command(
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
                environment=process_environment,
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
def _isolated_windows_docker_environment(
    environment: dict[str, str],
) -> Iterator[dict[str, str]]:
    with tempfile.TemporaryDirectory(
        prefix="automation-tool-docker-config-",
        ignore_cleanup_errors=True,
    ) as directory:
        docker_config = Path(directory)
        (docker_config / "config.json").write_text(
            '{"auths":{"https://index.docker.io/v1/":{}}}\n',
            encoding="utf-8",
        )
        isolated_environment = environment.copy()
        isolated_environment["DOCKER_CONFIG"] = os.fspath(docker_config)
        yield isolated_environment


@contextmanager
def managed_test_postgres(
    *,
    compose: list[str],
    database_port: int,
    environment: dict[str, str],
    repository_root: Path,
) -> Iterator[None]:
    """Start one isolated test database and always remove its resources."""

    system = platform.system()
    if system == "Windows" and _native_windows_postgres_available():
        with _native_windows_postgres(
            database_port=database_port,
            environment=environment,
        ):
            yield
        return

    docker_environment = (
        _isolated_windows_docker_environment(environment)
        if system == "Windows"
        else nullcontext(environment)
    )
    with docker_environment as process_environment:
        try:
            subprocess.run(
                [*compose, "up", "--detach", "--wait", "postgres-test"],
                check=True,
                cwd=repository_root,
                env=process_environment,
            )
            yield
        finally:
            subprocess.run(
                [*compose, "down", "--volumes", "--remove-orphans"],
                check=False,
                cwd=repository_root,
                env=process_environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
