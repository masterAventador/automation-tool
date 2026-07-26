#!/usr/bin/env python3
"""Run Rust ExecutorManager against a real signed PyInstaller Executor and Uvicorn."""

from __future__ import annotations

import json
import os
import platform
import queue
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import uvicorn
from automation_tool.control_plane import create_app
from automation_tool.control_plane.application.device_credentials import (
    ParsedDeviceCredential,
)
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

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
RUST_ROOT = REPOSITORY_ROOT / "frontend" / "src-tauri"
INSTALLATION_ID = UUID("123e4567-e89b-42d3-a456-426614174003")
EXECUTOR_ID = UUID("123e4567-e89b-42d3-a456-426614174004")
CREDENTIAL_ID = UUID("123e4567-e89b-42d3-a456-426614174007")
TEST_SIGNING_SEED = bytes(range(32))


class FixedClock:
    def __init__(self) -> None:
        self._now = datetime.now(UTC)

    def now(self) -> datetime:
        return self._now


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
        self.events: queue.Queue[str] = queue.Queue()

    async def register(
        self,
        bound: BoundExecutorConnection,
        channel: ExecutorConnectionChannel,
    ) -> OnlineExecutorConnection:
        result = await super().register(bound, channel)
        self.events.put("registered")
        return result

    async def record_heartbeat(
        self,
        bound: BoundExecutorConnection,
        *,
        sequence: int,
    ) -> OnlineExecutorConnection:
        result = await super().record_heartbeat(bound, sequence=sequence)
        self.events.put("heartbeat")
        return result

    async def unregister(self, bound: BoundExecutorConnection) -> bool:
        result = await super().unregister(bound)
        self.events.put("unregistered")
        return result

    def drain_event_names(self) -> list[str]:
        observed: list[str] = []
        while True:
            try:
                observed.append(self.events.get_nowait())
            except queue.Empty:
                return observed


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
        if self.thread.is_alive():
            raise RuntimeError("E4-07 Uvicorn did not stop")


def start_control_plane() -> RunningControlPlane:
    material = DeviceSessionFactory(secret_source=os.urandom, id_source=uuid4).create()
    sessions = DeviceSessionService(
        repository=LiveSessionRepository(
            expected=ParsedDeviceSession(
                session_id=material.session_id,
                secret_digest=material.secret_digest,
            )
        ),
        clock=FixedClock(),
        session_factory=DeviceSessionFactory(secret_source=os.urandom, id_source=uuid4),
    )
    registry = ObservedRegistry()
    app = create_app(
        database=None,
        executor_connection_service=ExecutorConnectionService(sessions),
        executor_connection_registry=registry,
        executor_connection_hello_timeout_seconds=2,
        executor_connection_recheck_interval_seconds=0.05,
    )
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = int(listener.getsockname()[1])
    server = uvicorn.Server(
        uvicorn.Config(app, log_level="critical", access_log=False, ws="websockets-sansio")
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
    if not server.started:
        raise RuntimeError("E4-07 Uvicorn did not start")
    return RunningControlPlane(server, thread, port, registry, material.session_token)


def _completed_process_diagnostic(
    completed: subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes],
    *,
    lines_per_stream: int = 20,
) -> str:
    """Render a bounded tail from each builder output stream."""
    parts: list[str] = []
    for name, output in (("stderr", completed.stderr), ("stdout", completed.stdout)):
        rendered = (
            output.decode("utf-8", errors="replace")
            if isinstance(output, bytes)
            else output
        )
        lines = [line for line in (rendered or "").splitlines() if line.strip()]
        if lines:
            parts.append(f"{name}:\n" + "\n".join(lines[-lines_per_stream:]))
    return "\n".join(parts) if parts else "(builder produced no output)"


