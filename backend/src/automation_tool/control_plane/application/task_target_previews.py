"""Installation-scoped target preview queries, exclusions, and confirmation."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable
from uuid import RFC_4122, UUID, uuid4

from automation_tool.control_plane.application.task_targets import TaskTargetRecord
from automation_tool.control_plane.application.tasks import TaskRecord
from automation_tool.control_plane.domain import (
    DouyinCandidateDisposition,
    InstallationId,
    TargetId,
    TaskId,
    TaskStatus,
)
from automation_tool.protocol import MAX_TASK_TARGET_LIMIT, IdempotencyKey

_CURSOR_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,512}$")


class InvalidTaskTargetPreview(ValueError):
    def __init__(self) -> None:
        super().__init__("Task target preview request is invalid")


class TaskTargetPreviewNotFound(LookupError):
    def __init__(self) -> None:
        super().__init__("Task target preview is unavailable")


class TaskTargetPreviewConflict(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Task target preview is stale or conflicts with current state")


class TaskTargetPreviewUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Task target preview service is unavailable")


class TaskTargetPreviewClock(Protocol):
    def now(self) -> datetime: ...


def _canonical_utc(value: object) -> datetime:
    if type(value) is not datetime or value.tzinfo is None:
        raise InvalidTaskTargetPreview
    try:
        if value.utcoffset() != timedelta(0):
            raise InvalidTaskTargetPreview
        return value.astimezone(UTC)
    except InvalidTaskTargetPreview:
        raise
    except Exception:
        raise InvalidTaskTargetPreview from None


def _uuid_v4(value: object) -> UUID:
    if not isinstance(value, UUID) or value.version != 4 or value.variant != RFC_4122:
        raise InvalidTaskTargetPreview
    return value


@dataclass(frozen=True, slots=True, repr=False)
class TaskTargetPreviewItem:
    target: TaskTargetRecord
    user_excluded: bool = False

    def __post_init__(self) -> None:
        if (
            not isinstance(self.target, TaskTargetRecord)
            or type(self.user_excluded) is not bool
            or (
                self.user_excluded
                and self.target.disposition is not DouyinCandidateDisposition.ELIGIBLE
            )
        ):
            raise InvalidTaskTargetPreview

    @property
    def selected(self) -> bool:
        return (
            self.target.disposition is DouyinCandidateDisposition.ELIGIBLE
            and not self.user_excluded
        )

    def __repr__(self) -> str:
        return (
            "TaskTargetPreviewItem("
            f"ordinal={self.target.ordinal!r}, selected={self.selected!r}, <redacted>)"
        )


@dataclass(frozen=True, slots=True)
class TaskTargetPreviewSnapshot:
    task: TaskRecord
    page_revision: int
    items: tuple[TaskTargetPreviewItem, ...]
    selected_target_count: int
    user_excluded_target_count: int
    confirmed_at: datetime | None = None

    def __post_init__(self) -> None:
        confirmed_at = None if self.confirmed_at is None else _canonical_utc(self.confirmed_at)
        if (
            not isinstance(self.task, TaskRecord)
            or type(self.page_revision) is not int
            or not 1 <= self.page_revision <= (1 << 53) - 1
            or type(self.items) is not tuple
            or any(
                not isinstance(item, TaskTargetPreviewItem)
                or item.target.task_id != self.task.task_id
                or item.target.installation_id != self.task.installation_id
                or item.target.candidate.page_revision != self.page_revision
                for item in self.items
            )
            or type(self.selected_target_count) is not int
            or not 0 <= self.selected_target_count <= MAX_TASK_TARGET_LIMIT
            or type(self.user_excluded_target_count) is not int
            or not 0 <= self.user_excluded_target_count <= MAX_TASK_TARGET_LIMIT
            or self.selected_target_count + self.user_excluded_target_count > MAX_TASK_TARGET_LIMIT
            or (confirmed_at is None and self.task.status is not TaskStatus.AWAITING_CONFIRMATION)
            or (
                confirmed_at is not None
                and self.task.status
                in {
                    TaskStatus.DRAFT,
                    TaskStatus.VALIDATING,
                    TaskStatus.DISCOVERING_TARGETS,
                    TaskStatus.AWAITING_CONFIRMATION,
                }
            )
        ):
            raise InvalidTaskTargetPreview
        object.__setattr__(self, "confirmed_at", confirmed_at)


@dataclass(frozen=True, slots=True)
class TaskTargetPreviewPage:
    snapshot: TaskTargetPreviewSnapshot
    next_cursor: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, TaskTargetPreviewSnapshot) or (
            self.next_cursor is not None
            and (
                type(self.next_cursor) is not str or not _CURSOR_PATTERN.fullmatch(self.next_cursor)
            )
        ):
            raise InvalidTaskTargetPreview


@dataclass(frozen=True, slots=True)
class TaskTargetPreviewMutationResult:
    snapshot: TaskTargetPreviewSnapshot
    replayed: bool

    def __post_init__(self) -> None:
        if (
            not isinstance(self.snapshot, TaskTargetPreviewSnapshot)
            or type(self.replayed) is not bool
        ):
            raise InvalidTaskTargetPreview


@dataclass(frozen=True, slots=True)
class PendingTaskTargetExclusions:
    source_message_id: UUID
    installation_id: InstallationId
    task_id: TaskId
    page_revision: int
    expected_task_revision: int
    excluded_target_ids: tuple[TargetId, ...]
    idempotency_key: str
    requested_at: datetime

    def __post_init__(self) -> None:
        requested_at = _canonical_utc(self.requested_at)
        try:
            normalized_key = str(IdempotencyKey(self.idempotency_key))
        except Exception:
            raise InvalidTaskTargetPreview from None
        if (
            _uuid_v4(self.source_message_id) is not self.source_message_id
            or not isinstance(self.installation_id, InstallationId)
            or not isinstance(self.task_id, TaskId)
            or type(self.page_revision) is not int
            or not 1 <= self.page_revision <= (1 << 53) - 1
            or type(self.expected_task_revision) is not int
            or not 1 <= self.expected_task_revision <= (1 << 53) - 1
            or type(self.excluded_target_ids) is not tuple
            or len(self.excluded_target_ids) > MAX_TASK_TARGET_LIMIT
            or any(not isinstance(value, TargetId) for value in self.excluded_target_ids)
            or len(set(self.excluded_target_ids)) != len(self.excluded_target_ids)
        ):
            raise InvalidTaskTargetPreview
        object.__setattr__(self, "idempotency_key", normalized_key)
        object.__setattr__(self, "requested_at", requested_at)

    def fingerprint(self) -> bytes:
        return _fingerprint(
            operation="replace_exclusions",
            installation_id=self.installation_id,
            task_id=self.task_id,
            page_revision=self.page_revision,
            expected_task_revision=self.expected_task_revision,
            target_ids=self.excluded_target_ids,
        )


@dataclass(frozen=True, slots=True)
class PendingTaskTargetConfirmation:
    source_message_id: UUID
    installation_id: InstallationId
    task_id: TaskId
    page_revision: int
    expected_task_revision: int
    idempotency_key: str
    requested_at: datetime

    def __post_init__(self) -> None:
        requested_at = _canonical_utc(self.requested_at)
        try:
            normalized_key = str(IdempotencyKey(self.idempotency_key))
        except Exception:
            raise InvalidTaskTargetPreview from None
        if (
            _uuid_v4(self.source_message_id) is not self.source_message_id
            or not isinstance(self.installation_id, InstallationId)
            or not isinstance(self.task_id, TaskId)
            or type(self.page_revision) is not int
            or not 1 <= self.page_revision <= (1 << 53) - 1
            or type(self.expected_task_revision) is not int
            or not 1 <= self.expected_task_revision <= (1 << 53) - 1
        ):
            raise InvalidTaskTargetPreview
        object.__setattr__(self, "idempotency_key", normalized_key)
        object.__setattr__(self, "requested_at", requested_at)

    def fingerprint(self) -> bytes:
        return _fingerprint(
            operation="confirm",
            installation_id=self.installation_id,
            task_id=self.task_id,
            page_revision=self.page_revision,
            expected_task_revision=self.expected_task_revision,
            target_ids=(),
        )


def _fingerprint(
    *,
    operation: str,
    installation_id: InstallationId,
    task_id: TaskId,
    page_revision: int,
    expected_task_revision: int,
    target_ids: tuple[TargetId, ...],
) -> bytes:
    encoded = json.dumps(
        {
            "expectedTaskRevision": expected_task_revision,
            "excludedTargetIds": [str(value) for value in target_ids],
            "installationId": str(installation_id),
            "operation": operation,
            "pageRevision": page_revision,
            "taskId": str(task_id),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(encoded).digest()


@runtime_checkable
class TaskTargetPreviewRepository(Protocol):
    async def read_page(
        self,
        *,
        installation_id: InstallationId,
        task_id: TaskId,
        expected_page_revision: int | None,
        expected_task_revision: int | None,
        after_ordinal: int | None,
        after_target_id: TargetId | None,
        limit: int,
    ) -> TaskTargetPreviewSnapshot: ...

    async def replace_exclusions(
        self, pending: PendingTaskTargetExclusions
    ) -> TaskTargetPreviewMutationResult: ...

    async def confirm(
        self, pending: PendingTaskTargetConfirmation
    ) -> TaskTargetPreviewMutationResult: ...


@dataclass(frozen=True, slots=True)
class _Cursor:
    page_revision: int
    task_revision: int
    ordinal: int
    target_id: TargetId


def _encode_cursor(value: _Cursor) -> str:
    payload = json.dumps(
        {
            "ordinal": value.ordinal,
            "pageRevision": value.page_revision,
            "targetId": str(value.target_id),
            "taskRevision": value.task_revision,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(value: object) -> _Cursor:
    if type(value) is not str or not _CURSOR_PATTERN.fullmatch(value):
        raise InvalidTaskTargetPreview
    try:
        raw = base64.urlsafe_b64decode(value + ("=" * (-len(value) % 4)))
        parsed = json.loads(
            raw.decode("ascii"),
            object_pairs_hook=lambda pairs: _unique_object(pairs),
        )
        if set(parsed) != {"ordinal", "pageRevision", "targetId", "taskRevision"}:
            raise ValueError
        cursor = _Cursor(
            page_revision=parsed["pageRevision"],
            task_revision=parsed["taskRevision"],
            ordinal=parsed["ordinal"],
            target_id=TargetId.parse(parsed["targetId"]),
        )
        if (
            type(cursor.page_revision) is not int
            or not 1 <= cursor.page_revision <= (1 << 53) - 1
            or type(cursor.task_revision) is not int
            or not 1 <= cursor.task_revision <= (1 << 53) - 1
            or type(cursor.ordinal) is not int
            or not 1 <= cursor.ordinal <= MAX_TASK_TARGET_LIMIT
        ):
            raise ValueError
        return cursor
    except (UnicodeError, ValueError, TypeError, binascii.Error):
        raise InvalidTaskTargetPreview from None


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


class TaskTargetPreviewService:
    def __init__(
        self,
        *,
        repository: TaskTargetPreviewRepository,
        clock: TaskTargetPreviewClock | None = None,
        id_source: object = uuid4,
    ) -> None:
        if not isinstance(repository, TaskTargetPreviewRepository) or not callable(id_source):
            raise InvalidTaskTargetPreview
        self._repository = repository
        self._clock = clock or _SystemClock()
        self._id_source = id_source

    async def get(
        self,
        *,
        installation_id: InstallationId,
        task_id: TaskId,
        cursor: str | None,
        limit: int,
    ) -> TaskTargetPreviewPage:
        if (
            not isinstance(installation_id, InstallationId)
            or not isinstance(task_id, TaskId)
            or type(limit) is not int
            or not 1 <= limit <= MAX_TASK_TARGET_LIMIT
        ):
            raise InvalidTaskTargetPreview
        boundary = None if cursor is None else _decode_cursor(cursor)
        try:
            snapshot = await self._repository.read_page(
                installation_id=installation_id,
                task_id=task_id,
                expected_page_revision=None if boundary is None else boundary.page_revision,
                expected_task_revision=None if boundary is None else boundary.task_revision,
                after_ordinal=None if boundary is None else boundary.ordinal,
                after_target_id=None if boundary is None else boundary.target_id,
                limit=limit + 1,
            )
        except (TaskTargetPreviewNotFound, TaskTargetPreviewConflict):
            raise
        except Exception:
            raise TaskTargetPreviewUnavailable from None
        has_more = len(snapshot.items) > limit
        visible = snapshot.items[:limit]
        if has_more:
            last = visible[-1].target
            next_cursor = _encode_cursor(
                _Cursor(
                    page_revision=snapshot.page_revision,
                    task_revision=snapshot.task.revision,
                    ordinal=last.ordinal,
                    target_id=last.target_id,
                )
            )
        else:
            next_cursor = None
        return TaskTargetPreviewPage(
            snapshot=TaskTargetPreviewSnapshot(
                task=snapshot.task,
                page_revision=snapshot.page_revision,
                items=visible,
                selected_target_count=snapshot.selected_target_count,
                user_excluded_target_count=snapshot.user_excluded_target_count,
                confirmed_at=snapshot.confirmed_at,
            ),
            next_cursor=next_cursor,
        )

    async def replace_exclusions(
        self,
        *,
        installation_id: InstallationId,
        task_id: TaskId,
        page_revision: int,
        expected_task_revision: int,
        excluded_target_ids: tuple[TargetId, ...],
        idempotency_key: str,
    ) -> TaskTargetPreviewMutationResult:
        try:
            pending = PendingTaskTargetExclusions(
                source_message_id=_uuid_v4(self._id_source()),
                installation_id=installation_id,
                task_id=task_id,
                page_revision=page_revision,
                expected_task_revision=expected_task_revision,
                excluded_target_ids=excluded_target_ids,
                idempotency_key=idempotency_key,
                requested_at=_canonical_utc(self._clock.now()),
            )
            return await self._repository.replace_exclusions(pending)
        except (InvalidTaskTargetPreview, TaskTargetPreviewNotFound, TaskTargetPreviewConflict):
            raise
        except Exception:
            raise TaskTargetPreviewUnavailable from None

    async def confirm(
        self,
        *,
        installation_id: InstallationId,
        task_id: TaskId,
        page_revision: int,
        expected_task_revision: int,
        idempotency_key: str,
    ) -> TaskTargetPreviewMutationResult:
        try:
            pending = PendingTaskTargetConfirmation(
                source_message_id=_uuid_v4(self._id_source()),
                installation_id=installation_id,
                task_id=task_id,
                page_revision=page_revision,
                expected_task_revision=expected_task_revision,
                idempotency_key=idempotency_key,
                requested_at=_canonical_utc(self._clock.now()),
            )
            return await self._repository.confirm(pending)
        except (InvalidTaskTargetPreview, TaskTargetPreviewNotFound, TaskTargetPreviewConflict):
            raise
        except Exception:
            raise TaskTargetPreviewUnavailable from None


class _SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


__all__ = [
    "InvalidTaskTargetPreview",
    "PendingTaskTargetConfirmation",
    "PendingTaskTargetExclusions",
    "TaskTargetPreviewConflict",
    "TaskTargetPreviewItem",
    "TaskTargetPreviewMutationResult",
    "TaskTargetPreviewNotFound",
    "TaskTargetPreviewPage",
    "TaskTargetPreviewRepository",
    "TaskTargetPreviewService",
    "TaskTargetPreviewSnapshot",
    "TaskTargetPreviewUnavailable",
]
