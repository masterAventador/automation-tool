import os
import secrets
import signal
import socket
import subprocess
import sys
from collections.abc import Iterator
from contextlib import suppress
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


def assert_private_profile_directory(path: Path) -> None:
    """Assert a browser profile directory is private to the current user.

    POSIX carries the intent directly in the mode bits. Windows has no POSIX
    mode (``mkdir(mode=...)`` is ignored) and a pytest temporary directory
    inherits the ``%TEMP%`` ACL, which a test cannot tighten by itself, so the
    mode-bit assertion is meaningless there. Production profile privacy on
    Windows is enforced by the protected DACL in ``browser_profiles_windows.rs``
    (current-user SID only, ``SE_DACL_PROTECTED``) and is covered by the EB-09
    Rust tests, not by this fixture.
    """
    assert path.is_dir(), path
    if os.name == "nt":
        return
    assert os.stat(path).st_mode & 0o777 == 0o700


def create_private_profile_directory(path: Path) -> Path:
    """Create a profile directory as privately as the platform allows."""
    path.mkdir(mode=0o700)
    return path


def process_ids_matching(needle: str) -> set[int]:
    """Live process ids whose command line contains ``needle``, minus this one.

    POSIX has ``pgrep -f``. Windows has neither ``pgrep`` nor a command-line
    filter in ``tasklist``, so it queries the CIM process table instead. The
    needle travels through the environment rather than the command line, so no
    PowerShell quoting rule can alter a path containing spaces or backslashes.
    """
    if os.name == "nt":
        script = (
            "$needle = $env:AUTOMATION_TOOL_PROCESS_NEEDLE; "
            "Get-CimInstance Win32_Process | "
            "Where-Object { $_.CommandLine -and $_.CommandLine.Contains($needle) } | "
            "ForEach-Object { $_.ProcessId }"
        )
        command = [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ]
        environment = {**os.environ, "AUTOMATION_TOOL_PROCESS_NEEDLE": needle}
    else:
        command = ["pgrep", "-f", needle]
        environment = None
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        env=environment,
        timeout=60,
    )
    return {
        int(line)
        for line in completed.stdout.split()
        if line.strip().isdigit() and int(line) != os.getpid()
    }


def terminate_process(pid: int) -> None:
    """Kill one process outright, ignoring one that is already gone.

    ``SIGKILL`` does not exist on Windows; ``taskkill /F`` is its equivalent.
    """
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/PID", str(pid)],
            capture_output=True,
            check=False,
            timeout=60,
        )
        return
    with suppress(ProcessLookupError):
        os.kill(pid, signal.SIGKILL)


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


sys.path.insert(0, os.fspath(REPOSITORY_ROOT / "scripts"))
from build_embedded_chromium_staging import (  # noqa: E402
    build_staging,
    load_staging_contract,
)
from run_bm_04_acceptance import current_target_id  # noqa: E402
from run_bm_08_acceptance import CHROMIUM_CONTRACT, DEFAULT_ARCHIVES  # noqa: E402


@pytest.fixture(scope="session")
def staged_embedded_chromium(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The digest-verified staged embedded Chromium from the EB-03 cache.

    Tests depending on this fixture never fall back to a system browser; a
    missing archive cache skips instead of downloading anything.
    """
    target_id = current_target_id()
    archive = DEFAULT_ARCHIVES.get(target_id)
    if archive is None or not archive.is_file():
        pytest.skip("embedded Chromium archive cache is unavailable")
    contract = load_staging_contract(CHROMIUM_CONTRACT)
    target = contract.targets[target_id]
    if not target.buildable:
        pytest.skip(f"embedded Chromium target is not buildable: {target_id}")
    result = build_staging(
        contract=contract,
        target_id=target_id,
        archive_path=archive.resolve(strict=True),
        archive_sha256=target.archive_sha256,
        output=tmp_path_factory.mktemp("automation-tool-eb11-chromium") / "staging",
    )
    return (result.output / Path(*target.executable.split("/"))).resolve(strict=True)
