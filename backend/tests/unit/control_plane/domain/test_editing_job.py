"""Editing job lifecycle: the closed transition graph and what each state carries."""

from __future__ import annotations

import pytest

from automation_tool.control_plane.domain.editing_job import (
    EditingJobStateMachine,
    EditingJobStatus,
    InvalidEditingJobTransition,
)


def test_invalid_transition_is_a_value_error() -> None:
    assert issubclass(InvalidEditingJobTransition, ValueError)


def test_the_first_release_has_exactly_six_states() -> None:
    """No PAUSED (a 5-55s render has no pause story) and no OUTCOME_UNCERTAIN
    (the output file is ours to inspect — a half-written mp4 is a failure)."""
    assert {status.value for status in EditingJobStatus} == {
        "queued",
        "running",
        "cancelling",
        "succeeded",
        "failed",
        "cancelled",
    }


def test_terminal_states_have_no_way_out() -> None:
    terminal = EditingJobStateMachine.terminal_statuses()
    assert terminal == frozenset(
        {EditingJobStatus.SUCCEEDED, EditingJobStatus.FAILED, EditingJobStatus.CANCELLED}
    )
    for status in terminal:
        assert EditingJobStateMachine.allowed_targets(status) == frozenset()
        assert EditingJobStateMachine.is_terminal(status)


def test_a_live_state_is_not_terminal() -> None:
    for status in (
        EditingJobStatus.QUEUED,
        EditingJobStatus.RUNNING,
        EditingJobStatus.CANCELLING,
    ):
        assert not EditingJobStateMachine.is_terminal(status)


def test_is_terminal_rejects_a_foreign_value_without_raising() -> None:
    assert not EditingJobStateMachine.is_terminal("succeeded")
    assert not EditingJobStateMachine.is_terminal(None)


@pytest.mark.parametrize(
    ("current", "targets"),
    [
        (
            EditingJobStatus.QUEUED,
            {EditingJobStatus.RUNNING, EditingJobStatus.CANCELLING, EditingJobStatus.FAILED},
        ),
        (
            EditingJobStatus.RUNNING,
            {
                EditingJobStatus.CANCELLING,
                EditingJobStatus.SUCCEEDED,
                EditingJobStatus.FAILED,
            },
        ),
        (
            EditingJobStatus.CANCELLING,
            {
                EditingJobStatus.SUCCEEDED,
                EditingJobStatus.FAILED,
                EditingJobStatus.CANCELLED,
            },
        ),
    ],
)
def test_the_transition_graph_is_exactly_this(
    current: EditingJobStatus, targets: set[EditingJobStatus]
) -> None:
    assert EditingJobStateMachine.allowed_targets(current) == frozenset(targets)


def test_cancelling_may_still_land_on_success() -> None:
    """Cancellation is cooperative: the request can race a finished render."""
    assert (
        EditingJobStateMachine.transition(EditingJobStatus.CANCELLING, EditingJobStatus.SUCCEEDED)
        is EditingJobStatus.SUCCEEDED
    )


@pytest.mark.parametrize(
    ("current", "targets"),
    [
        (
            EditingJobStatus.QUEUED,
            {EditingJobStatus.RUNNING, EditingJobStatus.CANCELLING, EditingJobStatus.FAILED},
        ),
        (
            EditingJobStatus.RUNNING,
            {
                EditingJobStatus.CANCELLING,
                EditingJobStatus.SUCCEEDED,
                EditingJobStatus.FAILED,
            },
        ),
        (
            EditingJobStatus.CANCELLING,
            {
                EditingJobStatus.SUCCEEDED,
                EditingJobStatus.FAILED,
                EditingJobStatus.CANCELLED,
            },
        ),
    ],
)
def test_can_transition_confirms_every_legal_edge(
    current: EditingJobStatus, targets: set[EditingJobStatus]
) -> None:
    """Every other test only ever feeds `can_transition` an illegal or a
    foreign pair, so a `can_transition` that hard-coded `False` would pass
    all of them silently. This is the only place that asks it for `True`."""
    for target in targets:
        assert EditingJobStateMachine.can_transition(current, target) is True
        assert EditingJobStateMachine.transition(current, target) is target


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (EditingJobStatus.QUEUED, EditingJobStatus.SUCCEEDED),
        (EditingJobStatus.QUEUED, EditingJobStatus.CANCELLED),
        (EditingJobStatus.RUNNING, EditingJobStatus.QUEUED),
        (EditingJobStatus.RUNNING, EditingJobStatus.CANCELLED),
        (EditingJobStatus.CANCELLING, EditingJobStatus.RUNNING),
        (EditingJobStatus.SUCCEEDED, EditingJobStatus.FAILED),
        (EditingJobStatus.FAILED, EditingJobStatus.QUEUED),
        (EditingJobStatus.CANCELLED, EditingJobStatus.RUNNING),
        (EditingJobStatus.QUEUED, EditingJobStatus.QUEUED),
    ],
)
def test_an_illegal_transition_is_refused(
    current: EditingJobStatus, target: EditingJobStatus
) -> None:
    assert not EditingJobStateMachine.can_transition(current, target)
    with pytest.raises(InvalidEditingJobTransition):
        EditingJobStateMachine.transition(current, target)


def test_a_lost_worker_does_not_send_a_running_job_back_to_the_queue() -> None:
    """ffmpeg has no checkpoint; resuming would be a lie. Re-running is a new job."""
    assert EditingJobStatus.QUEUED not in EditingJobStateMachine.allowed_targets(
        EditingJobStatus.RUNNING
    )


@pytest.mark.parametrize("value", ["running", None, 1])
def test_a_foreign_value_is_not_a_state(value: object) -> None:
    assert not EditingJobStateMachine.can_transition(value, EditingJobStatus.RUNNING)
    assert not EditingJobStateMachine.can_transition(EditingJobStatus.QUEUED, value)
    with pytest.raises(InvalidEditingJobTransition):
        EditingJobStateMachine.allowed_targets(value)
    with pytest.raises(InvalidEditingJobTransition):
        EditingJobStateMachine.transition(value, EditingJobStatus.RUNNING)
    with pytest.raises(InvalidEditingJobTransition):
        EditingJobStateMachine.transition(EditingJobStatus.QUEUED, value)
