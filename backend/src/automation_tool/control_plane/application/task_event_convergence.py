"""Validate Executor events and request one atomic durable convergence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from automation_tool.control_plane.domain import (
    ActionId,
    ActionOutcome,
    ActionStatus,
    ExecutionAttemptStatus,
    TaskEventType,
    TaskSnapshotProjection,
    TaskStatus,
)
from automation_tool.protocol import (
    ACTION_RESULT_EVIDENCE_VERSION,
    FAILED_ACTION_RESULT_EVIDENCE,
    SUCCESS_ACTION_RESULT_EVIDENCE,
    UNCERTAIN_ACTION_RESULT_EVIDENCE,
    ActionResultEvidence,
    TaskEventEnvelope,
)


class TaskEventConvergenceRejected(ValueError):
    """An event conflicts with the closed convergence contract."""

    def __init__(self) -> None:
        super().__init__("Task event convergence is rejected")


class TaskEventConvergenceUnavailable(RuntimeError):
    """The durable event projection cannot currently make safe progress."""

    def __init__(self) -> None:
        super().__init__("Task event convergence is unavailable")


class TaskEventConvergenceClock(Protocol):
    def now(self) -> datetime: ...


class SystemTaskEventConvergenceClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class _EventPlan:
    event_type: TaskEventType
    task_status: TaskStatus | None
    attempt_status: ExecutionAttemptStatus | None


_EVENT_PLANS: dict[str, _EventPlan] = {
    "task.started": _EventPlan(
        TaskEventType.TASK_STARTED,
        TaskStatus.RUNNING,
        ExecutionAttemptStatus.RUNNING,
    ),
    "step.started": _EventPlan(TaskEventType.STEP_STARTED, None, None),
    "step.progress": _EventPlan(TaskEventType.STEP_PROGRESS, None, None),
    "step.completed": _EventPlan(TaskEventType.STEP_COMPLETED, None, None),
    "step.failed": _EventPlan(TaskEventType.STEP_FAILED, None, None),
    "session.login_required": _EventPlan(
        TaskEventType.TASK_AWAITING_PLATFORM_LOGIN,
        TaskStatus.AWAITING_PLATFORM_LOGIN,
        ExecutionAttemptStatus.AWAITING_HUMAN,
    ),
    "handoff.requested": _EventPlan(
        TaskEventType.TASK_AWAITING_HUMAN,
        TaskStatus.AWAITING_HUMAN,
        ExecutionAttemptStatus.AWAITING_HUMAN,
    ),
    "task.paused": _EventPlan(
        TaskEventType.TASK_PAUSED,
        TaskStatus.PAUSED,
        ExecutionAttemptStatus.PAUSED,
    ),
    "task.resumed": _EventPlan(
        TaskEventType.TASK_RESUMED,
        TaskStatus.RUNNING,
        ExecutionAttemptStatus.RUNNING,
    ),
    "task.cancelled": _EventPlan(
        TaskEventType.TASK_CANCELLED,
        TaskStatus.CANCELLED,
        ExecutionAttemptStatus.CANCELLED,
    ),
    "task.completed": _EventPlan(
        TaskEventType.TASK_COMPLETED,
        TaskStatus.SUCCEEDED,
        ExecutionAttemptStatus.SUCCEEDED,
    ),
    "task.partially_completed": _EventPlan(
        TaskEventType.TASK_PARTIALLY_COMPLETED,
        TaskStatus.PARTIALLY_SUCCEEDED,
        ExecutionAttemptStatus.PARTIALLY_SUCCEEDED,
    ),
    "task.failed": _EventPlan(
        TaskEventType.TASK_FAILED,
        TaskStatus.FAILED,
        ExecutionAttemptStatus.FAILED,
    ),
    "task.outcome_uncertain": _EventPlan(
        TaskEventType.TASK_OUTCOME_UNCERTAIN,
        TaskStatus.OUTCOME_UNCERTAIN,
        ExecutionAttemptStatus.OUTCOME_UNCERTAIN,
    ),
}

_ACTION_OUTCOME_EVIDENCE = {
    ActionOutcome.SUCCEEDED: SUCCESS_ACTION_RESULT_EVIDENCE,
    ActionOutcome.FAILED: FAILED_ACTION_RESULT_EVIDENCE,
    ActionOutcome.OUTCOME_UNCERTAIN: UNCERTAIN_ACTION_RESULT_EVIDENCE,
}


@dataclass(frozen=True, slots=True)
class PendingTaskEvent:
    message: TaskEventEnvelope
    event_type: TaskEventType
    target_task_status: TaskStatus | None
    target_attempt_status: ExecutionAttemptStatus | None
    action_id: ActionId | None
    target_action_status: ActionStatus | None
    target_action_outcome: ActionOutcome | None
    progress_percent: int | None
    source_fingerprint: bytes
    received_at: datetime
    action_evidence: ActionResultEvidence | None = None

    def __post_init__(self) -> None:
        if self.action_id is not None and self.action_evidence is None:
            outcome = self.target_action_outcome
            fallback = (
                {
                    ActionOutcome.SUCCEEDED: ActionResultEvidence.EXECUTOR_REPORTED_SUCCESS,
                    ActionOutcome.FAILED: ActionResultEvidence.EXECUTOR_REPORTED_FAILURE,
                    ActionOutcome.OUTCOME_UNCERTAIN: (ActionResultEvidence.FINAL_STATE_UNCONFIRMED),
                }.get(outcome)
                if isinstance(outcome, ActionOutcome)
                else None
            )
            if fallback is not None:
                object.__setattr__(self, "action_evidence", fallback)
        if (
            not isinstance(self.message, TaskEventEnvelope)
            or not isinstance(self.event_type, TaskEventType)
            or (
                self.target_task_status is not None
                and not isinstance(self.target_task_status, TaskStatus)
            )
            or (
                self.target_attempt_status is not None
                and not isinstance(self.target_attempt_status, ExecutionAttemptStatus)
            )
            or (self.action_id is not None and not isinstance(self.action_id, ActionId))
            or (
                self.target_action_status is not None
                and not isinstance(self.target_action_status, ActionStatus)
            )
            or (
                self.target_action_outcome is not None
                and not isinstance(self.target_action_outcome, ActionOutcome)
            )
            or (self.action_id is None and self.target_action_status is not None)
            or (self.action_id is None and self.target_action_outcome is not None)
            or (self.action_id is None and self.action_evidence is not None)
            or (
                self.action_evidence is not None
                and not isinstance(self.action_evidence, ActionResultEvidence)
            )
            or (
                self.target_action_outcome in _ACTION_OUTCOME_EVIDENCE
                and self.action_evidence not in _ACTION_OUTCOME_EVIDENCE[self.target_action_outcome]
            )
            or (
                self.progress_percent is not None
                and (
                    type(self.progress_percent) is not int or not 0 <= self.progress_percent <= 100
                )
            )
            or type(self.source_fingerprint) is not bytes
            or len(self.source_fingerprint) != 32
            or not isinstance(self.received_at, datetime)
            or self.received_at.utcoffset() is None
        ):
            raise TaskEventConvergenceRejected


@dataclass(frozen=True, slots=True)
class TaskEventConvergenceResult:
    snapshot: TaskSnapshotProjection
    duplicate: bool

    def __post_init__(self) -> None:
        if (
            not isinstance(self.snapshot, TaskSnapshotProjection)
            or type(self.duplicate) is not bool
        ):
            raise TaskEventConvergenceRejected


@runtime_checkable
class TaskEventConvergenceRepository(Protocol):
    async def converge(self, pending: PendingTaskEvent) -> TaskEventConvergenceResult: ...


def _source_fingerprint(message: TaskEventEnvelope) -> bytes:
    stable = message.model_dump(mode="json")
    del stable["message_id"]
    del stable["sent_at"]
    encoded = json.dumps(
        stable,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).digest()


def _action_plan(
    message: TaskEventEnvelope,
) -> tuple[
    ActionId | None,
    ActionStatus | None,
    ActionOutcome | None,
    ActionResultEvidence | None,
    int | None,
]:
    payload = message.payload
    message_type = message.message_type
    action_event = message_type in {
        "step.started",
        "step.progress",
        "step.completed",
        "step.failed",
        "task.outcome_uncertain",
    }
    if not action_event:
        if payload:
            raise TaskEventConvergenceRejected
        return None, None, None, None, None

    allowed = {"action_id"}
    if message_type == "step.progress":
        allowed.add("progress_percent")
    if message_type in {"step.completed", "step.failed", "task.outcome_uncertain"}:
        allowed.update({"evidence", "evidence_version"})
    if not set(payload).issubset(allowed):
        raise TaskEventConvergenceRejected
    action_id: ActionId | None = None
    if "action_id" in payload:
        try:
            action_id = ActionId.parse(payload["action_id"])
        except Exception:
            raise TaskEventConvergenceRejected from None

    evidence: ActionResultEvidence | None = None
    evidence_value = payload.get("evidence")
    evidence_version = payload.get("evidence_version")
    if (evidence_value is None) != (evidence_version is None):
        raise TaskEventConvergenceRejected
    if evidence_value is not None:
        if (
            action_id is None
            or type(evidence_value) is not str
            or evidence_version != ACTION_RESULT_EVIDENCE_VERSION
        ):
            raise TaskEventConvergenceRejected
        try:
            evidence = ActionResultEvidence(evidence_value)
        except (TypeError, ValueError):
            raise TaskEventConvergenceRejected from None

    progress_percent: int | None = None
    if message_type == "step.progress":
        progress_percent = payload.get("progress_percent")  # type: ignore[assignment]
        if type(progress_percent) is not int or not 0 <= progress_percent <= 100:
            raise TaskEventConvergenceRejected
    if message_type == "step.started":
        return action_id, ActionStatus.DISPATCHED if action_id else None, None, None, None
    if message_type == "step.completed":
        evidence = evidence or (
            ActionResultEvidence.EXECUTOR_REPORTED_SUCCESS if action_id else None
        )
        if evidence is not None and evidence not in SUCCESS_ACTION_RESULT_EVIDENCE:
            raise TaskEventConvergenceRejected
        return (
            action_id,
            ActionStatus.VERIFIED if action_id else None,
            ActionOutcome.SUCCEEDED if action_id else None,
            evidence,
            None,
        )
    if message_type == "step.failed":
        evidence = evidence or (
            ActionResultEvidence.EXECUTOR_REPORTED_FAILURE if action_id else None
        )
        if evidence is not None and evidence not in FAILED_ACTION_RESULT_EVIDENCE:
            raise TaskEventConvergenceRejected
        return (
            action_id,
            ActionStatus.VERIFIED if action_id else None,
            ActionOutcome.FAILED if action_id else None,
            evidence,
            None,
        )
    if message_type == "task.outcome_uncertain" and action_id is not None:
        evidence = evidence or ActionResultEvidence.FINAL_STATE_UNCONFIRMED
        if evidence not in UNCERTAIN_ACTION_RESULT_EVIDENCE:
            raise TaskEventConvergenceRejected
        return (
            action_id,
            ActionStatus.OUTCOME_UNCERTAIN,
            ActionOutcome.OUTCOME_UNCERTAIN,
            evidence,
            None,
        )
    return action_id, None, None, None, progress_percent


class TaskEventConvergenceService:
    """Convert one formal Executor event into one closed atomic persistence plan."""

    def __init__(
        self,
        *,
        repository: TaskEventConvergenceRepository,
        clock: TaskEventConvergenceClock | None = None,
    ) -> None:
        if not isinstance(repository, TaskEventConvergenceRepository):
            raise TaskEventConvergenceRejected
        self._repository = repository
        self._clock = clock or SystemTaskEventConvergenceClock()

    def _now(self) -> datetime:
        try:
            value = self._clock.now()
            if not isinstance(value, datetime) or value.utcoffset() is None:
                raise ValueError
            return value.astimezone(UTC)
        except Exception:
            pass
        raise TaskEventConvergenceUnavailable

    async def receive(self, message: TaskEventEnvelope) -> TaskEventConvergenceResult:
        if not isinstance(message, TaskEventEnvelope):
            raise TaskEventConvergenceRejected
        received_at = self._now()
        if received_at < message.sent_at or received_at >= message.deadline_at:
            raise TaskEventConvergenceRejected
        plan = _EVENT_PLANS[message.message_type]
        (
            action_id,
            action_status,
            action_outcome,
            action_evidence,
            progress_percent,
        ) = _action_plan(message)
        pending = PendingTaskEvent(
            message=message,
            event_type=plan.event_type,
            target_task_status=plan.task_status,
            target_attempt_status=plan.attempt_status,
            action_id=action_id,
            target_action_status=action_status,
            target_action_outcome=action_outcome,
            action_evidence=action_evidence,
            progress_percent=progress_percent,
            source_fingerprint=_source_fingerprint(message),
            received_at=received_at,
        )
        try:
            return await self._repository.converge(pending)
        except TaskEventConvergenceRejected:
            raise
        except TaskEventConvergenceUnavailable:
            raise
        except Exception:
            pass
        raise TaskEventConvergenceUnavailable


__all__ = [
    "PendingTaskEvent",
    "SystemTaskEventConvergenceClock",
    "TaskEventConvergenceClock",
    "TaskEventConvergenceRejected",
    "TaskEventConvergenceRepository",
    "TaskEventConvergenceResult",
    "TaskEventConvergenceService",
    "TaskEventConvergenceUnavailable",
]
