from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from enum import StrEnum

import pytest

from automation_tool.control_plane.domain import (
    MAX_TASK_EVENT_SEQUENCE,
    InvalidTaskEventModel,
    SafeTaskEventMessage,
    TaskEventType,
    TaskEventVersion,
    TaskId,
    TaskSnapshotProjection,
    TaskStatus,
)
from automation_tool.protocol.executor_envelope import MAX_EXECUTOR_SEQUENCE

NOW = datetime(2026, 7, 18, 17, 0, tzinfo=UTC)


def test_task_event_version_types_and_sequence_limit_are_an_exact_contract() -> None:
    assert issubclass(TaskEventType, StrEnum)
    assert tuple(event_type.value for event_type in TaskEventType) == (
        "task.created",
        "task.validation_started",
        "task.validation_failed",
        "task.awaiting_platform_login",
        "task.discovery_started",
        "task.awaiting_confirmation",
        "task.target_selection_updated",
        "task.targets_confirmed",
        "task.started",
        "step.started",
        "step.progress",
        "step.completed",
        "step.failed",
        "task.awaiting_human",
        "task.paused",
        "task.resumed",
        "task.cancelling",
        "task.cancelled",
        "task.completed",
        "task.partially_completed",
        "task.failed",
        "task.outcome_uncertain",
    )
    assert tuple(version.value for version in TaskEventVersion) == ("1.0",)
    assert MAX_TASK_EVENT_SEQUENCE == MAX_EXECUTOR_SEQUENCE == 2**53 - 1


def test_safe_task_event_message_accepts_only_bounded_redacted_single_line_text() -> None:
    message = SafeTaskEventMessage("已暂停, 等待用户确认")
    assert message.value == "已暂停, 等待用户确认"
    assert str(message) == message.value

    rejected = (
        "",
        "x" * 1025,
        "line one\nline two",
        "unsafe\u202evalue",
        "Bearer private-token",
        "cookie=private-value",
        "file:///Users/private/result.png",
        "/Users/private/result.png",
        r"C:\\Users\\private\\result.png",
        "data:image/png;base64,AAAA",
    )
    for value in rejected:
        with pytest.raises(InvalidTaskEventModel) as captured:
            SafeTaskEventMessage(value)
        assert str(captured.value) == "Task event model is invalid"
        if value:
            assert value not in str(captured.value)

    with pytest.raises(InvalidTaskEventModel):
        SafeTaskEventMessage(123)


def test_snapshot_projection_is_strongly_typed_bounded_and_immutable() -> None:
    task_id = TaskId.new()
    projection = TaskSnapshotProjection(
        task_id=task_id,
        status=TaskStatus.RUNNING,
        revision=7,
        last_event_sequence=11,
        updated_at=NOW,
    )
    assert projection.task_id == task_id
    assert projection.last_event_sequence == 11
    with pytest.raises(FrozenInstanceError):
        projection.revision = 8

    invalid_values: tuple[dict[str, object], ...] = (
        {"task_id": "task"},
        {"status": "running"},
        {"revision": True},
        {"revision": 0},
        {"last_event_sequence": True},
        {"last_event_sequence": -1},
        {"last_event_sequence": MAX_TASK_EVENT_SEQUENCE + 1},
        {"updated_at": datetime(2026, 7, 18, 17, 0)},
    )
    baseline: dict[str, object] = {
        "task_id": task_id,
        "status": TaskStatus.RUNNING,
        "revision": 7,
        "last_event_sequence": 11,
        "updated_at": NOW,
    }
    for overrides in invalid_values:
        values = baseline | overrides
        with pytest.raises(InvalidTaskEventModel):
            TaskSnapshotProjection(**values)
