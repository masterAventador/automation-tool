"""PostgreSQL lifecycle for confirmed one-at-a-time action execution."""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from conftest import AlembicRunner
from sqlalchemy import delete, func, insert, select, update

from automation_tool.control_plane.application.action_execution_orchestration import (
    ActionExecutionAdvanceKind,
    ActionExecutionLimits,
    ActionExecutionOrchestrationRejected,
    PendingActionExecutionAdvance,
)
from automation_tool.control_plane.application.task_command_delivery import (
    ActionCommandContext,
    TaskCommandDeliveryRejected,
)
from automation_tool.control_plane.application.task_target_previews import (
    TASK_TARGET_CONFIRMATION_INTENT_VERSION,
    TaskTargetConfirmationIntent,
)
from automation_tool.control_plane.domain import (
    DOUYIN_CANDIDATE_POLICY_VERSION,
    DOUYIN_SEARCH_EXPOSURE_TEMPLATE,
    ActionId,
    ActionOutcome,
    ActionStatus,
    DouyinCandidateDisposition,
    DouyinSearchExposureAction,
    ExecutionAttemptStatus,
    ExecutorId,
    InstallationId,
    TargetId,
    TaskCommandResponseType,
    TaskCommandStatus,
    TaskCommandType,
    TaskId,
    TaskStatus,
)
from automation_tool.control_plane.infrastructure.database import (
    Database,
    SqlAlchemyActionExecutionOrchestrationRepository,
    SqlAlchemyTaskCommandRepository,
    action_failure_circuits,
    action_risk_authorizations,
    action_risk_results,
    douyin_search_exposure_definitions,
    execution_attempts,
    installations,
    platform_session_gates,
    platform_session_health,
    task_actions,
    task_commands,
    task_events,
    task_target_confirmations,
    task_target_exclusions,
    task_targets,
    tasks,
)
from automation_tool.control_plane.infrastructure.database import (
    task_command_repository as task_command_repository_module,
)
from automation_tool.protocol import (
    ActionResultEvidence,
    DouyinCandidate,
    DouyinCandidateSource,
    DouyinCandidateSummary,
    PlatformSessionState,
    TaskCommandResultEnvelope,
)

NOW = datetime(2026, 7, 21, 7, 0, tzinfo=UTC)
EXECUTOR_ID = ExecutorId.parse("123e4567-e89b-42d3-a456-426614174001")


async def reset_data(database: Database) -> None:
    async with database.session() as session:
        await session.execute(delete(action_failure_circuits))
        await session.execute(delete(action_risk_results))
        await session.execute(delete(task_events))
        await session.execute(delete(task_commands))
        await session.execute(delete(action_risk_authorizations))
        await session.execute(delete(task_actions))
        await session.execute(update(tasks).values(current_attempt_id=None))
        await session.execute(delete(execution_attempts))
        await session.execute(delete(task_target_confirmations))
        await session.execute(delete(task_target_exclusions))
        await session.execute(delete(task_targets))
        await session.execute(delete(douyin_search_exposure_definitions))
        await session.execute(delete(platform_session_gates))
        await session.execute(delete(platform_session_health))
        await session.execute(delete(tasks))
        await session.execute(delete(installations))


