"""Environment wiring for the independently deployed registration API."""

import base64
import binascii
import re
import secrets
from datetime import UTC, datetime
from typing import Final
from uuid import uuid4

from pydantic_settings import BaseSettings, SettingsConfigDict

from automation_tool.control_plane.application.device_credentials import DeviceCredentialFactory
from automation_tool.control_plane.application.registration import (
    InstallationRegistrationService,
)
from automation_tool.control_plane.bootstrap.local_provisioning import (
    LocalRegistrationBootstrap,
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
    *,
    provisioned: LocalRegistrationBootstrap | None = None,
) -> InstallationRegistrationService | None:
    """Build registration from exactly one source of bootstrap trust.

    A deployment configures the environment pair; a loopback start hands in the
    public half of the grant it just issued. Supplying both would leave two
    keys able to register against one service, so it fails closed instead.
    """
    settings = _RegistrationSettings()
    configured = (settings.demo_environment_id, settings.demo_bootstrap_public_key)
    if provisioned is not None:
        if any(value is not None for value in configured):
            raise RegistrationConfigurationError
        configured = (provisioned.environment_id, provisioned.public_key)
    if configured[0] is None and configured[1] is None:
        return None
    if configured[0] is None or configured[1] is None:
        raise RegistrationConfigurationError
    try:
        environment_id = DemoEnvironmentId.parse(configured[0])
        verifier = Ed25519BootstrapTokenVerifier(_public_key(configured[1]))
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
        credential_factory=DeviceCredentialFactory(
            secret_source=secrets.token_bytes,
            id_source=uuid4,
        ),
    )


__all__ = ["RegistrationConfigurationError", "registration_service_from_environment"]
