from __future__ import annotations

import json
import queue
import secrets
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import uvicorn

from automation_tool.control_plane import create_app
from automation_tool.control_plane.application.device_credentials import ParsedDeviceCredential
from automation_tool.control_plane.application.device_sessions import (
    AuthenticatedDeviceSession,
    DeviceSessionCapability,
    DeviceSessionFactory,
    DeviceSessionRejected,
    DeviceSessionService,
    IssuedDeviceSession,
    ParsedDeviceSession,
    PendingDeviceSession,
)
from automation_tool.control_plane.application.executor_connection_registry import (
    ExecutorConnectionChannel,
    ExecutorConnectionRegistry,
    OnlineExecutorConnection,
)
from automation_tool.control_plane.application.executor_connections import (
    BoundExecutorConnection,
    ExecutorConnectionService,
)

NOW = datetime(2026, 7, 19, 8, 0, tzinfo=UTC)
INSTALLATION_ID = UUID("123e4567-e89b-42d3-a456-426614174003")
EXECUTOR_ID = UUID("123e4567-e89b-42d3-a456-426614174004")
LOCAL_SESSION_TOKEN = "05" * 32
CREDENTIAL_ID = UUID("123e4567-e89b-42d3-a456-426614174007")
BACKEND_ROOT = Path(__file__).resolve().parents[2]
EXECUTOR_ENTRY = Path(sys.executable).with_name("automation-tool-executor")


class FixedClock:
    @staticmethod
    def now() -> datetime:
        return NOW


@dataclass
class LiveSessionRepository:
    expected: ParsedDeviceSession

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
        if (
            presented_session != self.expected
            or required_capability is not DeviceSessionCapability.EXECUTOR_CONNECT
        ):
            raise DeviceSessionRejected
        return AuthenticatedDeviceSession(
            session_id=presented_session.session_id,
            installation_id=INSTALLATION_ID,
            credential_id=CREDENTIAL_ID,
            credential_version=1,
            capability=required_capability,
            expires_at=authenticated_at + timedelta(minutes=5),
        )


class ObservedRegistry(ExecutorConnectionRegistry):
    def __init__(self) -> None:
        super().__init__()
        self.events: queue.Queue[tuple[str, OnlineExecutorConnection | bool]] = queue.Queue()

    async def register(
        self,
        bound: BoundExecutorConnection,
        channel: ExecutorConnectionChannel,
    ) -> OnlineExecutorConnection:
        online = await super().register(bound, channel)
        self.events.put(("registered", online))
        return online

    async def record_heartbeat(
        self,
        bound: BoundExecutorConnection,
        *,
        sequence: int,
    ) -> OnlineExecutorConnection:
        online = await super().record_heartbeat(bound, sequence=sequence)
        self.events.put(("heartbeat", online))
        return online

    async def unregister(self, bound: BoundExecutorConnection) -> bool:
        removed = await super().unregister(bound)
        self.events.put(("unregistered", removed))
        return removed


@dataclass
class RunningControlPlane:
    server: uvicorn.Server
    thread: threading.Thread
    port: int
    registry: ObservedRegistry
    session_token: str

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5)
        assert not self.thread.is_alive()


def start_control_plane() -> RunningControlPlane:
    material = DeviceSessionFactory(secret_source=secrets.token_bytes, id_source=uuid4).create()
    sessions = DeviceSessionService(
        repository=LiveSessionRepository(
            expected=ParsedDeviceSession(
                session_id=material.session_id,
                secret_digest=material.secret_digest,
            )
        ),
        clock=FixedClock(),
        session_factory=DeviceSessionFactory(
            secret_source=secrets.token_bytes,
            id_source=uuid4,
        ),
    )
    registry = ObservedRegistry()
    app = create_app(
        database=None,
        executor_connection_service=ExecutorConnectionService(sessions),
        executor_connection_registry=registry,
        executor_connection_hello_timeout_seconds=1,
        executor_connection_recheck_interval_seconds=0.05,
    )
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = int(listener.getsockname()[1])
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            log_level="critical",
            access_log=False,
            ws="websockets-sansio",
        )
    )
    thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [listener]},
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    return RunningControlPlane(server, thread, port, registry, material.session_token)


