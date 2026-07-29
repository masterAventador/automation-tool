#!/usr/bin/env python3
"""Run T3-14 through one hidden Tauri App, Uvicorn, PostgreSQL, and FakeExecutor."""

from __future__ import annotations

import asyncio
import json
import os
import queue
import secrets
import shutil
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from desktop_e2e_prerequisites import (
    prepare_startup_gate,
    require_reserved_port_still_free,
    reserve_control_plane_port,
    startup_gate_environment,
    terminate_app_process_tree,
)
from run_i2_13_acceptance import post_json, require_port_closed
from run_t3_06_acceptance import (
    BACKEND_ROOT,
    FRONTEND_ROOT,
    REPOSITORY_ROOT,
    base64url,
    compose_command,
    unused_loopback_port,
    verify_app_private_data,
    wait_for_control_plane,
)
from sqlalchemy import insert, select, text, update
from sqlalchemy.ext.asyncio import create_async_engine

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
from automation_tool.control_plane.application.task_target_previews import (
    TASK_TARGET_CONFIRMATION_INTENT_VERSION,
    TaskTargetConfirmationIntent,
)
from automation_tool.control_plane.domain import (
    ExecutionAttemptId,
    ExecutionAttemptStatus,
    InstallationId,
    TargetId,
    TaskCommandStatus,
    TaskCommandType,
    TaskEventType,
    TaskId,
    TaskStatus,
)
from automation_tool.control_plane.infrastructure.database import (
    Database,
    SqlAlchemyTaskCommandRepository,
    douyin_search_exposure_definitions,
    execution_attempts,
    task_commands,
    task_events,
    task_target_confirmations,
    task_target_exclusions,
    tasks,
)
from automation_tool.control_plane.infrastructure.database.task_target_repository import (
    SqlAlchemyTaskTargetRepository,
)
# Imported by module path, not from the package root. The shipped package's
# `__init__.py` deliberately re-exports no test doubles: `automation-tool-executor.spec`
# declares `excludes=[]`, so what reaches a customer's installer is decided by the
# import graph alone, and one `import automation_tool.executor` would be enough to
# drag a Fake into the frozen bundle (CLAUDE.md §9.2). This script was left behind
# when that boundary was drawn and had been failing at import ever since.
from automation_tool.executor.fake_client import (
    FakeExecutorClient,
    FakeExecutorClientConfiguration,
)
from automation_tool.executor.fake import (
    FakeExecutorEngine,
    FakeExecutorScenario,
)
from automation_tool.protocol import (
    MAX_EXECUTOR_MESSAGE_BYTES,
    DouyinCandidate,
    DouyinCandidateSource,
    DouyinCandidateSummary,
    DouyinSearchExposureAction,
)

# The Task revision a seeded target confirmation establishes. Creating a Task
# leaves it at revision 1 and confirming its targets advances it once, and the
# production offer guard only accepts a confirmation whose
# `confirmed_task_revision` equals the Task's current revision — so both halves
# of this fixture, and every assertion downstream of it, read the same number.
CONFIRMED_TASK_REVISION = 2

TAURI_CONFIG = FRONTEND_ROOT / "src-tauri" / "tauri.task-termination-e2e.conf.json"
CONTROL_PLANE_PORT = reserve_control_plane_port()
APP_IDENTIFIER = "com.aventador.automationtool.t314acceptance"
ENVIRONMENT_ID = "t314-acceptance"
CANCEL_TASK_KEY = "task:termination:cancel:tauri-acceptance"
EMERGENCY_TASK_KEY = "task:termination:emergency:tauri-acceptance"
DEVICE_CREDENTIAL_FILE = "device-credential-v1"


def require_control_plane_port_available() -> None:
    require_reserved_port_still_free(CONTROL_PLANE_PORT)


def require_hidden_tauri_configuration() -> None:
    configuration = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))
    windows = configuration.get("app", {}).get("windows", [])
    if len(windows) != 1 or windows[0].get("visible") is not False:
        raise RuntimeError("T3-14 Tauri acceptance must run with visible=false")


