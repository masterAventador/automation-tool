from __future__ import annotations

from itertools import product

import pytest

from automation_tool.control_plane.domain.task_state_machine import (
    InvalidTaskTransition,
    TaskStateMachine,
    TaskStatus,
)

EXPECTED_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.DRAFT: frozenset({TaskStatus.VALIDATING}),
    TaskStatus.VALIDATING: frozenset(
        {
            TaskStatus.AWAITING_DEVICE,
            TaskStatus.CANCELLING,
            TaskStatus.FAILED,
        }
    ),
    TaskStatus.AWAITING_DEVICE: frozenset(
        {
            TaskStatus.AWAITING_PLATFORM_LOGIN,
            TaskStatus.CANCELLING,
            TaskStatus.FAILED,
        }
    ),
    TaskStatus.AWAITING_PLATFORM_LOGIN: frozenset(
        {
            TaskStatus.DISCOVERING_TARGETS,
            TaskStatus.CANCELLING,
            TaskStatus.FAILED,
        }
    ),
    TaskStatus.DISCOVERING_TARGETS: frozenset(
        {
            TaskStatus.AWAITING_CONFIRMATION,
            TaskStatus.AWAITING_HUMAN,
            TaskStatus.CANCELLING,
            TaskStatus.FAILED,
        }
    ),
    TaskStatus.AWAITING_CONFIRMATION: frozenset(
        {
            TaskStatus.DISCOVERING_TARGETS,
            TaskStatus.QUEUED,
            TaskStatus.CANCELLING,
        }
    ),
    TaskStatus.QUEUED: frozenset(
        {
            TaskStatus.AWAITING_DEVICE,
            TaskStatus.RUNNING,
            TaskStatus.CANCELLING,
            TaskStatus.FAILED,
        }
    ),
    TaskStatus.RUNNING: frozenset(
        {
            TaskStatus.PAUSED,
            TaskStatus.AWAITING_HUMAN,
            TaskStatus.CANCELLING,
            TaskStatus.SUCCEEDED,
            TaskStatus.PARTIALLY_SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.OUTCOME_UNCERTAIN,
        }
    ),
    TaskStatus.PAUSED: frozenset(
        {
            TaskStatus.RUNNING,
            TaskStatus.AWAITING_HUMAN,
            TaskStatus.CANCELLING,
        }
    ),
    TaskStatus.AWAITING_HUMAN: frozenset(
        {
            TaskStatus.DISCOVERING_TARGETS,
            TaskStatus.RUNNING,
            TaskStatus.CANCELLING,
            TaskStatus.FAILED,
            TaskStatus.OUTCOME_UNCERTAIN,
        }
    ),
    TaskStatus.CANCELLING: frozenset(
        {
            TaskStatus.SUCCEEDED,
            TaskStatus.PARTIALLY_SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.OUTCOME_UNCERTAIN,
        }
    ),
    TaskStatus.SUCCEEDED: frozenset(),
    TaskStatus.PARTIALLY_SUCCEEDED: frozenset(),
    TaskStatus.FAILED: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
    TaskStatus.OUTCOME_UNCERTAIN: frozenset(),
}


def test_status_wire_values_and_terminal_set_are_exact() -> None:
    assert [status.value for status in TaskStatus] == [
        "draft",
        "validating",
        "awaiting_device",
        "awaiting_platform_login",
        "discovering_targets",
        "awaiting_confirmation",
        "queued",
        "running",
        "paused",
        "awaiting_human",
        "cancelling",
        "succeeded",
        "partially_succeeded",
        "failed",
        "cancelled",
        "outcome_uncertain",
    ]
    assert TaskStateMachine.terminal_statuses() == frozenset(
        {
            TaskStatus.SUCCEEDED,
            TaskStatus.PARTIALLY_SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.OUTCOME_UNCERTAIN,
        }
    )


def test_every_status_pair_matches_the_explicit_transition_matrix() -> None:
    assert set(EXPECTED_TRANSITIONS) == set(TaskStatus)

    for current, target in product(TaskStatus, repeat=2):
        expected = target in EXPECTED_TRANSITIONS[current]
        assert TaskStateMachine.can_transition(current, target) is expected
        if expected:
            assert TaskStateMachine.transition(current, target) is target
        else:
            with pytest.raises(InvalidTaskTransition):
                TaskStateMachine.transition(current, target)


def test_allowed_targets_are_immutable_exact_views() -> None:
    for status, targets in EXPECTED_TRANSITIONS.items():
        actual = TaskStateMachine.allowed_targets(status)
        assert actual == targets
        assert isinstance(actual, frozenset)


def test_terminal_states_have_no_outbound_or_self_transition() -> None:
    for terminal in TaskStateMachine.terminal_statuses():
        assert TaskStateMachine.is_terminal(terminal) is True
        assert TaskStateMachine.allowed_targets(terminal) == frozenset()
        for target in TaskStatus:
            assert TaskStateMachine.can_transition(terminal, target) is False

    for status in TaskStatus:
        assert TaskStateMachine.can_transition(status, status) is False
        assert TaskStateMachine.is_terminal(status) is (
            status in TaskStateMachine.terminal_statuses()
        )


def test_cancellation_requires_acknowledgement_and_preserves_completion_races() -> None:
    assert TaskStateMachine.can_transition(TaskStatus.RUNNING, TaskStatus.CANCELLED) is False
    assert TaskStateMachine.can_transition(TaskStatus.RUNNING, TaskStatus.CANCELLING) is True
    assert TaskStateMachine.allowed_targets(TaskStatus.CANCELLING) == frozenset(
        {
            TaskStatus.SUCCEEDED,
            TaskStatus.PARTIALLY_SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.OUTCOME_UNCERTAIN,
        }
    )


def test_outcome_uncertain_is_only_reachable_after_execution_or_handoff() -> None:
    sources = {
        status
        for status in TaskStatus
        if TaskStateMachine.can_transition(status, TaskStatus.OUTCOME_UNCERTAIN)
    }
    assert sources == {
        TaskStatus.RUNNING,
        TaskStatus.AWAITING_HUMAN,
        TaskStatus.CANCELLING,
    }


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("running", TaskStatus.SUCCEEDED),
        (TaskStatus.RUNNING, "succeeded"),
        (None, TaskStatus.FAILED),
        (TaskStatus.DRAFT, object()),
    ],
)
def test_untyped_inputs_fail_closed_without_string_coercion(
    current: object,
    target: object,
) -> None:
    assert TaskStateMachine.can_transition(current, target) is False
    with pytest.raises(InvalidTaskTransition) as captured:
        TaskStateMachine.transition(current, target)

    assert str(captured.value) == "Task state transition is invalid"
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_allowed_targets_rejects_an_untyped_status_with_the_same_safe_error() -> None:
    with pytest.raises(InvalidTaskTransition, match=r"^Task state transition is invalid$"):
        TaskStateMachine.allowed_targets("running")

    assert TaskStateMachine.is_terminal("succeeded") is False


def test_rejection_does_not_reflect_states_or_expose_exception_details() -> None:
    with pytest.raises(InvalidTaskTransition) as captured:
        TaskStateMachine.transition(TaskStatus.DRAFT, TaskStatus.RUNNING)

    assert str(captured.value) == "Task state transition is invalid"
    assert "draft" not in repr(captured.value).lower()
    assert "running" not in repr(captured.value).lower()
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
