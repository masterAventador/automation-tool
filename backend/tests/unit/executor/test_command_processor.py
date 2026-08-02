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

from automation_tool.executor import command_processor as command_processor_module
from automation_tool.executor.command_processor import (
    ExecutorCommandExpired,
    ExecutorCommandProcessor,
    ExecutorCommandRejected,
    SystemExecutorCommandClock,
)
from automation_tool.executor.ledger import (
    AttemptCheckpoint,
    AttemptCheckpointState,
    CommandReceipt,
    ExecutorLedger,
    ExecutorLedgerRejected,
)
from automation_tool.protocol import (
    TaskCommandEnvelope,
    TaskCommandResultEnvelope,
    TaskEventEnvelope,
    parse_executor_message,
)

NOW = datetime(2026, 7, 19, 14, 0, tzinfo=UTC)
INSTALLATION_ID = "123e4567-e89b-42d3-a456-426614174003"
EXECUTOR_ID = "123e4567-e89b-42d3-a456-426614174004"
TASK_ID = "123e4567-e89b-42d3-a456-426614174005"
ATTEMPT_ID = "123e4567-e89b-42d3-a456-426614174006"


@dataclass
class MutableClock:
    value: datetime = NOW

    def now(self) -> datetime:
        return self.value


class DeterministicIds:
    def __init__(self, *, fail_at: int | None = None) -> None:
        self._values = count(1)
        self._fail_at = fail_at

    def __call__(self) -> UUID:
        value = next(self._values)
        if value == self._fail_at:
            raise RuntimeError("private id failure")
        return UUID(f"923e4567-e89b-42d3-a456-{value:012d}")


def command(
    *,
    message_id: str = "323e4567-e89b-42d3-a456-426614174001",
    idempotency_key: str = "executor-real:offer:1",
    sent_at: datetime = NOW,
    deadline_at: datetime = NOW + timedelta(minutes=5),
    message_type: str = "task.offer",
    installation_id: str = INSTALLATION_ID,
    payload: dict[str, object] | None = None,
) -> str:
    return json.dumps(
        {
            "protocol_version": "1.0",
            "message_id": message_id,
            "message_type": message_type,
            "sent_at": sent_at.isoformat().replace("+00:00", "Z"),
            "deadline_at": deadline_at.isoformat().replace("+00:00", "Z"),
            "installation_id": installation_id,
            "executor_id": EXECUTOR_ID,
            "correlation_id": "323e4567-e89b-42d3-a456-426614174002",
            "idempotency_key": idempotency_key,
            "sequence": 1,
            "payload": (
                {"task_event_sequence_baseline": 0}
                if payload is None and message_type == "task.offer"
                else {}
                if payload is None
                else payload
            ),
            "task_id": TASK_ID,
            "execution_attempt_id": ATTEMPT_ID,
        },
        separators=(",", ":"),
    )


def processor(
    state_directory: Path,
    *,
    clock: object = MutableClock(),
    id_source: object = None,
) -> ExecutorCommandProcessor:
    ledger = ExecutorLedger(
        state_directory=state_directory,
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
    )
    return ExecutorCommandProcessor(
        ledger=ledger,
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
        clock=clock,  # type: ignore[arg-type]
        id_source=cast(
            Callable[[], object],
            DeterministicIds() if id_source is None else id_source,
        ),
    )


def test_offer_is_atomically_checkpointed_without_fabricating_action_success(
    tmp_path: Path,
) -> None:
    state_directory = tmp_path / "state"
    active = processor(state_directory)

    batch = active.handle(command())

    assert [message.message_type for message in batch] == [
        "task.accept",
        "task.started",
    ]
    assert isinstance(batch[0], TaskCommandResultEnvelope)
    assert all(isinstance(message, TaskEventEnvelope) for message in batch[1:])
    assert [message.sequence for message in batch] == [1, 1]
    assert batch[0].payload == {"accepted": True}
    checkpoint = active.ledger.get_checkpoint(ATTEMPT_ID)
    assert checkpoint is not None
    assert checkpoint.state is AttemptCheckpointState.RUNNING
    assert checkpoint.last_event_sequence == 1
    assert checkpoint.revision == 2

    reopened = processor(state_directory, id_source=DeterministicIds())
    assert reopened.handle(command()) == batch
    retry_with_new_wire_identity = command(
        message_id="323e4567-e89b-42d3-a456-426614174099",
        sent_at=NOW + timedelta(seconds=1),
    )
    assert reopened.handle(retry_with_new_wire_identity) == batch


