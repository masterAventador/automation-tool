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
    OutboxEntry,
    PendingTaskControl,
)
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
    TaskCommandResultEnvelope,
    TaskEventEnvelope,
    action_authorization_idempotency_key,
)

NOW = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
INSTALLATION_ID = ProtocolInstallationId("123e4567-e89b-42d3-a456-426614174003")
EXECUTOR_ID = ProtocolExecutorId("123e4567-e89b-42d3-a456-426614174004")
TASK_ID = ProtocolTaskId("123e4567-e89b-42d3-a456-426614174005")
ATTEMPT_ID = ProtocolExecutionAttemptId("123e4567-e89b-42d3-a456-426614174006")


def resource_id(index: int) -> str:
    return str(UUID(f"323e4567-e89b-42d3-a456-{index:012d}"))


@dataclass
class MutableClock:
    value: datetime = NOW

    def now(self) -> datetime:
        return self.value


class DeterministicIds:
    def __init__(self) -> None:
        self._values = count(1)

    def __call__(self) -> UUID:
        return UUID(f"923e4567-e89b-42d3-a456-{next(self._values):012d}")


def command(
    message_type: str,
    *,
    sequence: int,
    message_id: str,
    correlation_id: str,
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
            "correlation_id": correlation_id,
            "idempotency_key": f"executor:h801:{message_type}:{sequence}",
            "sequence": sequence,
            "payload": {},
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


def control_event(
    control: TaskCommandEnvelope,
    *,
    message_type: str,
    sequence: int,
    message_id: str,
) -> TaskEventEnvelope:
    return TaskEventEnvelope.model_validate(
        {
            "protocol_version": "1.0",
            "message_id": message_id,
            "message_type": message_type,
            "sent_at": NOW,
            "deadline_at": NOW + timedelta(minutes=5),
            "installation_id": str(INSTALLATION_ID),
            "executor_id": str(EXECUTOR_ID),
            "correlation_id": str(control.correlation_id),
            "idempotency_key": f"executor:h801:event:{message_type}:{sequence}:{message_id}",
            "sequence": sequence,
            "payload": {},
            "task_id": str(TASK_ID),
            "execution_attempt_id": str(ATTEMPT_ID),
        }
    )


def active_processor(state_directory: Path) -> ExecutorCommandProcessor:
    opened = ExecutorLedger(
        state_directory=state_directory,
        installation_id=str(INSTALLATION_ID),
        executor_id=str(EXECUTOR_ID),
    )
    offer = command(
        "task.offer",
        sequence=1,
        message_id=resource_id(1),
        correlation_id=resource_id(101),
    )
    opened.receive_command(offer)
    opened.compare_and_set_checkpoint(
        attempt_id=str(ATTEMPT_ID),
        expected_revision=1,
        state=AttemptCheckpointState.RUNNING,
        last_event_sequence=2,
    )
    return ExecutorCommandProcessor(
        ledger=opened,
        installation_id=str(INSTALLATION_ID),
        executor_id=str(EXECUTOR_ID),
        clock=MutableClock(),
        id_source=cast(Callable[[], object], DeterministicIds()),
    )


def action_claims(index: int) -> ActionAuthorizationClaims:
    action_id = ProtocolActionId(resource_id(index))
    return ActionAuthorizationClaims(
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


def prepare_action(
    opened: ExecutorLedger,
    index: int,
    *,
    dispatch: bool,
    offset_seconds: int = 0,
) -> tuple[ActionAuthorizationClaims, bytes]:
    claims = action_claims(index)
    fingerprint = hashlib.sha256(f"effect-{index}".encode()).digest()
    opened.admit_action(
        claims=claims,
        authorization_fingerprint=hashlib.sha256(f"authorization-{index}".encode()).digest(),
        admitted_at=NOW + timedelta(seconds=offset_seconds),
        minimum_interval_seconds=1,
        task_action_limit=100,
    )
    opened.prepare_side_effect(
        action_id=str(claims.action_id),
        effect_fingerprint=fingerprint,
        prepared_at=NOW + timedelta(seconds=offset_seconds + 1),
    )
    if dispatch:
        opened.begin_side_effect_dispatch(
            action_id=str(claims.action_id),
            effect_fingerprint=fingerprint,
            dispatched_at=NOW + timedelta(seconds=offset_seconds + 2),
        )
    return claims, fingerprint


def test_pause_acknowledges_then_projects_paused_only_at_a_safe_checkpoint(
    tmp_path: Path,
) -> None:
    processor = active_processor(tmp_path / "safe")
    pause = command(
        "task.pause",
        sequence=2,
        message_id=resource_id(2),
        correlation_id=resource_id(102),
    )

    first = processor.handle(source(pause))
    replay = processor.handle(source(pause))

    assert [message.message_type for message in first] == ["task.control_ack", "task.paused"]
    assert replay == first
    assert isinstance(first[0], TaskCommandResultEnvelope)
    assert isinstance(first[1], TaskEventEnvelope)
    assert first[0].payload == {"acknowledged": True}
    assert first[0].sequence == pause.sequence
    assert first[1].sequence == 3
    assert first[1].correlation_id == pause.correlation_id
    checkpoint = processor.ledger.get_checkpoint(str(ATTEMPT_ID))
    assert checkpoint is not None
    assert checkpoint.state is AttemptCheckpointState.PAUSED
    assert checkpoint.last_event_sequence == 3
    assert processor.poll_controls() == ()


def test_pause_waits_for_dispatched_action_and_blocks_every_new_dispatch(
    tmp_path: Path,
) -> None:
    processor = active_processor(tmp_path / "atomic")
    running, running_fingerprint = prepare_action(processor.ledger, 10, dispatch=True)
    waiting, waiting_fingerprint = prepare_action(
        processor.ledger,
        20,
        dispatch=False,
        offset_seconds=2,
    )
    pause = command(
        "task.pause",
        sequence=2,
        message_id=resource_id(3),
        correlation_id=resource_id(103),
    )

    acknowledged = processor.handle(source(pause))

    assert [message.message_type for message in acknowledged] == ["task.control_ack"]
    assert processor.poll_controls() == ()
    checkpoint = processor.ledger.get_checkpoint(str(ATTEMPT_ID))
    assert checkpoint is not None
    assert checkpoint.state is AttemptCheckpointState.RUNNING
    with pytest.raises(ExecutorLedgerRejected):
        processor.ledger.begin_side_effect_dispatch(
            action_id=str(waiting.action_id),
            effect_fingerprint=waiting_fingerprint,
            dispatched_at=NOW + timedelta(seconds=3),
        )

    processor.ledger.verify_side_effect(
        action_id=str(running.action_id),
        effect_fingerprint=running_fingerprint,
        verification_fingerprint=hashlib.sha256(b"verified").digest(),
        verified_at=NOW + timedelta(seconds=4),
    )
    paused = processor.poll_controls()

    assert [message.message_type for message in paused] == ["task.paused"]
    assert processor.poll_controls() == ()
    checkpoint = processor.ledger.get_checkpoint(str(ATTEMPT_ID))
    assert checkpoint is not None
    assert checkpoint.state is AttemptCheckpointState.PAUSED
    with pytest.raises(ExecutorLedgerRejected):
        processor.ledger.begin_side_effect_dispatch(
            action_id=str(waiting.action_id),
            effect_fingerprint=waiting_fingerprint,
            dispatched_at=NOW + timedelta(seconds=5),
        )


def test_resume_is_durable_and_only_then_reopens_dispatch(
    tmp_path: Path,
) -> None:
    processor = active_processor(tmp_path / "resume")
    waiting, fingerprint = prepare_action(processor.ledger, 30, dispatch=False)
    pause = command(
        "task.pause",
        sequence=2,
        message_id=resource_id(4),
        correlation_id=resource_id(104),
    )
    resume = command(
        "task.resume",
        sequence=3,
        message_id=resource_id(5),
        correlation_id=resource_id(105),
    )

    assert [message.message_type for message in processor.handle(source(pause))] == [
        "task.control_ack",
        "task.paused",
    ]
    resumed = processor.handle(source(resume))

    assert [message.message_type for message in resumed] == ["task.control_ack", "task.resumed"]
    checkpoint = processor.ledger.get_checkpoint(str(ATTEMPT_ID))
    assert checkpoint is not None
    assert checkpoint.state is AttemptCheckpointState.RUNNING
    assert checkpoint.last_event_sequence == 4
    dispatched = processor.ledger.begin_side_effect_dispatch(
        action_id=str(waiting.action_id),
        effect_fingerprint=fingerprint,
        dispatched_at=NOW + timedelta(seconds=3),
    )
    assert dispatched.replayed is False


def test_control_state_and_type_fail_closed_without_poisoning_the_attempt(
    tmp_path: Path,
) -> None:
    processor = active_processor(tmp_path / "invalid")
    invalid_resume = command(
        "task.resume",
        sequence=2,
        message_id=resource_id(6),
        correlation_id=resource_id(106),
    )
    cancel = command(
        "task.cancel",
        sequence=2,
        message_id=resource_id(7),
        correlation_id=resource_id(107),
    )

    for rejected in (invalid_resume, cancel):
        with pytest.raises(ExecutorCommandRejected):
            processor.handle(source(rejected))
        checkpoint = processor.ledger.get_checkpoint(str(ATTEMPT_ID))
        assert checkpoint is not None
        assert checkpoint.state is AttemptCheckpointState.RUNNING
        assert checkpoint.last_command_sequence == 1


def test_control_ledger_boundaries_replay_and_corruption_fail_closed(
    tmp_path: Path,
) -> None:
    opened = active_processor(tmp_path / "boundaries")
    pause = command(
        "task.pause",
        sequence=2,
        message_id=resource_id(40),
        correlation_id=resource_id(140),
    )
    paused_event = control_event(
        pause,
        message_type="task.paused",
        sequence=3,
        message_id=resource_id(41),
    )

    with pytest.raises(ExecutorLedgerRejected):
        PendingTaskControl(command=pause, checkpoint_revision=0, next_event_sequence=3)
    for limit in (0, True):
        with pytest.raises(ExecutorLedgerRejected):
            opened.ledger.pending_task_controls(limit=limit)
    with pytest.raises(ExecutorLedgerRejected):
        opened.ledger.complete_task_control(
            source_message_id=resource_id(999),
            expected_checkpoint_revision=2,
            event=paused_event,
        )
    with pytest.raises(ExecutorLedgerRejected):
        opened.ledger.complete_task_control(
            source_message_id=resource_id(1),
            expected_checkpoint_revision=0,
            event=paused_event,
        )
    with pytest.raises(ExecutorLedgerRejected):
        opened.ledger.complete_task_control(
            source_message_id=resource_id(1),
            expected_checkpoint_revision=2,
            event=paused_event,
        )

    projected = opened.handle(source(pause))
    replay = opened.ledger.complete_task_control(
        source_message_id=str(pause.message_id),
        expected_checkpoint_revision=3,
        event=cast(TaskEventEnvelope, projected[1]),
    )
    assert replay is not None
    assert replay.replayed is True
    assert replay.message == projected[1]

    corrupt = active_processor(tmp_path / "corrupt")
    pending_pause = command(
        "task.pause",
        sequence=2,
        message_id=resource_id(42),
        correlation_id=resource_id(142),
    )
    corrupt.ledger.receive_command(pending_pause)
    corrupt.ledger.enqueue_outbox(
        source_message_id=str(pending_pause.message_id),
        message=TaskCommandResultEnvelope.model_validate(
            {
                **pending_pause.model_dump(mode="json"),
                "message_id": resource_id(43),
                "message_type": "task.control_ack",
                "idempotency_key": "executor:h801:corrupt-control-ack",
                "payload": {"acknowledged": True},
            }
        ),
    )
    lifecycle = {
        **pending_pause.model_dump(mode="json", exclude={"task_id", "execution_attempt_id"}),
        "message_type": "executor.heartbeat",
        "idempotency_key": "executor:h801:corrupt-heartbeat",
        "payload": {"status": "healthy"},
    }
    with sqlite3.connect(corrupt.ledger.database_path) as connection:
        connection.execute(
            "UPDATE executor_commands SET envelope = ? WHERE message_id = ?",
            (
                json.dumps(lifecycle, separators=(",", ":"), sort_keys=True),
                str(pending_pause.message_id),
            ),
        )
    with pytest.raises(ExecutorLedgerRejected):
        corrupt.ledger.pending_task_controls(limit=1)


def test_control_projection_checks_ack_identity_sequence_and_dispatched_window(
    tmp_path: Path,
) -> None:
    processor = active_processor(tmp_path / "projection")
    running, fingerprint = prepare_action(processor.ledger, 50, dispatch=True)
    pause = command(
        "task.pause",
        sequence=2,
        message_id=resource_id(51),
        correlation_id=resource_id(151),
    )
    assert [message.message_type for message in processor.handle(source(pause))] == [
        "task.control_ack"
    ]
    checkpoint = processor.ledger.get_checkpoint(str(ATTEMPT_ID))
    assert checkpoint is not None
    paused_event = control_event(
        pause,
        message_type="task.paused",
        sequence=3,
        message_id=resource_id(52),
    )

    assert (
        processor.ledger.complete_task_control(
            source_message_id=str(pause.message_id),
            expected_checkpoint_revision=checkpoint.revision,
            event=paused_event,
        )
        is None
    )
    with pytest.raises(ExecutorLedgerRejected):
        processor.ledger.complete_task_control(
            source_message_id=str(pause.message_id),
            expected_checkpoint_revision=checkpoint.revision,
            event=control_event(
                pause,
                message_type="task.paused",
                sequence=4,
                message_id=resource_id(53),
            ),
        )
    processor.ledger.verify_side_effect(
        action_id=str(running.action_id),
        effect_fingerprint=fingerprint,
        verification_fingerprint=hashlib.sha256(b"projection-verified").digest(),
        verified_at=NOW + timedelta(seconds=4),
    )


def test_control_processor_handles_unrelated_pending_and_projection_races(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    processor = active_processor(tmp_path / "processor-race")
    pause = command(
        "task.pause",
        sequence=2,
        message_id=resource_id(60),
        correlation_id=resource_id(160),
    )
    unrelated = command(
        "task.pause",
        sequence=2,
        message_id=resource_id(61),
        correlation_id=resource_id(161),
    )
    monkeypatch.setattr(
        processor.ledger,
        "pending_task_controls",
        lambda *, limit: (
            PendingTaskControl(
                command=unrelated,
                checkpoint_revision=3,
                next_event_sequence=3,
            ),
            PendingTaskControl(
                command=pause,
                checkpoint_revision=3,
                next_event_sequence=3,
            ),
        ),
    )

    def replay_projection(**arguments: object) -> OutboxEntry:
        event = cast(TaskEventEnvelope, arguments["event"])
        return OutboxEntry(
            message=event,
            source_message_id=str(pause.message_id),
            replayed=True,
        )

    monkeypatch.setattr(processor.ledger, "complete_task_control", replay_projection)
    assert [message.message_type for message in processor.handle(source(pause))] == [
        "task.control_ack"
    ]

    corrupt = active_processor(tmp_path / "processor-corrupt")
    corrupt_pause = command(
        "task.pause",
        sequence=2,
        message_id=resource_id(62),
        correlation_id=resource_id(162),
    )
    non_ack = OutboxEntry(
        message=control_event(
            corrupt_pause,
            message_type="task.paused",
            sequence=3,
            message_id=resource_id(63),
        ),
        source_message_id=str(corrupt_pause.message_id),
        replayed=False,
    )
    responses = iter(((), (non_ack,)))
    monkeypatch.setattr(
        corrupt.ledger,
        "outbox_for_command",
        lambda _message_id: next(responses),
    )
    with pytest.raises(ExecutorCommandRejected):
        corrupt.handle(source(corrupt_pause))
