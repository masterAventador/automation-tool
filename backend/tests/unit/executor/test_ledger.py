from __future__ import annotations

import os
import sqlite3
import stat
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import cast
from uuid import UUID

import pytest

from automation_tool.executor import ledger as ledger_module
from automation_tool.executor.ledger import (
    EXECUTOR_LEDGER_FILE_NAME,
    AttemptCheckpoint,
    AttemptCheckpointState,
    ExecutorLedger,
    ExecutorLedgerRejected,
    LocalPlatformSession,
    PlatformSessionState,
)
from automation_tool.protocol import (
    ExecutorLifecycleEnvelope,
    TaskCommandEnvelope,
    TaskCommandResultEnvelope,
    TaskEventEnvelope,
)

NOW = datetime(2026, 7, 19, 10, 0, tzinfo=UTC)
INSTALLATION_ID = "123e4567-e89b-42d3-a456-426614174003"
EXECUTOR_ID = "123e4567-e89b-42d3-a456-426614174004"
TASK_ID = "123e4567-e89b-42d3-a456-426614174005"
ATTEMPT_ID = "123e4567-e89b-42d3-a456-426614174006"


def _uuid(value: int) -> str:
    return str(UUID(f"323e4567-e89b-42d3-a456-{value:012d}"))


def command(
    sequence: int,
    *,
    message_id: str | None = None,
    idempotency_key: str | None = None,
    message_type: str = "task.offer",
    task_id: str = TASK_ID,
    attempt_id: str = ATTEMPT_ID,
    payload: dict[str, object] | None = None,
) -> TaskCommandEnvelope:
    return TaskCommandEnvelope.model_validate(
        {
            "protocol_version": "1.0",
            "message_id": message_id or _uuid(sequence),
            "message_type": message_type,
            "sent_at": NOW,
            "deadline_at": NOW + timedelta(minutes=5),
            "installation_id": INSTALLATION_ID,
            "executor_id": EXECUTOR_ID,
            "correlation_id": _uuid(900),
            "idempotency_key": idempotency_key or f"executor-ledger:test:{sequence}",
            "sequence": sequence,
            "payload": (
                {"task_event_sequence_baseline": 0}
                if payload is None and message_type == "task.offer"
                else {} if payload is None else payload
            ),
            "task_id": task_id,
            "execution_attempt_id": attempt_id,
        }
    )


def outbound(
    *,
    message_id: str,
    idempotency_key: str,
    message_type: str = "task.accept",
    sequence: int = 1,
    payload: dict[str, object] | None = None,
    task_id: str = TASK_ID,
    attempt_id: str = ATTEMPT_ID,
) -> TaskCommandResultEnvelope | TaskEventEnvelope:
    model = TaskEventEnvelope if message_type == "task.started" else TaskCommandResultEnvelope
    return model.model_validate(
        {
            "protocol_version": "1.0",
            "message_id": message_id,
            "message_type": message_type,
            "sent_at": NOW,
            "deadline_at": NOW + timedelta(minutes=5),
            "installation_id": INSTALLATION_ID,
            "executor_id": EXECUTOR_ID,
            "correlation_id": _uuid(900),
            "idempotency_key": idempotency_key,
            "sequence": sequence,
            "payload": payload or {},
            "task_id": task_id,
            "execution_attempt_id": attempt_id,
        }
    )


def ledger(state_directory: Path) -> ExecutorLedger:
    return ExecutorLedger(
        state_directory=state_directory,
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
    )


