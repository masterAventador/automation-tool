"""Fail-closed database configuration for the Control Plane process."""

from pydantic import SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from automation_tool.control_plane.infrastructure.database import Database


class DatabaseConfigurationError(RuntimeError):
    """Database configuration is absent or invalid without revealing its value."""


class _DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTOMATION_TOOL_", extra="ignore")

    database_url: SecretStr

    @field_validator("database_url")
    @classmethod
    def require_async_postgresql(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().startswith("postgresql+asyncpg://"):
            raise ValueError("async PostgreSQL URL required")
        return value


def database_url_from_environment() -> str:
    """Read a validated async PostgreSQL URL without reflecting rejected input."""

    try:
        # BaseSettings supplies this required field from the process environment.
        settings = _DatabaseSettings()  # type: ignore[call-arg]
        return settings.database_url.get_secret_value()
    except ValidationError:
        raise DatabaseConfigurationError("Database configuration is invalid") from None


def database_from_environment() -> Database:
    """Build the process-owned database from validated environment settings."""

    return Database.from_url(database_url_from_environment())
