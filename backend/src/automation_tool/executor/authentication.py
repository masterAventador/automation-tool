"""Domain-separated proof for one one-shot Tauri-to-Executor launch secret."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re

from pydantic import SecretStr

from automation_tool.protocol import EXECUTOR_PROTOCOL_VERSION

_LOCAL_SESSION_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_AUTHENTICATION_DOMAIN = b"automation-tool.local-executor-event.v1\0"
_PROOF_PREFIX = "atlep1."
_ALLOWED_EVENTS = frozenset(("executor.healthy", "executor.stopped"))


class LocalSessionAuthenticationRejected(ValueError):
    """The local launch secret or event cannot establish the process boundary."""

    def __init__(self) -> None:
        super().__init__("Local Executor authentication is rejected")


def require_local_session_token(value: object) -> str:
    """Accept only the canonical lowercase encoding of exactly 256 random bits."""

    if type(value) is not str or _LOCAL_SESSION_PATTERN.fullmatch(value) is None:
        raise LocalSessionAuthenticationRejected
    return value


class LocalSessionAuthenticator:
    """Produce non-reflective event proofs without exposing the launch secret."""

    __slots__ = ("_key",)

    def __init__(self, token: SecretStr) -> None:
        if not isinstance(token, SecretStr):
            raise LocalSessionAuthenticationRejected
        encoded = require_local_session_token(token.get_secret_value())
        self._key = bytearray.fromhex(encoded)

    def __repr__(self) -> str:
        return "LocalSessionAuthenticator([REDACTED])"

    def proof_for(self, event: str) -> str:
        if type(event) is not str or event not in _ALLOWED_EVENTS or len(self._key) != 32:
            raise LocalSessionAuthenticationRejected
        message = (
            _AUTHENTICATION_DOMAIN
            + event.encode("ascii")
            + b"\0"
            + EXECUTOR_PROTOCOL_VERSION.encode("ascii")
        )
        digest = hmac.digest(self._key, message, hashlib.sha256)
        encoded = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        return _PROOF_PREFIX + encoded

    def close(self) -> None:
        for index in range(len(self._key)):
            self._key[index] = 0
        self._key.clear()


__all__ = [
    "LocalSessionAuthenticationRejected",
    "LocalSessionAuthenticator",
    "require_local_session_token",
]
