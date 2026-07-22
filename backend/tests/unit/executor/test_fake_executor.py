from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import count
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from automation_tool.executor.fake import (
    FakeExecutorEngine,
    FakeExecutorRejected,
    FakeExecutorScenario,
)
from automation_tool.protocol import (
    ExecutorLifecycleEnvelope,
    TaskCommandResultEnvelope,
    TaskEventEnvelope,
)

NOW = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)
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
    def __init__(self) -> None:
        self._values = count(1)

    def __call__(self) -> UUID:
        return UUID(f"923e4567-e89b-42d3-a456-{next(self._values):012d}")


class RecoveringIds:
    def __init__(self, *, fail_at: int) -> None:
        self._values = count(1)
        self._fail_at = fail_at

    def __call__(self) -> UUID:
        value = next(self._values)
        if value == self._fail_at:
            raise RuntimeError("private id source failure")
        return UUID(f"923e4567-e89b-42d3-a456-{value:012d}")


class InvalidClock:
    def __init__(self, value: object, *, raises: bool = False) -> None:
        self._value = value
        self._raises = raises

    def now(self) -> datetime:
        if self._raises:
            raise RuntimeError("private clock failure")
        return cast(datetime, self._value)


def command(
    message_type: str,
    *,
    sequence: int,
    message_id: str | None = None,
    idempotency_key: str | None = None,
    correlation_id: str = "323e4567-e89b-42d3-a456-426614174002",
    task_id: str = TASK_ID,
    attempt_id: str = ATTEMPT_ID,
    installation_id: str = INSTALLATION_ID,
    executor_id: str = EXECUTOR_ID,
    deadline_at: datetime = NOW + timedelta(minutes=5),
) -> str:
    source = {
        "protocol_version": "1.0",
        "message_id": message_id or f"323e4567-e89b-42d3-a456-{sequence:012d}",
        "message_type": message_type,
        "sent_at": NOW.isoformat().replace("+00:00", "Z"),
        "deadline_at": deadline_at.isoformat().replace("+00:00", "Z"),
        "installation_id": installation_id,
        "executor_id": executor_id,
        "correlation_id": correlation_id,
        "idempotency_key": idempotency_key or f"task:fake:{message_type}:{sequence}",
        "sequence": sequence,
        "payload": (
            {"task_event_sequence_baseline": 0}
            if message_type == "task.offer"
            else {}
        ),
        "task_id": task_id,
        "execution_attempt_id": attempt_id,
    }
    return json.dumps(source, separators=(",", ":"))


def engine(scenario: FakeExecutorScenario) -> FakeExecutorEngine:
    return FakeExecutorEngine(
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
        scenario=scenario,
        clock=MutableClock(),
        id_source=DeterministicIds(),
    )


def test_constructor_identity_properties_and_system_clock_fail_closed() -> None:
    fake = engine(FakeExecutorScenario.REJECT)
    assert fake.installation_id == INSTALLATION_ID
    assert fake.executor_id == EXECUTOR_ID

    default_clock = FakeExecutorEngine(
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
        scenario=FakeExecutorScenario.REJECT,
    )
    assert default_clock.build_hello().sent_at.utcoffset() == timedelta(0)

    for invalid_installation_id in (
        cast(str, object()),
        "not-a-uuid",
        str(UUID(int=4)),
    ):
        with pytest.raises(FakeExecutorRejected):
            FakeExecutorEngine(
                installation_id=invalid_installation_id,
                executor_id=EXECUTOR_ID,
                scenario=FakeExecutorScenario.REJECT,
            )
    with pytest.raises(FakeExecutorRejected):
        FakeExecutorEngine(
            installation_id=INSTALLATION_ID,
            executor_id=EXECUTOR_ID,
            scenario=cast(FakeExecutorScenario, "reject"),
        )
    with pytest.raises(FakeExecutorRejected):
        FakeExecutorEngine(
            installation_id=INSTALLATION_ID,
            executor_id=EXECUTOR_ID,
            scenario=FakeExecutorScenario.REJECT,
            id_source=cast(Callable[[], object], None),
        )


def test_invalid_clock_generated_ids_and_hello_validation_fail_closed() -> None:
    invalid_clocks = (
        InvalidClock(object()),
        InvalidClock(datetime(2026, 7, 18, 10, 0)),
        InvalidClock(NOW, raises=True),
    )
    for clock in invalid_clocks:
        fake = FakeExecutorEngine(
            installation_id=INSTALLATION_ID,
            executor_id=EXECUTOR_ID,
            scenario=FakeExecutorScenario.REJECT,
            clock=clock,
        )
        with pytest.raises(FakeExecutorRejected):
            fake.build_hello()

    invalid_id = FakeExecutorEngine(
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
        scenario=FakeExecutorScenario.REJECT,
        clock=MutableClock(),
        id_source=lambda: object(),
    )
    with pytest.raises(FakeExecutorRejected):
        invalid_id.build_hello()

    with pytest.raises(FakeExecutorRejected):
        engine(FakeExecutorScenario.REJECT).build_hello(sequence=9_007_199_254_740_992)


