from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from conftest import AlembicRunner
from sqlalchemy import delete, insert, text, update
from sqlalchemy.exc import IntegrityError

from automation_tool.control_plane.domain import (
    MAX_TASK_EVENT_SEQUENCE,
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

PREVIOUS_REVISION = "20260718_0008"
HEAD_REVISION = "20260721_0026"
NOW = datetime(2026, 7, 18, 5, 30, tzinfo=UTC)
DEADLINE = NOW + timedelta(minutes=5)
EXPECTED_COLUMNS = {
    "message_id",
    "correlation_id",
    "installation_id",
    "task_id",
    "execution_attempt_id",
    "sequence",
    "command_type",
    "target_confirmation_message_id",
    "action_id",
    "status",
    "idempotency_key",
    "revision",
    "delivery_attempts",
    "next_delivery_at",
    "lease_expires_at",
    "delivered_at",
    "acknowledged_at",
    "response_message_id",
    "response_type",
    "deadline_at",
    "created_at",
    "updated_at",
}
EXPECTED_CONSTRAINTS = {
    "pk_task_commands",
    "fk_task_commands_attempt_binding",
    "fk_task_commands_action_binding",
    "uq_task_commands_attempt_sequence",
    "uq_task_commands_idempotency",
    "uq_task_commands_response_message",
    "uq_task_commands_action",
    "ck_task_commands_message_uuid_v4",
    "ck_task_commands_correlation_uuid_v4",
    "ck_task_commands_response_uuid_v4",
    "ck_task_commands_target_confirmation_uuid_v4",
    "ck_task_commands_action_uuid_v4",
    "ck_task_commands_action_scope",
    "ck_task_commands_target_confirmation_scope",
    "ck_task_commands_sequence_range",
    "ck_task_commands_type",
    "ck_task_commands_status",
    "ck_task_commands_idempotency_key",
    "ck_task_commands_revision_positive",
    "ck_task_commands_delivery_attempts_nonnegative",
    "ck_task_commands_time_order",
    "ck_task_commands_status_coherence",
    "ck_task_commands_response_coherence",
}


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
                creation_idempotency_key=f"task:seed:{task_id}",
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


def command_values(
    installation_id: InstallationId,
    task_id: TaskId,
    attempt_id: ExecutionAttemptId,
    *,
    sequence: int = 1,
    command_type: TaskCommandType = TaskCommandType.TASK_OFFER,
) -> dict[str, object]:
    return {
        "message_id": uuid4(),
        "correlation_id": uuid4(),
        "installation_id": installation_id.uuid,
        "task_id": task_id.uuid,
        "execution_attempt_id": attempt_id.uuid,
        "sequence": sequence,
        "command_type": command_type.value,
        "status": TaskCommandStatus.PENDING.value,
        "idempotency_key": f"task:command:{sequence}",
        "revision": 1,
        "delivery_attempts": 0,
        "next_delivery_at": NOW,
        "deadline_at": DEADLINE,
        "created_at": NOW,
        "updated_at": NOW,
    }


def acknowledged_values(
    installation_id: InstallationId,
    task_id: TaskId,
    attempt_id: ExecutionAttemptId,
    *,
    sequence: int,
    command_type: TaskCommandType,
    response_type: TaskCommandResponseType,
    status: TaskCommandStatus = TaskCommandStatus.ACKNOWLEDGED,
) -> dict[str, object]:
    values = command_values(
        installation_id,
        task_id,
        attempt_id,
        sequence=sequence,
        command_type=command_type,
    )
    values.update(
        {
            "status": status.value,
            "delivery_attempts": 1,
            "next_delivery_at": None,
            "delivered_at": NOW + timedelta(seconds=1),
            "acknowledged_at": NOW + timedelta(seconds=2),
            "response_message_id": uuid4(),
            "response_type": response_type.value,
            "updated_at": NOW + timedelta(seconds=2),
        }
    )
    return values


@pytest.mark.asyncio
async def test_task_command_migration_upgrades_checks_and_downgrades_cleanly(
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
                        "where table_schema = 'public' and table_name = 'task_commands'"
                    )
                )
            )
            constraints = set(
                await session.scalars(
                    text(
                        "select conname from pg_constraint "
                        "where conrelid = 'public.task_commands'::regclass"
                    )
                )
            )
            indexes = set(
                await session.scalars(
                    text(
                        "select indexname from pg_indexes "
                        "where schemaname = 'public' and tablename = 'task_commands'"
                    )
                )
            )
        assert revision == HEAD_REVISION
        assert columns == EXPECTED_COLUMNS
        assert constraints >= EXPECTED_CONSTRAINTS
        assert {
            "ix_task_commands_outbox_due",
            "ix_task_commands_installation_task_created",
        } <= indexes

        alembic_runner(postgresql_url, "downgrade", PREVIOUS_REVISION)
        async with database.session() as session:
            downgraded_revision = await session.scalar(
                text("select version_num from alembic_version")
            )
            removed = await session.scalar(text("select to_regclass('public.task_commands')"))
            events_remain = await session.scalar(text("select to_regclass('public.task_events')"))
        assert downgraded_revision == PREVIOUS_REVISION
        assert removed is None
        assert events_remain == "task_events"
    finally:
        await database.close()
        alembic_runner(postgresql_url, "upgrade", "head")


