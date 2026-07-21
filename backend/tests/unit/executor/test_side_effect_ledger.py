from __future__ import annotations

import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from automation_tool.executor.ledger import ExecutorLedger, ExecutorLedgerRejected
from automation_tool.executor.side_effect_ledger import LocalSideEffect, SideEffectState
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
    action_authorization_idempotency_key,
)

NOW = datetime(2026, 7, 20, 6, 0, tzinfo=UTC)
INSTALLATION_ID = ProtocolInstallationId("123e4567-e89b-42d3-a456-426614174005")
EXECUTOR_ID = ProtocolExecutorId("123e4567-e89b-42d3-a456-426614174006")
ATTEMPT_ID = ProtocolExecutionAttemptId("123e4567-e89b-42d3-a456-426614174007")
TASK_ID = ProtocolTaskId("123e4567-e89b-42d3-a456-426614174008")


def resource_id(index: int, kind: type[str]) -> str:
    return kind(str(UUID(f"323e4567-e89b-42d3-a456-{index:012d}")))


def claims(
    index: int,
    *,
    action: DouyinSearchExposureAction = DouyinSearchExposureAction.COMMENT,
    deadline_at: datetime = NOW + timedelta(minutes=5),
) -> ActionAuthorizationClaims:
    action_id = ProtocolActionId(resource_id(index, str))
    return ActionAuthorizationClaims(
        version=ACTION_AUTHORIZATION_VERSION,
        action_id=action_id,
        target_id=ProtocolTargetId(resource_id(index + 100, str)),
        execution_attempt_id=ATTEMPT_ID,
        task_id=TASK_ID,
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
        platform="douyin",
        action=action,
        idempotency_key=action_authorization_idempotency_key(action_id),
        authorized_at=NOW,
        deadline_at=deadline_at,
    )


def ledger(state_directory: Path) -> ExecutorLedger:
    return ExecutorLedger(
        state_directory=state_directory,
        installation_id=str(INSTALLATION_ID),
        executor_id=str(EXECUTOR_ID),
    )


def admit(
    opened: ExecutorLedger,
    index: int,
    *,
    action: DouyinSearchExposureAction = DouyinSearchExposureAction.COMMENT,
    admitted_at: datetime = NOW,
    deadline_at: datetime = NOW + timedelta(minutes=5),
) -> ActionAuthorizationClaims:
    authorization = claims(index, action=action, deadline_at=deadline_at)
    opened.admit_action(
        claims=authorization,
        authorization_fingerprint=hashlib.sha256(f"authorization-{index}".encode()).digest(),
        admitted_at=admitted_at,
        minimum_interval_seconds=1,
        task_action_limit=100,
    )
    return authorization


def effect_fingerprint(index: int) -> bytes:
    return hashlib.sha256(f"private effect {index}".encode()).digest()


def verification_fingerprint(index: int) -> bytes:
    return hashlib.sha256(f"private verification {index}".encode()).digest()


def test_a7_07_side_effect_state_machine_is_closed_and_redacted() -> None:
    assert tuple(SideEffectState) == (
        SideEffectState.PREPARED,
        SideEffectState.DISPATCHED,
        SideEffectState.VERIFIED,
        SideEffectState.UNCERTAIN,
    )
    valid = LocalSideEffect(
        action_id=resource_id(1, str),
        target_id=resource_id(101, str),
        execution_attempt_id=str(ATTEMPT_ID),
        task_id=str(TASK_ID),
        installation_id=str(INSTALLATION_ID),
        executor_id=str(EXECUTOR_ID),
        platform="douyin",
        action=DouyinSearchExposureAction.COMMENT,
        idempotency_key=f"action:{resource_id(1, str)}",
        effect_fingerprint=b"x" * 32,
        state=SideEffectState.PREPARED,
        prepared_at=NOW,
        dispatched_at=None,
        settled_at=None,
        verification_fingerprint=None,
        revision=1,
        replayed=False,
    )

    class ExplodingTimezone(tzinfo):
        def utcoffset(self, value: datetime | None) -> timedelta | None:
            raise RuntimeError

        def dst(self, value: datetime | None) -> timedelta | None:
            return timedelta(0)

        def tzname(self, value: datetime | None) -> str | None:
            return "exploding"

    for invalid in (
        {"state": cast(SideEffectState, "prepared")},
        {"action_id": cast(str, 1)},
        {"action_id": "not-a-uuid"},
        {"prepared_at": NOW.replace(tzinfo=None)},
        {"prepared_at": NOW.astimezone(timezone(timedelta(hours=1)))},
        {"prepared_at": NOW.replace(tzinfo=ExplodingTimezone())},
    ):
        with pytest.raises(ExecutorLedgerRejected):
            replace(valid, **invalid)