def test_parser_lifecycle_and_identity_mismatches_fail_closed() -> None:
    fake = engine(FakeExecutorScenario.REJECT)
    lifecycle = fake.build_hello().model_dump_json()
    invalid_sources = (
        "{}",
        lifecycle,
        command(
            "task.offer",
            sequence=1,
            installation_id="123e4567-e89b-42d3-a456-426614174099",
        ),
        command(
            "task.offer",
            sequence=1,
            executor_id="123e4567-e89b-42d3-a456-426614174099",
        ),
    )
    for source in invalid_sources:
        with pytest.raises(FakeExecutorRejected):
            fake.handle(source)


def test_scenarios_emit_only_formal_results_and_cover_every_task_event() -> None:
    observed_events: set[str] = set()
    observed_results: set[str] = set()

    for scenario in FakeExecutorScenario:
        if scenario is FakeExecutorScenario.HOLD:
            continue
        batch = engine(scenario).handle(command("task.offer", sequence=1))
        for message in batch:
            assert isinstance(message, (TaskCommandResultEnvelope, TaskEventEnvelope))
            if isinstance(message, TaskCommandResultEnvelope):
                observed_results.add(message.message_type)
            else:
                observed_events.add(message.message_type)
            assert str(message.installation_id) == INSTALLATION_ID
            assert str(message.executor_id) == EXECUTOR_ID
            assert str(message.task_id) == TASK_ID
            assert str(message.execution_attempt_id) == ATTEMPT_ID

    assert observed_results == {"task.accept", "task.reject"}
    assert observed_events >= {
        "task.started",
        "step.started",
        "step.progress",
        "step.completed",
        "step.failed",
        "session.login_required",
        "handoff.requested",
        "task.completed",
        "task.partially_completed",
        "task.failed",
        "task.outcome_uncertain",
    }


def test_hold_scenario_replays_pause_resume_cancel_and_emergency_events() -> None:
    fake = engine(FakeExecutorScenario.HOLD)
    offered = fake.handle(command("task.offer", sequence=1))
    paused = fake.handle(command("task.pause", sequence=2))
    resumed = fake.handle(command("task.resume", sequence=3))
    cancelled = fake.handle(command("task.cancel", sequence=4))

    assert [message.message_type for message in offered] == [
        "task.accept",
        "task.started",
        "step.started",
    ]
    assert [message.message_type for message in paused] == [
        "task.control_ack",
        "task.paused",
    ]
    assert [message.message_type for message in resumed] == [
        "task.control_ack",
        "task.resumed",
    ]
    assert [message.message_type for message in cancelled] == [
        "task.control_ack",
        "task.cancelled",
    ]

    emergency = engine(FakeExecutorScenario.HOLD)
    emergency.handle(command("task.offer", sequence=1))
    stopped = emergency.handle(command("task.emergency_stop", sequence=2))
    assert [message.message_type for message in stopped] == [
        "task.control_ack",
        "task.outcome_uncertain",
    ]


def test_command_replay_returns_identical_messages_without_duplicate_events() -> None:
    fake = engine(FakeExecutorScenario.SUCCEED)
    source = command("task.offer", sequence=1)

    first = fake.handle(source)
    replay = fake.handle(source)
    same_intent_new_message = fake.handle(
        command(
            "task.offer",
            sequence=1,
            message_id="423e4567-e89b-42d3-a456-426614174001",
            idempotency_key="task:fake:task.offer:1",
        )
    )

    assert replay == first
    assert same_intent_new_message == first
    event_sequences = [
        message.sequence for message in first if isinstance(message, TaskEventEnvelope)
    ]
    assert event_sequences == list(range(1, len(event_sequences) + 1))

    with pytest.raises(FakeExecutorRejected):
        fake.handle(
            command(
                "task.offer",
                sequence=1,
                message_id="323e4567-e89b-42d3-a456-000000000001",
                idempotency_key="different-intent",
            )
        )
    with pytest.raises(FakeExecutorRejected):
        fake.handle(
            command(
                "task.offer",
                sequence=1,
                message_id="423e4567-e89b-42d3-a456-426614174002",
                idempotency_key="task:fake:task.offer:1",
                correlation_id="423e4567-e89b-42d3-a456-426614174003",
            )
        )