def test_empty_private_directory_migrates_to_the_exact_v7_schema(tmp_path: Path) -> None:
    state_directory = tmp_path / "executor-state"

    opened = ledger(state_directory)

    assert opened.database_path == state_directory / EXECUTOR_LEDGER_FILE_NAME
    with sqlite3.connect(opened.database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (7,)
        assert connection.execute(
            "SELECT network_connected FROM executor_action_guard WHERE singleton_id = 1"
        ).fetchone() == (1,)
        assert connection.execute(
            """
            SELECT minimum_interval_seconds, task_action_limit
            FROM executor_action_policy WHERE singleton_id = 1
            """
        ).fetchone() == (None, None)
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            )
        }
    assert tables == {
        "executor_action_admissions",
        "executor_action_guard",
        "executor_action_policy",
        "executor_attempt_checkpoints",
        "executor_commands",
        "executor_identity",
        "executor_outbox",
        "executor_platform_sessions",
        "executor_side_effects",
    }
    if os.name != "nt":
        assert stat.S_IMODE(state_directory.stat().st_mode) == 0o700
        assert stat.S_IMODE(opened.database_path.stat().st_mode) == 0o600

    reopened = ledger(state_directory)
    assert reopened.database_path == opened.database_path
    with pytest.raises(ExecutorLedgerRejected):
        ExecutorLedger(
            state_directory=state_directory,
            installation_id="123e4567-e89b-42d3-a456-426614174099",
            executor_id=EXECUTOR_ID,
        )


def test_platform_session_revision_is_durable_monotonic_and_recovery_is_explicit(
    tmp_path: Path,
) -> None:
    state_directory = tmp_path / "session-state"
    opened = ledger(state_directory)

    missing = opened.record_platform_session(
        platform="douyin",
        state=PlatformSessionState.MISSING,
        observed_at=NOW,
    )
    expired = opened.record_platform_session(
        platform="douyin",
        state=PlatformSessionState.EXPIRED,
        observed_at=NOW + timedelta(seconds=1),
    )
    with pytest.raises(ExecutorLedgerRejected):
        opened.record_platform_session(
            platform="douyin",
            state=PlatformSessionState.HEALTHY,
            observed_at=NOW + timedelta(seconds=2),
        )
    recovered = opened.record_platform_session(
        platform="douyin",
        state=PlatformSessionState.HEALTHY,
        observed_at=NOW + timedelta(seconds=3),
        advance_epoch=True,
    )

    assert missing.session_revision == 1
    assert missing.circuit_open is True
    assert expired.session_revision == 1
    assert recovered.session_revision == 2
    assert recovered.circuit_open is False
    assert ledger(state_directory).get_platform_session("douyin") == recovered
    with pytest.raises(ExecutorLedgerRejected):
        opened.record_platform_session(
            platform="douyin",
            state=PlatformSessionState.RISK,
            observed_at=NOW - timedelta(seconds=1),
        )


def test_platform_session_values_and_transitions_fail_closed_at_every_boundary(
    tmp_path: Path,
) -> None:
    valid = {
        "platform": "douyin",
        "state": PlatformSessionState.MISSING,
        "session_revision": 1,
        "observed_at": NOW,
    }
    invalid_values = (
        {"platform": "private"},
        {"state": "missing"},
        {"session_revision": True},
        {"session_revision": 0},
        {"observed_at": "private"},
        {"observed_at": datetime(2026, 7, 19, 10, 0)},
    )
    for overrides in invalid_values:
        with pytest.raises(ExecutorLedgerRejected):
            LocalPlatformSession(**(valid | overrides))  # type: ignore[arg-type]

    opened = ledger(tmp_path / "session-boundaries")
    with pytest.raises(ExecutorLedgerRejected):
        opened.get_platform_session("private")
    with pytest.raises(ExecutorLedgerRejected):
        opened.record_platform_session(
            platform="douyin",
            state="missing",  # type: ignore[arg-type]
            observed_at=NOW,
        )
    first = opened.record_platform_session(
        platform="douyin",
        state=PlatformSessionState.MISSING,
        observed_at=NOW,
        advance_epoch=True,
    )
    assert first.session_revision == 1
    assert (
        opened.record_platform_session(
            platform="douyin",
            state=PlatformSessionState.MISSING,
            observed_at=NOW,
        )
        == first
    )
    for state, advance_epoch in (
        (PlatformSessionState.EXPIRED, False),
        (PlatformSessionState.MISSING, True),
    ):
        with pytest.raises(ExecutorLedgerRejected):
            opened.record_platform_session(
                platform="douyin",
                state=state,
                observed_at=NOW,
                advance_epoch=advance_epoch,
            )


