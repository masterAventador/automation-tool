"""Editing job lifecycle: the closed transition graph and what each state carries."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from automation_tool.control_plane.domain.editing_job import (
    EditingJob,
    EditingJobFailureCode,
    EditingJobId,
    EditingJobStateMachine,
    EditingJobStatus,
    InvalidEditingJobModel,
    InvalidEditingJobTransition,
)
from automation_tool.control_plane.domain.editing_project import EditingProjectId
from automation_tool.control_plane.domain.resource_ids import ArtifactId
from automation_tool.control_plane.domain.timeline import TimelineId


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


def test_failure_codes_cover_the_render_failure_matrix() -> None:
    assert {code.value for code in EditingJobFailureCode} == {
        "invalid_timeline",
        "material_unavailable",
        "material_unsupported",
        "font_unavailable",
        "render_failed",
        "resource_exhausted",
        "permission_denied",
        "worker_lost",
    }


_CREATED = datetime(2026, 7, 29, 10, 0, tzinfo=UTC)
_UPDATED = datetime(2026, 7, 29, 10, 1, tzinfo=UTC)


def _job(**overrides: object) -> EditingJob:
    defaults: dict[str, object] = {
        "job_id": EditingJobId.new(),
        "project_id": EditingProjectId.new(),
        "timeline_id": TimelineId.new(),
        "timeline_revision": 1,
        "status": EditingJobStatus.QUEUED,
        "failure_code": None,
        "output_artifact_id": None,
        "created_at": _CREATED,
        "updated_at": _CREATED,
    }
    defaults.update(overrides)
    return EditingJob(**defaults)  # type: ignore[arg-type]


def test_a_queued_job_records_which_revision_it_will_render() -> None:
    """The timeline can be edited while the job runs; the job pins a revision."""
    assert _job(timeline_revision=7).timeline_revision == 7


@pytest.mark.parametrize(
    "status",
    [EditingJobStatus.QUEUED, EditingJobStatus.RUNNING, EditingJobStatus.CANCELLING],
)
def test_a_job_still_in_flight_has_neither_an_output_nor_a_reason(
    status: EditingJobStatus,
) -> None:
    _job(status=status)
    with pytest.raises(InvalidEditingJobModel):
        _job(status=status, output_artifact_id=ArtifactId.new())
    with pytest.raises(InvalidEditingJobModel):
        _job(status=status, failure_code=EditingJobFailureCode.RENDER_FAILED)


def test_a_succeeded_job_must_point_at_what_it_produced() -> None:
    artifact_id = ArtifactId.new()
    job = _job(status=EditingJobStatus.SUCCEEDED, output_artifact_id=artifact_id)
    assert job.output_artifact_id is artifact_id
    with pytest.raises(InvalidEditingJobModel):
        _job(status=EditingJobStatus.SUCCEEDED)
    with pytest.raises(InvalidEditingJobModel):
        _job(
            status=EditingJobStatus.SUCCEEDED,
            output_artifact_id=artifact_id,
            failure_code=EditingJobFailureCode.RENDER_FAILED,
        )


def test_a_failed_job_must_say_why() -> None:
    job = _job(
        status=EditingJobStatus.FAILED,
        failure_code=EditingJobFailureCode.MATERIAL_UNAVAILABLE,
    )
    assert job.failure_code is EditingJobFailureCode.MATERIAL_UNAVAILABLE
    with pytest.raises(InvalidEditingJobModel):
        _job(status=EditingJobStatus.FAILED)
    with pytest.raises(InvalidEditingJobModel):
        _job(
            status=EditingJobStatus.FAILED,
            failure_code=EditingJobFailureCode.RENDER_FAILED,
            output_artifact_id=ArtifactId.new(),
        )


def test_a_cancelled_job_carries_neither() -> None:
    _job(status=EditingJobStatus.CANCELLED)
    with pytest.raises(InvalidEditingJobModel):
        _job(status=EditingJobStatus.CANCELLED, failure_code=EditingJobFailureCode.WORKER_LOST)
    with pytest.raises(InvalidEditingJobModel):
        _job(status=EditingJobStatus.CANCELLED, output_artifact_id=ArtifactId.new())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("job_id", "not-an-id"),
        ("project_id", "not-an-id"),
        ("timeline_id", "not-an-id"),
        # Sibling resource ids: structurally valid, wrong type. A bare string
        # is rejected however wide the guard is, so only these pin its
        # narrowness -- and these three fields sit side by side with the same
        # underlying representation, so a repository hydrating columns in the
        # wrong order is exactly what the narrowness is for.
        ("job_id", ArtifactId.new()),
        ("project_id", TimelineId.new()),
        ("timeline_id", EditingProjectId.new()),
        ("timeline_revision", 0),
        ("timeline_revision", 1.0),
        ("timeline_revision", True),
        ("status", "queued"),
        ("status", None),
        ("failure_code", "render_failed"),
        ("output_artifact_id", "not-an-id"),
        ("created_at", datetime(2026, 7, 29, 10, 0)),
        ("created_at", "2026-07-29T10:00:00Z"),
        ("created_at", datetime(2026, 7, 29, 10, 0, tzinfo=timezone(timedelta(hours=8)))),
        ("updated_at", datetime(2026, 7, 29, 10, 0)),
    ],
)
def test_job_structural_bounds_fail_closed(field: str, value: object) -> None:
    with pytest.raises(InvalidEditingJobModel):
        _job(**{field: value})


def test_the_output_reference_is_narrow_too() -> None:
    """Kept out of the table above on purpose.

    Under the default QUEUED status `_validate_facts_match_status` refuses any
    output at all, so a wrong-typed one there would prove the fact coupling,
    not the type narrowness. Only a SUCCEEDED job reaches the isinstance guard.
    """
    with pytest.raises(InvalidEditingJobModel):
        _job(status=EditingJobStatus.SUCCEEDED, output_artifact_id=TimelineId.new())


def test_invalid_model_is_a_value_error() -> None:
    """The API layer maps `ValueError` to 4xx; a silently widened base turns a
    domain rejection into a 500. Its sibling transition error already has this."""
    assert issubclass(InvalidEditingJobModel, ValueError)


def test_a_job_cannot_be_updated_before_it_was_created() -> None:
    _job(updated_at=_UPDATED)
    with pytest.raises(InvalidEditingJobModel):
        _job(created_at=_UPDATED, updated_at=_CREATED)


def test_a_render_walks_from_queued_to_a_finished_file() -> None:
    artifact_id = ArtifactId.new()
    job = _job()
    running = job.start(_UPDATED)
    assert running.status is EditingJobStatus.RUNNING
    done = running.succeed(artifact_id, _UPDATED)
    assert done.status is EditingJobStatus.SUCCEEDED
    assert done.output_artifact_id is artifact_id


def test_a_cancel_request_waits_for_the_worker_to_confirm() -> None:
    cancelling = _job().start(_UPDATED).request_cancel(_UPDATED)
    assert cancelling.status is EditingJobStatus.CANCELLING
    assert cancelling.confirm_cancelled(_UPDATED).status is EditingJobStatus.CANCELLED


def test_a_cancel_request_that_lost_the_race_still_records_the_file() -> None:
    artifact_id = ArtifactId.new()
    cancelling = _job().start(_UPDATED).request_cancel(_UPDATED)
    assert cancelling.succeed(artifact_id, _UPDATED).output_artifact_id is artifact_id


def test_a_failure_carries_its_reason_through_the_transition() -> None:
    failed = _job().start(_UPDATED).fail(EditingJobFailureCode.WORKER_LOST, _UPDATED)
    assert failed.status is EditingJobStatus.FAILED
    assert failed.failure_code is EditingJobFailureCode.WORKER_LOST


def test_every_transition_method_refuses_an_illegal_move() -> None:
    done = _job(status=EditingJobStatus.SUCCEEDED, output_artifact_id=ArtifactId.new())
    with pytest.raises(InvalidEditingJobTransition):
        done.start(_UPDATED)
    with pytest.raises(InvalidEditingJobTransition):
        done.request_cancel(_UPDATED)
    with pytest.raises(InvalidEditingJobTransition):
        done.fail(EditingJobFailureCode.RENDER_FAILED, _UPDATED)
    with pytest.raises(InvalidEditingJobTransition):
        done.confirm_cancelled(_UPDATED)
    with pytest.raises(InvalidEditingJobTransition):
        _job().succeed(ArtifactId.new(), _UPDATED)
    with pytest.raises(InvalidEditingJobTransition):
        _job().confirm_cancelled(_UPDATED)


def test_a_transition_refuses_a_timestamp_that_moves_backwards() -> None:
    with pytest.raises(InvalidEditingJobModel):
        _job(updated_at=_UPDATED).start(_CREATED)


def test_a_transition_refuses_a_malformed_timestamp_before_comparing_it() -> None:
    """A naive datetime cannot be compared (`<`) to the job's tz-aware
    `updated_at` — without validating the format first, this would raise a
    bare TypeError instead of the same InvalidEditingJobModel every other
    malformed timestamp produces."""
    with pytest.raises(InvalidEditingJobModel):
        _job().start(datetime(2026, 7, 29, 10, 1))


def test_succeeding_demands_a_real_artifact_id() -> None:
    with pytest.raises(InvalidEditingJobModel):
        _job().start(_UPDATED).succeed("not-an-id", _UPDATED)  # type: ignore[arg-type]


def test_failing_demands_a_real_failure_code() -> None:
    with pytest.raises(InvalidEditingJobModel):
        _job().start(_UPDATED).fail("render_failed", _UPDATED)  # type: ignore[arg-type]