def test_message_generation_failure_rolls_back_state_and_event_sequence() -> None:
    fake = FakeExecutorEngine(
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
        scenario=FakeExecutorScenario.SUCCEED,
        clock=MutableClock(),
        id_source=RecoveringIds(fail_at=3),
    )

    with pytest.raises(
        FakeExecutorRejected,
        match=r"^Fake Executor command is rejected$",
    ):
        fake.handle(command("task.offer", sequence=1))

    retried = fake.handle(command("task.offer", sequence=1))
    event_sequences = [
        message.sequence for message in retried if isinstance(message, TaskEventEnvelope)
    ]
    assert event_sequences == [1, 2, 3, 4, 5]


def test_hello_is_a_formal_lifecycle_envelope_and_rejects_invalid_sequence() -> None:
    fake = engine(FakeExecutorScenario.REJECT)

    hello = fake.build_hello(sequence=2)

    assert isinstance(hello, ExecutorLifecycleEnvelope)
    assert hello.message_type == "executor.hello"
    assert hello.sequence == 2
    assert str(hello.installation_id) == INSTALLATION_ID
    assert str(hello.executor_id) == EXECUTOR_ID
    assert hello.payload == {
        "architecture": "arm64",
        "executor_version": "0.1.0",
        "platform": "macos",
    }
    for invalid in (0, -1, True):
        with pytest.raises(FakeExecutorRejected):
            fake.build_hello(sequence=invalid)


def test_identity_deadline_order_and_command_state_fail_closed() -> None:
    invalid_sources = (
        command("task.offer", sequence=1, executor_id=str(UUID(int=4))),
        command("task.offer", sequence=1, deadline_at=NOW),
        command("task.pause", sequence=1),
    )
    for source in invalid_sources:
        with pytest.raises(FakeExecutorRejected, match=r"^Fake Executor command is rejected$"):
            engine(FakeExecutorScenario.HOLD).handle(source)

    expired = FakeExecutorEngine(
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
        scenario=FakeExecutorScenario.HOLD,
        clock=MutableClock(NOW + timedelta(minutes=2)),
    )
    with pytest.raises(FakeExecutorRejected):
        expired.handle(
            command(
                "task.offer",
                sequence=1,
                deadline_at=NOW + timedelta(minutes=1),
            )
        )

    fake = engine(FakeExecutorScenario.HOLD)
    fake.handle(command("task.offer", sequence=1))
    with pytest.raises(FakeExecutorRejected):
        fake.handle(command("task.resume", sequence=2))
    with pytest.raises(FakeExecutorRejected):
        fake.handle(command("task.pause", sequence=3))
    with pytest.raises(FakeExecutorRejected):
        fake.handle(command("task.offer", sequence=2))

    different_task = engine(FakeExecutorScenario.HOLD)
    different_task.handle(command("task.offer", sequence=1))
    with pytest.raises(FakeExecutorRejected):
        different_task.handle(
            command(
                "task.pause",
                sequence=2,
                task_id="123e4567-e89b-42d3-a456-426614174099",
            )
        )

    with pytest.raises(FakeExecutorRejected):
        engine(FakeExecutorScenario.HOLD).handle(command("task.cancel", sequence=1))

    awaiting = engine(FakeExecutorScenario.LOGIN_REQUIRED)
    awaiting.handle(command("task.offer", sequence=1))
    assert [
        message.message_type for message in awaiting.handle(command("task.cancel", sequence=2))
    ] == ["task.control_ack", "task.cancelled"]


def test_result_generation_failure_is_safe_and_retryable() -> None:
    fake = FakeExecutorEngine(
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
        scenario=FakeExecutorScenario.REJECT,
        clock=MutableClock(),
        id_source=RecoveringIds(fail_at=1),
    )

    with pytest.raises(FakeExecutorRejected):
        fake.handle(command("task.offer", sequence=1))

    assert [message.message_type for message in fake.handle(command("task.offer", sequence=1))] == [
        "task.reject"
    ]


def test_fake_executor_has_no_control_plane_rpa_or_side_effect_dependency() -> None:
    source = Path(__file__).parents[3] / "src/automation_tool/executor/fake.py"
    text = source.read_text(encoding="utf-8")

    assert "automation_tool.control_plane" not in text
    assert "playwright" not in text.lower()
    assert "subprocess" not in text
    assert "open(" not in text
    assert "Path(" not in text
    assert "sqlite" not in text.lower()
