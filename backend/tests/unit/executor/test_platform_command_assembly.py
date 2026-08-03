"""The production command assembly where publish and login share infrastructure.

These tests exercise the exact wiring `run_executor` builds, because the
publish and login command families share one `BrowserLaunchAuthority`, one
Control Plane outbox and one operations browser. Isolating publish behind its
own collaborators hides precisely the failures that kill the executor.
"""

from __future__ import annotations

import importlib.util
import json
import queue
import sys
from datetime import UTC, datetime, timedelta
from io import BytesIO, StringIO
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest
from pydantic import SecretStr

from automation_tool.executor import cli as executor_cli
from automation_tool.executor.authentication import LocalSessionAuthenticator
from automation_tool.executor.bootstrap import read_executor_bootstrap
from automation_tool.executor.browser_authority import BrowserLaunchAuthority
from automation_tool.executor.browser_runtime import BrowserLaunchRequest, BrowserRuntime
from automation_tool.executor.cli import build_platform_command_router
from automation_tool.executor.command_processor import ExecutorCommandProcessor
from automation_tool.executor.ledger import ExecutorLedger
from automation_tool.executor.platform_commands import (
    DOUYIN_PUBLISH_PREFLIGHT_COMMAND,
    PlatformCommand,
)
from automation_tool.executor.runtime import (
    ExecutorProcessRejected,
    ExecutorProcessReporter,
    LocalExecutorProcess,
    RuntimeMetadata,
)
from automation_tool.protocol import PlatformSessionHealthEnvelope

TOKEN = "".join(f"{value:02x}" for value in range(32))
COMMAND_ID = "123e4567-e89b-42d3-a456-426614174005"
PUBLISH_JOB_ID = "123e4567-e89b-42d3-a456-426614174006"
INSTALLATION_ID = "123e4567-e89b-42d3-a456-426614174003"
EXECUTOR_ID = "123e4567-e89b-42d3-a456-426614174004"
EXECUTABLE = "/opt/automation-tool/chromium"
PROFILE = "/opt/automation-tool/profile"
TITLE = "标题"
DESCRIPTION = "简介"


def authenticator() -> LocalSessionAuthenticator:
    return LocalSessionAuthenticator(SecretStr(TOKEN))


def ledger_for(tmp_path: Path) -> ExecutorLedger:
    return ExecutorLedger(
        state_directory=tmp_path / "ledger",
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
    )


def publish_command(
    artifact_path: str,
    *,
    executable: str = EXECUTABLE,
    profile: str = PROFILE,
) -> PlatformCommand:
    source = authenticator()
    payload: dict[str, object] = {
        "artifactPath": artifact_path,
        "commandId": COMMAND_ID,
        "commandType": DOUYIN_PUBLISH_PREFLIGHT_COMMAND,
        "description": DESCRIPTION,
        "executablePath": executable,
        "headless": True,
        "profileDirectory": profile,
        "protocolVersion": "1.0",
        "publishJobId": PUBLISH_JOB_ID,
        "title": TITLE,
    }
    payload["authenticationProof"] = source.proof_for_publish_command(
        command_id=COMMAND_ID,
        command_type=DOUYIN_PUBLISH_PREFLIGHT_COMMAND,
        executable_path=executable,
        profile_directory=profile,
        headless=True,
        publish_job_id=PUBLISH_JOB_ID,
        artifact_path=artifact_path,
        title=TITLE,
        description=DESCRIPTION,
    )
    return PlatformCommand.model_validate(payload)


def production_router(tmp_path: Path, outbox: queue.Queue[object]) -> Any:
    return build_platform_command_router(
        ledger=ledger_for(tmp_path),
        browser_authority=BrowserLaunchAuthority(),
        local_outbox=outbox,
        runtime_factory=BrowserRuntime,
    )


class _Socket:
    def __init__(self) -> None:
        self.sources: list[str] = []

    def send(self, source: str) -> None:
        self.sources.append(source)


NOW = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return NOW


def bootstrap_document(state_directory: Path) -> Any:
    source = json.dumps(
        {
            "bootstrap_version": "1",
            "websocket_url": "ws://127.0.0.1:9/api/v1/executors/connect",
            "local_session_token": TOKEN,
            "session_token": "private-session",
            "installation_id": INSTALLATION_ID,
            "executor_id": EXECUTOR_ID,
            "heartbeat_interval_seconds": 1,
            "state_directory": str(state_directory),
        },
        separators=(",", ":"),
    )
    return read_executor_bootstrap(BytesIO((source + "\n").encode()))


def process_for(tmp_path: Path, outbox: queue.Queue[object]) -> LocalExecutorProcess:
    """Build the real Control Plane process around the shared outbox."""
    state_directory = tmp_path / "executor-state"
    return LocalExecutorProcess(
        bootstrap=bootstrap_document(state_directory),
        metadata=RuntimeMetadata(
            executor_version="0.1.0",
            platform="macos",
            architecture="arm64",
        ),
        reporter=ExecutorProcessReporter(StringIO(), authenticator()),
        command_processor=ExecutorCommandProcessor(
            ledger=ExecutorLedger(
                state_directory=state_directory,
                installation_id=INSTALLATION_ID,
                executor_id=EXECUTOR_ID,
            ),
            installation_id=INSTALLATION_ID,
            executor_id=EXECUTOR_ID,
            clock=FixedClock(),
        ),
        clock=FixedClock(),
        local_outbox=outbox,
    )


