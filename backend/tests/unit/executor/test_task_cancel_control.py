from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import count
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from automation_tool.executor.command_processor import (
    ExecutorCommandProcessor,
    ExecutorCommandRejected,
)
from automation_tool.executor.ledger import (
    AttemptCheckpointState,
    ExecutorLedger,
    ExecutorLedgerRejected,
    PendingTaskControl,
)
from automation_tool.executor.side_effect_ledger import SideEffectState
from automation_tool.protocol import (
    ACTION_AUTHORIZATION_VERSION,
    ActionAuthorizationClaims,
    DouyinSearchExposureAction,
    ProtocolActionId,
    ProtocolExecutionAttemptId,
    ProtocolExecutorId,
    ProtocolInstallationId,
    ProtocolTargetId,
    ProtocolTaskId,
    TaskCommandEnvelope,
    TaskEventEnvelope,
    action_authorization_idempotency_key,
)

NOW = datetime(2026, 7, 21, 14, 0, tzinfo=UTC)
INSTALLATION_ID = ProtocolInstallationId("123e4567-e89b-42d3-a456-426614174003")
EXECUTOR_ID = ProtocolExecutorId("123e4567-e89b-42d3-a456-426614174004")
TASK_ID = ProtocolTaskId("123e4567-e89b-42d3-a456-426614174005")
ATTEMPT_ID = ProtocolExecutionAttemptId("123e4567-e89b-42d3-a456-426614174006")


def resource_id(index: int) -> str:
    return str(UUID(f"423e4567-e89b-42d3-a456-{index:012d}"))


@dataclass
class FixedClock:
    @staticmethod
    def now() -> datetime:
        return NOW


class DeterministicIds:
    def __init__(self) -> None:
        self._values = count(1)

    def __call__(self) -> UUID:
        return UUID(f"a23e4567-e89b-42d3-a456-{next(self._values):012d}")


def command(
    message_type: str,
    *,
    sequence: int,
    message_id: str,
) -> TaskCommandEnvelope:
    return TaskCommandEnvelope.model_validate(
        {
            "protocol_version": "1.0",
            "message_id": message_id,
            "message_type": message_type,
            "sent_at": NOW,
            "deadline_at": NOW + timedelta(minutes=5),
            "installation_id": str(INSTALLATION_ID),
            "executor_id": str(EXECUTOR_ID),
            "correlation_id": resource_id(sequence + 500),
            "idempotency_key": f"executor:h802:{message_type}:{sequence}:{message_id}",
            "sequence": sequence,
            "payload": (
                {"task_event_sequence_baseline": 0} if message_type == "task.offer" else {}
            ),
            "task_id": str(TASK_ID),
            "execution_attempt_id": str(ATTEMPT_ID),
        }
    )


