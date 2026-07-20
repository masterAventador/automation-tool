from __future__ import annotations

import base64
import json
from collections.abc import Awaitable, Callable
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from automation_tool.control_plane.application.task_target_previews import (
    InvalidTaskTargetPreview,
    PendingTaskTargetConfirmation,
    PendingTaskTargetExclusions,
    TaskTargetPreviewConflict,
    TaskTargetPreviewItem,
    TaskTargetPreviewMutationResult,
    TaskTargetPreviewNotFound,
    TaskTargetPreviewPage,
    TaskTargetPreviewService,
    TaskTargetPreviewSnapshot,
    TaskTargetPreviewUnavailable,
    _decode_cursor,
)
from automation_tool.control_plane.application.task_targets import TaskTargetRecord
from automation_tool.control_plane.application.tasks import TaskRecord
from automation_tool.control_plane.domain import (
    DOUYIN_CANDIDATE_POLICY_VERSION,
    DouyinCandidateDisposition,
    InstallationId,
    TargetId,
    TaskId,
    TaskStatus,
)
from automation_tool.protocol import (
    DouyinCandidate,
    DouyinCandidateSource,
    DouyinCandidateSummary,
)

NOW = datetime(2026, 7, 20, 1, 0, tzinfo=UTC)


class BrokenTimezone(tzinfo):
    def utcoffset(self, value: datetime | None) -> timedelta | None:
        raise RuntimeError("private timezone failure")

    def dst(self, value: datetime | None) -> timedelta | None:
        return None

    def tzname(self, value: datetime | None) -> str | None:
        return None


def target(disposition: DouyinCandidateDisposition) -> TaskTargetRecord:
    return TaskTargetRecord(
        target_id=TargetId.new(),
        task_id=TaskId.new(),
        installation_id=InstallationId.new(),
        ordinal=1,
        candidate=DouyinCandidate(
            platform_target_id="private-platform-id",
            summary=DouyinCandidateSummary(
                display_name="预览目标",
                public_handle="public_handle",
            ),
            source=DouyinCandidateSource.GENERAL_SEARCH_AUTHOR,
            page_revision=7,
        ),
        disposition=disposition,
        policy_version=DOUYIN_CANDIDATE_POLICY_VERSION,
        evaluated_at=NOW,
        created_at=NOW,
    )


def task(*, status: TaskStatus = TaskStatus.AWAITING_CONFIRMATION) -> TaskRecord:
    value = target(DouyinCandidateDisposition.ELIGIBLE)
    return TaskRecord(
        task_id=value.task_id,
        installation_id=value.installation_id,
        status=status,
        revision=4,
        last_event_sequence=3,
        created_at=NOW,
        updated_at=NOW,
    )


def snapshot(*, status: TaskStatus = TaskStatus.AWAITING_CONFIRMATION) -> TaskTargetPreviewSnapshot:
    task_value = task(status=status)
    target_value = target(DouyinCandidateDisposition.ELIGIBLE)
    target_value = replace(
        target_value,
        task_id=task_value.task_id,
        installation_id=task_value.installation_id,
    )
    return TaskTargetPreviewSnapshot(
        task=task_value,
        page_revision=7,
        items=(TaskTargetPreviewItem(target=target_value),),
        selected_target_count=1,
        user_excluded_target_count=0,
        confirmed_at=NOW if status is not TaskStatus.AWAITING_CONFIRMATION else None,
    )


class MemoryRepository:
    def __init__(self) -> None:
        self.value = snapshot()
        self.failure: Exception | None = None

    async def read_page(self, **_: object) -> TaskTargetPreviewSnapshot:
        if self.failure is not None:
            raise self.failure
        return self.value

    async def replace_exclusions(
        self, pending: PendingTaskTargetExclusions
    ) -> TaskTargetPreviewMutationResult:
        if self.failure is not None:
            raise self.failure
        return TaskTargetPreviewMutationResult(snapshot=self.value, replayed=False)

    async def confirm(
        self, pending: PendingTaskTargetConfirmation
    ) -> TaskTargetPreviewMutationResult:
        if self.failure is not None:
            raise self.failure
        return TaskTargetPreviewMutationResult(snapshot=self.value, replayed=False)


class FixedClock:
    def now(self) -> datetime:
        return NOW


def test_preview_item_exposes_selection_without_private_candidate_identity() -> None:
    item = TaskTargetPreviewItem(target=target(DouyinCandidateDisposition.ELIGIBLE))

    assert item.user_excluded is False
    assert item.selected is True
    assert "private-platform-id" not in repr(item)
    with pytest.raises(FrozenInstanceError):
        item.user_excluded = True  # type: ignore[misc]


def test_policy_excluded_target_cannot_be_marked_as_user_excluded() -> None:
    with pytest.raises(ValueError):
        TaskTargetPreviewItem(
            target=target(DouyinCandidateDisposition.BLACKLISTED),
            user_excluded=True,
        )


