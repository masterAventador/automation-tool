import pytest
from fastapi.testclient import TestClient

from automation_tool.control_plane import create_app
from automation_tool.control_plane.bootstrap.database import (
    DatabaseConfigurationError,
    database_from_environment,
)
from automation_tool.control_plane.infrastructure.database import Database


def test_missing_database_configuration_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTOMATION_TOOL_DATABASE_URL", raising=False)

    with pytest.raises(DatabaseConfigurationError) as captured:
        create_app()

    assert str(captured.value) == "Database configuration is invalid"
    assert captured.value.__cause__ is None


def test_invalid_database_url_does_not_reflect_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "AUTOMATION_TOOL_DATABASE_URL",
        "mysql://operator:private-password@database.invalid/control_plane",
    )

    with pytest.raises(DatabaseConfigurationError) as captured:
        database_from_environment()

    assert "private-password" not in str(captured.value)
    assert "private-password" not in repr(captured.value)
    assert captured.value.__cause__ is None


@pytest.mark.asyncio
async def test_valid_database_configuration_builds_a_disposable_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "AUTOMATION_TOOL_DATABASE_URL",
        "postgresql+asyncpg://operator:private-password@127.0.0.1:1/control_plane",
    )

    database = database_from_environment()

    assert isinstance(database, Database)
    await database.close()


def test_database_connection_failure_is_a_safe_retryable_health_error() -> None:
    database = Database.from_url(
        "postgresql+asyncpg://operator:private-password@127.0.0.1:1/control_plane",
        connect_timeout_seconds=0.1,
    )

    with TestClient(create_app(database=database)) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 503
    assert response.json()["error"] == {
        "code": "dependency_unavailable",
        "message": "Database is unavailable",
        "requestId": response.headers["x-request-id"],
        "retryable": True,
    }
    assert "private-password" not in response.text
