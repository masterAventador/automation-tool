"""Local Executor verification of Control Plane-signed action authority."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from automation_tool.protocol import (
    DouyinSearchExposureAction,
    IdempotencyKey,
    ProtocolActionId,
    ProtocolExecutionAttemptId,
    ProtocolExecutorId,
    ProtocolInstallationId,
    ProtocolTargetId,
    ProtocolTaskId,
    action_authorization_idempotency_key,
)


class ActionAuthorizationVerificationRejected(PermissionError):
    def __init__(self) -> None:
        super().__init__("Action authorization verification is rejected")


class ActionAuthorizationVerificationClock(Protocol):
    def now(self) -> datetime: ...


@dataclass(frozen=True, slots=True, repr=False)
class ActionAuthorizationExpectation:
    action_id: ProtocolActionId
    target_id: ProtocolTargetId
    execution_attempt_id: ProtocolExecutionAttemptId
    task_id: ProtocolTaskId
    installation_id: ProtocolInstallationId
    executor_id: ProtocolExecutorId
    platform: str
    action: DouyinSearchExposureAction
    idempotency_key: IdempotencyKey

    def __post_init__(self) -> None:
        if (
            type(self.action_id) is not ProtocolActionId
            or type(self.target_id) is not ProtocolTargetId
            or type(self.execution_attempt_id) is not ProtocolExecutionAttemptId
            or type(self.task_id) is not ProtocolTaskId
            or type(self.installation_id) is not ProtocolInstallationId
            or type(self.executor_id) is not ProtocolExecutorId
            or self.platform != "douyin"
            or not isinstance(self.action, DouyinSearchExposureAction)
            or type(self.idempotency_key) is not IdempotencyKey
            or self.idempotency_key != action_authorization_idempotency_key(self.action_id)
        ):
            raise ActionAuthorizationVerificationRejected

    def __repr__(self) -> str:
        return "ActionAuthorizationExpectation(<redacted>)"


def _canonical_utc(value: object) -> datetime | None:
    if type(value) is not datetime or value.tzinfo is None:
        return None
    try:
        if value.utcoffset() != timedelta(0):
            return None
        return value.astimezone(UTC)
    except Exception:
        return None


__all__ = [
    "ActionAuthorizationExpectation",
    "ActionAuthorizationVerificationClock",
    "ActionAuthorizationVerificationRejected",
]
