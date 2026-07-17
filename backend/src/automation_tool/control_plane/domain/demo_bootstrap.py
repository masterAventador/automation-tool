"""Fail-closed capability model for controlled Demo installation bootstrap."""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum

MAX_DEMO_BOOTSTRAP_LIFETIME = timedelta(days=7)
_ENVIRONMENT_PATTERN = re.compile(
    r"(?:[a-z0-9]|[a-z0-9][a-z0-9-]{0,62}[a-z0-9])",
)


class InvalidDemoEnvironmentId(ValueError):
    """A Demo environment identifier is not an exact bounded slug."""

    def __init__(self) -> None:
        super().__init__("Invalid demo environment ID")


class InvalidDemoBootstrap(ValueError):
    """A Demo bootstrap grant contains an unsafe validity boundary."""

    def __init__(self) -> None:
        super().__init__("Invalid demo bootstrap grant")


class BootstrapPurpose(StrEnum):
    """The complete purpose set understood by Demo bootstrap credentials."""

    REGISTER_INSTALLATION = "installation.register"


class BootstrapDenialReason(StrEnum):
    """Safe internal reasons for rejecting a bootstrap authorization attempt."""

    PURPOSE_MISMATCH = "purpose_mismatch"
    ENVIRONMENT_MISMATCH = "environment_mismatch"
    NOT_YET_VALID = "not_yet_valid"
    EXPIRED = "expired"


class BootstrapAuthorizationDenied(PermissionError):
    """A bootstrap grant cannot authorize the requested registration."""

    def __init__(self, reason: BootstrapDenialReason) -> None:
        super().__init__("Demo bootstrap authorization denied")
        self.reason = reason


@dataclass(frozen=True, slots=True)
class DemoEnvironmentId:
    """An exact, non-secret deployment environment binding."""

    _value: str

    def __post_init__(self) -> None:
        if not isinstance(self._value, str) or _ENVIRONMENT_PATTERN.fullmatch(self._value) is None:
            raise InvalidDemoEnvironmentId

    @classmethod
    def parse(cls, value: object) -> "DemoEnvironmentId":
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            raise InvalidDemoEnvironmentId
        return cls(value)

    def __str__(self) -> str:
        return self._value


def _normalized_utc(value: object) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise InvalidDemoBootstrap
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class DemoBootstrapGrant:
    """A short-lived capability that can only register one Demo installation."""

    environment_id: DemoEnvironmentId
    not_before: datetime
    expires_at: datetime
    purpose: BootstrapPurpose = field(
        default=BootstrapPurpose.REGISTER_INSTALLATION,
        init=False,
    )

    def __post_init__(self) -> None:
        if not isinstance(self.environment_id, DemoEnvironmentId):
            raise InvalidDemoBootstrap

        normalized_not_before = _normalized_utc(self.not_before)
        normalized_expires_at = _normalized_utc(self.expires_at)
        lifetime = normalized_expires_at - normalized_not_before
        if lifetime <= timedelta(0) or lifetime > MAX_DEMO_BOOTSTRAP_LIFETIME:
            raise InvalidDemoBootstrap

        object.__setattr__(self, "not_before", normalized_not_before)
        object.__setattr__(self, "expires_at", normalized_expires_at)

    def authorize(
        self,
        *,
        purpose: object,
        environment_id: object,
        at: datetime,
    ) -> None:
        """Authorize only the typed registration purpose inside the bound scope."""
        attempted_at = _normalized_utc(at)
        if purpose is not self.purpose:
            raise BootstrapAuthorizationDenied(BootstrapDenialReason.PURPOSE_MISMATCH)
        if (
            not isinstance(environment_id, DemoEnvironmentId)
            or environment_id != self.environment_id
        ):
            raise BootstrapAuthorizationDenied(BootstrapDenialReason.ENVIRONMENT_MISMATCH)
        if attempted_at < self.not_before:
            raise BootstrapAuthorizationDenied(BootstrapDenialReason.NOT_YET_VALID)
        if attempted_at >= self.expires_at:
            raise BootstrapAuthorizationDenied(BootstrapDenialReason.EXPIRED)


__all__ = [
    "MAX_DEMO_BOOTSTRAP_LIFETIME",
    "BootstrapAuthorizationDenied",
    "BootstrapDenialReason",
    "BootstrapPurpose",
    "DemoBootstrapGrant",
    "DemoEnvironmentId",
    "InvalidDemoBootstrap",
    "InvalidDemoEnvironmentId",
]
