from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import count
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest

from automation_tool.executor.browser_runtime import BrowserWindow
from automation_tool.executor.command_processor import ExecutorCommandProcessor
from automation_tool.executor.crash_recovery import (
    ExecutorCrashRecoveryCoordinator,
    ExecutorCrashRecoveryRejected,
)
from automation_tool.executor.ledger import (
    AttemptCheckpointState,
    ExecutorLedger,
    ExecutorLedgerRejected,
)
from automation_tool.executor.rpa.douyin.side_effect_recovery import (
    DouyinSideEffectRecovery,
    DouyinSideEffectRecoveryRejected,
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

NOW = datetime(2026, 7, 21, 16, 0, tzinfo=UTC)
INSTALLATION_ID = ProtocolInstallationId("123e4567-e89b-42d3-a456-426614174003")
EXECUTOR_ID = ProtocolExecutorId("123e4567-e89b-42d3-a456-426614174004")
TASK_ID = ProtocolTaskId("123e4567-e89b-42d3-a456-426614174005")
ATTEMPT_ID = ProtocolExecutionAttemptId("123e4567-e89b-42d3-a456-426614174006")


def resource_id(index: int) -> str:
    return str(UUID(f"323e4567-e89b-42d3-a456-{index:012d}"))


@dataclass
class FixedClock:
    value: datetime = NOW + timedelta(seconds=30)

    def now(self) -> datetime:
        return self.value


class DeterministicIds:
    def __init__(self) -> None:
        self._values = count(1)

    def __call__(self) -> UUID:
        return UUID(f"923e4567-e89b-42d3-a456-{next(self._values):012d}")


def offer() -> TaskCommandEnvelope:
    return TaskCommandEnvelope.model_validate(
        {
            "protocol_version": "1.0",
            "message_id": resource_id(1),
            "message_type": "task.offer",
            "sent_at": NOW,
            "deadline_at": NOW + timedelta(minutes=5),
            "installation_id": str(INSTALLATION_ID),
            "executor_id": str(EXECUTOR_ID),
            "correlation_id": resource_id(101),
            "idempotency_key": "executor:h805:offer:1",
            "sequence": 1,
            "payload": {"task_event_sequence_baseline": 0},
            "task_id": str(TASK_ID),
            "execution_attempt_id": str(ATTEMPT_ID),
        }
    )


def event(command: TaskCommandEnvelope, message_type: str, sequence: int) -> TaskEventEnvelope:
    return TaskEventEnvelope.model_validate(
        {
            "protocol_version": "1.0",
            "message_id": resource_id(10 + sequence),
            "message_type": message_type,
            "sent_at": NOW,
            "deadline_at": NOW + timedelta(minutes=5),
            "installation_id": str(INSTALLATION_ID),
            "executor_id": str(EXECUTOR_ID),
            "correlation_id": str(command.correlation_id),
            "idempotency_key": f"executor:h805:event:{sequence}",
            "sequence": sequence,
            "payload": {},
            "task_id": str(TASK_ID),
            "execution_attempt_id": str(ATTEMPT_ID),
        }
    )


def action_claims(
    index: int,
    *,
    action: DouyinSearchExposureAction = DouyinSearchExposureAction.COMMENT,
) -> ActionAuthorizationClaims:
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
        action=action,
        idempotency_key=action_authorization_idempotency_key(action_id),
        authorized_at=NOW,
        deadline_at=NOW + timedelta(minutes=5),
    )


