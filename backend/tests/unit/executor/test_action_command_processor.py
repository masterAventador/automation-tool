from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, cast
from uuid import UUID

import pytest

from automation_tool.executor.action_operation import DouyinActionOperation
from automation_tool.executor.command_processor import (
    ExecutorCommandProcessor,
    ExecutorCommandRejected,
)
from automation_tool.executor.ledger import AttemptCheckpointState, ExecutorLedger
from automation_tool.executor.rpa.douyin.action_result import DouyinActionResultFact
from automation_tool.protocol import (
    ActionResultEvidence,
    ProtocolActionId,
    ProtocolTargetId,
    TaskCommandResultEnvelope,
    TaskEventEnvelope,
)

NOW = datetime(2026, 7, 21, 9, 0, tzinfo=UTC)
INSTALLATION_ID = "123e4567-e89b-42d3-a456-426614174003"
EXECUTOR_ID = "123e4567-e89b-42d3-a456-426614174004"
TASK_ID = "123e4567-e89b-42d3-a456-426614174005"
ATTEMPT_ID = "123e4567-e89b-42d3-a456-426614174006"
ACTION_ID = "223e4567-e89b-42d3-a456-426614174001"
TARGET_ID = "223e4567-e89b-42d3-a456-426614174002"
AUTHORITY = "ataa1.Y2Fub25pY2Fs.c2lnbmF0dXJl"
PRIVATE_TEMPLATE = "您好 {{target_display_name}}, 这是不应落盘的评论"
PRIVATE_DISPLAY_NAME = "不应落盘的目标名称"


@dataclass
class Clock:
    value: datetime = NOW

    def now(self) -> datetime:
        return self.value


class DeterministicIds:
    def __init__(self) -> None:
        self._value = 0

    def __call__(self) -> UUID:
        self._value += 1
        return UUID(f"923e4567-e89b-42d3-a456-{self._value:012d}")


@dataclass
class RecordingActionOperation:
    fact: DouyinActionResultFact
    calls: int = 0

    def run(self, command: object) -> DouyinActionResultFact:
        del command
        self.calls += 1
        return self.fact