def test_v1_ledger_is_migrated_in_place_without_losing_commands(tmp_path: Path) -> None:
    state_directory = tmp_path / "legacy-state"
    opened = ledger(state_directory)
    source = command(1)
    opened.receive_command(source)
    with sqlite3.connect(opened.database_path) as connection:
        connection.execute("DROP TABLE executor_side_effects")
        connection.execute("DROP TABLE executor_action_admissions")
        connection.execute("DROP TABLE executor_action_guard")
        connection.execute("DROP TABLE executor_action_policy")
        connection.execute("DROP TABLE executor_platform_sessions")
        connection.execute("PRAGMA user_version = 1")

    migrated = ledger(state_directory)

    assert migrated.receive_command(source).replayed is True
    with sqlite3.connect(migrated.database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (7,)
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name = 'executor_platform_sessions'"
        ).fetchone() == ("executor_platform_sessions",)


def test_v3_ledger_adds_an_initial_local_action_guard_without_losing_state(
    tmp_path: Path,
) -> None:
    state_directory = tmp_path / "legacy-v3-state"
    opened = ledger(state_directory)
    source = command(1)
    opened.receive_command(source)
    session = opened.record_platform_session(
        platform="douyin",
        state=PlatformSessionState.HEALTHY,
        observed_at=NOW,
    )
    with sqlite3.connect(opened.database_path) as connection:
        connection.execute("DROP TABLE executor_side_effects")
        connection.execute("DROP TABLE executor_action_admissions")
        connection.execute("DROP TABLE executor_action_guard")
        connection.execute("DROP TABLE executor_action_policy")
        connection.execute("PRAGMA user_version = 3")

    migrated = ledger(state_directory)

    assert migrated.receive_command(source).replayed is True
    assert migrated.get_platform_session("douyin") == session
    assert migrated.get_action_emergency_stop().engaged is False
    assert migrated.get_action_emergency_stop().revision == 0
    with sqlite3.connect(migrated.database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (7,)
        assert connection.execute("SELECT COUNT(*) FROM executor_action_admissions").fetchone() == (
            0,
        )
        assert connection.execute(
            """
            SELECT minimum_interval_seconds, task_action_limit
            FROM executor_action_policy WHERE singleton_id = 1
            """
        ).fetchone() == (None, None)


def test_commands_are_durable_idempotent_and_attempt_sequences_are_contiguous(
    tmp_path: Path,
) -> None:
    opened = ledger(tmp_path / "state")
    first = command(1)

    received = opened.receive_command(first)
    replayed = opened.receive_command(first)
    reopened_replay = ledger(tmp_path / "state").receive_command(first)

    assert received.replayed is False
    assert replayed.replayed is True
    assert reopened_replay.replayed is True
    assert replayed.message_id == str(first.message_id)
    checkpoint = opened.get_checkpoint(ATTEMPT_ID)
    assert checkpoint is not None
    assert checkpoint.task_id == TASK_ID
    assert checkpoint.last_command_sequence == 1
    assert checkpoint.last_event_sequence == 0
    assert checkpoint.revision == 1
    assert checkpoint.state is AttemptCheckpointState.RECEIVED
    assert opened.get_checkpoint(_uuid(777)) is None
    with pytest.raises(ExecutorLedgerRejected):
        opened.get_checkpoint("not-an-attempt")

    same_intent_new_message = first.model_copy(update={"message_id": _uuid(88)})
    assert opened.receive_command(same_intent_new_message).replayed is True
    with pytest.raises(ExecutorLedgerRejected):
        opened.receive_command(
            command(
                1,
                message_id=str(first.message_id),
                payload={"task_event_sequence_baseline": 1},
            )
        )
    with pytest.raises(ExecutorLedgerRejected):
        opened.receive_command(
            command(
                1,
                message_id=_uuid(89),
                payload={"task_event_sequence_baseline": 1},
            )
        )
    with pytest.raises(ExecutorLedgerRejected):
        opened.receive_command(command(3))
    with pytest.raises(ExecutorLedgerRejected):
        opened.receive_command(command(2, attempt_id=_uuid(700)))
    with pytest.raises(ExecutorLedgerRejected):
        opened.receive_command(command(2, task_id=_uuid(701)))

    second_identity = command(2, message_type="task.pause")
    collision = second_identity.model_copy(
        update={
            "message_id": first.message_id,
            "idempotency_key": second_identity.idempotency_key,
        }
    )
    opened.compare_and_set_checkpoint(
        attempt_id=ATTEMPT_ID,
        expected_revision=1,
        state=AttemptCheckpointState.RUNNING,
        last_event_sequence=0,
    )
    opened.receive_command(second_identity)
    with pytest.raises(ExecutorLedgerRejected):
        opened.receive_command(collision)

    wrong_installation = command(3, message_type="task.resume").model_copy(
        update={"installation_id": _uuid(702)}
    )
    with pytest.raises(ExecutorLedgerRejected):
        opened.receive_command(wrong_installation)
    with pytest.raises(ExecutorLedgerRejected):
        opened.receive_command(cast(TaskCommandEnvelope, object()))

    assert second_identity.sequence == 2
    assert opened.get_checkpoint(ATTEMPT_ID).last_command_sequence == 2  # type: ignore[union-attr]


def test_checkpoint_compare_and_set_has_one_winner_and_never_regresses_events(
    tmp_path: Path,
) -> None:
    state_directory = tmp_path / "state"
    first = ledger(state_directory)
    second = ledger(state_directory)
    first.receive_command(command(1))

    barrier = threading.Barrier(2)

    def update(opened: ExecutorLedger, state: AttemptCheckpointState) -> object:
        barrier.wait()
        try:
            return opened.compare_and_set_checkpoint(
                attempt_id=ATTEMPT_ID,
                expected_revision=1,
                state=state,
                last_event_sequence=2,
            )
        except ExecutorLedgerRejected as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(
            pool.map(
                lambda item: update(*item),
                (
                    (first, AttemptCheckpointState.RUNNING),
                    (second, AttemptCheckpointState.PAUSED),
                ),
            )
        )
    winners = [outcome for outcome in outcomes if not isinstance(outcome, Exception)]
    rejected = [outcome for outcome in outcomes if isinstance(outcome, ExecutorLedgerRejected)]
    assert len(winners) == 1
    assert len(rejected) == 1
    running = first.get_checkpoint(ATTEMPT_ID)
    assert running is not None
    assert running.revision == 2
    assert running.state in {AttemptCheckpointState.RUNNING, AttemptCheckpointState.PAUSED}
    assert running.last_event_sequence == 2
    with pytest.raises(ExecutorLedgerRejected):
        first.compare_and_set_checkpoint(
            attempt_id=ATTEMPT_ID,
            expected_revision=2,
            state=AttemptCheckpointState.RUNNING,
            last_event_sequence=1,
        )

    invalid_updates = (
        {
            "attempt_id": "not-an-attempt",
            "expected_revision": 2,
            "state": running.state,
            "last_event_sequence": 2,
        },
        {
            "attempt_id": ATTEMPT_ID,
            "expected_revision": True,
            "state": running.state,
            "last_event_sequence": 2,
        },
        {
            "attempt_id": ATTEMPT_ID,
            "expected_revision": 0,
            "state": running.state,
            "last_event_sequence": 2,
        },
        {
            "attempt_id": ATTEMPT_ID,
            "expected_revision": 2,
            "state": "running",
            "last_event_sequence": 2,
        },
        {
            "attempt_id": ATTEMPT_ID,
            "expected_revision": 2,
            "state": running.state,
            "last_event_sequence": True,
        },
        {
            "attempt_id": ATTEMPT_ID,
            "expected_revision": 2,
            "state": running.state,
            "last_event_sequence": -1,
        },
        {
            "attempt_id": ATTEMPT_ID,
            "expected_revision": 2,
            "state": running.state,
            "last_event_sequence": 2**53,
        },
    )
    for invalid in invalid_updates:
        with pytest.raises(ExecutorLedgerRejected):
            first.compare_and_set_checkpoint(**invalid)  # type: ignore[arg-type]


def test_outbox_replays_exact_protocol_messages_and_delivery_is_durable(tmp_path: Path) -> None:
    state_directory = tmp_path / "state"
    opened = ledger(state_directory)
    source = command(1)
    opened.receive_command(source)
    accepted = outbound(
        message_id=_uuid(100),
        idempotency_key="executor-ledger:accept:1",
    )
    started = outbound(
        message_id=_uuid(101),
        idempotency_key="executor-ledger:event:1",
        message_type="task.started",
        sequence=1,
    )

    first = opened.enqueue_outbox(source_message_id=str(source.message_id), message=accepted)
    replay = opened.enqueue_outbox(source_message_id=str(source.message_id), message=accepted)
    opened.enqueue_outbox(source_message_id=str(source.message_id), message=started)

    assert first.replayed is False
    assert replay.replayed is True
    assert [item.message for item in opened.pending_outbox(limit=10)] == [accepted, started]
    assert opened.mark_outbox_delivered(str(accepted.message_id)) is True
    assert opened.mark_outbox_delivered(str(accepted.message_id)) is False
    pending_after_reopen = ledger(state_directory).pending_outbox(limit=10)
    assert [item.message for item in pending_after_reopen] == [started]

    conflicting = accepted.model_copy(update={"payload": {"reason_code": "changed"}})
    with pytest.raises(ExecutorLedgerRejected):
        opened.enqueue_outbox(source_message_id=str(source.message_id), message=conflicting)
    with pytest.raises(ExecutorLedgerRejected):
        opened.enqueue_outbox(
            source_message_id=_uuid(999),
            message=outbound(
                message_id=_uuid(102),
                idempotency_key="executor-ledger:event:unknown-source",
                message_type="task.started",
            ),
        )

    collision = accepted.model_copy(update={"idempotency_key": started.idempotency_key})
    with pytest.raises(ExecutorLedgerRejected):
        opened.enqueue_outbox(source_message_id=str(source.message_id), message=collision)
    wrong_installation = outbound(
        message_id=_uuid(103),
        idempotency_key="executor-ledger:wrong-installation",
    ).model_copy(update={"installation_id": _uuid(702)})
    with pytest.raises(ExecutorLedgerRejected):
        opened.enqueue_outbox(
            source_message_id=str(source.message_id),
            message=wrong_installation,
        )
    with pytest.raises(ExecutorLedgerRejected):
        opened.enqueue_outbox(
            source_message_id=str(source.message_id),
            message=cast(TaskCommandResultEnvelope, object()),
        )
    for invalid_limit in (0, 1001, True):
        with pytest.raises(ExecutorLedgerRejected):
            opened.pending_outbox(limit=invalid_limit)
    with pytest.raises(ExecutorLedgerRejected):
        opened.mark_outbox_delivered(_uuid(998))
    with pytest.raises(ExecutorLedgerRejected):
        opened.mark_outbox_delivered("not-a-message")

    lifecycle = ExecutorLifecycleEnvelope.model_validate(
        {
            "protocol_version": "1.0",
            "message_id": _uuid(104),
            "message_type": "executor.heartbeat",
            "sent_at": NOW,
            "deadline_at": NOW + timedelta(minutes=5),
            "installation_id": INSTALLATION_ID,
            "executor_id": EXECUTOR_ID,
            "correlation_id": _uuid(900),
            "idempotency_key": "executor-ledger:corrupt-lifecycle",
            "sequence": 1,
            "payload": {},
        }
    )
    with sqlite3.connect(opened.database_path) as connection:
        connection.execute(
            "UPDATE executor_outbox SET envelope = ? WHERE message_id = ?",
            (lifecycle.model_dump_json(), str(started.message_id)),
        )
    with pytest.raises(ExecutorLedgerRejected):
        opened.pending_outbox(limit=10)


def test_pending_event_spool_is_bounded_and_overflow_rolls_back_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened = ledger(tmp_path / "bounded-spool")
    first_source = command(1)
    opened.receive_command(first_source)
    first = outbound(
        message_id=_uuid(110),
        idempotency_key="executor-ledger:spool:first",
    )
    opened.enqueue_outbox(source_message_id=str(first_source.message_id), message=first)
    monkeypatch.setattr(ledger_module, "_MAX_PENDING_OUTBOX_ENTRIES", 1)
    monkeypatch.setattr(ledger_module, "_MAX_PENDING_OUTBOX_BYTES", 1024 * 1024)

    second_task = _uuid(710)
    second_attempt = _uuid(711)
    second_source = command(
        1,
        message_id=_uuid(112),
        idempotency_key="executor-ledger:spool:second-source",
        task_id=second_task,
        attempt_id=second_attempt,
    )
    opened.receive_command(second_source)
    second = outbound(
        message_id=_uuid(113),
        idempotency_key="executor-ledger:spool:second",
        task_id=second_task,
        attempt_id=second_attempt,
    )
    with pytest.raises(ExecutorLedgerRejected):
        opened.commit_outcome(
            source_message_id=str(second_source.message_id),
            expected_checkpoint_revision=1,
            checkpoint_state=AttemptCheckpointState.TERMINAL,
            last_event_sequence=0,
            messages=(second,),
        )
    assert opened.get_checkpoint(second_attempt) == AttemptCheckpoint(
        attempt_id=second_attempt,
        task_id=second_task,
        last_command_sequence=1,
        last_event_sequence=0,
        state=AttemptCheckpointState.RECEIVED,
        revision=1,
    )
    assert opened.outbox_for_command(str(second_source.message_id)) == ()

    assert opened.mark_outbox_delivered(str(first.message_id)) is True
    committed = opened.commit_outcome(
        source_message_id=str(second_source.message_id),
        expected_checkpoint_revision=1,
        checkpoint_state=AttemptCheckpointState.TERMINAL,
        last_event_sequence=0,
        messages=(second,),
    )
    assert [entry.message for entry in committed] == [second]

    with sqlite3.connect(opened.database_path) as connection:
        pending_bytes = int(
            connection.execute(
                "SELECT length(CAST(envelope AS BLOB)) FROM executor_outbox WHERE delivered = 0"
            ).fetchone()[0]
        )
    monkeypatch.setattr(ledger_module, "_MAX_PENDING_OUTBOX_ENTRIES", 2)
    monkeypatch.setattr(ledger_module, "_MAX_PENDING_OUTBOX_BYTES", pending_bytes)
    third = outbound(
        message_id=_uuid(114),
        idempotency_key="executor-ledger:spool:third",
        task_id=second_task,
        attempt_id=second_attempt,
    )
    with pytest.raises(ExecutorLedgerRejected):
        opened.enqueue_outbox(source_message_id=str(second_source.message_id), message=third)


def test_atomic_outcome_commit_and_recovery_reject_every_inconsistent_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_directory = tmp_path / "atomic-state"
    opened = ledger(state_directory)
    source = command(1)
    opened.receive_command(source)
    accepted = outbound(
        message_id=_uuid(200),
        idempotency_key="executor-ledger:atomic:accept",
    )
    started = outbound(
        message_id=_uuid(201),
        idempotency_key="executor-ledger:atomic:started",
        message_type="task.started",
    )
    batch = (accepted, started)

    committed = opened.commit_outcome(
        source_message_id=str(source.message_id),
        expected_checkpoint_revision=1,
        checkpoint_state=AttemptCheckpointState.TERMINAL,
        last_event_sequence=1,
        messages=batch,
    )

    assert [entry.message for entry in committed] == list(batch)
    assert all(entry.replayed is False for entry in committed)
    assert [entry.message for entry in opened.outbox_for_command(str(source.message_id))] == list(
        batch
    )
    assert opened.outbox_for_command(_uuid(999)) == ()
    assert opened.requeue_delivered_outbox() == 0
    assert opened.mark_outbox_delivered(str(accepted.message_id)) is True
    assert opened.requeue_delivered_outbox() == 1
    assert [entry.message for entry in opened.pending_outbox(limit=10)] == list(batch)

    with pytest.raises(ExecutorLedgerRejected):
        opened.commit_outcome(
            source_message_id=str(source.message_id),
            expected_checkpoint_revision=2,
            checkpoint_state=AttemptCheckpointState.TERMINAL,
            last_event_sequence=1,
            messages=batch,
        )
    invalid_arguments: tuple[dict[str, object], ...] = (
        {"expected_checkpoint_revision": 0},
        {"checkpoint_state": "terminal"},
        {"last_event_sequence": -1},
        {"messages": []},
        {"messages": ()},
    )
    for invalid in invalid_arguments:
        arguments: dict[str, object] = {
            "source_message_id": str(source.message_id),
            "expected_checkpoint_revision": 2,
            "checkpoint_state": AttemptCheckpointState.TERMINAL,
            "last_event_sequence": 1,
            "messages": batch,
        }
        arguments.update(invalid)
        with pytest.raises(ExecutorLedgerRejected):
            opened.commit_outcome(**arguments)  # type: ignore[arg-type]

    duplicate_state = tmp_path / "duplicate-state"
    duplicate = ledger(duplicate_state)
    duplicate.receive_command(source)
    with pytest.raises(ExecutorLedgerRejected):
        duplicate.commit_outcome(
            source_message_id=str(source.message_id),
            expected_checkpoint_revision=1,
            checkpoint_state=AttemptCheckpointState.TERMINAL,
            last_event_sequence=1,
            messages=(accepted, accepted),
        )
    with pytest.raises(ExecutorLedgerRejected):
        duplicate.commit_outcome(
            source_message_id=_uuid(999),
            expected_checkpoint_revision=1,
            checkpoint_state=AttemptCheckpointState.TERMINAL,
            last_event_sequence=1,
            messages=batch,
        )
    mismatched = accepted.model_copy(update={"task_id": UUID(_uuid(998))})
    with pytest.raises(ExecutorLedgerRejected):
        duplicate.commit_outcome(
            source_message_id=str(source.message_id),
            expected_checkpoint_revision=1,
            checkpoint_state=AttemptCheckpointState.TERMINAL,
            last_event_sequence=1,
            messages=(mismatched,),
        )
    with pytest.raises(ExecutorLedgerRejected):
        duplicate.commit_outcome(
            source_message_id=str(source.message_id),
            expected_checkpoint_revision=2,
            checkpoint_state=AttemptCheckpointState.TERMINAL,
            last_event_sequence=1,
            messages=batch,
        )
    with sqlite3.connect(duplicate.database_path) as connection:
        connection.execute("UPDATE executor_attempt_checkpoints SET last_event_sequence = 2")
    with pytest.raises(ExecutorLedgerRejected):
        duplicate.commit_outcome(
            source_message_id=str(source.message_id),
            expected_checkpoint_revision=1,
            checkpoint_state=AttemptCheckpointState.TERMINAL,
            last_event_sequence=1,
            messages=batch,
        )

    with pytest.raises(ExecutorLedgerRejected):
        opened.outbox_for_command("not-a-command")
    with monkeypatch.context() as scoped:
        scoped.setattr(opened, "_connect", lambda: (_ for _ in ()).throw(OSError()))
        with pytest.raises(ExecutorLedgerRejected):
            opened.requeue_delivered_outbox()


def test_newer_or_corrupt_schema_and_symlink_paths_fail_closed(tmp_path: Path) -> None:
    state_directory = tmp_path / "state"
    opened = ledger(state_directory)
    with sqlite3.connect(opened.database_path) as connection:
        connection.execute("PRAGMA user_version = 8")
    with pytest.raises(ExecutorLedgerRejected):
        ledger(state_directory)

    missing_identity = tmp_path / "missing-identity"
    missing = ledger(missing_identity)
    with sqlite3.connect(missing.database_path) as connection:
        connection.execute("DELETE FROM executor_identity")
    rebound = ledger(missing_identity)
    with sqlite3.connect(rebound.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM executor_identity").fetchone() == (1,)

    corrupt_state = tmp_path / "corrupt-state"
    corrupt = ledger(corrupt_state)
    with sqlite3.connect(corrupt.database_path) as connection:
        connection.execute("DROP TABLE executor_identity")
    with pytest.raises(ExecutorLedgerRejected):
        ledger(corrupt_state)

    real_directory = tmp_path / "real"
    real_directory.mkdir(mode=0o700)
    linked_directory = tmp_path / "linked"
    if os.name == "nt":
        junction = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", linked_directory, real_directory],
            capture_output=True,
            check=False,
        )
        assert junction.returncode == 0, junction.stderr
    elif hasattr(os, "symlink"):
        linked_directory.symlink_to(real_directory, target_is_directory=True)
    else:
        return
    with pytest.raises(ExecutorLedgerRejected):
        ledger(linked_directory)


@pytest.mark.skipif(os.name != "nt", reason="Windows DACL boundary")
def test_windows_broad_directory_and_database_acls_fail_closed(tmp_path: Path) -> None:
    broad_directory = tmp_path / "broad-directory"
    broad_directory.mkdir()
    directory_acl = subprocess.run(
        ["icacls.exe", broad_directory, "/grant", "*S-1-5-11:(M)"],
        capture_output=True,
        check=False,
    )
    assert directory_acl.returncode == 0, directory_acl.stderr
    with pytest.raises(ExecutorLedgerRejected):
        ledger(broad_directory)

    file_state = tmp_path / "broad-database"
    opened = ledger(file_state)
    file_acl = subprocess.run(
        ["icacls.exe", opened.database_path, "/grant", "*S-1-5-11:(R)"],
        capture_output=True,
        check=False,
    )
    assert file_acl.returncode == 0, file_acl.stderr
    with pytest.raises(ExecutorLedgerRejected):
        opened.get_checkpoint(ATTEMPT_ID)


def test_windows_acl_adapter_invokes_the_native_validator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Path] = []
    adapter = ModuleType("automation_tool.executor.windows_acl")
    adapter.validate_private_acl = lambda path: calls.append(path)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "automation_tool.executor.windows_acl", adapter)
    monkeypatch.setattr(os, "name", "nt")

    ledger_module._validate_windows_private_acl(tmp_path)

    assert calls == [tmp_path]


