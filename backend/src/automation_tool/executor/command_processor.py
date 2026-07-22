"""Durable no-side-effect command processing for the packaged Local Executor."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Protocol, cast, runtime_checkable
from uuid import RFC_4122, UUID, uuid4

from automation_tool.executor.action_operation import DouyinActionOperation
from automation_tool.executor.discovery_operation import (
    DouyinDiscoveryExecutionResult,
    DouyinDiscoveryOperation,
    DouyinDiscoveryOperationState,
)
from automation_tool.executor.ledger import (
    AttemptCheckpointState,
    ExecutorLedger,
    ExecutorLedgerRejected,
)
from automation_tool.executor.rpa.douyin.action_result import DouyinActionResultFact
from automation_tool.protocol import (
    DOUYIN_CANDIDATE_VERSION,
    DOUYIN_DISCOVERY_PROTOCOL_VERSION,
    EXECUTOR_PROTOCOL_VERSION,
    MAX_DISCOVERY_BATCH_CANDIDATES,
    DouyinCandidate,
    TaskActionCommandEnvelope,
    TaskCommandEnvelope,
    TaskCommandResultEnvelope,
    TaskDiscoveryBatchEnvelope,
    TaskDiscoveryCommandEnvelope,
    TaskDiscoveryCompletedEnvelope,
    TaskEventEnvelope,
    parse_executor_message,
)

_MESSAGE_DEADLINE = timedelta(seconds=30)
_MAX_PENDING_OUTBOX = 1000
_MISSING = object()

type ExecutorCommandMessage = (
    TaskCommandEnvelope | TaskDiscoveryCommandEnvelope | TaskActionCommandEnvelope
)
type ExecutorOutboundMessage = (
    TaskCommandResultEnvelope
    | TaskEventEnvelope
    | TaskDiscoveryBatchEnvelope
    | TaskDiscoveryCompletedEnvelope
)


class ExecutorCommandRejected(ValueError):
    """A command cannot be processed without weakening the durable boundary."""

    def __init__(self) -> None:
        super().__init__("Local Executor command is rejected")


class ExecutorCommandExpired(ExecutorCommandRejected):
    """A valid command arrived after its server-authored UTC deadline."""

    def __init__(self) -> None:
        ValueError.__init__(self, "Local Executor command deadline has expired")


@runtime_checkable
class ExecutorCommandClock(Protocol):
    def now(self) -> datetime: ...


class SystemExecutorCommandClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class ExecutorCommandProcessor:
    """Persist commands before producing a fixed, replayable outcome batch."""

    def __init__(
        self,
        *,
        ledger: ExecutorLedger,
        installation_id: str,
        executor_id: str,
        clock: ExecutorCommandClock | None = None,
        id_source: Callable[[], object] = uuid4,
        discovery_operation: DouyinDiscoveryOperation | None = None,
        action_operation: DouyinActionOperation | None = None,
    ) -> None:
        try:
            resolved_clock = SystemExecutorCommandClock() if clock is None else clock
            if (
                not isinstance(ledger, ExecutorLedger)
                or not isinstance(resolved_clock, ExecutorCommandClock)
                or not callable(id_source)
                or (
                    discovery_operation is not None
                    and not isinstance(discovery_operation, DouyinDiscoveryOperation)
                )
                or (
                    action_operation is not None
                    and not isinstance(action_operation, DouyinActionOperation)
                )
            ):
                raise ValueError
            self._installation_id = _canonical_uuid_v4(installation_id)
            self._executor_id = _canonical_uuid_v4(executor_id)
            self._ledger = ledger
            self._clock = resolved_clock
            self._id_source = id_source
            self._discovery_operation = discovery_operation
            self._action_operation = action_operation
        except Exception:
            raise ExecutorCommandRejected from None

    @property
    def ledger(self) -> ExecutorLedger:
        return self._ledger

    def handle(self, source: str | bytes) -> tuple[ExecutorOutboundMessage, ...]:
        expired = False
        try:
            return self._handle(source)
        except ExecutorCommandExpired:
            expired = True
        except Exception:
            pass
        if expired:
            raise ExecutorCommandExpired
        raise ExecutorCommandRejected

    def pending_outbox(self) -> tuple[ExecutorOutboundMessage, ...]:
        return _collapse_failure(self._pending_outbox)

    def recover_outbox(self) -> tuple[ExecutorOutboundMessage, ...]:
        return _collapse_failure(self._recover_outbox)

    def poll_controls(self) -> tuple[ExecutorOutboundMessage, ...]:
        return _collapse_failure(self._poll_controls)

    def mark_delivered(self, message_id: str) -> bool:
        return _collapse_failure(lambda: self._ledger.mark_outbox_delivered(message_id))

    def emergency_stop_received(self) -> bool:
        return _collapse_failure(self._ledger.has_received_task_emergency_stop)

    def _handle(self, source: str | bytes) -> tuple[ExecutorOutboundMessage, ...]:
        command = parse_executor_message(source)
        if (
            not isinstance(
                command,
                (TaskCommandEnvelope, TaskDiscoveryCommandEnvelope, TaskActionCommandEnvelope),
            )
            or (
                isinstance(command, TaskCommandEnvelope)
                and command.message_type
                not in {
                    "task.offer",
                    "task.pause",
                    "task.resume",
                    "task.cancel",
                    "task.emergency_stop",
                }
            )
            or str(command.installation_id) != self._installation_id
            or str(command.executor_id) != self._executor_id
        ):
            raise ValueError
        if self._now() >= command.deadline_at:
            raise ExecutorCommandExpired
        if isinstance(command, TaskCommandEnvelope) and command.message_type in {
            "task.pause",
            "task.resume",
            "task.cancel",
            "task.emergency_stop",
        }:
            return self._handle_control(command)
        receipt = self._ledger.receive_command(command)
        existing = self._ledger.outbox_for_command(receipt.message_id)
        if existing:
            return tuple(entry.message for entry in existing)
        checkpoint = self._ledger.get_checkpoint(receipt.attempt_id)
        if checkpoint is None:
            raise ValueError
        checkpoint_state = AttemptCheckpointState.TERMINAL
        if isinstance(command, TaskActionCommandEnvelope):
            action_operation = self._action_operation
            if action_operation is None or checkpoint.state is not AttemptCheckpointState.RUNNING:
                raise ValueError
            fact = action_operation.run(command)
            if (
                not isinstance(fact, DouyinActionResultFact)
                or str(fact.action_id) != str(command.payload.action_id)
                or str(fact.target_id) != str(command.payload.target_id)
            ):
                raise ValueError
            batch = self._action_batch(command, fact, checkpoint.last_event_sequence)
            last_event_sequence = checkpoint.last_event_sequence + 2
            checkpoint_state = (
                AttemptCheckpointState.OUTCOME_UNCERTAIN
                if fact.message_type == "task.outcome_uncertain"
                else AttemptCheckpointState.RUNNING
            )
        elif checkpoint.state is not AttemptCheckpointState.RECEIVED:
            raise ValueError
        elif isinstance(command, TaskDiscoveryCommandEnvelope):
            if checkpoint.last_event_sequence != 0:
                raise ValueError
            discovery_operation = self._discovery_operation
            if discovery_operation is None:
                raise ValueError
            outcome = discovery_operation.run(
                command.payload,
                cancellation_requested=lambda: False,
            )
            if (
                not isinstance(outcome, DouyinDiscoveryExecutionResult)
                or outcome.page_revision != command.payload.page_revision
                or len(outcome.candidates) > command.payload.target_limit
            ):
                raise ValueError
            batch = self._discovery_batch(command, outcome)
            last_event_sequence = len(batch) - 1
        else:
            baseline = command.payload["task_event_sequence_baseline"]
            if type(baseline) is not int or checkpoint.last_event_sequence != baseline:
                raise ValueError
            batch = self._started_batch(command, baseline=baseline)
            last_event_sequence = baseline + 1
            checkpoint_state = AttemptCheckpointState.RUNNING
        try:
            entries = self._ledger.commit_outcome(
                source_message_id=receipt.message_id,
                expected_checkpoint_revision=checkpoint.revision,
                checkpoint_state=checkpoint_state,
                last_event_sequence=last_event_sequence,
                messages=batch,
            )
        except ExecutorLedgerRejected:
            replay = self._ledger.outbox_for_command(receipt.message_id)
            if not replay:
                raise
            return tuple(entry.message for entry in replay)
        return tuple(entry.message for entry in entries)

    def _handle_control(
        self,
        command: TaskCommandEnvelope,
    ) -> tuple[ExecutorOutboundMessage, ...]:
        receipt = (
            self._ledger.receive_task_emergency_stop(command, changed_at=self._now())
            if command.message_type == "task.emergency_stop"
            else self._ledger.receive_command(command)
        )
        existing = self._ledger.outbox_for_command(receipt.message_id)
        if not existing:
            self._ledger.enqueue_outbox(
                source_message_id=receipt.message_id,
                message=self._control_ack(command, source_message_id=receipt.message_id),
            )
        self._advance_controls(source_message_id=receipt.message_id)
        entries = self._ledger.outbox_for_command(receipt.message_id)
        if not entries or entries[0].message.message_type != "task.control_ack":
            raise ValueError
        return tuple(entry.message for entry in entries)

    def _pending_outbox(self) -> tuple[ExecutorOutboundMessage, ...]:
        return tuple(
            entry.message for entry in self._ledger.pending_outbox(limit=_MAX_PENDING_OUTBOX)
        )

    def _recover_outbox(self) -> tuple[ExecutorOutboundMessage, ...]:
        self._ledger.requeue_delivered_outbox()
        return self._pending_outbox()

    def _poll_controls(self) -> tuple[ExecutorOutboundMessage, ...]:
        return self._advance_controls(source_message_id=None)

    def _advance_controls(
        self,
        *,
        source_message_id: str | None,
    ) -> tuple[ExecutorOutboundMessage, ...]:
        projected: list[ExecutorOutboundMessage] = []
        for pending in self._ledger.pending_task_controls(limit=100):
            command = pending.command
            if source_message_id is not None and str(command.message_id) != source_message_id:
                continue
            event = self._event(
                command,
                message_type=pending.event_type,
                sequence=pending.next_event_sequence,
            )
            completed = self._ledger.complete_task_control(
                source_message_id=str(command.message_id),
                expected_checkpoint_revision=pending.checkpoint_revision,
                event=event,
            )
            if completed is not None and not completed.replayed:
                projected.append(completed.message)
        return tuple(projected)

    def _now(self) -> datetime:
        value = self._clock.now()
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise ValueError
        return value.astimezone(UTC)

    def _new_id(self) -> str:
        value = self._id_source()
        if not isinstance(value, UUID) or value.version != 4 or value.variant != RFC_4122:
            raise ValueError
        return str(value)

    def _started_batch(
        self,
        command: TaskCommandEnvelope,
        *,
        baseline: int,
    ) -> tuple[ExecutorOutboundMessage, ...]:
        return (
            self._result(command),
            self._event(
                command,
                message_type="task.started",
                sequence=baseline + 1,
            ),
        )

    def _action_batch(
        self,
        command: TaskActionCommandEnvelope,
        fact: DouyinActionResultFact,
        last_event_sequence: int,
    ) -> tuple[ExecutorOutboundMessage, ...]:
        started_sequence = last_event_sequence + 1
        result_sequence = started_sequence + 1
        return (
            self._result(command),
            self._event(command, message_type="step.started", sequence=started_sequence),
            self._event(
                command,
                message_type=fact.message_type,
                sequence=result_sequence,
                payload=fact.payload,
            ),
        )

    def _discovery_batch(
        self,
        command: TaskDiscoveryCommandEnvelope,
        outcome: DouyinDiscoveryExecutionResult,
    ) -> tuple[ExecutorOutboundMessage, ...]:
        result = self._result(command)
        candidates = outcome.candidates
        batch_count = (
            len(candidates) + MAX_DISCOVERY_BATCH_CANDIDATES - 1
        ) // MAX_DISCOVERY_BATCH_CANDIDATES
        chunks = tuple(
            self._discovery_chunk(
                command,
                candidates=candidates[offset : offset + MAX_DISCOVERY_BATCH_CANDIDATES],
                batch_index=offset // MAX_DISCOVERY_BATCH_CANDIDATES + 1,
                batch_count=batch_count,
            )
            for offset in range(0, len(candidates), MAX_DISCOVERY_BATCH_CANDIDATES)
        )
        completed = self._discovery_completed(
            command,
            outcome=outcome,
            batch_count=batch_count,
            sequence=batch_count + 1,
        )
        return (result, *chunks, completed)

    def _result(self, command: ExecutorCommandMessage) -> TaskCommandResultEnvelope:
        now = self._now()
        action = isinstance(command, TaskActionCommandEnvelope)
        return TaskCommandResultEnvelope.model_validate(
            {
                "protocol_version": EXECUTOR_PROTOCOL_VERSION,
                "message_id": self._new_id(),
                "message_type": "action.accept" if action else "task.accept",
                "sent_at": now,
                "deadline_at": now + _MESSAGE_DEADLINE,
                "installation_id": self._installation_id,
                "executor_id": self._executor_id,
                "correlation_id": str(command.correlation_id),
                "idempotency_key": f"executor:result:{command.message_id}",
                "sequence": command.sequence,
                "payload": {"accepted": True},
                "task_id": str(command.task_id),
                "execution_attempt_id": str(command.execution_attempt_id),
            }
        )

    def _control_ack(
        self,
        command: TaskCommandEnvelope,
        *,
        source_message_id: str,
    ) -> TaskCommandResultEnvelope:
        now = self._now()
        return TaskCommandResultEnvelope.model_validate(
            {
                "protocol_version": EXECUTOR_PROTOCOL_VERSION,
                "message_id": self._new_id(),
                "message_type": "task.control_ack",
                "sent_at": now,
                "deadline_at": now + _MESSAGE_DEADLINE,
                "installation_id": self._installation_id,
                "executor_id": self._executor_id,
                "correlation_id": str(command.correlation_id),
                "idempotency_key": f"executor:result:{source_message_id}",
                "sequence": command.sequence,
                "payload": {"acknowledged": True},
                "task_id": str(command.task_id),
                "execution_attempt_id": str(command.execution_attempt_id),
            }
        )

    def _discovery_chunk(
        self,
        command: TaskDiscoveryCommandEnvelope,
        *,
        candidates: tuple[DouyinCandidate, ...],
        batch_index: int,
        batch_count: int,
    ) -> TaskDiscoveryBatchEnvelope:
        now = self._now()
        payload_candidates = [
            {
                "candidate_version": DOUYIN_CANDIDATE_VERSION,
                "platform_target_id": candidate.platform_target_id,
                "display_name": candidate.summary.display_name,
                "public_handle": candidate.summary.public_handle,
                "source": candidate.source.value,
                "page_revision": candidate.page_revision,
            }
            for candidate in candidates
        ]
        return TaskDiscoveryBatchEnvelope.model_validate(
            {
                "protocol_version": EXECUTOR_PROTOCOL_VERSION,
                "message_id": self._new_id(),
                "message_type": "task.discovery_batch",
                "sent_at": now,
                "deadline_at": now + _MESSAGE_DEADLINE,
                "installation_id": self._installation_id,
                "executor_id": self._executor_id,
                "correlation_id": str(command.correlation_id),
                "idempotency_key": (
                    f"executor:discovery:{command.execution_attempt_id}:batch:{batch_index}"
                ),
                "sequence": batch_index,
                "payload": {
                    "discovery_version": DOUYIN_DISCOVERY_PROTOCOL_VERSION,
                    "page_revision": command.payload.page_revision,
                    "batch_index": batch_index,
                    "batch_count": batch_count,
                    "candidates": payload_candidates,
                },
                "task_id": str(command.task_id),
                "execution_attempt_id": str(command.execution_attempt_id),
            }
        )

    def _discovery_completed(
        self,
        command: TaskDiscoveryCommandEnvelope,
        *,
        outcome: DouyinDiscoveryExecutionResult,
        batch_count: int,
        sequence: int,
    ) -> TaskDiscoveryCompletedEnvelope:
        now = self._now()
        wire_outcome = {
            DouyinDiscoveryOperationState.COMPLETED: "completed",
            DouyinDiscoveryOperationState.LOGIN_REQUIRED: "login_required",
            DouyinDiscoveryOperationState.HANDOFF_REQUIRED: "handoff_required",
            DouyinDiscoveryOperationState.FAILED: "failed",
        }[outcome.state]
        return TaskDiscoveryCompletedEnvelope.model_validate(
            {
                "protocol_version": EXECUTOR_PROTOCOL_VERSION,
                "message_id": self._new_id(),
                "message_type": "task.discovery_completed",
                "sent_at": now,
                "deadline_at": now + _MESSAGE_DEADLINE,
                "installation_id": self._installation_id,
                "executor_id": self._executor_id,
                "correlation_id": str(command.correlation_id),
                "idempotency_key": (f"executor:discovery:{command.execution_attempt_id}:completed"),
                "sequence": sequence,
                "payload": {
                    "discovery_version": DOUYIN_DISCOVERY_PROTOCOL_VERSION,
                    "outcome": wire_outcome,
                    "evidence": outcome.evidence,
                    "page_revision": outcome.page_revision,
                    "batch_count": batch_count,
                    "candidate_count": len(outcome.candidates),
                },
                "task_id": str(command.task_id),
                "execution_attempt_id": str(command.execution_attempt_id),
            }
        )

    def _event(
        self,
        command: TaskCommandEnvelope | TaskActionCommandEnvelope,
        *,
        message_type: str,
        sequence: int,
        payload: Mapping[str, object] | None = None,
    ) -> TaskEventEnvelope:
        now = self._now()
        resolved_payload = (
            payload
            if payload is not None
            else (
                {"progress_percent": 100}
                if message_type == "step.progress"
                else (
                    {"action_id": str(command.payload.action_id)}
                    if isinstance(command, TaskActionCommandEnvelope)
                    else {}
                )
            )
        )
        return TaskEventEnvelope.model_validate(
            {
                "protocol_version": EXECUTOR_PROTOCOL_VERSION,
                "message_id": self._new_id(),
                "message_type": message_type,
                "sent_at": now,
                "deadline_at": now + _MESSAGE_DEADLINE,
                "installation_id": self._installation_id,
                "executor_id": self._executor_id,
                "correlation_id": str(command.correlation_id),
                "idempotency_key": f"executor:event:{command.task_id}:{sequence}",
                "sequence": sequence,
                "payload": resolved_payload,
                "task_id": str(command.task_id),
                "execution_attempt_id": str(command.execution_attempt_id),
            }
        )


def _canonical_uuid_v4(value: object) -> str:
    if type(value) is not str:
        raise ValueError
    parsed = UUID(value)
    if parsed.version != 4 or parsed.variant != RFC_4122 or value != str(parsed):
        raise ValueError
    return value


def _collapse_failure[Result](operation: Callable[[], Result]) -> Result:
    result: Result | object = _MISSING
    with suppress(Exception):
        result = operation()
    if result is _MISSING:
        raise ExecutorCommandRejected
    return cast(Result, result)


__all__ = [
    "ExecutorCommandClock",
    "ExecutorCommandExpired",
    "ExecutorCommandProcessor",
    "ExecutorCommandRejected",
    "ExecutorOutboundMessage",
    "SystemExecutorCommandClock",
]
