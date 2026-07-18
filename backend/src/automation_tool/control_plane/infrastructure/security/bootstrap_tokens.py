"""Verification-only format for offline-signed Demo bootstrap claims."""

import base64
import binascii
import hashlib
import json
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Final

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from automation_tool.control_plane.application.registration import (
    BootstrapCredentialRejected,
    VerifiedBootstrapCredential,
)
from automation_tool.control_plane.domain import (
    BootstrapPurpose,
    DemoBootstrapGrant,
    DemoEnvironmentId,
    InvalidDemoBootstrap,
    InvalidDemoEnvironmentId,
)

_TOKEN_PREFIX: Final = "atb1"
_MAX_TOKEN_LENGTH: Final = 4096
_PUBLIC_KEY_LENGTH: Final = 32
_SIGNATURE_LENGTH: Final = 64
_BASE64URL_PATTERN: Final = re.compile(r"[A-Za-z0-9_-]+")
_CLAIM_NAMES: Final = frozenset({"environmentId", "expiresAt", "notBefore", "purpose", "version"})


class _InvalidToken(ValueError):
    pass


def _base64url_decode(segment: str) -> bytes:
    if _BASE64URL_PATTERN.fullmatch(segment) is None:
        raise _InvalidToken
    try:
        decoded = base64.urlsafe_b64decode(segment + ("=" * (-len(segment) % 4)))
    except (ValueError, binascii.Error):
        raise _InvalidToken from None
    canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
    if canonical != segment:
        raise _InvalidToken
    return decoded


def _unique_object(pairs: Iterable[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _InvalidToken
        result[key] = value
    return result


def _timestamp(value: object) -> datetime:
    if type(value) is not int:
        raise _InvalidToken
    try:
        return datetime.fromtimestamp(value, UTC)
    except (OverflowError, OSError, ValueError):
        raise _InvalidToken from None


def _parse_grant(payload: bytes) -> DemoBootstrapGrant:
    try:
        claims = json.loads(payload, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, _InvalidToken):
        raise _InvalidToken from None
    if not isinstance(claims, dict) or set(claims) != _CLAIM_NAMES:
        raise _InvalidToken
    if type(claims["version"]) is not int or claims["version"] != 1:
        raise _InvalidToken
    if claims["purpose"] != BootstrapPurpose.REGISTER_INSTALLATION.value:
        raise _InvalidToken
    try:
        environment_id = DemoEnvironmentId.parse(claims["environmentId"])
        return DemoBootstrapGrant(
            environment_id=environment_id,
            not_before=_timestamp(claims["notBefore"]),
            expires_at=_timestamp(claims["expiresAt"]),
        )
    except (InvalidDemoBootstrap, InvalidDemoEnvironmentId, _InvalidToken):
        raise _InvalidToken from None


class Ed25519BootstrapTokenVerifier:
    """Verify exact `atb1` claims with one configured offline public key."""

    def __init__(self, public_key: bytes) -> None:
        if not isinstance(public_key, bytes) or len(public_key) != _PUBLIC_KEY_LENGTH:
            raise BootstrapCredentialRejected
        self._public_key = Ed25519PublicKey.from_public_bytes(public_key)

    def verify(self, token: str) -> VerifiedBootstrapCredential:
        try:
            if not isinstance(token, str) or not 0 < len(token) <= _MAX_TOKEN_LENGTH:
                raise _InvalidToken
            token_bytes = token.encode("ascii")
            prefix, payload_segment, signature_segment = token.split(".")
            if prefix != _TOKEN_PREFIX:
                raise _InvalidToken
            payload = _base64url_decode(payload_segment)
            signature = _base64url_decode(signature_segment)
            if len(signature) != _SIGNATURE_LENGTH:
                raise _InvalidToken
            self._public_key.verify(signature, f"{prefix}.{payload_segment}".encode("ascii"))
            grant = _parse_grant(payload)
            return VerifiedBootstrapCredential(
                grant=grant,
                fingerprint=hashlib.sha256(token_bytes).digest(),
            )
        except (
            InvalidSignature,
            UnicodeEncodeError,
            ValueError,
        ):
            raise BootstrapCredentialRejected from None


__all__ = [
    "BootstrapCredentialRejected",
    "Ed25519BootstrapTokenVerifier",
    "VerifiedBootstrapCredential",
]
