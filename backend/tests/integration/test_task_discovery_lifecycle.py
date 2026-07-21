from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

import pytest
from conftest import AlembicRunner
from sqlalchemy import delete, insert, select, update

from automation_tool.control_plane.application import task_discovery as discovery_application
from automation_tool.control_plane.application.task_command_delivery import (
    TaskCommandDeliveryRejected,
    TaskCommandRecord,
)
from automation_tool.control_plane.application.task_discovery import (
    TaskDiscoveryConvergenceService,
    TaskDiscoveryInstallationBusy,
    TaskDiscoveryRejected,
    TaskDiscoveryStartResult,
    TaskDiscoveryStartService,
)
from automation_tool.control_plane.domain import (
    DouyinSearchExposureAction,
    DouyinSearchExposureDefinition,
    ExecutionAttemptStatus,
    InstallationId,
    TaskCommandStatus,
    TaskEventType,
    TaskId,
    TaskStatus,
)
from automation_tool.control_plane.infrastructure.database import (
    Database,
    douyin_search_exposure_definitions,
    execution_attempts,
    installations,
    platform_session_gates,
    platform_session_health,
    task_commands,
    task_events,
    task_targets,
    tasks,
)
from automation_tool.control_plane.infrastructure.database import (
    task_discovery_repository as discovery_repository_module,
)
from automation_tool.control_plane.infrastructure.database.task_command_repository import (
    SqlAlchemyTaskCommandRepository,
)
from automation_tool.control_plane.infrastructure.database.task_discovery_repository import (
    SqlAlchemyTaskDiscoveryRepository,
)
from automation_tool.control_plane.infrastructure.database.task_repository import (
    SqlAlchemyTaskRepository,
)
from automation_tool.protocol import (
    TaskCommandResultEnvelope,
    TaskDiscoveryBatchEnvelope,
    TaskDiscoveryCompletedEnvelope,
)

BASE = datetime(2026, 7, 19, 19, 0, tzinfo=UTC)
EXECUTOR_ID = "123e4567-e89b-42d3-a456-426614174004"
DEFINITION = DouyinSearchExposureDefinition(
    search_keyword="新能源汽车",
    action=DouyinSearchExposureAction.BROWSE,
    message_template=None,
    target_limit=10,
    minimum_interval_seconds=30,
    maximum_interval_seconds=90,
    preview_required=True,
    final_confirmation_required=True,
)


@dataclass
class FixedClock:
    value: datetime

    def now(self) -> datetime:
        return self.value


async def reset_data(database: Database) -> None:
    async with database.session() as session:
        await session.execute(delete(task_targets))
        await session.execute(delete(task_commands))
        await session.execute(delete(task_events))
        await session.execute(update(tasks).values(current_attempt_id=None))
        await session.execute(delete(execution_attempts))
        await session.execute(delete(douyin_search_exposure_definitions))
        await session.execute(delete(tasks))
        await session.execute(delete(platform_session_gates))
        await session.execute(delete(platform_session_health))
        await session.execute(delete(installations))


async def seed_ready_task(database: Database) -> tuple[InstallationId, TaskId]:
    installation_id = InstallationId.new()
    task_id = TaskId.new()
    async with database.session() as session:
        await session.execute(
            insert(installations).values(
                id=installation_id.uuid,
                device_public_key=secrets.token_bytes(32),
                created_at=BASE,
                updated_at=BASE,
            )
        )
        await session.execute(
            insert(platform_session_health).values(
                installation_id=installation_id.uuid,
                platform="douyin",
                state="healthy",
                session_revision=1,
                observed_at=BASE,
                updated_at=BASE,
            )
        )
    created = await SqlAlchemyTaskRepository(database).create(
        task_id=task_id,
        installation_id=installation_id,
        idempotency_key=f"task:create:{task_id}",
        definition=DEFINITION,
        created_at=BASE,
    )
    assert created.created is True
    return installation_id, task_id


async def seed_additional_ready_task(
    database: Database,
    installation_id: InstallationId,
) -> TaskId:
    task_id = TaskId.new()
    created = await SqlAlchemyTaskRepository(database).create(
        task_id=task_id,
        installation_id=installation_id,
        idempotency_key=f"task:create:{task_id}",
        definition=DEFINITION,
        created_at=BASE,
    )
    assert created.created is True
    return task_id


