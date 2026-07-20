from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from automation_tool.executor import ledger as ledger_module
from automation_tool.executor.action_authorization import (
    ActionAuthorizationExpectation,
    Ed25519ActionAuthorizationVerifier,
)
from automation_tool.executor.action_gate import (
    ActionGateLimited,
    ActionGateRejected,
    ExecutorActionGate,
    LocalActionHardPolicy,
    LocalActionLimitReason,
)
from automation_tool.executor.ledger import (
    ExecutorActionAdmissionLimited,
    ExecutorLedger,
    ExecutorLedgerRejected,
    LocalActionAdmission,
    LocalActionHardPolicyBinding,
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
    action_authorization_idempotency_key,
    action_authorization_signing_input,
    encode_action_authorization_token,
    parse_action_authorization_token,
)

NOW = datetime(2026, 7, 20, 3, 0, tzinfo=UTC)
PRIVATE_KEY = bytes(range(32))
INSTALLATION_ID = ProtocolInstallationId("123e4567-e89b-42d3-a456-426614174005")
EXECUTOR_ID = ProtocolExecutorId("123e4567-e89b-42d3-a456-426614174006")
ATTEMPT_ID = ProtocolExecutionAttemptId("123e4567-e89b-42d3-a456-426614174007")
TASK_ID = ProtocolTaskId("123e4567-e89b-42d3-a456-426614174008")


class MutableClock:
    def __init__(self, value: object = NOW) -> None:
        self.value = value

    def now(self) -> datetime:
        return cast(datetime, self.value)


class BrokenTimezone(tzinfo):
    def utcoffset(self, value: datetime | None) -> timedelta | None:
        raise RuntimeError("private timezone failure")

    def dst(self, value: datetime | None) -> timedelta | None:
        return None

    def tzname(self, value: datetime | None) -> str | None:
        return None


def resource_id(index: int, kind: type[str]) -> str:
    return kind(str(UUID(f"323e4567-e89b-42d3-a456-{index:012d}")))


def authorization(
    index: int,
    *,
    task_id: ProtocolTaskId = TASK_ID,
) -> tuple[str, ActionAuthorizationExpectation]:
    action_id = ProtocolActionId(resource_id(index, str))
    claims = ActionAuthorizationClaims(
        version=ACTION_AUTHORIZATION_VERSION,
        action_id=action_id,
        target_id=ProtocolTargetId(resource_id(index + 100, str)),
        execution_attempt_id=ATTEMPT_ID,
        task_id=task_id,
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
        platform="douyin",
        action=DouyinSearchExposureAction.COMMENT,
        idempotency_key=action_authorization_idempotency_key(action_id),
        authorized_at=NOW,
        deadline_at=NOW + timedelta(minutes=5),
    )
    token = encode_action_authorization_token(
        claims,
        Ed25519PrivateKey.from_private_bytes(PRIVATE_KEY).sign(
            action_authorization_signing_input(claims)
        ),
    )
    return token, ActionAuthorizationExpectation(
        action_id=claims.action_id,
        target_id=claims.target_id,
        execution_attempt_id=claims.execution_attempt_id,
        task_id=claims.task_id,
        installation_id=claims.installation_id,
        executor_id=claims.executor_id,
        platform=claims.platform,
        action=claims.action,
        idempotency_key=claims.idempotency_key,
    )


def action_gate(
    state_directory: Path,
    clock: MutableClock,
    *,
    minimum_interval: timedelta = timedelta(seconds=30),
    task_action_limit: int = 2,
) -> ExecutorActionGate:
    ledger = ExecutorLedger(
        state_directory=state_directory,
        installation_id=str(INSTALLATION_ID),
        executor_id=str(EXECUTOR_ID),
    )
    public_key = Ed25519PrivateKey.from_private_bytes(PRIVATE_KEY).public_key().public_bytes_raw()
    return ExecutorActionGate(
        ledger=ledger,
        verifier=Ed25519ActionAuthorizationVerifier(public_key=public_key, clock=clock),
        policy=LocalActionHardPolicy(
            minimum_interval=minimum_interval,
            task_action_limit=task_action_limit,
        ),
        clock=clock,
    )


