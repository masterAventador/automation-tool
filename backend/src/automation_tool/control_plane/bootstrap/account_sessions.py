"""Fail-closed environment wiring for customer account sessions."""

import base64
import binascii
import re
from datetime import UTC, datetime
from typing import Final

from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from automation_tool.control_plane.application.account_sessions import (
    AccountRecoveryFactory,
    AccountSessionFactory,
    AccountSessionService,
)
from automation_tool.control_plane.infrastructure.database import (
    Database,
    SqlAlchemyAccountSessionRepository,
)
from automation_tool.control_plane.infrastructure.security.passwords import (
    Argon2idPasswordHasher,
)

_BASE64URL_PATTERN: Final = re.compile(r"[A-Za-z0-9_-]+")
_DUMMY_PASSWORD: Final = "account timing defense password"


class AccountSessionConfigurationError(RuntimeError):
    """Deployment secrets are partial or malformed without value reflection."""

    def __init__(self) -> None:
        super().__init__("Account session configuration is invalid")


class _AccountSessionSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTOMATION_TOOL_", extra="ignore")

    account_password_pepper: str | None = None
    account_password_pepper_version: int | None = None
    account_fingerprint_key: str | None = None


class _SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


def _secret(value: str) -> bytes:
    if _BASE64URL_PATTERN.fullmatch(value) is None:
        raise AccountSessionConfigurationError
    try:
        decoded = base64.urlsafe_b64decode(value + ("=" * (-len(value) % 4)))
    except (ValueError, binascii.Error):
        raise AccountSessionConfigurationError from None
    canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
    if canonical != value or len(decoded) != 32:
        raise AccountSessionConfigurationError
    return decoded


def account_session_service_from_environment(
    database: Database,
) -> AccountSessionService | None:
    """Enable public account auth only when all three exact secrets are present."""

    try:
        settings = _AccountSessionSettings()
    except ValidationError:
        raise AccountSessionConfigurationError from None
    configured = (
        settings.account_password_pepper,
        settings.account_password_pepper_version,
        settings.account_fingerprint_key,
    )
    if all(value is None for value in configured):
        return None
    if any(value is None for value in configured):
        raise AccountSessionConfigurationError
    version = settings.account_password_pepper_version
    if type(version) is not int or version <= 0:
        raise AccountSessionConfigurationError
    try:
        hasher = Argon2idPasswordHasher(
            pepper=_secret(settings.account_password_pepper or ""),
            pepper_version=version,
        )
        fingerprint_key = _secret(settings.account_fingerprint_key or "")
    except (AccountSessionConfigurationError, RuntimeError):
        raise AccountSessionConfigurationError from None
    return AccountSessionService(
        repository=SqlAlchemyAccountSessionRepository(database),
        password_hasher=hasher,
        clock=_SystemClock(),
        session_factory=AccountSessionFactory(),
        recovery_factory=AccountRecoveryFactory(),
        fingerprint_key=fingerprint_key,
        dummy_password_hash=hasher.hash(_DUMMY_PASSWORD),
    )


__all__ = [
    "AccountSessionConfigurationError",
    "account_session_service_from_environment",
]
