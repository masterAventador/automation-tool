"""Durable no-side-effect command processing for the packaged Local Executor."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Protocol, cast, runtime_checkable
from uuid import RFC_4122, UUID, uuid4

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
from automation_tool.protocol import (
    DOUYIN_CANDIDATE_VERSION,
    DOUYIN_DISCOVERY_PROTOCOL_VERSION,
    EXECUTOR_PROTOCOL_VERSION,
    MAX_DISCOVERY_BATCH_CANDIDATES,
    DouyinCandidate,
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

type ExecutorCommandMessage = TaskCommandEnvelope | TaskDiscoveryCommandEnvelope
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
            ):
                raise ValueError
            self._installation_id = _canonical_uuid_v4(installation_id)
            self._executor_id = _canonical_uuid_v4(executor_id)
            self._ledger = ledger
            self._clock = resolved_clock
            self._id_source = id_source
            self._discovery_operation = discovery_operation
        except Exception:
            raise ExecutorCommandRejected from None

    @property
    def ledger(self) -> ExecutorLedger:
        return self._ledger

    def handle(self, source: str | bytes) -> tuple[ExecutorOutboundMessage, ...]:
        return _collapse_failure(lambda: self._handle(source))

    def pending_outbox(self) -> tuple[ExecutorOutboundMessage, ...]:
        return _collapse_failure(self._pending_outbox)

    def recover_outbox(self) -> tuple[ExecutorOutboundMessage, ...]:
        return _collapse_failure(self._recover_outbox)

    def poll_controls(self) -> tuple[ExecutorOutboundMessage, ...]:
        return _collapse_failure(self._poll_controls)

    def mark_delivered(self, message_id: str) -> bool:
        return _collapse_failure(lambda: self._ledger.mark_outbox_delivered(message_id))

    def _handle(self, source: str | bytes) -> tuple[ExecutorOutboundMessage, ...]:
        command = parse_executor_message(source)
        if (
            not isinstance(command, (TaskCommandEnvelope, TaskDiscoveryCommandEnvelope))
            or (
                isinstance(command, TaskCommandEnvelope)
                and command.message_type
                not in {"task.offer", "task.pause", "task.resume", "task.cancel"}
            )
            or str(command.installation_id) != self._installation_id
            or str(command.executor_id) != self._executor_id
            or self._now() >= command.deadline_at
        ):
            raise ValueError
        if isinstance(command, TaskCommandEnvelope) and command.message_type in {
            "task.pause",
            "task.resume",
            "task.cancel",
        }:
            return self._handle_control(command)
        receipt = self._ledger.receive_command(command)
        existing = self._ledger.outbox_for_command(receipt.message_id)
        if existing:
            return tuple(entry.message for entry in existing)
        checkpoint = self._ledger.get_checkpoint(receipt.attempt_id)
        if (
            checkpoint is None
            or checkpoint.state is not AttemptCheckpointState.RECEIVED
            or checkpoint.last_event_sequence != 0
        ):
            raise ValueError
        if isinstance(command, TaskDiscoveryCommandEnvelope):
            operation = self._discovery_operation
            if operation is None:
                raise ValueError
            outcome = operation.run(command.payload, cancellation_requested=lambda: False)
            if (
                not isinstance(outcome, DouyinDiscoveryExecutionResult)
                or outcome.page_revision != command.payload.page_revision
                or len(outcome.candidates) > command.payload.target_limit
            ):
                raise ValueError
            batch = self._discovery_batch(command, outcome)
        else:
            batch = self._success_batch(command)
        last_event_sequence = len(batch) - 1
        try:
            entries = self._ledger.commit_outcome(
                source_message_id=receipt.message_id,
                expected_checkpoint_revision=checkpoint.revision,
                checkpoint_state=AttemptCheckpointState.TERMINAL,
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
        receipt = self._ledger.receive_command(command)
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

    def _success_batch(
        self,
        command: TaskCommandEnvelope,
    ) -> tuple[ExecutorOutboundMessage, ...]:
        result = self._result(command)
        event_types = (
            "task.started",
            "step.started",
            "step.progress",
            "step.completed",
            "task.completed",
        )
        events = tuple(
            self._event(command, message_type=message_type, sequence=sequence)
            for sequence, message_type in enumerate(event_types, start=1)
        )
        return (result, *events)

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
        return TaskCommandResultEnvelope.model_validate(
            {
                "protocol_version": EXECUTOR_PROTOCOL_VERSION,
                "message_id": self._new_id(),
                "message_type": "task.accept",
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
        command: TaskCommandEnvelope,
        *,
        message_type: str,
        sequence: int,
    ) -> TaskEventEnvelope:
        now = self._now()
        payload = {"progress_percent": 100} if message_type == "step.progress" else {}
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
                "payload": payload,
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
    "ExecutorCommandProcessor",
    "ExecutorCommandRejected",
    "ExecutorOutboundMessage",
    "SystemExecutorCommandClock",
]
