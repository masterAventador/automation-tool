"""Closed customer account values frozen by the U9-01 threat model."""

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Self

MIN_PASSWORD_CHARACTERS: Final = 12
MAX_PASSWORD_CHARACTERS: Final = 128
_LOGIN_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{2,63}$", re.ASCII)
_PASSWORD_HASH = re.compile(
    r"^\$argon2id\$v=19\$m=65536,t=3,p=4\$[A-Za-z0-9+/]+\$[A-Za-z0-9+/]+$",
    re.ASCII,
)


class InvalidAccountModel(ValueError):
    """An account value failed its closed validation contract."""

    def __init__(self) -> None:
        super().__init__("Account model is invalid")


class AccountStatus(StrEnum):
    """Persisted customer account lifecycle."""

    ACTIVE = "active"
    LOCKED = "locked"
    DISABLED = "disabled"


class AccountAuditActorKind(StrEnum):
    """Closed actor classes allowed in account audit facts."""

    OPERATIONS = "operations"
    USER = "user"
    SYSTEM = "system"


class AccountAuditEventType(StrEnum):
    """Events frozen by account.threat-model.v1."""

    ACCOUNT_CREATED = "account.created"
    ACCOUNT_LOCKED = "account.locked"
    ACCOUNT_UNLOCKED = "account.unlocked"
    ACCOUNT_DISABLED = "account.disabled"
    ACCOUNT_ENABLED = "account.enabled"
    LOGIN_SUCCEEDED = "login.succeeded"
    LOGIN_FAILED = "login.failed"
    CREDENTIAL_CHANGED = "credential.changed"
    RECOVERY_ISSUED = "recovery.issued"
    RECOVERY_CONSUMED = "recovery.consumed"
    SESSION_REFRESHED = "session.refreshed"
    SESSION_LOGGED_OUT = "session.logged_out"
    SESSION_REUSE_DETECTED = "session.reuse_detected"
    SESSION_ALL_REVOKED = "session.all_revoked"
    DEVICE_BOUND = "device.bound"
    DEVICE_REVOKED = "device.revoked"


@dataclass(frozen=True, slots=True, repr=False)
class LoginName:
    """An immutable ASCII login name with one lowercase storage form."""

    value: str

    def __post_init__(self) -> None:
        if type(self.value) is not str or _LOGIN_NAME.fullmatch(self.value) is None:
            raise InvalidAccountModel
        object.__setattr__(self, "value", self.value.lower())

    @classmethod
    def parse(cls, value: object) -> Self:
        if isinstance(value, cls):
            return value
        if type(value) is not str:
            raise InvalidAccountModel
        return cls(value)

    def __str__(self) -> str:
        return self.value

    def __repr__(self) -> str:
        return "LoginName(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class PasswordHash:
    """A bounded Argon2id PHC string plus its out-of-database Pepper version."""

    encoded: str
    pepper_version: int
    version: int = 1

    def __post_init__(self) -> None:
        if (
            type(self.encoded) is not str
            or len(self.encoded) > 255
            or _PASSWORD_HASH.fullmatch(self.encoded) is None
            or type(self.pepper_version) is not int
            or self.pepper_version <= 0
            or type(self.version) is not int
            or self.version <= 0
        ):
            raise InvalidAccountModel

    def __repr__(self) -> str:
        return "PasswordHash(<redacted>)"


__all__ = [
    "MAX_PASSWORD_CHARACTERS",
    "MIN_PASSWORD_CHARACTERS",
    "AccountAuditActorKind",
    "AccountAuditEventType",
    "AccountStatus",
    "InvalidAccountModel",
    "LoginName",
    "PasswordHash",
]