def test_v4_ledger_migrates_to_exact_v6_without_losing_action_admission(
    tmp_path: Path,
) -> None:
    state_directory = tmp_path / "legacy-v4"
    opened = ledger(state_directory)
    authorization = admit(opened, 1)
    with sqlite3.connect(opened.database_path) as connection:
        connection.execute("DROP TABLE executor_side_effects")
        connection.execute("ALTER TABLE executor_action_guard DROP COLUMN network_connected")
        connection.execute("PRAGMA user_version = 4")

    migrated = ledger(state_directory)

    assert migrated.get_action_admission(str(authorization.action_id)) is not None
    assert migrated.get_side_effect(str(authorization.action_id)) is None
    with sqlite3.connect(migrated.database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (6,)
        columns = {row[1] for row in connection.execute("PRAGMA table_info(executor_side_effects)")}
        invalid_states = (
            ("prepared", None, None, b"p" * 32, 1),
            (
                "verified",
                "2026-07-20T06:00:01.000000Z",
                "2026-07-20T06:00:02.000000Z",
                None,
                3,
            ),
            (
                "uncertain",
                "2026-07-20T06:00:01.000000Z",
                "2026-07-20T06:00:02.000000Z",
                b"p" * 32,
                3,
            ),
        )
        for state, dispatched_at, settled_at, proof, revision in invalid_states:
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO executor_side_effects (
                        action_id, effect_fingerprint, state, prepared_at,
                        dispatched_at, settled_at, verification_fingerprint, revision
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(authorization.action_id),
                        effect_fingerprint(1),
                        state,
                        "2026-07-20T06:00:00.000000Z",
                        dispatched_at,
                        settled_at,
                        proof,
                        revision,
                    ),
                )
    assert columns == {
        "action_id",
        "effect_fingerprint",
        "state",
        "prepared_at",
        "dispatched_at",
        "settled_at",
        "verification_fingerprint",
        "revision",
    }


def test_side_effect_lifecycle_is_exact_durable_and_never_redispatches(
    tmp_path: Path,
) -> None:
    state_directory = tmp_path / "state"
    opened = ledger(state_directory)
    authorization = admit(opened, 10)
    fingerprint = effect_fingerprint(10)
    proof = verification_fingerprint(10)

    prepared = opened.prepare_side_effect(
        action_id=str(authorization.action_id),
        effect_fingerprint=fingerprint,
        prepared_at=NOW + timedelta(seconds=1),
    )
    prepared_replay = opened.prepare_side_effect(
        action_id=str(authorization.action_id),
        effect_fingerprint=fingerprint,
        prepared_at=NOW + timedelta(seconds=2),
    )
    restarted = ledger(state_directory)
    dispatched = restarted.begin_side_effect_dispatch(
        action_id=str(authorization.action_id),
        effect_fingerprint=fingerprint,
        dispatched_at=NOW + timedelta(seconds=3),
    )
    dispatch_replay = restarted.begin_side_effect_dispatch(
        action_id=str(authorization.action_id),
        effect_fingerprint=fingerprint,
        dispatched_at=NOW + timedelta(seconds=4),
    )
    with pytest.raises(ExecutorLedgerRejected):
        restarted.verify_side_effect(
            action_id=str(authorization.action_id),
            effect_fingerprint=effect_fingerprint(11),
            verification_fingerprint=proof,
            verified_at=NOW + timedelta(seconds=5),
        )
    with pytest.raises(ExecutorLedgerRejected):
        restarted.verify_side_effect(
            action_id=str(authorization.action_id),
            effect_fingerprint=fingerprint,
            verification_fingerprint=proof,
            verified_at=(NOW + timedelta(seconds=5)).replace(tzinfo=None),
        )
    verified = restarted.verify_side_effect(
        action_id=str(authorization.action_id),
        effect_fingerprint=fingerprint,
        verification_fingerprint=proof,
        verified_at=NOW + timedelta(seconds=5),
    )
    verify_replay = restarted.verify_side_effect(
        action_id=str(authorization.action_id),
        effect_fingerprint=fingerprint,
        verification_fingerprint=proof,
        verified_at=NOW + timedelta(seconds=6),
    )
    with pytest.raises(ExecutorLedgerRejected):
        restarted.verify_side_effect(
            action_id=str(authorization.action_id),
            effect_fingerprint=fingerprint,
            verification_fingerprint=verification_fingerprint(11),
            verified_at=NOW + timedelta(seconds=7),
        )
    post_terminal_dispatch = restarted.begin_side_effect_dispatch(
        action_id=str(authorization.action_id),
        effect_fingerprint=fingerprint,
        dispatched_at=NOW + timedelta(seconds=8),
    )

    assert prepared.state is SideEffectState.PREPARED
    assert prepared.revision == 1 and prepared.replayed is False
    assert prepared_replay.state is SideEffectState.PREPARED
    assert prepared_replay.revision == 1 and prepared_replay.replayed is True
    assert dispatched.state is SideEffectState.DISPATCHED
    assert dispatched.revision == 2 and dispatched.replayed is False
    assert dispatch_replay.state is SideEffectState.DISPATCHED
    assert dispatch_replay.revision == 2 and dispatch_replay.replayed is True
    assert verified.state is SideEffectState.VERIFIED
    assert verified.revision == 3 and verified.replayed is False
    assert verify_replay.state is SideEffectState.VERIFIED
    assert verify_replay.revision == 3 and verify_replay.replayed is True
    assert post_terminal_dispatch.state is SideEffectState.VERIFIED
    assert post_terminal_dispatch.replayed is True
    assert restarted.get_side_effect(str(authorization.action_id)) == replace(
        verified, replayed=False
    )
    assert restarted.list_unresolved_side_effects(limit=100) == ()
    assert "323e4567" not in repr(verified)
    assert "private" not in restarted.database_path.read_bytes().decode("utf-8", errors="ignore")


