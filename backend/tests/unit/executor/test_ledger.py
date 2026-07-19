from __future__ import annotations

import os
import sqlite3
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from automation_tool.executor.ledger import (
    EXECUTOR_LEDGER_FILE_NAME,
    AttemptCheckpointState,
    ExecutorLedger,
    ExecutorLedgerRejected,
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
            "payload": payload or {},
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
            "task_id": TASK_ID,
            "execution_attempt_id": ATTEMPT_ID,
        }
    )


def ledger(state_directory: Path) -> ExecutorLedger:
    return ExecutorLedger(
        state_directory=state_directory,
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
    )


def test_empty_private_directory_migrates_to_the_exact_v1_schema(tmp_path: Path) -> None:
    state_directory = tmp_path / "executor-state"

    opened = ledger(state_directory)

    assert opened.database_path == state_directory / EXECUTOR_LEDGER_FILE_NAME
    with sqlite3.connect(opened.database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (1,)
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            )
        }
    assert tables == {
        "executor_attempt_checkpoints",
        "executor_commands",
        "executor_identity",
        "executor_outbox",
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
            command(1, message_id=str(first.message_id), payload={"changed": True})
        )
    with pytest.raises(ExecutorLedgerRejected):
        opened.receive_command(command(1, message_id=_uuid(89), payload={"changed": True}))
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
        connection.execute("PRAGMA user_version = 2")
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

    if not hasattr(os, "symlink"):
        return
    real_directory = tmp_path / "real"
    real_directory.mkdir(mode=0o700)
    linked_directory = tmp_path / "linked"
    linked_directory.symlink_to(real_directory, target_is_directory=True)
    with pytest.raises(ExecutorLedgerRejected):
        ledger(linked_directory)


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