def offer() -> str:
    return json.dumps(
        {
            "protocol_version": "1.0",
            "message_id": "323e4567-e89b-42d3-a456-426614174001",
            "message_type": "task.offer",
            "sent_at": NOW.isoformat().replace("+00:00", "Z"),
            "deadline_at": (NOW + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
            "installation_id": INSTALLATION_ID,
            "executor_id": EXECUTOR_ID,
            "correlation_id": "323e4567-e89b-42d3-a456-426614174002",
            "idempotency_key": f"task:offer:{ATTEMPT_ID}",
            "sequence": 1,
            "payload": {},
            "task_id": TASK_ID,
            "execution_attempt_id": ATTEMPT_ID,
        },
        separators=(",", ":"),
    )


def action_command(
    *,
    message_id: str = "423e4567-e89b-42d3-a456-426614174001",
) -> str:
    return json.dumps(
        {
            "protocol_version": "1.0",
            "message_id": message_id,
            "message_type": "action.execute",
            "sent_at": NOW.isoformat().replace("+00:00", "Z"),
            "deadline_at": (NOW + timedelta(minutes=4)).isoformat().replace("+00:00", "Z"),
            "installation_id": INSTALLATION_ID,
            "executor_id": EXECUTOR_ID,
            "correlation_id": "423e4567-e89b-42d3-a456-426614174002",
            "idempotency_key": f"action:{ACTION_ID}",
            "sequence": 2,
            "payload": {
                "action_version": "douyin.action-command.v1",
                "action_id": ACTION_ID,
                "target_id": TARGET_ID,
                "action": "comment",
                "signed_authority": AUTHORITY,
                "platform_target_id": "douyin-user-1",
                "display_name": PRIVATE_DISPLAY_NAME,
                "public_handle": "target-one",
                "source": "general_search_author",
                "page_revision": 1,
                "message_template_version": "action-message-template.v1",
                "message_template": PRIVATE_TEMPLATE,
            },
            "task_id": TASK_ID,
            "execution_attempt_id": ATTEMPT_ID,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def processor(
    state_directory: Path,
    operation: RecordingActionOperation,
) -> ExecutorCommandProcessor:
    return ExecutorCommandProcessor(
        ledger=ExecutorLedger(
            state_directory=state_directory,
            installation_id=INSTALLATION_ID,
            executor_id=EXECUTOR_ID,
        ),
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
        clock=Clock(),
        id_source=DeterministicIds(),
        action_operation=cast(DouyinActionOperation, operation),
    )


def result_fact(
    *,
    message_type: Literal["step.completed", "step.failed", "task.outcome_uncertain"] = (
        "step.completed"
    ),
    evidence: ActionResultEvidence = ActionResultEvidence.COMMENT_CONFIRMED,
) -> DouyinActionResultFact:
    return DouyinActionResultFact(
        action_id=ProtocolActionId(ACTION_ID),
        target_id=ProtocolTargetId(TARGET_ID),
        message_type=message_type,
        evidence=evidence,
    )


def test_action_processor_starts_the_offer_then_executes_and_replays_one_real_action(
    tmp_path: Path,
) -> None:
    operation = RecordingActionOperation(result_fact())
    active = processor(tmp_path / "state", operation)

    offered = active.handle(offer())
    assert [message.message_type for message in offered] == ["task.accept", "task.started"]
    assert isinstance(offered[0], TaskCommandResultEnvelope)
    assert isinstance(offered[1], TaskEventEnvelope)
    checkpoint = active.ledger.get_checkpoint(ATTEMPT_ID)
    assert checkpoint is not None
    assert checkpoint.state is AttemptCheckpointState.RUNNING
    assert checkpoint.last_event_sequence == 1

    executed = active.handle(action_command())
    assert [message.message_type for message in executed] == [
        "action.accept",
        "step.started",
        "step.completed",
    ]
    assert executed[0].payload == {"accepted": True}
    assert executed[1].payload == {"action_id": ACTION_ID}
    assert executed[2].payload == {
        "action_id": ACTION_ID,
        "evidence": "comment_confirmed",
        "evidence_version": "action-result-evidence.v1",
    }
    assert [message.sequence for message in executed] == [2, 2, 3]
    assert operation.calls == 1

    reopened = processor(tmp_path / "state", operation)
    assert reopened.handle(action_command()) == executed
    assert operation.calls == 1


def test_action_command_storage_omits_authority_message_and_candidate_labels(
    tmp_path: Path,
) -> None:
    operation = RecordingActionOperation(result_fact())
    active = processor(tmp_path / "private-state", operation)
    active.handle(offer())
    active.handle(action_command())

    database = active.ledger.database_path.read_bytes()
    assert AUTHORITY.encode() not in database
    assert PRIVATE_TEMPLATE.encode() not in database
    assert PRIVATE_DISPLAY_NAME.encode() not in database


@pytest.mark.parametrize(
    ("fact", "expected_type", "expected_state"),
    (
        (
            result_fact(
                message_type="step.failed",
                evidence=ActionResultEvidence.LOGIN_REQUIRED,
            ),
            "step.failed",
            AttemptCheckpointState.RUNNING,
        ),
        (
            result_fact(
                message_type="task.outcome_uncertain",
                evidence=ActionResultEvidence.DISPATCH_TIMED_OUT,
            ),
            "task.outcome_uncertain",
            AttemptCheckpointState.OUTCOME_UNCERTAIN,
        ),
    ),
)
def test_failed_and_uncertain_action_facts_never_collapse_to_success(
    tmp_path: Path,
    fact: DouyinActionResultFact,
    expected_type: str,
    expected_state: AttemptCheckpointState,
) -> None:
    active = processor(tmp_path / expected_type, RecordingActionOperation(fact))
    active.handle(offer())

    batch = active.handle(action_command())

    assert [message.message_type for message in batch] == [
        "action.accept",
        "step.started",
        expected_type,
    ]
    checkpoint = active.ledger.get_checkpoint(ATTEMPT_ID)
    assert checkpoint is not None and checkpoint.state is expected_state


def test_action_processor_rejects_invalid_operation_without_reflecting_it(tmp_path: Path) -> None:
    ledger = ExecutorLedger(
        state_directory=tmp_path / "invalid-operation",
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
    )
    with pytest.raises(ExecutorCommandRejected):
        ExecutorCommandProcessor(
            ledger=ledger,
            installation_id=INSTALLATION_ID,
            executor_id=EXECUTOR_ID,
            action_operation=cast(DouyinActionOperation, object()),
        )


def test_action_processor_requires_operation_and_running_attempt(tmp_path: Path) -> None:
    missing_operation = ExecutorCommandProcessor(
        ledger=ExecutorLedger(
            state_directory=tmp_path / "missing-operation",
            installation_id=INSTALLATION_ID,
            executor_id=EXECUTOR_ID,
        ),
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
        clock=Clock(),
        id_source=DeterministicIds(),
    )
    missing_operation.handle(offer())
    with pytest.raises(ExecutorCommandRejected):
        missing_operation.handle(action_command())

    operation = RecordingActionOperation(result_fact())
    inactive = processor(tmp_path / "paused-attempt", operation)
    inactive.handle(offer())
    checkpoint = inactive.ledger.get_checkpoint(ATTEMPT_ID)
    assert checkpoint is not None
    inactive.ledger.compare_and_set_checkpoint(
        attempt_id=ATTEMPT_ID,
        expected_revision=checkpoint.revision,
        state=AttemptCheckpointState.PAUSED,
        last_event_sequence=checkpoint.last_event_sequence,
    )
    with pytest.raises(ExecutorCommandRejected):
        inactive.handle(action_command())
    assert operation.calls == 0


@pytest.mark.parametrize(
    "invalid_fact",
    (
        cast(DouyinActionResultFact, object()),
        DouyinActionResultFact(
            action_id=ProtocolActionId("223e4567-e89b-42d3-a456-426614174099"),
            target_id=ProtocolTargetId(TARGET_ID),
            message_type="step.completed",
            evidence=ActionResultEvidence.COMMENT_CONFIRMED,
        ),
        DouyinActionResultFact(
            action_id=ProtocolActionId(ACTION_ID),
            target_id=ProtocolTargetId("223e4567-e89b-42d3-a456-426614174099"),
            message_type="step.completed",
            evidence=ActionResultEvidence.COMMENT_CONFIRMED,
        ),
    ),
)
def test_action_processor_rejects_unbound_operation_fact(
    tmp_path: Path,
    invalid_fact: DouyinActionResultFact,
) -> None:
    active = processor(tmp_path / str(id(invalid_fact)), RecordingActionOperation(invalid_fact))
    active.handle(offer())

    with pytest.raises(ExecutorCommandRejected):
        active.handle(action_command())
    assert active.ledger.outbox_for_command("423e4567-e89b-42d3-a456-426614174001") == ()


def test_second_offer_cannot_overwrite_a_running_attempt(tmp_path: Path) -> None:
    active = processor(tmp_path / "second-offer", RecordingActionOperation(result_fact()))
    active.handle(offer())
    repeated = json.loads(offer())
    repeated.update(
        {
            "message_id": "323e4567-e89b-42d3-a456-426614174099",
            "idempotency_key": f"task:offer:{ATTEMPT_ID}:second",
            "sequence": 2,
        }
    )

    with pytest.raises(ExecutorCommandRejected):
        active.handle(json.dumps(repeated, separators=(",", ":")))