def seed(state_directory: Path) -> tuple[ExecutorLedger, str, str, tuple[TaskEventEnvelope, ...]]:
    opened = ExecutorLedger(
        state_directory=state_directory,
        installation_id=str(INSTALLATION_ID),
        executor_id=str(EXECUTOR_ID),
    )
    source = offer()
    opened.receive_command(source)
    started = (event(source, "task.started", 1), event(source, "step.started", 2))
    for message in started:
        opened.enqueue_outbox(source_message_id=str(source.message_id), message=message)
        opened.mark_outbox_delivered(str(message.message_id))
    opened.compare_and_set_checkpoint(
        attempt_id=str(ATTEMPT_ID),
        expected_revision=1,
        state=AttemptCheckpointState.RUNNING,
        last_event_sequence=2,
    )
    action_ids: list[str] = []
    for offset, (index, action) in enumerate(
        (
            (201, DouyinSearchExposureAction.COMMENT),
            (202, DouyinSearchExposureAction.DIRECT_MESSAGE),
        )
    ):
        claims = action_claims(index, action=action)
        fingerprint = hashlib.sha256(f"h805-effect-{index}".encode()).digest()
        opened.admit_action(
            claims=claims,
            authorization_fingerprint=hashlib.sha256(
                f"h805-authorization-{index}".encode()
            ).digest(),
            admitted_at=NOW + timedelta(seconds=offset * 3),
            minimum_interval_seconds=1,
            task_action_limit=100,
        )
        opened.prepare_side_effect(
            action_id=str(claims.action_id),
            effect_fingerprint=fingerprint,
            prepared_at=NOW + timedelta(seconds=offset * 3 + 1),
        )
        action_ids.append(str(claims.action_id))
        if offset == 0:
            opened.begin_side_effect_dispatch(
                action_id=str(claims.action_id),
                effect_fingerprint=fingerprint,
                dispatched_at=NOW + timedelta(seconds=offset * 3 + 2),
            )
    return opened, action_ids[0], action_ids[1], started


def coordinator(opened: ExecutorLedger) -> ExecutorCrashRecoveryCoordinator:
    return ExecutorCrashRecoveryCoordinator(
        ledger=opened,
        clock=FixedClock(),
        id_source=cast(Callable[[], object], DeterministicIds()),
    )


def test_crash_recovery_settles_dispatched_without_page_or_redispatch_and_projects_once(
    tmp_path: Path,
) -> None:
    opened, dispatched_id, prepared_id, started = seed(tmp_path / "state")

    first = coordinator(opened).run()

    assert len(first) == 1
    recovered = first[0]
    assert recovered.message_type == "task.outcome_uncertain"
    assert recovered.sequence == 3
    assert recovered.idempotency_key == f"executor:recovery:{dispatched_id}"
    assert recovered.payload == {
        "action_id": dispatched_id,
        "evidence": "recovery_unconfirmed",
        "evidence_version": "action-result-evidence.v1",
    }
    assert opened.get_side_effect(dispatched_id).state is SideEffectState.UNCERTAIN  # type: ignore[union-attr]
    assert opened.get_side_effect(prepared_id).state is SideEffectState.PREPARED  # type: ignore[union-attr]
    checkpoint = opened.get_checkpoint(str(ATTEMPT_ID))
    assert checkpoint is not None
    assert checkpoint.state is AttemptCheckpointState.OUTCOME_UNCERTAIN
    assert checkpoint.last_event_sequence == 3

    second = coordinator(opened).run()
    assert second == first
    assert opened.get_checkpoint(str(ATTEMPT_ID)) == checkpoint

    processor = ExecutorCommandProcessor(
        ledger=opened,
        installation_id=str(INSTALLATION_ID),
        executor_id=str(EXECUTOR_ID),
        clock=FixedClock(),
        id_source=cast(Callable[[], object], DeterministicIds()),
    )
    assert processor.recover_outbox() == (*started, recovered)


def test_normal_startup_has_no_crash_recovery_side_effects(tmp_path: Path) -> None:
    opened, dispatched_id, prepared_id, started = seed(tmp_path / "normal")
    processor = ExecutorCommandProcessor(
        ledger=opened,
        installation_id=str(INSTALLATION_ID),
        executor_id=str(EXECUTOR_ID),
        clock=FixedClock(),
        id_source=cast(Callable[[], object], DeterministicIds()),
    )

    assert processor.recover_outbox() == started
    assert opened.get_side_effect(dispatched_id).state is SideEffectState.DISPATCHED  # type: ignore[union-attr]
    assert opened.get_side_effect(prepared_id).state is SideEffectState.PREPARED  # type: ignore[union-attr]
    assert opened.get_checkpoint(str(ATTEMPT_ID)).state is AttemptCheckpointState.RUNNING  # type: ignore[union-attr]


