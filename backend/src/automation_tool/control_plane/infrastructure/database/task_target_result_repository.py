"""PostgreSQL projection of one Task's target-level results."""

from __future__ import annotations

from datetime import datetime
from typing import cast

from sqlalchemy import and_, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import SQLAlchemyError

from automation_tool.control_plane.application.task_target_results import (
    InvalidTaskTargetResult,
    TaskTargetResult,
    TaskTargetResultEvidence,
    TaskTargetResultSnapshot,
    TaskTargetResultStatus,
    TaskTargetResultUnavailable,
)
from automation_tool.control_plane.application.tasks import TaskRecord
from automation_tool.control_plane.domain import (
    ActionId,
    ActionOutcome,
    ActionStatus,
    DouyinCandidateDisposition,
    InstallationId,
    TargetId,
    TaskId,
    TaskStatus,
)
from automation_tool.protocol import (
    FAILED_ACTION_RESULT_EVIDENCE,
    SUCCESS_ACTION_RESULT_EVIDENCE,
    UNCERTAIN_ACTION_RESULT_EVIDENCE,
    ActionResultEvidence,
)

from .schema import (
    action_risk_authorizations,
    task_actions,
    task_target_exclusions,
    task_targets,
    tasks,
)
from .session import Database

_DISPOSITION_EVIDENCE = {
    DouyinCandidateDisposition.DUPLICATE_IN_TASK: ActionResultEvidence.DUPLICATE_IN_TASK,
    DouyinCandidateDisposition.DUPLICATE_IN_HISTORY: ActionResultEvidence.DUPLICATE_IN_HISTORY,
    DouyinCandidateDisposition.BLACKLISTED: ActionResultEvidence.BLACKLISTED,
}


def _task(row: RowMapping) -> TaskRecord:
    return TaskRecord(
        task_id=TaskId.parse(row["id"]),
        installation_id=InstallationId.parse(row["installation_id"]),
        status=TaskStatus(cast(str, row["status"])),
        revision=cast(int, row["revision"]),
        last_event_sequence=cast(int, row["last_event_sequence"]),
        created_at=cast(datetime, row["created_at"]),
        updated_at=cast(datetime, row["updated_at"]),
    )


def _persisted_evidence(
    row: RowMapping,
    *,
    allowed: frozenset[ActionResultEvidence],
    fallback: ActionResultEvidence,
) -> ActionResultEvidence:
    value = row["evidence_code"]
    if value is None:
        return fallback
    try:
        evidence = ActionResultEvidence(cast(str, value))
    except ValueError:
        raise InvalidTaskTargetResult from None
    if evidence not in allowed:
        raise InvalidTaskTargetResult
    return evidence


def _result(row: RowMapping) -> TaskTargetResult:
    target_id = TargetId.parse(row["target_id"])
    disposition = DouyinCandidateDisposition(cast(str, row["disposition"]))
    action_value = row["action_id"]
    action_id = None if action_value is None else ActionId.parse(action_value)
    if row["excluded_at"] is not None:
        status = TaskTargetResultStatus.SKIPPED
        evidence = ActionResultEvidence.USER_EXCLUDED
        action_id = None
        updated_at = cast(datetime, row["excluded_at"])
    elif disposition is not DouyinCandidateDisposition.ELIGIBLE:
        status = TaskTargetResultStatus.SKIPPED
        evidence = _DISPOSITION_EVIDENCE[disposition]
        action_id = None
        updated_at = cast(datetime, row["target_updated_at"])
    elif action_id is None:
        status = TaskTargetResultStatus.PENDING
        evidence = ActionResultEvidence.AWAITING_EXECUTION
        updated_at = cast(datetime, row["target_updated_at"])
    else:
        action_status = ActionStatus(cast(str, row["action_status"]))
        action_outcome = ActionOutcome(cast(str, row["action_outcome"]))
        updated_at = cast(datetime, row["action_updated_at"])
        if action_status in {ActionStatus.PLANNED, ActionStatus.AUTHORIZED, ActionStatus.PREPARED}:
            status = TaskTargetResultStatus.PENDING
            evidence = ActionResultEvidence.ACTION_PENDING
        elif action_status is ActionStatus.DISPATCHED:
            status = TaskTargetResultStatus.RUNNING
            evidence = ActionResultEvidence.ACTION_IN_PROGRESS
        elif action_status is ActionStatus.CANCELLED:
            status = TaskTargetResultStatus.SKIPPED
            evidence = ActionResultEvidence.ACTION_CANCELLED
        elif action_status is ActionStatus.VERIFIED and action_outcome is ActionOutcome.SUCCEEDED:
            status = TaskTargetResultStatus.SUCCEEDED
            evidence = _persisted_evidence(
                row,
                allowed=SUCCESS_ACTION_RESULT_EVIDENCE,
                fallback=ActionResultEvidence.EXECUTOR_REPORTED_SUCCESS,
            )
        elif action_status is ActionStatus.VERIFIED and action_outcome is ActionOutcome.FAILED:
            status = TaskTargetResultStatus.FAILED
            evidence = _persisted_evidence(
                row,
                allowed=FAILED_ACTION_RESULT_EVIDENCE,
                fallback=ActionResultEvidence.EXECUTOR_REPORTED_FAILURE,
            )
        elif (
            action_status is ActionStatus.OUTCOME_UNCERTAIN
            and action_outcome is ActionOutcome.OUTCOME_UNCERTAIN
        ):
            status = TaskTargetResultStatus.OUTCOME_UNCERTAIN
            evidence = _persisted_evidence(
                row,
                allowed=UNCERTAIN_ACTION_RESULT_EVIDENCE,
                fallback=ActionResultEvidence.FINAL_STATE_UNCONFIRMED,
            )
        else:
            raise InvalidTaskTargetResult
    return TaskTargetResult(
        target_id=target_id,
        ordinal=cast(int, row["ordinal"]),
        display_name=cast(str, row["display_name"]),
        public_handle=cast(str | None, row["public_handle"]),
        status=status,
        evidence=TaskTargetResultEvidence(evidence),
        action_id=action_id,
        updated_at=updated_at,
    )


