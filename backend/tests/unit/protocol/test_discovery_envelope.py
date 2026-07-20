from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from automation_tool.protocol import (
    DOUYIN_DISCOVERY_PROTOCOL_VERSION,
    MAX_DISCOVERY_BATCH_CANDIDATES,
    DouyinDiscoveryBatchPayload,
    DouyinDiscoveryCommandPayload,
    DouyinDiscoveryCompletedPayload,
    TaskDiscoveryBatchEnvelope,
    TaskDiscoveryCommandEnvelope,
    TaskDiscoveryCompletedEnvelope,
    parse_executor_message,
)

NOW = datetime(2026, 7, 19, 15, 0, tzinfo=UTC)
MESSAGE_ID = "123e4567-e89b-42d3-a456-426614174001"
CORRELATION_ID = "123e4567-e89b-42d3-a456-426614174002"
INSTALLATION_ID = "123e4567-e89b-42d3-a456-426614174003"
EXECUTOR_ID = "123e4567-e89b-42d3-a456-426614174004"
TASK_ID = "123e4567-e89b-42d3-a456-426614174005"
ATTEMPT_ID = "123e4567-e89b-42d3-a456-426614174006"


def envelope(message_type: str, payload: dict[str, object], **changes: object) -> dict[str, object]:
    source: dict[str, object] = {
        "protocol_version": "1.0",
        "message_id": MESSAGE_ID,
        "message_type": message_type,
        "sent_at": NOW.isoformat().replace("+00:00", "Z"),
        "deadline_at": (NOW + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        "installation_id": INSTALLATION_ID,
        "executor_id": EXECUTOR_ID,
        "correlation_id": CORRELATION_ID,
        "idempotency_key": f"discovery:{message_type}:1",
        "sequence": 1,
        "payload": payload,
        "task_id": TASK_ID,
        "execution_attempt_id": ATTEMPT_ID,
    }
    source.update(changes)
    return source


def candidate(index: int, *, page_revision: int = 7) -> dict[str, object]:
    return {
        "candidate_version": "douyin.candidate.v1",
        "platform_target_id": f"author-{index}",
        "display_name": f"目标 {index}",
        "public_handle": f"target_{index}",
        "source": "general_search_author",
        "page_revision": page_revision,
    }


def command_payload(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "discovery_version": DOUYIN_DISCOVERY_PROTOCOL_VERSION,
        "keyword": "自动化运营",
        "target_limit": 100,
        "page_revision": 7,
    }
    payload.update(changes)
    return payload


def batch_payload(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "discovery_version": DOUYIN_DISCOVERY_PROTOCOL_VERSION,
        "page_revision": 7,
        "batch_index": 1,
        "batch_count": 2,
        "candidates": [candidate(1), candidate(2)],
    }
    payload.update(changes)
    return payload


def completed_payload(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "discovery_version": DOUYIN_DISCOVERY_PROTOCOL_VERSION,
        "outcome": "completed",
        "evidence": "candidates_extracted",
        "page_revision": 7,
        "batch_count": 2,
        "candidate_count": 12,
    }
    payload.update(changes)
    return payload


def test_discover_command_and_chunked_results_use_distinct_strict_envelopes() -> None:
    command = parse_executor_message(json.dumps(envelope("task.discover", command_payload())))
    batch = parse_executor_message(json.dumps(envelope("task.discovery_batch", batch_payload())))
    completed = parse_executor_message(
        json.dumps(envelope("task.discovery_completed", completed_payload()))
    )

    assert isinstance(command, TaskDiscoveryCommandEnvelope)
    assert isinstance(command.payload, DouyinDiscoveryCommandPayload)
    assert command.payload.to_search_input().target_limit == 100
    assert isinstance(batch, TaskDiscoveryBatchEnvelope)
    assert isinstance(batch.payload, DouyinDiscoveryBatchPayload)
    assert next(item.to_candidate() for item in batch.payload.candidates).platform_target_id == (
        "author-1"
    )
    assert isinstance(completed, TaskDiscoveryCompletedEnvelope)
    assert isinstance(completed.payload, DouyinDiscoveryCompletedPayload)


def test_discovery_batches_cover_one_hundred_candidates_without_oversized_messages() -> None:
    encoded_sizes: list[int] = []
    for batch_index in range(1, 11):
        candidates = [
            candidate((batch_index - 1) * MAX_DISCOVERY_BATCH_CANDIDATES + offset)
            for offset in range(1, MAX_DISCOVERY_BATCH_CANDIDATES + 1)
        ]
        document = envelope(
            "task.discovery_batch",
            batch_payload(
                batch_index=batch_index,
                batch_count=10,
                candidates=candidates,
            ),
            message_id=f"123e4567-e89b-42d3-a456-{batch_index:012d}",
            idempotency_key=f"discovery:batch:{batch_index}",
            sequence=batch_index,
        )
        parsed = parse_executor_message(json.dumps(document, ensure_ascii=False))
        assert isinstance(parsed, TaskDiscoveryBatchEnvelope)
        encoded_sizes.append(len(parsed.model_dump_json().encode("utf-8")))

    candidate_count = 0
    for index in range(1, 11):
        parsed = parse_executor_message(
            json.dumps(
                envelope(
                    "task.discovery_batch",
                    batch_payload(batch_index=index, batch_count=10, candidates=[candidate(index)]),
                    message_id=f"123e4567-e89b-42d3-a456-{index + 20:012d}",
                    idempotency_key=f"discovery:count:{index}",
                )
            )
        )
        assert isinstance(parsed, TaskDiscoveryBatchEnvelope)
        candidate_count += len(parsed.payload.candidates)
    assert candidate_count == 10
    assert max(encoded_sizes) < 16 * 1024


@pytest.mark.parametrize(
    "payload",
    (
        command_payload(discovery_version="future"),
        command_payload(keyword=" private\nvalue"),
        command_payload(target_limit=101),
        command_payload(page_revision=0),
    ),
)
def test_discovery_command_payload_fails_closed(payload: dict[str, object]) -> None:
    with pytest.raises((ValidationError, ValueError)):
        TaskDiscoveryCommandEnvelope.model_validate(envelope("task.discover", payload))


@pytest.mark.parametrize(
    "payload",
    (
        batch_payload(candidates=[]),
        batch_payload(candidates=[candidate(index) for index in range(11)]),
        batch_payload(batch_index=3, batch_count=2),
        batch_payload(candidates=[candidate(1, page_revision=8)]),
        batch_payload(candidates=[{**candidate(1), "absolute_url": "https://example.test"}]),
        batch_payload(candidates=[{**candidate(1), "display_name": "cookie=private"}]),
    ),
)
def test_discovery_batch_payload_fails_closed(payload: dict[str, object]) -> None:
    with pytest.raises((ValidationError, ValueError)):
        TaskDiscoveryBatchEnvelope.model_validate(envelope("task.discovery_batch", payload))


@pytest.mark.parametrize(
    "payload",
    (
        completed_payload(candidate_count=0),
        completed_payload(batch_count=1, candidate_count=12),
        completed_payload(outcome="login_required", evidence="login_required"),
        completed_payload(outcome="failed", evidence="page_unavailable"),
        completed_payload(outcome="completed", evidence="page_unavailable"),
    ),
)
def test_discovery_completion_requires_exact_outcome_count_and_evidence_shape(
    payload: dict[str, object],
) -> None:
    if payload["outcome"] != "completed":
        payload = {**payload, "batch_count": 0, "candidate_count": 0}
        TaskDiscoveryCompletedEnvelope.model_validate(envelope("task.discovery_completed", payload))
        return
    with pytest.raises((ValidationError, ValueError)):
        TaskDiscoveryCompletedEnvelope.model_validate(envelope("task.discovery_completed", payload))


def test_non_success_completion_is_zero_candidate_and_uses_closed_evidence() -> None:
    for outcome, evidence in (
        ("login_required", "login_required"),
        ("handoff_required", "blocking_dialog"),
        ("failed", "page_unavailable"),
    ):
        parsed = TaskDiscoveryCompletedEnvelope.model_validate(
            envelope(
                "task.discovery_completed",
                completed_payload(
                    outcome=outcome,
                    evidence=evidence,
                    batch_count=0,
                    candidate_count=0,
                ),
            )
        )
        assert parsed.payload.outcome == outcome

    with pytest.raises(ValidationError):
        TaskDiscoveryCompletedEnvelope.model_validate(
            envelope(
                "task.discovery_completed",
                completed_payload(
                    outcome="failed",
                    evidence="private backend detail",
                    batch_count=0,
                    candidate_count=0,
                ),
            )
        )

    with pytest.raises(ValidationError):
        TaskDiscoveryCompletedEnvelope.model_validate(
            envelope(
                "task.discovery_completed",
                completed_payload(
                    outcome="login_required",
                    evidence="login_required",
                    batch_count=1,
                    candidate_count=1,
                ),
            )
        )
