#!/usr/bin/env python3
"""Run the isolated T3-09 persistent command delivery acceptance."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

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
from sqlalchemy import insert, select
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import ClientConnection

from automation_tool.control_plane.api.executor_websocket import (
    EXECUTOR_CLOSE_PROTOCOL_REJECTED,
)
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
from automation_tool.control_plane.domain import (
    ExecutionAttemptId,
    ExecutionAttemptStatus,
    InstallationId,
    TaskCommandStatus,
    TaskCommandType,
    TaskId,
)
from automation_tool.control_plane.infrastructure.database import (
    Database,
    SqlAlchemyTaskCommandRepository,
    execution_attempts,
    task_commands,
    tasks,
)
from automation_tool.protocol import MAX_EXECUTOR_MESSAGE_BYTES


def isolated_environment(database_port: int) -> tuple[dict[str, str], str]:
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("AUTOMATION_TOOL_")
    }
    database_password = secrets.token_hex(24)
    database_url = (
        "postgresql+asyncpg://automation_tool_t309:"
        f"{database_password}@127.0.0.1:{database_port}/automation_tool_t309"
    )
    environment.update(
        {
            "AUTOMATION_TOOL_DEV_DB_USER": "unused_t309_dev",
            "AUTOMATION_TOOL_DEV_DB_PASSWORD": secrets.token_hex(24),
            "AUTOMATION_TOOL_DEV_DB_NAME": "unused_t309_dev",
            "AUTOMATION_TOOL_DEV_DB_PORT": str(unused_loopback_port()),
            "AUTOMATION_TOOL_TEST_DB_USER": "automation_tool_t309",
            "AUTOMATION_TOOL_TEST_DB_PASSWORD": database_password,
            "AUTOMATION_TOOL_TEST_DB_NAME": "automation_tool_t309",
            "AUTOMATION_TOOL_TEST_DB_PORT": str(database_port),
            "AUTOMATION_TOOL_DATABASE_URL": database_url,
        }
    )
    return environment, database_url


def lifecycle_message(
    installation_id: InstallationId,
    executor_id: str,
    *,
    message_type: str,
    sequence: int,
) -> str:
    sent_at = datetime.now(UTC)
    payload = (
        {
            "architecture": "arm64",
            "executor_version": "0.1.0",
            "platform": "macos",
        }
        if message_type == "executor.hello"
        else {"status": "healthy"}
    )
    return json.dumps(
        {
            "protocol_version": "1.0",
            "message_id": str(uuid4()),
            "message_type": message_type,
            "sent_at": sent_at.isoformat(timespec="microseconds").replace("+00:00", "Z"),
            "deadline_at": (sent_at + timedelta(seconds=30))
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z"),
            "installation_id": str(installation_id),
            "executor_id": executor_id,
            "correlation_id": str(uuid4()),
            "idempotency_key": f"executor:{message_type}:{uuid4()}",
            "sequence": sequence,
            "payload": payload,
        },
        separators=(",", ":"),
    )


def command_response(
    offer: dict[str, object],
    *,
    message_id: UUID,
    correlation_id: str | None = None,
) -> str:
    sent_at = datetime.now(UTC)
    return json.dumps(
        {
            "protocol_version": "1.0",
            "message_id": str(message_id),
            "message_type": "task.accept",
            "sent_at": sent_at.isoformat(timespec="microseconds").replace("+00:00", "Z"),
            "deadline_at": (sent_at + timedelta(seconds=30))
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z"),
            "installation_id": offer["installation_id"],
            "executor_id": offer["executor_id"],
            "correlation_id": correlation_id or offer["correlation_id"],
            "idempotency_key": f"task:accept:{message_id}",
            "sequence": offer["sequence"],
            "payload": {"accepted": True},
            "task_id": offer["task_id"],
            "execution_attempt_id": offer["execution_attempt_id"],
        },
        separators=(",", ":"),
    )


def receive_offer(connection: ClientConnection) -> dict[str, object]:
    source = connection.recv(timeout=5)
    if not isinstance(source, str):
        raise RuntimeError("T3-09 received a non-text command")
    parsed = json.loads(source)
    if not isinstance(parsed, dict) or parsed.get("message_type") != "task.offer":
        raise RuntimeError("T3-09 received an unexpected command")
    return parsed


def expect_closed(connection: ClientConnection, *, code: int, reason: str) -> None:
    try:
        connection.recv(timeout=5)
    except ConnectionClosed as error:
        if error.code == code and error.reason == reason:
            return
    raise RuntimeError("T3-09 WebSocket closed with an unexpected public result")


def expect_no_command(connection: ClientConnection) -> None:
    try:
        connection.recv(timeout=1.5)
    except TimeoutError:
        return
    except ConnectionClosed as error:
        raise RuntimeError("T3-09 Executor closed while checking expiry") from error
    raise RuntimeError("T3-09 delivered a command after its deadline")


async def seed_attempt_and_offer(
    database_url: str,
    installation_id: InstallationId,
) -> tuple[TaskId, ExecutionAttemptId, TaskCommandRecord]:
    database = Database.from_url(database_url)
    task_id = TaskId.new()
    attempt_id = ExecutionAttemptId.new()
    now = datetime.now(UTC)
    try:
        async with database.session() as session:
            await session.execute(
                insert(tasks).values(
                    id=task_id.uuid,
                    installation_id=installation_id.uuid,
                    creation_idempotency_key=f"task:t309:{task_id}",
                    created_at=now,
                    updated_at=now,
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
        service = TaskCommandDeliveryService(
            repository=SqlAlchemyTaskCommandRepository(database),
            registry=ExecutorConnectionRegistry(),
        )
        offer = await service.enqueue(
            installation_id=installation_id,
            task_id=task_id,
            execution_attempt_id=attempt_id,
            sequence=1,
            command_type=TaskCommandType.TASK_OFFER,
            idempotency_key="task:t309:offer:1",
            deadline_at=now + timedelta(minutes=3),
        )
        return task_id, attempt_id, offer
    finally:
        await database.close()


async def enqueue_expiring_offer(
    database_url: str,
    installation_id: InstallationId,
    task_id: TaskId,
    attempt_id: ExecutionAttemptId,
) -> TaskCommandRecord:
    database = Database.from_url(database_url)
    try:
        service = TaskCommandDeliveryService(
            repository=SqlAlchemyTaskCommandRepository(database),
            registry=ExecutorConnectionRegistry(),
        )
        return await service.enqueue(
            installation_id=installation_id,
            task_id=task_id,
            execution_attempt_id=attempt_id,
            sequence=2,
            command_type=TaskCommandType.TASK_OFFER,
            idempotency_key="task:t309:offer:expires",
            deadline_at=datetime.now(UTC) + timedelta(milliseconds=500),
        )
    finally:
        await database.close()


def exercise_real_delivery(
    port: int,
    credential: str,
    installation_id: InstallationId,
    database_url: str,
    task_id: TaskId,
    attempt_id: ExecutionAttemptId,
) -> tuple[UUID, UUID, TaskCommandRecord]:
    exchanged = post_json(
        port,
        "/api/v1/device-sessions",
        credential,
        payload={"capability": DeviceSessionCapability.EXECUTOR_CONNECT.value},
        expected_status=201,
    )
    session_token = exchanged.get("sessionToken")
    if not isinstance(session_token, str):
        raise RuntimeError("T3-09 Session exchange omitted its opaque session")
    executor_id = str(uuid4())

    with open_executor(port, session_token, EXECUTOR_WEBSOCKET_SUBPROTOCOL) as first:
        first.send(
            lifecycle_message(
                installation_id,
                executor_id,
                message_type="executor.hello",
                sequence=1,
            )
        )
        first_offer = receive_offer(first)

    with open_executor(port, session_token, EXECUTOR_WEBSOCKET_SUBPROTOCOL) as second:
        second.send(
            lifecycle_message(
                installation_id,
                executor_id,
                message_type="executor.hello",
                sequence=1,
            )
        )
        second_offer = receive_offer(second)
        if second_offer["message_id"] != first_offer["message_id"]:
            raise RuntimeError("T3-09 reconnect did not replay the same persistent command")
        second.send(
            command_response(
                second_offer,
                message_id=uuid4(),
                correlation_id=str(uuid4()),
            )
        )
        expect_closed(
            second,
            code=EXECUTOR_CLOSE_PROTOCOL_REJECTED,
            reason="Executor protocol is rejected",
        )

    first_response_id = uuid4()
    duplicate_response_id = uuid4()
    with open_executor(port, session_token, EXECUTOR_WEBSOCKET_SUBPROTOCOL) as recovered:
        recovered.send(
            lifecycle_message(
                installation_id,
                executor_id,
                message_type="executor.hello",
                sequence=1,
            )
        )
        recovered_offer = receive_offer(recovered)
        if recovered_offer["message_id"] != first_offer["message_id"]:
            raise RuntimeError("T3-09 recovery changed the persistent command identity")
        recovered.send(
            command_response(
                recovered_offer,
                message_id=first_response_id,
            )
        )
        recovered.send(
            command_response(
                recovered_offer,
                message_id=duplicate_response_id,
            )
        )
        recovered.send(
            lifecycle_message(
                installation_id,
                executor_id,
                message_type="executor.heartbeat",
                sequence=2,
            )
        )
        expect_no_command(recovered)

    expiring = asyncio.run(
        enqueue_expiring_offer(
            database_url,
            installation_id,
            task_id,
            attempt_id,
        )
    )
    time.sleep(0.8)
    with open_executor(port, session_token, EXECUTOR_WEBSOCKET_SUBPROTOCOL) as after_deadline:
        after_deadline.send(
            lifecycle_message(
                installation_id,
                executor_id,
                message_type="executor.hello",
                sequence=1,
            )
        )
        expect_no_command(after_deadline)
    return first_response_id, duplicate_response_id, expiring


async def verify_database_state(
    database_url: str,
    original: TaskCommandRecord,
    first_response_id: UUID,
    duplicate_response_id: UUID,
    expiring: TaskCommandRecord,
) -> None:
    database = Database.from_url(database_url)
    try:
        async with database.session() as session:
            original_row = (
                (
                    await session.execute(
                        select(task_commands).where(
                            task_commands.c.message_id == original.message_id
                        )
                    )
                )
                .mappings()
                .one()
            )
            expiring_row = (
                (
                    await session.execute(
                        select(task_commands).where(
                            task_commands.c.message_id == expiring.message_id
                        )
                    )
                )
                .mappings()
                .one()
            )
    finally:
        await database.close()
    if (
        original_row["status"] != TaskCommandStatus.ACKNOWLEDGED.value
        or original_row["delivery_attempts"] != 3
        or original_row["response_message_id"] != first_response_id
        or original_row["response_type"] != "task.accept"
        or original_row["acknowledged_at"] is None
    ):
        raise RuntimeError("T3-09 acknowledged command database state is invalid")
    if original_row["response_message_id"] == duplicate_response_id:
        raise RuntimeError("T3-09 duplicate ACK overwrote the first response fact")
    if (
        expiring_row["status"] != TaskCommandStatus.EXPIRED.value
        or expiring_row["delivery_attempts"] != 0
        or expiring_row["response_message_id"] is not None
    ):
        raise RuntimeError("T3-09 expired command database state is invalid")


def main() -> None:
    project_name = f"automation-tool-t309-{os.getpid()}"
    database_port = unused_loopback_port()
    control_plane_port = unused_loopback_port()
    environment, database_url = isolated_environment(database_port)
    compose = compose_command(project_name)
    server: subprocess.Popen[bytes] | None = None

    try:
        print("[T3-09] Starting isolated PostgreSQL")
        subprocess.run(
            [*compose, "up", "--detach", "--wait", "postgres-test"],
            check=True,
            cwd=REPOSITORY_ROOT,
            env=environment,
        )
        print("[T3-09] Applying the production Alembic migration chain")
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            check=True,
            cwd=BACKEND_ROOT,
            env=environment,
        )
        credential, installation_id = asyncio.run(seed_active_credential(database_url))
        task_id, attempt_id, original = asyncio.run(
            seed_attempt_and_offer(database_url, installation_id)
        )
        print("[T3-09] Starting the real Uvicorn network boundary in the background")
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
        print("[T3-09] Exercising delivery, reconnect replay, ACK, dedupe, and expiry")
        first_response_id, duplicate_response_id, expiring = exercise_real_delivery(
            control_plane_port,
            credential,
            installation_id,
            database_url,
            task_id,
            attempt_id,
        )
        asyncio.run(
            verify_database_state(
                database_url,
                original,
                first_response_id,
                duplicate_response_id,
                expiring,
            )
        )
        print("[T3-09] Real-network command delivery acceptance passed")
    finally:
        if server is not None and server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
        subprocess.run(
            [*compose, "down", "--volumes", "--remove-orphans"],
            check=False,
            cwd=REPOSITORY_ROOT,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        require_port_closed(control_plane_port)
        require_port_closed(database_port)


if __name__ == "__main__":
    main()
