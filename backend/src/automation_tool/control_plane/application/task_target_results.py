"""Installation-scoped, privacy-safe target result projections."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol, runtime_checkable

from automation_tool.control_plane.application.tasks import TaskRecord
from automation_tool.control_plane.domain import ActionId, InstallationId, TargetId, TaskId
from automation_tool.protocol import (
    FAILED_ACTION_RESULT_EVIDENCE,
    MAX_TASK_TARGET_LIMIT,
    SKIPPED_ACTION_RESULT_EVIDENCE,
    SUCCESS_ACTION_RESULT_EVIDENCE,
    UNCERTAIN_ACTION_RESULT_EVIDENCE,
    DouyinCandidateRejected,
    DouyinCandidateSummary,
)
from automation_tool.protocol import (
    ActionResultEvidence as TaskTargetResultEvidence,
)


class InvalidTaskTargetResult(ValueError):
    def __init__(self) -> None:
        super().__init__("Task target result is invalid")


class TaskTargetResultNotFound(LookupError):
    def __init__(self) -> None:
        super().__init__("Task target results are unavailable")


class TaskTargetResultUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Task target result service is unavailable")


class TaskTargetResultStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    SKIPPED = "skipped"
    FAILED = "failed"
    OUTCOME_UNCERTAIN = "outcome_uncertain"


_STATUS_EVIDENCE = {
    TaskTargetResultStatus.PENDING: frozenset(
        {
            TaskTargetResultEvidence.AWAITING_EXECUTION,
            TaskTargetResultEvidence.ACTION_PENDING,
        }
    ),
    TaskTargetResultStatus.RUNNING: frozenset({TaskTargetResultEvidence.ACTION_IN_PROGRESS}),
    TaskTargetResultStatus.SUCCEEDED: SUCCESS_ACTION_RESULT_EVIDENCE,
    TaskTargetResultStatus.SKIPPED: SKIPPED_ACTION_RESULT_EVIDENCE,
    TaskTargetResultStatus.FAILED: FAILED_ACTION_RESULT_EVIDENCE,
    TaskTargetResultStatus.OUTCOME_UNCERTAIN: UNCERTAIN_ACTION_RESULT_EVIDENCE,
}
_ACTION_REQUIRED = frozenset(
    {
        TaskTargetResultStatus.RUNNING,
        TaskTargetResultStatus.SUCCEEDED,
        TaskTargetResultStatus.FAILED,
        TaskTargetResultStatus.OUTCOME_UNCERTAIN,
    }
)


def _canonical_utc(value: object) -> datetime | None:
    if type(value) is not datetime or value.tzinfo is None:
        return None
    if value.utcoffset() != timedelta(0):
        return None
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True, repr=False)
class TaskTargetResult:
    target_id: TargetId
    ordinal: int
    display_name: str
    public_handle: str | None
    status: TaskTargetResultStatus
    evidence: TaskTargetResultEvidence
    action_id: ActionId | None
    updated_at: datetime

    def __post_init__(self) -> None:
        timestamp = _canonical_utc(self.updated_at)
        try:
            summary_valid = isinstance(
                DouyinCandidateSummary(
                    display_name=self.display_name,
                    public_handle=self.public_handle,
                ),
                DouyinCandidateSummary,
            )
        except DouyinCandidateRejected:
            summary_valid = False
        action_scope_valid = (
            (self.status in _ACTION_REQUIRED and isinstance(self.action_id, ActionId))
            or (
                self.status is TaskTargetResultStatus.SKIPPED
                and self.evidence is TaskTargetResultEvidence.ACTION_CANCELLED
                and isinstance(self.action_id, ActionId)
            )
            or (
                self.status is TaskTargetResultStatus.PENDING
                and self.evidence is TaskTargetResultEvidence.ACTION_PENDING
                and isinstance(self.action_id, ActionId)
            )
            or (
                self.status is TaskTargetResultStatus.PENDING
                and self.evidence is TaskTargetResultEvidence.AWAITING_EXECUTION
                and self.action_id is None
            )
            or (
                self.status is TaskTargetResultStatus.SKIPPED
                and self.evidence is not TaskTargetResultEvidence.ACTION_CANCELLED
                and self.action_id is None
            )
        )
        if (
            not isinstance(self.target_id, TargetId)
            or type(self.ordinal) is not int
            or not 1 <= self.ordinal <= MAX_TASK_TARGET_LIMIT
            or not summary_valid
            or not isinstance(self.status, TaskTargetResultStatus)
            or not isinstance(self.evidence, TaskTargetResultEvidence)
            or self.evidence not in _STATUS_EVIDENCE[self.status]
            or not action_scope_valid
            or timestamp is None
        ):
            raise InvalidTaskTargetResult
        object.__setattr__(self, "updated_at", timestamp)

    def __repr__(self) -> str:
        return (
            "TaskTargetResult("
            f"ordinal={self.ordinal!r}, status={self.status.value!r}, "
            f"evidence={self.evidence.value!r}, <redacted>)"
        )


@dataclass(frozen=True, slots=True)
class TaskTargetResultSnapshot:
    task: TaskRecord
    items: tuple[TaskTargetResult, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.task, TaskRecord)
            or type(self.items) is not tuple
            or len(self.items) > MAX_TASK_TARGET_LIMIT
            or any(not isinstance(item, TaskTargetResult) for item in self.items)
            or tuple(item.ordinal for item in self.items)
            != tuple(sorted(item.ordinal for item in self.items))
            or len({item.target_id for item in self.items}) != len(self.items)
        ):
            raise InvalidTaskTargetResult


@runtime_checkable
class TaskTargetResultRepository(Protocol):
    async def get(
        self,
        *,
        installation_id: InstallationId,
        task_id: TaskId,
    ) -> TaskTargetResultSnapshot | None: ...


class TaskTargetResultService:
    def __init__(self, *, repository: TaskTargetResultRepository) -> None:
        if not isinstance(repository, TaskTargetResultRepository):
            raise InvalidTaskTargetResult
        self._repository = repository

    async def get(
        self,
        *,
        installation_id: InstallationId,
        task_id: TaskId,
    ) -> TaskTargetResultSnapshot:
        if not isinstance(installation_id, InstallationId) or not isinstance(task_id, TaskId):
            raise InvalidTaskTargetResult
        try:
            snapshot = await self._repository.get(
                installation_id=installation_id,
                task_id=task_id,
            )
        except (InvalidTaskTargetResult, TaskTargetResultUnavailable):
            raise
        except Exception:
            raise TaskTargetResultUnavailable from None
        if snapshot is None:
            raise TaskTargetResultNotFound
        return snapshot


__all__ = [
    "InvalidTaskTargetResult",
    "TaskTargetResult",
    "TaskTargetResultEvidence",
    "TaskTargetResultNotFound",
    "TaskTargetResultRepository",
    "TaskTargetResultService",
    "TaskTargetResultSnapshot",
    "TaskTargetResultStatus",
    "TaskTargetResultUnavailable",
]
