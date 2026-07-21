from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from sqlalchemy.engine import RowMapping

from automation_tool.control_plane.application.task_event_convergence import (
    PendingTaskEvent,
    SystemTaskEventConvergenceClock,
    TaskEventConvergenceRejected,
    TaskEventConvergenceResult,
    TaskEventConvergenceService,
    TaskEventConvergenceUnavailable,
)
from automation_tool.control_plane.domain import (
    ActionId,
    ActionOutcome,
    ActionStatus,
    ExecutionAttemptStatus,
    TaskEventType,
    TaskId,
    TaskSnapshotProjection,
    TaskStatus,
)
from automation_tool.control_plane.infrastructure.database import (
    task_event_convergence_repository,
)
from automation_tool.protocol import (
    ACTION_RESULT_EVIDENCE_VERSION,
    ActionResultEvidence,
    TaskEventEnvelope,
    parse_executor_message,
)

NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
INSTALLATION_ID = "123e4567-e89b-42d3-a456-426614174003"
EXECUTOR_ID = "123e4567-e89b-42d3-a456-426614174004"
TASK_ID = "123e4567-e89b-42d3-a456-426614174005"
ATTEMPT_ID = "123e4567-e89b-42d3-a456-426614174006"
ACTION_ID = "123e4567-e89b-42d3-a456-426614174007"


@dataclass
class MutableClock:
    value: object = NOW
    raises: bool = False

    def now(self) -> datetime:
        if self.raises:
            raise RuntimeError("private clock failure")
        return cast(datetime, self.value)


class RecordingRepository:
    def __init__(self) -> None:
        self.pending: list[PendingTaskEvent] = []
        self.failure: Exception | None = None

    async def converge(self, pending: PendingTaskEvent) -> TaskEventConvergenceResult:
        if self.failure is not None:
            raise self.failure
        self.pending.append(pending)
        return TaskEventConvergenceResult(
            snapshot=TaskSnapshotProjection(
                task_id=TaskId.parse(TASK_ID),
                status=pending.target_task_status or TaskStatus.RUNNING,
                revision=2,
                last_event_sequence=pending.message.sequence,
                updated_at=pending.received_at,
            ),
            duplicate=False,
        )