def start_executor(
    control_plane: RunningControlPlane,
    state_directory: Path,
) -> tuple[subprocess.Popen[str], str]:
    bootstrap = json.dumps(
        {
            "bootstrap_version": "1",
            "websocket_url": (f"ws://127.0.0.1:{control_plane.port}/api/v1/executors/connect"),
            "local_session_token": LOCAL_SESSION_TOKEN,
            "session_token": control_plane.session_token,
            "installation_id": str(INSTALLATION_ID),
            "executor_id": str(EXECUTOR_ID),
            "heartbeat_interval_seconds": 1,
            "state_directory": str(state_directory),
        },
        separators=(",", ":"),
    )
    process = subprocess.Popen(
        [str(EXECUTOR_ENTRY)],
        cwd=BACKEND_ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=(
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if sys.platform == "win32" else 0
        ),
    )
    stdin = process.stdin
    assert stdin is not None
    stdin.write(bootstrap + "\n")
    stdin.flush()
    return process, bootstrap


def stop_process(process: subprocess.Popen[str]) -> tuple[str, str]:
    stop_signal = vars(signal)["CTRL_BREAK_EVENT"] if sys.platform == "win32" else signal.SIGTERM
    process.send_signal(stop_signal)
    stdout, stderr = process.communicate(timeout=5)
    return stdout, stderr


def test_real_process_bootstraps_over_stdin_heartbeats_to_control_plane_and_stops(
    tmp_path: Path,
) -> None:
    control_plane = start_control_plane()
    process: subprocess.Popen[str] | None = None
    try:
        state_directory = tmp_path / "executor-state"
        process, bootstrap = start_executor(control_plane, state_directory)
        event, registered_value = control_plane.registry.events.get(timeout=5)
        assert event == "registered"
        assert isinstance(registered_value, OnlineExecutorConnection)
        assert registered_value.installation_id.uuid == INSTALLATION_ID
        assert registered_value.executor_id.uuid == EXECUTOR_ID
        assert registered_value.last_sequence == 1

        event, heartbeat_value = control_plane.registry.events.get(timeout=5)
        assert event == "heartbeat"
        assert isinstance(heartbeat_value, OnlineExecutorConnection)
        assert heartbeat_value.last_sequence == 2

        stdout, stderr = stop_process(process)
        assert process.returncode == 0
        events = [json.loads(line) for line in stdout.splitlines()]
        assert [event["event"] for event in events] == [
            "executor.healthy",
            "executor.stopped",
        ]
        assert all(event["authenticationProof"].startswith("atlep1.") for event in events)
        assert events[0]["authenticationProof"] != events[1]["authenticationProof"]
        assert stderr == ""
        assert control_plane.session_token not in stdout
        assert control_plane.session_token not in stderr
        assert control_plane.session_token not in repr(process.args)
        assert LOCAL_SESSION_TOKEN not in stdout
        assert LOCAL_SESSION_TOKEN not in stderr
        assert LOCAL_SESSION_TOKEN not in repr(process.args)
        assert control_plane.session_token in bootstrap
        assert LOCAL_SESSION_TOKEN in bootstrap
        ledger_path = state_directory / "executor-ledger.sqlite3"
        assert ledger_path.is_file()
        with sqlite3.connect(ledger_path) as connection:
            assert connection.execute("PRAGMA user_version").fetchone() == (3,)
            assert connection.execute(
                "SELECT installation_id, executor_id FROM executor_identity"
            ).fetchone() == (str(INSTALLATION_ID), str(EXECUTOR_ID))
            assert [
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(executor_platform_sessions)"
                ).fetchall()
            ] == ["platform", "state", "session_revision", "observed_at"]
        ledger_bytes = ledger_path.read_bytes()
        assert control_plane.session_token.encode() not in ledger_bytes
        assert LOCAL_SESSION_TOKEN.encode() not in ledger_bytes
        event, removed = control_plane.registry.events.get(timeout=5)
        assert (event, removed) == ("unregistered", True)
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
        control_plane.stop()


def test_invalid_bootstrap_exits_with_one_fixed_error_and_never_reflects_secret() -> None:
    private = "private-bootstrap-material"
    process = subprocess.run(
        [str(EXECUTOR_ENTRY)],
        cwd=BACKEND_ROOT,
        input=json.dumps(
            {
                "bootstrap_version": "1",
                "websocket_url": "wss://user@invalid.example/api/v1/executors/connect",
                "local_session_token": LOCAL_SESSION_TOKEN,
                "session_token": private,
                "installation_id": str(INSTALLATION_ID),
                "executor_id": str(EXECUTOR_ID),
                "heartbeat_interval_seconds": 1,
            }
        )
        + "\n",
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )

    assert process.returncode == 2
    assert process.stdout == ""
    assert process.stderr == "Local Executor bootstrap is rejected\n"
    assert private not in process.stderr
