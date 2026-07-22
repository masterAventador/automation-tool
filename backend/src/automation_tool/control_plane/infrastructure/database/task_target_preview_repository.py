"""Atomic PostgreSQL target preview, exclusion, and confirmation repository."""

from __future__ import annotations

from datetime import datetime
from typing import cast

from sqlalchemy import and_, delete, func, insert, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from automation_tool.control_plane.application.task_target_previews import (
    TASK_TARGET_CONFIRMATION_INTENT_VERSION,
    PendingTaskTargetConfirmation,
    PendingTaskTargetExclusions,
    TaskTargetConfirmationIntent,
    TaskTargetPreviewConflict,
    TaskTargetPreviewItem,
    TaskTargetPreviewMutationResult,
    TaskTargetPreviewNotFound,
    TaskTargetPreviewSnapshot,
)
from automation_tool.control_plane.domain import (
    TERMINAL_EXECUTION_ATTEMPT_STATUSES,
    DouyinCandidateDisposition,
    DouyinSearchExposureAction,
    InstallationId,
    InstallationStatus,
    TargetId,
    TaskEventType,
    TaskEventVersion,
    TaskId,
    TaskStateMachine,
    TaskStatus,
)
from automation_tool.control_plane.infrastructure.database.schema import (
    douyin_search_exposure_definitions,
    execution_attempts,
    installations,
    task_events,
    task_target_confirmations,
    task_target_exclusions,
    task_targets,
    tasks,
)
from automation_tool.control_plane.infrastructure.database.session import Database
from automation_tool.control_plane.infrastructure.database.task_repository import (
    _record as task_record_from_row,
)
from automation_tool.control_plane.infrastructure.database.task_target_repository import (
    _record as target_record_from_row,
)