def event(
    message_type: str,
    *,
    sequence: int = 1,
    payload: object | None = None,
    sent_at: datetime = NOW,
    deadline_at: datetime = NOW + timedelta(seconds=30),
) -> TaskEventEnvelope:
    parsed = parse_executor_message(
        json.dumps(
            {
                "protocol_version": "1.0",
                "message_id": f"423e4567-e89b-42d3-a456-{sequence:012d}",
                "message_type": message_type,
                "sent_at": sent_at.isoformat().replace("+00:00", "Z"),
                "deadline_at": deadline_at.isoformat().replace("+00:00", "Z"),
                "installation_id": INSTALLATION_ID,
                "executor_id": EXECUTOR_ID,
                "correlation_id": "323e4567-e89b-42d3-a456-426614174002",
                "idempotency_key": f"task:event:{message_type}:{sequence}",
                "sequence": sequence,
                "payload": {} if payload is None else payload,
                "task_id": TASK_ID,
                "execution_attempt_id": ATTEMPT_ID,
            },
            separators=(",", ":"),
        )
    )
    assert isinstance(parsed, TaskEventEnvelope)
    return parsed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message_type", "event_type", "task_status", "attempt_status", "payload"),
    (
        (
            "task.started",
            TaskEventType.TASK_STARTED,
            TaskStatus.RUNNING,
            ExecutionAttemptStatus.RUNNING,
            {},
        ),
        ("step.started", TaskEventType.STEP_STARTED, None, None, {}),
        (
            "step.progress",
            TaskEventType.STEP_PROGRESS,
            None,
            None,
            {"progress_percent": 50},
        ),
        ("step.completed", TaskEventType.STEP_COMPLETED, None, None, {}),
        ("step.failed", TaskEventType.STEP_FAILED, None, None, {}),
        (
            "session.login_required",
            TaskEventType.TASK_AWAITING_PLATFORM_LOGIN,
            TaskStatus.AWAITING_PLATFORM_LOGIN,
            ExecutionAttemptStatus.AWAITING_HUMAN,
            {},
        ),
        (
            "handoff.requested",
            TaskEventType.TASK_AWAITING_HUMAN,
            TaskStatus.AWAITING_HUMAN,
            ExecutionAttemptStatus.AWAITING_HUMAN,
            {},
        ),
        (
            "task.paused",
            TaskEventType.TASK_PAUSED,
            TaskStatus.PAUSED,
            ExecutionAttemptStatus.PAUSED,
            {},
        ),
        (
            "task.resumed",
            TaskEventType.TASK_RESUMED,
            TaskStatus.RUNNING,
            ExecutionAttemptStatus.RUNNING,
            {},
        ),
        (
            "task.cancelled",
            TaskEventType.TASK_CANCELLED,
            TaskStatus.CANCELLED,
            ExecutionAttemptStatus.CANCELLED,
            {},
        ),
        (
            "task.completed",
            TaskEventType.TASK_COMPLETED,
            TaskStatus.SUCCEEDED,
            ExecutionAttemptStatus.SUCCEEDED,
            {},
        ),
        (
            "task.partially_completed",
            TaskEventType.TASK_PARTIALLY_COMPLETED,
            TaskStatus.PARTIALLY_SUCCEEDED,
            ExecutionAttemptStatus.PARTIALLY_SUCCEEDED,
            {},
        ),
        (
            "task.failed",
            TaskEventType.TASK_FAILED,
            TaskStatus.FAILED,
            ExecutionAttemptStatus.FAILED,
            {},
        ),
        (
            "task.outcome_uncertain",
            TaskEventType.TASK_OUTCOME_UNCERTAIN,
            TaskStatus.OUTCOME_UNCERTAIN,
            ExecutionAttemptStatus.OUTCOME_UNCERTAIN,
            {},
        ),
    ),
)
async def test_service_maps_every_executor_event_to_a_closed_convergence_plan(
    message_type: str,
    event_type: TaskEventType,
    task_status: TaskStatus | None,
    attempt_status: ExecutionAttemptStatus | None,
    payload: dict[str, object],
) -> None:
    repository = RecordingRepository()
    service = TaskEventConvergenceService(repository=repository, clock=MutableClock())
    message = event(message_type, payload=payload)

    result = await service.receive(message)

    assert result.duplicate is False
    assert len(repository.pending) == 1
    pending = repository.pending[0]
    assert pending.message is message
    assert pending.event_type is event_type
    assert pending.target_task_status is task_status
    assert pending.target_attempt_status is attempt_status
    assert pending.action_id is None
    assert pending.target_action_status is None
    assert pending.target_action_outcome is None
    assert pending.progress_percent == (50 if message_type == "step.progress" else None)
    assert pending.received_at == NOW
    assert len(pending.source_fingerprint) == 32


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message_type", "payload", "action_status", "action_outcome"),
    (
        ("step.started", {"action_id": ACTION_ID}, ActionStatus.DISPATCHED, None),
        (
            "step.progress",
            {"action_id": ACTION_ID, "progress_percent": 25},
            None,
            None,
        ),
        (
            "step.completed",
            {"action_id": ACTION_ID},
            ActionStatus.VERIFIED,
            ActionOutcome.SUCCEEDED,
        ),
        (
            "step.failed",
            {"action_id": ACTION_ID},
            ActionStatus.VERIFIED,
            ActionOutcome.FAILED,
        ),
    ),
)
async def test_action_projection_requires_an_explicit_bound_action_id(
    message_type: str,
    payload: dict[str, object],
    action_status: ActionStatus | None,
    action_outcome: ActionOutcome | None,
) -> None:
    repository = RecordingRepository()
    service = TaskEventConvergenceService(repository=repository, clock=MutableClock())

    await service.receive(event(message_type, payload=payload))

    pending = repository.pending[0]
    assert str(pending.action_id) == ACTION_ID
    assert pending.target_action_status is action_status
    assert pending.target_action_outcome is action_outcome


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message_type", "evidence", "action_status", "action_outcome"),
    (
        (
            "step.completed",
            ActionResultEvidence.COMMENT_CONFIRMED,
            ActionStatus.VERIFIED,
            ActionOutcome.SUCCEEDED,
        ),
        (
            "step.failed",
            ActionResultEvidence.LOGIN_REQUIRED,
            ActionStatus.VERIFIED,
            ActionOutcome.FAILED,
        ),
        (
            "task.outcome_uncertain",
            ActionResultEvidence.DISPATCH_TIMED_OUT,
            ActionStatus.OUTCOME_UNCERTAIN,
            ActionOutcome.OUTCOME_UNCERTAIN,
        ),
    ),
)
async def test_action_projection_accepts_only_versioned_outcome_evidence(
    message_type: str,
    evidence: ActionResultEvidence,
    action_status: ActionStatus,
    action_outcome: ActionOutcome,
) -> None:
    repository = RecordingRepository()
    service = TaskEventConvergenceService(repository=repository, clock=MutableClock())

    await service.receive(
        event(
            message_type,
            payload={
                "action_id": ACTION_ID,
                "evidence": evidence.value,
                "evidence_version": ACTION_RESULT_EVIDENCE_VERSION,
            },
        )
    )

    pending = repository.pending[0]
    assert pending.target_action_status is action_status
    assert pending.target_action_outcome is action_outcome
    assert pending.action_evidence is evidence


