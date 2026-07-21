#!/usr/bin/env python3
"""Run H8-01 from one hidden App through the real pause-aware Local Executor."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from run_i2_13_acceptance import post_json, require_port_closed
from run_t3_06_acceptance import (
    BACKEND_ROOT,
    FRONTEND_ROOT,
    REPOSITORY_ROOT,
    compose_command,
    unused_loopback_port,
    verify_app_private_data,
    wait_for_control_plane,
)
from run_t3_13_acceptance import (
    APP_IDENTIFIER,
    CONTROL_PLANE_PORT,
    app_data_directory,
    isolated_environment,
    require_control_plane_port_available,
    require_hidden_tauri_configuration,
    wait_for_app_task,
)
from run_t3_14_acceptance import seed_attempt_and_offer, seed_task_confirmation
from sqlalchemy import select

from automation_tool.control_plane.application.device_sessions import (
    DeviceSessionCapability,
)
from automation_tool.control_plane.application.task_command_delivery import (
    TaskCommandRecord,
)
from automation_tool.control_plane.domain import (
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
from automation_tool.executor.ledger import (
    AttemptCheckpointState,
    ExecutorLedger,
    ExecutorLedgerRejected,
)
from automation_tool.protocol import (
    ACTION_AUTHORIZATION_VERSION,
    EXECUTOR_PROTOCOL_VERSION,
    MAX_EXECUTOR_MESSAGE_BYTES,
    ActionAuthorizationClaims,
    DouyinSearchExposureAction,
    ProtocolActionId,
    ProtocolExecutionAttemptId,
    ProtocolExecutorId,
    ProtocolInstallationId,
    ProtocolTargetId,
    ProtocolTaskId,
    TaskCommandEnvelope,
    action_authorization_idempotency_key,
)

EXECUTOR_STATE_DIRECTORY_NAME = "h8-01-executor-state"


def resource_id(index: int) -> str:
    return str(UUID(f"823e4567-e89b-42d3-a456-{index:012d}"))


def exchange_executor_session(credential: str) -> str:
    exchanged = post_json(
        CONTROL_PLANE_PORT,
        "/api/v1/device-sessions",
        credential,
        payload={"capability": DeviceSessionCapability.EXECUTOR_CONNECT.value},
        expected_status=201,
    )
    token = exchanged.get("sessionToken")
    if not isinstance(token, str):
        raise RuntimeError("H8-01 Executor Session exchange omitted its opaque token")
    return token


def run_offer_fixture(
    credential: str,
    installation_id: InstallationId,
) -> None:
    client = FakeExecutorClient(
        configuration=FakeExecutorClientConfiguration(
            websocket_url=f"ws://127.0.0.1:{CONTROL_PLANE_PORT}/api/v1/executors/connect",
            session_token=exchange_executor_session(credential),
        ),
        engine=FakeExecutorEngine(
            installation_id=str(installation_id),
            executor_id=str(uuid4()),
            scenario=FakeExecutorScenario.HOLD,
        ),
    )
    if client.run(max_commands=1) != 1:
        raise RuntimeError("H8-01 offer fixture did not establish the running Task")


def action_claims(
    *,
    action_id: str,
    target_id: str,
    installation_id: InstallationId,
    task_id: TaskId,
    attempt_id: str,
    executor_id: str,
    authorized_at: datetime,
) -> ActionAuthorizationClaims:
    typed_action_id = ProtocolActionId(action_id)
    return ActionAuthorizationClaims(
        version=ACTION_AUTHORIZATION_VERSION,
        action_id=typed_action_id,
        target_id=ProtocolTargetId(target_id),
        execution_attempt_id=ProtocolExecutionAttemptId(attempt_id),
        task_id=ProtocolTaskId(str(task_id)),
        installation_id=ProtocolInstallationId(str(installation_id)),
        executor_id=ProtocolExecutorId(executor_id),
        platform="douyin",
        action=DouyinSearchExposureAction.COMMENT,
        idempotency_key=action_authorization_idempotency_key(typed_action_id),
        authorized_at=authorized_at,
        deadline_at=authorized_at + timedelta(minutes=5),
    )


def seed_local_checkpoint(
    state_directory: Path,
    original: TaskCommandRecord,
    executor_id: str,
) -> tuple[ExecutorLedger, tuple[str, bytes], tuple[str, bytes]]:
    state_directory.mkdir(mode=0o700)
    ledger = ExecutorLedger(
        state_directory=state_directory,
        installation_id=str(original.installation_id),
        executor_id=executor_id,
    )
    offer = TaskCommandEnvelope.model_validate(
        {
            "protocol_version": EXECUTOR_PROTOCOL_VERSION,
            "message_id": str(original.message_id),
            "message_type": "task.offer",
            "sent_at": original.created_at,
            "deadline_at": original.deadline_at,
            "installation_id": str(original.installation_id),
            "executor_id": executor_id,
            "correlation_id": str(original.correlation_id),
            "idempotency_key": original.idempotency_key,
            "sequence": original.sequence,
            "payload": {},
            "task_id": str(original.task_id),
            "execution_attempt_id": str(original.execution_attempt_id),
        }
    )
    ledger.receive_command(offer)
    ledger.compare_and_set_checkpoint(
        attempt_id=str(original.execution_attempt_id),
        expected_revision=1,
        state=AttemptCheckpointState.RUNNING,
        last_event_sequence=2,
    )
    base = datetime.now(UTC) - timedelta(seconds=30)
    prepared: list[tuple[str, bytes]] = []
    for offset, index in enumerate((1, 2)):
        claims = action_claims(
            action_id=resource_id(index),
            target_id=resource_id(index + 100),
            installation_id=original.installation_id,
            task_id=original.task_id,
            attempt_id=str(original.execution_attempt_id),
            executor_id=executor_id,
            authorized_at=base,
        )
        effect = hashlib.sha256(f"h8-01-effect-{index}".encode()).digest()
        ledger.admit_action(
            claims=claims,
            authorization_fingerprint=hashlib.sha256(
                f"h8-01-authorization-{index}".encode()
            ).digest(),
            admitted_at=base + timedelta(seconds=offset * 2),
            minimum_interval_seconds=1,
            task_action_limit=100,
        )
        ledger.prepare_side_effect(
            action_id=str(claims.action_id),
            effect_fingerprint=effect,
            prepared_at=base + timedelta(seconds=offset * 2 + 1),
        )
        prepared.append((str(claims.action_id), effect))
    first_action, first_effect = prepared[0]
    ledger.begin_side_effect_dispatch(
        action_id=first_action,
        effect_fingerprint=first_effect,
        dispatched_at=base + timedelta(seconds=2),
    )
    return ledger, prepared[0], prepared[1]


def start_real_executor(
    *,
    credential: str,
    installation_id: InstallationId,
    executor_id: str,
    state_directory: Path,
) -> tuple[subprocess.Popen[str], str, str]:
    local_session_token = secrets.token_hex(32)
    executor_session_token = exchange_executor_session(credential)
    bootstrap = json.dumps(
        {
            "bootstrap_version": "1",
            "websocket_url": f"ws://127.0.0.1:{CONTROL_PLANE_PORT}/api/v1/executors/connect",
            "local_session_token": local_session_token,
            "session_token": executor_session_token,
            "installation_id": str(installation_id),
            "executor_id": executor_id,
            "heartbeat_interval_seconds": 1,
            "state_directory": str(state_directory),
        },
        separators=(",", ":"),
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "automation_tool.executor"],
        cwd=BACKEND_ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if process.stdin is None:
        raise RuntimeError("H8-01 real Executor stdin is unavailable")
    process.stdin.write(bootstrap + "\n")
    process.stdin.flush()
    return process, local_session_token, executor_session_token


async def wait_for_pause_acknowledgement(
    database_url: str,
    original: TaskCommandRecord,
    app_process: subprocess.Popen[bytes],
) -> None:
    database = Database.from_url(database_url)
    deadline = time.monotonic() + 120
    try:
        while True:
            if app_process.poll() is not None:
                raise RuntimeError("H8-01 hidden App exited before the pause ACK")
            async with database.session() as session:
                pause = (
                    (
                        await session.execute(
                            select(task_commands).where(
                                task_commands.c.task_id == original.task_id.uuid,
                                task_commands.c.sequence == 2,
                            )
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                task = (
                    (
                        await session.execute(
                            select(tasks).where(tasks.c.id == original.task_id.uuid)
                        )
                    )
                    .mappings()
                    .one()
                )
                event_types = list(
                    await session.scalars(
                        select(task_events.c.event_type)
                        .where(task_events.c.task_id == original.task_id.uuid)
                        .order_by(task_events.c.sequence)
                    )
                )
            if pause is not None and pause["status"] == TaskCommandStatus.ACKNOWLEDGED.value:
                if task["status"] != TaskStatus.RUNNING.value or event_types != [
                    "task.started",
                    "step.started",
                ]:
                    raise RuntimeError("H8-01 projected PAUSED before the atomic action settled")
                return
            if time.monotonic() >= deadline:
                raise RuntimeError("H8-01 pause command was not acknowledged in time")
            await asyncio.sleep(0.05)
    finally:
        await database.close()


async def verify_final_database_state(
    database_url: str,
    original: TaskCommandRecord,
) -> None:
    database = Database.from_url(database_url)
    try:
        async with database.session() as session:
            commands = (
                (
                    await session.execute(
                        select(task_commands)
                        .where(task_commands.c.task_id == original.task_id.uuid)
                        .order_by(task_commands.c.sequence)
                    )
                )
                .mappings()
                .all()
            )
            events = (
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
            task = (
                (await session.execute(select(tasks).where(tasks.c.id == original.task_id.uuid)))
                .mappings()
                .one()
            )
            attempt = (
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
        if [row["command_type"] for row in commands] != [
            TaskCommandType.TASK_OFFER.value,
            TaskCommandType.TASK_PAUSE.value,
            TaskCommandType.TASK_RESUME.value,
        ] or any(row["status"] != TaskCommandStatus.ACKNOWLEDGED.value for row in commands):
            raise RuntimeError("H8-01 command history is invalid")
        if [row["event_type"] for row in events] != [
            TaskEventType.TASK_STARTED.value,
            TaskEventType.STEP_STARTED.value,
            TaskEventType.TASK_PAUSED.value,
            TaskEventType.TASK_RESUMED.value,
        ]:
            raise RuntimeError("H8-01 event timeline is invalid")
        if (
            task["status"] != TaskStatus.RUNNING.value
            or task["revision"] != 6
            or task["last_event_sequence"] != 4
            or attempt["status"] != ExecutionAttemptStatus.RUNNING.value
            or attempt["revision"] != 4
        ):
            raise RuntimeError("H8-01 final Task or Attempt projection is invalid")
    finally:
        await database.close()


def stop_executor(process: subprocess.Popen[str]) -> tuple[str, str]:
    stop_signal = vars(signal)["CTRL_BREAK_EVENT"] if sys.platform == "win32" else signal.SIGTERM
    process.send_signal(stop_signal)
    return process.communicate(timeout=10)


def main() -> None:
    require_control_plane_port_available()
    require_hidden_tauri_configuration()
    private_app_data = app_data_directory()
    if private_app_data.exists():
        raise RuntimeError(f"Refusing to reuse existing {APP_IDENTIFIER} App data")

    project_name = f"automation-tool-h801-{os.getpid()}"
    database_port = unused_loopback_port()
    environment, database_url = isolated_environment(database_port)
    compose = compose_command(project_name)
    server: subprocess.Popen[bytes] | None = None
    app_process: subprocess.Popen[bytes] | None = None
    executor_process: subprocess.Popen[str] | None = None
    local_session_token = ""
    executor_session_token = ""

    try:
        print("[H8-01] Starting isolated PostgreSQL")
        subprocess.run(
            [*compose, "up", "--detach", "--wait", "postgres-test"],
            check=True,
            cwd=REPOSITORY_ROOT,
            env=environment,
        )
        print("[H8-01] Applying the production Alembic migration chain")
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            check=True,
            cwd=BACKEND_ROOT,
            env=environment,
        )
        print("[H8-01] Starting the real Uvicorn boundary in the background")
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
                str(CONTROL_PLANE_PORT),
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
        wait_for_control_plane()
        print("[H8-01] Running the real Tauri App with visible=false")
        app_process = subprocess.Popen(
            ["pnpm", "test:task-control-tauri"],
            cwd=FRONTEND_ROOT,
            env=environment,
        )
        installation_id, task_id, credential = asyncio.run(
            wait_for_app_task(database_url, private_app_data, app_process)
        )
        asyncio.run(
            seed_task_confirmation(
                database_url,
                installation_id,
                task_id,
                include_target_results=False,
            )
        )
        original = asyncio.run(
            seed_attempt_and_offer(
                database_url,
                installation_id,
                task_id,
                label="h8-01-safe-pause",
                confirmed_target_revision=True,
            )
        )
        run_offer_fixture(credential, installation_id)
        executor_id = str(uuid4())
        state_directory = private_app_data / EXECUTOR_STATE_DIRECTORY_NAME
        ledger, dispatched, waiting = seed_local_checkpoint(
            state_directory,
            original,
            executor_id,
        )
        executor_process, local_session_token, executor_session_token = start_real_executor(
            credential=credential,
            installation_id=installation_id,
            executor_id=executor_id,
            state_directory=state_directory,
        )
        asyncio.run(wait_for_pause_acknowledgement(database_url, original, app_process))
        try:
            ledger.begin_side_effect_dispatch(
                action_id=waiting[0],
                effect_fingerprint=waiting[1],
                dispatched_at=datetime.now(UTC),
            )
        except ExecutorLedgerRejected:
            pass
        else:
            raise RuntimeError("H8-01 pause request allowed a new side-effect dispatch")
        ledger.verify_side_effect(
            action_id=dispatched[0],
            effect_fingerprint=dispatched[1],
            verification_fingerprint=hashlib.sha256(b"h8-01-verified").digest(),
            verified_at=datetime.now(UTC),
        )
        try:
            app_exit = app_process.wait(timeout=180)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("H8-01 hidden App acceptance did not finish") from error
        if app_exit != 0:
            raise RuntimeError("H8-01 hidden App acceptance failed")
        app_process = None
        asyncio.run(verify_final_database_state(database_url, original))
        checkpoint = ledger.get_checkpoint(str(original.execution_attempt_id))
        if (
            checkpoint is None
            or checkpoint.state is not AttemptCheckpointState.RUNNING
            or checkpoint.last_command_sequence != 3
            or checkpoint.last_event_sequence != 4
        ):
            raise RuntimeError("H8-01 local checkpoint did not resume from the safe pause")
        if executor_process.poll() is not None:
            raise RuntimeError("H8-01 real Executor exited before cleanup")
        stdout, stderr = stop_executor(executor_process)
        if executor_process.returncode != 0 or stderr:
            raise RuntimeError("H8-01 real Executor did not stop cleanly")
        executor_process = None
        if "executor.stopped" not in stdout:
            raise RuntimeError("H8-01 real Executor omitted its authenticated stop event")
        private_bytes = ledger.database_path.read_bytes()
        for secret in (local_session_token, executor_session_token, credential):
            if secret.encode() in private_bytes:
                raise RuntimeError("H8-01 local ledger persisted a credential")
        verify_app_private_data(private_app_data)
        print("[H8-01] Hidden-App safe pause/resume acceptance passed")
    finally:
        if app_process is not None and app_process.poll() is None:
            app_process.terminate()
            try:
                app_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                app_process.kill()
                app_process.wait(timeout=5)
        if executor_process is not None and executor_process.poll() is None:
            executor_process.terminate()
            try:
                executor_process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                executor_process.kill()
                executor_process.communicate(timeout=5)
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
        if private_app_data.exists():
            shutil.rmtree(private_app_data)
        require_port_closed(CONTROL_PLANE_PORT)
        require_port_closed(database_port)


if __name__ == "__main__":
    main()