def app_data_directory() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_IDENTIFIER
    if sys.platform == "win32":
        roaming = os.environ.get("APPDATA")
        if roaming is None:
            raise RuntimeError("Windows roaming AppData is unavailable")
        return Path(roaming) / APP_IDENTIFIER
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg_data_home) if xdg_data_home else Path.home() / ".local" / "share"
    return base / APP_IDENTIFIER


def signed_bootstrap() -> tuple[str, str]:
    signer = Ed25519PrivateKey.generate()
    now = datetime.now(UTC)
    claims = {
        "environmentId": ENVIRONMENT_ID,
        "expiresAt": int((now + timedelta(hours=1)).timestamp()),
        "notBefore": int((now - timedelta(seconds=30)).timestamp()),
        "purpose": "installation.register",
        "version": 1,
    }
    payload = json.dumps(
        claims,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    payload_segment = base64url(payload)
    signing_input = f"atb1.{payload_segment}".encode("ascii")
    token = f"atb1.{payload_segment}.{base64url(signer.sign(signing_input))}"
    public_key = base64url(signer.public_key().public_bytes_raw())
    return token, public_key


def isolated_environment(database_port: int) -> tuple[dict[str, str], str]:
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("AUTOMATION_TOOL_")
    }
    database_password = secrets.token_hex(24)
    database_url = (
        "postgresql+asyncpg://automation_tool_t314:"
        f"{database_password}@127.0.0.1:{database_port}/automation_tool_t314"
    )
    bootstrap_token, bootstrap_public_key = signed_bootstrap()
    environment.update(
        {
            "AUTOMATION_TOOL_DEV_DB_USER": "unused_t314_dev",
            "AUTOMATION_TOOL_DEV_DB_PASSWORD": secrets.token_hex(24),
            "AUTOMATION_TOOL_DEV_DB_NAME": "unused_t314_dev",
            "AUTOMATION_TOOL_DEV_DB_PORT": str(unused_loopback_port()),
            "AUTOMATION_TOOL_TEST_DB_USER": "automation_tool_t314",
            "AUTOMATION_TOOL_TEST_DB_PASSWORD": database_password,
            "AUTOMATION_TOOL_TEST_DB_NAME": "automation_tool_t314",
            "AUTOMATION_TOOL_TEST_DB_PORT": str(database_port),
            "AUTOMATION_TOOL_DATABASE_URL": database_url,
            "AUTOMATION_TOOL_DEMO_ENVIRONMENT_ID": ENVIRONMENT_ID,
            "AUTOMATION_TOOL_DEMO_BOOTSTRAP_PUBLIC_KEY": bootstrap_public_key,
            "AUTOMATION_TOOL_T314_BOOTSTRAP_TOKEN": bootstrap_token,
            "AUTOMATION_TOOL_T314_ENVIRONMENT_ID": ENVIRONMENT_ID,
        }
    )
    return (
        startup_gate_environment(environment, control_plane_port=CONTROL_PLANE_PORT),
        database_url,
    )


async def wait_for_app_task(
    database_url: str,
    private_app_data: Path,
    app_process: subprocess.Popen[bytes],
    task_key: str,
) -> tuple[InstallationId, TaskId, str]:
    engine = create_async_engine(database_url)
    deadline = time.monotonic() + 120
    try:
        while True:
            if app_process.poll() is not None:
                raise RuntimeError("T3-14 hidden App exited before creating both Tasks")
            async with engine.connect() as connection:
                row = (
                    await connection.execute(
                        text(
                            "select installation_id::text, id::text from tasks "
                            "where creation_idempotency_key = :key"
                        ),
                        {"key": task_key},
                    )
                ).one_or_none()
            credential_path = private_app_data / DEVICE_CREDENTIAL_FILE
            if row is not None and credential_path.is_file():
                try:
                    credential = credential_path.read_text(encoding="ascii")
                except (OSError, UnicodeError) as error:
                    raise RuntimeError("T3-14 App credential vault is unreadable") from error
                return InstallationId.parse(row[0]), TaskId.parse(row[1]), credential
            if time.monotonic() >= deadline:
                raise RuntimeError("T3-14 hidden App did not create its Task in time")
            await asyncio.sleep(0.05)
    finally:
        await engine.dispose()


