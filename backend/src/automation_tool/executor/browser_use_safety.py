"""BU-05: untrusted-input redaction and confirmation gates for Browser Use.

Everything a page hands the model — DOM text, screenshots' extracted text,
navigation instructions, agent history — is untrusted. Before any of it
reaches the model it is redacted of cookies, tokens, keys, CDP loopback URLs
and local filesystem paths. Real secret values live behind a placeholder gate
so the model only ever sees `<secret>key</secret>`; revealing a real value to
the page requires an explicit prior confirmation and is single-use. External
side effects (publish/comment/message) pass a critical-point confirmation
gate that binds the confirmed content hash to a single-use dispatch token, so
the actual browser action can only fire once, against exactly what the user
approved. This layer reuses the ActionGate/ledger for admission and never
holds cookies or tokens in model-visible state.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field
from typing import Final, NoReturn
from uuid import uuid4

from automation_tool.protocol.safe_text import is_sha256_hex

_KEY_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_ACTION_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SECRET_PLACEHOLDER_PATTERN: Final = re.compile(r"<secret>([^<>]{1,64})</secret>")
_MAX_SECRET_VALUE_CHARS: Final = 4096
_DISPATCH_TOKEN_BYTES: Final = 32

_REDACTIONS: Final = (
    # Cookie name=value pairs.
    (re.compile(r"(?i)\b[a-z_][a-z0-9_]*\s*=\s*[^\s;,'\"]+"), "[redacted-pair]"),
    # Bearer / api keys.
    (re.compile(r"(?i)bearer\s+\S+"), "[redacted-auth]"),
    (re.compile(r"\bsk-[A-Za-z0-9._-]{8,}"), "[redacted-key]"),
    # CDP / loopback endpoints.
    (re.compile(r"https?://127\.0\.0\.1:\d+\S*"), "[redacted-cdp]"),
    (re.compile(r"https?://localhost:\d+\S*"), "[redacted-cdp]"),
    # Local filesystem paths.
    (re.compile(r"/Users/[^\s'\"]+"), "[redacted-path]"),
    (re.compile(r"/home/[^\s'\"]+"), "[redacted-path]"),
    (re.compile(r"[A-Za-z]:\\\\?Users\\\\?[^\s'\"]+"), "[redacted-path]"),
    (re.compile(r"[A-Za-z]:\\Users\\[^\s'\"]+"), "[redacted-path]"),
)


class BrowserUseSafetyRejected(RuntimeError):
    """An untrusted input or confirmation transition violated the safety policy."""

    def __init__(self) -> None:
        super().__init__("browser use safety policy rejected the operation")


def _reject() -> NoReturn:
    raise BrowserUseSafetyRejected


def redact_untrusted_text(text: str) -> str:
    """Redact secrets from untrusted page/model text before it reaches a model."""
    if type(text) is not str:
        _reject()
    redacted = text
    for pattern, replacement in _REDACTIONS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


class SensitiveDataGate:
    """Placeholder gate: the model sees only `<secret>key</secret>` names."""

    __slots__ = ("_confirmed", "_secrets")

    def __init__(self) -> None:
        self._secrets: dict[str, str] = {}
        self._confirmed: set[str] = set()

    def register(self, key: str, value: str) -> None:
        """Register one placeholder key mapping to a real secret value."""
        if (
            type(key) is not str
            or _KEY_PATTERN.fullmatch(key) is None
            or type(value) is not str
            or not 1 <= len(value) <= _MAX_SECRET_VALUE_CHARS
        ):
            _reject()
        self._secrets[key] = value

    def model_visible(self, text: str) -> str:
        """Return text safe for the model: only known placeholders, no real values."""
        if type(text) is not str:
            _reject()
        for match in _SECRET_PLACEHOLDER_PATTERN.finditer(text):
            if match.group(1) not in self._secrets:
                _reject()
        for value in self._secrets.values():
            if value in text:
                _reject()
        return text

    def confirm_send(self, key: str) -> None:
        """Confirm one single-use permission to reveal a real secret to the page."""
        if key not in self._secrets:
            _reject()
        self._confirmed.add(key)

    def reveal(self, key: str) -> str:
        """Reveal a real secret to the page, consuming its single confirmation."""
        if key not in self._secrets or key not in self._confirmed:
            _reject()
        self._confirmed.discard(key)
        return self._secrets[key]

    def __repr__(self) -> str:
        return f"SensitiveDataGate(keys={sorted(self._secrets)!r})"


@dataclass(frozen=True, repr=False)
class SideEffectApproval:
    """The user-facing critical-point summary and its confirmation identifier."""

    confirmation_id: str
    summary: str

    def __repr__(self) -> str:
        return f"SideEffectApproval(confirmation_id={self.confirmation_id!r})"


@dataclass(frozen=True)
class _PendingSideEffect:
    action: str
    target_account: str
    content_hash: str
    dispatch_token: str | None = field(default=None)
    dispatched: bool = field(default=False)


class SideEffectConfirmationGate:
    """Critical-point confirmation binding the approved content to one dispatch."""

    __slots__ = ("_pending",)

    def __init__(self) -> None:
        self._pending: dict[str, _PendingSideEffect] = {}

    def present(self, *, action: str, target_account: str, content_hash: str) -> SideEffectApproval:
        """Present the external side effect for explicit user confirmation."""
        if (
            type(action) is not str
            or _ACTION_PATTERN.fullmatch(action) is None
            or type(target_account) is not str
            or not target_account
            or len(target_account) > 128
            or type(content_hash) is not str
            or not is_sha256_hex(content_hash)
        ):
            _reject()
        confirmation_id = str(uuid4())
        self._pending[confirmation_id] = _PendingSideEffect(
            action=action, target_account=target_account, content_hash=content_hash
        )
        summary = (
            f"即将执行 {action}，目标账号 {target_account}，"
            f"内容摘要 {content_hash[:12]}…"
        )
        return SideEffectApproval(confirmation_id=confirmation_id, summary=summary)

    def authorize_dispatch(self, confirmation_id: str, *, confirmed: bool) -> str:
        """Turn an explicit confirmation into a single-use dispatch token."""
        pending = self._pending.get(confirmation_id) if type(confirmation_id) is str else None
        if pending is None or pending.dispatched or confirmed is not True:
            _reject()
        token = secrets.token_hex(_DISPATCH_TOKEN_BYTES)
        self._pending[confirmation_id] = _PendingSideEffect(
            action=pending.action,
            target_account=pending.target_account,
            content_hash=pending.content_hash,
            dispatch_token=token,
        )
        return token

    def consume_dispatch(self, token: str, *, content_hash: str) -> None:
        """Consume the single dispatch token for exactly the confirmed content."""
        if type(token) is not str or type(content_hash) is not str:
            _reject()
        for confirmation_id, pending in self._pending.items():
            if pending.dispatch_token is not None and secrets.compare_digest(
                pending.dispatch_token, token
            ):
                if pending.dispatched or pending.content_hash != content_hash:
                    _reject()
                self._pending[confirmation_id] = _PendingSideEffect(
                    action=pending.action,
                    target_account=pending.target_account,
                    content_hash=pending.content_hash,
                    dispatch_token=None,
                    dispatched=True,
                )
                return
        _reject()

    def __repr__(self) -> str:
        return f"SideEffectConfirmationGate(pending={len(self._pending)})"


__all__ = [
    "BrowserUseSafetyRejected",
    "SensitiveDataGate",
    "SideEffectApproval",
    "SideEffectConfirmationGate",
    "redact_untrusted_text",
]
