#!/usr/bin/env python3
"""Run B5-12 through system Chrome, production WebSocket, Uvicorn, and PostgreSQL."""

from __future__ import annotations

import asyncio
import os
import secrets
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from playwright.sync_api import Route, sync_playwright
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
from sqlalchemy import select

from automation_tool.control_plane.application.device_sessions import (
    DeviceSessionCapability,
)
from automation_tool.control_plane.infrastructure.database import (
    Database,
    platform_session_health,
)
from automation_tool.executor.browser_runtime import BrowserWindow
from automation_tool.executor.ledger import ExecutorLedger
from automation_tool.executor.rpa.douyin.health import DouyinSessionHealthReporter
from automation_tool.executor.transport import (
    connect_executor_websocket,
    serialize_executor_message,
)
from automation_tool.protocol import (
    EXECUTOR_PROTOCOL_VERSION,
    MAX_EXECUTOR_MESSAGE_BYTES,
    ExecutorLifecycleEnvelope,
    PlatformSessionHealthEnvelope,
)

CHROME_EXECUTABLE = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


def isolated_environment(database_port: int) -> tuple[dict[str, str], str]:
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("AUTOMATION_TOOL_")
    }
    password = secrets.token_hex(24)
    database_url = (
        "postgresql+asyncpg://automation_tool_b512:"
        f"{password}@127.0.0.1:{database_port}/automation_tool_b512"
    )
    environment.update(
        {
            "AUTOMATION_TOOL_DEV_DB_USER": "unused_b512_dev",
            "AUTOMATION_TOOL_DEV_DB_PASSWORD": secrets.token_hex(24),
            "AUTOMATION_TOOL_DEV_DB_NAME": "unused_b512_dev",
            "AUTOMATION_TOOL_DEV_DB_PORT": str(unused_loopback_port()),
            "AUTOMATION_TOOL_TEST_DB_USER": "automation_tool_b512",
            "AUTOMATION_TOOL_TEST_DB_PASSWORD": password,
            "AUTOMATION_TOOL_TEST_DB_NAME": "automation_tool_b512",
            "AUTOMATION_TOOL_TEST_DB_PORT": str(database_port),
            "AUTOMATION_TOOL_DATABASE_URL": database_url,
        }
    )
    return environment, database_url


def hello(installation_id: str, executor_id: str) -> ExecutorLifecycleEnvelope:
    now = datetime.now(UTC)
    return ExecutorLifecycleEnvelope.model_validate(
        {
            "protocol_version": EXECUTOR_PROTOCOL_VERSION,
            "message_id": str(uuid4()),
            "message_type": "executor.hello",
            "sent_at": now,
            "deadline_at": now + timedelta(seconds=30),
            "installation_id": installation_id,
            "executor_id": executor_id,
            "correlation_id": str(uuid4()),
            "idempotency_key": f"executor:hello:{executor_id}",
            "sequence": 1,
            "payload": {
                "architecture": "arm64",
                "executor_version": "0.1.0",
                "platform": "macos",
            },
        }
    )


def build_real_browser_report(
    *,
    state_directory: Path,
    installation_id: str,
    executor_id: str,
) -> PlatformSessionHealthEnvelope:
    if not CHROME_EXECUTABLE.is_file():
        raise RuntimeError("B5-12 requires installed system Chrome")
    ledger = ExecutorLedger(
        state_directory=state_directory,
        installation_id=installation_id,
        executor_id=executor_id,
    )
    reporter = DouyinSessionHealthReporter(ledger=ledger)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=CHROME_EXECUTABLE,
            headless=True,
        )
        try:
            page = browser.new_page()

            def fulfill(route: Route) -> None:
                route.fulfill(
                    status=200,
                    content_type="text/html; charset=utf-8",
                    body='<main><div data-e2e="user-avatar">fixture</div></main>',
                )

            page.route("https://www.douyin.com/**", fulfill)
            page.goto("https://www.douyin.com/user/self", wait_until="domcontentloaded")
            return reporter.observe(
                BrowserWindow._for_runtime(object(), page),
                sequence=2,
            )
        finally:
            browser.close()


async def wait_for_projection(database_url: str, installation_id: str) -> None:
    database = Database.from_url(database_url)
    deadline = time.monotonic() + 10
    try:
        while True:
            async with database.session() as session:
                row = (
                    (
                        await session.execute(
                            select(platform_session_health).where(
                                platform_session_health.c.installation_id == UUID(installation_id)
                            )
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
            if row is not None:
                if (
                    set(row)
                    != {
                        "installation_id",
                        "platform",
                        "state",
                        "session_revision",
                        "observed_at",
                        "updated_at",
                    }
                    or row["platform"] != "douyin"
                    or row["state"] != "healthy"
                    or row["session_revision"] != 1
                ):
                    raise RuntimeError("B5-12 persisted an invalid Session projection")
                return
            if time.monotonic() >= deadline:
                raise RuntimeError("B5-12 did not converge before its acceptance deadline")
            await asyncio.sleep(0.05)
    finally:
        await database.close()


def main() -> None:
    project_name = f"automation-tool-b512-{os.getpid()}"
    database_port = unused_loopback_port()
    control_plane_port = unused_loopback_port()
    environment, database_url = isolated_environment(database_port)
    compose = compose_command(project_name)
    server: subprocess.Popen[bytes] | None = None

    try:
        print("[B5-12] Starting isolated PostgreSQL")
        subprocess.run(
            [*compose, "up", "--detach", "--wait", "postgres-test"],
            check=True,
            cwd=REPOSITORY_ROOT,
            env=environment,
        )
        print("[B5-12] Applying the production Alembic migration chain")
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            check=True,
            cwd=BACKEND_ROOT,
            env=environment,
        )
        credential, installation_id = asyncio.run(seed_active_credential(database_url))
        print("[B5-12] Starting the real Uvicorn boundary in the background")
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
        exchanged = post_json(
            control_plane_port,
            "/api/v1/device-sessions",
            credential,
            payload={"capability": DeviceSessionCapability.EXECUTOR_CONNECT.value},
            expected_status=201,
        )
        session_token = exchanged.get("sessionToken")
        if not isinstance(session_token, str):
            raise RuntimeError("B5-12 Session exchange omitted its opaque session")
        executor_id = str(uuid4())
        with tempfile.TemporaryDirectory(
            prefix="automation-tool-b512-",
            dir="/private/tmp",
        ) as temporary:
            report = build_real_browser_report(
                state_directory=Path(temporary),
                installation_id=str(installation_id),
                executor_id=executor_id,
            )
            print("[B5-12] Sending the real detector fact over the production WebSocket")
            websocket = connect_executor_websocket(
                websocket_url=(f"ws://127.0.0.1:{control_plane_port}/api/v1/executors/connect"),
                session_token=session_token,
                open_timeout=timedelta(seconds=5),
                close_timeout=timedelta(seconds=2),
            )
            with websocket:
                websocket.send(serialize_executor_message(hello(str(installation_id), executor_id)))
                websocket.send(serialize_executor_message(report))
                asyncio.run(wait_for_projection(database_url, str(installation_id)))
        print("[B5-12] Real-network non-sensitive Session projection acceptance passed")
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