def test_local_policy_requires_explicit_bounded_hard_limits() -> None:
    policy = LocalActionHardPolicy(
        minimum_interval=timedelta(seconds=30),
        task_action_limit=2,
    )

    assert policy.minimum_interval == timedelta(seconds=30)
    assert policy.task_action_limit == 2
    assert "30" not in repr(policy)
    for values in (
        {"minimum_interval": "30 seconds", "task_action_limit": 2},
        {"minimum_interval": timedelta(0), "task_action_limit": 2},
        {"minimum_interval": timedelta(microseconds=1), "task_action_limit": 2},
        {"minimum_interval": timedelta(hours=2), "task_action_limit": 2},
        {"minimum_interval": timedelta(seconds=30), "task_action_limit": 0},
        {"minimum_interval": timedelta(seconds=30), "task_action_limit": True},
        {"minimum_interval": timedelta(seconds=30), "task_action_limit": 101},
    ):
        with pytest.raises(ActionGateRejected):
            LocalActionHardPolicy(**values)  # type: ignore[arg-type]


def test_persisted_local_policy_can_only_tighten_and_rejects_corrupt_values(
    tmp_path: Path,
) -> None:
    clock = MutableClock()
    initial = action_gate(tmp_path / "state", clock)
    tightened = action_gate(
        tmp_path / "state",
        clock,
        minimum_interval=timedelta(seconds=60),
        task_action_limit=1,
    )
    loosened = action_gate(
        tmp_path / "state",
        clock,
        minimum_interval=timedelta(seconds=1),
        task_action_limit=100,
    )

    assert initial._policy.minimum_interval == timedelta(seconds=30)
    assert tightened._policy.minimum_interval == timedelta(seconds=60)
    assert tightened._policy.task_action_limit == 1
    assert loosened._policy == tightened._policy
    binding = LocalActionHardPolicyBinding(
        minimum_interval_seconds=60,
        task_action_limit=1,
    )
    assert "60" not in repr(binding)
    with pytest.raises(ExecutorLedgerRejected):
        LocalActionHardPolicyBinding(minimum_interval_seconds=0, task_action_limit=1)
    with pytest.raises(ExecutorLedgerRejected):
        initial._ledger.bind_action_hard_policy(
            minimum_interval_seconds=0,
            task_action_limit=1,
        )
    with sqlite3.connect(initial._ledger.database_path) as connection:
        connection.execute("DELETE FROM executor_action_policy")
    with pytest.raises(ActionGateRejected):
        action_gate(
            tmp_path / "state",
            clock,
            minimum_interval=timedelta(seconds=1),
            task_action_limit=100,
        )
    partial = action_gate(tmp_path / "partial-state", clock)
    with sqlite3.connect(partial._ledger.database_path) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute("UPDATE executor_action_policy SET task_action_limit = NULL")
    with pytest.raises(ActionGateRejected):
        action_gate(tmp_path / "partial-state", clock)


def test_gate_enforces_local_interval_task_limit_and_exact_replay_durably(
    tmp_path: Path,
) -> None:
    clock = MutableClock()
    gate = action_gate(tmp_path / "state", clock)
    first_token, first_expected = authorization(1)
    second_token, second_expected = authorization(2)
    third_token, third_expected = authorization(3)
    other_task = ProtocolTaskId(resource_id(900, str))
    fourth_token, fourth_expected = authorization(4, task_id=other_task)

    first = gate.admit(token=first_token, expected=first_expected)
    replay = gate.admit(token=first_token, expected=first_expected)
    weakened = action_gate(
        tmp_path / "state",
        clock,
        minimum_interval=timedelta(seconds=1),
        task_action_limit=100,
    )
    assert weakened._policy.minimum_interval == timedelta(seconds=30)
    assert weakened._policy.task_action_limit == 2
    clock.value = NOW + timedelta(seconds=29)
    with pytest.raises(ActionGateLimited) as interval:
        weakened.admit(token=second_token, expected=second_expected)
    clock.value = NOW + timedelta(seconds=30)
    second = weakened.admit(token=second_token, expected=second_expected)
    clock.value = NOW + timedelta(seconds=60)
    with pytest.raises(ActionGateLimited) as task_limit:
        weakened.admit(token=third_token, expected=third_expected)
    fourth = weakened.admit(token=fourth_token, expected=fourth_expected)

    assert first.task_action_ordinal == 1
    assert first.replayed is False
    assert replay.task_action_ordinal == 1
    assert replay.replayed is True
    assert second.task_action_ordinal == 2
    assert interval.value.reason is LocalActionLimitReason.MINIMUM_INTERVAL
    assert task_limit.value.reason is LocalActionLimitReason.TASK_ACTION_LIMIT
    assert action_gate(tmp_path / "state", clock).admission(first_expected.action_id) == first
    assert fourth.task_action_ordinal == 1
    assert "323e4567" not in repr(first)