def test_invalid_identity_permissions_file_shapes_and_open_races_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ExecutorLedgerRejected):
        ExecutorLedger(
            state_directory=cast(Path, object()),
            installation_id=INSTALLATION_ID,
            executor_id=EXECUTOR_ID,
        )
    for installation_id in (cast(str, object()), "not-a-uuid", str(UUID(int=4))):
        with pytest.raises(ExecutorLedgerRejected):
            ExecutorLedger(
                state_directory=tmp_path / f"invalid-{id(installation_id)}",
                installation_id=installation_id,
                executor_id=EXECUTOR_ID,
            )

    if os.name != "nt":
        public_directory = tmp_path / "public-state"
        public_directory.mkdir(mode=0o700)
        public_directory.chmod(0o755)
        with pytest.raises(ExecutorLedgerRejected):
            ledger(public_directory)

        insecure_file_state = tmp_path / "insecure-file-state"
        insecure = ledger(insecure_file_state)
        insecure.database_path.chmod(0o644)
        with pytest.raises(ExecutorLedgerRejected):
            insecure.get_checkpoint(ATTEMPT_ID)

    non_file_state = tmp_path / "non-file-state"
    non_file = ledger(non_file_state)
    displaced = non_file.database_path.with_suffix(".saved")
    non_file.database_path.replace(displaced)
    non_file.database_path.mkdir(mode=0o700)
    with pytest.raises(ExecutorLedgerRejected):
        non_file.get_checkpoint(ATTEMPT_ID)

    raced = ledger(tmp_path / "raced-state")
    identities = iter(((1, 1), (2, 2)))
    monkeypatch.setattr(raced, "_secure_database_identity", lambda: next(identities))
    with pytest.raises(ExecutorLedgerRejected):
        raced.get_checkpoint(ATTEMPT_ID)

    replaced_directory = tmp_path / "replaced-directory"
    replaced_directory.mkdir(mode=0o700)
    replacement_file = tmp_path / "replacement-file"
    replacement_file.touch(mode=0o600)
    original_stat = Path.stat
    original_mkdir = Path.mkdir

    def replaced_stat(path: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        if path == replaced_directory:
            return original_stat(replacement_file, follow_symlinks=follow_symlinks)
        return original_stat(path, follow_symlinks=follow_symlinks)

    directory_race = ledger(tmp_path / "directory-race-control")
    directory_race._state_directory = replaced_directory

    def existing_mkdir(
        path: Path,
        mode: int = 0o777,
        parents: bool = False,
        exist_ok: bool = False,
    ) -> None:
        if path != replaced_directory:
            original_mkdir(path, mode=mode, parents=parents, exist_ok=exist_ok)

    monkeypatch.setattr(Path, "mkdir", existing_mkdir)
    monkeypatch.setattr(Path, "stat", replaced_stat)
    with pytest.raises(ValueError):
        directory_race._prepare_private_directory()
