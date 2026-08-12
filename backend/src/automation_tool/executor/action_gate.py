"""Local, durable hard limits applied after Control Plane action verification."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol, runtime_checkable

from automation_tool.executor.action_authorization import ActionAuthorizationExpectation
from automation_tool.executor.ledger import (
    ExecutorActionAdmissionLimited,
    ExecutorLedger,
    LocalActionAdmission,
    LocalActionEmergencyStop,
)
from automation_tool.protocol import (
    ACTION_AUTHORIZATION_MAX_LIFETIME,
    ACTION_AUTHORIZATION_VERSION,
    ActionAuthorizationClaims,
    ProtocolActionId,
)

_MAXIMUM_LOCAL_ACTION_INTERVAL = timedelta(hours=1)
_MAXIMUM_LOCAL_TASK_ACTIONS = 100


class LocalActionLimitReason(StrEnum):
    EMERGENCY_STOP = "emergency_stop"
    MINIMUM_INTERVAL = "minimum_interval"
    TASK_ACTION_LIMIT = "task_action_limit"


class ActionGateRejected(PermissionError):
    def __init__(self) -> None:
        super().__init__("Local action gate is rejected")


class ActionGateLimited(PermissionError):
    def __init__(self, reason: LocalActionLimitReason) -> None:
        if not isinstance(reason, LocalActionLimitReason):
            raise ActionGateRejected
        self.reason = reason
        PermissionError.__init__(self, "Local action gate is limited")


@dataclass(frozen=True, slots=True, repr=False)
class LocalActionHardPolicy:
    minimum_interval: timedelta
    task_action_limit: int

    def __post_init__(self) -> None:
        if (
            type(self.minimum_interval) is not timedelta
            or self.minimum_interval.microseconds != 0
            or not timedelta(seconds=1) <= self.minimum_interval <= _MAXIMUM_LOCAL_ACTION_INTERVAL
            or type(self.task_action_limit) is not int
            or not 1 <= self.task_action_limit <= _MAXIMUM_LOCAL_TASK_ACTIONS
        ):
            raise ActionGateRejected

    def __repr__(self) -> str:
        return "LocalActionHardPolicy(<redacted>)"


@runtime_checkable
class ActionGateClock(Protocol):
    def now(self) -> datetime: ...


class ExecutorActionGate:
    """Require installation-local durable permission for every platform action."""

    __slots__ = ("_clock", "_ledger", "_policy")

    def __init__(
        self,
        *,
        ledger: ExecutorLedger,
        policy: LocalActionHardPolicy,
        clock: ActionGateClock,
    ) -> None:
        try:
            if (
                not isinstance(ledger, ExecutorLedger)
                or not isinstance(policy, LocalActionHardPolicy)
                or not isinstance(clock, ActionGateClock)
            ):
                raise ValueError
            binding = ledger.bind_action_hard_policy(
                minimum_interval_seconds=int(policy.minimum_interval.total_seconds()),
                task_action_limit=policy.task_action_limit,
            )
            self._ledger = ledger
            self._policy = LocalActionHardPolicy(
                minimum_interval=timedelta(seconds=binding.minimum_interval_seconds),
                task_action_limit=binding.task_action_limit,
            )
            self._clock = clock
        except Exception:
            raise ActionGateRejected from None

    def __repr__(self) -> str:
        return "ExecutorActionGate(<redacted>)"

    def admit(
        self,
        *,
        expected: ActionAuthorizationExpectation,
    ) -> LocalActionAdmission:
        try:
            now = self._now()
            claims = ActionAuthorizationClaims(
                version=ACTION_AUTHORIZATION_VERSION,
                action_id=expected.action_id,
                target_id=expected.target_id,
                execution_attempt_id=expected.execution_attempt_id,
                task_id=expected.task_id,
                installation_id=expected.installation_id,
                executor_id=expected.executor_id,
                platform=expected.platform,
                action=expected.action,
                idempotency_key=expected.idempotency_key,
                authorized_at=now,
                deadline_at=now + ACTION_AUTHORIZATION_MAX_LIFETIME,
            )
            fingerprint = hashlib.sha256(str(expected.action_id).encode("ascii")).digest()
            return self._ledger.admit_action(
                claims=claims,
                authorization_fingerprint=fingerprint,
                admitted_at=self._now(),
                minimum_interval_seconds=int(self._policy.minimum_interval.total_seconds()),
                task_action_limit=self._policy.task_action_limit,
            )
        except ExecutorActionAdmissionLimited as error:
            raise ActionGateLimited(LocalActionLimitReason(error.reason.value)) from None
        except Exception:
            raise ActionGateRejected from None

    def admission(self, action_id: ProtocolActionId) -> LocalActionAdmission | None:
        try:
            if type(action_id) is not ProtocolActionId:
                raise ValueError
            return self._ledger.get_action_admission(str(action_id))
        except Exception:
            raise ActionGateRejected from None

    def emergency_stop(self) -> LocalActionEmergencyStop:
        try:
            return self._ledger.get_action_emergency_stop()
        except Exception:
            raise ActionGateRejected from None

    def engage_emergency_stop(self) -> LocalActionEmergencyStop:
        try:
            return self._ledger.engage_action_emergency_stop(changed_at=self._now())
        except Exception:
            raise ActionGateRejected from None

    def clear_emergency_stop(self, *, expected_revision: int) -> LocalActionEmergencyStop:
        try:
            if type(expected_revision) is not int or expected_revision <= 0:
                raise ValueError
            return self._ledger.clear_action_emergency_stop(
                expected_revision=expected_revision,
                changed_at=self._now(),
            )
        except Exception:
            raise ActionGateRejected from None

    def _now(self) -> datetime:
        value = self._clock.now()
        if type(value) is not datetime or value.tzinfo is None:
            raise ValueError
        if value.utcoffset() != timedelta(0):
            raise ValueError
        return value.astimezone(UTC)


__all__ = [
    "ActionGateClock",
    "ActionGateLimited",
    "ActionGateRejected",
    "ExecutorActionGate",
    "LocalActionHardPolicy",
    "LocalActionLimitReason",
]
