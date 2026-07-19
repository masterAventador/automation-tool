import os
import secrets
import socket
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILE = REPOSITORY_ROOT / "compose.yaml"
BACKEND_ROOT = REPOSITORY_ROOT / "backend"

sys.path.insert(0, os.fspath(REPOSITORY_ROOT))
from scripts.acceptance_postgres import (  # type: ignore[import-not-found]  # noqa: E402
    managed_test_postgres,
)


class AlembicRunner(Protocol):
    def __call__(self, database_url: str, *arguments: str) -> None: ...


def unused_loopback_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@pytest.fixture
def alembic_runner() -> AlembicRunner:
    def run(database_url: str, *arguments: str) -> None:
        environment = os.environ.copy()
        environment["AUTOMATION_TOOL_DATABASE_URL"] = database_url
        subprocess.run(
            ["alembic", *arguments],
            check=True,
            capture_output=True,
            cwd=BACKEND_ROOT,
            env=environment,
            text=True,
        )

    return run


@pytest.fixture(scope="session")
def postgresql_url() -> Iterator[str]:
    project_name = f"automation-tool-pytest-{os.getpid()}"
    test_port = unused_loopback_port()
    environment = os.environ.copy()
    environment.update(
        {
            "AUTOMATION_TOOL_DEV_DB_USER": "unused_dev",
            "AUTOMATION_TOOL_DEV_DB_PASSWORD": secrets.token_hex(24),
            "AUTOMATION_TOOL_DEV_DB_NAME": "unused_dev",
            "AUTOMATION_TOOL_DEV_DB_PORT": str(unused_loopback_port()),
            "AUTOMATION_TOOL_TEST_DB_USER": "automation_tool_test",
            "AUTOMATION_TOOL_TEST_DB_PASSWORD": secrets.token_hex(24),
            "AUTOMATION_TOOL_TEST_DB_NAME": "automation_tool_test",
            "AUTOMATION_TOOL_TEST_DB_PORT": str(test_port),
        }
    )
    command = [
        "docker",
        "compose",
        "--project-name",
        project_name,
        "--env-file",
        os.devnull,
        "--file",
        str(COMPOSE_FILE),
    ]

    password = environment["AUTOMATION_TOOL_TEST_DB_PASSWORD"]
    database_url = (
        "postgresql+asyncpg://automation_tool_test:"
        f"{password}@127.0.0.1:{test_port}/automation_tool_test"
    )
    with managed_test_postgres(
        compose=command,
        database_port=test_port,
        environment=environment,
        repository_root=REPOSITORY_ROOT,
    ):
        yield database_url
