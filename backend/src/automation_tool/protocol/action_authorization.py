"""Canonical short-lived claims signed by the Control Plane for one platform action."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from automation_tool.protocol.douyin_search import DouyinSearchExposureAction
from automation_tool.protocol.executor_envelope import (
    IdempotencyKey,
    ProtocolActionId,
    ProtocolExecutionAttemptId,
    ProtocolExecutorId,
    ProtocolInstallationId,
    ProtocolTargetId,
    ProtocolTaskId,
)

ACTION_AUTHORIZATION_VERSION: Final = "action-authorization.v1"
ACTION_AUTHORIZATION_MAX_LIFETIME: Final = timedelta(minutes=5)
ACTION_AUTHORIZATION_CLOCK_SKEW: Final = timedelta(seconds=30)
ACTION_AUTHORIZATION_TOKEN_PREFIX: Final = "ataa1"
MAX_ACTION_AUTHORIZATION_TOKEN_BYTES: Final = 2048

_SIGNING_DOMAIN: Final = b"automation-tool.action-authorization.v1\0"
_SIGNATURE_BYTES: Final = 64
_BASE64URL_PATTERN: Final = re.compile(r"[A-Za-z0-9_-]+\Z")
_CLAIM_NAMES: Final = frozenset(
    {
        "action",
        "action_id",
        "authorized_at",
        "deadline_at",
        "execution_attempt_id",
        "executor_id",
        "idempotency_key",
        "installation_id",
        "platform",
        "target_id",
        "task_id",
        "version",
    }
)


class ActionAuthorizationRejected(ValueError):
    """A signed action authorization is malformed, over-scoped, or incoherent."""

    def __init__(self) -> None:
        super().__init__("Action authorization is rejected")


@dataclass(frozen=True, slots=True, repr=False)
class ActionAuthorizationClaims:
    """Exact authority granted for one action and one Local Executor."""

    version: str
    action_id: ProtocolActionId
    target_id: ProtocolTargetId
    execution_attempt_id: ProtocolExecutionAttemptId
    task_id: ProtocolTaskId
    installation_id: ProtocolInstallationId
    executor_id: ProtocolExecutorId
    platform: str
    action: DouyinSearchExposureAction
    idempotency_key: IdempotencyKey
    authorized_at: datetime
    deadline_at: datetime

    def __post_init__(self) -> None:
        authorized_at = _canonical_utc(self.authorized_at)
        deadline_at = _canonical_utc(self.deadline_at)
        if (
            self.version != ACTION_AUTHORIZATION_VERSION
            or type(self.action_id) is not ProtocolActionId
            or type(self.target_id) is not ProtocolTargetId
            or type(self.execution_attempt_id) is not ProtocolExecutionAttemptId
            or type(self.task_id) is not ProtocolTaskId
            or type(self.installation_id) is not ProtocolInstallationId
            or type(self.executor_id) is not ProtocolExecutorId
            or self.platform != "douyin"
            or not isinstance(self.action, DouyinSearchExposureAction)
            or type(self.idempotency_key) is not IdempotencyKey
            or self.idempotency_key != action_authorization_idempotency_key(self.action_id)
            or authorized_at is None
            or deadline_at is None
            or not authorized_at < deadline_at
            or deadline_at - authorized_at > ACTION_AUTHORIZATION_MAX_LIFETIME
        ):
            raise ActionAuthorizationRejected
        object.__setattr__(self, "authorized_at", authorized_at)
        object.__setattr__(self, "deadline_at", deadline_at)

    def __repr__(self) -> str:
        return (
            "ActionAuthorizationClaims("
            f"platform={self.platform!r}, action={self.action.value!r}, "
            f"version={self.version!r}, <redacted>)"
        )


@dataclass(frozen=True, slots=True, repr=False)
class ParsedActionAuthorizationToken:
    claims: ActionAuthorizationClaims
    signing_input: bytes
    signature: bytes
    fingerprint: bytes

    def __repr__(self) -> str:
        return "ParsedActionAuthorizationToken(<redacted>)"


def _canonical_utc(value: object) -> datetime | None:
    if type(value) is not datetime or value.tzinfo is None:
        return None
    try:
        if value.utcoffset() != timedelta(0):
            return None
        return value.astimezone(UTC)
    except Exception:
        return None


def _render_timestamp(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: object) -> datetime:
    if type(value) is not str:
        raise ActionAuthorizationRejected
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    except ValueError:
        raise ActionAuthorizationRejected from None
    if _render_timestamp(parsed) != value:
        raise ActionAuthorizationRejected
    return parsed


def action_authorization_idempotency_key(action_id: ProtocolActionId) -> IdempotencyKey:
    if type(action_id) is not ProtocolActionId:
        raise ActionAuthorizationRejected
    return IdempotencyKey(f"action:{action_id}")


def _document(claims: ActionAuthorizationClaims) -> dict[str, object]:
    if not isinstance(claims, ActionAuthorizationClaims):
        raise ActionAuthorizationRejected
    return {
        "action": claims.action.value,
        "action_id": str(claims.action_id),
        "authorized_at": _render_timestamp(claims.authorized_at),
        "deadline_at": _render_timestamp(claims.deadline_at),
        "execution_attempt_id": str(claims.execution_attempt_id),
        "executor_id": str(claims.executor_id),
        "idempotency_key": str(claims.idempotency_key),
        "installation_id": str(claims.installation_id),
        "platform": claims.platform,
        "target_id": str(claims.target_id),
        "task_id": str(claims.task_id),
        "version": claims.version,
    }


def _payload(claims: ActionAuthorizationClaims) -> bytes:
    try:
        return json.dumps(
            _document(claims),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, UnicodeEncodeError, ValueError):
        raise ActionAuthorizationRejected from None


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _base64url_decode(value: str) -> bytes:
    if _BASE64URL_PATTERN.fullmatch(value) is None:
        raise ActionAuthorizationRejected
    try:
        decoded = base64.urlsafe_b64decode(value + ("=" * (-len(value) % 4)))
    except (ValueError, binascii.Error):
        raise ActionAuthorizationRejected from None
    if _base64url_encode(decoded) != value:
        raise ActionAuthorizationRejected
    return decoded


def _payload_segment(claims: ActionAuthorizationClaims) -> str:
    return _base64url_encode(_payload(claims))


def action_authorization_signing_input(claims: ActionAuthorizationClaims) -> bytes:
    try:
        framed = f"{ACTION_AUTHORIZATION_TOKEN_PREFIX}.{_payload_segment(claims)}".encode("ascii")
    except (UnicodeEncodeError, ValueError):
        raise ActionAuthorizationRejected from None
    return _SIGNING_DOMAIN + framed


def encode_action_authorization_token(
    claims: ActionAuthorizationClaims,
    signature: bytes,
) -> str:
    if (
        not isinstance(claims, ActionAuthorizationClaims)
        or type(signature) is not bytes
        or len(signature) != _SIGNATURE_BYTES
    ):
        raise ActionAuthorizationRejected
    payload_segment = _payload_segment(claims)
    token = f"{ACTION_AUTHORIZATION_TOKEN_PREFIX}.{payload_segment}.{_base64url_encode(signature)}"
    return token


def _unique_object(pairs: Iterable[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ActionAuthorizationRejected
        result[key] = value
    return result


def _claims(payload: bytes) -> ActionAuthorizationClaims:
    try:
        document = json.loads(payload, object_pairs_hook=_unique_object)
        if not isinstance(document, dict) or set(document) != _CLAIM_NAMES:
            raise ActionAuthorizationRejected
        claims = ActionAuthorizationClaims(
            version=document["version"],
            action_id=ProtocolActionId(document["action_id"]),
            target_id=ProtocolTargetId(document["target_id"]),
            execution_attempt_id=ProtocolExecutionAttemptId(document["execution_attempt_id"]),
            task_id=ProtocolTaskId(document["task_id"]),
            installation_id=ProtocolInstallationId(document["installation_id"]),
            executor_id=ProtocolExecutorId(document["executor_id"]),
            platform=document["platform"],
            action=DouyinSearchExposureAction(document["action"]),
            idempotency_key=IdempotencyKey(document["idempotency_key"]),
            authorized_at=_parse_timestamp(document["authorized_at"]),
            deadline_at=_parse_timestamp(document["deadline_at"]),
        )
    except (
        ActionAuthorizationRejected,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
    ):
        raise ActionAuthorizationRejected from None
    if _payload(claims) != payload:
        raise ActionAuthorizationRejected
    return claims


def parse_action_authorization_token(value: object) -> ParsedActionAuthorizationToken:
    try:
        if type(value) is not str:
            raise ActionAuthorizationRejected
        token_bytes = value.encode("ascii")
        if not 1 <= len(token_bytes) <= MAX_ACTION_AUTHORIZATION_TOKEN_BYTES:
            raise ActionAuthorizationRejected
        prefix, payload_segment, signature_segment = value.split(".")
        if prefix != ACTION_AUTHORIZATION_TOKEN_PREFIX:
            raise ActionAuthorizationRejected
        payload = _base64url_decode(payload_segment)
        signature = _base64url_decode(signature_segment)
        if len(signature) != _SIGNATURE_BYTES:
            raise ActionAuthorizationRejected
        claims = _claims(payload)
        signing_input = action_authorization_signing_input(claims)
        return ParsedActionAuthorizationToken(
            claims=claims,
            signing_input=signing_input,
            signature=signature,
            fingerprint=hashlib.sha256(token_bytes).digest(),
        )
    except (ActionAuthorizationRejected, UnicodeEncodeError, ValueError):
        raise ActionAuthorizationRejected from None


__all__ = [
    "ACTION_AUTHORIZATION_CLOCK_SKEW",
    "ACTION_AUTHORIZATION_MAX_LIFETIME",
    "ACTION_AUTHORIZATION_TOKEN_PREFIX",
    "ACTION_AUTHORIZATION_VERSION",
    "MAX_ACTION_AUTHORIZATION_TOKEN_BYTES",
    "ActionAuthorizationClaims",
    "ActionAuthorizationRejected",
    "ParsedActionAuthorizationToken",
    "action_authorization_idempotency_key",
    "action_authorization_signing_input",
    "encode_action_authorization_token",
    "parse_action_authorization_token",
]