async def seed_attempt_and_offer(
    database_url: str,
    installation_id: InstallationId,
    task_id: TaskId,
    *,
    label: str,
    confirmed_target_revision: bool = False,
) -> TaskCommandRecord:
    database = Database.from_url(database_url)
    attempt_id = ExecutionAttemptId.new()
    now = datetime.now(UTC)
    try:
        async with database.session() as session:
            current = await session.scalar(
                select(tasks.c.status).where(
                    tasks.c.id == task_id.uuid,
                    tasks.c.installation_id == installation_id.uuid,
                )
            )
            if current != TaskStatus.DRAFT.value:
                raise RuntimeError("T3-14 App Task fixture is not draft")
            await session.execute(
                update(tasks)
                .where(
                    tasks.c.id == task_id.uuid,
                    tasks.c.installation_id == installation_id.uuid,
                )
                .values(
                    status=TaskStatus.QUEUED.value,
                    revision=CONFIRMED_TASK_REVISION if confirmed_target_revision else 1,
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
            idempotency_key=f"task:acceptance:offer:{label}",
            deadline_at=now + timedelta(minutes=3),
        )
    finally:
        await database.close()


async def seed_task_confirmation(
    database_url: str,
    installation_id: InstallationId,
    task_id: TaskId,
    *,
    include_target_results: bool,
) -> tuple[TargetId, ...]:
    """Seed the current confirmation required by the production offer guard.

    The guard compares the confirmed action and copy against the Task's own
    Douyin search-exposure definition, so both are read from that definition
    rather than restated here. Drivers whose Task comes from the App's create
    form get whatever the form submitted — the default action is `browse` with
    no copy at all — and a fixture that assumed one hard-coded `comment`
    template silently failed the guard for them.
    """
    database = Database.from_url(database_url)
    now = datetime.now(UTC)
    candidate_facts = (
        (
            ("a715-success", "成功目标", "a715_success"),
            ("a715-skipped", "用户排除目标", "a715_skipped"),
            ("a715-failed", "失败目标", "a715_failed"),
            ("a715-uncertain", "不确定目标", "a715_uncertain"),
        )
        if include_target_results
        else (("acceptance-controlled", "受控目标", "acceptance_controlled"),)
    )
    candidates = tuple(
        DouyinCandidate(
            platform_target_id=platform_target_id,
            summary=DouyinCandidateSummary(
                display_name=display_name,
                public_handle=public_handle,
            ),
            source=DouyinCandidateSource.GENERAL_SEARCH_AUTHOR,
            page_revision=1,
        )
        for platform_target_id, display_name, public_handle in candidate_facts
    )
    try:
        async with database.session() as session:
            definition = (
                (
                    await session.execute(
                        select(
                            douyin_search_exposure_definitions.c.action,
                            douyin_search_exposure_definitions.c.message_template,
                        ).where(
                            douyin_search_exposure_definitions.c.task_id == task_id.uuid,
                            douyin_search_exposure_definitions.c.installation_id
                            == installation_id.uuid,
                        )
                    )
                )
                .mappings()
                .one()
            )
        targets = await SqlAlchemyTaskTargetRepository(database).evaluate_and_replace(
            task_id=task_id,
            installation_id=installation_id,
            candidates=candidates,
            blacklist=(),
            evaluated_at=now,
        )
        selected_target_ids = tuple(
            target.target_id
            for index, target in enumerate(targets)
            if not include_target_results or index != 1
        )
        intent = TaskTargetConfirmationIntent(
            installation_id=installation_id,
            task_id=task_id,
            page_revision=1,
            confirmation_revision=1,
            action=DouyinSearchExposureAction(definition["action"]),
            message_template=definition["message_template"],
            selected_target_ids=selected_target_ids,
        )
        async with database.session() as session:
            if include_target_results:
                await session.execute(
                    insert(task_target_exclusions).values(
                        target_id=targets[1].target_id.uuid,
                        task_id=task_id.uuid,
                        installation_id=installation_id.uuid,
                        page_revision=1,
                        excluded_at=now,
                    )
                )
            await session.execute(
                insert(task_target_confirmations).values(
                    task_id=task_id.uuid,
                    installation_id=installation_id.uuid,
                    page_revision=1,
                    selection_task_revision=1,
                    confirmed_task_revision=CONFIRMED_TASK_REVISION,
                    selected_target_count=intent.selected_target_count,
                    action=intent.action.value,
                    message_template=intent.message_template,
                    intent_version=TASK_TARGET_CONFIRMATION_INTENT_VERSION,
                    intent_fingerprint=intent.fingerprint(),
                    source_message_id=TaskId.new().uuid,
                    source_idempotency_key=f"task:acceptance:confirm:{task_id}",
                    source_fingerprint=secrets.token_bytes(32),
                    confirmed_at=now,
                    created_at=now,
                )
            )
        return tuple(target.target_id for target in targets)
    finally:
        await database.close()


def fake_executor_client(credential: str, installation_id: InstallationId) -> FakeExecutorClient:
    exchanged = post_json(
        CONTROL_PLANE_PORT,
        "/api/v1/device-sessions",
        credential,
        payload={"capability": DeviceSessionCapability.EXECUTOR_CONNECT.value},
        expected_status=201,
    )
    session_token = exchanged.get("sessionToken")
    if not isinstance(session_token, str):
        raise RuntimeError("T3-14 Executor Session exchange omitted its opaque token")
    return FakeExecutorClient(
        configuration=FakeExecutorClientConfiguration(
            websocket_url=(f"ws://127.0.0.1:{CONTROL_PLANE_PORT}/api/v1/executors/connect"),
            session_token=session_token,
        ),
        engine=FakeExecutorEngine(
            installation_id=str(installation_id),
            executor_id=str(uuid4()),
            scenario=FakeExecutorScenario.HOLD,
        ),
    )


async def verify_database_state(
    database_url: str,
    cancel_offer: TaskCommandRecord,
    emergency_offer: TaskCommandRecord,
) -> None:
    database = Database.from_url(database_url)
    expectations = (
        (
            cancel_offer,
            TaskCommandType.TASK_CANCEL,
            TaskEventType.TASK_CANCELLED,
            TaskStatus.CANCELLED,
            ExecutionAttemptStatus.CANCELLED,
        ),
        (
            emergency_offer,
            TaskCommandType.TASK_EMERGENCY_STOP,
            TaskEventType.TASK_OUTCOME_UNCERTAIN,
            TaskStatus.OUTCOME_UNCERTAIN,
            ExecutionAttemptStatus.OUTCOME_UNCERTAIN,
        ),
    )
    try:
        async with database.session() as session:
            for offer, control_type, terminal_event, task_status, attempt_status in expectations:
                command_rows = (
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
                event_rows = (
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
                task_row = (
                    (await session.execute(select(tasks).where(tasks.c.id == offer.task_id.uuid)))
                    .mappings()
                    .one()
                )
                attempt_row = (
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
                if [row["sequence"] for row in command_rows] != [1, 2]:
                    raise RuntimeError("T3-14 command sequence is invalid")
                if [row["command_type"] for row in command_rows] != [
                    TaskCommandType.TASK_OFFER.value,
                    control_type.value,
                ]:
                    raise RuntimeError("T3-14 command vocabulary is invalid")
                if any(
                    row["status"] != TaskCommandStatus.ACKNOWLEDGED.value for row in command_rows
                ):
                    raise RuntimeError("T3-14 commands were not acknowledged")
                if [row["event_type"] for row in event_rows] != [
                    TaskEventType.TASK_STARTED.value,
                    TaskEventType.STEP_STARTED.value,
                    terminal_event.value,
                ]:
                    raise RuntimeError("T3-14 event timeline is invalid")
                if (
                    task_row["status"] != task_status.value
                    or task_row["revision"] != 6
                    or task_row["last_event_sequence"] != 3
                    or attempt_row["status"] != attempt_status.value
                    or attempt_row["revision"] != 4
                    or attempt_row["finished_at"] is None
                ):
                    raise RuntimeError("T3-14 final Task or Attempt projection is invalid")

            capabilities = list(
                await session.scalars(text("select capability from device_sessions order by id"))
            )
        if capabilities.count(DeviceSessionCapability.EXECUTOR_CONNECT.value) != 1 or any(
            capability
            not in {
                DeviceSessionCapability.APP_CONTROL_PLANE.value,
                DeviceSessionCapability.EXECUTOR_CONNECT.value,
            }
            for capability in capabilities
        ):
            raise RuntimeError("T3-14 used an unexpected Session capability")
    finally:
        await database.close()


def main() -> None:
    require_control_plane_port_available()
    require_hidden_tauri_configuration()
    private_app_data = app_data_directory()
    if private_app_data.exists():
        raise RuntimeError("Refusing to reuse an existing T3-14 App data directory")
    prepare_startup_gate(private_app_data)

    project_name = f"automation-tool-t314-{os.getpid()}"
    database_port = unused_loopback_port()
    environment, database_url = isolated_environment(database_port)
    environment["AUTOMATION_TOOL_TASK_TERMINATION_CONFIRMED_REVISION"] = "1"
    compose = compose_command(project_name)
    server: subprocess.Popen[bytes] | None = None
    app_process: subprocess.Popen[bytes] | None = None
    executor_thread: threading.Thread | None = None
    executor_result: queue.Queue[object] = queue.Queue()

    try:
        print("[T3-14] Starting isolated PostgreSQL")
        subprocess.run(
            [*compose, "up", "--detach", "--wait", "postgres-test"],
            check=True,
            cwd=REPOSITORY_ROOT,
            env=environment,
        )
        print("[T3-14] Applying the production Alembic migration chain")
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            check=True,
            cwd=BACKEND_ROOT,
            env=environment,
        )
        print("[T3-14] Starting the real Uvicorn boundary in the background")
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
        print("[T3-14] Running the real Tauri App with visible=false")
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
                label="cancel",
                confirmed_target_revision=True,
            )
        )
        client = fake_executor_client(credential, installation_id)

        def run_executor() -> None:
            try:
                executor_result.put(client.run(max_commands=4))
            except Exception as error:
                executor_result.put(error)

        executor_thread = threading.Thread(target=run_executor, daemon=True)
        executor_thread.start()
        second_installation, emergency_task_id, _ = asyncio.run(
            wait_for_app_task(
                database_url,
                private_app_data,
                app_process,
                EMERGENCY_TASK_KEY,
            )
        )
        if second_installation != installation_id:
            raise RuntimeError("T3-14 Tasks crossed Installation scope")
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
                label="emergency",
                confirmed_target_revision=True,
            )
        )
        try:
            app_exit = app_process.wait(timeout=240)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("T3-14 hidden App acceptance did not finish") from error
        if app_exit != 0:
            raise RuntimeError("T3-14 hidden App acceptance failed")
        app_process = None
        executor_thread.join(timeout=10)
        if executor_thread.is_alive():
            raise RuntimeError("T3-14 FakeExecutor did not finish")
        processed = executor_result.get_nowait()
        if isinstance(processed, Exception):
            raise processed
        if processed != 4:
            raise RuntimeError("T3-14 FakeExecutor did not process both termination paths")
        verify_app_private_data(private_app_data)
        asyncio.run(verify_database_state(database_url, cancel_offer, emergency_offer))
        print("[T3-14] Hidden-App cancel/emergency-stop acceptance passed")
    finally:
        if app_process is not None:
            terminate_app_process_tree(app_process)
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