def test_verified_comment_and_message_recover_in_sequence_without_dom_access(
    tmp_path: Path,
) -> None:
    opened, comment_id, message_id, _started = seed(tmp_path / "verified")
    comment = opened.get_side_effect(comment_id)
    message = opened.get_side_effect(message_id)
    assert comment is not None and message is not None
    opened.verify_side_effect(
        action_id=comment_id,
        effect_fingerprint=comment.effect_fingerprint,
        verification_fingerprint=b"c" * 32,
        verified_at=NOW + timedelta(seconds=3),
    )
    opened.begin_side_effect_dispatch(
        action_id=message_id,
        effect_fingerprint=message.effect_fingerprint,
        dispatched_at=NOW + timedelta(seconds=5),
    )
    opened.verify_side_effect(
        action_id=message_id,
        effect_fingerprint=message.effect_fingerprint,
        verification_fingerprint=b"m" * 32,
        verified_at=NOW + timedelta(seconds=6),
    )
    window = BrowserWindow._for_runtime(object(), cast(Any, object()))
    recovery = ExecutorCrashRecoveryCoordinator(
        ledger=opened,
        clock=FixedClock(),
        id_source=cast(Callable[[], object], DeterministicIds()),
        window=window,
    )

    events = recovery.run()

    assert [value.message_type for value in events] == ["step.completed", "step.completed"]
    assert [value.sequence for value in events] == [3, 4]
    assert [value.payload["evidence"] for value in events] == [
        "comment_confirmed",
        "message_confirmed",
    ]
    checkpoint = opened.get_checkpoint(str(ATTEMPT_ID))
    assert checkpoint is not None
    assert checkpoint.state is AttemptCheckpointState.RUNNING
    assert checkpoint.last_event_sequence == 4
    assert coordinator(opened).run() == events


def test_crash_recovery_rejects_bad_construction_reuse_and_collapsed_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    empty = ExecutorLedger(
        state_directory=tmp_path / "empty",
        installation_id=str(INSTALLATION_ID),
        executor_id=str(EXECUTOR_ID),
    )
    for values in (
        {"ledger": object()},
        {"clock": object()},
        {"id_source": object()},
        {"window": object()},
    ):
        arguments: dict[str, object] = {
            "ledger": empty,
            "clock": FixedClock(),
            "id_source": DeterministicIds(),
        }
        arguments.update(values)
        with pytest.raises(ExecutorCrashRecoveryRejected):
            ExecutorCrashRecoveryCoordinator(**arguments)  # type: ignore[arg-type]
    with pytest.raises(DouyinSideEffectRecoveryRejected):
        DouyinSideEffectRecovery.without_page_context(
            ledger=cast(ExecutorLedger, object()),
            clock=FixedClock(),
        )

    one_shot = coordinator(empty)
    assert repr(one_shot) == "ExecutorCrashRecoveryCoordinator(<redacted>)"
    assert one_shot.run() == ()
    with pytest.raises(ExecutorCrashRecoveryRejected):
        one_shot.run()

    failed = coordinator(empty)
    monkeypatch.setattr(
        empty,
        "list_crash_recovery_side_effects",
        lambda **_values: (_ for _ in ()).throw(ExecutorLedgerRejected()),
    )
    with pytest.raises(ExecutorCrashRecoveryRejected) as captured:
        failed.run()
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


@pytest.mark.parametrize("failure", ("clock", "id", "checkpoint", "effect", "commit"))
def test_crash_recovery_fails_closed_at_every_projection_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    opened, _dispatched_id, _prepared_id, _started = seed(tmp_path / failure)
    clock: object = FixedClock()
    ids: object = DeterministicIds()
    if failure == "clock":
        clock = type("InvalidClock", (), {"now": lambda _self: object()})()
    elif failure == "id":

        def invalid_id() -> object:
            return object()

        ids = invalid_id
    elif failure == "checkpoint":
        monkeypatch.setattr(opened, "get_checkpoint", lambda _attempt_id: None)
    elif failure == "effect":
        original_get_side_effect = opened.get_side_effect
        reads = count()

        def lose_effect_after_recovery(action_id: str) -> object:
            return original_get_side_effect(action_id) if next(reads) == 0 else None

        monkeypatch.setattr(opened, "get_side_effect", lose_effect_after_recovery)
    else:
        monkeypatch.setattr(
            opened,
            "commit_side_effect_recovery",
            lambda **_values: type("InvalidEntry", (), {"message": object()})(),
        )
    recovery = ExecutorCrashRecoveryCoordinator(
        ledger=opened,
        clock=cast(FixedClock, clock),
        id_source=cast(Callable[[], object], ids),
    )

    with pytest.raises(ExecutorCrashRecoveryRejected):
        recovery.run()


