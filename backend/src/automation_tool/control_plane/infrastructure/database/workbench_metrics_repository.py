"""Single-statement PostgreSQL workbench metric projection."""

from __future__ import annotations

from typing import cast

from sqlalchemy import func, select, true
from sqlalchemy.exc import SQLAlchemyError

from automation_tool.control_plane.application.workbench_metrics import (
    InvalidWorkbenchMetrics,
    WorkbenchMetricsRepositoryRejected,
    WorkbenchMetricsSnapshot,
)
from automation_tool.control_plane.domain import ActionOutcome, InstallationId, TaskStatus

from .schema import task_actions, tasks
from .session import Database


class SqlAlchemyWorkbenchMetricsRepository:
    def __init__(self, database: Database) -> None:
        if not isinstance(database, Database):
            raise WorkbenchMetricsRepositoryRejected
        self._database = database

    async def get(self, *, installation_id: InstallationId) -> WorkbenchMetricsSnapshot:
        if not isinstance(installation_id, InstallationId):
            raise InvalidWorkbenchMetrics
        task_counts = (
            select(
                func.count(tasks.c.id).label("task_total"),
                func.count(tasks.c.id)
                .filter(
                    tasks.c.status.in_(
                        (TaskStatus.SUCCEEDED.value, TaskStatus.PARTIALLY_SUCCEEDED.value)
                    )
                )
                .label("task_succeeded"),
                func.count(tasks.c.id)
                .filter(tasks.c.status == TaskStatus.FAILED.value)
                .label("task_failed"),
                func.count(tasks.c.id)
                .filter(tasks.c.status == TaskStatus.AWAITING_HUMAN.value)
                .label("task_handoff_required"),
                func.count(tasks.c.id)
                .filter(tasks.c.status == TaskStatus.OUTCOME_UNCERTAIN.value)
                .label("task_outcome_uncertain"),
            )
            .where(tasks.c.installation_id == installation_id.uuid)
            .cte("workbench_task_counts")
        )
        action_counts = (
            select(
                func.count(task_actions.c.id).label("action_total"),
                func.count(task_actions.c.id)
                .filter(task_actions.c.outcome == ActionOutcome.SUCCEEDED.value)
                .label("action_succeeded"),
                func.count(task_actions.c.id)
                .filter(task_actions.c.outcome == ActionOutcome.FAILED.value)
                .label("action_failed"),
                func.count(task_actions.c.id)
                .filter(task_actions.c.outcome == ActionOutcome.OUTCOME_UNCERTAIN.value)
                .label("action_outcome_uncertain"),
            )
            .where(task_actions.c.installation_id == installation_id.uuid)
            .cte("workbench_action_counts")
        )
        statement = select(
            task_counts.c.task_total,
            task_counts.c.task_succeeded,
            task_counts.c.task_failed,
            task_counts.c.task_handoff_required,
            task_counts.c.task_outcome_uncertain,
            action_counts.c.action_total,
            action_counts.c.action_succeeded,
            action_counts.c.action_failed,
            action_counts.c.action_outcome_uncertain,
        ).select_from(task_counts.join(action_counts, true()))
        try:
            async with self._database.session() as session:
                row = (await session.execute(statement)).mappings().one()
        except SQLAlchemyError:
            raise WorkbenchMetricsRepositoryRejected from None
        return WorkbenchMetricsSnapshot(
            task_total=cast(int, row["task_total"]),
            task_succeeded=cast(int, row["task_succeeded"]),
            task_failed=cast(int, row["task_failed"]),
            task_handoff_required=cast(int, row["task_handoff_required"]),
            task_outcome_uncertain=cast(int, row["task_outcome_uncertain"]),
            action_total=cast(int, row["action_total"]),
            action_succeeded=cast(int, row["action_succeeded"]),
            action_failed=cast(int, row["action_failed"]),
            action_outcome_uncertain=cast(int, row["action_outcome_uncertain"]),
        )


__all__ = ["SqlAlchemyWorkbenchMetricsRepository"]
