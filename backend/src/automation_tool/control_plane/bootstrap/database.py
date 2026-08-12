"""Database configuration for the Control Plane process.

The local demo runs on SQLite: a single file under the service's data
directory, no external database process. `AUTOMATION_TOOL_DATABASE_URL` may
still override the location (any `sqlite+aiosqlite://` URL); when absent the
default file is derived from `AUTOMATION_TOOL_DATA_DIR` or the repository's
`.local` directory during development.
"""

import os
from pathlib import Path

from automation_tool.control_plane.bootstrap.runtime_secrets import (
    RuntimeSecretError,
    RuntimeSecretName,
    runtime_secret,
)
from automation_tool.control_plane.infrastructure.database import Database

DATA_DIRECTORY_ENVIRONMENT = "AUTOMATION_TOOL_DATA_DIR"
DATABASE_FILE_NAME = "control-plane.db"


class DatabaseConfigurationError(RuntimeError):
    """Database configuration is absent or invalid without revealing its value."""


def default_database_file() -> Path:
    configured = os.environ.get(DATA_DIRECTORY_ENVIRONMENT)
    if configured:
        directory = Path(configured)
    else:
        repository_root = Path(__file__).resolve().parents[5]
        directory = repository_root / ".local" / "control-plane"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / DATABASE_FILE_NAME


def database_url_from_environment() -> str:
    """Resolve the SQLite URL: explicit override first, derived default second."""

    try:
        value = runtime_secret(RuntimeSecretName.DATABASE_URL, required=False)
    except RuntimeSecretError:
        raise DatabaseConfigurationError("Database configuration is invalid") from None
    if value is None:
        return f"sqlite+aiosqlite:///{default_database_file()}"
    if not value.startswith("sqlite+aiosqlite://"):
        raise DatabaseConfigurationError("Database configuration is invalid")
    return value


def database_from_environment() -> Database:
    """Build the process-owned database from validated environment settings."""

    return Database.from_url(database_url_from_environment())
