from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from conftest import AlembicRunner
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.exc import IntegrityError

from automation_tool.control_plane import create_app
from automation_tool.control_plane.application.action_risk_authorizations import (
    ActionRiskAuthorization,
    ActionRiskAuthorizationLimited,
    ActionRiskAuthorizationRejected,
    ActionRiskAuthorizationUnavailable,
    ActionRiskLimitReason,
)
from automation_tool.control_plane.application.device_credentials import ParsedDeviceCredential
from automation_tool.control_plane.application.device_sessions import (
    AuthenticatedDeviceSession,
    DeviceSessionCapability,
    DeviceSessionFactory,
    DeviceSessionService,
    IssuedDeviceSession,
    ParsedDeviceSession,
    PendingDeviceSession,
)
from automation_tool.control_plane.application.executor_connections import (
    ExecutorConnectionService,
)
from automation_tool.control_plane.application.task_event_convergence import (
    TaskEventConvergenceRejected,
    TaskEventConvergenceService,
)
from automation_tool.control_plane.application.task_target_previews import (
    TASK_TARGET_CONFIRMATION_INTENT_VERSION,
    TaskTargetConfirmationIntent,
)
from automation_tool.control_plane.domain import (
    DOUYIN_CANDIDATE_POLICY_VERSION,
    DOUYIN_SEARCH_EXPOSURE_TEMPLATE,
    MAX_ACTION_RISK_LIMIT,
    ActionId,
    ActionOutcome,
    ActionRiskPlatform,
    ActionRiskPolicy,
    ActionRiskScope,
    ActionStatus,
    DouyinCandidateDisposition,
    DouyinSearchExposureAction,
    ExecutionAttemptId,
    ExecutionAttemptStatus,
    ExecutorId,
    InstallationId,
    TargetId,
    TaskCommandResponseType,
    TaskCommandStatus,
    TaskCommandType,
    TaskEventType,
    TaskId,
    TaskStatus,
)
from automation_tool.control_plane.infrastructure.database import (
    Database,
    SqlAlchemyActionRiskAuthorizationRepository,
    SqlAlchemyTaskEventConvergenceRepository,
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
from automation_tool.control_plane.infrastructure.security import (
    Ed25519ActionAuthorizationIssuer,
)
from automation_tool.executor.action_authorization import (
    ActionAuthorizationExpectation,
    Ed25519ActionAuthorizationVerifier,
)
from automation_tool.executor.action_gate import (
    ExecutorActionGate,
    LocalActionHardPolicy,
)
from automation_tool.executor.ledger import ExecutorLedger
from automation_tool.protocol import (
    EXECUTOR_WEBSOCKET_SUBPROTOCOL,
    DouyinCandidateSource,
    PlatformSessionState,
    ProtocolActionId,
    ProtocolExecutionAttemptId,
    ProtocolExecutorId,
    ProtocolInstallationId,
    ProtocolTargetId,
    ProtocolTaskId,
    TaskEventEnvelope,
    action_authorization_idempotency_key,
    parse_executor_message,
)

PREVIOUS_REVISION = "20260720_0019"
HEAD_REVISION = "20260723_0033"
NOW = datetime(2026, 7, 20, 1, 0, tzinfo=UTC)
EXPECTED_COLUMNS = {
    "action_id",
    "target_id",
    "execution_attempt_id",
    "task_id",
    "installation_id",
    "ordinal",
    "platform",
    "action",
    "policy_version",
    "effective_minimum_interval_seconds",
    "task_action_limit",
    "daily_action_limit",
    "consecutive_failure_threshold",
    "task_count_after",
    "daily_count_after",
    "authorized_day",
    "authorized_at",
    "created_at",
}
EXPECTED_CONSTRAINTS = {
    "pk_action_risk_authorizations",
    "fk_action_risk_authorizations_action_binding",
    "fk_action_risk_authorizations_target_binding",
    "ck_action_risk_authorizations_platform",
    "ck_action_risk_authorizations_action",
    "ck_action_risk_authorizations_policy_version",
    "ck_action_risk_authorizations_interval",
    "ck_action_risk_authorizations_limits",
    "ck_action_risk_authorizations_counts",
    "ck_action_risk_authorizations_utc_day",
    "ck_action_risk_authorizations_time_order",
    "uq_action_risk_authorizations_task_count",
    "uq_action_risk_authorizations_daily_count",
}


@dataclass(frozen=True, slots=True)
class RunnableTarget:
    task_id: TaskId
    attempt_id: ExecutionAttemptId
    target_id: TargetId


@dataclass(slots=True)
class FixedAuthorizationClock:
    value: datetime

    def now(self) -> datetime:
        return self.value


@dataclass(slots=True)
class WebSocketSessionRepository:
    installation_id: InstallationId
    expected: ParsedDeviceSession
    credential_id: UUID

    async def issue(
        self,
        *,
        presented_credential: ParsedDeviceCredential,
        pending_session: PendingDeviceSession,
        capability: DeviceSessionCapability,
        issued_at: datetime,
        not_before: datetime,
        expires_at: datetime,
    ) -> IssuedDeviceSession:
        raise AssertionError("not used")

    async def authenticate(
        self,
        *,
        presented_session: ParsedDeviceSession,
        required_capability: DeviceSessionCapability,
        authenticated_at: datetime,
    ) -> AuthenticatedDeviceSession:
        assert presented_session == self.expected
        assert required_capability is DeviceSessionCapability.EXECUTOR_CONNECT
        return AuthenticatedDeviceSession(
            session_id=presented_session.session_id,
            installation_id=self.installation_id.uuid,
            credential_id=self.credential_id,
            credential_version=1,
            capability=required_capability,
            expires_at=authenticated_at + timedelta(minutes=5),
        )


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


async def seed_installation(database: Database) -> InstallationId:
    installation_id = InstallationId.new()
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
    return installation_id


async def seed_runnable_target(
    database: Database,
    installation_id: InstallationId,
    *,
    action: DouyinSearchExposureAction = DouyinSearchExposureAction.COMMENT,
    definition_interval_seconds: int = 10,
    target_count: int = 1,
) -> tuple[RunnableTarget, ...]:
    task_id = TaskId.new()
    attempt_id = ExecutionAttemptId.new()
    target_ids = tuple(TargetId.new() for _ in range(target_count))
    async with database.session() as session:
        await session.execute(
            insert(tasks).values(
                id=task_id.uuid,
                installation_id=installation_id.uuid,
                creation_idempotency_key=f"task:a702:{task_id}",
                status=TaskStatus.RUNNING.value,
                revision=6,
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
                action=action.value,
                message_template=None if action is DouyinSearchExposureAction.BROWSE else "您好",
                target_limit=target_count,
                minimum_interval_seconds=definition_interval_seconds,
                maximum_interval_seconds=max(definition_interval_seconds, 30),
                preview_required=True,
                final_confirmation_required=True,
            )
        )
        for ordinal, target_id in enumerate(target_ids, start=1):
            await session.execute(
                insert(task_targets).values(
                    id=target_id.uuid,
                    task_id=task_id.uuid,
                    installation_id=installation_id.uuid,
                    ordinal=ordinal,
                    platform_target_id=f"target-{ordinal}",
                    dedupe_key="atdck1_" + f"{ordinal:043d}",
                    display_name=f"目标 {ordinal}",
                    public_handle=None,
                    source=DouyinCandidateSource.GENERAL_SEARCH_AUTHOR.value,
                    page_revision=1,
                    disposition=DouyinCandidateDisposition.ELIGIBLE.value,
                    policy_version=DOUYIN_CANDIDATE_POLICY_VERSION,
                    evaluated_at=NOW,
                    created_at=NOW,
                )
            )
        intent = TaskTargetConfirmationIntent(
            installation_id=installation_id,
            task_id=task_id,
            page_revision=1,
            confirmation_revision=4,
            action=action,
            message_template=None if action is DouyinSearchExposureAction.BROWSE else "您好",
            selected_target_ids=tuple(target_ids),
        )
        await session.execute(
            insert(task_target_confirmations).values(
                task_id=task_id.uuid,
                installation_id=installation_id.uuid,
                page_revision=1,
                selection_task_revision=4,
                confirmed_task_revision=5,
                selected_target_count=target_count,
                action=intent.action.value,
                message_template=intent.message_template,
                intent_version=TASK_TARGET_CONFIRMATION_INTENT_VERSION,
                intent_fingerprint=intent.fingerprint(),
                source_message_id=TaskId.new().uuid,
                source_idempotency_key=f"target:confirm:a702:{task_id}",
                source_fingerprint=secrets.token_bytes(32),
                confirmed_at=NOW,
                created_at=NOW,
            )
        )
        await session.execute(
            insert(execution_attempts).values(
                id=attempt_id.uuid,
                task_id=task_id.uuid,
                installation_id=installation_id.uuid,
                attempt_number=1,
                status=ExecutionAttemptStatus.RUNNING.value,
                revision=1,
                created_at=NOW,
                updated_at=NOW,
                started_at=NOW,
            )
        )
        await session.execute(
            update(tasks)
            .where(tasks.c.id == task_id.uuid)
            .values(current_attempt_id=attempt_id.uuid)
        )
    return tuple(
        RunnableTarget(task_id=task_id, attempt_id=attempt_id, target_id=target_id)
        for target_id in target_ids
    )


async def finish_runnable_task(
    database: Database,
    target: RunnableTarget,
    *,
    finished_at: datetime,
) -> None:
    async with database.session() as session:
        await session.execute(
            update(execution_attempts)
            .where(execution_attempts.c.id == target.attempt_id.uuid)
            .values(
                status=ExecutionAttemptStatus.SUCCEEDED.value,
                updated_at=finished_at,
                finished_at=finished_at,
            )
        )
        await session.execute(
            update(tasks)
            .where(tasks.c.id == target.task_id.uuid)
            .values(
                status=TaskStatus.SUCCEEDED.value,
                current_attempt_id=None,
                updated_at=finished_at,
            )
        )


def policy(
    installation_id: InstallationId,
    *,
    minimum_interval_seconds: int = 5,
    task_action_limit: int = 2,
    daily_action_limit: int = 3,
    consecutive_failure_threshold: int = 3,
) -> ActionRiskPolicy:
    return ActionRiskPolicy(
        scope=ActionRiskScope(
            installation_id=installation_id,
            platform=ActionRiskPlatform.DOUYIN,
            action=DouyinSearchExposureAction.COMMENT,
        ),
        minimum_interval=timedelta(seconds=minimum_interval_seconds),
        task_action_limit=task_action_limit,
        daily_action_limit=daily_action_limit,
        consecutive_failure_threshold=consecutive_failure_threshold,
    )


async def authorize(
    repository: SqlAlchemyActionRiskAuthorizationRepository,
    target: RunnableTarget,
    installation_id: InstallationId,
    *,
    action_id: ActionId | None = None,
    authorized_at: datetime = NOW,
    risk_policy: ActionRiskPolicy | None = None,
) -> ActionRiskAuthorization:
    return await repository.authorize(
        action_id=action_id or ActionId.new(),
        target_id=target.target_id,
        execution_attempt_id=target.attempt_id,
        task_id=target.task_id,
        installation_id=installation_id,
        policy=risk_policy or policy(installation_id),
        authorized_at=authorized_at,
    )


def action_event(
    *,
    installation_id: InstallationId,
    task_id: TaskId,
    attempt_id: ExecutionAttemptId,
    message_type: str,
    sequence: int,
    observed_at: datetime,
    action_id: ActionId | None = None,
    correlation_id: UUID | None = None,
    executor_id: ExecutorId | None = None,
) -> TaskEventEnvelope:
    payload = {} if action_id is None else {"action_id": str(action_id)}
    parsed = parse_executor_message(
        json.dumps(
            {
                "protocol_version": "1.0",
                "message_id": str(TaskId.new()),
                "message_type": message_type,
                "sent_at": observed_at.isoformat().replace("+00:00", "Z"),
                "deadline_at": (observed_at + timedelta(minutes=5))
                .isoformat()
                .replace("+00:00", "Z"),
                "installation_id": str(installation_id),
                "executor_id": str(executor_id or ExecutorId.new()),
                "correlation_id": str(correlation_id or TaskId.new().uuid),
                "idempotency_key": f"task:a714:{task_id}:{sequence}",
                "sequence": sequence,
                "payload": payload,
                "task_id": str(task_id),
                "execution_attempt_id": str(attempt_id),
            },
            separators=(",", ":"),
        )
    )
    assert isinstance(parsed, TaskEventEnvelope)
    return parsed


def convergence_service(
    database: Database,
    observed_at: datetime,
) -> TaskEventConvergenceService:
    return TaskEventConvergenceService(
        repository=SqlAlchemyTaskEventConvergenceRepository(database),
        clock=FixedAuthorizationClock(observed_at),
    )


async def mark_dispatched(
    database: Database,
    authorizations: tuple[ActionRiskAuthorization, ...],
    *,
    updated_at: datetime,
) -> None:
    async with database.session() as session:
        await session.execute(
            update(task_actions)
            .where(
                task_actions.c.id.in_(
                    tuple(authorization.action_id.uuid for authorization in authorizations)
                )
            )
            .values(
                status=ActionStatus.DISPATCHED.value,
                revision=2,
                updated_at=updated_at,
            )
        )


async def seed_resume_ack(
    database: Database,
    *,
    target: RunnableTarget,
    installation_id: InstallationId,
    correlation_id: UUID,
    acknowledged_at: datetime,
) -> None:
    async with database.session() as session:
        await session.execute(
            insert(task_commands).values(
                message_id=TaskId.new().uuid,
                correlation_id=correlation_id,
                installation_id=installation_id.uuid,
                task_id=target.task_id.uuid,
                execution_attempt_id=target.attempt_id.uuid,
                sequence=1,
                command_type=TaskCommandType.TASK_RESUME.value,
                status=TaskCommandStatus.ACKNOWLEDGED.value,
                idempotency_key=f"task:a714:resume:{target.task_id}",
                revision=4,
                delivery_attempts=1,
                next_delivery_at=None,
                lease_expires_at=None,
                delivered_at=acknowledged_at,
                acknowledged_at=acknowledged_at,
                response_message_id=TaskId.new().uuid,
                response_type=TaskCommandResponseType.TASK_CONTROL_ACK.value,
                deadline_at=acknowledged_at + timedelta(minutes=5),
                created_at=acknowledged_at,
                updated_at=acknowledged_at,
            )
        )


async def seed_two_authorized_actions(
    database: Database,
    repository: SqlAlchemyActionRiskAuthorizationRepository,
    *,
    consecutive_failure_threshold: int,
) -> tuple[InstallationId, ActionRiskAuthorization, ActionRiskAuthorization]:
    installation_id = await seed_installation(database)
    first_target, second_target = await seed_runnable_target(
        database,
        installation_id,
        definition_interval_seconds=1,
        target_count=2,
    )
    risk_policy = policy(
        installation_id,
        minimum_interval_seconds=1,
        task_action_limit=2,
        daily_action_limit=2,
        consecutive_failure_threshold=consecutive_failure_threshold,
    )
    first = await authorize(
        repository,
        first_target,
        installation_id,
        risk_policy=risk_policy,
    )
    second = await authorize(
        repository,
        second_target,
        installation_id,
        authorized_at=NOW + timedelta(seconds=2),
        risk_policy=risk_policy,
    )
    await mark_dispatched(database, (first, second), updated_at=NOW + timedelta(seconds=4))
    return installation_id, first, second


@pytest.mark.asyncio
async def test_migration_creates_exact_authorization_table_and_downgrades_cleanly(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    alembic_runner(postgresql_url, "check")
    database = Database.from_url(postgresql_url)
    try:
        async with database.session() as session:
            revision = await session.scalar(text("select version_num from alembic_version"))
            columns = set(
                await session.scalars(
                    text(
                        "select column_name from information_schema.columns "
                        "where table_schema = 'public' "
                        "and table_name = 'action_risk_authorizations'"
                    )
                )
            )
            constraints = set(
                await session.scalars(
                    text(
                        "select conname from pg_constraint where conrelid = "
                        "'public.action_risk_authorizations'::regclass"
                    )
                )
            )
            indexes = set(
                await session.scalars(
                    text(
                        "select indexname from pg_indexes where schemaname = 'public' "
                        "and tablename = 'action_risk_authorizations'"
                    )
                )
            )
        assert revision == HEAD_REVISION
        assert columns == EXPECTED_COLUMNS
        assert constraints >= EXPECTED_CONSTRAINTS
        assert {
            "ix_action_risk_authorizations_scope_time",
            "ix_action_risk_authorizations_task_scope",
        } <= indexes

        alembic_runner(postgresql_url, "downgrade", PREVIOUS_REVISION)
        async with database.session() as session:
            assert await session.scalar(text("select version_num from alembic_version")) == (
                PREVIOUS_REVISION
            )
            assert (
                await session.scalar(
                    text("select to_regclass('public.action_risk_authorizations')")
                )
                is None
            )
    finally:
        await database.close()
        alembic_runner(postgresql_url, "upgrade", "head")


@pytest.mark.asyncio
async def test_authorization_persists_action_policy_snapshot_and_exact_replay(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyActionRiskAuthorizationRepository(database)
    try:
        await reset_data(database)
        installation_id = await seed_installation(database)
        target = (await seed_runnable_target(database, installation_id))[0]
        action_id = ActionId.new()

        first = await authorize(repository, target, installation_id, action_id=action_id)
        replay = await authorize(
            repository,
            target,
            installation_id,
            action_id=action_id,
            authorized_at=NOW + timedelta(minutes=1),
        )

        assert replay == first
        assert first.effective_minimum_interval_seconds == 10
        assert first.task_count_after == first.daily_count_after == 1
        assert first.remaining_task_actions == 1
        assert first.remaining_daily_actions == 2
        async with database.session() as session:
            assert await session.scalar(select(func.count()).select_from(task_actions)) == 1
            assert (
                await session.scalar(select(func.count()).select_from(action_risk_authorizations))
                == 1
            )
        with pytest.raises(ActionRiskAuthorizationRejected):
            await repository.authorize(
                action_id=action_id,
                target_id=TargetId.new(),
                execution_attempt_id=target.attempt_id,
                task_id=target.task_id,
                installation_id=installation_id,
                policy=policy(installation_id),
                authorized_at=NOW,
            )
        with pytest.raises(ActionRiskAuthorizationRejected):
            await repository.authorize(
                action_id=action_id,
                target_id=target.target_id,
                execution_attempt_id=target.attempt_id,
                task_id=target.task_id,
                installation_id=installation_id,
                policy=ActionRiskPolicy(
                    scope=ActionRiskScope(
                        installation_id=installation_id,
                        platform=ActionRiskPlatform.DOUYIN,
                        action=DouyinSearchExposureAction.BROWSE,
                    ),
                    minimum_interval=timedelta(seconds=5),
                    task_action_limit=2,
                    daily_action_limit=3,
                    consecutive_failure_threshold=3,
                ),
                authorized_at=NOW,
            )
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_database_authorization_is_signed_and_locally_admitted_for_exact_executor_intent(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
    tmp_path: Path,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyActionRiskAuthorizationRepository(database)
    private_key = bytes(range(32))
    executor_id = ExecutorId.new()
    try:
        await reset_data(database)
        installation_id = await seed_installation(database)
        target = (await seed_runnable_target(database, installation_id))[0]

        authorization = await authorize(repository, target, installation_id)
        issued = Ed25519ActionAuthorizationIssuer(
            private_key=private_key,
            clock=FixedAuthorizationClock(NOW),
            authorization_lifetime=timedelta(seconds=60),
        ).issue(authorization=authorization, executor_id=executor_id)
        protocol_action_id = ProtocolActionId(str(authorization.action_id))
        verifier = Ed25519ActionAuthorizationVerifier(
            public_key=(
                Ed25519PrivateKey.from_private_bytes(private_key).public_key().public_bytes_raw()
            ),
            clock=FixedAuthorizationClock(NOW + timedelta(seconds=1)),
        )
        expected = ActionAuthorizationExpectation(
            action_id=protocol_action_id,
            target_id=ProtocolTargetId(str(authorization.target_id)),
            execution_attempt_id=ProtocolExecutionAttemptId(
                str(authorization.execution_attempt_id)
            ),
            task_id=ProtocolTaskId(str(authorization.task_id)),
            installation_id=ProtocolInstallationId(str(authorization.installation_id)),
            executor_id=ProtocolExecutorId(str(executor_id)),
            platform=authorization.platform.value,
            action=authorization.action,
            idempotency_key=action_authorization_idempotency_key(protocol_action_id),
        )
        ledger = ExecutorLedger(
            state_directory=tmp_path / "executor-state",
            installation_id=str(installation_id),
            executor_id=str(executor_id),
        )
        admitted = ExecutorActionGate(
            ledger=ledger,
            verifier=verifier,
            policy=LocalActionHardPolicy(
                minimum_interval=timedelta(seconds=30),
                task_action_limit=2,
            ),
            clock=FixedAuthorizationClock(NOW + timedelta(seconds=1)),
        ).admit(
            token=issued.token,
            expected=expected,
        )

        assert admitted.action_id == str(issued.claims.action_id)
        assert admitted.authorized_at == authorization.authorized_at
        assert admitted.deadline_at == NOW + timedelta(seconds=60)
        assert (
            admitted.authorization_fingerprint
            == hashlib.sha256(issued.token.encode("ascii")).digest()
        )
        assert admitted.replayed is False
        assert issued.token.encode("ascii") not in ledger.database_path.read_bytes()
        with sqlite3.connect(ledger.database_path) as connection:
            assert connection.execute(
                """
                SELECT minimum_interval_seconds, task_action_limit
                FROM executor_action_policy WHERE singleton_id = 1
                """
            ).fetchone() == (30, 2)
        async with database.session() as session:
            assert (
                await session.scalar(select(func.count()).select_from(action_risk_authorizations))
                == 1
            )
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_minimum_interval_task_and_utc_daily_limits_are_all_enforced(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyActionRiskAuthorizationRepository(database)
    try:
        await reset_data(database)
        installation_id = await seed_installation(database)
        first_task = await seed_runnable_target(database, installation_id, target_count=3)
        await authorize(repository, first_task[0], installation_id)
        with pytest.raises(ActionRiskAuthorizationLimited) as interval:
            await authorize(
                repository,
                first_task[1],
                installation_id,
                authorized_at=NOW + timedelta(seconds=9),
            )
        assert interval.value.reason is ActionRiskLimitReason.MINIMUM_INTERVAL

        await authorize(
            repository,
            first_task[1],
            installation_id,
            authorized_at=NOW + timedelta(seconds=10),
        )
        with pytest.raises(ActionRiskAuthorizationLimited) as task_limit:
            await authorize(
                repository,
                first_task[2],
                installation_id,
                authorized_at=NOW + timedelta(seconds=20),
            )
        assert task_limit.value.reason is ActionRiskLimitReason.TASK_ACTION_LIMIT

        await finish_runnable_task(
            database,
            first_task[0],
            finished_at=NOW + timedelta(seconds=20),
        )
        second_task = (await seed_runnable_target(database, installation_id))[0]
        await authorize(
            repository,
            second_task,
            installation_id,
            authorized_at=NOW + timedelta(seconds=20),
        )
        await finish_runnable_task(
            database,
            second_task,
            finished_at=NOW + timedelta(seconds=30),
        )
        third_task = (await seed_runnable_target(database, installation_id))[0]
        with pytest.raises(ActionRiskAuthorizationLimited) as daily_limit:
            await authorize(
                repository,
                third_task,
                installation_id,
                authorized_at=NOW + timedelta(seconds=30),
            )
        assert daily_limit.value.reason is ActionRiskLimitReason.DAILY_ACTION_LIMIT
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_concurrent_authorizations_cannot_cross_any_hard_limit(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyActionRiskAuthorizationRepository(database)
    try:
        await reset_data(database)
        installation_id = await seed_installation(database)
        targets = await seed_runnable_target(database, installation_id, target_count=5)

        results = await asyncio.gather(
            *(authorize(repository, target, installation_id) for target in targets),
            return_exceptions=True,
        )

        assert sum(not isinstance(result, BaseException) for result in results) == 1
        assert all(
            not isinstance(result, BaseException)
            or isinstance(result, ActionRiskAuthorizationLimited)
            for result in results
        )
        async with database.session() as session:
            assert (
                await session.scalar(select(func.count()).select_from(action_risk_authorizations))
                == 1
            )
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_authorization_rejects_unhealthy_unconfirmed_or_cross_scope_inputs(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyActionRiskAuthorizationRepository(database)
    try:
        await reset_data(database)
        installation_id = await seed_installation(database)
        target = (await seed_runnable_target(database, installation_id))[0]

        with pytest.raises(ActionRiskAuthorizationRejected):
            await authorize(
                repository,
                target,
                installation_id,
                risk_policy=ActionRiskPolicy(
                    scope=ActionRiskScope(
                        installation_id=InstallationId.new(),
                        platform=ActionRiskPlatform.DOUYIN,
                        action=DouyinSearchExposureAction.COMMENT,
                    ),
                    minimum_interval=timedelta(seconds=5),
                    task_action_limit=2,
                    daily_action_limit=3,
                    consecutive_failure_threshold=3,
                ),
            )
        async with database.session() as session:
            await session.execute(
                update(platform_session_health)
                .where(platform_session_health.c.installation_id == installation_id.uuid)
                .values(state=PlatformSessionState.RISK.value)
            )
        with pytest.raises(ActionRiskAuthorizationRejected):
            await authorize(repository, target, installation_id)

        async with database.session() as session:
            await session.execute(
                update(platform_session_health)
                .where(platform_session_health.c.installation_id == installation_id.uuid)
                .values(state=PlatformSessionState.HEALTHY.value)
            )
            await session.execute(
                insert(task_target_exclusions).values(
                    target_id=target.target_id.uuid,
                    task_id=target.task_id.uuid,
                    installation_id=installation_id.uuid,
                    page_revision=1,
                    excluded_at=NOW,
                )
            )
        with pytest.raises(ActionRiskAuthorizationRejected):
            await authorize(repository, target, installation_id)

        async with database.session() as session:
            await session.execute(delete(task_target_exclusions))
            await session.execute(
                update(task_target_confirmations)
                .where(task_target_confirmations.c.task_id == target.task_id.uuid)
                .values(intent_fingerprint=b"x" * 32)
            )
        with pytest.raises(ActionRiskAuthorizationRejected):
            await authorize(repository, target, installation_id)

        async with database.session() as session:
            await session.execute(
                delete(task_target_confirmations).where(
                    task_target_confirmations.c.task_id == target.task_id.uuid
                )
            )
        with pytest.raises(ActionRiskAuthorizationRejected):
            await authorize(repository, target, installation_id)
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_authorization_fails_closed_for_invalid_state_time_conflict_and_database(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyActionRiskAuthorizationRepository(database)
    unavailable = Database.from_url(
        "postgresql+asyncpg://automation_tool_test:private@127.0.0.1:1/automation_tool_test",
        connect_timeout_seconds=0.05,
    )
    unavailable_repository = SqlAlchemyActionRiskAuthorizationRepository(unavailable)
    try:
        await reset_data(database)
        installation_id = await seed_installation(database)
        targets = await seed_runnable_target(database, installation_id, target_count=2)

        async with database.session() as session:
            await session.execute(
                update(installations)
                .where(installations.c.id == installation_id.uuid)
                .values(status="revoked", revoked_at=NOW)
            )
        with pytest.raises(ActionRiskAuthorizationRejected):
            await authorize(repository, targets[0], installation_id)

        async with database.session() as session:
            await session.execute(
                update(installations)
                .where(installations.c.id == installation_id.uuid)
                .values(status="active", revoked_at=None)
            )
        with pytest.raises(ActionRiskAuthorizationRejected):
            await repository.authorize(
                action_id=ActionId.new(),
                target_id=targets[0].target_id,
                execution_attempt_id=targets[0].attempt_id,
                task_id=TaskId.new(),
                installation_id=installation_id,
                policy=policy(installation_id),
                authorized_at=NOW,
            )
        with pytest.raises(ActionRiskAuthorizationRejected):
            await authorize(
                repository,
                targets[0],
                installation_id,
                risk_policy=ActionRiskPolicy(
                    scope=ActionRiskScope(
                        installation_id=installation_id,
                        platform=ActionRiskPlatform.DOUYIN,
                        action=DouyinSearchExposureAction.BROWSE,
                    ),
                    minimum_interval=timedelta(seconds=5),
                    task_action_limit=2,
                    daily_action_limit=3,
                    consecutive_failure_threshold=3,
                ),
            )

        await authorize(
            repository,
            targets[0],
            installation_id,
            authorized_at=NOW + timedelta(seconds=20),
        )
        with pytest.raises(ActionRiskAuthorizationRejected):
            await authorize(
                repository,
                targets[1],
                installation_id,
                authorized_at=NOW + timedelta(seconds=10),
            )
        with pytest.raises(ActionRiskAuthorizationRejected):
            await authorize(
                repository,
                targets[0],
                installation_id,
                authorized_at=NOW + timedelta(seconds=40),
            )

        with pytest.raises(ActionRiskAuthorizationUnavailable) as captured:
            await unavailable_repository.authorize(
                action_id=ActionId.new(),
                target_id=targets[0].target_id,
                execution_attempt_id=targets[0].attempt_id,
                task_id=targets[0].task_id,
                installation_id=installation_id,
                policy=policy(installation_id),
                authorized_at=NOW,
            )
        assert captured.value.__cause__ is None
        assert "private" not in str(captured.value)
    finally:
        await unavailable.close()
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_database_constraints_reject_incoherent_authorization_rows(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyActionRiskAuthorizationRepository(database)
    try:
        await reset_data(database)
        installation_id = await seed_installation(database)
        target = (await seed_runnable_target(database, installation_id))[0]
        created = await authorize(repository, target, installation_id)
        async with database.session() as session:
            row = (
                (
                    await session.execute(
                        select(action_risk_authorizations).where(
                            action_risk_authorizations.c.action_id == created.action_id.uuid
                        )
                    )
                )
                .mappings()
                .one()
            )
        for overrides in (
            {"authorized_day": NOW.date() - timedelta(days=1)},
            {"platform": "private"},
            {"task_count_after": 3},
            {"daily_count_after": 4},
            {"policy_version": "latest"},
        ):
            values = dict(row)
            values["action_id"] = ActionId.new().uuid
            values.update(overrides)
            with pytest.raises(IntegrityError):
                async with database.session() as session:
                    await session.execute(insert(action_risk_authorizations).values(values))
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_consecutive_failures_open_handoff_block_new_actions_and_resume_explicitly(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    authorization_repository = SqlAlchemyActionRiskAuthorizationRepository(database)
    observed_at = NOW + timedelta(seconds=10)
    try:
        await reset_data(database)
        installation_id = await seed_installation(database)
        targets = await seed_runnable_target(
            database,
            installation_id,
            definition_interval_seconds=1,
            target_count=4,
        )
        risk_policy = policy(
            installation_id,
            minimum_interval_seconds=1,
            task_action_limit=4,
            daily_action_limit=4,
            consecutive_failure_threshold=3,
        )
        authorizations = tuple(
            [
                await authorize(
                    authorization_repository,
                    target,
                    installation_id,
                    authorized_at=NOW + timedelta(seconds=index * 2),
                    risk_policy=risk_policy,
                )
                for index, target in enumerate(targets[:3])
            ]
        )
        await mark_dispatched(database, authorizations, updated_at=NOW + timedelta(seconds=6))
        convergence = convergence_service(database, observed_at)

        results = []
        messages = []
        for sequence, authorization in enumerate(authorizations, start=1):
            message = action_event(
                installation_id=installation_id,
                task_id=authorization.task_id,
                attempt_id=authorization.execution_attempt_id,
                message_type="step.failed",
                sequence=sequence,
                observed_at=observed_at,
                action_id=authorization.action_id,
            )
            messages.append(message)
            results.append(await convergence.receive(message))
        duplicate = await convergence.receive(messages[-1])

        assert [result.snapshot.status for result in results] == [
            TaskStatus.RUNNING,
            TaskStatus.RUNNING,
            TaskStatus.AWAITING_HUMAN,
        ]
        assert duplicate.duplicate is True
        assert duplicate.snapshot == results[-1].snapshot
        with pytest.raises(ActionRiskAuthorizationLimited) as blocked:
            await authorize(
                authorization_repository,
                targets[3],
                installation_id,
                authorized_at=NOW + timedelta(seconds=20),
                risk_policy=risk_policy,
            )
        assert blocked.value.reason is ActionRiskLimitReason.CONSECUTIVE_FAILURE_CIRCUIT
        replay = await authorize(
            authorization_repository,
            targets[2],
            installation_id,
            action_id=authorizations[2].action_id,
            authorized_at=NOW + timedelta(seconds=20),
            risk_policy=risk_policy,
        )
        assert replay == authorizations[2]

        correlation_id = TaskId.new().uuid
        await seed_resume_ack(
            database,
            target=targets[0],
            installation_id=installation_id,
            correlation_id=correlation_id,
            acknowledged_at=observed_at,
        )
        resumed_at = NOW + timedelta(seconds=11)
        resumed = await convergence_service(database, resumed_at).receive(
            action_event(
                installation_id=installation_id,
                task_id=targets[0].task_id,
                attempt_id=targets[0].attempt_id,
                message_type="task.resumed",
                sequence=4,
                observed_at=resumed_at,
                correlation_id=correlation_id,
            )
        )
        assert resumed.snapshot.status is TaskStatus.RUNNING

        final_authorization = await authorize(
            authorization_repository,
            targets[3],
            installation_id,
            authorized_at=NOW + timedelta(seconds=20),
            risk_policy=risk_policy,
        )
        await mark_dispatched(
            database,
            (final_authorization,),
            updated_at=NOW + timedelta(seconds=20),
        )
        completed_at = NOW + timedelta(seconds=21)
        completed = await convergence_service(database, completed_at).receive(
            action_event(
                installation_id=installation_id,
                task_id=final_authorization.task_id,
                attempt_id=final_authorization.execution_attempt_id,
                message_type="step.completed",
                sequence=5,
                observed_at=completed_at,
                action_id=final_authorization.action_id,
            )
        )
        assert completed.snapshot.status is TaskStatus.RUNNING

        async with database.session() as session:
            result_rows = (
                (
                    await session.execute(
                        select(action_risk_results).order_by(
                            action_risk_results.c.observed_at,
                            action_risk_results.c.action_id,
                        )
                    )
                )
                .mappings()
                .all()
            )
            circuit = (await session.execute(select(action_failure_circuits))).mappings().one()
            event_types = list(
                await session.scalars(
                    select(task_events.c.event_type)
                    .where(task_events.c.task_id == targets[0].task_id.uuid)
                    .order_by(task_events.c.sequence)
                )
            )
        result_by_action = {row["action_id"]: row for row in result_rows}
        ordered_results = [
            result_by_action[authorization.action_id.uuid]
            for authorization in (*authorizations, final_authorization)
        ]
        assert [row["consecutive_failures_after"] for row in ordered_results] == [1, 2, 3, 0]
        assert [row["triggered_handoff"] for row in ordered_results] == [
            False,
            False,
            True,
            False,
        ]
        assert circuit["consecutive_failures"] == 0
        assert circuit["circuit_open"] is False
        assert circuit["revision"] == 5
        assert circuit["opened_by_action_id"] is None
        assert event_types == [
            TaskEventType.STEP_FAILED.value,
            TaskEventType.STEP_FAILED.value,
            TaskEventType.TASK_AWAITING_HUMAN.value,
            TaskEventType.TASK_RESUMED.value,
            TaskEventType.STEP_COMPLETED.value,
        ]
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_success_resets_only_a_closed_failure_streak(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyActionRiskAuthorizationRepository(database)
    observed_at = NOW + timedelta(seconds=10)
    try:
        await reset_data(database)
        installation_id = await seed_installation(database)
        targets = await seed_runnable_target(
            database,
            installation_id,
            definition_interval_seconds=1,
            target_count=3,
        )
        risk_policy = policy(
            installation_id,
            minimum_interval_seconds=1,
            task_action_limit=3,
            daily_action_limit=3,
            consecutive_failure_threshold=3,
        )
        authorizations = tuple(
            [
                await authorize(
                    repository,
                    target,
                    installation_id,
                    authorized_at=NOW + timedelta(seconds=index * 2),
                    risk_policy=risk_policy,
                )
                for index, target in enumerate(targets)
            ]
        )
        await mark_dispatched(database, authorizations, updated_at=NOW + timedelta(seconds=6))
        convergence = convergence_service(database, observed_at)
        for sequence, (message_type, authorization) in enumerate(
            zip(
                ("step.failed", "step.completed", "step.failed"),
                authorizations,
                strict=True,
            ),
            start=1,
        ):
            await convergence.receive(
                action_event(
                    installation_id=installation_id,
                    task_id=authorization.task_id,
                    attempt_id=authorization.execution_attempt_id,
                    message_type=message_type,
                    sequence=sequence,
                    observed_at=observed_at,
                    action_id=authorization.action_id,
                )
            )

        async with database.session() as session:
            result_rows = (await session.execute(select(action_risk_results))).mappings().all()
            circuit = (await session.execute(select(action_failure_circuits))).mappings().one()
        result_by_action = {row["action_id"]: row for row in result_rows}
        assert [
            result_by_action[authorization.action_id.uuid]["consecutive_failures_after"]
            for authorization in authorizations
        ] == [1, 0, 1]
        assert circuit["consecutive_failures"] == 1
        assert circuit["circuit_open"] is False
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_late_success_cannot_auto_close_an_open_circuit(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyActionRiskAuthorizationRepository(database)
    try:
        await reset_data(database)
        installation_id = await seed_installation(database)
        first_target, second_target = await seed_runnable_target(
            database,
            installation_id,
            definition_interval_seconds=1,
            target_count=2,
        )
        risk_policy = policy(
            installation_id,
            minimum_interval_seconds=1,
            task_action_limit=2,
            daily_action_limit=3,
            consecutive_failure_threshold=1,
        )
        failed_authorization = await authorize(
            repository,
            first_target,
            installation_id,
            risk_policy=risk_policy,
        )
        successful_authorization = await authorize(
            repository,
            second_target,
            installation_id,
            authorized_at=NOW + timedelta(seconds=2),
            risk_policy=risk_policy,
        )
        await mark_dispatched(
            database,
            (failed_authorization, successful_authorization),
            updated_at=NOW + timedelta(seconds=4),
        )
        failed_at = NOW + timedelta(seconds=10)
        await convergence_service(database, failed_at).receive(
            action_event(
                installation_id=installation_id,
                task_id=failed_authorization.task_id,
                attempt_id=failed_authorization.execution_attempt_id,
                message_type="step.failed",
                sequence=1,
                observed_at=failed_at,
                action_id=failed_authorization.action_id,
            )
        )
        async with database.session() as session:
            await session.execute(
                update(tasks)
                .where(tasks.c.id == failed_authorization.task_id.uuid)
                .values(status=TaskStatus.RUNNING.value)
            )
            await session.execute(
                update(execution_attempts)
                .where(execution_attempts.c.id == failed_authorization.execution_attempt_id.uuid)
                .values(status=ExecutionAttemptStatus.RUNNING.value)
            )
        successful_at = NOW + timedelta(seconds=11)
        await convergence_service(database, successful_at).receive(
            action_event(
                installation_id=installation_id,
                task_id=successful_authorization.task_id,
                attempt_id=successful_authorization.execution_attempt_id,
                message_type="step.completed",
                sequence=2,
                observed_at=successful_at,
                action_id=successful_authorization.action_id,
            )
        )

        with pytest.raises(ActionRiskAuthorizationLimited) as blocked:
            await authorize(
                repository,
                second_target,
                installation_id,
                authorized_at=NOW + timedelta(seconds=20),
                risk_policy=risk_policy,
            )
        assert blocked.value.reason is ActionRiskLimitReason.CONSECUTIVE_FAILURE_CIRCUIT
        async with database.session() as session:
            circuit = (await session.execute(select(action_failure_circuits))).mappings().one()
            late_result = (
                (
                    await session.execute(
                        select(action_risk_results).where(
                            action_risk_results.c.action_id
                            == successful_authorization.action_id.uuid
                        )
                    )
                )
                .mappings()
                .one()
            )
        assert circuit["circuit_open"] is True
        assert circuit["consecutive_failures"] == 1
        assert circuit["opened_by_action_id"] == failed_authorization.action_id.uuid
        assert late_result["outcome"] == ActionOutcome.SUCCEEDED.value
        assert late_result["circuit_open_after"] is True
        assert late_result["consecutive_failures_after"] == 1
        assert late_result["triggered_handoff"] is False
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_concurrent_failures_are_serialized_and_only_one_opens_the_circuit(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyActionRiskAuthorizationRepository(database)
    observed_at = NOW + timedelta(seconds=10)
    try:
        await reset_data(database)
        installation_id = await seed_installation(database)
        first_target, second_target = await seed_runnable_target(
            database,
            installation_id,
            definition_interval_seconds=1,
            target_count=2,
        )
        risk_policy = policy(
            installation_id,
            minimum_interval_seconds=1,
            task_action_limit=2,
            daily_action_limit=2,
            consecutive_failure_threshold=2,
        )
        first = await authorize(
            repository,
            first_target,
            installation_id,
            risk_policy=risk_policy,
        )
        second = await authorize(
            repository,
            second_target,
            installation_id,
            authorized_at=NOW + timedelta(seconds=2),
            risk_policy=risk_policy,
        )
        await mark_dispatched(database, (first, second), updated_at=NOW + timedelta(seconds=4))

        outcomes = await asyncio.gather(
            *(
                convergence_service(database, observed_at).receive(
                    action_event(
                        installation_id=installation_id,
                        task_id=authorization.task_id,
                        attempt_id=authorization.execution_attempt_id,
                        message_type="step.failed",
                        sequence=sequence,
                        observed_at=observed_at,
                        action_id=authorization.action_id,
                    )
                )
                for sequence, authorization in enumerate((first, second), start=1)
            )
        )

        assert {outcome.snapshot.status for outcome in outcomes} == {
            TaskStatus.RUNNING,
            TaskStatus.AWAITING_HUMAN,
        }
        async with database.session() as session:
            result_rows = (await session.execute(select(action_risk_results))).mappings().all()
            circuit = (await session.execute(select(action_failure_circuits))).mappings().one()
        assert sorted(row["consecutive_failures_after"] for row in result_rows) == [1, 2]
        assert sum(row["triggered_handoff"] for row in result_rows) == 1
        assert circuit["consecutive_failures"] == 2
        assert circuit["circuit_open"] is True
        assert circuit["revision"] == 2
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_preexisting_result_and_circuit_clock_rollback_fail_without_partial_projection(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyActionRiskAuthorizationRepository(database)
    try:
        await reset_data(database)
        installation_id, first, second = await seed_two_authorized_actions(
            database,
            repository,
            consecutive_failure_threshold=3,
        )
        async with database.session() as session:
            await session.execute(
                insert(action_risk_results).values(
                    action_id=first.action_id.uuid,
                    installation_id=installation_id.uuid,
                    platform=first.platform.value,
                    action=first.action.value,
                    outcome=ActionOutcome.FAILED.value,
                    consecutive_failures_after=1,
                    consecutive_failure_threshold=3,
                    circuit_open_after=False,
                    triggered_handoff=False,
                    observed_at=NOW + timedelta(seconds=5),
                    created_at=NOW + timedelta(seconds=5),
                )
            )
        with pytest.raises(TaskEventConvergenceRejected):
            await convergence_service(database, NOW + timedelta(seconds=10)).receive(
                action_event(
                    installation_id=installation_id,
                    task_id=first.task_id,
                    attempt_id=first.execution_attempt_id,
                    message_type="step.failed",
                    sequence=1,
                    observed_at=NOW + timedelta(seconds=10),
                    action_id=first.action_id,
                )
            )
        async with database.session() as session:
            await session.execute(delete(action_risk_results))

        recorded_at = NOW + timedelta(seconds=20)
        await convergence_service(database, recorded_at).receive(
            action_event(
                installation_id=installation_id,
                task_id=first.task_id,
                attempt_id=first.execution_attempt_id,
                message_type="step.failed",
                sequence=1,
                observed_at=recorded_at,
                action_id=first.action_id,
            )
        )
        stale_at = NOW + timedelta(seconds=19)
        async with database.session() as session:
            await session.execute(
                update(tasks)
                .where(tasks.c.id == second.task_id.uuid)
                .values(updated_at=stale_at - timedelta(seconds=1))
            )
            await session.execute(
                update(execution_attempts)
                .where(execution_attempts.c.id == second.execution_attempt_id.uuid)
                .values(updated_at=stale_at - timedelta(seconds=1))
            )
        with pytest.raises(TaskEventConvergenceRejected):
            await convergence_service(database, stale_at).receive(
                action_event(
                    installation_id=installation_id,
                    task_id=second.task_id,
                    attempt_id=second.execution_attempt_id,
                    message_type="step.failed",
                    sequence=2,
                    observed_at=stale_at,
                    action_id=second.action_id,
                )
            )
        async with database.session() as session:
            assert await session.scalar(select(func.count()).select_from(action_risk_results)) == 1
            assert (
                await session.scalar(
                    select(task_actions.c.status).where(task_actions.c.id == second.action_id.uuid)
                )
                == ActionStatus.DISPATCHED.value
            )
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_failure_counter_overflow_is_rejected_without_new_result(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyActionRiskAuthorizationRepository(database)
    try:
        await reset_data(database)
        installation_id, first, second = await seed_two_authorized_actions(
            database,
            repository,
            consecutive_failure_threshold=MAX_ACTION_RISK_LIMIT,
        )
        first_at = NOW + timedelta(seconds=10)
        await convergence_service(database, first_at).receive(
            action_event(
                installation_id=installation_id,
                task_id=first.task_id,
                attempt_id=first.execution_attempt_id,
                message_type="step.completed",
                sequence=1,
                observed_at=first_at,
                action_id=first.action_id,
            )
        )
        async with database.session() as session:
            await session.execute(
                update(action_failure_circuits).values(consecutive_failures=MAX_ACTION_RISK_LIMIT)
            )
        second_at = NOW + timedelta(seconds=11)
        with pytest.raises(TaskEventConvergenceRejected):
            await convergence_service(database, second_at).receive(
                action_event(
                    installation_id=installation_id,
                    task_id=second.task_id,
                    attempt_id=second.execution_attempt_id,
                    message_type="step.failed",
                    sequence=2,
                    observed_at=second_at,
                    action_id=second.action_id,
                )
            )
        async with database.session() as session:
            assert await session.scalar(select(func.count()).select_from(action_risk_results)) == 1
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_only_the_task_that_opened_the_circuit_can_clear_it_with_monotonic_time(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyActionRiskAuthorizationRepository(database)
    try:
        await reset_data(database)
        installation_id = await seed_installation(database)
        owner_target = (
            await seed_runnable_target(
                database,
                installation_id,
                definition_interval_seconds=1,
            )
        )[0]
        risk_policy = policy(
            installation_id,
            minimum_interval_seconds=1,
            task_action_limit=1,
            daily_action_limit=2,
            consecutive_failure_threshold=1,
        )
        owner = await authorize(
            repository,
            owner_target,
            installation_id,
            risk_policy=risk_policy,
        )
        await mark_dispatched(database, (owner,), updated_at=NOW + timedelta(seconds=4))
        opened_at = NOW + timedelta(seconds=10)
        await convergence_service(database, opened_at).receive(
            action_event(
                installation_id=installation_id,
                task_id=owner.task_id,
                attempt_id=owner.execution_attempt_id,
                message_type="step.failed",
                sequence=1,
                observed_at=opened_at,
                action_id=owner.action_id,
            )
        )
        async with database.session() as session:
            await session.execute(
                update(tasks)
                .where(tasks.c.id == owner.task_id.uuid)
                .values(
                    status=TaskStatus.CANCELLED.value,
                    current_attempt_id=None,
                    updated_at=opened_at,
                )
            )
            await session.execute(
                update(execution_attempts)
                .where(execution_attempts.c.id == owner.execution_attempt_id.uuid)
                .values(
                    status=ExecutionAttemptStatus.CANCELLED.value,
                    updated_at=opened_at,
                    finished_at=opened_at,
                )
            )
        other_target = (
            await seed_runnable_target(
                database,
                installation_id,
                definition_interval_seconds=1,
            )
        )[0]
        async with database.session() as session:
            await session.execute(
                update(tasks)
                .where(tasks.c.id == other_target.task_id.uuid)
                .values(status=TaskStatus.AWAITING_HUMAN.value, updated_at=opened_at)
            )
            await session.execute(
                update(execution_attempts)
                .where(execution_attempts.c.id == other_target.attempt_id.uuid)
                .values(
                    status=ExecutionAttemptStatus.AWAITING_HUMAN.value,
                    updated_at=opened_at,
                )
            )
        other_correlation = TaskId.new().uuid
        await seed_resume_ack(
            database,
            target=other_target,
            installation_id=installation_id,
            correlation_id=other_correlation,
            acknowledged_at=opened_at,
        )
        other_resume_at = NOW + timedelta(seconds=11)
        await convergence_service(database, other_resume_at).receive(
            action_event(
                installation_id=installation_id,
                task_id=other_target.task_id,
                attempt_id=other_target.attempt_id,
                message_type="task.resumed",
                sequence=1,
                observed_at=other_resume_at,
                correlation_id=other_correlation,
            )
        )
        async with database.session() as session:
            circuit_open = await session.scalar(select(action_failure_circuits.c.circuit_open))
        assert circuit_open is True

        await reset_data(database)
        installation_id = await seed_installation(database)
        owner_target = (
            await seed_runnable_target(
                database,
                installation_id,
                definition_interval_seconds=1,
            )
        )[0]
        owner = await authorize(
            repository,
            owner_target,
            installation_id,
            risk_policy=policy(
                installation_id,
                minimum_interval_seconds=1,
                task_action_limit=1,
                daily_action_limit=1,
                consecutive_failure_threshold=1,
            ),
        )
        await mark_dispatched(database, (owner,), updated_at=NOW + timedelta(seconds=4))
        await convergence_service(database, opened_at).receive(
            action_event(
                installation_id=installation_id,
                task_id=owner.task_id,
                attempt_id=owner.execution_attempt_id,
                message_type="step.failed",
                sequence=1,
                observed_at=opened_at,
                action_id=owner.action_id,
            )
        )
        async with database.session() as session:
            await session.execute(
                update(action_failure_circuits).values(updated_at=NOW + timedelta(seconds=20))
            )
        owner_correlation = TaskId.new().uuid
        await seed_resume_ack(
            database,
            target=owner_target,
            installation_id=installation_id,
            correlation_id=owner_correlation,
            acknowledged_at=opened_at,
        )
        stale_resume_at = NOW + timedelta(seconds=15)
        with pytest.raises(TaskEventConvergenceRejected):
            await convergence_service(database, stale_resume_at).receive(
                action_event(
                    installation_id=installation_id,
                    task_id=owner.task_id,
                    attempt_id=owner.execution_attempt_id,
                    message_type="task.resumed",
                    sequence=2,
                    observed_at=stale_resume_at,
                    correlation_id=owner_correlation,
                )
            )
        async with database.session() as session:
            assert await session.scalar(select(action_failure_circuits.c.circuit_open)) is True
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_non_circuit_handoff_resume_does_not_create_failure_state(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        installation_id = await seed_installation(database)
        target = (await seed_runnable_target(database, installation_id))[0]
        handoff_at = NOW + timedelta(seconds=10)
        async with database.session() as session:
            await session.execute(
                update(tasks)
                .where(tasks.c.id == target.task_id.uuid)
                .values(status=TaskStatus.AWAITING_HUMAN.value, updated_at=handoff_at)
            )
            await session.execute(
                update(execution_attempts)
                .where(execution_attempts.c.id == target.attempt_id.uuid)
                .values(
                    status=ExecutionAttemptStatus.AWAITING_HUMAN.value,
                    updated_at=handoff_at,
                )
            )
        correlation_id = TaskId.new().uuid
        await seed_resume_ack(
            database,
            target=target,
            installation_id=installation_id,
            correlation_id=correlation_id,
            acknowledged_at=handoff_at,
        )
        resumed_at = NOW + timedelta(seconds=11)
        resumed = await convergence_service(database, resumed_at).receive(
            action_event(
                installation_id=installation_id,
                task_id=target.task_id,
                attempt_id=target.attempt_id,
                message_type="task.resumed",
                sequence=1,
                observed_at=resumed_at,
                correlation_id=correlation_id,
            )
        )
        assert resumed.snapshot.status is TaskStatus.RUNNING
        async with database.session() as session:
            assert (
                await session.scalar(select(func.count()).select_from(action_failure_circuits)) == 0
            )
    finally:
        await reset_data(database)
        await database.close()


def test_authenticated_executor_websocket_is_the_original_failure_circuit_caller(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    observed_at = NOW + timedelta(seconds=10)
    executor_id = ExecutorId.new()

    async def seed() -> tuple[
        InstallationId,
        ActionRiskAuthorization,
        RunnableTarget,
        ActionRiskPolicy,
    ]:
        database = Database.from_url(postgresql_url)
        repository = SqlAlchemyActionRiskAuthorizationRepository(database)
        try:
            await reset_data(database)
            installation_id = await seed_installation(database)
            targets = await seed_runnable_target(
                database,
                installation_id,
                definition_interval_seconds=1,
                target_count=2,
            )
            risk_policy = policy(
                installation_id,
                minimum_interval_seconds=1,
                task_action_limit=2,
                daily_action_limit=2,
                consecutive_failure_threshold=1,
            )
            authorization = await authorize(
                repository,
                targets[0],
                installation_id,
                risk_policy=risk_policy,
            )
            await mark_dispatched(
                database,
                (authorization,),
                updated_at=NOW + timedelta(seconds=4),
            )
            return installation_id, authorization, targets[1], risk_policy
        finally:
            await database.close()

    installation_id, authorization, next_target, risk_policy = asyncio.run(seed())
    app_database = Database.from_url(postgresql_url)
    session_material = DeviceSessionFactory(
        secret_source=secrets.token_bytes,
        id_source=uuid4,
    ).create()
    session_repository = WebSocketSessionRepository(
        installation_id=installation_id,
        expected=ParsedDeviceSession(
            session_id=session_material.session_id,
            secret_digest=session_material.secret_digest,
        ),
        credential_id=uuid4(),
    )
    device_sessions = DeviceSessionService(
        repository=session_repository,
        clock=FixedAuthorizationClock(observed_at),
        session_factory=DeviceSessionFactory(
            secret_source=secrets.token_bytes,
            id_source=uuid4,
        ),
    )
    app = create_app(
        database=app_database,
        executor_connection_service=ExecutorConnectionService(device_sessions),
        task_event_convergence_service=convergence_service(app_database, observed_at),
        executor_connection_recheck_interval_seconds=0.01,
    )
    # This acceptance sends only an Executor event; command delivery is an unrelated
    # outbound loop and is disabled so the TestClient shutdown cannot cancel it mid-query.
    app.state.task_command_delivery_service = None
    hello = json.dumps(
        {
            "protocol_version": "1.0",
            "message_id": str(uuid4()),
            "message_type": "executor.hello",
            "sent_at": observed_at.isoformat().replace("+00:00", "Z"),
            "deadline_at": (observed_at + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
            "installation_id": str(installation_id),
            "executor_id": str(executor_id),
            "correlation_id": str(uuid4()),
            "idempotency_key": f"executor:a714:{executor_id}",
            "sequence": 1,
            "payload": {
                "architecture": "arm64",
                "executor_version": "0.1.0",
                "platform": "macos",
            },
        },
        separators=(",", ":"),
    )
    failed = action_event(
        installation_id=installation_id,
        task_id=authorization.task_id,
        attempt_id=authorization.execution_attempt_id,
        message_type="step.failed",
        sequence=1,
        observed_at=observed_at,
        action_id=authorization.action_id,
        executor_id=executor_id,
    )
    heartbeat_at = observed_at + timedelta(seconds=1)
    heartbeat = json.dumps(
        {
            "protocol_version": "1.0",
            "message_id": str(uuid4()),
            "message_type": "executor.heartbeat",
            "sent_at": heartbeat_at.isoformat().replace("+00:00", "Z"),
            "deadline_at": (heartbeat_at + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
            "installation_id": str(installation_id),
            "executor_id": str(executor_id),
            "correlation_id": str(uuid4()),
            "idempotency_key": f"executor:a714:heartbeat:{executor_id}",
            "sequence": 2,
            "payload": {"status": "healthy"},
        },
        separators=(",", ":"),
    )

    try:
        with (
            TestClient(app) as client,
            client.websocket_connect(
                "/api/v1/executors/connect",
                headers={"authorization": f"Bearer {session_material.session_token}"},
                subprotocols=[EXECUTOR_WEBSOCKET_SUBPROTOCOL],
            ) as websocket,
        ):
            websocket.send_text(hello)
            websocket.send_text(json.dumps(failed.model_dump(mode="json"), separators=(",", ":")))
            websocket.send_text(heartbeat)
            portal = client.portal
            assert portal is not None
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                online = portal.call(
                    app.state.executor_connection_registry.snapshot,
                    installation_id,
                )
                if online is not None and online.last_sequence == 2:
                    break
                time.sleep(0.005)
            else:
                raise AssertionError("Executor heartbeat did not pass the failure event")

        async def verify() -> None:
            database = Database.from_url(postgresql_url)
            try:
                async with database.session() as session:
                    circuit = (
                        (await session.execute(select(action_failure_circuits))).mappings().one()
                    )
                    result = (await session.execute(select(action_risk_results))).mappings().one()
                    task_event_type = await session.scalar(
                        select(task_events.c.event_type).where(
                            task_events.c.task_id == authorization.task_id.uuid,
                            task_events.c.sequence == 1,
                        )
                    )
                assert circuit["circuit_open"] is True
                assert circuit["consecutive_failures"] == 1
                assert result["action_id"] == authorization.action_id.uuid
                assert result["triggered_handoff"] is True
                assert task_event_type == TaskEventType.TASK_AWAITING_HUMAN.value
                with pytest.raises(ActionRiskAuthorizationLimited) as blocked:
                    await authorize(
                        SqlAlchemyActionRiskAuthorizationRepository(database),
                        next_target,
                        installation_id,
                        authorized_at=NOW + timedelta(seconds=20),
                        risk_policy=risk_policy,
                    )
                assert blocked.value.reason is ActionRiskLimitReason.CONSECUTIVE_FAILURE_CIRCUIT
            finally:
                await reset_data(database)
                await database.close()

        asyncio.run(verify())
    finally:
        if app.state.lifecycle_state != "stopped":
            asyncio.run(app_database.close())