async def seed_confirmed_task(database: Database) -> tuple[InstallationId, TaskId]:
    installation_id = InstallationId.new()
    task_id = TaskId.new()
    target_ids = tuple(uuid4() for _ in range(2))
    message_template = "您好 {{target_display_name}}"
    intent = TaskTargetConfirmationIntent(
        installation_id=installation_id,
        task_id=task_id,
        page_revision=1,
        confirmation_revision=1,
        action=DouyinSearchExposureAction.COMMENT,
        message_template=message_template,
        selected_target_ids=tuple(TargetId.parse(value) for value in target_ids),
    )
    async with database.session() as session:
        await session.execute(
            insert(installations).values(
                id=installation_id.uuid,
                device_public_key=secrets.token_bytes(32),
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.execute(
            insert(platform_session_health).values(
                installation_id=installation_id.uuid,
                platform="douyin",
                state=PlatformSessionState.HEALTHY.value,
                session_revision=1,
                observed_at=NOW,
                updated_at=NOW,
            )
        )
        await session.execute(
            insert(tasks).values(
                id=task_id.uuid,
                installation_id=installation_id.uuid,
                creation_idempotency_key=f"task:h816c:{task_id}",
                status=TaskStatus.QUEUED.value,
                revision=2,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.execute(
            insert(douyin_search_exposure_definitions).values(
                task_id=task_id.uuid,
                installation_id=installation_id.uuid,
                template=DOUYIN_SEARCH_EXPOSURE_TEMPLATE,
                search_keyword="新能源汽车",
                action=DouyinSearchExposureAction.COMMENT.value,
                message_template=message_template,
                target_limit=2,
                minimum_interval_seconds=2,
                maximum_interval_seconds=10,
                preview_required=True,
                final_confirmation_required=True,
            )
        )
        for ordinal, target_id in enumerate(target_ids, start=1):
            candidate = DouyinCandidate(
                platform_target_id=f"douyin-user-{ordinal}",
                summary=DouyinCandidateSummary(
                    display_name=f"目标 {ordinal}",
                    public_handle=f"target-{ordinal}",
                ),
                source=DouyinCandidateSource.GENERAL_SEARCH_AUTHOR,
                page_revision=1,
            )
            await session.execute(
                insert(task_targets).values(
                    id=target_id,
                    task_id=task_id.uuid,
                    installation_id=installation_id.uuid,
                    ordinal=ordinal,
                    platform_target_id=candidate.platform_target_id,
                    dedupe_key=str(candidate.dedupe_key),
                    display_name=candidate.summary.display_name,
                    public_handle=candidate.summary.public_handle,
                    source=candidate.source.value,
                    page_revision=candidate.page_revision,
                    disposition=DouyinCandidateDisposition.ELIGIBLE.value,
                    policy_version=DOUYIN_CANDIDATE_POLICY_VERSION,
                    evaluated_at=NOW,
                    created_at=NOW,
                )
            )
        await session.execute(
            insert(task_target_confirmations).values(
                task_id=task_id.uuid,
                installation_id=installation_id.uuid,
                page_revision=1,
                selection_task_revision=1,
                confirmed_task_revision=2,
                selected_target_count=2,
                action=intent.action.value,
                message_template=intent.message_template,
                intent_version=TASK_TARGET_CONFIRMATION_INTENT_VERSION,
                intent_fingerprint=intent.fingerprint(),
                source_message_id=uuid4(),
                source_idempotency_key=f"target:confirm:h816c:{task_id}",
                source_fingerprint=secrets.token_bytes(32),
                confirmed_at=NOW,
                created_at=NOW,
            )
        )
    return installation_id, task_id


def pending(
    installation_id: InstallationId,
    requested_at: datetime,
) -> PendingActionExecutionAdvance:
    return PendingActionExecutionAdvance(
        installation_id=installation_id,
        executor_id=EXECUTOR_ID,
        execution_attempt_id=uuid4(),
        action_id=uuid4(),
        message_id=uuid4(),
        correlation_id=uuid4(),
        limits=ActionExecutionLimits(
            minimum_interval_seconds=2,
            task_action_limit=2,
            daily_action_limit=10,
            consecutive_failure_threshold=3,
        ),
        requested_at=requested_at,
        command_deadline_at=requested_at + timedelta(minutes=5),
    )


@pytest.mark.asyncio
async def test_confirmed_task_is_offered_then_authorized_one_target_at_a_time(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        installation_id, task_id = await seed_confirmed_task(database)
        repository = SqlAlchemyActionExecutionOrchestrationRepository(database)

        offered = await repository.advance(pending(installation_id, NOW))
        assert offered.kind is ActionExecutionAdvanceKind.TASK_OFFERED
        async with database.session() as session:
            attempt_id = await session.scalar(
                select(execution_attempts.c.id).where(execution_attempts.c.task_id == task_id.uuid)
            )
            offer = (
                (
                    await session.execute(
                        select(task_commands).where(
                            task_commands.c.command_type == TaskCommandType.TASK_OFFER.value
                        )
                    )
                )
                .mappings()
                .one()
            )
            assert attempt_id is not None
            assert offer["target_confirmation_message_id"] is not None
            assert offer["action_id"] is None
            await session.execute(
                update(execution_attempts)
                .where(execution_attempts.c.id == attempt_id)
                .values(
                    status=ExecutionAttemptStatus.RUNNING.value,
                    revision=2,
                    started_at=NOW + timedelta(seconds=1),
                    updated_at=NOW + timedelta(seconds=1),
                )
            )
            await session.execute(
                update(tasks)
                .where(tasks.c.id == task_id.uuid)
                .values(
                    status=TaskStatus.RUNNING.value,
                    updated_at=NOW + timedelta(seconds=1),
                )
            )
            await session.execute(
                update(task_commands)
                .where(task_commands.c.message_id == offer["message_id"])
                .values(
                    status=TaskCommandStatus.ACKNOWLEDGED.value,
                    revision=3,
                    delivery_attempts=1,
                    next_delivery_at=None,
                    delivered_at=NOW + timedelta(milliseconds=500),
                    acknowledged_at=NOW + timedelta(milliseconds=600),
                    response_message_id=uuid4(),
                    response_type=TaskCommandResponseType.TASK_ACCEPT.value,
                    updated_at=NOW + timedelta(milliseconds=600),
                )
            )

        first = await repository.advance(pending(installation_id, NOW + timedelta(seconds=2)))
        assert first.kind is ActionExecutionAdvanceKind.ACTION_ENQUEUED

        async with database.session() as session:
            first_action_id = await session.scalar(
                select(task_actions.c.id).order_by(task_actions.c.ordinal)
            )
            assert first_action_id is not None
            await session.execute(
                delete(task_commands).where(task_commands.c.action_id == first_action_id)
            )
        healed = await repository.advance(
            pending(installation_id, NOW + timedelta(seconds=2, milliseconds=10))
        )
        assert healed.kind is ActionExecutionAdvanceKind.ACTION_ENQUEUED

        command_repository = SqlAlchemyTaskCommandRepository(database)
        claimed = await command_repository.claim_next(
            installation_id=installation_id,
            now=NOW + timedelta(seconds=2, milliseconds=50),
            lease_expires_at=NOW + timedelta(seconds=12),
            retry_delivered_before=NOW,
            recover_delivered=False,
        )
        assert claimed is not None
        assert claimed.command_type is TaskCommandType.ACTION_EXECUTE
        assert isinstance(claimed.action_context, ActionCommandContext)
        assert claimed.action_context.candidate.platform_target_id == "douyin-user-1"
        assert claimed.action_context.message_template == "您好 {{target_display_name}}"
        delivered = await command_repository.mark_delivered(
            message_id=claimed.message_id,
            expected_revision=claimed.revision,
            delivered_at=NOW + timedelta(seconds=2, milliseconds=60),
        )
        accepted = await command_repository.acknowledge(
            response=TaskCommandResultEnvelope.model_validate(
                {
                    "protocol_version": "1.0",
                    "message_id": str(uuid4()),
                    "message_type": "action.accept",
                    "sent_at": NOW + timedelta(seconds=2, milliseconds=70),
                    "deadline_at": NOW + timedelta(seconds=32),
                    "installation_id": str(installation_id),
                    "executor_id": str(EXECUTOR_ID),
                    "correlation_id": str(delivered.correlation_id),
                    "idempotency_key": f"action:accept:{delivered.action_id}",
                    "sequence": delivered.sequence,
                    "payload": {"accepted": True},
                    "task_id": str(task_id),
                    "execution_attempt_id": str(delivered.execution_attempt_id),
                }
            ),
            received_at=NOW + timedelta(seconds=2, milliseconds=80),
        )
        assert accepted.status is TaskCommandStatus.ACKNOWLEDGED
        assert accepted.response_type is TaskCommandResponseType.ACTION_ACCEPT

        still_waiting = await repository.advance(
            pending(installation_id, NOW + timedelta(seconds=2, milliseconds=100))
        )
        assert still_waiting.kind is ActionExecutionAdvanceKind.IDLE

        async with database.session() as session:
            await session.execute(
                update(task_actions)
                .where(task_actions.c.id == first_action_id)
                .values(
                    status=ActionStatus.VERIFIED.value,
                    outcome=ActionOutcome.SUCCEEDED.value,
                    evidence_code=ActionResultEvidence.COMMENT_CONFIRMED.value,
                    revision=2,
                    updated_at=NOW + timedelta(seconds=2, milliseconds=500),
                    finished_at=NOW + timedelta(seconds=2, milliseconds=500),
                )
            )

        limited = await repository.advance(pending(installation_id, NOW + timedelta(seconds=3)))
        assert limited.kind is ActionExecutionAdvanceKind.RATE_LIMITED
        concurrent = await asyncio.gather(
            repository.advance(pending(installation_id, NOW + timedelta(seconds=4))),
            repository.advance(pending(installation_id, NOW + timedelta(seconds=4))),
        )
        assert sorted(result.kind for result in concurrent) == [
            ActionExecutionAdvanceKind.ACTION_ENQUEUED,
            ActionExecutionAdvanceKind.RATE_LIMITED,
        ]

        async with database.session() as session:
            assert await session.scalar(select(func.count()).select_from(task_actions)) == 2
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(task_commands)
                    .where(task_commands.c.command_type == TaskCommandType.ACTION_EXECUTE.value)
                )
                == 2
            )
            commands = (
                (
                    await session.execute(
                        select(task_commands)
                        .where(task_commands.c.command_type == TaskCommandType.ACTION_EXECUTE.value)
                        .order_by(task_commands.c.sequence)
                    )
                )
                .mappings()
                .all()
            )
            assert [command["status"] for command in commands] == [
                TaskCommandStatus.ACKNOWLEDGED.value,
                TaskCommandStatus.PENDING.value,
            ]
            assert all(
                command["action_id"] is not None
                and command["target_confirmation_message_id"] is not None
                and command["idempotency_key"] == f"action:{command['action_id']}"
                for command in commands
            )

        second_action_id = ActionId.parse(commands[1]["action_id"])
        duplicate = await repository._enqueue_authorized(
            pending(installation_id, NOW + timedelta(seconds=4, milliseconds=100)),
            action_id=second_action_id,
        )
        assert duplicate.kind is ActionExecutionAdvanceKind.ACTION_ENQUEUED
        with pytest.raises(ActionExecutionOrchestrationRejected):
            await repository._enqueue_authorized(
                pending(installation_id, NOW + timedelta(seconds=4, milliseconds=200)),
                action_id=ActionId.new(),
            )
        async with database.session() as session:
            await session.execute(
                delete(task_commands).where(task_commands.c.action_id == second_action_id.uuid)
            )
        with pytest.raises(ActionExecutionOrchestrationRejected):
            await repository._enqueue_authorized(
                pending(installation_id, NOW + timedelta(minutes=10)),
                action_id=second_action_id,
            )
        async with database.session() as session:
            await session.execute(
                update(installations)
                .where(installations.c.id == installation_id.uuid)
                .values(
                    status="revoked",
                    revoked_at=NOW + timedelta(minutes=11),
                    updated_at=NOW + timedelta(minutes=11),
                )
            )
        with pytest.raises(ActionExecutionOrchestrationRejected):
            await repository._enqueue_authorized(
                pending(installation_id, NOW + timedelta(minutes=12)),
                action_id=second_action_id,
            )
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_orchestrator_idles_for_every_non_executable_database_state(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        repository = SqlAlchemyActionExecutionOrchestrationRepository(database)
        missing = await repository.advance(pending(InstallationId.new(), NOW))
        assert missing.kind is ActionExecutionAdvanceKind.IDLE

        installation_id, task_id = await seed_confirmed_task(database)
        async with database.session() as session:
            await session.execute(
                update(platform_session_health)
                .where(platform_session_health.c.installation_id == installation_id.uuid)
                .values(state=PlatformSessionState.RISK.value)
            )
        unhealthy = await repository.advance(pending(installation_id, NOW))
        assert unhealthy.kind is ActionExecutionAdvanceKind.IDLE

        async with database.session() as session:
            await session.execute(
                update(platform_session_health)
                .where(platform_session_health.c.installation_id == installation_id.uuid)
                .values(state=PlatformSessionState.HEALTHY.value)
            )
            await session.execute(
                insert(platform_session_gates).values(
                    installation_id=installation_id.uuid,
                    platform="douyin",
                    state="blocked",
                    session_revision=1,
                    updated_at=NOW,
                )
            )
        gated = await repository.advance(pending(installation_id, NOW))
        assert gated.kind is ActionExecutionAdvanceKind.IDLE

        async with database.session() as session:
            await session.execute(delete(platform_session_gates))
            await session.execute(
                update(tasks).where(tasks.c.id == task_id.uuid).values(revision=3)
            )
        no_matching_queue = await repository.advance(pending(installation_id, NOW))
        assert no_matching_queue.kind is ActionExecutionAdvanceKind.IDLE

        async with database.session() as session:
            await session.execute(
                update(tasks).where(tasks.c.id == task_id.uuid).values(revision=2)
            )
        offered = await repository.advance(pending(installation_id, NOW))
        assert offered.kind is ActionExecutionAdvanceKind.TASK_OFFERED
        not_started = await repository.advance(
            pending(installation_id, NOW + timedelta(milliseconds=100))
        )
        assert not_started.kind is ActionExecutionAdvanceKind.IDLE

        async with database.session() as session:
            attempt_id = await session.scalar(
                select(execution_attempts.c.id).where(execution_attempts.c.task_id == task_id.uuid)
            )
            assert attempt_id is not None
            await session.execute(
                update(execution_attempts)
                .where(execution_attempts.c.id == attempt_id)
                .values(status=ExecutionAttemptStatus.RUNNING.value, revision=2)
            )
        task_not_running = await repository.advance(
            pending(installation_id, NOW + timedelta(milliseconds=200))
        )
        assert task_not_running.kind is ActionExecutionAdvanceKind.IDLE

        async with database.session() as session:
            await session.execute(
                update(tasks)
                .where(tasks.c.id == task_id.uuid)
                .values(status=TaskStatus.RUNNING.value, current_attempt_id=None)
            )
        wrong_current_attempt = await repository.advance(
            pending(installation_id, NOW + timedelta(milliseconds=300))
        )
        assert wrong_current_attempt.kind is ActionExecutionAdvanceKind.IDLE

        async with database.session() as session:
            await session.execute(
                update(tasks)
                .where(tasks.c.id == task_id.uuid)
                .values(current_attempt_id=attempt_id)
            )
            await session.execute(
                update(task_targets)
                .where(task_targets.c.task_id == task_id.uuid)
                .values(disposition=DouyinCandidateDisposition.BLACKLISTED.value)
            )
        no_eligible_target = await repository.advance(
            pending(installation_id, NOW + timedelta(milliseconds=400))
        )
        assert no_eligible_target.kind is ActionExecutionAdvanceKind.IDLE

        async with database.session() as session:
            await session.execute(
                update(task_targets)
                .where(task_targets.c.task_id == task_id.uuid)
                .values(disposition=DouyinCandidateDisposition.ELIGIBLE.value)
            )
            await session.execute(
                delete(douyin_search_exposure_definitions).where(
                    douyin_search_exposure_definitions.c.task_id == task_id.uuid
                )
            )
        missing_definition = await repository.advance(
            pending(installation_id, NOW + timedelta(milliseconds=500))
        )
        assert missing_definition.kind is ActionExecutionAdvanceKind.IDLE
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_action_claim_rejects_missing_binding_and_authorization_context(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        installation_id, task_id = await seed_confirmed_task(database)
        attempt_id = uuid4()
        async with database.session() as session:
            await session.execute(
                insert(execution_attempts).values(
                    id=attempt_id,
                    task_id=task_id.uuid,
                    installation_id=installation_id.uuid,
                    attempt_number=1,
                    status=ExecutionAttemptStatus.RUNNING.value,
                    revision=2,
                    created_at=NOW,
                    updated_at=NOW,
                    started_at=NOW,
                )
            )
            await session.execute(
                update(tasks)
                .where(tasks.c.id == task_id.uuid)
                .values(
                    status=TaskStatus.RUNNING.value,
                    current_attempt_id=attempt_id,
                    updated_at=NOW,
                )
            )
        orchestration = SqlAlchemyActionExecutionOrchestrationRepository(database)
        queued = await orchestration.advance(pending(installation_id, NOW + timedelta(seconds=2)))
        assert queued.kind is ActionExecutionAdvanceKind.ACTION_ENQUEUED

        command_repository = SqlAlchemyTaskCommandRepository(database)
        original_record = task_command_repository_module._record

        def missing_action_binding(row: object) -> object:
            return replace(original_record(row), action_id=None)  # type: ignore[arg-type]

        monkeypatch.setattr(task_command_repository_module, "_record", missing_action_binding)
        with pytest.raises(TaskCommandDeliveryRejected):
            await command_repository.claim_next(
                installation_id=installation_id,
                now=NOW + timedelta(seconds=3),
                lease_expires_at=NOW + timedelta(seconds=13),
                retry_delivered_before=NOW,
                recover_delivered=False,
            )

        monkeypatch.setattr(task_command_repository_module, "_record", original_record)
        async with database.session() as session:
            await session.execute(delete(action_risk_authorizations))
        with pytest.raises(TaskCommandDeliveryRejected):
            await command_repository.claim_next(
                installation_id=installation_id,
                now=NOW + timedelta(seconds=3),
                lease_expires_at=NOW + timedelta(seconds=13),
                retry_delivered_before=NOW,
                recover_delivered=False,
            )
    finally:
        await reset_data(database)
        await database.close()