@pytest.mark.parametrize(
    "invalid",
    (
        None,
        datetime(2026, 7, 20, 1, 0),
        datetime(2026, 7, 20, 9, 0, tzinfo=timezone(timedelta(hours=8))),
        datetime(2026, 7, 20, 1, 0, tzinfo=BrokenTimezone()),
    ),
)
def test_pending_models_reject_non_utc_time(invalid: object) -> None:
    with pytest.raises(InvalidTaskTargetPreview):
        PendingTaskTargetConfirmation(
            source_message_id=uuid4(),
            installation_id=InstallationId.new(),
            task_id=TaskId.new(),
            page_revision=1,
            expected_task_revision=1,
            idempotency_key="task:preview:time",
            requested_at=cast(datetime, invalid),
        )


@pytest.mark.parametrize(
    ("field", "invalid"),
    (
        ("source_message_id", UUID("123e4567-e89b-12d3-a456-426614174000")),
        ("installation_id", TaskId.new()),
        ("task_id", InstallationId.new()),
        ("page_revision", True),
        ("page_revision", 0),
        ("page_revision", (1 << 53)),
        ("expected_task_revision", 0),
        ("expected_task_revision", (1 << 53)),
        ("excluded_target_ids", []),
        ("excluded_target_ids", tuple(TargetId.new() for _ in range(101))),
        ("excluded_target_ids", (TaskId.new(),)),
        ("idempotency_key", "contains space"),
    ),
)
def test_pending_exclusions_reject_invalid_fields(field: str, invalid: Any) -> None:
    pending = PendingTaskTargetExclusions(
        source_message_id=uuid4(),
        installation_id=InstallationId.new(),
        task_id=TaskId.new(),
        page_revision=1,
        expected_task_revision=1,
        excluded_target_ids=(),
        idempotency_key="task:preview:exclude",
        requested_at=NOW,
    )
    with pytest.raises(InvalidTaskTargetPreview):
        replace(pending, **{field: invalid})


def test_pending_exclusions_reject_duplicate_ids_and_fingerprint_is_stable() -> None:
    target_id = TargetId.new()
    pending = PendingTaskTargetExclusions(
        source_message_id=uuid4(),
        installation_id=InstallationId.new(),
        task_id=TaskId.new(),
        page_revision=7,
        expected_task_revision=4,
        excluded_target_ids=(target_id,),
        idempotency_key="task:preview:exclude",
        requested_at=NOW,
    )
    replay = replace(pending, source_message_id=uuid4(), requested_at=NOW + timedelta(seconds=1))
    assert pending.fingerprint() == replay.fingerprint()
    assert len(pending.fingerprint()) == 32
    with pytest.raises(InvalidTaskTargetPreview):
        replace(pending, excluded_target_ids=(target_id, target_id))


@pytest.mark.parametrize(
    ("field", "invalid"),
    (
        ("source_message_id", UUID("123e4567-e89b-12d3-a456-426614174000")),
        ("installation_id", TaskId.new()),
        ("task_id", InstallationId.new()),
        ("page_revision", False),
        ("page_revision", 0),
        ("expected_task_revision", 0),
        ("idempotency_key", "contains space"),
    ),
)
def test_pending_confirmation_rejects_invalid_fields(field: str, invalid: Any) -> None:
    pending = PendingTaskTargetConfirmation(
        source_message_id=uuid4(),
        installation_id=InstallationId.new(),
        task_id=TaskId.new(),
        page_revision=1,
        expected_task_revision=1,
        idempotency_key="task:preview:confirm",
        requested_at=NOW,
    )
    assert len(pending.fingerprint()) == 32
    with pytest.raises(InvalidTaskTargetPreview):
        replace(pending, **{field: invalid})


def test_preview_value_objects_reject_incoherent_state() -> None:
    eligible = target(DouyinCandidateDisposition.ELIGIBLE)
    with pytest.raises(InvalidTaskTargetPreview):
        TaskTargetPreviewItem(target=cast(TaskTargetRecord, object()))
    with pytest.raises(InvalidTaskTargetPreview):
        TaskTargetPreviewItem(target=eligible, user_excluded=cast(bool, 1))

    valid = snapshot()
    invalid_snapshots: tuple[dict[str, object], ...] = (
        {"task": object()},
        {"page_revision": 0},
        {"items": []},
        {"selected_target_count": -1},
        {"user_excluded_target_count": 101},
        {"selected_target_count": 100, "user_excluded_target_count": 1},
        {"task": replace(valid.task, status=TaskStatus.QUEUED)},
        {"confirmed_at": NOW},
    )
    for overrides in invalid_snapshots:
        with pytest.raises(InvalidTaskTargetPreview):
            replace(valid, **overrides)  # type: ignore[arg-type]

    running = snapshot(status=TaskStatus.RUNNING)
    assert running.confirmed_at == NOW
    with pytest.raises(InvalidTaskTargetPreview):
        replace(running, task=replace(running.task, status=TaskStatus.DISCOVERING_TARGETS))
    with pytest.raises(InvalidTaskTargetPreview):
        TaskTargetPreviewPage(snapshot=cast(TaskTargetPreviewSnapshot, object()), next_cursor=None)
    with pytest.raises(InvalidTaskTargetPreview):
        TaskTargetPreviewPage(snapshot=valid, next_cursor="invalid+cursor")
    with pytest.raises(InvalidTaskTargetPreview):
        TaskTargetPreviewMutationResult(snapshot=valid, replayed=cast(bool, 1))


