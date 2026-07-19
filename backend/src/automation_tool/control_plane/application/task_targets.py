"""Application contracts for Installation-scoped Task target persistence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from automation_tool.control_plane.domain import (
    DOUYIN_CANDIDATE_POLICY_VERSION,
    DouyinCandidateDisposition,
    InstallationId,
    TargetId,
    TaskId,
)
from automation_tool.protocol import MAX_TASK_TARGET_LIMIT, DouyinCandidate


class TaskTargetPersistenceRejected(PermissionError):
    """A target scope, snapshot, cursor, or persistence operation was rejected."""

    def __init__(self) -> None:
        super().__init__("Task target persistence operation is rejected")


@dataclass(frozen=True, slots=True, repr=False)
class TaskTargetRecord:
    """One ordered preview row with only the bounded Candidate summary."""

    target_id: TargetId
    task_id: TaskId
    installation_id: InstallationId
    ordinal: int
    candidate: DouyinCandidate
    disposition: DouyinCandidateDisposition
    policy_version: str
    evaluated_at: datetime
    created_at: datetime

    def __post_init__(self) -> None:
        evaluated_at = _canonical_utc(self.evaluated_at)
        created_at = _canonical_utc(self.created_at)
        if (
            not isinstance(self.target_id, TargetId)
            or not isinstance(self.task_id, TaskId)
            or not isinstance(self.installation_id, InstallationId)
            or type(self.ordinal) is not int
            or not 1 <= self.ordinal <= MAX_TASK_TARGET_LIMIT
            or not isinstance(self.candidate, DouyinCandidate)
            or not isinstance(self.disposition, DouyinCandidateDisposition)
            or self.policy_version != DOUYIN_CANDIDATE_POLICY_VERSION
            or evaluated_at is None
            or created_at is None
            or created_at < evaluated_at
        ):
            raise TaskTargetPersistenceRejected
        object.__setattr__(self, "evaluated_at", evaluated_at)
        object.__setattr__(self, "created_at", created_at)

    def __repr__(self) -> str:
        return (
            "TaskTargetRecord("
            f"ordinal={self.ordinal!r}, disposition={self.disposition.value!r}, "
            f"page_revision={self.candidate.page_revision!r}, <redacted>)"
        )


def _canonical_utc(value: object) -> datetime | None:
    if type(value) is not datetime or value.tzinfo is None:
        return None
    try:
        if value.utcoffset() != timedelta(0):
            return None
        return value.astimezone(UTC)
    except Exception:
        return None


__all__ = [
    "TaskTargetPersistenceRejected",
    "TaskTargetRecord",
]