def test_emergency_stop_latch_survives_restart_and_needs_local_revision_to_clear(
    tmp_path: Path,
) -> None:
    clock = MutableClock()
    state_directory = tmp_path / "state"
    gate = action_gate(state_directory, clock)
    stopped = gate.engage_emergency_stop()
    replay = gate.engage_emergency_stop()
    token, expected = authorization(10)

    restarted = action_gate(state_directory, clock)
    with pytest.raises(ActionGateLimited) as limited:
        restarted.admit(token=token, expected=expected)
    with pytest.raises(ActionGateRejected):
        restarted.clear_emergency_stop(expected_revision=stopped.revision + 1)
    clock.value = NOW + timedelta(seconds=1)
    cleared = restarted.clear_emergency_stop(expected_revision=stopped.revision)
    admission = restarted.admit(token=token, expected=expected)

    assert stopped.engaged is True
    assert stopped.revision == 1
    assert replay == stopped
    assert limited.value.reason is LocalActionLimitReason.EMERGENCY_STOP
    assert cleared.engaged is False
    assert cleared.revision == 2
    assert admission.task_action_ordinal == 1


def test_gate_constructor_clock_and_boundary_failures_are_fixed_and_redacted(
    tmp_path: Path,
) -> None:
    state_directory = tmp_path / "state"
    clock = MutableClock()
    gate = action_gate(state_directory, clock)
    token, expected = authorization(20)

    assert gate._ledger.installation_id == str(INSTALLATION_ID)
    assert gate._ledger.executor_id == str(EXECUTOR_ID)
    assert gate.admission(expected.action_id) is None
    assert gate.emergency_stop().revision == 0
    assert "private" not in repr(gate).lower()
    with pytest.raises(ActionGateRejected):
        ActionGateLimited("private")  # type: ignore[arg-type]
    with pytest.raises(ExecutorLedgerRejected):
        ExecutorActionAdmissionLimited("private")  # type: ignore[arg-type]
    for field, invalid in (
        ("ledger", object()),
        ("verifier", object()),
        ("policy", object()),
        ("clock", object()),
    ):
        values: dict[str, object] = {
            "ledger": gate._ledger,
            "verifier": gate._verifier,
            "policy": gate._policy,
            "clock": clock,
        }
        values[field] = invalid
        with pytest.raises(ActionGateRejected):
            ExecutorActionGate(**values)  # type: ignore[arg-type]

    with pytest.raises(ActionGateRejected) as invalid_token:
        gate.admit(token="private", expected=expected)
    assert invalid_token.value.__cause__ is None
    assert "private" not in str(invalid_token.value)
    with pytest.raises(ActionGateRejected):
        gate.admission(ProtocolTargetId(resource_id(21, str)))  # type: ignore[arg-type]
    with pytest.raises(ActionGateRejected):
        gate.clear_emergency_stop(expected_revision=0)

    for invalid_now in (
        "private",
        datetime(2026, 7, 20, 3, 0),
        datetime(2026, 7, 20, 11, 0, tzinfo=timezone(timedelta(hours=8))),
        datetime(2026, 7, 20, 3, 0, tzinfo=BrokenTimezone()),
    ):
        clock.value = invalid_now
        with pytest.raises(ActionGateRejected):
            gate.engage_emergency_stop()
    clock.value = NOW
    assert gate.admit(token=token, expected=expected).task_action_ordinal == 1


def test_ledger_rejects_invalid_local_admission_stop_inputs_and_broken_utc(
    tmp_path: Path,
) -> None:
    clock = MutableClock()
    gate = action_gate(tmp_path / "state", clock)
    token, _expected = authorization(25)
    parsed = parse_action_authorization_token(token)

    with pytest.raises(ExecutorLedgerRejected):
        gate._ledger.admit_action(
            claims=parsed.claims,
            authorization_fingerprint=parsed.fingerprint,
            admitted_at=cast(datetime, "private"),
            minimum_interval_seconds=30,
            task_action_limit=2,
        )
    with pytest.raises(ExecutorLedgerRejected):
        gate._ledger.engage_action_emergency_stop(changed_at=datetime(2026, 7, 20, 3, 0))
    with pytest.raises(ExecutorLedgerRejected):
        gate._ledger.clear_action_emergency_stop(
            expected_revision=0,
            changed_at=NOW,
        )
    assert ledger_module._canonical_utc("private") is None
    assert (
        ledger_module._canonical_utc(
            datetime(2026, 7, 20, 11, 0, tzinfo=timezone(timedelta(hours=8)))
        )
        is None
    )
    assert (
        ledger_module._canonical_utc(datetime(2026, 7, 20, 3, 0, tzinfo=BrokenTimezone())) is None
    )
    with pytest.raises(ValueError):
        ledger_module._decode_utc(1)


