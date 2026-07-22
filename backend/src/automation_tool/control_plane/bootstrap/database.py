"""Fail-closed database configuration for the Control Plane process."""

from automation_tool.control_plane.bootstrap.runtime_secrets import (
    RuntimeSecretError,
    RuntimeSecretName,
    runtime_secret,
)
from automation_tool.control_plane.infrastructure.database import Database


class DatabaseConfigurationError(RuntimeError):
    """Database configuration is absent or invalid without revealing its value."""


def database_url_from_environment() -> str:
    """Read a validated async PostgreSQL URL from the selected secret delivery."""

    try:
        value = runtime_secret(RuntimeSecretName.DATABASE_URL, required=True)
    except RuntimeSecretError:
        raise DatabaseConfigurationError("Database configuration is invalid") from None
    if value is None or not value.startswith("postgresql+asyncpg://"):
        raise DatabaseConfigurationError("Database configuration is invalid")
    return value


def database_from_environment() -> Database:
    """Build the process-owned database from validated environment settings."""

    return Database.from_url(database_url_from_environment())
