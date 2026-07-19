"""Atomic PostgreSQL repository for Installation-scoped Task target previews."""

from __future__ import annotations

from datetime import datetime
from typing import cast

from sqlalchemy import and_, delete, func, insert, or_, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError

from automation_tool.control_plane.application.task_targets import (
    TaskTargetPersistenceRejected,
    TaskTargetRecord,
)
from automation_tool.control_plane.domain import (
    DouyinCandidateDisposition,
    DouyinCandidateHistoryFact,
    InstallationId,
    InstallationStatus,
    InvalidDouyinCandidatePolicy,
    TargetId,
    TaskId,
    evaluate_douyin_candidates,
)
from automation_tool.control_plane.infrastructure.database.schema import (
    installations,
    task_targets,
    tasks,
)
from automation_tool.control_plane.infrastructure.database.session import Database
from automation_tool.protocol import (
    MAX_EXECUTOR_SEQUENCE,
    MAX_TASK_TARGET_LIMIT,
    DouyinCandidate,
    DouyinCandidateKey,
    DouyinCandidateSource,
    DouyinCandidateSummary,
)


def _record(row: RowMapping) -> TaskTargetRecord:
    try:
        candidate = DouyinCandidate(
            platform_target_id=cast(str, row["platform_target_id"]),
            summary=DouyinCandidateSummary(
                display_name=cast(str, row["display_name"]),
                public_handle=cast(str | None, row["public_handle"]),
            ),
            source=DouyinCandidateSource(cast(str, row["source"])),
            page_revision=cast(int, row["page_revision"]),
        )
        if candidate.dedupe_key != DouyinCandidateKey.parse(row["dedupe_key"]):
            raise TaskTargetPersistenceRejected
        return TaskTargetRecord(
            target_id=TargetId.parse(row["id"]),
            task_id=TaskId.parse(row["task_id"]),
            installation_id=InstallationId.parse(row["installation_id"]),
            ordinal=cast(int, row["ordinal"]),
            candidate=candidate,
            disposition=DouyinCandidateDisposition(cast(str, row["disposition"])),
            policy_version=cast(str, row["policy_version"]),
            evaluated_at=cast(datetime, row["evaluated_at"]),
            created_at=cast(datetime, row["created_at"]),
        )
    except (KeyError, TypeError, ValueError):
        raise TaskTargetPersistenceRejected from None


def _identity(task_id: object, installation_id: object) -> tuple[TaskId, InstallationId]:
    if not isinstance(task_id, TaskId) or not isinstance(installation_id, InstallationId):
        raise TaskTargetPersistenceRejected
    return task_id, installation_id


