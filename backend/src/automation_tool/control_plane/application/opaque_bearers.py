"""Shared canonical opaque bearer primitives for device credentials and sessions."""

import base64
import binascii
import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final
from uuid import UUID

_SECRET_LENGTH: Final = 32
_MAX_BEARER_LENGTH: Final = 256
_PREFIX_PATTERN: Final = re.compile(r"[a-z][a-z0-9]{1,15}")
_BASE64URL_PATTERN: Final = re.compile(r"[A-Za-z0-9_-]+")


class InvalidOpaqueBearer(ValueError):
    """An opaque bearer is not canonical or safely bounded."""


@dataclass(frozen=True, slots=True)
class OpaqueBearerMaterial:
    bearer_id: UUID
    bearer: str
    secret_digest: bytes


@dataclass(frozen=True, slots=True)
class ParsedOpaqueBearer:
    bearer_id: UUID
    secret_digest: bytes


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


class OpaqueBearerCodec:
    """Encode and parse one versioned bearer prefix without retaining its secret."""

    def __init__(self, prefix: str) -> None:
        if _PREFIX_PATTERN.fullmatch(prefix) is None:
            raise ValueError("Opaque bearer prefix is invalid")
        self._prefix = prefix

    def parse(self, value: object) -> ParsedOpaqueBearer:
        if not isinstance(value, str) or not value or len(value) > _MAX_BEARER_LENGTH:
            raise InvalidOpaqueBearer
        try:
            prefix, identifier, encoded_secret = value.split(".")
        except ValueError:
            raise InvalidOpaqueBearer from None
        if prefix != self._prefix or _BASE64URL_PATTERN.fullmatch(encoded_secret) is None:
            raise InvalidOpaqueBearer
        try:
            bearer_id = UUID(identifier)
            secret = base64.urlsafe_b64decode(encoded_secret + ("=" * (-len(encoded_secret) % 4)))
        except (ValueError, binascii.Error):
            raise InvalidOpaqueBearer from None
        if (
            bearer_id.version != 4
            or str(bearer_id) != identifier
            or len(secret) != _SECRET_LENGTH
            or _base64url(secret) != encoded_secret
        ):
            raise InvalidOpaqueBearer
        return ParsedOpaqueBearer(
            bearer_id=bearer_id,
            secret_digest=hashlib.sha256(secret).digest(),
        )

    def create(
        self,
        *,
        secret_source: Callable[[int], bytes],
        id_source: Callable[[], UUID],
    ) -> OpaqueBearerMaterial:
        secret = secret_source(_SECRET_LENGTH)
        bearer_id = id_source()
        if (
            not isinstance(secret, bytes)
            or len(secret) != _SECRET_LENGTH
            or not isinstance(bearer_id, UUID)
            or bearer_id.version != 4
        ):
            raise RuntimeError("Opaque bearer generation failed")
        return OpaqueBearerMaterial(
            bearer_id=bearer_id,
            bearer=f"{self._prefix}.{bearer_id}.{_base64url(secret)}",
            secret_digest=hashlib.sha256(secret).digest(),
        )


__all__ = [
    "InvalidOpaqueBearer",
    "OpaqueBearerCodec",
    "OpaqueBearerMaterial",
    "ParsedOpaqueBearer",
]
