#!/usr/bin/env python3
"""Prove the D6-13 confirmation guard through the real Executor WebSocket."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from acceptance_postgres import managed_test_postgres
from run_i2_13_acceptance import (
    BACKEND_ROOT,
    REPOSITORY_ROOT,
    compose_command,
    open_executor,
    post_json,
    require_port_closed,
    seed_active_credential,
    unused_loopback_port,
    wait_for_control_plane,
)
from run_t3_09_acceptance import lifecycle_message
from sqlalchemy import delete, insert, select
from websockets.exceptions import ConnectionClosed
from websockets.sync.connection import Connection

from automation_tool.control_plane.application.device_sessions import (
    DeviceSessionCapability,
)
from automation_tool.control_plane.application.executor_connection_registry import (
    ExecutorConnectionRegistry,
)
from automation_tool.control_plane.application.executor_connections import (
    EXECUTOR_WEBSOCKET_SUBPROTOCOL,
)
from automation_tool.control_plane.application.task_command_delivery import (
    TaskCommandDeliveryService,
    TaskCommandRecord,
)
from automation_tool.control_plane.application.task_target_previews import (
    TASK_TARGET_CONFIRMATION_INTENT_VERSION,
)
from automation_tool.control_plane.domain import (
    ExecutionAttemptId,
    ExecutionAttemptStatus,
    InstallationId,
    TaskCommandStatus,
    TaskCommandType,
    TaskId,
    TaskStatus,
)
from automation_tool.control_plane.infrastructure.database import (
    Database,
    SqlAlchemyTaskCommandRepository,
    douyin_search_exposure_definitions,
    execution_attempts,
    task_commands,
    task_target_confirmations,
    tasks,
)
from automation_tool.protocol import MAX_EXECUTOR_MESSAGE_BYTES


def isolated_environment(database_port: int) -> tuple[dict[str, str], str]:
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("AUTOMATION_TOOL_")
    }
    database_password = secrets.token_hex(24)
    database_url = (
        "postgresql+asyncpg://automation_tool_d613:"
        f"{database_password}@127.0.0.1:{database_port}/automation_tool_d613"
    )
    environment.update(
        {
            "AUTOMATION_TOOL_DEV_DB_USER": "unused_d613_dev",
            "AUTOMATION_TOOL_DEV_DB_PASSWORD": secrets.token_hex(24),
            "AUTOMATION_TOOL_DEV_DB_NAME": "unused_d613_dev",
            "AUTOMATION_TOOL_DEV_DB_PORT": str(unused_loopback_port()),
            "AUTOMATION_TOOL_TEST_DB_USER": "automation_tool_d613",
            "AUTOMATION_TOOL_TEST_DB_PASSWORD": database_password,
            "AUTOMATION_TOOL_TEST_DB_NAME": "automation_tool_d613",
            "AUTOMATION_TOOL_TEST_DB_PORT": str(database_port),
            "AUTOMATION_TOOL_DATABASE_URL": database_url,
        }
    )
    return environment, database_url


async def seed_business_task(
    database_url: str,
    installation_id: InstallationId,
    *,
    confirmed: bool,
) -> tuple[TaskId, ExecutionAttemptId, UUID | None]:
    database = Database.from_url(database_url)
    task_id = TaskId.new()
    attempt_id = ExecutionAttemptId.new()
    confirmation_message_id = uuid4() if confirmed else None
    now = datetime.now(UTC)
    try:
        async with database.session() as session:
            await session.execute(
                insert(tasks).values(
                    id=task_id.uuid,
                    installation_id=installation_id.uuid,
                    creation_idempotency_key=f"task:d613:{task_id}",
                    status=(
                        TaskStatus.QUEUED.value
                        if confirmed
                        else TaskStatus.AWAITING_CONFIRMATION.value
                    ),
                    revision=2 if confirmed else 1,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.execute(
                insert(douyin_search_exposure_definitions).values(
                    task_id=task_id.uuid,
                    installation_id=installation_id.uuid,
                    search_keyword="D6-13 受控验收",
                    action="comment",
                    message_template="D6-13 只验证投递守卫",
                    target_limit=1,
                    minimum_interval_seconds=30,
                    maximum_interval_seconds=60,
                    preview_required=True,
                    final_confirmation_required=True,
                )
            )
            await session.execute(
                insert(execution_attempts).values(
                    id=attempt_id.uuid,
                    task_id=task_id.uuid,
                    installation_id=installation_id.uuid,
                    attempt_number=1,
                    status=ExecutionAttemptStatus.PENDING.value,
                    created_at=now,
                    updated_at=now,
                )
            )
            if confirmation_message_id is not None:
                await session.execute(
                    insert(task_target_confirmations).values(
                        task_id=task_id.uuid,
                        installation_id=installation_id.uuid,
                        page_revision=1,
                        selection_task_revision=1,
                        confirmed_task_revision=2,
                        selected_target_count=1,
                        action="comment",
                        message_template="D6-13 只验证投递守卫",
                        intent_version=TASK_TARGET_CONFIRMATION_INTENT_VERSION,
                        intent_fingerprint=secrets.token_bytes(32),
                        source_message_id=confirmation_message_id,
                        source_idempotency_key=f"task:d613:confirm:{task_id}",
                        source_fingerprint=secrets.token_bytes(32),
                        confirmed_at=now,
                        created_at=now,
                    )
                )
        return task_id, attempt_id, confirmation_message_id
    finally:
        await database.close()


async def seed_unbound_offer(
    database_url: str,
    installation_id: InstallationId,
) -> UUID:
    task_id, attempt_id, confirmation_id = await seed_business_task(
        database_url,
        installation_id,
        confirmed=False,
    )
    if confirmation_id is not None:
        raise RuntimeError("D6-13 unbound setup unexpectedly created a confirmation")
    database = Database.from_url(database_url)
    now = datetime.now(UTC)
    message_id = uuid4()
    try:
        async with database.session() as session:
            await session.execute(
                insert(task_commands).values(
                    message_id=message_id,
                    correlation_id=uuid4(),
                    installation_id=installation_id.uuid,
                    task_id=task_id.uuid,
                    execution_attempt_id=attempt_id.uuid,
                    sequence=1,
                    command_type=TaskCommandType.TASK_OFFER.value,
                    status=TaskCommandStatus.PENDING.value,
                    idempotency_key=f"task:d613:unbound:{task_id}",
                    revision=1,
                    delivery_attempts=0,
                    next_delivery_at=now,
                    deadline_at=now + timedelta(minutes=3),
                    created_at=now,
                    updated_at=now,
                )
            )
        return message_id
    finally:
        await database.close()


async def enqueue_confirmed_offer(
    database_url: str,
    installation_id: InstallationId,
) -> tuple[TaskCommandRecord, UUID]:
    task_id, attempt_id, confirmation_id = await seed_business_task(
        database_url,
        installation_id,
        confirmed=True,
    )
    if confirmation_id is None:
        raise RuntimeError("D6-13 confirmed setup omitted its confirmation")
    database = Database.from_url(database_url)
    try:
        offer = await TaskCommandDeliveryService(
            repository=SqlAlchemyTaskCommandRepository(database),
            registry=ExecutorConnectionRegistry(),
        ).enqueue(
            installation_id=installation_id,
            task_id=task_id,
            execution_attempt_id=attempt_id,
            sequence=1,
            command_type=TaskCommandType.TASK_OFFER,
            idempotency_key=f"task:d613:offer:{task_id}",
            deadline_at=datetime.now(UTC) + timedelta(minutes=3),
        )
        return offer, confirmation_id
    finally:
        await database.close()


async def invalidate_confirmation(database_url: str, task_id: TaskId) -> None:
    database = Database.from_url(database_url)
    try:
        async with database.session() as session:
            await session.execute(
                delete(task_target_confirmations).where(
                    task_target_confirmations.c.task_id == task_id.uuid
                )
            )
    finally:
        await database.close()


def expect_no_command(connection: Connection) -> None:
    try:
        connection.recv(timeout=1.5)
    except TimeoutError:
        return
    except ConnectionClosed as error:
        raise RuntimeError("D6-13 Executor closed while checking the guard") from error
    raise RuntimeError("D6-13 delivered an unconfirmed business offer")


def receive_offer(connection: Connection, expected_message_id: UUID) -> None:
    source = connection.recv(timeout=5)
    if not isinstance(source, str):
        raise RuntimeError("D6-13 received a non-text command")
    parsed = json.loads(source)
    if (
        not isinstance(parsed, dict)
        or parsed.get("message_type") != TaskCommandType.TASK_OFFER.value
        or parsed.get("message_id") != str(expected_message_id)
    ):
        raise RuntimeError("D6-13 delivered the wrong business command")


def executor_session(port: int, credential: str) -> str:
    exchanged = post_json(
        port,
        "/api/v1/device-sessions",
        credential,
        payload={"capability": DeviceSessionCapability.EXECUTOR_CONNECT.value},
        expected_status=201,
    )
    token = exchanged.get("sessionToken")
    if not isinstance(token, str):
        raise RuntimeError("D6-13 Session exchange omitted its opaque token")
    return token


def prove_guarded_websocket(
    port: int,
    session_token: str,
    installation_id: InstallationId,
) -> None:
    executor_id = str(uuid4())
    with open_executor(port, session_token, EXECUTOR_WEBSOCKET_SUBPROTOCOL) as guarded:
        guarded.send(
            lifecycle_message(
                installation_id,
                executor_id,
                message_type="executor.hello",
                sequence=1,
            )
        )
        expect_no_command(guarded)


def receive_confirmed_websocket(
    port: int,
    session_token: str,
    installation_id: InstallationId,
    expected_offer: TaskCommandRecord,
) -> None:
    executor_id = str(uuid4())
    with open_executor(port, session_token, EXECUTOR_WEBSOCKET_SUBPROTOCOL) as current:
        current.send(
            lifecycle_message(
                installation_id,
                executor_id,
                message_type="executor.hello",
                sequence=1,
            )
        )
        receive_offer(current, expected_offer.message_id)


async def verify_database_state(
    database_url: str,
    unbound_message_id: UUID,
    stale: TaskCommandRecord,
    current: TaskCommandRecord,
    current_confirmation_id: UUID,
) -> None:
    database = Database.from_url(database_url)
    try:
        async with database.session() as session:
            rows = {
                row["message_id"]: row
                for row in (
                    await session.execute(
                        select(task_commands).where(
                            task_commands.c.message_id.in_(
                                (
                                    unbound_message_id,
                                    stale.message_id,
                                    current.message_id,
                                )
                            )
                        )
                    )
                )
                .mappings()
                .all()
            }
    finally:
        await database.close()
    unbound_row = rows[unbound_message_id]
    stale_row = rows[stale.message_id]
    current_row = rows[current.message_id]
    if (
        unbound_row["status"] != TaskCommandStatus.PENDING.value
        or unbound_row["delivery_attempts"] != 0
        or unbound_row["target_confirmation_message_id"] is not None
    ):
        raise RuntimeError("D6-13 unbound offer escaped its fail-closed state")
    if (
        stale_row["status"] != TaskCommandStatus.PENDING.value
        or stale_row["delivery_attempts"] != 0
        or stale_row["target_confirmation_message_id"] is None
    ):
        raise RuntimeError("D6-13 stale offer escaped its fail-closed state")
    if (
        current_row["status"] != TaskCommandStatus.DELIVERED.value
        or current_row["delivery_attempts"] != 1
        or current_row["target_confirmation_message_id"] != current_confirmation_id
    ):
        raise RuntimeError("D6-13 current confirmed offer did not traverse the outbox")


def stop_server(server: subprocess.Popen[bytes] | None) -> None:
    if server is None or server.poll() is not None:
        return
    server.terminate()
    try:
        server.wait(timeout=10)
    except subprocess.TimeoutExpired:
        server.kill()
        server.wait(timeout=5)


def main() -> None:
    project_name = f"automation-tool-d613-{os.getpid()}"
    database_port = unused_loopback_port()
    control_plane_port = unused_loopback_port()
    require_port_closed(database_port)
    require_port_closed(control_plane_port)
    environment, database_url = isolated_environment(database_port)
    compose = compose_command(project_name)
    server: subprocess.Popen[bytes] | None = None

    try:
        with managed_test_postgres(
            compose=compose,
            database_port=database_port,
            environment=environment,
            repository_root=REPOSITORY_ROOT,
        ):
            print("[D6-13] Applying the production Alembic migration chain")
            subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", "head"],
                check=True,
                cwd=BACKEND_ROOT,
                env=environment,
            )
            credential, installation_id = asyncio.run(seed_active_credential(database_url))
            unbound = asyncio.run(seed_unbound_offer(database_url, installation_id))
            stale, _ = asyncio.run(enqueue_confirmed_offer(database_url, installation_id))
            asyncio.run(invalidate_confirmation(database_url, stale.task_id))

            print("[D6-13] Starting the real Uvicorn Executor boundary")
            server = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "automation_tool.control_plane:create_app",
                    "--factory",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(control_plane_port),
                    "--ws-max-size",
                    str(MAX_EXECUTOR_MESSAGE_BYTES),
                    "--ws",
                    "websockets-sansio",
                    "--no-access-log",
                ],
                cwd=BACKEND_ROOT,
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            wait_for_control_plane(control_plane_port, server)
            session_token = executor_session(control_plane_port, credential)

            print("[D6-13] Proving unbound and stale offers are not delivered")
            prove_guarded_websocket(
                control_plane_port,
                session_token,
                installation_id,
            )
            current, current_confirmation_id = asyncio.run(
                enqueue_confirmed_offer(database_url, installation_id)
            )
            receive_confirmed_websocket(
                control_plane_port,
                session_token,
                installation_id,
                current,
            )
            asyncio.run(
                verify_database_state(
                    database_url,
                    unbound,
                    stale,
                    current,
                    current_confirmation_id,
                )
            )
            print("[D6-13] Real Executor WebSocket confirmation guard passed")
            stop_server(server)
            server = None
    finally:
        stop_server(server)
        require_port_closed(control_plane_port)
        require_port_closed(database_port)


if __name__ == "__main__":
    main()
