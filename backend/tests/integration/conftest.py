import os
import secrets
import socket
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILE = REPOSITORY_ROOT / "compose.yaml"
BACKEND_ROOT = REPOSITORY_ROOT / "backend"


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

    try:
        subprocess.run(
            [*command, "up", "--detach", "--wait", "postgres-test"],
            check=True,
            capture_output=True,
            env=environment,
            text=True,
        )
        password = environment["AUTOMATION_TOOL_TEST_DB_PASSWORD"]
        yield (
            "postgresql+asyncpg://automation_tool_test:"
            f"{password}@127.0.0.1:{test_port}/automation_tool_test"
        )
    finally:
        subprocess.run(
            [*command, "down", "--volumes", "--remove-orphans"],
            check=False,
            capture_output=True,
            env=environment,
            text=True,
        )
