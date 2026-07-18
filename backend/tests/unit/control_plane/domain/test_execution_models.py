from enum import StrEnum

from automation_tool.control_plane.domain import (
    TERMINAL_ACTION_STATUSES,
    TERMINAL_EXECUTION_ATTEMPT_STATUSES,
    ActionOutcome,
    ActionStatus,
    ExecutionAttemptStatus,
)


def test_execution_attempt_statuses_and_terminals_are_an_exact_immutable_contract() -> None:
    assert issubclass(ExecutionAttemptStatus, StrEnum)
    assert tuple(status.value for status in ExecutionAttemptStatus) == (
        "pending",
        "offered",
        "accepted",
        "running",
        "paused",
        "awaiting_human",
        "cancelling",
        "succeeded",
        "partially_succeeded",
        "failed",
        "cancelled",
        "rejected",
        "expired",
        "outcome_uncertain",
    )
    assert (
        frozenset(
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
        == TERMINAL_EXECUTION_ATTEMPT_STATUSES
    )


def test_action_status_and_outcome_contracts_separate_phase_from_result() -> None:
    assert tuple(status.value for status in ActionStatus) == (
        "planned",
        "authorized",
        "prepared",
        "dispatched",
        "verified",
        "cancelled",
        "outcome_uncertain",
    )
    assert (
        frozenset(
            {
                ActionStatus.VERIFIED,
                ActionStatus.CANCELLED,
                ActionStatus.OUTCOME_UNCERTAIN,
            }
        )
        == TERMINAL_ACTION_STATUSES
    )
    assert tuple(outcome.value for outcome in ActionOutcome) == (
        "pending",
        "succeeded",
        "failed",
        "cancelled",
        "outcome_uncertain",
    )