def build_signed_executor(
    workspace: Path,
    *,
    build_id: str = "e4-07-real",
    spec_path: Path | None = None,
) -> Path:
    distribution = workspace / "dist"
    work = workspace / "build"
    resolved_spec = (
        BACKEND_ROOT / "automation-tool-executor.spec" if spec_path is None else spec_path
    )
    if not resolved_spec.is_file():
        raise RuntimeError("E4-07 PyInstaller spec is missing")
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            os.fspath(distribution),
            "--workpath",
            os.fspath(work),
            os.fspath(resolved_spec),
        ],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if completed.returncode != 0:
        diagnostic = _completed_process_diagnostic(completed)
        raise RuntimeError(f"E4-07 PyInstaller build failed\n{diagnostic}")
    package_root = distribution / "automation-tool-executor"
    architecture = "x86_64" if platform.machine().lower() in {"x86_64", "amd64"} else "aarch64"
    manifest_platform = "windows" if platform.system() == "Windows" else "macos"
    manifest = subprocess.run(
        [
            sys.executable,
            "-m",
            "automation_tool.executor.package_manifest",
            "--bundle-dir",
            os.fspath(package_root),
            "--executor-version",
            "0.1.0",
            "--build-id",
            build_id,
            "--platform",
            manifest_platform,
            "--architecture",
            architecture,
        ],
        cwd=BACKEND_ROOT,
        input=TEST_SIGNING_SEED,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if manifest.returncode != 0:
        diagnostic = _completed_process_diagnostic(manifest)
        raise RuntimeError(f"E4-07 package signing failed\n{diagnostic}")
    return package_root


def run_rust_manager(
    package_root: Path, control_plane: RunningControlPlane, workspace: Path
) -> None:
    configuration_path = workspace / "executor-manager.json"
    configuration_path.write_text(
        json.dumps(
            {
                "packageRoot": os.fspath(package_root),
                "websocketUrl": (f"ws://127.0.0.1:{control_plane.port}/api/v1/executors/connect"),
                "sessionToken": control_plane.session_token,
                "installationId": str(INSTALLATION_ID),
                "executorId": str(EXECUTOR_ID),
                "stateDirectory": os.fspath(workspace / "executor-state"),
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    configuration_path.chmod(0o600)
    environment = os.environ.copy()
    environment["AUTOMATION_TOOL_E407_CONFIGURATION"] = os.fspath(configuration_path)
    completed = subprocess.run(
        [
            "cargo",
            "test",
            "--locked",
            "--test",
            "executor_manager_packaged",
            "real_packaged_executor_uses_the_public_manager_lifecycle",
            "--",
            "--ignored",
            "--exact",
        ],
        cwd=RUST_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if (
        control_plane.session_token in completed.stdout
        or control_plane.session_token in completed.stderr
    ):
        raise RuntimeError("E4-07 acceptance reflected the Control Plane session")
    if completed.returncode != 0 or "1 passed; 0 failed" not in completed.stdout:
        diagnostic = (completed.stdout + "\n" + completed.stderr).replace(
            control_plane.session_token,
            "[REDACTED]",
        )[-4000:]
        raise RuntimeError(f"E4-07 Rust manager acceptance failed\n{diagnostic}")

    ledger_path = workspace / "executor-state" / "executor-ledger.sqlite3"
    if not ledger_path.is_file():
        raise RuntimeError("E4-11 Rust manager did not create the Executor ledger")
    connection = sqlite3.connect(ledger_path)
    try:
        version = connection.execute("PRAGMA user_version").fetchone()
        identity = connection.execute(
            "SELECT installation_id, executor_id FROM executor_identity"
        ).fetchone()
    finally:
        connection.close()
    if version != (2,) or identity != (str(INSTALLATION_ID), str(EXECUTOR_ID)):
        raise RuntimeError("E4-11 Executor ledger migration or identity binding is invalid")
    ledger_bytes = ledger_path.read_bytes()
    if control_plane.session_token.encode() in ledger_bytes:
        raise RuntimeError("E4-11 Executor ledger persisted the Control Plane session")


def main() -> None:
    if platform.system() not in {"Darwin", "Windows"}:
        raise RuntimeError("E4-07 local acceptance requires macOS or Windows")
    control_plane = start_control_plane()
    try:
        with tempfile.TemporaryDirectory(prefix="automation-tool-e4-07-") as directory:
            workspace = Path(directory).resolve(strict=True)
            package_root = build_signed_executor(workspace)
            run_rust_manager(package_root, control_plane, workspace)
        observed = [control_plane.registry.events.get(timeout=5) for _ in range(3)]
        if observed != ["registered", "heartbeat", "unregistered"]:
            raise RuntimeError("E4-07 Control Plane lifecycle facts did not converge")
    except Exception as error:
        observed = control_plane.registry.drain_event_names()
        raise RuntimeError(f"{error}\nControl Plane events: {observed!r}") from None
    finally:
        control_plane.stop()
    print("E4-07 acceptance passed: Rust Manager -> signed PyInstaller Executor -> Uvicorn")


if __name__ == "__main__":
    main()
