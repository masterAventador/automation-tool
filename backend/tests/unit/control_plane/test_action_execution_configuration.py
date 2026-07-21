from __future__ import annotations

import asyncio
import base64

import pytest

from automation_tool.control_plane import create_app
from automation_tool.control_plane.application.action_execution_orchestration import (
    ActionExecutionOrchestrationService,
)
from automation_tool.control_plane.bootstrap.action_execution import (
    ActionExecutionConfigurationError,
    action_execution_runtime_from_environment,
)
from automation_tool.control_plane.infrastructure.database import Database
from automation_tool.control_plane.infrastructure.security import (
    Ed25519ActionAuthorizationIssuer,
)

ENVIRONMENT_NAMES = (
    "AUTOMATION_TOOL_ACTION_AUTHORIZATION_PRIVATE_KEY",
    "AUTOMATION_TOOL_ACTION_MINIMUM_INTERVAL_SECONDS",
    "AUTOMATION_TOOL_ACTION_TASK_LIMIT",
    "AUTOMATION_TOOL_ACTION_DAILY_LIMIT",
    "AUTOMATION_TOOL_ACTION_CONSECUTIVE_FAILURE_THRESHOLD",
)


def database_without_connection() -> Database:
    return Database.from_url(
        "postgresql+asyncpg://unused:unused@127.0.0.1:1/unused",
        connect_timeout_seconds=0.01,
    )


def base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def clear(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENVIRONMENT_NAMES:
        monkeypatch.delenv(name, raising=False)


def configure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "AUTOMATION_TOOL_ACTION_AUTHORIZATION_PRIVATE_KEY",
        base64url(b"h816c-private-key-material-00001"),
    )
    monkeypatch.setenv("AUTOMATION_TOOL_ACTION_MINIMUM_INTERVAL_SECONDS", "5")
    monkeypatch.setenv("AUTOMATION_TOOL_ACTION_TASK_LIMIT", "20")
    monkeypatch.setenv("AUTOMATION_TOOL_ACTION_DAILY_LIMIT", "100")
    monkeypatch.setenv("AUTOMATION_TOOL_ACTION_CONSECUTIVE_FAILURE_THRESHOLD", "3")


def test_absent_action_execution_configuration_keeps_runtime_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear(monkeypatch)
    database = database_without_connection()
    try:
        assert action_execution_runtime_from_environment(database) is None
    finally:
        asyncio.run(database.close())


def test_complete_server_policy_builds_orchestrator_and_signer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear(monkeypatch)
    configure(monkeypatch)
    database = database_without_connection()
    try:
        runtime = action_execution_runtime_from_environment(database)
        assert runtime is not None
        assert isinstance(runtime.service, ActionExecutionOrchestrationService)
        assert isinstance(runtime.issuer, Ed25519ActionAuthorizationIssuer)
        assert "private" not in repr(runtime)
    finally:
        asyncio.run(database.close())


@pytest.mark.parametrize(
    ("name", "value"),
    (
        ("AUTOMATION_TOOL_ACTION_TASK_LIMIT", None),
        ("AUTOMATION_TOOL_ACTION_MINIMUM_INTERVAL_SECONDS", "0"),
        ("AUTOMATION_TOOL_ACTION_DAILY_LIMIT", "invalid"),
        ("AUTOMATION_TOOL_ACTION_AUTHORIZATION_PRIVATE_KEY", "+"),
        ("AUTOMATION_TOOL_ACTION_AUTHORIZATION_PRIVATE_KEY", "A"),
        ("AUTOMATION_TOOL_ACTION_AUTHORIZATION_PRIVATE_KEY", base64url(bytes(32))),
    ),
)
def test_partial_or_invalid_action_policy_fails_closed_without_reflection(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str | None,
) -> None:
    clear(monkeypatch)
    configure(monkeypatch)
    if value is None:
        monkeypatch.delenv(name)
    else:
        monkeypatch.setenv(name, value)
    database = database_without_connection()
    try:
        with pytest.raises(ActionExecutionConfigurationError) as captured:
            action_execution_runtime_from_environment(database)
        assert str(captured.value) == "Action execution configuration is invalid"
        if value is not None and "PRIVATE_KEY" in name and len(value) > 16:
            assert value not in repr(captured.value)
    finally:
        asyncio.run(database.close())


def test_default_app_factory_wires_complete_action_execution_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear(monkeypatch)
    configure(monkeypatch)
    monkeypatch.setenv(
        "AUTOMATION_TOOL_DATABASE_URL",
        "postgresql+asyncpg://unused:unused@127.0.0.1:1/unused",
    )
    monkeypatch.delenv("AUTOMATION_TOOL_DEMO_ENVIRONMENT_ID", raising=False)
    monkeypatch.delenv("AUTOMATION_TOOL_DEMO_BOOTSTRAP_PUBLIC_KEY", raising=False)

    app = create_app()

    assert isinstance(
        app.state.action_execution_orchestration_service,
        ActionExecutionOrchestrationService,
    )
    assert isinstance(app.state.database, Database)
    asyncio.run(app.state.database.close())
