from __future__ import annotations

import asyncio
import secrets
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from conftest import AlembicRunner
from sqlalchemy import delete, insert, select, update

from automation_tool.control_plane.application.task_command_delivery import (
    PendingTaskCommand,
    TaskCommandDeliveryRejected,
)
from automation_tool.control_plane.domain import (
    ExecutionAttemptId,
    ExecutionAttemptStatus,
    InstallationId,
    TaskCommandResponseType,
    TaskCommandStatus,
    TaskCommandType,
    TaskId,
)
from automation_tool.control_plane.infrastructure.database import (
    Database,
    SqlAlchemyTaskCommandRepository,
    device_credentials,
    device_sessions,
    execution_attempts,
    installation_registration_challenges,
    installations,
    task_actions,
    task_commands,
    task_events,
    tasks,
)
from automation_tool.control_plane.infrastructure.database import (
    task_command_repository as repository_module,
)
from automation_tool.protocol import TaskCommandResultEnvelope

NOW = datetime(2026, 7, 18, 8, 30, tzinfo=UTC)


async def reset_data(database: Database) -> None:
    async with database.session() as session:
        await session.execute(delete(task_commands))
        await session.execute(delete(task_events))
        await session.execute(delete(task_actions))
        await session.execute(update(tasks).values(current_attempt_id=None))
        await session.execute(delete(execution_attempts))
        await session.execute(delete(tasks))
        await session.execute(delete(installation_registration_challenges))
        await session.execute(delete(device_sessions))
        await session.execute(delete(device_credentials))
        await session.execute(delete(installations))


