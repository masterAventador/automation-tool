#!/usr/bin/env python3
"""Run T3-11 through FakeExecutor, real Uvicorn, and PostgreSQL."""

from __future__ import annotations

import asyncio
import os
import secrets
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from run_i2_13_acceptance import (
    BACKEND_ROOT,
    REPOSITORY_ROOT,
    compose_command,
    post_json,
    require_port_closed,
    seed_active_credential,
    unused_loopback_port,
    wait_for_control_plane,
)
from sqlalchemy import insert, select, update

from automation_tool.control_plane.application.device_sessions import (
    DeviceSessionCapability,
)
from automation_tool.control_plane.application.executor_connection_registry import (
    ExecutorConnectionRegistry,
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
    TaskEventType,
    TaskId,
    TaskStatus,
)
from automation_tool.control_plane.infrastructure.database import (
    Database,
    SqlAlchemyTaskCommandRepository,
    execution_attempts,
    task_commands,
    task_events,
    tasks,
)
from automation_tool.executor import (
    FakeExecutorClient,
    FakeExecutorClientConfiguration,
    FakeExecutorEngine,
    FakeExecutorScenario,
)
from automation_tool.protocol import MAX_EXECUTOR_MESSAGE_BYTES


def isolated_environment(database_port: int) -> tuple[dict[str, str], str]:
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("AUTOMATION_TOOL_")
    }
    database_password = secrets.token_hex(24)
    database_url = (
        "postgresql+asyncpg://automation_tool_t311:"
        f"{database_password}@127.0.0.1:{database_port}/automation_tool_t311"
    )
    environment.update(
        {
            "AUTOMATION_TOOL_DEV_DB_USER": "unused_t311_dev",
            "AUTOMATION_TOOL_DEV_DB_PASSWORD": secrets.token_hex(24),
            "AUTOMATION_TOOL_DEV_DB_NAME": "unused_t311_dev",
            "AUTOMATION_TOOL_DEV_DB_PORT": str(unused_loopback_port()),
            "AUTOMATION_TOOL_TEST_DB_USER": "automation_tool_t311",
            "AUTOMATION_TOOL_TEST_DB_PASSWORD": database_password,
            "AUTOMATION_TOOL_TEST_DB_NAME": "automation_tool_t311",
            "AUTOMATION_TOOL_TEST_DB_PORT": str(database_port),
            "AUTOMATION_TOOL_DATABASE_URL": database_url,
        }
    )
    return environment, database_url


async def seed_attempt_and_offer(
    database_url: str,
    installation_id: InstallationId,
) -> TaskCommandRecord:
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
                    creation_idempotency_key=f"task:t311:{task_id}",
                    status=TaskStatus.QUEUED.value,
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
                    status=ExecutionAttemptStatus.ACCEPTED.value,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.execute(
                update(tasks)
                .where(tasks.c.id == task_id.uuid)
                .values(current_attempt_id=attempt_id.uuid)
            )
        service = TaskCommandDeliveryService(
            repository=SqlAlchemyTaskCommandRepository(database),
            registry=ExecutorConnectionRegistry(),
        )
        return await service.enqueue(
            installation_id=installation_id,
            task_id=task_id,
            execution_attempt_id=attempt_id,
            sequence=1,
            command_type=TaskCommandType.TASK_OFFER,
            idempotency_key="task:t311:offer:1",
            deadline_at=now + timedelta(minutes=3),
        )
    finally:
        await database.close()


def exercise_fake_executor(
    port: int,
    credential: str,
    installation_id: InstallationId,
) -> None:
    exchanged = post_json(
        port,
        "/api/v1/device-sessions",
        credential,
        payload={"capability": DeviceSessionCapability.EXECUTOR_CONNECT.value},
        expected_status=201,
    )
    session_token = exchanged.get("sessionToken")
    if not isinstance(session_token, str):
        raise RuntimeError("T3-11 Session exchange omitted its opaque session")
    client = FakeExecutorClient(
        configuration=FakeExecutorClientConfiguration(
            websocket_url=f"ws://127.0.0.1:{port}/api/v1/executors/connect",
            session_token=session_token,
        ),
        engine=FakeExecutorEngine(
            installation_id=str(installation_id),
            executor_id=str(uuid4()),
            scenario=FakeExecutorScenario.SUCCEED,
        ),
    )
    if client.run(max_commands=1) != 1:
        raise RuntimeError("T3-11 FakeExecutor did not process the persistent offer")