def test_network_gate_stops_new_dispatch_at_the_prepared_safe_point(tmp_path: Path) -> None:
    opened = ledger(tmp_path / "network-gate")
    authorization = admit(opened, 19)
    fingerprint = effect_fingerprint(19)
    opened.prepare_side_effect(
        action_id=str(authorization.action_id),
        effect_fingerprint=fingerprint,
        prepared_at=NOW + timedelta(seconds=1),
    )

    assert opened.transport_connected() is True
    assert opened.set_transport_connected(False) is True
    assert opened.set_transport_connected(False) is False
    with pytest.raises(ExecutorLedgerRejected):
        opened.begin_side_effect_dispatch(
            action_id=str(authorization.action_id),
            effect_fingerprint=fingerprint,
            dispatched_at=NOW + timedelta(seconds=2),
        )
    retained = opened.get_side_effect(str(authorization.action_id))
    assert retained is not None
    assert retained.state is SideEffectState.PREPARED
    assert retained.revision == 1

    assert opened.set_transport_connected(True) is True
    dispatched = opened.begin_side_effect_dispatch(
        action_id=str(authorization.action_id),
        effect_fingerprint=fingerprint,
        dispatched_at=NOW + timedelta(seconds=3),
    )
    assert dispatched.state is SideEffectState.DISPATCHED
    with pytest.raises(ExecutorLedgerRejected):
        opened.set_transport_connected(1)  # type: ignore[arg-type]

    missing = ledger(tmp_path / "missing-network-gate")
    missing_authorization = admit(missing, 20)
    missing_fingerprint = effect_fingerprint(20)
    missing.prepare_side_effect(
        action_id=str(missing_authorization.action_id),
        effect_fingerprint=missing_fingerprint,
        prepared_at=NOW + timedelta(seconds=1),
    )
    with sqlite3.connect(missing.database_path) as connection:
        connection.execute("DELETE FROM executor_action_guard")
    with pytest.raises(ExecutorLedgerRejected):
        missing.transport_connected()
    with pytest.raises(ExecutorLedgerRejected):
        missing.set_transport_connected(False)
    with pytest.raises(ExecutorLedgerRejected):
        missing.begin_side_effect_dispatch(
            action_id=str(missing_authorization.action_id),
            effect_fingerprint=missing_fingerprint,
            dispatched_at=NOW + timedelta(seconds=2),
        )


