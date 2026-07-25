"""Domain-separated proof for one one-shot Tauri-to-Executor launch secret."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
from uuid import RFC_4122, UUID

from pydantic import SecretStr

from automation_tool.protocol import EXECUTOR_PROTOCOL_VERSION
from automation_tool.protocol.safe_text import contains_control_or_bidi

_LOCAL_SESSION_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_AUTHENTICATION_DOMAIN = b"automation-tool.local-executor-event.v1\0"
_PROOF_PREFIX = "atlep1."
_ALLOWED_EVENTS = frozenset(("executor.healthy", "executor.stopped"))
_COMMAND_AUTHENTICATION_DOMAIN = b"automation-tool.local-executor-command.v1\0"
_COMMAND_RESULT_AUTHENTICATION_DOMAIN = b"automation-tool.local-executor-result.v1\0"
_COMMAND_PROOF_PREFIX = "atlcp1."
_PUBLISH_COMMAND_AUTHENTICATION_DOMAIN = b"automation-tool.local-executor-publish-command.v1\0"
_PUBLISH_DISPATCH_AUTHENTICATION_DOMAIN = (
    b"automation-tool.local-executor-publish-dispatch.v1\0"
)
_ALLOWED_COMMANDS = frozenset(("douyin.login.open", "douyin.login.recheck"))
_ALLOWED_SESSION_COMMANDS = frozenset(("douyin.logout.complete", "douyin.publish.release"))
_ALLOWED_PUBLISH_COMMANDS = frozenset(("douyin.publish.preflight",))
_ALLOWED_PUBLISH_DISPATCH_COMMANDS = frozenset(("douyin.publish.dispatch",))
# An account name is rendered in a confirmation dialog, so it is bounded far
# more tightly than the publish copy that shares `_bounded_text`.
MAX_RESULT_ACCOUNT_CHARACTERS = 64
_MAX_PUBLISH_TEXT_CHARACTERS = 4096
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
        "publish_pre_submit_ready",
        "publish_handoff_required",
        "publish_blocked",
        "publish_verified",
        "publish_outcome_uncertain",
        "publish_not_dispatched",
        "publish_released",
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

    def proof_for_publish_command(
        self,
        *,
        command_id: str,
        command_type: str,
        executable_path: str,
        profile_directory: str,
        headless: bool,
        publish_job_id: str,
        artifact_path: str,
        title: str,
        description: str,
    ) -> str:
        """Bind the browser identity, artifact and user text into one publish proof."""
        _require_uuid_v4(command_id)
        _require_uuid_v4(publish_job_id)
        if (
            type(command_type) is not str
            or command_type not in _ALLOWED_PUBLISH_COMMANDS
            or not _bounded_text(executable_path)
            or not _bounded_text(profile_directory)
            or not _bounded_text(artifact_path)
            or not _bounded_text(title)
            or not _bounded_text(description)
            or type(headless) is not bool
        ):
            raise LocalSessionAuthenticationRejected
        return self._proof(
            _PUBLISH_COMMAND_AUTHENTICATION_DOMAIN,
            (
                command_id,
                command_type,
                executable_path,
                profile_directory,
                "1" if headless else "0",
                publish_job_id,
                artifact_path,
                title,
                description,
                EXECUTOR_PROTOCOL_VERSION,
            ),
        )

    def verify_publish_command(
        self,
        *,
        command_id: str,
        command_type: str,
        executable_path: str,
        profile_directory: str,
        headless: bool,
        publish_job_id: str,
        artifact_path: str,
        title: str,
        description: str,
        presented_proof: str,
    ) -> None:
        expected = self.proof_for_publish_command(
            command_id=command_id,
            command_type=command_type,
            executable_path=executable_path,
            profile_directory=profile_directory,
            headless=headless,
            publish_job_id=publish_job_id,
            artifact_path=artifact_path,
            title=title,
            description=description,
        )
        if type(presented_proof) is not str or not hmac.compare_digest(expected, presented_proof):
            raise LocalSessionAuthenticationRejected

    def proof_for_publish_dispatch_command(
        self,
        *,
        command_id: str,
        command_type: str,
        publish_job_id: str,
        confirmation_id: str,
    ) -> str:
        """Bind the job and the approval this dispatch is allowed to spend.

        The dispatch carries no artifact or text of its own - it acts on the
        pre-submit state the executor already holds - so what has to be
        unforgeable is which job and which confirmation it names.
        """
        _require_uuid_v4(command_id)
        _require_uuid_v4(publish_job_id)
        _require_uuid_v4(confirmation_id)
        if type(command_type) is not str or command_type not in _ALLOWED_PUBLISH_DISPATCH_COMMANDS:
            raise LocalSessionAuthenticationRejected
        return self._proof(
            _PUBLISH_DISPATCH_AUTHENTICATION_DOMAIN,
            (
                command_id,
                command_type,
                publish_job_id,
                confirmation_id,
                EXECUTOR_PROTOCOL_VERSION,
            ),
        )

    def verify_publish_dispatch_command(
        self,
        *,
        command_id: str,
        command_type: str,
        publish_job_id: str,
        confirmation_id: str,
        presented_proof: str,
    ) -> None:
        expected = self.proof_for_publish_dispatch_command(
            command_id=command_id,
            command_type=command_type,
            publish_job_id=publish_job_id,
            confirmation_id=confirmation_id,
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

    def proof_for_command_result(
        self,
        *,
        command_id: str,
        state: str,
        confirmation_id: str | None = None,
        target_account: str | None = None,
    ) -> str:
        """Bind one result, including the terms of any approval it carries.

        A preflight that stops before submission has to tell the App which
        account it is about to post to and which confirmation would authorize
        it. Both are page-derived facts the App cannot check for itself, so
        they are bound into the proof: an account name swapped on the way out
        would otherwise be the operator approving one account and publishing to
        another.
        """
        _require_uuid_v4(command_id)
        if state not in _ALLOWED_COMMAND_RESULTS or type(state) is not str:
            raise LocalSessionAuthenticationRejected
        if confirmation_id is None and target_account is None:
            return self._proof(
                _COMMAND_RESULT_AUTHENTICATION_DOMAIN,
                (command_id, state, EXECUTOR_PROTOCOL_VERSION),
            )
        # Half an approval is not an approval: one term without the other would
        # leave the App showing an account nobody can confirm, or confirming
        # terms nobody was shown.
        if confirmation_id is None or target_account is None:
            raise LocalSessionAuthenticationRejected
        _require_uuid_v4(confirmation_id)
        if (
            not _bounded_text(target_account)
            or len(target_account) > MAX_RESULT_ACCOUNT_CHARACTERS
        ):
            raise LocalSessionAuthenticationRejected
        return self._proof(
            _COMMAND_RESULT_AUTHENTICATION_DOMAIN,
            (
                command_id,
                state,
                confirmation_id,
                target_account,
                EXECUTOR_PROTOCOL_VERSION,
            ),
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


def _bounded_text(value: object) -> bool:
    """Reject control characters so no field can impersonate the proof separator."""
    return (
        type(value) is str
        and 1 <= len(value) <= _MAX_PUBLISH_TEXT_CHARACTERS
        and not contains_control_or_bidi(value)
    )


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
        or not _bounded_text(executable_path)
        or not _bounded_text(profile_directory)
        or type(headless) is not bool
    ):
        raise LocalSessionAuthenticationRejected


__all__ = [
    "LocalSessionAuthenticationRejected",
    "LocalSessionAuthenticator",
    "require_local_session_token",
]
