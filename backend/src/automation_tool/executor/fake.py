"""Deterministic no-side-effect Executor for production-protocol replay."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import RFC_4122, UUID, uuid4

from automation_tool.protocol import (
    EXECUTOR_PROTOCOL_VERSION,
    ExecutorLifecycleEnvelope,
    ExecutorProtocolError,
    TaskCommandEnvelope,
    TaskCommandResultEnvelope,
    TaskEventEnvelope,
    parse_executor_message,
)


class FakeExecutorRejected(ValueError):
    """A fake command cannot be replayed without weakening production rules."""

    def __init__(self) -> None:
        super().__init__("Fake Executor command is rejected")


class FakeExecutorScenario(StrEnum):
    SUCCEED = "succeed"
    PARTIAL = "partial"
    FAIL = "fail"
    LOGIN_REQUIRED = "login_required"
    HANDOFF = "handoff"
    OUTCOME_UNCERTAIN = "outcome_uncertain"
    REJECT = "reject"
    HOLD = "hold"


class FakeExecutorClock(Protocol):
    def now(self) -> datetime: ...


class SystemFakeExecutorClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class _AttemptState(StrEnum):
    NEW = "new"
    RUNNING = "running"
    PAUSED = "paused"
    AWAITING = "awaiting"
    TERMINAL = "terminal"


type FakeExecutorMessage = TaskCommandResultEnvelope | TaskEventEnvelope


@dataclass(frozen=True, slots=True)
class _ReplayEntry:
    fingerprint: str
    messages: tuple[FakeExecutorMessage, ...]


_SCENARIO_EVENTS: dict[FakeExecutorScenario, tuple[str, ...]] = {
    FakeExecutorScenario.SUCCEED: (
        "task.started",
        "step.started",
        "step.progress",
        "step.completed",
        "task.completed",
    ),
    FakeExecutorScenario.PARTIAL: (
        "task.started",
        "step.started",
        "step.completed",
        "step.failed",
        "task.partially_completed",
    ),
    FakeExecutorScenario.FAIL: (
        "task.started",
        "step.started",
        "step.failed",
        "task.failed",
    ),
    FakeExecutorScenario.LOGIN_REQUIRED: ("session.login_required",),
    FakeExecutorScenario.HANDOFF: (
        "task.started",
        "handoff.requested",
    ),
    FakeExecutorScenario.OUTCOME_UNCERTAIN: (
        "task.started",
        "task.outcome_uncertain",
    ),
    FakeExecutorScenario.REJECT: (),
    FakeExecutorScenario.HOLD: (
        "task.started",
        "step.started",
    ),
}


def _aware_utc(value: object) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise FakeExecutorRejected
    return value.astimezone(UTC)


def _canonical_uuid_v4(value: object) -> str:
    if type(value) is not str:
        raise FakeExecutorRejected
    try:
        parsed = UUID(value)
    except ValueError:
        raise FakeExecutorRejected from None
    if parsed.version != 4 or parsed.variant != RFC_4122 or value != str(parsed):
        raise FakeExecutorRejected
    return value


def _generated_uuid_v4(source: Callable[[], object]) -> str:
    try:
        value = source()
    except Exception:
        raise FakeExecutorRejected from None
    if not isinstance(value, UUID) or value.version != 4 or value.variant != RFC_4122:
        raise FakeExecutorRejected
    return str(value)


def _fingerprint(message: TaskCommandEnvelope) -> str:
    stable = {
        "protocol_version": message.protocol_version,
        "message_type": message.message_type,
        "deadline_at": message.deadline_at.isoformat(),
        "installation_id": str(message.installation_id),
        "executor_id": str(message.executor_id),
        "correlation_id": str(message.correlation_id),
        "idempotency_key": str(message.idempotency_key),
        "sequence": message.sequence,
        "payload": message.payload,
        "task_id": str(message.task_id),
        "execution_attempt_id": str(message.execution_attempt_id),
    }
    return json.dumps(stable, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


class FakeExecutorEngine:
    """Transform formal commands into deterministic formal result/event batches."""

    def __init__(
        self,
        *,
        installation_id: str,
        executor_id: str,
        scenario: FakeExecutorScenario,
        clock: FakeExecutorClock | None = None,
        id_source: Callable[[], object] = uuid4,
    ) -> None:
        if not isinstance(scenario, FakeExecutorScenario) or not callable(id_source):
            raise FakeExecutorRejected
        self._installation_id = _canonical_uuid_v4(installation_id)
        self._executor_id = _canonical_uuid_v4(executor_id)
        self._scenario = scenario
        self._clock = clock or SystemFakeExecutorClock()
        self._id_source = id_source
        self._by_message: dict[str, _ReplayEntry] = {}
        self._by_idempotency: dict[str, _ReplayEntry] = {}
        self._attempt_states: dict[str, _AttemptState] = {}
        self._attempt_tasks: dict[str, str] = {}
        self._last_command_sequence: dict[str, int] = {}
        self._last_event_sequence: dict[str, int] = {}

    @property
    def installation_id(self) -> str:
        return self._installation_id

    @property
    def executor_id(self) -> str:
        return self._executor_id

    def _now(self) -> datetime:
        try:
            return _aware_utc(self._clock.now())
        except Exception:
            raise FakeExecutorRejected from None

    def handle(self, source: str | bytes) -> tuple[FakeExecutorMessage, ...]:
        try:
            parsed = parse_executor_message(source)
        except ExecutorProtocolError:
            raise FakeExecutorRejected from None
        if (
            not isinstance(parsed, TaskCommandEnvelope)
            or str(parsed.installation_id) != self._installation_id
            or str(parsed.executor_id) != self._executor_id
        ):
            raise FakeExecutorRejected

        fingerprint = _fingerprint(parsed)
        by_message = self._by_message.get(str(parsed.message_id))
        by_idempotency = self._by_idempotency.get(str(parsed.idempotency_key))
        replay = by_message or by_idempotency
        if replay is not None:
            if by_message is not None and by_message.fingerprint != fingerprint:
                raise FakeExecutorRejected
            if by_idempotency is not None and by_idempotency.fingerprint != fingerprint:
                raise FakeExecutorRejected
            return replay.messages
        if self._now() >= parsed.deadline_at:
            raise FakeExecutorRejected

        attempt_id = str(parsed.execution_attempt_id)
        task_id = str(parsed.task_id)
        expected_sequence = self._last_command_sequence.get(attempt_id, 0) + 1
        existing_task = self._attempt_tasks.get(attempt_id)
        if parsed.sequence != expected_sequence or (
            existing_task is not None and existing_task != task_id
        ):
            raise FakeExecutorRejected

        state_snapshot = self._attempt_states.copy()
        event_sequence_snapshot = self._last_event_sequence.copy()
        try:
            messages = self._apply(parsed)
        except FakeExecutorRejected:
            self._attempt_states = state_snapshot
            self._last_event_sequence = event_sequence_snapshot
            raise
        entry = _ReplayEntry(fingerprint=fingerprint, messages=messages)
        self._by_message[str(parsed.message_id)] = entry
        self._by_idempotency[str(parsed.idempotency_key)] = entry
        self._attempt_tasks[attempt_id] = task_id
        self._last_command_sequence[attempt_id] = parsed.sequence
        return messages

    def build_hello(self, *, sequence: int = 1) -> ExecutorLifecycleEnvelope:
        if type(sequence) is not int or sequence <= 0:
            raise FakeExecutorRejected
        now = self._now()
        try:
            return ExecutorLifecycleEnvelope.model_validate(
                {
                    "protocol_version": EXECUTOR_PROTOCOL_VERSION,
                    "message_id": _generated_uuid_v4(self._id_source),
                    "message_type": "executor.hello",
                    "sent_at": now,
                    "deadline_at": now + timedelta(seconds=30),
                    "installation_id": self._installation_id,
                    "executor_id": self._executor_id,
                    "correlation_id": _generated_uuid_v4(self._id_source),
                    "idempotency_key": f"fake:hello:{self._executor_id}",
                    "sequence": sequence,
                    "payload": {
                        "architecture": "arm64",
                        "executor_version": "0.1.0",
                        "platform": "macos",
                    },
                }
            )
        except Exception:
            raise FakeExecutorRejected from None

    def _apply(self, command: TaskCommandEnvelope) -> tuple[FakeExecutorMessage, ...]:
        attempt_id = str(command.execution_attempt_id)
        state = self._attempt_states.get(attempt_id, _AttemptState.NEW)
        if command.message_type == "task.offer":
            if state is not _AttemptState.NEW:
                raise FakeExecutorRejected
            task_id = str(command.task_id)
            baseline = command.payload["task_event_sequence_baseline"]
            if type(baseline) is not int:
                raise FakeExecutorRejected
            known_baseline = self._last_event_sequence.get(task_id)
            if known_baseline is None:
                self._last_event_sequence[task_id] = baseline
            elif known_baseline != baseline:
                raise FakeExecutorRejected
            if self._scenario is FakeExecutorScenario.REJECT:
                self._attempt_states[attempt_id] = _AttemptState.TERMINAL
                return (self._result(command, "task.reject"),)
            events = _SCENARIO_EVENTS[self._scenario]
            if self._scenario is FakeExecutorScenario.HOLD:
                next_state = _AttemptState.RUNNING
            elif self._scenario in {
                FakeExecutorScenario.LOGIN_REQUIRED,
                FakeExecutorScenario.HANDOFF,
            }:
                next_state = _AttemptState.AWAITING
            else:
                next_state = _AttemptState.TERMINAL
            self._attempt_states[attempt_id] = next_state
            return (
                self._result(command, "task.accept"),
                *(self._event(command, event_type) for event_type in events),
            )

        if command.message_type == "task.pause":
            if state is not _AttemptState.RUNNING:
                raise FakeExecutorRejected
            self._attempt_states[attempt_id] = _AttemptState.PAUSED
            event_type = "task.paused"
        elif command.message_type == "task.resume":
            if state is not _AttemptState.PAUSED:
                raise FakeExecutorRejected
            self._attempt_states[attempt_id] = _AttemptState.RUNNING
            event_type = "task.resumed"
        else:
            if state not in {
                _AttemptState.RUNNING,
                _AttemptState.PAUSED,
                _AttemptState.AWAITING,
            }:
                raise FakeExecutorRejected
            self._attempt_states[attempt_id] = _AttemptState.TERMINAL
            event_type = (
                "task.outcome_uncertain"
                if command.message_type == "task.emergency_stop"
                else "task.cancelled"
            )
        return (
            self._result(command, "task.control_ack"),
            self._event(command, event_type),
        )

    def _result(
        self,
        command: TaskCommandEnvelope,
        message_type: str,
    ) -> TaskCommandResultEnvelope:
        now = self._now()
        payload = (
            {"accepted": message_type == "task.accept"}
            if message_type != "task.control_ack"
            else {"acknowledged": True}
        )
        try:
            return TaskCommandResultEnvelope.model_validate(
                {
                    "protocol_version": EXECUTOR_PROTOCOL_VERSION,
                    "message_id": _generated_uuid_v4(self._id_source),
                    "message_type": message_type,
                    "sent_at": now,
                    "deadline_at": now + timedelta(seconds=30),
                    "installation_id": self._installation_id,
                    "executor_id": self._executor_id,
                    "correlation_id": str(command.correlation_id),
                    "idempotency_key": f"fake:result:{command.message_id}",
                    "sequence": command.sequence,
                    "payload": payload,
                    "task_id": str(command.task_id),
                    "execution_attempt_id": str(command.execution_attempt_id),
                }
            )
        except Exception:
            raise FakeExecutorRejected from None

    def _event(self, command: TaskCommandEnvelope, message_type: str) -> TaskEventEnvelope:
        task_id = str(command.task_id)
        sequence = self._last_event_sequence.get(task_id, 0) + 1
        now = self._now()
        payload: dict[str, object] = {}
        if message_type == "step.progress":
            payload = {"progress_percent": 50}
        try:
            event = TaskEventEnvelope.model_validate(
                {
                    "protocol_version": EXECUTOR_PROTOCOL_VERSION,
                    "message_id": _generated_uuid_v4(self._id_source),
                    "message_type": message_type,
                    "sent_at": now,
                    "deadline_at": now + timedelta(seconds=30),
                    "installation_id": self._installation_id,
                    "executor_id": self._executor_id,
                    "correlation_id": str(command.correlation_id),
                    "idempotency_key": f"fake:event:{task_id}:{sequence}",
                    "sequence": sequence,
                    "payload": payload,
                    "task_id": task_id,
                    "execution_attempt_id": str(command.execution_attempt_id),
                }
            )
        except Exception:
            raise FakeExecutorRejected from None
        self._last_event_sequence[task_id] = sequence
        return event


__all__ = [
    "FakeExecutorClock",
    "FakeExecutorEngine",
    "FakeExecutorMessage",
    "FakeExecutorRejected",
    "FakeExecutorScenario",
    "SystemFakeExecutorClock",
]
