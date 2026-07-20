from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from itertools import count
from pathlib import Path
from uuid import UUID

from automation_tool.executor.command_processor import ExecutorCommandProcessor
from automation_tool.executor.discovery_operation import (
    DouyinDiscoveryExecutionResult,
    DouyinDiscoveryOperationState,
)
from automation_tool.executor.ledger import AttemptCheckpointState, ExecutorLedger
from automation_tool.protocol import (
    DouyinCandidate,
    DouyinCandidateSource,
    DouyinCandidateSummary,
    TaskCommandResultEnvelope,
    TaskDiscoveryBatchEnvelope,
    TaskDiscoveryCompletedEnvelope,
)

NOW = datetime(2026, 7, 19, 15, 30, tzinfo=UTC)
INSTALLATION_ID = "123e4567-e89b-42d3-a456-426614174003"
EXECUTOR_ID = "123e4567-e89b-42d3-a456-426614174004"
TASK_ID = "123e4567-e89b-42d3-a456-426614174005"
ATTEMPT_ID = "123e4567-e89b-42d3-a456-426614174006"
MESSAGE_ID = "323e4567-e89b-42d3-a456-426614174001"


class Clock:
    @staticmethod
    def now() -> datetime:
        return NOW


class Ids:
    def __init__(self) -> None:
        self._values = count(1)

    def __call__(self) -> UUID:
        return UUID(f"923e4567-e89b-42d3-a456-{next(self._values):012d}")


class Discovery:
    def __init__(self, result: DouyinDiscoveryExecutionResult) -> None:
        self.result = result
        self.calls: list[tuple[object, Callable[[], bool]]] = []

    def run(
        self,
        payload: object,
        *,
        cancellation_requested: Callable[[], bool],
    ) -> DouyinDiscoveryExecutionResult:
        self.calls.append((payload, cancellation_requested))
        return self.result


def candidate(index: int, *, page_revision: int = 7) -> DouyinCandidate:
    return DouyinCandidate(
        platform_target_id=f"author-{index}",
        summary=DouyinCandidateSummary(
            display_name=f"目标 {index}",
            public_handle=f"target_{index}",
        ),
        source=DouyinCandidateSource.GENERAL_SEARCH_AUTHOR,
        page_revision=page_revision,
    )


def completed(count_value: int = 23) -> DouyinDiscoveryExecutionResult:
    return DouyinDiscoveryExecutionResult(
        state=DouyinDiscoveryOperationState.COMPLETED,
        evidence="candidates_extracted",
        page_revision=7,
        candidates=tuple(candidate(index) for index in range(1, count_value + 1)),
    )


def command(**changes: object) -> str:
    document: dict[str, object] = {
        "protocol_version": "1.0",
        "message_id": MESSAGE_ID,
        "message_type": "task.discover",
        "sent_at": NOW.isoformat().replace("+00:00", "Z"),
        "deadline_at": (NOW + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        "installation_id": INSTALLATION_ID,
        "executor_id": EXECUTOR_ID,
        "correlation_id": "323e4567-e89b-42d3-a456-426614174002",
        "idempotency_key": "task:discover:attempt:1",
        "sequence": 1,
        "payload": {
            "discovery_version": "douyin.discovery.v1",
            "keyword": "自动化运营",
            "target_limit": 23,
            "page_revision": 7,
        },
        "task_id": TASK_ID,
        "execution_attempt_id": ATTEMPT_ID,
    }
    document.update(changes)
    return json.dumps(document, ensure_ascii=False, separators=(",", ":"))


def processor(path: Path, discovery: Discovery) -> ExecutorCommandProcessor:
    return ExecutorCommandProcessor(
        ledger=ExecutorLedger(
            state_directory=path,
            installation_id=INSTALLATION_ID,
            executor_id=EXECUTOR_ID,
        ),
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
        clock=Clock(),
        id_source=Ids(),
        discovery_operation=discovery,
    )


def test_discover_command_runs_once_chunks_candidates_and_replays_durable_outbox(
    tmp_path: Path,
) -> None:
    discovery = Discovery(completed())
    active = processor(tmp_path / "state", discovery)

    batch = active.handle(command())

    assert isinstance(batch[0], TaskCommandResultEnvelope)
    assert batch[0].message_type == "task.accept"
    chunks = tuple(
        message for message in batch[1:-1] if isinstance(message, TaskDiscoveryBatchEnvelope)
    )
    assert len(chunks) == 3
    assert [len(message.payload.candidates) for message in chunks] == [10, 10, 3]
    assert [message.payload.batch_index for message in chunks] == [1, 2, 3]
    assert [message.sequence for message in batch] == [1, 1, 2, 3, 4]
    final = batch[-1]
    assert isinstance(final, TaskDiscoveryCompletedEnvelope)
    assert final.payload.outcome == "completed"
    assert final.payload.candidate_count == 23
    assert final.payload.batch_count == 3
    assert len(discovery.calls) == 1
    checkpoint = active.ledger.get_checkpoint(ATTEMPT_ID)
    assert checkpoint is not None
    assert checkpoint.state is AttemptCheckpointState.TERMINAL
    assert checkpoint.last_event_sequence == 4

    replay = processor(tmp_path / "state", Discovery(completed()))
    assert replay.handle(command()) == batch


def test_discover_non_success_emits_no_candidate_batch_and_closed_completion(
    tmp_path: Path,
) -> None:
    for state, evidence, outcome in (
        (DouyinDiscoveryOperationState.LOGIN_REQUIRED, "login_required", "login_required"),
        (DouyinDiscoveryOperationState.HANDOFF_REQUIRED, "blocking_dialog", "handoff_required"),
        (DouyinDiscoveryOperationState.FAILED, "page_unavailable", "failed"),
    ):
        result = DouyinDiscoveryExecutionResult(
            state=state,
            evidence=evidence,
            page_revision=7,
            candidates=(),
        )
        batch = processor(tmp_path / state.value, Discovery(result)).handle(command())

        assert len(batch) == 2
        assert isinstance(batch[1], TaskDiscoveryCompletedEnvelope)
        assert batch[1].payload.outcome == outcome
        assert batch[1].payload.candidate_count == 0
        assert batch[1].payload.batch_count == 0


def test_discover_rejects_missing_operation_or_mismatched_result_revision(
    tmp_path: Path,
) -> None:
    ledger = ExecutorLedger(
        state_directory=tmp_path / "missing",
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
    )
    without_operation = ExecutorCommandProcessor(
        ledger=ledger,
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
        clock=Clock(),
        id_source=Ids(),
    )
    from automation_tool.executor.command_processor import ExecutorCommandRejected

    try:
        without_operation.handle(command())
    except ExecutorCommandRejected:
        pass
    else:  # pragma: no cover - contract assertion
        raise AssertionError("discover command unexpectedly ran without an operation")

    mismatched = DouyinDiscoveryExecutionResult(
        state=DouyinDiscoveryOperationState.COMPLETED,
        evidence="candidates_extracted",
        page_revision=8,
        candidates=(candidate(1, page_revision=8),),
    )
    rejected = processor(tmp_path / "mismatched", Discovery(mismatched))
    try:
        rejected.handle(command())
    except ExecutorCommandRejected:
        pass
    else:  # pragma: no cover - contract assertion
        raise AssertionError("mismatched revision unexpectedly crossed the wire")
