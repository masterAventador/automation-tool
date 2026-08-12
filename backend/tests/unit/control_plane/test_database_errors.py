import pytest
from fastapi.testclient import TestClient

from automation_tool.control_plane import create_app
from automation_tool.control_plane.bootstrap.database import (
    DatabaseConfigurationError,
    database_from_environment,
)
from automation_tool.control_plane.infrastructure.database import Database


def test_missing_database_configuration_falls_back_to_the_default_sqlite_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("AUTOMATION_TOOL_DATABASE_URL", raising=False)
    monkeypatch.setenv("AUTOMATION_TOOL_DATA_DIR", str(tmp_path))

    from automation_tool.control_plane.bootstrap.database import database_url_from_environment

    url = database_url_from_environment()
    assert url.startswith("sqlite+aiosqlite:///")
    assert str(tmp_path) in url


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
        "sqlite+aiosqlite:////nonexistent-automation-tool/private-secret-path/control_plane.db",
    )

    database = database_from_environment()

    assert isinstance(database, Database)
    await database.close()


def test_database_connection_failure_is_a_safe_retryable_health_error() -> None:
    database = Database.from_url(
        "sqlite+aiosqlite:////nonexistent-automation-tool/private-secret-path/control_plane.db",
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