class SqlAlchemyTaskTargetRepository:
    """Evaluate and store one complete preview snapshot under an Installation lock."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def evaluate_and_replace(
        self,
        *,
        task_id: TaskId,
        installation_id: InstallationId,
        candidates: tuple[DouyinCandidate, ...],
        blacklist: tuple[DouyinCandidateKey, ...],
        evaluated_at: datetime,
    ) -> tuple[TaskTargetRecord, ...]:
        target_task, target_installation = _identity(task_id, installation_id)
        if type(candidates) is not tuple or not candidates:
            raise TaskTargetPersistenceRejected
        try:
            initial = evaluate_douyin_candidates(
                candidates=candidates,
                histories=(),
                blacklist=blacklist,
                evaluated_at=evaluated_at,
            )
            page_revision = candidates[0].page_revision
            async with self._database.session() as session:
                installation_status = await session.scalar(
                    select(installations.c.status)
                    .where(installations.c.id == target_installation.uuid)
                    .with_for_update()
                )
                if installation_status != InstallationStatus.ACTIVE.value:
                    raise TaskTargetPersistenceRejected
                parent = (
                    (
                        await session.execute(
                            select(tasks.c.created_at)
                            .where(
                                tasks.c.id == target_task.uuid,
                                tasks.c.installation_id == target_installation.uuid,
                            )
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if parent is None or initial.evaluated_at < parent["created_at"]:
                    raise TaskTargetPersistenceRejected

                existing_rows = (
                    (
                        await session.execute(
                            select(task_targets)
                            .where(
                                task_targets.c.task_id == target_task.uuid,
                                task_targets.c.installation_id == target_installation.uuid,
                            )
                            .order_by(task_targets.c.ordinal, task_targets.c.id)
                        )
                    )
                    .mappings()
                    .all()
                )
                existing_revisions = {cast(int, row["page_revision"]) for row in existing_rows}
                existing_evaluated_times = {
                    cast(datetime, row["evaluated_at"]) for row in existing_rows
                }
                if existing_revisions and (
                    len(existing_revisions) != 1
                    or len(existing_evaluated_times) != 1
                    or page_revision <= next(iter(existing_revisions))
                    or initial.evaluated_at < next(iter(existing_evaluated_times))
                ):
                    raise TaskTargetPersistenceRejected

                candidate_keys = tuple(dict.fromkeys(str(value.dedupe_key) for value in candidates))
                history_rows = (
                    (
                        await session.execute(
                            select(
                                task_targets.c.dedupe_key,
                                func.max(task_targets.c.evaluated_at).label("observed_at"),
                            )
                            .where(
                                task_targets.c.installation_id == target_installation.uuid,
                                task_targets.c.task_id != target_task.uuid,
                                task_targets.c.dedupe_key.in_(candidate_keys),
                            )
                            .group_by(task_targets.c.dedupe_key)
                        )
                    )
                    .mappings()
                    .all()
                )
                histories = tuple(
                    DouyinCandidateHistoryFact(
                        dedupe_key=DouyinCandidateKey.parse(row["dedupe_key"]),
                        observed_at=cast(datetime, row["observed_at"]),
                    )
                    for row in history_rows
                )
                evaluation = evaluate_douyin_candidates(
                    candidates=candidates,
                    histories=histories,
                    blacklist=blacklist,
                    evaluated_at=initial.evaluated_at,
                )
                if existing_rows:
                    await session.execute(
                        delete(task_targets).where(
                            task_targets.c.task_id == target_task.uuid,
                            task_targets.c.installation_id == target_installation.uuid,
                        )
                    )

                records: list[TaskTargetRecord] = []
                for ordinal, decision in enumerate(evaluation.decisions, start=1):
                    target_id_value = TargetId.new()
                    created = (
                        (
                            await session.execute(
                                insert(task_targets)
                                .values(
                                    id=target_id_value.uuid,
                                    task_id=target_task.uuid,
                                    installation_id=target_installation.uuid,
                                    ordinal=ordinal,
                                    platform_target_id=decision.candidate.platform_target_id,
                                    dedupe_key=str(decision.candidate.dedupe_key),
                                    display_name=decision.candidate.summary.display_name,
                                    public_handle=decision.candidate.summary.public_handle,
                                    source=decision.candidate.source.value,
                                    page_revision=decision.candidate.page_revision,
                                    disposition=decision.disposition.value,
                                    policy_version=evaluation.policy_version,
                                    evaluated_at=evaluation.evaluated_at,
                                    created_at=evaluation.evaluated_at,
                                )
                                .returning(*task_targets.c)
                            )
                        )
                        .mappings()
                        .one()
                    )
                    records.append(_record(created))
                return tuple(records)
        except (IntegrityError, InvalidDouyinCandidatePolicy):
            pass
        raise TaskTargetPersistenceRejected from None

    async def list_page(
        self,
        *,
        task_id: TaskId,
        installation_id: InstallationId,
        page_revision: int,
        after_ordinal: int | None,
        after_target_id: TargetId | None,
        limit: int,
    ) -> tuple[TaskTargetRecord, ...]:
        target_task, target_installation = _identity(task_id, installation_id)
        if (
            type(page_revision) is not int
            or not 1 <= page_revision <= MAX_EXECUTOR_SEQUENCE
            or type(limit) is not int
            or not 1 <= limit <= MAX_TASK_TARGET_LIMIT + 1
            or (after_ordinal is None) != (after_target_id is None)
        ):
            raise TaskTargetPersistenceRejected
        statement = select(task_targets).where(
            task_targets.c.task_id == target_task.uuid,
            task_targets.c.installation_id == target_installation.uuid,
            task_targets.c.page_revision == page_revision,
        )
        if after_ordinal is not None and after_target_id is not None:
            if (
                type(after_ordinal) is not int
                or not 1 <= after_ordinal <= MAX_TASK_TARGET_LIMIT
                or not isinstance(after_target_id, TargetId)
            ):
                raise TaskTargetPersistenceRejected
            statement = statement.where(
                or_(
                    task_targets.c.ordinal > after_ordinal,
                    and_(
                        task_targets.c.ordinal == after_ordinal,
                        task_targets.c.id > after_target_id.uuid,
                    ),
                )
            )
        statement = statement.order_by(task_targets.c.ordinal, task_targets.c.id).limit(limit)
        async with self._database.session() as session:
            rows = (await session.execute(statement)).mappings().all()
        return tuple(_record(row) for row in rows)


__all__ = ["SqlAlchemyTaskTargetRepository"]