@pytest.mark.asyncio
async def test_uncertain_action_projection_requires_a_dispatched_pending_action() -> None:
    repository = RecordingRepository()
    service = TaskEventConvergenceService(repository=repository, clock=MutableClock())
    await service.receive(event("task.outcome_uncertain", payload={"action_id": ACTION_ID}))
    pending = repository.pending[0]
    assert pending.action_evidence is ActionResultEvidence.FINAL_STATE_UNCONFIRMED
    assert task_event_convergence_repository._validate_action(
        cast(RowMapping, {"status": "dispatched", "outcome": "pending"}),
        pending,
    ) == (ActionStatus.OUTCOME_UNCERTAIN, ActionOutcome.OUTCOME_UNCERTAIN)
    with pytest.raises(TaskEventConvergenceRejected):
        task_event_convergence_repository._validate_action(
            cast(RowMapping, {"status": "prepared", "outcome": "pending"}),
            pending,
        )


@pytest.mark.asyncio
async def test_action_projection_rejects_partial_malformed_or_cross_outcome_evidence() -> None:
    repository = RecordingRepository()
    service = TaskEventConvergenceService(repository=repository, clock=MutableClock())
    invalid_payloads = (
        {
            "action_id": ACTION_ID,
            "evidence": "comment_confirmed",
        },
        {
            "action_id": ACTION_ID,
            "evidence_version": ACTION_RESULT_EVIDENCE_VERSION,
        },
        {
            "evidence": "comment_confirmed",
            "evidence_version": ACTION_RESULT_EVIDENCE_VERSION,
        },
        {
            "action_id": ACTION_ID,
            "evidence": 1,
            "evidence_version": ACTION_RESULT_EVIDENCE_VERSION,
        },
        {
            "action_id": ACTION_ID,
            "evidence": "comment_confirmed",
            "evidence_version": "private-version",
        },
        {
            "action_id": ACTION_ID,
            "evidence": "private-evidence",
            "evidence_version": ACTION_RESULT_EVIDENCE_VERSION,
        },
    )
    for payload in invalid_payloads:
        with pytest.raises(TaskEventConvergenceRejected):
            await service.receive(event("step.completed", payload=payload))

    for message_type, evidence in (
        ("step.completed", "login_required"),
        ("step.failed", "comment_confirmed"),
        ("task.outcome_uncertain", "comment_confirmed"),
    ):
        with pytest.raises(TaskEventConvergenceRejected):
            await service.receive(
                event(
                    message_type,
                    payload={
                        "action_id": ACTION_ID,
                        "evidence": evidence,
                        "evidence_version": ACTION_RESULT_EVIDENCE_VERSION,
                    },
                )
            )
    with pytest.raises(TaskEventConvergenceRejected):
        await service.receive(
            event(
                "task.outcome_uncertain",
                payload={
                    "evidence": "dispatch_timed_out",
                    "evidence_version": ACTION_RESULT_EVIDENCE_VERSION,
                },
            )
        )
    assert repository.pending == []


@pytest.mark.asyncio
async def test_service_rejects_expired_unknown_or_unsafe_payloads_without_persistence() -> None:
    repository = RecordingRepository()
    service = TaskEventConvergenceService(repository=repository, clock=MutableClock())
    invalid = (
        event(
            "task.started",
            sent_at=NOW - timedelta(minutes=1),
            deadline_at=NOW - timedelta(seconds=1),
        ),
        event("task.started", payload={"private": True}),
        event("step.progress", payload={}),
        event("step.progress", payload={"progress_percent": True}),
        event("step.progress", payload={"progress_percent": 101}),
        event("step.started", payload={"action_id": "not-a-uuid"}),
        event("step.completed", payload={"progress_percent": 100}),
    )

    for message in invalid:
        with pytest.raises(
            TaskEventConvergenceRejected,
            match=r"^Task event convergence is rejected$",
        ):
            await service.receive(message)
    assert repository.pending == []


