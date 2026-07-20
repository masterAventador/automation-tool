"""Local Executor verification of Control Plane-signed action authority."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from automation_tool.protocol import (
    ACTION_AUTHORIZATION_CLOCK_SKEW,
    ActionAuthorizationClaims,
    DouyinSearchExposureAction,
    IdempotencyKey,
    ProtocolActionId,
    ProtocolExecutionAttemptId,
    ProtocolExecutorId,
    ProtocolInstallationId,
    ProtocolTargetId,
    ProtocolTaskId,
    action_authorization_idempotency_key,
    parse_action_authorization_token,
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


def _matches(
    claims: ActionAuthorizationClaims,
    expected: ActionAuthorizationExpectation,
) -> bool:
    return (
        claims.action_id == expected.action_id
        and claims.target_id == expected.target_id
        and claims.execution_attempt_id == expected.execution_attempt_id
        and claims.task_id == expected.task_id
        and claims.installation_id == expected.installation_id
        and claims.executor_id == expected.executor_id
        and claims.platform == expected.platform
        and claims.action is expected.action
        and claims.idempotency_key == expected.idempotency_key
    )


class Ed25519ActionAuthorizationVerifier:
    """Verify one exact capability against a pinned Control Plane public key."""

    __slots__ = ("_clock", "_public_key")

    def __init__(
        self,
        *,
        public_key: bytes,
        clock: ActionAuthorizationVerificationClock,
    ) -> None:
        if (
            type(public_key) is not bytes
            or len(public_key) != 32
            or not callable(getattr(clock, "now", None))
        ):
            raise ActionAuthorizationVerificationRejected
        self._public_key = Ed25519PublicKey.from_public_bytes(public_key)
        self._clock = clock

    def __repr__(self) -> str:
        return "Ed25519ActionAuthorizationVerifier(<redacted>)"

    def verify(
        self,
        *,
        token: str,
        expected: ActionAuthorizationExpectation,
    ) -> ActionAuthorizationClaims:
        try:
            if not isinstance(expected, ActionAuthorizationExpectation):
                raise ActionAuthorizationVerificationRejected
            parsed = parse_action_authorization_token(token)
            self._public_key.verify(parsed.signature, parsed.signing_input)
            now = _canonical_utc(self._clock.now())
            if (
                now is None
                or now + ACTION_AUTHORIZATION_CLOCK_SKEW < parsed.claims.authorized_at
                or now >= parsed.claims.deadline_at
                or not _matches(parsed.claims, expected)
            ):
                raise ActionAuthorizationVerificationRejected
            return parsed.claims
        except Exception:
            raise ActionAuthorizationVerificationRejected from None


__all__ = [
    "ActionAuthorizationExpectation",
    "ActionAuthorizationVerificationClock",
    "ActionAuthorizationVerificationRejected",
    "Ed25519ActionAuthorizationVerifier",
]
