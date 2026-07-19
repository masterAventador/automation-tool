"""Durable no-side-effect command processing for the packaged Local Executor."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Protocol, cast, runtime_checkable
from uuid import RFC_4122, UUID, uuid4

from automation_tool.executor.ledger import (
    AttemptCheckpointState,
    ExecutorLedger,
    ExecutorLedgerRejected,
)
from automation_tool.protocol import (
    EXECUTOR_PROTOCOL_VERSION,
    TaskCommandEnvelope,
    TaskCommandResultEnvelope,
    TaskEventEnvelope,
    parse_executor_message,
)

_MESSAGE_DEADLINE = timedelta(seconds=30)
_MAX_PENDING_OUTBOX = 1000
_MISSING = object()

type ExecutorOutboundMessage = TaskCommandResultEnvelope | TaskEventEnvelope


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
    ) -> None:
        try:
            resolved_clock = SystemExecutorCommandClock() if clock is None else clock
            if (
                not isinstance(ledger, ExecutorLedger)
                or not isinstance(resolved_clock, ExecutorCommandClock)
                or not callable(id_source)
            ):
                raise ValueError
            self._installation_id = _canonical_uuid_v4(installation_id)
            self._executor_id = _canonical_uuid_v4(executor_id)
            self._ledger = ledger
            self._clock = resolved_clock
            self._id_source = id_source
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

    def mark_delivered(self, message_id: str) -> bool:
        return _collapse_failure(lambda: self._ledger.mark_outbox_delivered(message_id))

    def _handle(self, source: str | bytes) -> tuple[ExecutorOutboundMessage, ...]:
        command = parse_executor_message(source)
        if (
            not isinstance(command, TaskCommandEnvelope)
            or command.message_type != "task.offer"
            or str(command.installation_id) != self._installation_id
            or str(command.executor_id) != self._executor_id
            or self._now() >= command.deadline_at
        ):
            raise ValueError
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
        batch = self._success_batch(command)
        try:
            entries = self._ledger.commit_outcome(
                source_message_id=receipt.message_id,
                expected_checkpoint_revision=checkpoint.revision,
                checkpoint_state=AttemptCheckpointState.TERMINAL,
                last_event_sequence=5,
                messages=batch,
            )
        except ExecutorLedgerRejected:
            replay = self._ledger.outbox_for_command(receipt.message_id)
            if not replay:
                raise
            return tuple(entry.message for entry in replay)
        return tuple(entry.message for entry in entries)

    def _pending_outbox(self) -> tuple[ExecutorOutboundMessage, ...]:
        return tuple(
            entry.message for entry in self._ledger.pending_outbox(limit=_MAX_PENDING_OUTBOX)
        )

    def _recover_outbox(self) -> tuple[ExecutorOutboundMessage, ...]:
        self._ledger.requeue_delivered_outbox()
        return self._pending_outbox()

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

    def _result(self, command: TaskCommandEnvelope) -> TaskCommandResultEnvelope:
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