def test_offer_continues_the_task_global_event_sequence_across_attempts(
    tmp_path: Path,
) -> None:
    active = processor(tmp_path / "state")

    batch = active.handle(command(payload={"task_event_sequence_baseline": 4}))

    assert [message.message_type for message in batch] == [
        "task.accept",
        "task.started",
    ]
    assert [message.sequence for message in batch] == [1, 5]
    checkpoint = active.ledger.get_checkpoint(ATTEMPT_ID)
    assert checkpoint is not None
    assert checkpoint.last_event_sequence == 5


def test_offer_rejects_invalid_or_mismatched_internal_event_baselines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    valid = cast(TaskCommandEnvelope, parse_executor_message(command()))
    invalid_commands = (
        valid.model_copy(update={"payload": {"task_event_sequence_baseline": "invalid"}}),
        cast(
            TaskCommandEnvelope,
            parse_executor_message(command(payload={"task_event_sequence_baseline": 4})),
        ),
    )
    receipt = CommandReceipt(
        message_id=str(valid.message_id),
        idempotency_key=valid.idempotency_key,
        task_id=TASK_ID,
        attempt_id=ATTEMPT_ID,
        sequence=1,
        message_type="task.offer",
        replayed=False,
    )
    checkpoint = AttemptCheckpoint(
        task_id=TASK_ID,
        attempt_id=ATTEMPT_ID,
        last_command_sequence=1,
        last_event_sequence=0,
        state=AttemptCheckpointState.RECEIVED,
        revision=1,
    )
    for index, invalid in enumerate(invalid_commands):
        active = processor(tmp_path / f"invalid-baseline-{index}")
        with monkeypatch.context() as scoped:
            scoped.setattr(
                command_processor_module,
                "parse_executor_message",
                lambda _source, value=invalid: value,
            )
            scoped.setattr(active.ledger, "receive_command", lambda _command: receipt)
            scoped.setattr(active.ledger, "outbox_for_command", lambda _message_id: ())
            scoped.setattr(active.ledger, "get_checkpoint", lambda _attempt_id: checkpoint)
            with pytest.raises(ExecutorCommandRejected):
                active.handle("ignored")


def test_recovery_requeues_only_the_same_persisted_outbox_messages(tmp_path: Path) -> None:
    state_directory = tmp_path / "state"
    active = processor(state_directory)
    batch = active.handle(command())
    for message in batch:
        assert active.mark_delivered(str(message.message_id)) is True
    assert active.pending_outbox() == ()

    recovered = processor(state_directory, id_source=DeterministicIds())
    assert recovered.recover_outbox() == batch
    assert recovered.pending_outbox() == batch
    assert recovered.recover_outbox() == batch


def test_recovery_does_not_replay_outbox_messages_after_their_wire_deadline(
    tmp_path: Path,
) -> None:
    state_directory = tmp_path / "state"
    active = processor(state_directory)
    batch = active.handle(command())
    for message in batch:
        assert active.mark_delivered(str(message.message_id)) is True

    recovered = processor(
        state_directory,
        clock=MutableClock(NOW + timedelta(seconds=31)),
    )

    assert recovered.recover_outbox() == ()
    assert recovered.pending_outbox() == ()


def test_recovery_keeps_expired_never_sent_messages_durable(tmp_path: Path) -> None:
    state_directory = tmp_path / "state"
    active = processor(state_directory)
    batch = active.handle(command())

    recovered = processor(
        state_directory,
        clock=MutableClock(NOW + timedelta(seconds=31)),
    )

    assert recovered.recover_outbox() == ()
    assert recovered.pending_outbox() == ()
    with sqlite3.connect(recovered.ledger.database_path) as connection:
        rows = connection.execute(
            """
            SELECT envelope, delivered, expired
            FROM executor_outbox ORDER BY ordinal
            """
        ).fetchall()
    assert tuple(parse_executor_message(str(row[0])) for row in rows) == batch
    assert [(int(row[1]), int(row[2])) for row in rows] == [(0, 1), (0, 1)]


