"""Closed persistence enums for execution attempts and side-effect actions."""

from enum import StrEnum
from typing import Final


class ExecutionAttemptStatus(StrEnum):
    """Lifecycle facts for one delivery and execution attempt."""

    PENDING = "pending"
    OFFERED = "offered"
    ACCEPTED = "accepted"
    RUNNING = "running"
    PAUSED = "paused"
    AWAITING_HUMAN = "awaiting_human"
    CANCELLING = "cancelling"
    SUCCEEDED = "succeeded"
    PARTIALLY_SUCCEEDED = "partially_succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    OUTCOME_UNCERTAIN = "outcome_uncertain"


TERMINAL_EXECUTION_ATTEMPT_STATUSES: Final[frozenset[ExecutionAttemptStatus]] = frozenset(
    {
        ExecutionAttemptStatus.SUCCEEDED,
        ExecutionAttemptStatus.PARTIALLY_SUCCEEDED,
        ExecutionAttemptStatus.FAILED,
        ExecutionAttemptStatus.CANCELLED,
        ExecutionAttemptStatus.REJECTED,
        ExecutionAttemptStatus.EXPIRED,
        ExecutionAttemptStatus.OUTCOME_UNCERTAIN,
    }
)


class ActionStatus(StrEnum):
    """Durable phase of one externally observable action."""

    PLANNED = "planned"
    AUTHORIZED = "authorized"
    PREPARED = "prepared"
    DISPATCHED = "dispatched"
    VERIFIED = "verified"
    CANCELLED = "cancelled"
    OUTCOME_UNCERTAIN = "outcome_uncertain"


TERMINAL_ACTION_STATUSES: Final[frozenset[ActionStatus]] = frozenset(
    {
        ActionStatus.VERIFIED,
        ActionStatus.CANCELLED,
        ActionStatus.OUTCOME_UNCERTAIN,
    }
)


class ActionOutcome(StrEnum):
    """Result certainty kept separately from the action phase."""

    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    OUTCOME_UNCERTAIN = "outcome_uncertain"


__all__ = [
    "TERMINAL_ACTION_STATUSES",
    "TERMINAL_EXECUTION_ATTEMPT_STATUSES",
    "ActionOutcome",
    "ActionStatus",
    "ExecutionAttemptStatus",
]