def test_dispatched_side_effect_can_only_converge_to_verified_or_uncertain_once(
    tmp_path: Path,
) -> None:
    opened = ledger(tmp_path / "state")
    first = admit(opened, 20)
    second = admit(opened, 21, admitted_at=NOW + timedelta(seconds=1))
    first_effect = effect_fingerprint(20)
    second_effect = effect_fingerprint(21)
    for authorization, fingerprint, offset in (
        (first, first_effect, 2),
        (second, second_effect, 3),
    ):
        opened.prepare_side_effect(
            action_id=str(authorization.action_id),
            effect_fingerprint=fingerprint,
            prepared_at=NOW + timedelta(seconds=offset),
        )
        opened.begin_side_effect_dispatch(
            action_id=str(authorization.action_id),
            effect_fingerprint=fingerprint,
            dispatched_at=NOW + timedelta(seconds=offset + 1),
        )

    uncertain = opened.mark_side_effect_uncertain(
        action_id=str(first.action_id),
        effect_fingerprint=first_effect,
        uncertain_at=NOW + timedelta(seconds=5),
    )
    replay = opened.mark_side_effect_uncertain(
        action_id=str(first.action_id),
        effect_fingerprint=first_effect,
        uncertain_at=NOW + timedelta(seconds=6),
    )
    assert uncertain.state is SideEffectState.UNCERTAIN
    assert uncertain.revision == 3 and uncertain.replayed is False
    assert replay.replayed is True
    with pytest.raises(ExecutorLedgerRejected):
        opened.verify_side_effect(
            action_id=str(first.action_id),
            effect_fingerprint=first_effect,
            verification_fingerprint=verification_fingerprint(20),
            verified_at=NOW + timedelta(seconds=7),
        )

    def settle(mode: str) -> object:
        try:
            if mode == "verified":
                return opened.verify_side_effect(
                    action_id=str(second.action_id),
                    effect_fingerprint=second_effect,
                    verification_fingerprint=verification_fingerprint(21),
                    verified_at=NOW + timedelta(seconds=8),
                )
            return opened.mark_side_effect_uncertain(
                action_id=str(second.action_id),
                effect_fingerprint=second_effect,
                uncertain_at=NOW + timedelta(seconds=8),
            )
        except Exception as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(settle, ("verified", "uncertain")))
    assert sum(isinstance(result, LocalSideEffect) for result in results) == 1
    assert sum(isinstance(result, ExecutorLedgerRejected) for result in results) == 1
    settled = opened.get_side_effect(str(second.action_id))
    assert settled is not None
    assert settled.state in {
        SideEffectState.VERIFIED,
        SideEffectState.UNCERTAIN,
    }


def test_dispatch_claim_has_one_new_winner_and_survives_restart(
    tmp_path: Path,
) -> None:
    state_directory = tmp_path / "state"
    opened = ledger(state_directory)
    authorization = admit(opened, 30)
    fingerprint = effect_fingerprint(30)
    opened.prepare_side_effect(
        action_id=str(authorization.action_id),
        effect_fingerprint=fingerprint,
        prepared_at=NOW + timedelta(seconds=1),
    )
    with pytest.raises(ExecutorLedgerRejected):
        opened.prepare_side_effect(
            action_id=str(authorization.action_id),
            effect_fingerprint=fingerprint,
            prepared_at=NOW.replace(tzinfo=None),
        )
    with pytest.raises(ExecutorLedgerRejected):
        opened.begin_side_effect_dispatch(
            action_id=str(authorization.action_id),
            effect_fingerprint=effect_fingerprint(51),
            dispatched_at=NOW + timedelta(seconds=2),
        )
    with pytest.raises(ExecutorLedgerRejected):
        opened.begin_side_effect_dispatch(
            action_id=str(authorization.action_id),
            effect_fingerprint=fingerprint,
            dispatched_at=(NOW + timedelta(seconds=2)).replace(tzinfo=None),
        )
    with pytest.raises(ExecutorLedgerRejected):
        opened._settle_side_effect(
            action_id=str(authorization.action_id),
            effect_fingerprint=fingerprint,
            target_state=cast(SideEffectState, "private"),
            verification_fingerprint=None,
            settled_at=NOW + timedelta(seconds=2),
        )

    def dispatch(_: int) -> LocalSideEffect:
        return ledger(state_directory).begin_side_effect_dispatch(
            action_id=str(authorization.action_id),
            effect_fingerprint=fingerprint,
            dispatched_at=NOW + timedelta(seconds=2),
        )

    with ThreadPoolExecutor(max_workers=5) as pool:
        results = tuple(pool.map(dispatch, range(5)))

    assert sum(not result.replayed for result in results) == 1
    assert sum(result.replayed for result in results) == 4
    assert all(result.state is SideEffectState.DISPATCHED for result in results)
    recovered = ledger(state_directory).list_unresolved_side_effects(limit=100)
    assert len(recovered) == 1
    assert recovered[0].state is SideEffectState.DISPATCHED
    assert recovered[0].replayed is False


