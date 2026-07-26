#!/usr/bin/env python3
"""Run H8-02 from the hidden App through cooperative Local Executor cancellation."""

from __future__ import annotations

import asyncio
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from uuid import uuid4

from desktop_e2e_prerequisites import (
    prepare_startup_gate,
    terminate_app_process_tree,
)
from run_h8_01_acceptance import (
    run_offer_fixture,
    seed_local_checkpoint,
    start_real_executor,
    stop_executor,
)
from run_i2_13_acceptance import require_port_closed
from run_t3_06_acceptance import (
    BACKEND_ROOT,
    FRONTEND_ROOT,
    REPOSITORY_ROOT,
    compose_command,
    unused_loopback_port,
    verify_app_private_data,
    wait_for_control_plane,
)
from run_t3_14_acceptance import (
    CANCEL_TASK_KEY,
    CONTROL_PLANE_PORT,
    EMERGENCY_TASK_KEY,
    app_data_directory,
    fake_executor_client,
    isolated_environment,
    require_control_plane_port_available,
    require_hidden_tauri_configuration,
    seed_attempt_and_offer,
    seed_task_confirmation,
    wait_for_app_task,
)
from sqlalchemy import select

from automation_tool.control_plane.application.task_command_delivery import TaskCommandRecord
from automation_tool.control_plane.domain import (
    ExecutionAttemptStatus,
    TaskCommandStatus,
    TaskCommandType,
    TaskEventType,
    TaskStatus,
)
from automation_tool.control_plane.infrastructure.database import (
    Database,
    execution_attempts,
    task_commands,
    task_events,
    tasks,
)
from automation_tool.executor.ledger import AttemptCheckpointState, ExecutorLedgerRejected
from automation_tool.protocol import MAX_EXECUTOR_MESSAGE_BYTES

EXECUTOR_STATE_DIRECTORY_NAME = "h8-02-executor-state"


