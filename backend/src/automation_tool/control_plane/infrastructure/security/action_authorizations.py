"""Control Plane-only Ed25519 issuance for short-lived action authority."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from automation_tool.control_plane.application.action_risk_authorizations import (
    ActionRiskAuthorization,
)
from automation_tool.control_plane.domain import ActionRiskPlatform, ExecutorId
from automation_tool.protocol import (
    ACTION_AUTHORIZATION_MAX_LIFETIME,
    ACTION_AUTHORIZATION_VERSION,
    ActionAuthorizationClaims,
    ProtocolActionId,
    ProtocolExecutionAttemptId,
    ProtocolExecutorId,
    ProtocolInstallationId,
    ProtocolTargetId,
    ProtocolTaskId,
    action_authorization_idempotency_key,
    action_authorization_signing_input,
    encode_action_authorization_token,
)


class ActionAuthorizationIssuanceRejected(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Action authorization issuance is rejected")


class ActionAuthorizationIssuanceClock(Protocol):
    def now(self) -> datetime: ...


@dataclass(frozen=True, slots=True, repr=False)
class IssuedActionAuthorization:
    claims: ActionAuthorizationClaims
    token: str
    fingerprint: bytes

    def __repr__(self) -> str:
        return "IssuedActionAuthorization(<redacted>)"


def _canonical_utc(value: object) -> datetime | None:
    if type(value) is not datetime or value.tzinfo is None:
        return None
    try:
        if value.utcoffset() != timedelta(0):
            return None
        return value.astimezone(UTC)
    except Exception:
        return None


class Ed25519ActionAuthorizationIssuer:
    """Map one immutable A7-02 fact to one deterministic signed capability."""

    __slots__ = ("_clock", "_lifetime", "_private_key")

    def __init__(
        self,
        *,
        private_key: bytes,
        clock: ActionAuthorizationIssuanceClock,
        authorization_lifetime: timedelta,
    ) -> None:
        if (
            type(private_key) is not bytes
            or len(private_key) != 32
            or not callable(getattr(clock, "now", None))
            or type(authorization_lifetime) is not timedelta
            or authorization_lifetime.microseconds != 0
            or not timedelta(seconds=1)
            <= authorization_lifetime
            <= ACTION_AUTHORIZATION_MAX_LIFETIME
        ):
            raise ActionAuthorizationIssuanceRejected
        self._private_key = Ed25519PrivateKey.from_private_bytes(private_key)
        self._clock = clock
        self._lifetime = authorization_lifetime

    def __repr__(self) -> str:
        return "Ed25519ActionAuthorizationIssuer(<redacted>)"

    def _now(self) -> datetime:
        try:
            value = _canonical_utc(self._clock.now())
        except Exception:
            value = None
        if value is None:
            raise ActionAuthorizationIssuanceRejected
        return value

    def issue(
        self,
        *,
        authorization: ActionRiskAuthorization,
        executor_id: ExecutorId,
    ) -> IssuedActionAuthorization:
        now = self._now()
        if (
            not isinstance(authorization, ActionRiskAuthorization)
            or type(executor_id) is not ExecutorId
            or authorization.platform is not ActionRiskPlatform.DOUYIN
            or authorization.created_at != authorization.authorized_at
        ):
            raise ActionAuthorizationIssuanceRejected
        deadline_at = authorization.authorized_at + self._lifetime
        if now < authorization.authorized_at or now >= deadline_at:
            raise ActionAuthorizationIssuanceRejected
        try:
            action_id = ProtocolActionId(str(authorization.action_id))
            claims = ActionAuthorizationClaims(
                version=ACTION_AUTHORIZATION_VERSION,
                action_id=action_id,
                target_id=ProtocolTargetId(str(authorization.target_id)),
                execution_attempt_id=ProtocolExecutionAttemptId(
                    str(authorization.execution_attempt_id)
                ),
                task_id=ProtocolTaskId(str(authorization.task_id)),
                installation_id=ProtocolInstallationId(str(authorization.installation_id)),
                executor_id=ProtocolExecutorId(str(executor_id)),
                platform=authorization.platform.value,
                action=authorization.action,
                idempotency_key=action_authorization_idempotency_key(action_id),
                authorized_at=authorization.authorized_at,
                deadline_at=deadline_at,
            )
            signature = self._private_key.sign(action_authorization_signing_input(claims))
            token = encode_action_authorization_token(claims, signature)
            return IssuedActionAuthorization(
                claims=claims,
                token=token,
                fingerprint=hashlib.sha256(token.encode("ascii")).digest(),
            )
        except Exception:
            raise ActionAuthorizationIssuanceRejected from None


__all__ = [
    "ActionAuthorizationIssuanceClock",
    "ActionAuthorizationIssuanceRejected",
    "Ed25519ActionAuthorizationIssuer",
    "IssuedActionAuthorization",
]