@pytest.mark.asyncio
async def test_command_defaults_and_valid_delivery_ack_reject_expiry_states_are_persisted(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        installation_id, task_id, attempt_id = await seed_attempt(database)
        pending_id = uuid4()
        async with database.session() as session:
            pending = (
                (
                    await session.execute(
                        insert(task_commands)
                        .values(
                            message_id=pending_id,
                            correlation_id=uuid4(),
                            installation_id=installation_id.uuid,
                            task_id=task_id.uuid,
                            execution_attempt_id=attempt_id.uuid,
                            sequence=1,
                            command_type=TaskCommandType.TASK_OFFER.value,
                            idempotency_key="task:offer:attempt:1",
                            deadline_at=DEADLINE,
                            created_at=NOW,
                            updated_at=NOW,
                            next_delivery_at=NOW,
                        )
                        .returning(*task_commands.c)
                    )
                )
                .mappings()
                .one()
            )
            in_flight = command_values(installation_id, task_id, attempt_id, sequence=2)
            in_flight.update(
                {
                    "status": TaskCommandStatus.IN_FLIGHT.value,
                    "delivery_attempts": 1,
                    "next_delivery_at": None,
                    "lease_expires_at": NOW + timedelta(seconds=30),
                }
            )
            delivered = command_values(installation_id, task_id, attempt_id, sequence=3)
            delivered.update(
                {
                    "status": TaskCommandStatus.DELIVERED.value,
                    "delivery_attempts": 1,
                    "next_delivery_at": None,
                    "delivered_at": NOW + timedelta(seconds=1),
                    "updated_at": NOW + timedelta(seconds=1),
                }
            )
            accepted = acknowledged_values(
                installation_id,
                task_id,
                attempt_id,
                sequence=4,
                command_type=TaskCommandType.TASK_OFFER,
                response_type=TaskCommandResponseType.TASK_ACCEPT,
            )
            control_ack = acknowledged_values(
                installation_id,
                task_id,
                attempt_id,
                sequence=5,
                command_type=TaskCommandType.TASK_PAUSE,
                response_type=TaskCommandResponseType.TASK_CONTROL_ACK,
            )
            rejected = acknowledged_values(
                installation_id,
                task_id,
                attempt_id,
                sequence=6,
                command_type=TaskCommandType.TASK_OFFER,
                response_type=TaskCommandResponseType.TASK_REJECT,
                status=TaskCommandStatus.REJECTED,
            )
            discovery_accepted = acknowledged_values(
                installation_id,
                task_id,
                attempt_id,
                sequence=7,
                command_type=TaskCommandType.TASK_DISCOVER,
                response_type=TaskCommandResponseType.TASK_ACCEPT,
            )
            expired = command_values(installation_id, task_id, attempt_id, sequence=8)
            expired.update(
                {
                    "status": TaskCommandStatus.EXPIRED.value,
                    "next_delivery_at": None,
                    "updated_at": DEADLINE,
                }
            )
            for values in (
                in_flight,
                delivered,
                accepted,
                control_ack,
                rejected,
                discovery_accepted,
                expired,
            ):
                await session.execute(insert(task_commands).values(values))

        assert pending["status"] == TaskCommandStatus.PENDING.value
        assert pending["revision"] == 1
        assert pending["delivery_attempts"] == 0
        assert pending["response_message_id"] is None
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_command_sequence_idempotency_and_response_uniqueness_are_scoped(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        installation_id, task_id, attempt_id = await seed_attempt(database)
        other_installation, other_task, other_attempt = await seed_attempt(database)
        original = command_values(installation_id, task_id, attempt_id)
        accepted = acknowledged_values(
            installation_id,
            task_id,
            attempt_id,
            sequence=2,
            command_type=TaskCommandType.TASK_OFFER,
            response_type=TaskCommandResponseType.TASK_ACCEPT,
        )
        async with database.session() as session:
            await session.execute(insert(task_commands).values(original))
            await session.execute(insert(task_commands).values(accepted))

        duplicate_sequence = command_values(installation_id, task_id, attempt_id, sequence=1)
        duplicate_sequence["idempotency_key"] = "task:command:duplicate-sequence"
        duplicate_key = command_values(installation_id, task_id, attempt_id, sequence=3)
        duplicate_key["idempotency_key"] = original["idempotency_key"]
        duplicate_response = acknowledged_values(
            installation_id,
            task_id,
            attempt_id,
            sequence=4,
            command_type=TaskCommandType.TASK_OFFER,
            response_type=TaskCommandResponseType.TASK_ACCEPT,
        )
        duplicate_response["response_message_id"] = accepted["response_message_id"]
        for duplicate in (duplicate_sequence, duplicate_key, duplicate_response):
            with pytest.raises(IntegrityError):
                async with database.session() as session:
                    await session.execute(insert(task_commands).values(duplicate))

        same_values_other_scope = command_values(
            other_installation,
            other_task,
            other_attempt,
            sequence=1,
        )
        same_values_other_scope["idempotency_key"] = original["idempotency_key"]
        same_response_other_scope = acknowledged_values(
            other_installation,
            other_task,
            other_attempt,
            sequence=2,
            command_type=TaskCommandType.TASK_OFFER,
            response_type=TaskCommandResponseType.TASK_ACCEPT,
        )
        same_response_other_scope["response_message_id"] = accepted["response_message_id"]
        maximum = command_values(
            installation_id,
            task_id,
            attempt_id,
            sequence=MAX_TASK_EVENT_SEQUENCE,
        )
        async with database.session() as session:
            await session.execute(insert(task_commands).values(same_values_other_scope))
            await session.execute(insert(task_commands).values(same_response_other_scope))
            await session.execute(insert(task_commands).values(maximum))

        for invalid_sequence in (0, MAX_TASK_EVENT_SEQUENCE + 1):
            values = command_values(
                installation_id,
                task_id,
                attempt_id,
                sequence=invalid_sequence,
            )
            values["idempotency_key"] = f"task:command:invalid:{invalid_sequence}"
            with pytest.raises(IntegrityError):
                async with database.session() as session:
                    await session.execute(insert(task_commands).values(values))
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_command_constraints_reject_invalid_scope_identity_time_state_and_response(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        installation_id, task_id, attempt_id = await seed_attempt(database)
        other_installation, other_task, other_attempt = await seed_attempt(database)
        invalid_cases: tuple[dict[str, object], ...] = (
            {"message_id": UUID("123e4567-e89b-12d3-a456-426614174000")},
            {"correlation_id": UUID("123e4567-e89b-12d3-a456-426614174000")},
            {"target_confirmation_message_id": UUID("123e4567-e89b-12d3-a456-426614174000")},
            {"installation_id": other_installation.uuid},
            {"task_id": other_task.uuid},
            {"execution_attempt_id": other_attempt.uuid},
            {"command_type": "task.future"},
            {"status": "unknown"},
            {"idempotency_key": "contains space"},
            {"revision": 0},
            {"delivery_attempts": -1},
            {"deadline_at": NOW},
            {"next_delivery_at": DEADLINE},
            {"updated_at": NOW - timedelta(microseconds=1)},
            {"status": TaskCommandStatus.IN_FLIGHT.value, "next_delivery_at": None},
            {
                "status": TaskCommandStatus.DELIVERED.value,
                "delivery_attempts": 1,
                "next_delivery_at": None,
            },
            {
                "status": TaskCommandStatus.PENDING.value,
                "delivered_at": NOW + timedelta(seconds=1),
            },
            {"status": TaskCommandStatus.EXPIRED.value},
            {
                "status": TaskCommandStatus.ACKNOWLEDGED.value,
                "delivery_attempts": 1,
                "next_delivery_at": None,
                "delivered_at": NOW + timedelta(seconds=1),
                "acknowledged_at": NOW + timedelta(seconds=2),
                "response_message_id": uuid4(),
                "response_type": TaskCommandResponseType.TASK_CONTROL_ACK.value,
                "updated_at": NOW + timedelta(seconds=2),
            },
        )
        for sequence, overrides in enumerate(invalid_cases, start=10):
            values = command_values(
                installation_id,
                task_id,
                attempt_id,
                sequence=sequence,
            )
            values.update(overrides)
            with pytest.raises(IntegrityError):
                async with database.session() as session:
                    await session.execute(insert(task_commands).values(values))

        invalid_control_response = acknowledged_values(
            installation_id,
            task_id,
            attempt_id,
            sequence=100,
            command_type=TaskCommandType.TASK_PAUSE,
            response_type=TaskCommandResponseType.TASK_ACCEPT,
        )
        invalid_response_uuid = acknowledged_values(
            installation_id,
            task_id,
            attempt_id,
            sequence=101,
            command_type=TaskCommandType.TASK_OFFER,
            response_type=TaskCommandResponseType.TASK_ACCEPT,
        )
        invalid_response_uuid["response_message_id"] = UUID("123e4567-e89b-12d3-a456-426614174000")
        invalid_discovery_response = acknowledged_values(
            installation_id,
            task_id,
            attempt_id,
            sequence=102,
            command_type=TaskCommandType.TASK_DISCOVER,
            response_type=TaskCommandResponseType.TASK_CONTROL_ACK,
        )
        invalid_control_confirmation = command_values(
            installation_id,
            task_id,
            attempt_id,
            sequence=103,
            command_type=TaskCommandType.TASK_PAUSE,
        )
        invalid_control_confirmation["target_confirmation_message_id"] = uuid4()
        for values in (
            invalid_control_response,
            invalid_response_uuid,
            invalid_discovery_response,
            invalid_control_confirmation,
        ):
            with pytest.raises(IntegrityError):
                async with database.session() as session:
                    await session.execute(insert(task_commands).values(values))
    finally:
        await reset_data(database)
        await database.close()