@pytest.mark.asyncio
async def test_service_wraps_clock_and_unexpected_repository_failures_without_secrets() -> None:
    repository = RecordingRepository()
    invalid_clocks = (MutableClock(value=object()), MutableClock(raises=True))
    for clock in invalid_clocks:
        service = TaskEventConvergenceService(repository=repository, clock=clock)
        with pytest.raises(TaskEventConvergenceUnavailable) as captured:
            await service.receive(event("task.started"))
        assert "private" not in str(captured.value)

    service = TaskEventConvergenceService(repository=repository, clock=MutableClock())
    repository.failure = RuntimeError("private database failure")
    with pytest.raises(
        TaskEventConvergenceUnavailable,
        match=r"^Task event convergence is unavailable$",
    ) as captured:
        await service.receive(event("task.started"))
    assert captured.value.__cause__ is None
    assert "private" not in str(captured.value)


@pytest.mark.asyncio
async def test_service_and_internal_models_reject_wrong_runtime_types() -> None:
    repository = RecordingRepository()
    assert SystemTaskEventConvergenceClock().now().utcoffset() == timedelta(0)
    with pytest.raises(TaskEventConvergenceRejected):
        TaskEventConvergenceService(repository=cast(RecordingRepository, object()))

    service = TaskEventConvergenceService(repository=repository, clock=MutableClock())
    with pytest.raises(TaskEventConvergenceRejected):
        await service.receive(cast(TaskEventEnvelope, object()))
    await service.receive(event("step.progress", payload={"progress_percent": 50}))
    valid = repository.pending[0]
    fallback = replace(
        valid,
        action_id=ActionId.parse(ACTION_ID),
        target_action_status=ActionStatus.VERIFIED,
        target_action_outcome=ActionOutcome.SUCCEEDED,
        action_evidence=None,
    )
    assert fallback.action_evidence is ActionResultEvidence.EXECUTOR_REPORTED_SUCCESS
    invalid_pending: tuple[Callable[[], PendingTaskEvent], ...] = (
        lambda: replace(valid, message=cast(TaskEventEnvelope, object())),
        lambda: replace(valid, event_type=cast(TaskEventType, "step.progress")),
        lambda: replace(valid, target_task_status=cast(TaskStatus, "running")),
        lambda: replace(
            valid,
            target_attempt_status=cast(ExecutionAttemptStatus, "running"),
        ),
        lambda: replace(valid, action_id=cast(ActionId, ACTION_ID)),
        lambda: replace(valid, target_action_status=cast(ActionStatus, "verified")),
        lambda: replace(valid, target_action_outcome=cast(ActionOutcome, "failed")),
        lambda: replace(valid, action_id=None, target_action_status=ActionStatus.VERIFIED),
        lambda: replace(valid, action_id=None, target_action_outcome=ActionOutcome.FAILED),
        lambda: replace(
            valid,
            action_id=None,
            action_evidence=ActionResultEvidence.LOGIN_REQUIRED,
        ),
        lambda: replace(
            fallback,
            action_evidence=ActionResultEvidence.LOGIN_REQUIRED,
        ),
        lambda: replace(
            fallback,
            action_evidence=cast(ActionResultEvidence, "executor_reported_success"),
        ),
        lambda: replace(valid, progress_percent=cast(int, True)),
        lambda: replace(valid, progress_percent=101),
        lambda: replace(valid, source_fingerprint=cast(bytes, bytearray(32))),
        lambda: replace(valid, source_fingerprint=b"short"),
        lambda: replace(valid, received_at=cast(datetime, object())),
        lambda: replace(valid, received_at=datetime(2026, 7, 18, 12, 0)),
    )
    for create_invalid in invalid_pending:
        with pytest.raises(TaskEventConvergenceRejected):
            create_invalid()

    snapshot = TaskSnapshotProjection(
        task_id=TaskId.parse(TASK_ID),
        status=TaskStatus.RUNNING,
        revision=2,
        last_event_sequence=1,
        updated_at=NOW,
    )
    with pytest.raises(TaskEventConvergenceRejected):
        TaskEventConvergenceResult(
            snapshot=cast(TaskSnapshotProjection, object()),
            duplicate=False,
        )
    with pytest.raises(TaskEventConvergenceRejected):
        TaskEventConvergenceResult(snapshot=snapshot, duplicate=cast(bool, 1))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    (TaskEventConvergenceRejected(), TaskEventConvergenceUnavailable()),
)
async def test_service_preserves_closed_repository_failure_categories(failure: Exception) -> None:
    repository = RecordingRepository()
    repository.failure = failure
    service = TaskEventConvergenceService(repository=repository, clock=MutableClock())

    with pytest.raises(type(failure)) as captured:
        await service.receive(event("task.started"))

    assert captured.value is failure