async def acknowledge_discovery(
    database: Database,
    *,
    installation_id: InstallationId,
    command: TaskCommandRecord,
) -> TaskCommandRecord:
    repository = SqlAlchemyTaskCommandRepository(database)
    claimed = await repository.claim_next(
        installation_id=installation_id,
        now=BASE + timedelta(seconds=1),
        lease_expires_at=BASE + timedelta(seconds=11),
        retry_delivered_before=BASE,
        recover_delivered=False,
    )
    assert claimed is not None
    assert claimed.discovery_payload is not None
    assert claimed.discovery_payload.page_revision == 1
    delivered = await repository.mark_delivered(
        message_id=claimed.message_id,
        expected_revision=claimed.revision,
        delivered_at=BASE + timedelta(seconds=1),
    )
    response = TaskCommandResultEnvelope.model_validate(
        {
            "protocol_version": "1.0",
            "message_id": str(uuid4()),
            "message_type": "task.accept",
            "sent_at": BASE + timedelta(seconds=2),
            "deadline_at": BASE + timedelta(minutes=2),
            "installation_id": str(installation_id),
            "executor_id": EXECUTOR_ID,
            "correlation_id": str(claimed.correlation_id),
            "idempotency_key": f"task:discover:accept:{claimed.message_id}",
            "sequence": claimed.sequence,
            "payload": {"accepted": True},
            "task_id": str(claimed.task_id),
            "execution_attempt_id": str(claimed.execution_attempt_id),
        }
    )
    acknowledged = await repository.acknowledge(
        response=response,
        received_at=BASE + timedelta(seconds=2),
    )
    assert delivered.status is TaskCommandStatus.DELIVERED
    assert acknowledged.status is TaskCommandStatus.ACKNOWLEDGED
    return claimed


def batch(command: TaskCommandRecord) -> TaskDiscoveryBatchEnvelope:
    return TaskDiscoveryBatchEnvelope.model_validate(
        {
            "protocol_version": "1.0",
            "message_id": str(uuid4()),
            "message_type": "task.discovery_batch",
            "sent_at": BASE + timedelta(seconds=3),
            "deadline_at": BASE + timedelta(minutes=2),
            "installation_id": str(command.installation_id),
            "executor_id": EXECUTOR_ID,
            "correlation_id": str(command.correlation_id),
            "idempotency_key": f"task:discovery:batch:{command.message_id}",
            "sequence": 1,
            "payload": {
                "discovery_version": "douyin.discovery.v1",
                "page_revision": 1,
                "batch_index": 1,
                "batch_count": 1,
                "candidates": [
                    {
                        "candidate_version": "douyin.candidate.v1",
                        "platform_target_id": "author-001",
                        "display_name": "目标一",
                        "public_handle": "target_001",
                        "source": "general_search_author",
                        "page_revision": 1,
                    },
                    {
                        "candidate_version": "douyin.candidate.v1",
                        "platform_target_id": "author-002",
                        "display_name": "目标二",
                        "public_handle": None,
                        "source": "general_search_author",
                        "page_revision": 1,
                    },
                ],
            },
            "task_id": str(command.task_id),
            "execution_attempt_id": str(command.execution_attempt_id),
        }
    )


def completed(
    command: TaskCommandRecord,
    *,
    outcome: str = "completed",
    evidence_override: str | None = None,
) -> TaskDiscoveryCompletedEnvelope:
    successful = outcome == "completed"
    evidence = (
        evidence_override
        or {
            "completed": "candidates_extracted",
            "login_required": "login_required",
            "handoff_required": "blocking_dialog",
            "failed": "page_unavailable",
        }[outcome]
    )
    return TaskDiscoveryCompletedEnvelope.model_validate(
        {
            "protocol_version": "1.0",
            "message_id": str(uuid4()),
            "message_type": "task.discovery_completed",
            "sent_at": BASE + timedelta(seconds=4),
            "deadline_at": BASE + timedelta(minutes=2),
            "installation_id": str(command.installation_id),
            "executor_id": EXECUTOR_ID,
            "correlation_id": str(command.correlation_id),
            "idempotency_key": f"task:discovery:completed:{command.message_id}",
            "sequence": 2 if successful else 1,
            "payload": {
                "discovery_version": "douyin.discovery.v1",
                "outcome": outcome,
                "evidence": evidence,
                "page_revision": 1,
                "batch_count": 1 if successful else 0,
                "candidate_count": 2 if successful else 0,
            },
            "task_id": str(command.task_id),
            "execution_attempt_id": str(command.execution_attempt_id),
        }
    )