def test_recovery_reaches_valid_messages_after_more_than_one_historical_batch(
    tmp_path: Path,
) -> None:
    state_directory = tmp_path / "state"
    active = processor(state_directory)
    batch = active.handle(command())
    for message in batch:
        assert active.mark_delivered(str(message.message_id)) is True

    last_message = batch[0]
    with sqlite3.connect(active.ledger.database_path) as connection:
        for ordinal in range(3, 1002):
            active_deadline = ordinal == 1001
            message = batch[0].model_copy(
                update={
                    "message_id": UUID(
                        f"923e4567-e89b-42d3-a456-{ordinal:012d}"
                    ),
                    "idempotency_key": f"executor-recovery-history:{ordinal}",
                    "deadline_at": NOW
                    + timedelta(seconds=90 if active_deadline else 30),
                }
            )
            envelope = message.model_dump_json()
            connection.execute(
                """
                INSERT INTO executor_outbox (
                    ordinal, message_id, idempotency_key, intent_sha256,
                    envelope, source_message_id, delivered
                ) VALUES (?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    ordinal,
                    str(message.message_id),
                    str(message.idempotency_key),
                    hashlib.sha256(envelope.encode("utf-8")).digest(),
                    envelope,
                    "323e4567-e89b-42d3-a456-426614174001",
                ),
            )
            last_message = message

    recovered = processor(
        state_directory,
        clock=MutableClock(NOW + timedelta(seconds=31)),
    )

    restored = recovered.recover_outbox()
    assert len(restored) == 1
    assert str(restored[0].message_id) == str(last_message.message_id)
    assert restored[0].deadline_at == last_message.deadline_at
    assert recovered.pending_outbox() == restored


def test_invalid_expired_or_effectful_commands_fail_closed_without_an_outbox(
    tmp_path: Path,
) -> None:
    state_directory = tmp_path / "state"
    active = processor(state_directory)
    invalid_sources = (
        b"private-binary",
        "{}",
        command(deadline_at=NOW),
        command(installation_id="123e4567-e89b-42d3-a456-426614174099"),
        command(message_type="task.pause"),
    )
    for source in invalid_sources:
        with pytest.raises(
            ExecutorCommandRejected,
            match=r"^Local Executor command is rejected$",
        ) as captured:
            active.handle(source)
        assert captured.value.__cause__ is None
        assert captured.value.__context__ is None
        assert "private" not in str(captured.value).lower()
    assert active.pending_outbox() == ()


def test_expired_command_has_a_distinct_safe_failure_without_persistence(tmp_path: Path) -> None:
    active = processor(tmp_path / "expired-command")

    with pytest.raises(
        ExecutorCommandExpired,
        match=r"^Local Executor command deadline has expired$",
    ) as captured:
        active.handle(command(sent_at=NOW - timedelta(seconds=1), deadline_at=NOW))

    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert active.pending_outbox() == ()
    assert active.ledger.get_checkpoint(ATTEMPT_ID) is None


def test_generation_failure_leaves_only_the_received_checkpoint_and_retry_recovers(
    tmp_path: Path,
) -> None:
    state_directory = tmp_path / "state"
    failing = processor(state_directory, id_source=DeterministicIds(fail_at=2))

    with pytest.raises(ExecutorCommandRejected):
        failing.handle(command())

    checkpoint = failing.ledger.get_checkpoint(ATTEMPT_ID)
    assert checkpoint is not None
    assert checkpoint.state is AttemptCheckpointState.RECEIVED
    assert checkpoint.last_event_sequence == 0
    assert checkpoint.revision == 1
    assert failing.pending_outbox() == ()

    recovered = processor(state_directory, id_source=DeterministicIds())
    assert len(recovered.handle(command())) == 2


def test_constructor_clock_and_id_failures_do_not_reflect_private_details(tmp_path: Path) -> None:
    class InvalidClock:
        @staticmethod
        def now() -> datetime:
            return cast(datetime, object())

    with pytest.raises(ExecutorCommandRejected):
        processor(tmp_path / "invalid-clock", clock=InvalidClock()).handle(command())
    with pytest.raises(ExecutorCommandRejected):
        processor(tmp_path / "invalid-id", id_source=lambda: object()).handle(command())
    with pytest.raises(ExecutorCommandRejected):
        ExecutorCommandProcessor(
            ledger=cast(ExecutorLedger, object()),
            installation_id=INSTALLATION_ID,
            executor_id=EXECUTOR_ID,
        )

    valid_ledger = ExecutorLedger(
        state_directory=tmp_path / "constructor",
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
    )
    for values in (
        {"installation_id": cast(str, object())},
        {"installation_id": "not-a-uuid"},
        {"installation_id": str(UUID(int=4))},
        {"executor_id": "not-an-executor"},
        {"clock": object()},
        {"id_source": object()},
    ):
        arguments: dict[str, object] = {
            "ledger": valid_ledger,
            "installation_id": INSTALLATION_ID,
            "executor_id": EXECUTOR_ID,
        }
        arguments.update(values)
        with pytest.raises(ExecutorCommandRejected):
            ExecutorCommandProcessor(**arguments)  # type: ignore[arg-type]
    assert SystemExecutorCommandClock().now().utcoffset() == timedelta(0)


def test_checkpoint_race_and_ledger_failures_are_replayed_or_collapsed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_directory = tmp_path / "race-state"
    winner = processor(state_directory)
    batch = winner.handle(command())
    ledger = winner.ledger
    persisted = ledger.outbox_for_command("323e4567-e89b-42d3-a456-426614174001")
    checkpoint = AttemptCheckpoint(
        task_id=TASK_ID,
        attempt_id=ATTEMPT_ID,
        last_command_sequence=1,
        last_event_sequence=0,
        state=AttemptCheckpointState.RECEIVED,
        revision=1,
    )
    calls = count()

    monkeypatch.setattr(
        ledger,
        "outbox_for_command",
        lambda _message_id: () if next(calls) == 0 else persisted,
    )
    monkeypatch.setattr(ledger, "get_checkpoint", lambda _attempt_id: checkpoint)
    monkeypatch.setattr(
        ledger,
        "commit_outcome",
        lambda **_arguments: (_ for _ in ()).throw(ExecutorLedgerRejected()),
    )
    assert winner.handle(command()) == batch

    failed = processor(tmp_path / "failed-state")
    monkeypatch.setattr(failed.ledger, "outbox_for_command", lambda _message_id: ())
    monkeypatch.setattr(
        failed.ledger,
        "commit_outcome",
        lambda **_arguments: (_ for _ in ()).throw(ExecutorLedgerRejected()),
    )
    with pytest.raises(ExecutorCommandRejected):
        failed.handle(command())

    invalid_checkpoint = processor(tmp_path / "invalid-checkpoint")
    monkeypatch.setattr(invalid_checkpoint.ledger, "get_checkpoint", lambda _attempt_id: None)
    with pytest.raises(ExecutorCommandRejected):
        invalid_checkpoint.handle(command())


def test_outbox_operations_collapse_ledger_failures(tmp_path: Path) -> None:
    active = processor(tmp_path / "state")

    def reject(*_arguments: object, **_keywords: object) -> object:
        raise ExecutorLedgerRejected

    for method_name, operation in (
        ("outbox_for_delivery", active.pending_outbox),
        ("outbox_for_delivery", active.recover_outbox),
        ("mark_outbox_delivered", lambda: active.mark_delivered("not-a-message")),
    ):
        original = getattr(active.ledger, method_name)
        setattr(active.ledger, method_name, reject)
        try:
            with pytest.raises(ExecutorCommandRejected) as captured:
                operation()
            assert captured.value.__context__ is None
        finally:
            setattr(active.ledger, method_name, original)