def test_ledger_crash_recovery_queries_reject_invalid_limits_storage_and_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    empty = ExecutorLedger(
        state_directory=tmp_path / "empty-ledger",
        installation_id=str(INSTALLATION_ID),
        executor_id=str(EXECUTOR_ID),
    )
    for invalid_limit in (cast(int, True), 0, 101):
        with pytest.raises(ExecutorLedgerRejected):
            empty.list_crash_recovery_side_effects(limit=invalid_limit)
    with pytest.raises(ExecutorLedgerRejected):
        empty.get_side_effect_recovery_event("not-an-action")
    with pytest.raises(ExecutorLedgerRejected):
        empty.initial_task_command(resource_id(999))

    def reject_connect() -> object:
        raise RuntimeError("private storage failure")

    monkeypatch.setattr(empty, "_connect", reject_connect)
    with pytest.raises(ExecutorLedgerRejected) as captured:
        empty.list_crash_recovery_side_effects(limit=1)
    assert captured.value.__cause__ is None

    drifted, action_id, _prepared_id, _started = seed(tmp_path / "drifted-event")
    recovered = coordinator(drifted).run()[0]
    document = recovered.model_dump(mode="json")
    document["payload"]["action_id"] = resource_id(998)
    with closing(sqlite3.connect(drifted.database_path)) as connection:
        connection.execute(
            "UPDATE executor_outbox SET envelope = ? WHERE idempotency_key = ?",
            (
                json.dumps(document, separators=(",", ":"), sort_keys=True),
                f"executor:recovery:{action_id}",
            ),
        )
        connection.commit()
    with pytest.raises(ExecutorLedgerRejected):
        drifted.get_side_effect_recovery_event(action_id)

    wrong_command, _action, _prepared, started = seed(tmp_path / "wrong-command")
    with closing(sqlite3.connect(wrong_command.database_path)) as connection:
        connection.execute(
            "UPDATE executor_commands SET envelope = ?",
            (
                json.dumps(
                    started[0].model_dump(mode="json"),
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            ),
        )
        connection.commit()
    with pytest.raises(ExecutorLedgerRejected):
        wrong_command.initial_task_command(str(ATTEMPT_ID))


def test_ledger_crash_recovery_commit_is_atomic_replayable_and_rejects_drift(
    tmp_path: Path,
) -> None:
    opened, action_id, _prepared_id, _started = seed(tmp_path / "commit")
    recovered = coordinator(opened).run()[0]

    replay = opened.commit_side_effect_recovery(
        action_id=action_id,
        expected_checkpoint_revision=999,
        event=recovered,
    )
    assert replay.replayed is True
    assert replay.message == recovered
    mismatched = recovered.model_copy(update={"message_type": "step.completed"})
    with pytest.raises(ExecutorLedgerRejected):
        opened.commit_side_effect_recovery(
            action_id=action_id,
            expected_checkpoint_revision=3,
            event=mismatched,
        )
    with pytest.raises(ExecutorLedgerRejected):
        opened.commit_side_effect_recovery(
            action_id=action_id,
            expected_checkpoint_revision=0,
            event=recovered,
        )

    missing_action_id = resource_id(999)
    missing = recovered.model_copy(
        update={
            "idempotency_key": f"executor:recovery:{missing_action_id}",
            "payload": {**recovered.payload, "action_id": missing_action_id},
        }
    )
    with pytest.raises(ExecutorLedgerRejected):
        opened.commit_side_effect_recovery(
            action_id=missing_action_id,
            expected_checkpoint_revision=3,
            event=missing,
        )

    inconsistent, unsettled_id, _prepared, _events = seed(tmp_path / "inconsistent")
    effect = inconsistent.get_side_effect(unsettled_id)
    assert effect is not None
    inconsistent.mark_side_effect_uncertain(
        action_id=unsettled_id,
        effect_fingerprint=effect.effect_fingerprint,
        uncertain_at=NOW + timedelta(seconds=30),
    )
    source = inconsistent.initial_task_command(str(ATTEMPT_ID))
    invalid = event(source, "task.outcome_uncertain", 3).model_copy(
        update={
            "idempotency_key": f"executor:recovery:{unsettled_id}",
            "payload": {
                "action_id": unsettled_id,
                "evidence": "comment_confirmed",
                "evidence_version": "action-result-evidence.v1",
            },
        }
    )
    with pytest.raises(ExecutorLedgerRejected):
        inconsistent.commit_side_effect_recovery(
            action_id=unsettled_id,
            expected_checkpoint_revision=2,
            event=invalid,
        )