async def wait_for_convergence(database_url: str, original: TaskCommandRecord) -> None:
    database = Database.from_url(database_url)
    deadline = time.monotonic() + 10
    try:
        while True:
            async with database.session() as session:
                command_row = (
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
                task_row = (
                    (
                        await session.execute(
                            select(tasks).where(tasks.c.id == original.task_id.uuid)
                        )
                    )
                    .mappings()
                    .one()
                )
                attempt_row = (
                    (
                        await session.execute(
                            select(execution_attempts).where(
                                execution_attempts.c.id == original.execution_attempt_id.uuid
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
                event_rows = (
                    (
                        await session.execute(
                            select(task_events)
                            .where(task_events.c.task_id == original.task_id.uuid)
                            .order_by(task_events.c.sequence)
                        )
                    )
                    .mappings()
                    .all()
                )
            complete = (
                command_row["status"] == TaskCommandStatus.ACKNOWLEDGED.value
                and task_row["status"] == TaskStatus.SUCCEEDED.value
                and task_row["last_event_sequence"] == 5
                and attempt_row["status"] == ExecutionAttemptStatus.SUCCEEDED.value
                and len(event_rows) == 5
            )
            if complete:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("T3-11 did not converge before the acceptance deadline")
            await asyncio.sleep(0.05)
    finally:
        await database.close()

    if (
        command_row["delivery_attempts"] != 1
        or command_row["response_type"] != "task.accept"
        or command_row["response_message_id"] is None
        or command_row["acknowledged_at"] is None
    ):
        raise RuntimeError("T3-11 command acknowledgement state is invalid")
    if (
        task_row["revision"] != 6
        or attempt_row["revision"] != 3
        or attempt_row["started_at"] is None
        or attempt_row["finished_at"] is None
    ):
        raise RuntimeError("T3-11 Task or Attempt projection is invalid")
    expected_types = [
        TaskEventType.TASK_STARTED.value,
        TaskEventType.STEP_STARTED.value,
        TaskEventType.STEP_PROGRESS.value,
        TaskEventType.STEP_COMPLETED.value,
        TaskEventType.TASK_COMPLETED.value,
    ]
    if (
        [row["sequence"] for row in event_rows] != [1, 2, 3, 4, 5]
        or [row["event_type"] for row in event_rows] != expected_types
        or [row["task_revision"] for row in event_rows] != [2, 3, 4, 5, 6]
        or any(len(row["source_fingerprint"]) != 32 for row in event_rows)
        or any(not row["source_idempotency_key"] for row in event_rows)
    ):
        raise RuntimeError("T3-11 durable event timeline is invalid")


def main() -> None:
    project_name = f"automation-tool-t311-{os.getpid()}"
    database_port = unused_loopback_port()
    control_plane_port = unused_loopback_port()
    environment, database_url = isolated_environment(database_port)
    compose = compose_command(project_name)
    server: subprocess.Popen[bytes] | None = None

    try:
        print("[T3-11] Starting isolated PostgreSQL")
        subprocess.run(
            [*compose, "up", "--detach", "--wait", "postgres-test"],
            check=True,
            cwd=REPOSITORY_ROOT,
            env=environment,
        )
        print("[T3-11] Applying the production Alembic migration chain")
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            check=True,
            cwd=BACKEND_ROOT,
            env=environment,
        )
        credential, installation_id = asyncio.run(seed_active_credential(database_url))
        original = asyncio.run(seed_attempt_and_offer(database_url, installation_id))
        print("[T3-11] Starting the real Uvicorn boundary in the background")
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
        print("[T3-11] Replaying success events through the production WebSocket")
        exercise_fake_executor(control_plane_port, credential, installation_id)
        asyncio.run(wait_for_convergence(database_url, original))
        print("[T3-11] Real-network event convergence acceptance passed")
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