class SqlAlchemyTaskTargetPreviewRepository:
    def __init__(self, database: Database) -> None:
        if not isinstance(database, Database):
            raise TaskTargetPreviewConflict
        self._database = database

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
    ) -> TaskTargetPreviewSnapshot:
        if not isinstance(installation_id, InstallationId) or not isinstance(task_id, TaskId):
            raise TaskTargetPreviewNotFound
        async with self._database.session() as session:
            return await _read_snapshot(
                session,
                installation_id=installation_id,
                task_id=task_id,
                expected_page_revision=expected_page_revision,
                expected_task_revision=expected_task_revision,
                after_ordinal=after_ordinal,
                after_target_id=after_target_id,
                limit=limit,
            )

    async def replace_exclusions(
        self,
        pending: PendingTaskTargetExclusions,
    ) -> TaskTargetPreviewMutationResult:
        if not isinstance(pending, PendingTaskTargetExclusions):
            raise TaskTargetPreviewConflict
        async with self._database.session() as session:
            task_row = await _lock_mutable_preview(
                session,
                installation_id=pending.installation_id,
                task_id=pending.task_id,
                expected_task_revision=pending.expected_task_revision,
                page_revision=pending.page_revision,
            )
            replay = await _matching_event_replay(
                session,
                pending=pending,
                expected_event_type=TaskEventType.TASK_TARGET_SELECTION_UPDATED,
                current_task_revision=cast(int, task_row["revision"]),
            )
            if replay:
                if not await _exclusions_match(session, pending):
                    raise TaskTargetPreviewConflict
                return TaskTargetPreviewMutationResult(
                    snapshot=await _read_snapshot(
                        session,
                        installation_id=pending.installation_id,
                        task_id=pending.task_id,
                        expected_page_revision=pending.page_revision,
                        expected_task_revision=cast(int, task_row["revision"]),
                        after_ordinal=None,
                        after_target_id=None,
                        limit=101,
                    ),
                    replayed=True,
                )
            if cast(int, task_row["revision"]) != pending.expected_task_revision:
                raise TaskTargetPreviewConflict
            target_rows = (
                (
                    await session.execute(
                        select(task_targets)
                        .where(
                            task_targets.c.task_id == pending.task_id.uuid,
                            task_targets.c.installation_id == pending.installation_id.uuid,
                            task_targets.c.page_revision == pending.page_revision,
                        )
                        .order_by(task_targets.c.ordinal, task_targets.c.id)
                        .with_for_update()
                    )
                )
                .mappings()
                .all()
            )
            eligible = {
                TargetId.parse(row["id"])
                for row in target_rows
                if row["disposition"] == DouyinCandidateDisposition.ELIGIBLE.value
            }
            if not set(pending.excluded_target_ids).issubset(eligible):
                raise TaskTargetPreviewConflict
            if pending.requested_at < cast(datetime, task_row["updated_at"]):
                raise TaskTargetPreviewConflict
            await session.execute(
                delete(task_target_exclusions).where(
                    task_target_exclusions.c.task_id == pending.task_id.uuid,
                    task_target_exclusions.c.installation_id == pending.installation_id.uuid,
                    task_target_exclusions.c.page_revision == pending.page_revision,
                )
            )
            if pending.excluded_target_ids:
                await session.execute(
                    insert(task_target_exclusions),
                    [
                        {
                            "target_id": target_id.uuid,
                            "task_id": pending.task_id.uuid,
                            "installation_id": pending.installation_id.uuid,
                            "page_revision": pending.page_revision,
                            "excluded_at": pending.requested_at,
                        }
                        for target_id in pending.excluded_target_ids
                    ],
                )
            updated_task = await _append_preview_event(
                session,
                task_row=task_row,
                source_message_id=pending.source_message_id,
                source_idempotency_key=pending.idempotency_key,
                source_fingerprint=pending.fingerprint(),
                event_type=TaskEventType.TASK_TARGET_SELECTION_UPDATED,
                target_status=TaskStatus.AWAITING_CONFIRMATION,
                occurred_at=pending.requested_at,
            )
            return TaskTargetPreviewMutationResult(
                snapshot=await _read_snapshot(
                    session,
                    installation_id=pending.installation_id,
                    task_id=pending.task_id,
                    expected_page_revision=pending.page_revision,
                    expected_task_revision=cast(int, updated_task["revision"]),
                    after_ordinal=None,
                    after_target_id=None,
                    limit=101,
                ),
                replayed=False,
            )

    async def confirm(
        self,
        pending: PendingTaskTargetConfirmation,
    ) -> TaskTargetPreviewMutationResult:
        if not isinstance(pending, PendingTaskTargetConfirmation):
            raise TaskTargetPreviewConflict
        async with self._database.session() as session:
            task_row = await _lock_preview_task(
                session,
                installation_id=pending.installation_id,
                task_id=pending.task_id,
            )
            replay = await _matching_event_replay(
                session,
                pending=pending,
                expected_event_type=TaskEventType.TASK_TARGETS_CONFIRMED,
                current_task_revision=cast(int, task_row["revision"]),
            )
            if replay:
                confirmation = await session.scalar(
                    select(task_target_confirmations.c.task_id).where(
                        task_target_confirmations.c.task_id == pending.task_id.uuid,
                        task_target_confirmations.c.installation_id == pending.installation_id.uuid,
                        task_target_confirmations.c.page_revision == pending.page_revision,
                        task_target_confirmations.c.selection_task_revision
                        == pending.expected_task_revision,
                    )
                )
                if confirmation is None:
                    raise TaskTargetPreviewConflict
                return TaskTargetPreviewMutationResult(
                    snapshot=await _read_snapshot(
                        session,
                        installation_id=pending.installation_id,
                        task_id=pending.task_id,
                        expected_page_revision=pending.page_revision,
                        expected_task_revision=cast(int, task_row["revision"]),
                        after_ordinal=None,
                        after_target_id=None,
                        limit=101,
                    ),
                    replayed=True,
                )
            if (
                task_row["status"] != TaskStatus.AWAITING_CONFIRMATION.value
                or cast(int, task_row["revision"]) != pending.expected_task_revision
                or pending.requested_at < cast(datetime, task_row["updated_at"])
            ):
                raise TaskTargetPreviewConflict
            page_bounds = await _page_bounds(
                session,
                installation_id=pending.installation_id,
                task_id=pending.task_id,
                lock=True,
            )
            if page_bounds != (pending.page_revision, pending.page_revision):
                raise TaskTargetPreviewConflict
            selected_target_ids = await _selected_target_ids(
                session,
                installation_id=pending.installation_id,
                task_id=pending.task_id,
                page_revision=pending.page_revision,
            )
            if not selected_target_ids:
                raise TaskTargetPreviewConflict
            definition = await _read_definition(
                session,
                installation_id=pending.installation_id,
                task_id=pending.task_id,
                lock=True,
            )
            intent = _confirmation_intent(
                installation_id=pending.installation_id,
                task_id=pending.task_id,
                page_revision=pending.page_revision,
                confirmation_revision=pending.expected_task_revision,
                definition=definition,
                selected_target_ids=selected_target_ids,
            )
            next_revision = pending.expected_task_revision + 1
            await session.execute(
                insert(task_target_confirmations).values(
                    task_id=pending.task_id.uuid,
                    installation_id=pending.installation_id.uuid,
                    page_revision=pending.page_revision,
                    selection_task_revision=pending.expected_task_revision,
                    confirmed_task_revision=next_revision,
                    selected_target_count=intent.selected_target_count,
                    action=intent.action.value,
                    message_template=intent.message_template,
                    intent_version=TASK_TARGET_CONFIRMATION_INTENT_VERSION,
                    intent_fingerprint=intent.fingerprint(),
                    source_message_id=pending.source_message_id,
                    source_idempotency_key=pending.idempotency_key,
                    source_fingerprint=pending.fingerprint(),
                    confirmed_at=pending.requested_at,
                    created_at=pending.requested_at,
                )
            )
            updated_task = await _append_preview_event(
                session,
                task_row=task_row,
                source_message_id=pending.source_message_id,
                source_idempotency_key=pending.idempotency_key,
                source_fingerprint=pending.fingerprint(),
                event_type=TaskEventType.TASK_TARGETS_CONFIRMED,
                target_status=TaskStatus.QUEUED,
                occurred_at=pending.requested_at,
                clear_current_attempt=True,
            )
            return TaskTargetPreviewMutationResult(
                snapshot=await _read_snapshot(
                    session,
                    installation_id=pending.installation_id,
                    task_id=pending.task_id,
                    expected_page_revision=pending.page_revision,
                    expected_task_revision=cast(int, updated_task["revision"]),
                    after_ordinal=None,
                    after_target_id=None,
                    limit=101,
                ),
                replayed=False,
            )


