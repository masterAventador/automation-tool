"""Environment wiring for the independently deployed registration API."""

import base64
import binascii
import re
import secrets
from datetime import UTC, datetime
from typing import Final

from pydantic_settings import BaseSettings, SettingsConfigDict

from automation_tool.control_plane.application.registration import (
    InstallationRegistrationService,
)
from automation_tool.control_plane.domain import DemoEnvironmentId, InvalidDemoEnvironmentId
from automation_tool.control_plane.infrastructure.database import Database
from automation_tool.control_plane.infrastructure.database.registration import (
    SqlAlchemyInstallationRegistrationRepository,
)
from automation_tool.control_plane.infrastructure.security.bootstrap_tokens import (
    BootstrapCredentialRejected,
    Ed25519BootstrapTokenVerifier,
)

_BASE64URL_PATTERN: Final = re.compile(r"[A-Za-z0-9_-]+")


class RegistrationConfigurationError(RuntimeError):
    """Registration is partially or unsafely configured without value reflection."""

    def __init__(self) -> None:
        super().__init__("Installation registration configuration is invalid")


class _RegistrationSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTOMATION_TOOL_", extra="ignore")

    demo_environment_id: str | None = None
    demo_bootstrap_public_key: str | None = None


class _SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


def _public_key(value: str) -> bytes:
    if _BASE64URL_PATTERN.fullmatch(value) is None:
        raise RegistrationConfigurationError
    try:
        decoded = base64.urlsafe_b64decode(value + ("=" * (-len(value) % 4)))
    except (ValueError, binascii.Error):
        raise RegistrationConfigurationError from None
    canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
    if canonical != value or len(decoded) != 32:
        raise RegistrationConfigurationError
    return decoded


def registration_service_from_environment(
    database: Database,
) -> InstallationRegistrationService | None:
    """Build registration only when both exact public settings are present."""
    settings = _RegistrationSettings()
    if settings.demo_environment_id is None and settings.demo_bootstrap_public_key is None:
        return None
    if settings.demo_environment_id is None or settings.demo_bootstrap_public_key is None:
        raise RegistrationConfigurationError
    try:
        environment_id = DemoEnvironmentId.parse(settings.demo_environment_id)
        verifier = Ed25519BootstrapTokenVerifier(_public_key(settings.demo_bootstrap_public_key))
    except (
        BootstrapCredentialRejected,
        InvalidDemoEnvironmentId,
        RegistrationConfigurationError,
    ):
        raise RegistrationConfigurationError from None
    return InstallationRegistrationService(
        repository=SqlAlchemyInstallationRegistrationRepository(database),
        bootstrap_verifier=verifier,
        expected_environment_id=environment_id,
        clock=_SystemClock(),
        nonce_source=secrets.token_bytes,
    )


__all__ = ["RegistrationConfigurationError", "registration_service_from_environment"]
