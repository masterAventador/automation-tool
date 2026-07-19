"""Domain-separated proof for one one-shot Tauri-to-Executor launch secret."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
from uuid import RFC_4122, UUID

from pydantic import SecretStr

from automation_tool.protocol import EXECUTOR_PROTOCOL_VERSION

_LOCAL_SESSION_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_AUTHENTICATION_DOMAIN = b"automation-tool.local-executor-event.v1\0"
_PROOF_PREFIX = "atlep1."
_ALLOWED_EVENTS = frozenset(("executor.healthy", "executor.stopped"))
_COMMAND_AUTHENTICATION_DOMAIN = b"automation-tool.local-executor-command.v1\0"
_COMMAND_RESULT_AUTHENTICATION_DOMAIN = b"automation-tool.local-executor-result.v1\0"
_COMMAND_PROOF_PREFIX = "atlcp1."
_ALLOWED_COMMANDS = frozenset(("douyin.login.open", "douyin.login.recheck"))
_ALLOWED_SESSION_COMMANDS = frozenset(("douyin.logout.complete",))
_ALLOWED_COMMAND_RESULTS = frozenset(
    (
        "login_required",
        "awaiting_scan",
        "awaiting_confirmation",
        "qr_expired",
        "healthy",
        "handoff_required",
        "unknown",
        "logged_out",
    )
)


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

    def proof_for_command(
        self,
        *,
        command_id: str,
        command_type: str,
        executable_path: str,
        profile_directory: str,
        headless: bool,
    ) -> str:
        _require_command_fields(
            command_id=command_id,
            command_type=command_type,
            executable_path=executable_path,
            profile_directory=profile_directory,
            headless=headless,
        )
        return self._proof(
            _COMMAND_AUTHENTICATION_DOMAIN,
            (
                command_id,
                command_type,
                executable_path,
                profile_directory,
                "1" if headless else "0",
                EXECUTOR_PROTOCOL_VERSION,
            ),
        )

    def verify_command(
        self,
        *,
        command_id: str,
        command_type: str,
        executable_path: str,
        profile_directory: str,
        headless: bool,
        presented_proof: str,
    ) -> None:
        expected = self.proof_for_command(
            command_id=command_id,
            command_type=command_type,
            executable_path=executable_path,
            profile_directory=profile_directory,
            headless=headless,
        )
        if type(presented_proof) is not str or not hmac.compare_digest(expected, presented_proof):
            raise LocalSessionAuthenticationRejected

    def proof_for_session_command(self, *, command_id: str, command_type: str) -> str:
        _require_uuid_v4(command_id)
        if type(command_type) is not str or command_type not in _ALLOWED_SESSION_COMMANDS:
            raise LocalSessionAuthenticationRejected
        return self._proof(
            _COMMAND_AUTHENTICATION_DOMAIN,
            (command_id, command_type, EXECUTOR_PROTOCOL_VERSION),
        )

    def verify_session_command(
        self,
        *,
        command_id: str,
        command_type: str,
        presented_proof: str,
    ) -> None:
        expected = self.proof_for_session_command(
            command_id=command_id,
            command_type=command_type,
        )
        if type(presented_proof) is not str or not hmac.compare_digest(expected, presented_proof):
            raise LocalSessionAuthenticationRejected

    def proof_for_command_result(self, *, command_id: str, state: str) -> str:
        _require_uuid_v4(command_id)
        if state not in _ALLOWED_COMMAND_RESULTS or type(state) is not str:
            raise LocalSessionAuthenticationRejected
        return self._proof(
            _COMMAND_RESULT_AUTHENTICATION_DOMAIN,
            (command_id, state, EXECUTOR_PROTOCOL_VERSION),
        )

    def _proof(self, domain: bytes, parts: tuple[str, ...]) -> str:
        if len(self._key) != 32:
            raise LocalSessionAuthenticationRejected
        message = domain + b"\0".join(part.encode("utf-8") for part in parts)
        digest = hmac.digest(self._key, message, hashlib.sha256)
        encoded = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        return _COMMAND_PROOF_PREFIX + encoded

    def close(self) -> None:
        for index in range(len(self._key)):
            self._key[index] = 0
        self._key.clear()


def _require_uuid_v4(value: object) -> None:
    try:
        if type(value) is not str:
            raise ValueError
        parsed = UUID(value)
        if parsed.version != 4 or parsed.variant != RFC_4122 or str(parsed) != value:
            raise ValueError
    except Exception:
        raise LocalSessionAuthenticationRejected from None


def _require_command_fields(
    *,
    command_id: str,
    command_type: str,
    executable_path: str,
    profile_directory: str,
    headless: bool,
) -> None:
    _require_uuid_v4(command_id)
    if (
        type(command_type) is not str
        or command_type not in _ALLOWED_COMMANDS
        or type(executable_path) is not str
        or not executable_path
        or len(executable_path) > 4096
        or type(profile_directory) is not str
        or not profile_directory
        or len(profile_directory) > 4096
        or type(headless) is not bool
    ):
        raise LocalSessionAuthenticationRejected


__all__ = [
    "LocalSessionAuthenticationRejected",
    "LocalSessionAuthenticator",
    "require_local_session_token",
]