async def _lock_preview_task(
    session: AsyncSession,
    *,
    installation_id: InstallationId,
    task_id: TaskId,
) -> RowMapping:
    status = await session.scalar(
        select(installations.c.status)
        .where(installations.c.id == installation_id.uuid)
        .with_for_update()
    )
    if status != InstallationStatus.ACTIVE.value:
        raise TaskTargetPreviewNotFound
    row = (
        (
            await session.execute(
                select(tasks)
                .where(
                    tasks.c.id == task_id.uuid,
                    tasks.c.installation_id == installation_id.uuid,
                )
                .with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise TaskTargetPreviewNotFound
    return row


async def _lock_mutable_preview(
    session: AsyncSession,
    *,
    installation_id: InstallationId,
    task_id: TaskId,
    expected_task_revision: int,
    page_revision: int,
) -> RowMapping:
    row = await _lock_preview_task(
        session,
        installation_id=installation_id,
        task_id=task_id,
    )
    if row["status"] != TaskStatus.AWAITING_CONFIRMATION.value:
        raise TaskTargetPreviewConflict
    bounds = await _page_bounds(
        session,
        installation_id=installation_id,
        task_id=task_id,
        lock=True,
    )
    if bounds != (page_revision, page_revision):
        raise TaskTargetPreviewConflict
    if cast(int, row["revision"]) < expected_task_revision:
        raise TaskTargetPreviewConflict
    return row


async def _page_bounds(
    session: AsyncSession,
    *,
    installation_id: InstallationId,
    task_id: TaskId,
    lock: bool,
) -> tuple[int | None, int | None]:
    if lock:
        await session.execute(
            select(task_targets.c.id)
            .where(
                task_targets.c.task_id == task_id.uuid,
                task_targets.c.installation_id == installation_id.uuid,
            )
            .with_for_update()
        )
    row = (
        await session.execute(
            select(
                func.min(task_targets.c.page_revision),
                func.max(task_targets.c.page_revision),
            ).where(
                task_targets.c.task_id == task_id.uuid,
                task_targets.c.installation_id == installation_id.uuid,
            )
        )
    ).one()
    return cast(int | None, row[0]), cast(int | None, row[1])


async def _read_definition(
    session: AsyncSession,
    *,
    installation_id: InstallationId,
    task_id: TaskId,
    lock: bool,
) -> RowMapping:
    statement = select(
        douyin_search_exposure_definitions.c.action,
        douyin_search_exposure_definitions.c.message_template,
    ).where(
        douyin_search_exposure_definitions.c.task_id == task_id.uuid,
        douyin_search_exposure_definitions.c.installation_id == installation_id.uuid,
    )
    if lock:
        statement = statement.with_for_update()
    row = (await session.execute(statement)).mappings().one_or_none()
    if row is None:
        raise TaskTargetPreviewConflict
    return row


async def _selected_target_ids(
    session: AsyncSession,
    *,
    installation_id: InstallationId,
    task_id: TaskId,
    page_revision: int,
) -> tuple[TargetId, ...]:
    values = await session.scalars(
        select(task_targets.c.id)
        .select_from(
            task_targets.outerjoin(
                task_target_exclusions,
                and_(
                    task_target_exclusions.c.target_id == task_targets.c.id,
                    task_target_exclusions.c.task_id == task_targets.c.task_id,
                    task_target_exclusions.c.installation_id == task_targets.c.installation_id,
                    task_target_exclusions.c.page_revision == task_targets.c.page_revision,
                ),
            )
        )
        .where(
            task_targets.c.task_id == task_id.uuid,
            task_targets.c.installation_id == installation_id.uuid,
            task_targets.c.page_revision == page_revision,
            task_targets.c.disposition == DouyinCandidateDisposition.ELIGIBLE.value,
            task_target_exclusions.c.target_id.is_(None),
        )
        .order_by(task_targets.c.ordinal, task_targets.c.id)
    )
    return tuple(TargetId.parse(value) for value in values)


def _confirmation_intent(
    *,
    installation_id: InstallationId,
    task_id: TaskId,
    page_revision: int,
    confirmation_revision: int,
    definition: RowMapping,
    selected_target_ids: tuple[TargetId, ...],
) -> TaskTargetConfirmationIntent:
    try:
        return TaskTargetConfirmationIntent(
            installation_id=installation_id,
            task_id=task_id,
            page_revision=page_revision,
            confirmation_revision=confirmation_revision,
            action=DouyinSearchExposureAction(cast(str, definition["action"])),
            message_template=cast(str | None, definition["message_template"]),
            selected_target_ids=selected_target_ids,
        )
    except (KeyError, TypeError, ValueError):
        raise TaskTargetPreviewConflict from None


async def _read_snapshot(
    session: AsyncSession,
    *,
    installation_id: InstallationId,
    task_id: TaskId,
    expected_page_revision: int | None,
    expected_task_revision: int | None,
    after_ordinal: int | None,
    after_target_id: TargetId | None,
    limit: int,
) -> TaskTargetPreviewSnapshot:
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
        raise TaskTargetPreviewNotFound
    if expected_task_revision is not None and task_row["revision"] != expected_task_revision:
        raise TaskTargetPreviewConflict
    minimum, maximum = await _page_bounds(
        session,
        installation_id=installation_id,
        task_id=task_id,
        lock=False,
    )
    if minimum is None or minimum != maximum:
        raise TaskTargetPreviewNotFound
    if expected_page_revision is not None and maximum != expected_page_revision:
        raise TaskTargetPreviewConflict
    definition = await _read_definition(
        session,
        installation_id=installation_id,
        task_id=task_id,
        lock=False,
    )
    statement = (
        select(task_targets, task_target_exclusions.c.target_id.label("excluded_target_id"))
        .select_from(
            task_targets.outerjoin(
                task_target_exclusions,
                and_(
                    task_target_exclusions.c.target_id == task_targets.c.id,
                    task_target_exclusions.c.task_id == task_targets.c.task_id,
                    task_target_exclusions.c.installation_id == task_targets.c.installation_id,
                    task_target_exclusions.c.page_revision == task_targets.c.page_revision,
                ),
            )
        )
        .where(
            task_targets.c.task_id == task_id.uuid,
            task_targets.c.installation_id == installation_id.uuid,
            task_targets.c.page_revision == maximum,
        )
    )
    if after_ordinal is not None and after_target_id is not None:
        statement = statement.where(
            (task_targets.c.ordinal > after_ordinal)
            | and_(
                task_targets.c.ordinal == after_ordinal,
                task_targets.c.id > after_target_id.uuid,
            )
        )
    rows = (
        (
            await session.execute(
                statement.order_by(task_targets.c.ordinal, task_targets.c.id).limit(limit)
            )
        )
        .mappings()
        .all()
    )
    items = tuple(
        TaskTargetPreviewItem(
            target=target_record_from_row(row),
            user_excluded=row["excluded_target_id"] is not None,
        )
        for row in rows
    )
    counts = (
        await session.execute(
            select(
                func.count()
                .filter(
                    task_targets.c.disposition == DouyinCandidateDisposition.ELIGIBLE.value,
                    task_target_exclusions.c.target_id.is_(None),
                )
                .label("selected_count"),
                func.count(task_target_exclusions.c.target_id).label("excluded_count"),
            )
            .select_from(
                task_targets.outerjoin(
                    task_target_exclusions,
                    and_(
                        task_target_exclusions.c.target_id == task_targets.c.id,
                        task_target_exclusions.c.task_id == task_targets.c.task_id,
                        task_target_exclusions.c.installation_id == task_targets.c.installation_id,
                        task_target_exclusions.c.page_revision == task_targets.c.page_revision,
                    ),
                )
            )
            .where(
                task_targets.c.task_id == task_id.uuid,
                task_targets.c.installation_id == installation_id.uuid,
                task_targets.c.page_revision == maximum,
            )
        )
    ).one()
    confirmation = (
        (
            await session.execute(
                select(task_target_confirmations).where(
                    task_target_confirmations.c.task_id == task_id.uuid,
                    task_target_confirmations.c.installation_id == installation_id.uuid,
                    task_target_confirmations.c.page_revision == maximum,
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    confirmed_at = None if confirmation is None else confirmation["confirmed_at"]
    task_status = TaskStatus(cast(str, task_row["status"]))
    if (confirmed_at is None and task_status is not TaskStatus.AWAITING_CONFIRMATION) or (
        confirmed_at is not None
        and task_status
        in {
            TaskStatus.DRAFT,
            TaskStatus.VALIDATING,
            TaskStatus.DISCOVERING_TARGETS,
            TaskStatus.AWAITING_CONFIRMATION,
        }
    ):
        raise TaskTargetPreviewConflict
    selected_target_ids = await _selected_target_ids(
        session,
        installation_id=installation_id,
        task_id=task_id,
        page_revision=maximum,
    )
    if confirmation is None:
        confirmation_revision = cast(int, task_row["revision"])
        action = DouyinSearchExposureAction(cast(str, definition["action"]))
        message_template = cast(str | None, definition["message_template"])
    else:
        intent = _confirmation_intent(
            installation_id=installation_id,
            task_id=task_id,
            page_revision=maximum,
            confirmation_revision=cast(int, confirmation["selection_task_revision"]),
            definition=definition,
            selected_target_ids=selected_target_ids,
        )
        if (
            confirmation["action"] != intent.action.value
            or confirmation["message_template"] != intent.message_template
            or confirmation["intent_version"] != TASK_TARGET_CONFIRMATION_INTENT_VERSION
            or bytes(confirmation["intent_fingerprint"]) != intent.fingerprint()
            or confirmation["selected_target_count"] != intent.selected_target_count
        ):
            raise TaskTargetPreviewConflict
        confirmation_revision = intent.confirmation_revision
        action = intent.action
        message_template = intent.message_template
    return TaskTargetPreviewSnapshot(
        task=task_record_from_row(task_row),
        page_revision=maximum,
        confirmation_revision=confirmation_revision,
        action=action,
        message_template=message_template,
        items=items,
        selected_target_count=cast(int, counts.selected_count),
        user_excluded_target_count=cast(int, counts.excluded_count),
        confirmed_at=cast(datetime | None, confirmed_at),
    )


async def _matching_event_replay(
    session: AsyncSession,
    *,
    pending: PendingTaskTargetExclusions | PendingTaskTargetConfirmation,
    expected_event_type: TaskEventType,
    current_task_revision: int,
) -> bool:
    event = (
        (
            await session.execute(
                select(task_events).where(
                    task_events.c.installation_id == pending.installation_id.uuid,
                    task_events.c.source_idempotency_key == pending.idempotency_key,
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if event is None:
        return False
    if (
        event["task_id"] != pending.task_id.uuid
        or event["event_type"] != expected_event_type.value
        or bytes(event["source_fingerprint"]) != pending.fingerprint()
        or cast(int, event["task_revision"]) != current_task_revision
    ):
        raise TaskTargetPreviewConflict
    return True


async def _exclusions_match(
    session: AsyncSession,
    pending: PendingTaskTargetExclusions,
) -> bool:
    values = set(
        TargetId.parse(value)
        for value in await session.scalars(
            select(task_target_exclusions.c.target_id).where(
                task_target_exclusions.c.task_id == pending.task_id.uuid,
                task_target_exclusions.c.installation_id == pending.installation_id.uuid,
                task_target_exclusions.c.page_revision == pending.page_revision,
            )
        )
    )
    return values == set(pending.excluded_target_ids)


async def _append_preview_event(
    session: AsyncSession,
    *,
    task_row: RowMapping,
    source_message_id: object,
    source_idempotency_key: str,
    source_fingerprint: bytes,
    event_type: TaskEventType,
    target_status: TaskStatus,
    occurred_at: datetime,
    clear_current_attempt: bool = False,
) -> RowMapping:
    current_status = TaskStatus(cast(str, task_row["status"]))
    if target_status is not current_status:
        TaskStateMachine.transition(current_status, target_status)
    if clear_current_attempt and task_row["current_attempt_id"] is not None:
        attempt_status = await session.scalar(
            select(execution_attempts.c.status).where(
                execution_attempts.c.id == task_row["current_attempt_id"],
                execution_attempts.c.task_id == task_row["id"],
                execution_attempts.c.installation_id == task_row["installation_id"],
            )
        )
        if attempt_status not in {status.value for status in TERMINAL_EXECUTION_ATTEMPT_STATUSES}:
            raise TaskTargetPreviewConflict
    next_revision = cast(int, task_row["revision"]) + 1
    next_sequence = cast(int, task_row["last_event_sequence"]) + 1
    await session.execute(
        insert(task_events).values(
            task_id=task_row["id"],
            installation_id=task_row["installation_id"],
            sequence=next_sequence,
            event_version=TaskEventVersion.V1.value,
            event_type=event_type.value,
            task_revision=next_revision,
            task_status=target_status.value,
            execution_attempt_id=None,
            action_id=None,
            source_message_id=source_message_id,
            source_idempotency_key=source_idempotency_key,
            source_fingerprint=source_fingerprint,
            progress_percent=None,
            occurred_at=occurred_at,
            recorded_at=occurred_at,
            safe_message=None,
        )
    )
    statement = (
        update(tasks)
        .where(
            tasks.c.id == task_row["id"],
            tasks.c.installation_id == task_row["installation_id"],
            tasks.c.revision == task_row["revision"],
            tasks.c.last_event_sequence == task_row["last_event_sequence"],
        )
        .values(
            status=target_status.value,
            revision=next_revision,
            last_event_sequence=next_sequence,
            updated_at=occurred_at,
        )
    )
    if clear_current_attempt:
        statement = statement.values(current_attempt_id=None)
    return (await session.execute(statement.returning(*tasks.c))).mappings().one()


__all__ = ["SqlAlchemyTaskTargetPreviewRepository"]