def test_concurrent_local_admission_has_one_winner_and_exact_replays_do_not_recount(
    tmp_path: Path,
) -> None:
    state_directory = tmp_path / "state"
    clock = MutableClock()
    candidates = tuple(authorization(index) for index in range(30, 35))

    def admit(candidate: tuple[str, ActionAuthorizationExpectation]) -> object:
        try:
            return action_gate(
                state_directory,
                clock,
                minimum_interval=timedelta(seconds=1),
                task_action_limit=1,
            ).admit(token=candidate[0], expected=candidate[1])
        except Exception as error:
            return error

    with ThreadPoolExecutor(max_workers=5) as pool:
        results = tuple(pool.map(admit, candidates))

    winners = tuple(result for result in results if isinstance(result, LocalActionAdmission))
    assert len(winners) == 1
    assert all(
        not isinstance(result, BaseException) or isinstance(result, ActionGateLimited)
        for result in results
    )
    winner = winners[0]
    selected = next(
        candidate for candidate in candidates if str(candidate[1].action_id) == winner.action_id
    )
    replays = tuple(admit(selected) for _ in range(3))
    assert all(
        isinstance(result, LocalActionAdmission) and result.replayed is True for result in replays
    )


def test_corrupt_or_conflicting_local_facts_fail_closed_without_being_rewritten(
    tmp_path: Path,
) -> None:
    state_directory = tmp_path / "state"
    clock = MutableClock()
    gate = action_gate(state_directory, clock)
    token, expected = authorization(50)
    admitted = gate.admit(token=token, expected=expected)

    with sqlite3.connect(gate._ledger.database_path) as connection:
        connection.execute(
            "UPDATE executor_action_admissions SET authorization_fingerprint = ?",
            (bytes(reversed(admitted.authorization_fingerprint)),),
        )
    with pytest.raises(ActionGateRejected):
        gate.admit(token=token, expected=expected)

    with sqlite3.connect(gate._ledger.database_path) as connection:
        connection.execute(
            "UPDATE executor_action_admissions SET admitted_at = ?",
            ("2026-07-20T03:00:00.1Z",),
        )
    with pytest.raises(ActionGateRejected):
        gate.admission(expected.action_id)

    missing_guard = action_gate(tmp_path / "missing-guard", clock)
    with sqlite3.connect(missing_guard._ledger.database_path) as connection:
        connection.execute("DELETE FROM executor_action_guard")
    with pytest.raises(ActionGateRejected):
        missing_guard.emergency_stop()
    with pytest.raises(ActionGateRejected):
        missing_guard.engage_emergency_stop()


def test_local_clock_rollback_cannot_bypass_interval_or_reengage_after_clear(
    tmp_path: Path,
) -> None:
    clock = MutableClock(NOW + timedelta(seconds=30))
    gate = action_gate(tmp_path / "state", clock)
    first_token, first_expected = authorization(60)
    second_token, second_expected = authorization(61)
    gate.admit(token=first_token, expected=first_expected)

    clock.value = NOW
    with pytest.raises(ActionGateRejected):
        gate.admit(token=second_token, expected=second_expected)
    stopped = gate.engage_emergency_stop()
    clock.value = NOW + timedelta(seconds=31)
    gate.clear_emergency_stop(expected_revision=stopped.revision)
    clock.value = NOW + timedelta(seconds=30)
    with pytest.raises(ActionGateRejected):
        gate.engage_emergency_stop()


def test_local_value_objects_reject_corrupt_shapes_and_hide_identifiers(tmp_path: Path) -> None:
    clock = MutableClock()
    gate = action_gate(tmp_path / "state", clock)
    token, expected = authorization(70)
    admission = gate.admit(token=token, expected=expected)
    stop = gate.engage_emergency_stop()

    admission_changes: tuple[dict[str, object], ...] = (
        {"action_id": "private"},
        {"authorization_fingerprint": b""},
        {"deadline_at": admission.admitted_at},
        {"task_action_ordinal": 0},
        {"replayed": 1},
    )
    for changes in admission_changes:
        with pytest.raises(ExecutorLedgerRejected):
            replace(admission, **changes)  # type: ignore[arg-type]
    stop_changes: tuple[dict[str, object], ...] = (
        {"engaged": 1},
        {"revision": -1},
        {"revision": 0},
        {"changed_at": None},
    )
    for changes in stop_changes:
        with pytest.raises(ExecutorLedgerRejected):
            replace(stop, **changes)  # type: ignore[arg-type]
    assert "323e4567" not in repr(admission)
    assert "2026" not in repr(stop)
