"""Installation-scoped, read-only workbench metric snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, cast

from automation_tool.control_plane.domain import InstallationId

MAX_WORKBENCH_METRIC_COUNT = 9_007_199_254_740_991
WORKBENCH_METRICS_VERSION: Literal["workbench.metrics.v1"] = "workbench.metrics.v1"


class InvalidWorkbenchMetrics(ValueError):
    def __init__(self) -> None:
        super().__init__("Workbench metrics request is invalid")


class WorkbenchMetricsRepositoryRejected(RuntimeError):
    def __init__(self, *_ignored: object) -> None:
        super().__init__("Workbench metrics repository rejected the request")


class WorkbenchMetricsUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Workbench metrics are unavailable")


@dataclass(frozen=True, slots=True)
class WorkbenchMetricsSnapshot:
    task_total: int
    task_succeeded: int
    task_failed: int
    task_handoff_required: int
    task_outcome_uncertain: int
    action_total: int
    action_succeeded: int
    action_failed: int
    action_outcome_uncertain: int


class WorkbenchMetricsRepository(Protocol):
    async def get(self, *, installation_id: InstallationId) -> WorkbenchMetricsSnapshot: ...


def _valid_count(value: object) -> bool:
    return type(value) is int and 0 <= value <= MAX_WORKBENCH_METRIC_COUNT


def _valid_snapshot(value: object) -> bool:
    if not isinstance(value, WorkbenchMetricsSnapshot):
        return False
    counts = (
        value.task_total,
        value.task_succeeded,
        value.task_failed,
        value.task_handoff_required,
        value.task_outcome_uncertain,
        value.action_total,
        value.action_succeeded,
        value.action_failed,
        value.action_outcome_uncertain,
    )
    return (
        all(_valid_count(count) for count in counts)
        and value.task_succeeded
        + value.task_failed
        + value.task_handoff_required
        + value.task_outcome_uncertain
        <= value.task_total
        and value.action_succeeded + value.action_failed + value.action_outcome_uncertain
        <= value.action_total
    )


class WorkbenchMetricsService:
    def __init__(self, *, repository: WorkbenchMetricsRepository) -> None:
        self._repository = repository

    async def get(self, *, installation_id: InstallationId) -> WorkbenchMetricsSnapshot:
        if not isinstance(installation_id, InstallationId):
            raise InvalidWorkbenchMetrics
        rejected = False
        try:
            snapshot = await self._repository.get(installation_id=installation_id)
        except WorkbenchMetricsRepositoryRejected:
            rejected = True
            snapshot = None
        if rejected:
            raise WorkbenchMetricsUnavailable
        if not _valid_snapshot(snapshot):
            raise WorkbenchMetricsUnavailable
        return cast(WorkbenchMetricsSnapshot, snapshot)


__all__ = [
    "MAX_WORKBENCH_METRIC_COUNT",
    "WORKBENCH_METRICS_VERSION",
    "InvalidWorkbenchMetrics",
    "WorkbenchMetricsRepository",
    "WorkbenchMetricsRepositoryRejected",
    "WorkbenchMetricsService",
    "WorkbenchMetricsSnapshot",
    "WorkbenchMetricsUnavailable",
]