class SqlAlchemyTaskTargetResultRepository:
    def __init__(self, database: Database) -> None:
        if not isinstance(database, Database):
            raise TaskTargetResultUnavailable
        self._database = database

    async def get(
        self,
        *,
        installation_id: InstallationId,
        task_id: TaskId,
    ) -> TaskTargetResultSnapshot | None:
        if not isinstance(installation_id, InstallationId) or not isinstance(task_id, TaskId):
            raise InvalidTaskTargetResult
        try:
            async with self._database.session() as session:
                task_row = (
                    (
                        await session.execute(
                            select(tasks).where(
                                tasks.c.id == task_id.uuid,
                                tasks.c.installation_id == installation_id.uuid,
                            )
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if task_row is None:
                    return None
                current_attempt_id = task_row["current_attempt_id"]
                target_rows = (
                    (
                        await session.execute(
                            select(
                                task_targets.c.id.label("target_id"),
                                task_targets.c.ordinal,
                                task_targets.c.display_name,
                                task_targets.c.public_handle,
                                task_targets.c.disposition,
                                task_targets.c.created_at.label("target_updated_at"),
                                task_target_exclusions.c.excluded_at,
                                task_actions.c.id.label("action_id"),
                                task_actions.c.status.label("action_status"),
                                task_actions.c.outcome.label("action_outcome"),
                                task_actions.c.evidence_code,
                                task_actions.c.updated_at.label("action_updated_at"),
                            )
                            .select_from(
                                task_targets.outerjoin(
                                    task_target_exclusions,
                                    and_(
                                        task_target_exclusions.c.target_id == task_targets.c.id,
                                        task_target_exclusions.c.task_id == task_targets.c.task_id,
                                        task_target_exclusions.c.installation_id
                                        == task_targets.c.installation_id,
                                    ),
                                )
                                .outerjoin(
                                    action_risk_authorizations,
                                    and_(
                                        action_risk_authorizations.c.target_id == task_targets.c.id,
                                        action_risk_authorizations.c.task_id
                                        == task_targets.c.task_id,
                                        action_risk_authorizations.c.installation_id
                                        == task_targets.c.installation_id,
                                        action_risk_authorizations.c.execution_attempt_id
                                        == current_attempt_id,
                                    ),
                                )
                                .outerjoin(
                                    task_actions,
                                    task_actions.c.id == action_risk_authorizations.c.action_id,
                                )
                            )
                            .where(
                                task_targets.c.task_id == task_id.uuid,
                                task_targets.c.installation_id == installation_id.uuid,
                            )
                            .order_by(task_targets.c.ordinal, task_targets.c.id)
                        )
                    )
                    .mappings()
                    .all()
                )
            return TaskTargetResultSnapshot(
                task=_task(task_row),
                items=tuple(_result(row) for row in target_rows),
            )
        except (InvalidTaskTargetResult, TaskTargetResultUnavailable):
            raise
        except SQLAlchemyError:
            raise TaskTargetResultUnavailable from None


__all__ = ["SqlAlchemyTaskTargetResultRepository"]