async def seed_attempt(database: Database) -> tuple[InstallationId, TaskId, ExecutionAttemptId]:
    installation_id = InstallationId.new()
    task_id = TaskId.new()
    attempt_id = ExecutionAttemptId.new()
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
            insert(tasks).values(
                id=task_id.uuid,
                installation_id=installation_id.uuid,
                creation_idempotency_key=f"task:delivery:{task_id}",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.execute(
            insert(execution_attempts).values(
                id=attempt_id.uuid,
                task_id=task_id.uuid,
                installation_id=installation_id.uuid,
                attempt_number=1,
                status=ExecutionAttemptStatus.PENDING.value,
                created_at=NOW,
                updated_at=NOW,
            )
        )
    return installation_id, task_id, attempt_id


def pending(
    installation_id: InstallationId,
    task_id: TaskId,
    attempt_id: ExecutionAttemptId,
    *,
    sequence: int = 1,
    deadline_at: datetime = NOW + timedelta(minutes=5),
) -> PendingTaskCommand:
    return PendingTaskCommand(
        message_id=UUID(f"323e4567-e89b-42d3-a456-{sequence:012d}"),
        correlation_id=UUID(f"423e4567-e89b-42d3-a456-{sequence:012d}"),
        installation_id=installation_id,
        task_id=task_id,
        execution_attempt_id=attempt_id,
        sequence=sequence,
        command_type=TaskCommandType.TASK_OFFER,
        idempotency_key=f"task:offer:attempt:{sequence}",
        deadline_at=deadline_at,
        created_at=NOW,
    )


def response(
    command: PendingTaskCommand,
    *,
    message_id: str = "523e4567-e89b-42d3-a456-426614174001",
    message_type: str = "task.accept",
    payload: dict[str, bool] | None = None,
) -> TaskCommandResultEnvelope:
    resolved_payload = payload
    if resolved_payload is None:
        resolved_payload = (
            {"accepted": message_type == "task.accept"}
            if message_type != "task.control_ack"
            else {"acknowledged": True}
        )
    return TaskCommandResultEnvelope.model_validate(
        {
            "protocol_version": "1.0",
            "message_id": message_id,
            "message_type": message_type,
            "sent_at": "2026-07-18T08:30:02Z",
            "deadline_at": "2026-07-18T08:30:32Z",
            "installation_id": str(command.installation_id),
            "executor_id": "123e4567-e89b-42d3-a456-426614174004",
            "correlation_id": str(command.correlation_id),
            "idempotency_key": f"task:response:{command.sequence}",
            "sequence": command.sequence,
            "payload": resolved_payload,
            "task_id": str(command.task_id),
            "execution_attempt_id": str(command.execution_attempt_id),
        }
    )


@pytest.mark.asyncio
async def test_enqueue_is_idempotent_scoped_and_rejects_changed_intent(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        installation_id, task_id, attempt_id = await seed_attempt(database)
        repository = SqlAlchemyTaskCommandRepository(database)
        command = pending(installation_id, task_id, attempt_id)

        first, replay = await asyncio.gather(
            repository.enqueue(command),
            repository.enqueue(command),
        )
        assert first.message_id == replay.message_id == command.message_id
        async with database.session() as session:
            assert await session.scalar(select(task_commands.c.message_id)) == command.message_id

        changed = replace(
            command,
            sequence=2,
            message_id=UUID("623e4567-e89b-42d3-a456-426614174001"),
        )
        with pytest.raises(TaskCommandDeliveryRejected):
            await repository.enqueue(changed)
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_claim_delivery_reconnect_replay_and_ack_are_atomic(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        installation_id, task_id, attempt_id = await seed_attempt(database)
        repository = SqlAlchemyTaskCommandRepository(database)
        command = pending(installation_id, task_id, attempt_id)
        await repository.enqueue(command)

        claimed, competing = await asyncio.gather(
            repository.claim_next(
                installation_id=installation_id,
                now=NOW,
                lease_expires_at=NOW + timedelta(seconds=10),
                retry_delivered_before=NOW - timedelta(seconds=5),
                recover_delivered=False,
            ),
            repository.claim_next(
                installation_id=installation_id,
                now=NOW,
                lease_expires_at=NOW + timedelta(seconds=10),
                retry_delivered_before=NOW - timedelta(seconds=5),
                recover_delivered=False,
            ),
        )
        assert (claimed is None) != (competing is None)
        claimed = claimed or competing
        assert claimed is not None
        delivered = await repository.mark_delivered(
            message_id=claimed.message_id,
            expected_revision=claimed.revision,
            delivered_at=NOW,
        )
        assert delivered.status is TaskCommandStatus.DELIVERED

        assert (
            await repository.claim_next(
                installation_id=installation_id,
                now=NOW + timedelta(seconds=1),
                lease_expires_at=NOW + timedelta(seconds=11),
                retry_delivered_before=NOW - timedelta(seconds=4),
                recover_delivered=False,
            )
            is None
        )
        replayed = await repository.claim_next(
            installation_id=installation_id,
            now=NOW + timedelta(seconds=1),
            lease_expires_at=NOW + timedelta(seconds=11),
            retry_delivered_before=NOW + timedelta(seconds=1),
            recover_delivered=True,
        )
        assert replayed is not None
        assert replayed.delivery_attempts == 2
        delivered_again = await repository.mark_delivered(
            message_id=replayed.message_id,
            expected_revision=replayed.revision,
            delivered_at=NOW + timedelta(seconds=1),
        )
        acknowledged = await repository.acknowledge(
            response=response(command),
            received_at=NOW + timedelta(seconds=2),
        )
        assert acknowledged.status is TaskCommandStatus.ACKNOWLEDGED
        assert acknowledged.response_type is TaskCommandResponseType.TASK_ACCEPT
        assert acknowledged.revision == delivered_again.revision + 1

        duplicate = await repository.acknowledge(
            response=response(
                command,
                message_id="723e4567-e89b-42d3-a456-426614174001",
            ),
            received_at=NOW + timedelta(seconds=3),
        )
        assert duplicate == acknowledged
        with pytest.raises(TaskCommandDeliveryRejected):
            await repository.acknowledge(
                response=response(command, message_type="task.reject"),
                received_at=NOW + timedelta(seconds=3),
            )
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_failed_send_release_lease_recovery_and_deadline_expiry(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        installation_id, task_id, attempt_id = await seed_attempt(database)
        repository = SqlAlchemyTaskCommandRepository(database)
        command = pending(
            installation_id,
            task_id,
            attempt_id,
            deadline_at=NOW + timedelta(seconds=8),
        )
        await repository.enqueue(command)
        claimed = await repository.claim_next(
            installation_id=installation_id,
            now=NOW,
            lease_expires_at=NOW + timedelta(seconds=5),
            retry_delivered_before=NOW,
            recover_delivered=False,
        )
        assert claimed is not None
        pending_again = await repository.release_for_retry(
            message_id=claimed.message_id,
            expected_revision=claimed.revision,
            now=NOW + timedelta(seconds=1),
            retry_at=NOW + timedelta(seconds=2),
        )
        assert pending_again.status is TaskCommandStatus.PENDING
        assert pending_again.response_message_id is None
        claimed_again = await repository.claim_next(
            installation_id=installation_id,
            now=NOW + timedelta(seconds=2),
            lease_expires_at=NOW + timedelta(seconds=7),
            retry_delivered_before=NOW,
            recover_delivered=False,
        )
        assert claimed_again is not None
        assert claimed_again.delivery_attempts == 2

        assert (
            await repository.expire_due(
                installation_id=installation_id,
                now=NOW + timedelta(seconds=8),
            )
            == 1
        )
        expired = await repository.acknowledge(
            response=response(command),
            received_at=NOW + timedelta(seconds=9),
        )
        assert expired.status is TaskCommandStatus.EXPIRED
        assert expired.response_message_id is None
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_repository_validation_response_mapping_and_stale_operations_fail_closed(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        installation_id, task_id, attempt_id = await seed_attempt(database)
        repository = SqlAlchemyTaskCommandRepository(database)
        offer = pending(installation_id, task_id, attempt_id)

        with pytest.raises(TaskCommandDeliveryRejected):
            repository_module._aware_utc(NOW.replace(tzinfo=None))
        with pytest.raises(TaskCommandDeliveryRejected):
            repository_module._record({})  # type: ignore[arg-type]
        with pytest.raises(TaskCommandDeliveryRejected):
            SqlAlchemyTaskCommandRepository(object())  # type: ignore[arg-type]
        with pytest.raises(TaskCommandDeliveryRejected):
            await repository.enqueue(object())  # type: ignore[arg-type]
        with pytest.raises(TaskCommandDeliveryRejected):
            await repository.enqueue(
                replace(
                    offer,
                    installation_id=InstallationId.new(),
                    message_id=UUID("623e4567-e89b-42d3-a456-426614174001"),
                )
            )

        await repository.enqueue(offer)
        with pytest.raises(TaskCommandDeliveryRejected):
            await repository.enqueue(
                replace(
                    offer,
                    message_id=UUID("623e4567-e89b-42d3-a456-426614174002"),
                    correlation_id=UUID("623e4567-e89b-42d3-a456-426614174003"),
                    idempotency_key="task:offer:duplicate-sequence",
                )
            )

        with pytest.raises(TaskCommandDeliveryRejected):
            await repository.expire_due(
                installation_id=str(installation_id),  # type: ignore[arg-type]
                now=NOW,
            )
        with pytest.raises(TaskCommandDeliveryRejected):
            await repository.claim_next(
                installation_id=installation_id,
                now=NOW,
                lease_expires_at=NOW,
                retry_delivered_before=NOW,
                recover_delivered=False,
            )
        with pytest.raises(TaskCommandDeliveryRejected):
            await repository.claim_next(
                installation_id=installation_id,
                now=NOW,
                lease_expires_at=NOW + timedelta(seconds=1),
                retry_delivered_before=NOW,
                recover_delivered=1,  # type: ignore[arg-type]
            )
        with pytest.raises(TaskCommandDeliveryRejected):
            await repository.mark_delivered(
                message_id=str(offer.message_id),  # type: ignore[arg-type]
                expected_revision=1,
                delivered_at=NOW,
            )
        with pytest.raises(TaskCommandDeliveryRejected):
            await repository.mark_delivered(
                message_id=offer.message_id,
                expected_revision=99,
                delivered_at=NOW,
            )
        with pytest.raises(TaskCommandDeliveryRejected):
            await repository.release_for_retry(
                message_id=offer.message_id,
                expected_revision=1,
                now=NOW + timedelta(seconds=1),
                retry_at=NOW,
            )
        with pytest.raises(TaskCommandDeliveryRejected):
            await repository.release_for_retry(
                message_id=offer.message_id,
                expected_revision=99,
                now=NOW,
                retry_at=NOW + timedelta(seconds=1),
            )

        invalid_payload = response(offer, payload={})
        with pytest.raises(TaskCommandDeliveryRejected):
            await repository.acknowledge(response=invalid_payload, received_at=NOW)
        unmatched = replace(
            offer,
            correlation_id=UUID("623e4567-e89b-42d3-a456-426614174004"),
        )
        with pytest.raises(TaskCommandDeliveryRejected):
            await repository.acknowledge(response=response(unmatched), received_at=NOW)
        with pytest.raises(TaskCommandDeliveryRejected):
            await repository.acknowledge(
                response=response(
                    offer,
                    message_type="task.control_ack",
                    payload={"acknowledged": True},
                ),
                received_at=NOW,
            )
        with pytest.raises(TaskCommandDeliveryRejected):
            await repository.acknowledge(response=response(offer), received_at=NOW)
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_repository_control_reject_late_ack_and_response_uniqueness(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        installation_id, task_id, attempt_id = await seed_attempt(database)
        repository = SqlAlchemyTaskCommandRepository(database)

        async def deliver(command: PendingTaskCommand, at: datetime) -> None:
            await repository.enqueue(command)
            claimed = await repository.claim_next(
                installation_id=installation_id,
                now=at,
                lease_expires_at=at + timedelta(seconds=2),
                retry_delivered_before=at,
                recover_delivered=False,
            )
            assert claimed is not None
            await repository.mark_delivered(
                message_id=claimed.message_id,
                expected_revision=claimed.revision,
                delivered_at=at,
            )

        control = replace(
            pending(installation_id, task_id, attempt_id, sequence=1),
            command_type=TaskCommandType.TASK_PAUSE,
            idempotency_key="task:pause:attempt:1",
        )
        await deliver(control, NOW)
        controlled = await repository.acknowledge(
            response=response(
                control,
                message_type="task.control_ack",
                payload={"acknowledged": True},
            ),
            received_at=NOW + timedelta(seconds=1),
        )
        assert controlled.status is TaskCommandStatus.ACKNOWLEDGED
        assert controlled.response_type is TaskCommandResponseType.TASK_CONTROL_ACK

        rejected_offer = pending(installation_id, task_id, attempt_id, sequence=2)
        await deliver(rejected_offer, NOW + timedelta(seconds=2))
        rejected = await repository.acknowledge(
            response=response(
                rejected_offer,
                message_id="623e4567-e89b-42d3-a456-426614174005",
                message_type="task.reject",
            ),
            received_at=NOW + timedelta(seconds=3),
        )
        assert rejected.status is TaskCommandStatus.REJECTED
        with pytest.raises(TaskCommandDeliveryRejected):
            await repository.acknowledge(
                response=response(
                    rejected_offer,
                    message_id="623e4567-e89b-42d3-a456-426614174006",
                ),
                received_at=NOW + timedelta(seconds=4),
            )

        late_offer = pending(
            installation_id,
            task_id,
            attempt_id,
            sequence=3,
            deadline_at=NOW + timedelta(seconds=7),
        )
        await deliver(late_offer, NOW + timedelta(seconds=4))
        late = await repository.acknowledge(
            response=response(
                late_offer,
                message_id="623e4567-e89b-42d3-a456-426614174007",
            ),
            received_at=NOW + timedelta(seconds=8),
        )
        assert late.status is TaskCommandStatus.EXPIRED
        assert late.response_message_id is None

        first = pending(installation_id, task_id, attempt_id, sequence=4)
        await deliver(first, NOW + timedelta(seconds=9))
        shared_response_id = "623e4567-e89b-42d3-a456-426614174008"
        await repository.acknowledge(
            response=response(first, message_id=shared_response_id),
            received_at=NOW + timedelta(seconds=10),
        )
        second = pending(installation_id, task_id, attempt_id, sequence=5)
        await deliver(second, NOW + timedelta(seconds=11))
        with pytest.raises(TaskCommandDeliveryRejected):
            await repository.acknowledge(
                response=response(second, message_id=shared_response_id),
                received_at=NOW + timedelta(seconds=12),
            )

        expiring = pending(
            installation_id,
            task_id,
            attempt_id,
            sequence=6,
            deadline_at=NOW + timedelta(seconds=15),
        )
        await repository.enqueue(expiring)
        claimed = await repository.claim_next(
            installation_id=installation_id,
            now=NOW + timedelta(seconds=13),
            lease_expires_at=NOW + timedelta(seconds=14),
            retry_delivered_before=NOW,
            recover_delivered=False,
        )
        assert claimed is not None
        expired_on_release = await repository.release_for_retry(
            message_id=claimed.message_id,
            expected_revision=claimed.revision,
            now=NOW + timedelta(seconds=14),
            retry_at=NOW + timedelta(seconds=15),
        )
        assert expired_on_release.status is TaskCommandStatus.EXPIRED
    finally:
        await reset_data(database)
        await database.close()