def _cursor(payload: object) -> str:
    encoded = json.dumps(payload, separators=(",", ":")).encode("ascii")
    return base64.urlsafe_b64encode(encoded).decode("ascii").rstrip("=")


@pytest.mark.parametrize(
    "invalid",
    (
        None,
        "invalid+cursor",
        _cursor({"ordinal": 1}),
        _cursor(
            {
                "ordinal": 0,
                "pageRevision": 1,
                "targetId": str(TargetId.new()),
                "taskRevision": 1,
            }
        ),
        _cursor(
            {
                "ordinal": 1,
                "pageRevision": True,
                "targetId": str(TargetId.new()),
                "taskRevision": 1,
            }
        ),
        _cursor(
            {
                "ordinal": 1,
                "pageRevision": 1,
                "targetId": "private-invalid",
                "taskRevision": 1,
            }
        ),
        base64.urlsafe_b64encode(
            (
                '{"ordinal":1,"ordinal":2,"pageRevision":1,'
                f'"targetId":"{TargetId.new()}","taskRevision":1}}'
            ).encode("ascii")
        )
        .decode("ascii")
        .rstrip("="),
    ),
)
def test_preview_cursor_rejects_malformed_values(invalid: object) -> None:
    with pytest.raises(InvalidTaskTargetPreview):
        _decode_cursor(invalid)


@pytest.mark.asyncio
async def test_service_validates_inputs_paginates_and_maps_repository_failures() -> None:
    repository = MemoryRepository()
    service = TaskTargetPreviewService(repository=repository, clock=FixedClock())
    scoped_task = repository.value.task
    for invalid in (
        {"installation_id": TaskId.new(), "task_id": scoped_task.task_id, "limit": 1},
        {"installation_id": scoped_task.installation_id, "task_id": TargetId.new(), "limit": 1},
        {
            "installation_id": scoped_task.installation_id,
            "task_id": scoped_task.task_id,
            "limit": 0,
        },
    ):
        with pytest.raises(InvalidTaskTargetPreview):
            await service.get(cursor=None, **invalid)  # type: ignore[arg-type]

    repository.failure = TaskTargetPreviewNotFound()
    with pytest.raises(TaskTargetPreviewNotFound):
        await service.get(
            installation_id=scoped_task.installation_id,
            task_id=scoped_task.task_id,
            cursor=None,
            limit=1,
        )
    repository.failure = TaskTargetPreviewConflict()
    with pytest.raises(TaskTargetPreviewConflict):
        await service.get(
            installation_id=scoped_task.installation_id,
            task_id=scoped_task.task_id,
            cursor=None,
            limit=1,
        )
    repository.failure = RuntimeError("private")
    with pytest.raises(TaskTargetPreviewUnavailable):
        await service.get(
            installation_id=scoped_task.installation_id,
            task_id=scoped_task.task_id,
            cursor=None,
            limit=1,
        )


@pytest.mark.asyncio
async def test_service_mutations_propagate_domain_failures_and_hide_unknown_failures() -> None:
    repository = MemoryRepository()
    service = TaskTargetPreviewService(repository=repository, clock=FixedClock())
    scoped_task = repository.value.task
    calls: tuple[Callable[[], Awaitable[TaskTargetPreviewMutationResult]], ...] = (
        lambda: service.replace_exclusions(
            installation_id=scoped_task.installation_id,
            task_id=scoped_task.task_id,
            page_revision=7,
            expected_task_revision=4,
            excluded_target_ids=(),
            idempotency_key="task:preview:exclude",
        ),
        lambda: service.confirm(
            installation_id=scoped_task.installation_id,
            task_id=scoped_task.task_id,
            page_revision=7,
            expected_task_revision=4,
            idempotency_key="task:preview:confirm",
        ),
    )
    for call in calls:
        repository.failure = TaskTargetPreviewConflict()
        with pytest.raises(TaskTargetPreviewConflict):
            await call()
        repository.failure = RuntimeError("private")
        with pytest.raises(TaskTargetPreviewUnavailable) as captured:
            await call()
        assert "private" not in str(captured.value)
    with pytest.raises(InvalidTaskTargetPreview):
        TaskTargetPreviewService(repository=cast(Any, object()))
