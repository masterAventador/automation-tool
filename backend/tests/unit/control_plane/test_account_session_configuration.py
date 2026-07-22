import asyncio
import base64

import pytest

from automation_tool.control_plane import create_app
from automation_tool.control_plane.application.account_sessions import AccountSessionService
from automation_tool.control_plane.bootstrap.account_sessions import (
    AccountSessionConfigurationError,
    _SystemClock,
    account_session_service_from_environment,
)
from automation_tool.control_plane.infrastructure.database import Database


def base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def database_without_connection() -> Database:
    return Database.from_url(
        "postgresql+asyncpg://unused:unused@127.0.0.1:1/unused",
        connect_timeout_seconds=0.01,
    )


def clear_account_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "AUTOMATION_TOOL_ACCOUNT_PASSWORD_PEPPER",
        "AUTOMATION_TOOL_ACCOUNT_PASSWORD_PEPPER_VERSION",
        "AUTOMATION_TOOL_ACCOUNT_FINGERPRINT_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


def test_absent_account_secrets_keep_public_login_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_account_environment(monkeypatch)

    assert account_session_service_from_environment(database_without_connection()) is None


def test_exact_secret_set_builds_account_session_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTOMATION_TOOL_ACCOUNT_PASSWORD_PEPPER", base64url(b"p" * 32))
    monkeypatch.setenv("AUTOMATION_TOOL_ACCOUNT_PASSWORD_PEPPER_VERSION", "1")
    monkeypatch.setenv("AUTOMATION_TOOL_ACCOUNT_FINGERPRINT_KEY", base64url(b"f" * 32))

    service = account_session_service_from_environment(database_without_connection())

    assert isinstance(service, AccountSessionService)


@pytest.mark.parametrize(
    ("pepper", "version", "fingerprint"),
    (
        (base64url(b"p" * 32), None, base64url(b"f" * 32)),
        (None, "1", base64url(b"f" * 32)),
        (base64url(b"p" * 32), "1", None),
        ("+", "1", base64url(b"f" * 32)),
        ("A", "1", base64url(b"f" * 32)),
        (base64url(b"p" * 31), "1", base64url(b"f" * 32)),
        (base64url(b"p" * 32), "0", base64url(b"f" * 32)),
        (base64url(b"p" * 32), "not-an-integer", base64url(b"f" * 32)),
        (base64url(b"p" * 32), "1", base64url(b"f" * 33)),
    ),
)
def test_partial_or_invalid_account_configuration_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    pepper: str | None,
    version: str | None,
    fingerprint: str | None,
) -> None:
    clear_account_environment(monkeypatch)
    values = {
        "AUTOMATION_TOOL_ACCOUNT_PASSWORD_PEPPER": pepper,
        "AUTOMATION_TOOL_ACCOUNT_PASSWORD_PEPPER_VERSION": version,
        "AUTOMATION_TOOL_ACCOUNT_FINGERPRINT_KEY": fingerprint,
    }
    for name, value in values.items():
        if value is not None:
            monkeypatch.setenv(name, value)

    with pytest.raises(AccountSessionConfigurationError) as captured:
        account_session_service_from_environment(database_without_connection())

    assert str(captured.value) == "Account session configuration is invalid"
    assert all(
        value not in repr(captured.value)
        for value in values.values()
        if value is not None and len(value) > 8
    )


def test_account_clock_is_timezone_aware() -> None:
    assert _SystemClock().now().utcoffset() is not None


def test_default_app_factory_wires_account_sessions_from_deployment_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_account_environment(monkeypatch)
    monkeypatch.setenv(
        "AUTOMATION_TOOL_DATABASE_URL",
        "postgresql+asyncpg://unused:unused@127.0.0.1:1/unused",
    )
    monkeypatch.setenv("AUTOMATION_TOOL_ACCOUNT_PASSWORD_PEPPER", base64url(b"p" * 32))
    monkeypatch.setenv("AUTOMATION_TOOL_ACCOUNT_PASSWORD_PEPPER_VERSION", "1")
    monkeypatch.setenv("AUTOMATION_TOOL_ACCOUNT_FINGERPRINT_KEY", base64url(b"f" * 32))

    app = create_app()

    assert isinstance(app.state.account_session_service, AccountSessionService)
    assert isinstance(app.state.database, Database)
    asyncio.run(app.state.database.close())