def test_prepare_and_dispatch_require_current_admission_deadline_and_open_latch(
    tmp_path: Path,
) -> None:
    opened = ledger(tmp_path / "state")
    unknown = resource_id(999, str)
    with pytest.raises(ExecutorLedgerRejected):
        opened.prepare_side_effect(
            action_id=unknown,
            effect_fingerprint=effect_fingerprint(999),
            prepared_at=NOW,
        )

    browse = admit(opened, 40, action=DouyinSearchExposureAction.BROWSE)
    with pytest.raises(ExecutorLedgerRejected):
        opened.prepare_side_effect(
            action_id=str(browse.action_id),
            effect_fingerprint=effect_fingerprint(40),
            prepared_at=NOW + timedelta(seconds=1),
        )

    direct_message = admit(
        opened,
        43,
        action=DouyinSearchExposureAction.DIRECT_MESSAGE,
        admitted_at=NOW + timedelta(seconds=1),
    )
    direct_prepared = opened.prepare_side_effect(
        action_id=str(direct_message.action_id),
        effect_fingerprint=effect_fingerprint(43),
        prepared_at=NOW + timedelta(seconds=2),
    )
    assert direct_prepared.action is DouyinSearchExposureAction.DIRECT_MESSAGE

    expired = admit(
        opened,
        41,
        admitted_at=NOW + timedelta(seconds=1),
        deadline_at=NOW + timedelta(seconds=2),
    )
    with pytest.raises(ExecutorLedgerRejected):
        opened.prepare_side_effect(
            action_id=str(expired.action_id),
            effect_fingerprint=effect_fingerprint(41),
            prepared_at=NOW + timedelta(seconds=2),
        )

    current = admit(opened, 42, admitted_at=NOW + timedelta(seconds=2))
    stopped = opened.engage_action_emergency_stop(changed_at=NOW + timedelta(seconds=3))
    with pytest.raises(ExecutorLedgerRejected):
        opened.prepare_side_effect(
            action_id=str(current.action_id),
            effect_fingerprint=effect_fingerprint(42),
            prepared_at=NOW + timedelta(seconds=4),
        )
    opened.clear_action_emergency_stop(
        expected_revision=stopped.revision,
        changed_at=NOW + timedelta(seconds=5),
    )
    opened.prepare_side_effect(
        action_id=str(current.action_id),
        effect_fingerprint=effect_fingerprint(42),
        prepared_at=NOW + timedelta(seconds=6),
    )
    opened.engage_action_emergency_stop(changed_at=NOW + timedelta(seconds=7))
    with pytest.raises(ExecutorLedgerRejected):
        opened.begin_side_effect_dispatch(
            action_id=str(current.action_id),
            effect_fingerprint=effect_fingerprint(42),
            dispatched_at=NOW + timedelta(seconds=8),
        )


def test_side_effect_tamper_transition_order_and_inputs_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened = ledger(tmp_path / "state")
    authorization = admit(opened, 50)
    fingerprint = effect_fingerprint(50)
    opened.prepare_side_effect(
        action_id=str(authorization.action_id),
        effect_fingerprint=fingerprint,
        prepared_at=NOW + timedelta(seconds=1),
    )
    with pytest.raises(ExecutorLedgerRejected):
        opened.prepare_side_effect(
            action_id=str(authorization.action_id),
            effect_fingerprint=effect_fingerprint(51),
            prepared_at=NOW + timedelta(seconds=2),
        )
    with pytest.raises(ExecutorLedgerRejected):
        opened.verify_side_effect(
            action_id=str(authorization.action_id),
            effect_fingerprint=fingerprint,
            verification_fingerprint=verification_fingerprint(50),
            verified_at=NOW + timedelta(seconds=2),
        )
    with pytest.raises(ExecutorLedgerRejected):
        opened.mark_side_effect_uncertain(
            action_id=str(authorization.action_id),
            effect_fingerprint=fingerprint,
            uncertain_at=NOW + timedelta(seconds=2),
        )
    for invalid in (b"", bytearray(b"x" * 32), "private"):
        with pytest.raises(ExecutorLedgerRejected):
            opened.begin_side_effect_dispatch(
                action_id=str(authorization.action_id),
                effect_fingerprint=cast(bytes, invalid),
                dispatched_at=NOW + timedelta(seconds=2),
            )
    for invalid_limit in (0, 101, True):
        with pytest.raises(ExecutorLedgerRejected):
            opened.list_unresolved_side_effects(limit=invalid_limit)

    with sqlite3.connect(opened.database_path) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            "UPDATE executor_side_effects SET state = 'private' WHERE action_id = ?",
            (str(authorization.action_id),),
        )
    with pytest.raises(ExecutorLedgerRejected):
        opened.get_side_effect(str(authorization.action_id))

    with monkeypatch.context() as scoped:
        scoped.setattr(opened, "_connect", lambda: (_ for _ in ()).throw(OSError()))
        with pytest.raises(ExecutorLedgerRejected):
            opened.get_side_effect(str(authorization.action_id))