async def wait_for_cancel_acknowledgement(
    database_url: str,
    original: TaskCommandRecord,
    app_process: subprocess.Popen[bytes],
) -> None:
    database = Database.from_url(database_url)
    deadline = time.monotonic() + 120
    try:
        while True:
            if app_process.poll() is not None:
                raise RuntimeError("H8-02 hidden App exited before the cancel ACK")
            async with database.session() as session:
                cancel = (
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
            if cancel is not None and cancel["status"] == TaskCommandStatus.ACKNOWLEDGED.value:
                if task["status"] != TaskStatus.CANCELLING.value or event_types != [
                    TaskEventType.TASK_STARTED.value,
                    TaskEventType.STEP_STARTED.value,
                ]:
                    raise RuntimeError(
                        "H8-02 projected a terminal result before the atomic action settled"
                    )
                return
            if time.monotonic() >= deadline:
                raise RuntimeError("H8-02 cancel command was not acknowledged in time")
            await asyncio.sleep(0.05)
    finally:
        await database.close()


async def verify_final_database_state(
    database_url: str,
    cancel_offer: TaskCommandRecord,
    emergency_offer: TaskCommandRecord,
) -> None:
    database = Database.from_url(database_url)
    try:
        async with database.session() as session:
            for offer, control_type in (
                (cancel_offer, TaskCommandType.TASK_CANCEL),
                (emergency_offer, TaskCommandType.TASK_EMERGENCY_STOP),
            ):
                commands = (
                    (
                        await session.execute(
                            select(task_commands)
                            .where(task_commands.c.task_id == offer.task_id.uuid)
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
                            .where(task_events.c.task_id == offer.task_id.uuid)
                            .order_by(task_events.c.sequence)
                        )
                    )
                    .mappings()
                    .all()
                )
                task = (
                    (await session.execute(select(tasks).where(tasks.c.id == offer.task_id.uuid)))
                    .mappings()
                    .one()
                )
                attempt = (
                    (
                        await session.execute(
                            select(execution_attempts).where(
                                execution_attempts.c.id == offer.execution_attempt_id.uuid
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
                if [row["command_type"] for row in commands] != [
                    TaskCommandType.TASK_OFFER.value,
                    control_type.value,
                ] or any(row["status"] != TaskCommandStatus.ACKNOWLEDGED.value for row in commands):
                    raise RuntimeError("H8-02 command history is invalid")
                if [row["event_type"] for row in events] != [
                    TaskEventType.TASK_STARTED.value,
                    TaskEventType.STEP_STARTED.value,
                    TaskEventType.TASK_OUTCOME_UNCERTAIN.value,
                ]:
                    raise RuntimeError("H8-02 event timeline is invalid")
                if (
                    task["status"] != TaskStatus.OUTCOME_UNCERTAIN.value
                    or task["revision"] != 6
                    or task["last_event_sequence"] != 3
                    or attempt["status"] != ExecutionAttemptStatus.OUTCOME_UNCERTAIN.value
                    or attempt["revision"] != 4
                    or attempt["finished_at"] is None
                ):
                    raise RuntimeError("H8-02 final Task or Attempt projection is invalid")
    finally:
        await database.close()


def main() -> None:
    require_control_plane_port_available()
    require_hidden_tauri_configuration()
    private_app_data = app_data_directory()
    if private_app_data.exists():
        raise RuntimeError("Refusing to reuse existing H8-02 App data")
    prepare_startup_gate(private_app_data)

    project_name = f"automation-tool-h802-{os.getpid()}"
    database_port = unused_loopback_port()
    environment, database_url = isolated_environment(database_port)
    environment["AUTOMATION_TOOL_H802_CANCEL_OUTCOME_UNCERTAIN"] = "1"
    environment["AUTOMATION_TOOL_TASK_TERMINATION_CONFIRMED_REVISION"] = "1"
    compose = compose_command(project_name)
    server: subprocess.Popen[bytes] | None = None
    app_process: subprocess.Popen[bytes] | None = None
    executor_process: subprocess.Popen[str] | None = None
    fake_thread: threading.Thread | None = None
    fake_result: queue.Queue[object] = queue.Queue()
    local_session_token = ""
    executor_session_token = ""

    try:
        print("[H8-02] Starting isolated PostgreSQL")
        subprocess.run(
            [*compose, "up", "--detach", "--wait", "postgres-test"],
            check=True,
            cwd=REPOSITORY_ROOT,
            env=environment,
        )
        print("[H8-02] Applying the production Alembic migration chain")
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            check=True,
            cwd=BACKEND_ROOT,
            env=environment,
        )
        print("[H8-02] Starting the real Uvicorn boundary in the background")
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
        print("[H8-02] Running the real Tauri App with visible=false")
        app_process = subprocess.Popen(
            ["pnpm", "test:task-termination-tauri"],
            cwd=FRONTEND_ROOT,
            env=environment,
            start_new_session=True,
        )
        installation_id, cancel_task_id, credential = asyncio.run(
            wait_for_app_task(
                database_url,
                private_app_data,
                app_process,
                CANCEL_TASK_KEY,
            )
        )
        asyncio.run(
            seed_task_confirmation(
                database_url,
                installation_id,
                cancel_task_id,
                include_target_results=False,
            )
        )
        cancel_offer = asyncio.run(
            seed_attempt_and_offer(
                database_url,
                installation_id,
                cancel_task_id,
                label="h8-02-cancel",
                confirmed_target_revision=True,
            )
        )
        run_offer_fixture(credential, installation_id)
        executor_id = str(uuid4())
        state_directory = private_app_data / EXECUTOR_STATE_DIRECTORY_NAME
        ledger, dispatched, waiting = seed_local_checkpoint(
            state_directory,
            cancel_offer,
            executor_id,
        )
        executor_process, local_session_token, executor_session_token = start_real_executor(
            credential=credential,
            installation_id=installation_id,
            executor_id=executor_id,
            state_directory=state_directory,
        )
        asyncio.run(wait_for_cancel_acknowledgement(database_url, cancel_offer, app_process))
        try:
            ledger.begin_side_effect_dispatch(
                action_id=waiting[0],
                effect_fingerprint=waiting[1],
                dispatched_at=datetime.now(UTC),
            )
        except ExecutorLedgerRejected:
            pass
        else:
            raise RuntimeError("H8-02 cancel request allowed a new side-effect dispatch")
        ledger.mark_side_effect_uncertain(
            action_id=dispatched[0],
            effect_fingerprint=dispatched[1],
            uncertain_at=datetime.now(UTC),
        )

        second_installation, emergency_task_id, _ = asyncio.run(
            wait_for_app_task(
                database_url,
                private_app_data,
                app_process,
                EMERGENCY_TASK_KEY,
            )
        )
        if second_installation != installation_id:
            raise RuntimeError("H8-02 Tasks crossed Installation scope")
        if executor_process.poll() is not None:
            raise RuntimeError("H8-02 real Executor exited before cleanup")
        stdout, stderr = stop_executor(executor_process)
        if executor_process.returncode != 0 or stderr:
            raise RuntimeError("H8-02 real Executor did not stop cleanly")
        executor_process = None
        if "executor.stopped" not in stdout:
            raise RuntimeError("H8-02 real Executor omitted its authenticated stop event")

        asyncio.run(
            seed_task_confirmation(
                database_url,
                installation_id,
                emergency_task_id,
                include_target_results=False,
            )
        )
        emergency_offer = asyncio.run(
            seed_attempt_and_offer(
                database_url,
                installation_id,
                emergency_task_id,
                label="h8-02-emergency",
                confirmed_target_revision=True,
            )
        )
        fake = fake_executor_client(credential, installation_id)

        def run_fake_executor() -> None:
            try:
                fake_result.put(fake.run(max_commands=2))
            except Exception as error:
                fake_result.put(error)

        fake_thread = threading.Thread(target=run_fake_executor, daemon=True)
        fake_thread.start()
        try:
            app_exit = app_process.wait(timeout=240)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("H8-02 hidden App acceptance did not finish") from error
        if app_exit != 0:
            raise RuntimeError("H8-02 hidden App acceptance failed")
        app_process = None
        fake_thread.join(timeout=10)
        if fake_thread.is_alive():
            raise RuntimeError("H8-02 emergency-stop fixture did not finish")
        processed = fake_result.get_nowait()
        if isinstance(processed, Exception):
            raise processed
        if processed != 2:
            raise RuntimeError("H8-02 emergency-stop fixture missed a command")

        asyncio.run(verify_final_database_state(database_url, cancel_offer, emergency_offer))
        checkpoint = ledger.get_checkpoint(str(cancel_offer.execution_attempt_id))
        if (
            checkpoint is None
            or checkpoint.state is not AttemptCheckpointState.OUTCOME_UNCERTAIN
            or checkpoint.last_command_sequence != 2
            or checkpoint.last_event_sequence != 3
        ):
            raise RuntimeError("H8-02 local checkpoint did not preserve uncertainty")
        private_bytes = ledger.database_path.read_bytes()
        for secret in (local_session_token, executor_session_token, credential):
            if secret.encode() in private_bytes:
                raise RuntimeError("H8-02 local ledger persisted a credential")
        verify_app_private_data(private_app_data)
        print("[H8-02] Hidden-App cooperative cancellation acceptance passed")
    finally:
        if app_process is not None:
            terminate_app_process_tree(app_process)
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
        if fake_thread is not None and fake_thread.is_alive():
            fake_thread.join(timeout=10)
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
