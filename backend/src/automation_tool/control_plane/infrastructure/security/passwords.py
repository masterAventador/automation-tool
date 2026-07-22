"""Argon2id password hashing with a versioned deployment Pepper."""

import hashlib
import hmac
from typing import Final

from argon2 import PasswordHasher, Type
from argon2.exceptions import VerificationError

from automation_tool.control_plane.domain import (
    MAX_PASSWORD_CHARACTERS,
    MIN_PASSWORD_CHARACTERS,
    InvalidAccountModel,
    PasswordHash,
)

ARGON2_TIME_COST: Final = 3
ARGON2_MEMORY_COST_KIB: Final = 65_536
ARGON2_PARALLELISM: Final = 4
ARGON2_HASH_LENGTH: Final = 32
ARGON2_SALT_LENGTH: Final = 16
_PASSWORD_DOMAIN: Final = b"automation-tool:customer-password:v1\x00"


def _password_bytes(value: object) -> bytes:
    if type(value) is not str or not (
        MIN_PASSWORD_CHARACTERS <= len(value) <= MAX_PASSWORD_CHARACTERS
    ):
        raise InvalidAccountModel
    try:
        return value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise InvalidAccountModel from None


class Argon2idPasswordHasher:
    """Apply a keyed prehash before a fixed RFC 9106 Argon2id profile."""

    def __init__(self, *, pepper: object, pepper_version: object) -> None:
        if (
            type(pepper) is not bytes
            or len(pepper) != 32
            or type(pepper_version) is not int
            or pepper_version <= 0
        ):
            raise RuntimeError("Password hasher configuration is invalid")
        self._pepper = pepper
        self._pepper_version = pepper_version
        self._hasher = PasswordHasher(
            time_cost=ARGON2_TIME_COST,
            memory_cost=ARGON2_MEMORY_COST_KIB,
            parallelism=ARGON2_PARALLELISM,
            hash_len=ARGON2_HASH_LENGTH,
            salt_len=ARGON2_SALT_LENGTH,
            type=Type.ID,
        )

    def _prehash(self, password: object) -> bytes:
        return hmac.digest(
            self._pepper,
            _PASSWORD_DOMAIN + _password_bytes(password),
            hashlib.sha256,
        )

    def hash(self, password: object) -> PasswordHash:
        return PasswordHash(
            encoded=self._hasher.hash(self._prehash(password)),
            pepper_version=self._pepper_version,
        )

    def verify(self, password: object, stored: object) -> bool:
        if not isinstance(stored, PasswordHash) or stored.pepper_version != self._pepper_version:
            return False
        try:
            return bool(self._hasher.verify(stored.encoded, self._prehash(password)))
        except (InvalidAccountModel, VerificationError):
            return False


__all__ = [
    "ARGON2_HASH_LENGTH",
    "ARGON2_MEMORY_COST_KIB",
    "ARGON2_PARALLELISM",
    "ARGON2_SALT_LENGTH",
    "ARGON2_TIME_COST",
    "Argon2idPasswordHasher",
]
