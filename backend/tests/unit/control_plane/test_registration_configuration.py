import asyncio
import base64
from datetime import UTC

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from automation_tool.control_plane import create_app
from automation_tool.control_plane.application.registration import InstallationRegistrationService
from automation_tool.control_plane.bootstrap.registration import (
    RegistrationConfigurationError,
    _SystemClock,
    registration_service_from_environment,
)
from automation_tool.control_plane.infrastructure.database import Database


def base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def database_without_connection() -> Database:
    return Database.from_url(
        "postgresql+asyncpg://unused:unused@127.0.0.1:1/unused",
        connect_timeout_seconds=0.01,
    )


def test_absent_bootstrap_configuration_keeps_registration_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AUTOMATION_TOOL_DEMO_ENVIRONMENT_ID", raising=False)
    monkeypatch.delenv("AUTOMATION_TOOL_DEMO_BOOTSTRAP_PUBLIC_KEY", raising=False)

    assert registration_service_from_environment(database_without_connection()) is None


def test_exact_environment_and_raw_public_key_enable_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    public_key = Ed25519PrivateKey.generate().public_key().public_bytes_raw()
    monkeypatch.setenv("AUTOMATION_TOOL_DEMO_ENVIRONMENT_ID", "demo-cn-1")
    monkeypatch.setenv("AUTOMATION_TOOL_DEMO_BOOTSTRAP_PUBLIC_KEY", base64url(public_key))

    service = registration_service_from_environment(database_without_connection())

    assert isinstance(service, InstallationRegistrationService)


@pytest.mark.parametrize(
    ("environment_id", "public_key"),
    (
        ("demo-cn-1", None),
        (None, "x" * 43),
        ("DEMO-CN-1", "x" * 43),
        ("demo-cn-1", "+"),
        ("demo-cn-1", "A"),
        ("demo-cn-1", "private-invalid-public-key"),
        ("demo-cn-1", base64url(b"x" * 31)),
        ("demo-cn-1", base64url(b"x" * 33)),
    ),
)
def test_partial_or_invalid_configuration_fails_closed_without_reflection(
    monkeypatch: pytest.MonkeyPatch,
    environment_id: str | None,
    public_key: str | None,
) -> None:
    if environment_id is None:
        monkeypatch.delenv("AUTOMATION_TOOL_DEMO_ENVIRONMENT_ID", raising=False)
    else:
        monkeypatch.setenv("AUTOMATION_TOOL_DEMO_ENVIRONMENT_ID", environment_id)
    if public_key is None:
        monkeypatch.delenv("AUTOMATION_TOOL_DEMO_BOOTSTRAP_PUBLIC_KEY", raising=False)
    else:
        monkeypatch.setenv("AUTOMATION_TOOL_DEMO_BOOTSTRAP_PUBLIC_KEY", public_key)

    with pytest.raises(RegistrationConfigurationError) as captured:
        registration_service_from_environment(database_without_connection())

    assert str(captured.value) == "Installation registration configuration is invalid"
    if public_key is not None:
        assert public_key not in repr(captured.value)


def test_system_clock_is_timezone_aware() -> None:
    assert _SystemClock().now().tzinfo == UTC


def test_default_app_factory_wires_valid_deployment_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    public_key = Ed25519PrivateKey.generate().public_key().public_bytes_raw()
    monkeypatch.setenv(
        "AUTOMATION_TOOL_DATABASE_URL",
        "postgresql+asyncpg://unused:unused@127.0.0.1:1/unused",
    )
    monkeypatch.setenv("AUTOMATION_TOOL_DEMO_ENVIRONMENT_ID", "demo-cn-1")
    monkeypatch.setenv("AUTOMATION_TOOL_DEMO_BOOTSTRAP_PUBLIC_KEY", base64url(public_key))

    app = create_app()

    assert isinstance(app.state.registration_service, InstallationRegistrationService)
    assert isinstance(app.state.database, Database)
    asyncio.run(app.state.database.close())