@pytest.mark.asyncio
async def test_real_repository_delivers_converges_and_exactly_replays_discovery(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyTaskDiscoveryRepository(database)
    try:
        await reset_data(database)
        installation_id, task_id = await seed_ready_task(database)
        start_service = TaskDiscoveryStartService(
            repository=repository,
            clock=FixedClock(BASE + timedelta(seconds=1)),
        )
        started = await start_service.start(
            installation_id=installation_id,
            task_id=task_id,
            idempotency_key="task:discover:lifecycle",
        )
        replayed_start = await start_service.start(
            installation_id=installation_id,
            task_id=task_id,
            idempotency_key="task:discover:lifecycle",
        )
        assert started.created is True
        assert replayed_start.created is False
        assert replayed_start.command == started.command

        claimed = await acknowledge_discovery(
            database,
            installation_id=installation_id,
            command=started.command,
        )
        discovered_batch = batch(claimed)
        discovery_completed = completed(claimed)
        candidates = tuple(item.to_candidate() for item in discovered_batch.payload.candidates)
        completion_fingerprint = discovery_application._source_fingerprint(discovery_completed)
        for invalid_candidates in (None, (), candidates[:1]):
            with pytest.raises(TaskDiscoveryRejected):
                await repository.converge(
                    discovery_completed,
                    candidates=invalid_candidates,
                    source_fingerprint=completion_fingerprint,
                    received_at=BASE + timedelta(seconds=5),
                )
        wrong_revision = tuple(replace(item, page_revision=2) for item in candidates)
        with pytest.raises(TaskDiscoveryRejected):
            await repository.converge(
                discovery_completed,
                candidates=wrong_revision,
                source_fingerprint=completion_fingerprint,
                received_at=BASE + timedelta(seconds=5),
            )
        async with database.session() as session:
            await session.execute(
                update(installations)
                .where(installations.c.id == installation_id.uuid)
                .values(
                    status="revoked",
                    revoked_at=BASE + timedelta(seconds=3),
                    updated_at=BASE + timedelta(seconds=3),
                )
            )
        with pytest.raises(TaskDiscoveryRejected):
            await repository.converge(
                discovery_completed,
                candidates=candidates,
                source_fingerprint=completion_fingerprint,
                received_at=BASE + timedelta(seconds=5),
            )
        async with database.session() as session:
            await session.execute(
                update(installations)
                .where(installations.c.id == installation_id.uuid)
                .values(status="active", revoked_at=None, updated_at=BASE + timedelta(seconds=4))
            )
        convergence = TaskDiscoveryConvergenceService(
            repository=repository,
            clock=FixedClock(BASE + timedelta(seconds=5)),
        )
        await convergence.receive_batch(discovered_batch)
        result = await convergence.receive_completed(discovery_completed)
        assert result.duplicate is False
        assert result.task.status is TaskStatus.AWAITING_CONFIRMATION

        await convergence.receive_batch(discovered_batch)
        duplicate = await convergence.receive_completed(discovery_completed)
        assert duplicate.duplicate is True

        with pytest.raises(TaskDiscoveryRejected):
            await repository.converge(
                discovery_completed,
                candidates=candidates,
                source_fingerprint=b"x" * 32,
                received_at=BASE + timedelta(seconds=6),
            )
        for replay_candidates in (None, candidates[:1]):
            with pytest.raises(TaskDiscoveryRejected):
                await repository.converge(
                    discovery_completed,
                    candidates=replay_candidates,
                    source_fingerprint=completion_fingerprint,
                    received_at=BASE + timedelta(seconds=6),
                )

        async with database.session() as session:
            task_row = (
                (await session.execute(select(tasks).where(tasks.c.id == task_id.uuid)))
                .mappings()
                .one()
            )
            attempt_status = await session.scalar(
                select(execution_attempts.c.status).where(
                    execution_attempts.c.id == claimed.execution_attempt_id.uuid
                )
            )
            target_rows = (
                (await session.execute(select(task_targets).order_by(task_targets.c.ordinal)))
                .mappings()
                .all()
            )
            event_types = list(
                await session.scalars(
                    select(task_events.c.event_type).order_by(task_events.c.sequence)
                )
            )
        assert task_row["status"] == TaskStatus.AWAITING_CONFIRMATION.value
        assert task_row["revision"] == 3
        assert task_row["last_event_sequence"] == 2
        assert attempt_status == ExecutionAttemptStatus.SUCCEEDED.value
        assert [row["platform_target_id"] for row in target_rows] == [
            "author-001",
            "author-002",
        ]
        assert event_types == [
            TaskEventType.TASK_DISCOVERY_STARTED.value,
            TaskEventType.TASK_AWAITING_CONFIRMATION.value,
        ]

        changed = discovered_batch.model_copy(
            update={
                "payload": discovered_batch.payload.model_copy(
                    update={
                        "candidates": [
                            discovered_batch.payload.candidates[0].model_copy(
                                update={"display_name": "篡改目标"}
                            ),
                            discovered_batch.payload.candidates[1],
                        ]
                    }
                )
            }
        )
        with pytest.raises(TaskDiscoveryRejected):
            await convergence.receive_batch(changed)

        missing_attempt = discovered_batch.model_copy(
            update={"execution_attempt_id": uuid4(), "message_id": uuid4()}
        )
        with pytest.raises(TaskDiscoveryRejected):
            await repository.authorize_batch(missing_attempt)

        async with database.session() as session:
            await session.execute(
                update(tasks)
                .where(tasks.c.id == task_id.uuid)
                .values(status=TaskStatus.AWAITING_HUMAN.value)
            )
        with pytest.raises(TaskDiscoveryRejected):
            await repository.authorize_batch(discovered_batch)
        with pytest.raises(TaskDiscoveryRejected):
            await repository.converge(
                discovery_completed,
                candidates=candidates,
                source_fingerprint=completion_fingerprint,
                received_at=BASE + timedelta(seconds=6),
            )
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_installation_allows_only_one_concurrent_discovery_attempt_winner(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyTaskDiscoveryRepository(database)
    try:
        await reset_data(database)
        installation_id, first_task_id = await seed_ready_task(database)
        second_task_id = await seed_additional_ready_task(database, installation_id)
        service = TaskDiscoveryStartService(
            repository=repository,
            clock=FixedClock(BASE + timedelta(seconds=1)),
        )

        results = await asyncio.gather(
            service.start(
                installation_id=installation_id,
                task_id=first_task_id,
                idempotency_key="task:discover:installation-winner-1",
            ),
            service.start(
                installation_id=installation_id,
                task_id=second_task_id,
                idempotency_key="task:discover:installation-winner-2",
            ),
            return_exceptions=True,
        )

        assert sum(isinstance(result, TaskDiscoveryStartResult) for result in results) == 1
        assert sum(isinstance(result, TaskDiscoveryInstallationBusy) for result in results) == 1
        async with database.session() as session:
            attempt_task_ids = list(
                await session.scalars(
                    select(execution_attempts.c.task_id).where(
                        execution_attempts.c.installation_id == installation_id.uuid
                    )
                )
            )
            task_rows = (
                (
                    await session.execute(
                        select(tasks.c.id, tasks.c.status, tasks.c.current_attempt_id).where(
                            tasks.c.id.in_((first_task_id.uuid, second_task_id.uuid))
                        )
                    )
                )
                .mappings()
                .all()
            )
        assert len(attempt_task_ids) == 1
        assert {row["status"] for row in task_rows} == {
            TaskStatus.DRAFT.value,
            TaskStatus.DISCOVERING_TARGETS.value,
        }
        assert sum(row["current_attempt_id"] is not None for row in task_rows) == 1
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_discovery_start_requires_healthy_session_and_login_result_stores_no_targets(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyTaskDiscoveryRepository(database)
    try:
        await reset_data(database)
        installation_id, task_id = await seed_ready_task(database)
        service = TaskDiscoveryStartService(
            repository=repository,
            clock=FixedClock(BASE + timedelta(seconds=1)),
        )
        async with database.session() as session:
            await session.execute(
                update(platform_session_health)
                .where(platform_session_health.c.installation_id == installation_id.uuid)
                .values(state="missing", updated_at=BASE + timedelta(seconds=1))
            )
        with pytest.raises(TaskDiscoveryRejected):
            await service.start(
                installation_id=installation_id,
                task_id=task_id,
                idempotency_key="task:discover:unhealthy",
            )
        async with database.session() as session:
            await session.execute(
                update(platform_session_health)
                .where(platform_session_health.c.installation_id == installation_id.uuid)
                .values(state="healthy", updated_at=BASE + timedelta(seconds=1))
            )
        started = await service.start(
            installation_id=installation_id,
            task_id=task_id,
            idempotency_key="task:discover:login-required",
        )
        claimed = await acknowledge_discovery(
            database,
            installation_id=installation_id,
            command=started.command,
        )
        login_completed = completed(claimed, outcome="login_required")
        login_fingerprint = discovery_application._source_fingerprint(login_completed)
        login_candidates = tuple(item.to_candidate() for item in batch(claimed).payload.candidates)
        with pytest.raises(TaskDiscoveryRejected):
            await repository.converge(
                login_completed,
                candidates=login_candidates,
                source_fingerprint=login_fingerprint,
                received_at=BASE + timedelta(seconds=5),
            )
        with pytest.raises(TaskDiscoveryRejected):
            await repository.converge(
                login_completed,
                candidates=None,
                source_fingerprint=login_fingerprint,
                received_at=BASE,
            )
        result = await TaskDiscoveryConvergenceService(
            repository=repository,
            clock=FixedClock(BASE + timedelta(seconds=5)),
        ).receive_completed(login_completed)

        assert result.task.status is TaskStatus.AWAITING_PLATFORM_LOGIN
        async with database.session() as session:
            target_count = len((await session.execute(select(task_targets))).all())
            attempt_status = await session.scalar(
                select(execution_attempts.c.status).where(
                    execution_attempts.c.id == claimed.execution_attempt_id.uuid
                )
            )
        assert target_count == 0
        assert attempt_status == ExecutionAttemptStatus.FAILED.value
        with pytest.raises(TaskDiscoveryRejected):
            await repository.converge(
                login_completed,
                candidates=login_candidates,
                source_fingerprint=login_fingerprint,
                received_at=BASE + timedelta(seconds=6),
            )
        duplicate_login = await TaskDiscoveryConvergenceService(
            repository=repository,
            clock=FixedClock(BASE + timedelta(seconds=6)),
        ).receive_completed(login_completed)
        assert duplicate_login.duplicate is True

        async with database.session() as session:
            await session.execute(
                update(platform_session_health)
                .where(platform_session_health.c.installation_id == installation_id.uuid)
                .values(state="healthy", updated_at=BASE + timedelta(seconds=6))
            )
        retried = await TaskDiscoveryStartService(
            repository=repository,
            clock=FixedClock(BASE + timedelta(seconds=7)),
        ).start(
            installation_id=installation_id,
            task_id=task_id,
            idempotency_key="task:discover:after-login",
        )
        assert retried.created is True
        assert retried.task.status is TaskStatus.DISCOVERING_TARGETS
        assert retried.command.execution_attempt_id != claimed.execution_attempt_id
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_discovery_repository_start_and_input_rejection_matrix(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(TaskDiscoveryRejected):
        SqlAlchemyTaskDiscoveryRepository(cast(Any, object()))

    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyTaskDiscoveryRepository(database)
    try:
        await reset_data(database)
        with pytest.raises(TaskDiscoveryRejected):
            await repository.start(cast(Any, object()))
        with pytest.raises(TaskDiscoveryRejected):
            await repository.authorize_batch(cast(Any, object()))

        installation_id, task_id = await seed_ready_task(database)
        service = TaskDiscoveryStartService(
            repository=repository,
            clock=FixedClock(BASE + timedelta(seconds=2)),
        )
        async with database.session() as session:
            await session.execute(
                update(installations)
                .where(installations.c.id == installation_id.uuid)
                .values(
                    status="revoked",
                    revoked_at=BASE + timedelta(seconds=1),
                    updated_at=BASE + timedelta(seconds=1),
                )
            )
        with pytest.raises(TaskDiscoveryRejected):
            await service.start(
                installation_id=installation_id,
                task_id=task_id,
                idempotency_key="task:discover:revoked",
            )
        async with database.session() as session:
            await session.execute(
                update(installations)
                .where(installations.c.id == installation_id.uuid)
                .values(status="active", revoked_at=None, updated_at=BASE + timedelta(seconds=1))
            )
            await session.execute(
                insert(platform_session_gates).values(
                    installation_id=installation_id.uuid,
                    platform="douyin",
                    state="blocked",
                    session_revision=1,
                    updated_at=BASE + timedelta(seconds=1),
                )
            )
        with pytest.raises(TaskDiscoveryRejected):
            await service.start(
                installation_id=installation_id,
                task_id=task_id,
                idempotency_key="task:discover:blocked",
            )
        async with database.session() as session:
            await session.execute(delete(platform_session_gates))

        with pytest.raises(TaskDiscoveryRejected):
            await service.start(
                installation_id=installation_id,
                task_id=TaskId.new(),
                idempotency_key="task:discover:missing-task",
            )
        with pytest.raises(TaskDiscoveryRejected):
            await TaskDiscoveryStartService(
                repository=repository,
                clock=FixedClock(BASE - timedelta(seconds=1)),
            ).start(
                installation_id=installation_id,
                task_id=task_id,
                idempotency_key="task:discover:stale",
            )

        async with database.session() as session:
            await session.execute(delete(douyin_search_exposure_definitions))
        with pytest.raises(TaskDiscoveryRejected):
            await service.start(
                installation_id=installation_id,
                task_id=task_id,
                idempotency_key="task:discover:missing-definition",
            )

        await reset_data(database)
        installation_id, task_id = await seed_ready_task(database)
        service = TaskDiscoveryStartService(
            repository=repository,
            clock=FixedClock(BASE + timedelta(seconds=1)),
        )
        async with database.session() as session:
            await session.execute(
                update(tasks)
                .where(tasks.c.id == task_id.uuid)
                .values(status=TaskStatus.RUNNING.value)
            )
        with pytest.raises(TaskDiscoveryRejected):
            await service.start(
                installation_id=installation_id,
                task_id=task_id,
                idempotency_key="task:discover:running",
            )
        async with database.session() as session:
            await session.execute(
                update(tasks)
                .where(tasks.c.id == task_id.uuid)
                .values(status=TaskStatus.DRAFT.value)
            )

        first = await service.start(
            installation_id=installation_id,
            task_id=task_id,
            idempotency_key="task:discover:first",
        )
        second_task_id = TaskId.new()
        await SqlAlchemyTaskRepository(database).create(
            task_id=second_task_id,
            installation_id=installation_id,
            idempotency_key=f"task:create:{second_task_id}",
            definition=DEFINITION,
            created_at=BASE,
        )
        with pytest.raises(TaskDiscoveryRejected):
            await service.start(
                installation_id=installation_id,
                task_id=second_task_id,
                idempotency_key="task:discover:first",
            )
        async with database.session() as session:
            await session.execute(
                update(tasks)
                .where(tasks.c.id == task_id.uuid)
                .values(status=TaskStatus.AWAITING_PLATFORM_LOGIN.value)
            )
        with pytest.raises(TaskDiscoveryRejected):
            await service.start(
                installation_id=installation_id,
                task_id=task_id,
                idempotency_key="task:discover:active-attempt",
            )
        assert first.created is True
        async with database.session() as session:
            await session.execute(
                delete(douyin_search_exposure_definitions).where(
                    douyin_search_exposure_definitions.c.task_id == task_id.uuid
                )
            )
        with pytest.raises(TaskCommandDeliveryRejected):
            await SqlAlchemyTaskCommandRepository(database).claim_next(
                installation_id=installation_id,
                now=BASE + timedelta(seconds=2),
                lease_expires_at=BASE + timedelta(seconds=12),
                retry_delivered_before=BASE,
                recover_delivered=False,
            )

        await reset_data(database)
        installation_id, task_id = await seed_ready_task(database)
        with monkeypatch.context() as scoped:
            scoped.setattr(
                discovery_repository_module,
                "_require_discovery_reachable",
                lambda _status: (_ for _ in ()).throw(ValueError("private")),
            )
            with pytest.raises(TaskDiscoveryRejected):
                await TaskDiscoveryStartService(
                    repository=repository,
                    clock=FixedClock(BASE + timedelta(seconds=1)),
                ).start(
                    installation_id=installation_id,
                    task_id=task_id,
                    idempotency_key="task:discover:database-value-error",
                )
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_discovery_repository_convergence_input_and_terminal_outcome_matrix(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyTaskDiscoveryRepository(database)
    try:
        await reset_data(database)
        installation_id, task_id = await seed_ready_task(database)
        started = await TaskDiscoveryStartService(
            repository=repository,
            clock=FixedClock(BASE + timedelta(seconds=1)),
        ).start(
            installation_id=installation_id,
            task_id=task_id,
            idempotency_key="task:discover:invalid-converge-input",
        )
        claimed = await acknowledge_discovery(
            database,
            installation_id=installation_id,
            command=started.command,
        )
        message = completed(claimed, outcome="failed")
        fingerprint = discovery_application._source_fingerprint(message)
        invalid_calls = (
            {"message": cast(Any, object())},
            {"candidates": cast(Any, [])},
            {"source_fingerprint": cast(Any, bytearray(32))},
            {"source_fingerprint": b"short"},
            {"received_at": cast(Any, object())},
            {"received_at": datetime(2026, 7, 19, 19, 0)},
        )
        for changes in invalid_calls:
            values: dict[str, object] = {
                "message": message,
                "candidates": None,
                "source_fingerprint": fingerprint,
                "received_at": BASE + timedelta(seconds=5),
            }
            values.update(changes)
            with pytest.raises(TaskDiscoveryRejected):
                await repository.converge(**cast(Any, values))

        for outcome, evidence, expected_status in (
            ("handoff_required", "blocking_dialog", TaskStatus.AWAITING_HUMAN),
            ("handoff_required", "page_version_unknown", TaskStatus.AWAITING_HUMAN),
            ("handoff_required", "conflicting_anchors", TaskStatus.AWAITING_HUMAN),
            ("failed", "page_unavailable", TaskStatus.FAILED),
        ):
            await reset_data(database)
            installation_id, task_id = await seed_ready_task(database)
            started = await TaskDiscoveryStartService(
                repository=repository,
                clock=FixedClock(BASE + timedelta(seconds=1)),
            ).start(
                installation_id=installation_id,
                task_id=task_id,
                idempotency_key=f"task:discover:{outcome}:{evidence}",
            )
            claimed = await acknowledge_discovery(
                database,
                installation_id=installation_id,
                command=started.command,
            )
            message = completed(
                claimed,
                outcome=outcome,
                evidence_override=evidence,
            )
            fingerprint = discovery_application._source_fingerprint(message)
            candidates = tuple(item.to_candidate() for item in batch(claimed).payload.candidates)
            with pytest.raises(TaskDiscoveryRejected):
                await repository.converge(
                    message,
                    candidates=candidates,
                    source_fingerprint=fingerprint,
                    received_at=BASE + timedelta(seconds=5),
                )
            result = await TaskDiscoveryConvergenceService(
                repository=repository,
                clock=FixedClock(BASE + timedelta(seconds=5)),
            ).receive_completed(message)
            assert result.task.status is expected_status
            with pytest.raises(TaskDiscoveryRejected):
                await repository.converge(
                    message,
                    candidates=candidates,
                    source_fingerprint=fingerprint,
                    received_at=BASE + timedelta(seconds=6),
                )
            duplicate = await TaskDiscoveryConvergenceService(
                repository=repository,
                clock=FixedClock(BASE + timedelta(seconds=6)),
            ).receive_completed(message)
            assert duplicate.duplicate is True
    finally:
        await reset_data(database)
        await database.close()