def source(value: TaskCommandEnvelope) -> str:
    return json.dumps(
        value.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def event(
    value: TaskCommandEnvelope,
    *,
    message_type: str,
    sequence: int,
    message_id: str,
) -> TaskEventEnvelope:
    return TaskEventEnvelope.model_validate(
        {
            **value.model_dump(mode="json"),
            "message_id": message_id,
            "message_type": message_type,
            "idempotency_key": f"executor:h802:event:{message_type}:{message_id}",
            "sequence": sequence,
            "payload": {},
        }
    )


def running_processor(state_directory: Path) -> ExecutorCommandProcessor:
    ledger = ExecutorLedger(
        state_directory=state_directory,
        installation_id=str(INSTALLATION_ID),
        executor_id=str(EXECUTOR_ID),
    )
    ledger.receive_command(command("task.offer", sequence=1, message_id=resource_id(1)))
    ledger.compare_and_set_checkpoint(
        attempt_id=str(ATTEMPT_ID),
        expected_revision=1,
        state=AttemptCheckpointState.RUNNING,
        last_event_sequence=2,
    )
    return ExecutorCommandProcessor(
        ledger=ledger,
        installation_id=str(INSTALLATION_ID),
        executor_id=str(EXECUTOR_ID),
        clock=FixedClock(),
        id_source=cast(Callable[[], object], DeterministicIds()),
    )


def prepare_action(
    ledger: ExecutorLedger,
    *,
    index: int,
    dispatch: bool,
    offset_seconds: int = 0,
) -> tuple[str, bytes]:
    action_id = ProtocolActionId(resource_id(index))
    claims = ActionAuthorizationClaims(
        version=ACTION_AUTHORIZATION_VERSION,
        action_id=action_id,
        target_id=ProtocolTargetId(resource_id(index + 100)),
        execution_attempt_id=ATTEMPT_ID,
        task_id=TASK_ID,
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
        platform="douyin",
        action=DouyinSearchExposureAction.COMMENT,
        idempotency_key=action_authorization_idempotency_key(action_id),
        authorized_at=NOW,
        deadline_at=NOW + timedelta(minutes=5),
    )
    effect = hashlib.sha256(f"h8-02-effect-{index}".encode()).digest()
    ledger.admit_action(
        claims=claims,
        authorization_fingerprint=hashlib.sha256(f"h8-02-authorization-{index}".encode()).digest(),
        admitted_at=NOW + timedelta(seconds=offset_seconds),
        minimum_interval_seconds=1,
        task_action_limit=100,
    )
    ledger.prepare_side_effect(
        action_id=str(action_id),
        effect_fingerprint=effect,
        prepared_at=NOW + timedelta(seconds=offset_seconds + 1),
    )
    if dispatch:
        ledger.begin_side_effect_dispatch(
            action_id=str(action_id),
            effect_fingerprint=effect,
            dispatched_at=NOW + timedelta(seconds=offset_seconds + 2),
        )
    return str(action_id), effect


def test_safe_cancel_acknowledges_then_confirms_cancelled_once(tmp_path: Path) -> None:
    processor = running_processor(tmp_path / "safe-cancel")
    cancel = command("task.cancel", sequence=2, message_id=resource_id(2))

    first = processor.handle(source(cancel))
    replay = processor.handle(source(cancel))

    assert [message.message_type for message in first] == [
        "task.control_ack",
        "task.cancelled",
    ]
    assert replay == first
    checkpoint = processor.ledger.get_checkpoint(str(ATTEMPT_ID))
    assert checkpoint is not None
    assert checkpoint.state is AttemptCheckpointState.TERMINAL
    assert checkpoint.last_command_sequence == 2
    assert checkpoint.last_event_sequence == 3
    with pytest.raises(ExecutorLedgerRejected):
        processor.ledger.complete_task_control(
            source_message_id=str(cancel.message_id),
            expected_checkpoint_revision=3,
            event=event(
                cancel,
                message_type="task.cancelled",
                sequence=4,
                message_id=resource_id(5),
            ),
        )


def test_cancel_blocks_new_dispatch_and_waits_for_the_atomic_action(
    tmp_path: Path,
) -> None:
    processor = running_processor(tmp_path / "cancel-window")
    dispatched_id, dispatched_effect = prepare_action(
        processor.ledger,
        index=10,
        dispatch=True,
    )
    waiting_id, waiting_effect = prepare_action(
        processor.ledger,
        index=20,
        dispatch=False,
        offset_seconds=2,
    )
    cancel = command("task.cancel", sequence=2, message_id=resource_id(3))

    acknowledged = processor.handle(source(cancel))

    assert [message.message_type for message in acknowledged] == ["task.control_ack"]
    with pytest.raises(ExecutorLedgerRejected):
        processor.ledger.begin_side_effect_dispatch(
            action_id=waiting_id,
            effect_fingerprint=waiting_effect,
            dispatched_at=NOW + timedelta(seconds=4),
        )
    assert processor.poll_controls() == ()

    processor.ledger.mark_side_effect_uncertain(
        action_id=dispatched_id,
        effect_fingerprint=dispatched_effect,
        uncertain_at=NOW + timedelta(seconds=5),
    )
    terminal = processor.poll_controls()

    assert [message.message_type for message in terminal] == ["task.outcome_uncertain"]
    checkpoint = processor.ledger.get_checkpoint(str(ATTEMPT_ID))
    assert checkpoint is not None
    assert checkpoint.state is AttemptCheckpointState.OUTCOME_UNCERTAIN
    with pytest.raises(ExecutorLedgerRejected):
        processor.ledger.begin_side_effect_dispatch(
            action_id=waiting_id,
            effect_fingerprint=waiting_effect,
            dispatched_at=NOW + timedelta(seconds=6),
        )


def test_cancel_after_a_verified_atomic_action_confirms_cancelled(tmp_path: Path) -> None:
    processor = running_processor(tmp_path / "verified-window")
    action_id, effect = prepare_action(processor.ledger, index=30, dispatch=True)
    cancel = command("task.cancel", sequence=2, message_id=resource_id(4))

    assert [message.message_type for message in processor.handle(source(cancel))] == [
        "task.control_ack"
    ]
    processor.ledger.verify_side_effect(
        action_id=action_id,
        effect_fingerprint=effect,
        verification_fingerprint=hashlib.sha256(b"h8-02-verified").digest(),
        verified_at=NOW + timedelta(seconds=3),
    )

    terminal = processor.poll_controls()

    assert [message.message_type for message in terminal] == ["task.cancelled"]
    checkpoint = processor.ledger.get_checkpoint(str(ATTEMPT_ID))
    assert checkpoint is not None
    assert checkpoint.state is AttemptCheckpointState.TERMINAL


def test_cancel_from_a_safe_paused_checkpoint_confirms_cancelled(tmp_path: Path) -> None:
    processor = running_processor(tmp_path / "paused-cancel")
    pause = command("task.pause", sequence=2, message_id=resource_id(40))
    cancel = command("task.cancel", sequence=3, message_id=resource_id(41))

    assert [message.message_type for message in processor.handle(source(pause))] == [
        "task.control_ack",
        "task.paused",
    ]
    cancelled = processor.handle(source(cancel))

    assert [message.message_type for message in cancelled] == [
        "task.control_ack",
        "task.cancelled",
    ]
    assert cancelled[-1].sequence == 4
    checkpoint = processor.ledger.get_checkpoint(str(ATTEMPT_ID))
    assert checkpoint is not None
    assert checkpoint.state is AttemptCheckpointState.TERMINAL
    assert checkpoint.last_command_sequence == 3


def test_cancel_rejects_received_and_terminal_attempts_without_poisoning(
    tmp_path: Path,
) -> None:
    received_ledger = ExecutorLedger(
        state_directory=tmp_path / "received-cancel",
        installation_id=str(INSTALLATION_ID),
        executor_id=str(EXECUTOR_ID),
    )
    received_ledger.receive_command(command("task.offer", sequence=1, message_id=resource_id(50)))
    received = ExecutorCommandProcessor(
        ledger=received_ledger,
        installation_id=str(INSTALLATION_ID),
        executor_id=str(EXECUTOR_ID),
        clock=FixedClock(),
        id_source=cast(Callable[[], object], DeterministicIds()),
    )
    with pytest.raises(
        ExecutorCommandRejected,
        match=r"^Local Executor command is rejected$",
    ):
        received.handle(source(command("task.cancel", sequence=2, message_id=resource_id(51))))
    received_checkpoint = received.ledger.get_checkpoint(str(ATTEMPT_ID))
    assert received_checkpoint is not None
    assert received_checkpoint.state is AttemptCheckpointState.RECEIVED
    assert received_checkpoint.last_command_sequence == 1

    terminal = running_processor(tmp_path / "terminal-cancel")
    first = command("task.cancel", sequence=2, message_id=resource_id(52))
    terminal.handle(source(first))
    with pytest.raises(
        ExecutorCommandRejected,
        match=r"^Local Executor command is rejected$",
    ):
        terminal.handle(source(command("task.cancel", sequence=3, message_id=resource_id(53))))
    terminal_checkpoint = terminal.ledger.get_checkpoint(str(ATTEMPT_ID))
    assert terminal_checkpoint is not None
    assert terminal_checkpoint.state is AttemptCheckpointState.TERMINAL
    assert terminal_checkpoint.last_command_sequence == 2


def test_cancel_projection_recomputes_uncertainty_and_rejects_wrong_terminal_event(
    tmp_path: Path,
) -> None:
    processor = running_processor(tmp_path / "uncertain-projection")
    action_id, effect = prepare_action(processor.ledger, index=60, dispatch=True)
    cancel = command("task.cancel", sequence=2, message_id=resource_id(61))
    assert [message.message_type for message in processor.handle(source(cancel))] == [
        "task.control_ack"
    ]
    processor.ledger.mark_side_effect_uncertain(
        action_id=action_id,
        effect_fingerprint=effect,
        uncertain_at=NOW + timedelta(seconds=3),
    )
    pending = processor.ledger.pending_task_controls(limit=1)
    assert len(pending) == 1
    assert pending[0].outcome_uncertain is True
    assert pending[0].event_type == "task.outcome_uncertain"

    with pytest.raises(ExecutorLedgerRejected):
        processor.ledger.complete_task_control(
            source_message_id=str(cancel.message_id),
            expected_checkpoint_revision=pending[0].checkpoint_revision,
            event=event(
                cancel,
                message_type="task.cancelled",
                sequence=pending[0].next_event_sequence,
                message_id=resource_id(62),
            ),
        )

    assert [message.message_type for message in processor.poll_controls()] == [
        "task.outcome_uncertain"
    ]
    with pytest.raises(ExecutorLedgerRejected):
        PendingTaskControl(
            command=cancel,
            checkpoint_revision=3,
            next_event_sequence=3,
            outcome_uncertain=cast(bool, 1),
        )
    with pytest.raises(ExecutorLedgerRejected):
        PendingTaskControl(
            command=command("task.pause", sequence=2, message_id=resource_id(63)),
            checkpoint_revision=3,
            next_event_sequence=3,
            outcome_uncertain=True,
        )


def test_emergency_stop_atomically_latches_and_settles_inflight_action_uncertain(
    tmp_path: Path,
) -> None:
    processor = running_processor(tmp_path / "emergency-stop")
    dispatched_id, _dispatched_effect = prepare_action(
        processor.ledger,
        index=70,
        dispatch=True,
        offset_seconds=-4,
    )
    waiting_id, waiting_effect = prepare_action(
        processor.ledger,
        index=80,
        dispatch=False,
        offset_seconds=-1,
    )
    emergency_stop = command(
        "task.emergency_stop",
        sequence=2,
        message_id=resource_id(71),
    )

    first = processor.handle(source(emergency_stop))
    replay = processor.handle(source(emergency_stop))

    assert [message.message_type for message in first] == [
        "task.control_ack",
        "task.outcome_uncertain",
    ]
    assert replay == first
    assert processor.ledger.get_action_emergency_stop().engaged is True
    dispatched = processor.ledger.get_side_effect(dispatched_id)
    assert dispatched is not None
    assert dispatched.state is SideEffectState.UNCERTAIN
    checkpoint = processor.ledger.get_checkpoint(str(ATTEMPT_ID))
    assert checkpoint is not None
    assert checkpoint.state is AttemptCheckpointState.OUTCOME_UNCERTAIN
    assert checkpoint.last_command_sequence == 2
    assert checkpoint.last_event_sequence == 3
    with pytest.raises(ExecutorLedgerRejected):
        processor.ledger.begin_side_effect_dispatch(
            action_id=waiting_id,
            effect_fingerprint=waiting_effect,
            dispatched_at=NOW + timedelta(seconds=1),
        )


def test_emergency_stop_rejects_wrong_entrypoints_future_dispatch_and_storage_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    processor = running_processor(tmp_path / "emergency-failure-matrix")
    emergency_stop = command(
        "task.emergency_stop",
        sequence=2,
        message_id=resource_id(91),
    )
    with pytest.raises(ExecutorLedgerRejected):
        processor.ledger.receive_task_emergency_stop(
            command("task.cancel", sequence=2, message_id=resource_id(92)),
            changed_at=NOW,
        )
    with pytest.raises(ExecutorLedgerRejected):
        processor.ledger.receive_command(emergency_stop)

    dispatched_id, _ = prepare_action(
        processor.ledger,
        index=93,
        dispatch=True,
    )
    with pytest.raises(ExecutorCommandRejected):
        processor.handle(source(emergency_stop))
    dispatched = processor.ledger.get_side_effect(dispatched_id)
    assert dispatched is not None
    assert dispatched.state is SideEffectState.DISPATCHED
    assert processor.ledger.get_action_emergency_stop().engaged is False
    assert processor.ledger.has_received_task_emergency_stop() is False

    with monkeypatch.context() as scoped:
        scoped.setattr(
            processor.ledger,
            "_connect",
            lambda: (_ for _ in ()).throw(OSError()),
        )
        with pytest.raises(ExecutorLedgerRejected):
            processor.ledger.has_received_task_emergency_stop()


def test_emergency_latch_rejects_an_impossible_lost_locked_update() -> None:
    class Result:
        def __init__(self, row: tuple[object, ...] | None, rowcount: int) -> None:
            self._row = row
            self.rowcount = rowcount

        def fetchone(self) -> tuple[object, ...] | None:
            return self._row

    class LostUpdateConnection:
        def execute(self, statement: str, _parameters: object = None) -> Result:
            if "SELECT engaged" in statement:
                return Result((0, 0, None), -1)
            return Result(None, 0)

    with pytest.raises(ValueError):
        ExecutorLedger._engage_action_latch_in_connection(
            cast(sqlite3.Connection, LostUpdateConnection()),
            changed_at=NOW,
        )
