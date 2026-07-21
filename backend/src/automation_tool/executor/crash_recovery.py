"""Crash-only reconciliation between local side effects, checkpoints, and outbox."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable
from uuid import RFC_4122, UUID, uuid4

from automation_tool.executor.browser_runtime import BrowserWindow
from automation_tool.executor.ledger import AttemptCheckpointState, ExecutorLedger
from automation_tool.executor.rpa.douyin.action_result import (
    DouyinActionResultFact,
    recovered_action_result,
)
from automation_tool.executor.rpa.douyin.side_effect_recovery import (
    DouyinSideEffectRecovery,
)
from automation_tool.executor.side_effect_ledger import SideEffectState
from automation_tool.protocol import (
    EXECUTOR_PROTOCOL_VERSION,
    ProtocolActionId,
    TaskEventEnvelope,
)

_MESSAGE_DEADLINE = timedelta(seconds=30)
_MAX_RECOVERABLE_EFFECTS = 100


class ExecutorCrashRecoveryRejected(RuntimeError):
    """Crash recovery cannot safely reconcile the local durable facts."""

    def __init__(self) -> None:
        super().__init__("Local Executor crash recovery is unavailable")


@runtime_checkable
class ExecutorCrashRecoveryClock(Protocol):
    def now(self) -> datetime: ...


class ExecutorCrashRecoveryCoordinator:
    """Run once after a supervisor restart and never redispatch an external effect."""

    def __init__(
        self,
        *,
        ledger: ExecutorLedger,
        clock: ExecutorCrashRecoveryClock,
        id_source: Callable[[], object] = uuid4,
        window: BrowserWindow | None = None,
    ) -> None:
        if (
            not isinstance(ledger, ExecutorLedger)
            or not isinstance(clock, ExecutorCrashRecoveryClock)
            or not callable(id_source)
            or (window is not None and not isinstance(window, BrowserWindow))
        ):
            raise ExecutorCrashRecoveryRejected
        self._ledger = ledger
        self._clock = clock
        self._id_source = id_source
        self._window = window
        self._executed = False

    def __repr__(self) -> str:
        return "ExecutorCrashRecoveryCoordinator(<redacted>)"

    def run(self) -> tuple[TaskEventEnvelope, ...]:
        if self._executed:
            raise ExecutorCrashRecoveryRejected
        self._executed = True
        result: tuple[TaskEventEnvelope, ...] | None = None
        with suppress(Exception):
            result = self._run()
        if result is None:
            raise ExecutorCrashRecoveryRejected from None
        return result

    def _run(self) -> tuple[TaskEventEnvelope, ...]:
        effects = self._ledger.list_crash_recovery_side_effects(limit=_MAX_RECOVERABLE_EFFECTS)
        existing: list[TaskEventEnvelope] = []
        facts: list[DouyinActionResultFact] = []
        for effect in effects:
            existing_event = self._ledger.get_side_effect_recovery_event(effect.action_id)
            if existing_event is not None:
                existing.append(existing_event)
                continue
            if effect.state is SideEffectState.PREPARED:
                continue
            recovery = (
                DouyinSideEffectRecovery.without_page_context(
                    ledger=self._ledger,
                    clock=self._clock,
                )
                if self._window is None
                else DouyinSideEffectRecovery(
                    window=self._window,
                    ledger=self._ledger,
                    clock=self._clock,
                )
            )
            receipt = recovery.run(action_id=ProtocolActionId(effect.action_id))
            facts.append(recovered_action_result(receipt))

        if any(event.message_type == "task.outcome_uncertain" for event in existing):
            return tuple(existing)
        uncertain = next(
            (fact for fact in facts if fact.message_type == "task.outcome_uncertain"),
            None,
        )
        if uncertain is not None:
            return (*existing, self._project(uncertain))
        projected_events = list(existing)
        for fact in facts:
            projected_events.append(self._project(fact))
        return tuple(projected_events)

    def _project(self, fact: DouyinActionResultFact) -> TaskEventEnvelope:
        source = self._ledger.initial_task_command(self._attempt_id(fact))
        checkpoint = self._ledger.get_checkpoint(str(source.execution_attempt_id))
        if checkpoint is None or checkpoint.state not in {
            AttemptCheckpointState.RUNNING,
            AttemptCheckpointState.PAUSED,
        }:
            raise ValueError
        now = self._now()
        event = TaskEventEnvelope.model_validate(
            {
                "protocol_version": EXECUTOR_PROTOCOL_VERSION,
                "message_id": self._new_id(),
                "message_type": fact.message_type,
                "sent_at": now,
                "deadline_at": now + _MESSAGE_DEADLINE,
                "installation_id": self._ledger.installation_id,
                "executor_id": self._ledger.executor_id,
                "correlation_id": str(source.correlation_id),
                "idempotency_key": f"executor:recovery:{fact.action_id}",
                "sequence": checkpoint.last_event_sequence + 1,
                "payload": fact.payload,
                "task_id": str(source.task_id),
                "execution_attempt_id": str(source.execution_attempt_id),
            }
        )
        committed = self._ledger.commit_side_effect_recovery(
            action_id=str(fact.action_id),
            expected_checkpoint_revision=checkpoint.revision,
            event=event,
        )
        if not isinstance(committed.message, TaskEventEnvelope):
            raise ValueError
        return committed.message

    def _attempt_id(self, fact: DouyinActionResultFact) -> str:
        effect = self._ledger.get_side_effect(str(fact.action_id))
        if effect is None or effect.target_id != str(fact.target_id):
            raise ValueError
        return effect.execution_attempt_id

    def _now(self) -> datetime:
        value = self._clock.now()
        if type(value) is not datetime or value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError
        return value.astimezone(UTC)

    def _new_id(self) -> str:
        value = self._id_source()
        if not isinstance(value, UUID) or value.version != 4 or value.variant != RFC_4122:
            raise ValueError
        return str(value)


__all__ = [
    "ExecutorCrashRecoveryClock",
    "ExecutorCrashRecoveryCoordinator",
    "ExecutorCrashRecoveryRejected",
]