def session_health() -> PlatformSessionHealthEnvelope:
    return PlatformSessionHealthEnvelope.model_validate(
        {
            "protocol_version": "1.0",
            "message_id": "723e4567-e89b-42d3-a456-426614174001",
            "message_type": "platform.session_health",
            "sent_at": NOW,
            "deadline_at": NOW + timedelta(seconds=30),
            "installation_id": INSTALLATION_ID,
            "executor_id": EXECUTOR_ID,
            "correlation_id": "723e4567-e89b-42d3-a456-426614174002",
            "idempotency_key": "platform:douyin:session:1:1",
            "sequence": 41,
            "payload": {
                "platform": "douyin",
                "state": "missing",
                "session_revision": 1,
                "observed_at": NOW,
            },
        }
    )


def test_the_control_plane_outbox_only_accepts_protocol_envelopes(tmp_path: Path) -> None:
    """Anything that is not a protocol envelope terminates the outbox drain."""
    outbox: queue.Queue[object] = queue.Queue()
    outbox.put(object())
    process = process_for(tmp_path, outbox)
    with pytest.raises(ExecutorProcessRejected):
        process._send_local_outbox(cast(Any, _Socket()))


def test_production_router_keeps_publish_facts_out_of_the_control_plane_outbox(
    tmp_path: Path,
) -> None:
    """A publish command must never leave anything undeliverable in the shared outbox."""
    outbox: queue.Queue[object] = queue.Queue()
    authority = BrowserLaunchAuthority()
    router = build_platform_command_router(
        ledger=ledger_for(tmp_path),
        browser_authority=authority,
        local_outbox=outbox,
        runtime_factory=BrowserRuntime,
    )
    artifact = tmp_path / "clip.mp4"
    artifact.write_bytes(b"\x00\x00\x00\x18ftypmp42automation-tool")
    artifact.chmod(0o600)
    # The login browser already owns the shared authority, so publish is refused
    # before any browser starts, and records that refusal as a receipt.
    executable = tmp_path / "chromium"
    executable.write_bytes(b"#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    profile = tmp_path / "profile"
    profile.mkdir(mode=0o700)
    authority.authorize(
        BrowserLaunchRequest(
            executable_path=executable,
            profile_directory=profile,
            headless=True,
        )
    )
    held = authority.acquire()
    try:
        assert (
            router.handle(
                publish_command(
                    str(artifact),
                    executable=str(executable),
                    profile=str(profile),
                )
            )
            == "publish_blocked"
        )
    finally:
        held.close()
        router.close()

    process = process_for(tmp_path, outbox)
    socket = _Socket()
    process._send_local_outbox(cast(Any, socket))
    assert outbox.empty()
    assert socket.sources == []
    # The outcome is still observable, just not through the Control Plane queue.
    assert cast(Any, router._publish).latest_receipt() is not None


FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "fixtures"
FIXTURE_SUBSTITUTION = "vars(executor_cli)"


def acceptance_executor_entrypoints() -> list[Path]:
    """Every acceptance Executor entry point that swaps a production collaborator.

    Each one is packaged into its own signed binary and is therefore imported by
    nothing else in this suite. Without this list, a production constructor that
    gains an argument leaves them uncallable, and the only symptom is a desktop
    E2E driver that waits 120 seconds for a page fact that can never arrive.
    """
    found = sorted(
        path
        for path in FIXTURE_ROOT.glob("*_executor.py")
        if FIXTURE_SUBSTITUTION in path.read_text(encoding="utf-8")
    )
    # An empty parametrization is a passing test that checks nothing, which is
    # exactly the state this guard exists to end.
    assert found, f"no acceptance Executor entry point found under {FIXTURE_ROOT}"
    return found


def load_acceptance_entrypoint(path: Path) -> ModuleType:
    specification = importlib.util.spec_from_file_location(
        f"automation_tool_acceptance_entrypoint_{path.stem}", path
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    try:
        specification.loader.exec_module(module)
    except BaseException:
        del sys.modules[specification.name]
        raise
    return module


@pytest.mark.parametrize(
    "entrypoint",
    acceptance_executor_entrypoints(),
    ids=lambda path: path.stem,
)
def test_acceptance_executor_entrypoints_assemble_the_production_router(
    entrypoint: Path, tmp_path: Path
) -> None:
    """Each packaged acceptance Executor must survive the production assembly.

    Its `main()` performs the substitutions and then hands control to the real
    `run_executor`, so a replacement whose signature drifted from the production
    call site kills the process during startup - long before any assertion runs.
    """
    module = load_acceptance_entrypoint(entrypoint)
    original = dict(vars(executor_cli))
    vars(executor_cli)["main"] = lambda: None
    try:
        module.main()
        router = build_platform_command_router(
            ledger=ledger_for(tmp_path),
            browser_authority=BrowserLaunchAuthority(),
            local_outbox=queue.Queue(),
            runtime_factory=BrowserRuntime,
        )
        router.close()
    finally:
        vars(executor_cli).clear()
        vars(executor_cli).update(original)
        del sys.modules[module.__name__]


def test_login_health_still_reaches_the_control_plane_outbox(tmp_path: Path) -> None:
    """The shared outbox keeps working for the messages that do belong to it."""
    outbox: queue.Queue[object] = queue.Queue()
    outbox.put(session_health())
    process = process_for(tmp_path, outbox)
    socket = _Socket()
    process._send_local_outbox(cast(Any, socket))
    assert outbox.empty()
    assert len(socket.sources) == 1
    assert "platform.session_health" in socket.sources[0]
