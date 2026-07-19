from __future__ import annotations

import json
import threading
from io import BytesIO, StringIO
from pathlib import Path
from queue import Queue

import pytest
from pydantic import SecretStr

from automation_tool.executor.authentication import LocalSessionAuthenticator
from automation_tool.executor.platform_commands import (
    DouyinLoginCommandOperation,
    PlatformCommand,
    PlatformCommandRejected,
    PlatformCommandWorker,
    read_platform_command,
    write_platform_command_result,
)

TOKEN = "".join(f"{value:02x}" for value in range(32))
COMMAND_ID = "123e4567-e89b-42d3-a456-426614174005"
EXECUTABLE = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PROFILE = "/private/tmp/automation-tool-profile"


def command_source(**overrides: object) -> bytes:
    authenticator = LocalSessionAuthenticator(SecretStr(TOKEN))
    document: dict[str, object] = {
        "authenticationProof": authenticator.proof_for_command(
            command_id=COMMAND_ID,
            command_type="douyin.login.open",
            executable_path=EXECUTABLE,
            profile_directory=PROFILE,
            headless=True,
        ),
        "commandId": COMMAND_ID,
        "commandType": "douyin.login.open",
        "executablePath": EXECUTABLE,
        "headless": True,
        "profileDirectory": PROFILE,
        "protocolVersion": "1.0",
    }
    document.update(overrides)
    return (json.dumps(document, separators=(",", ":"), sort_keys=True) + "\n").encode()


def test_command_is_exact_authenticated_and_path_redacted_from_repr() -> None:
    authenticator = LocalSessionAuthenticator(SecretStr(TOKEN))

    command = read_platform_command(BytesIO(command_source()), authenticator)

    assert command.command_id == COMMAND_ID
    assert command.command_type == "douyin.login.open"
    assert command.headless is True
    assert EXECUTABLE not in repr(command)
    assert PROFILE not in repr(command)
    assert TOKEN not in repr(command)


def test_command_rejects_unknown_fields_bad_proof_and_unsupported_action() -> None:
    authenticator = LocalSessionAuthenticator(SecretStr(TOKEN))
    for source in (
        command_source(private="value"),
        command_source(authenticationProof="atlcp1." + "A" * 43),
        command_source(commandType="douyin.login.logout"),
    ):
        with pytest.raises(PlatformCommandRejected):
            read_platform_command(BytesIO(source), authenticator)


def test_result_is_exact_authenticated_and_contains_no_local_path() -> None:
    authenticator = LocalSessionAuthenticator(SecretStr(TOKEN))
    output = StringIO()

    write_platform_command_result(
        output,
        authenticator,
        command_id=COMMAND_ID,
        state="awaiting_scan",
    )

    result = json.loads(output.getvalue())
    assert result == {
        "authenticationProof": authenticator.proof_for_command_result(
            command_id=COMMAND_ID,
            state="awaiting_scan",
        ),
        "commandId": COMMAND_ID,
        "event": "platform.command.completed",
        "flowVersion": "douyin.qr-login.v2",
        "platform": "douyin",
        "protocolVersion": "1.0",
        "state": "awaiting_scan",
    }
    assert EXECUTABLE not in output.getvalue()
    assert PROFILE not in output.getvalue()
    assert TOKEN not in output.getvalue()


def test_worker_processes_each_authenticated_line_and_closes_its_operation() -> None:
    class Operation:
        def __init__(self) -> None:
            self.commands: list[object] = []
            self.closed = False

        def handle(self, command: object) -> str:
            self.commands.append(command)
            return "awaiting_scan"

        def close(self) -> None:
            self.closed = True

    operation = Operation()
    output = StringIO()
    worker = PlatformCommandWorker(
        input_stream=BytesIO(command_source()),
        authenticator=LocalSessionAuthenticator(SecretStr(TOKEN)),
        operation=operation,
        result_output=output,
    )

    worker.run(threading.Event())

    assert len(operation.commands) == 1
    assert operation.closed is True
    assert json.loads(output.getvalue())["state"] == "awaiting_scan"


def test_worker_fails_closed_on_an_unauthenticated_line() -> None:
    class Operation:
        def handle(self, command: object) -> str:
            raise AssertionError(command)

        def close(self) -> None:
            pass

    with pytest.raises(PlatformCommandRejected):
        PlatformCommandWorker(
            input_stream=BytesIO(command_source(authenticationProof="atlcp1." + "A" * 43)),
            authenticator=LocalSessionAuthenticator(SecretStr(TOKEN)),
            operation=Operation(),
            result_output=StringIO(),
        ).run(threading.Event())


def test_douyin_operation_reuses_one_flow_reports_health_and_closes_when_healthy(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "browser"
    executable.write_bytes(b"browser")
    executable.chmod(0o700)
    profile = tmp_path / "profile"
    profile.mkdir()

    class Runtime:
        def __init__(self) -> None:
            self.requests: list[object] = []
            self.closed = 0

        def start(self, request: object) -> None:
            self.requests.append(request)

        def close(self) -> None:
            self.closed += 1

    class Observation:
        def __init__(self, state: str) -> None:
            self.state = type("State", (), {"value": state})()

    class Flow:
        def __init__(self, runtime: Runtime) -> None:
            self.runtime = runtime
            self.closed = 0

        def begin(self) -> Observation:
            return Observation("awaiting_scan")

        def recheck(self) -> Observation:
            return Observation("healthy")

        def active_window(self) -> object:
            return object()

        def close(self) -> None:
            self.closed += 1

    class Reporter:
        def __init__(self) -> None:
            self.calls: list[tuple[object, int, bool]] = []

        def observe(self, window: object, *, sequence: int, recovered: bool) -> str:
            self.calls.append((window, sequence, recovered))
            return f"health-{sequence}"

    runtimes: list[Runtime] = []
    flows: list[Flow] = []
    reporter = Reporter()
    outbound: Queue[object] = Queue()
    operation = DouyinLoginCommandOperation(
        health_reporter=reporter,
        outbound=outbound,
        runtime_factory=lambda: runtimes.append(Runtime()) or runtimes[-1],
        flow_factory=lambda runtime: flows.append(Flow(runtime)) or flows[-1],
        sequence_source=iter((41, 42)).__next__,
    )

    def command(command_type: str) -> PlatformCommand:
        return PlatformCommand.model_validate(
            {
                "authenticationProof": "atlcp1." + "A" * 43,
                "commandId": COMMAND_ID,
                "commandType": command_type,
                "executablePath": str(executable),
                "headless": True,
                "profileDirectory": str(profile),
                "protocolVersion": "1.0",
            }
        )

    assert operation.handle(command("douyin.login.open")) == "awaiting_scan"
    assert operation.handle(command("douyin.login.recheck")) == "healthy"
    assert len(runtimes) == 1
    assert len(flows) == 1
    assert reporter.calls[0][1:] == (41, False)
    assert reporter.calls[1][1:] == (42, True)
    assert outbound.get_nowait() == "health-41"
    assert outbound.get_nowait() == "health-42"
    assert flows[0].closed == 1
    assert runtimes[0].closed == 1
