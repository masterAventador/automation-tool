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


__all__ = [
    "ACTION_AUTHORIZATION_CLOCK_SKEW",
    "ACTION_AUTHORIZATION_MAX_LIFETIME",
    "ACTION_AUTHORIZATION_VERSION",
    "ActionAuthorizationClaims",
    "ActionAuthorizationRejected",
    "action_authorization_idempotency_key",
]
