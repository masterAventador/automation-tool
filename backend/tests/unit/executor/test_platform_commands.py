from __future__ import annotations

import json
import threading
from io import BytesIO, StringIO
from pathlib import Path
from queue import Queue
from typing import Any, cast

import pytest
from pydantic import SecretStr

from automation_tool.executor.authentication import LocalSessionAuthenticator
from automation_tool.executor.browser_authority import (
    BrowserLaunchAuthority,
    BrowserLaunchAuthorityRejected,
)
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


def logout_command_source(**overrides: object) -> bytes:
    authenticator = LocalSessionAuthenticator(SecretStr(TOKEN))
    document: dict[str, object] = {
        "authenticationProof": authenticator.proof_for_session_command(
            command_id=COMMAND_ID,
            command_type="douyin.logout.complete",
        ),
        "commandId": COMMAND_ID,
        "commandType": "douyin.logout.complete",
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


def test_logout_completion_command_is_path_free_and_strictly_authenticated() -> None:
    authenticator = LocalSessionAuthenticator(SecretStr(TOKEN))

    command = read_platform_command(BytesIO(logout_command_source()), authenticator)

    assert command.command_type == "douyin.logout.complete"
    assert command.executable_path is None
    assert command.profile_directory is None
    assert command.headless is None
    with pytest.raises(PlatformCommandRejected):
        read_platform_command(
            BytesIO(logout_command_source(profileDirectory=PROFILE)),
            authenticator,
        )


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

        @staticmethod
        def record_logout(*, sequence: int) -> str:
            return f"logout-health-{sequence}"

    runtimes: list[Runtime] = []
    flows: list[Flow] = []
    reporter = Reporter()
    outbound: Queue[object] = Queue()
    browser_authority = BrowserLaunchAuthority()

    def runtime_factory() -> Runtime:
        runtime = Runtime()
        runtimes.append(runtime)
        return runtime

    def flow_factory(runtime: Runtime) -> Flow:
        flow = Flow(runtime)
        flows.append(flow)
        return flow

    operation = DouyinLoginCommandOperation(
        health_reporter=cast(Any, reporter),
        outbound=outbound,
        runtime_factory=cast(Any, runtime_factory),
        flow_factory=cast(Any, flow_factory),
        sequence_source=iter((41, 42, 43)).__next__,
        browser_authority=browser_authority,
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
    with browser_authority.lease() as retained:
        assert retained.profile_directory == profile

    assert operation.handle(
        PlatformCommand.model_validate(json.loads(logout_command_source()))
    ) == ("logged_out")
    assert outbound.get_nowait() == "logout-health-43"
    with pytest.raises(BrowserLaunchAuthorityRejected), browser_authority.lease():
        raise AssertionError


def test_douyin_operation_completes_logout_without_launching_a_browser() -> None:
    class Reporter:
        def record_logout(self, *, sequence: int) -> str:
            return f"logout-health-{sequence}"

        def observe(self, window: object, *, sequence: int, recovered: bool) -> str:
            raise AssertionError((window, sequence, recovered))

    outbound: Queue[object] = Queue()
    operation = DouyinLoginCommandOperation(
        health_reporter=cast(Any, Reporter()),
        outbound=outbound,
        runtime_factory=lambda: (_ for _ in ()).throw(AssertionError("browser launch")),
        sequence_source=lambda: 43,
    )
    command = PlatformCommand.model_validate(json.loads(logout_command_source()))

    assert operation.handle(command) == "logged_out"
    assert outbound.get_nowait() == "logout-health-43"


def test_command_model_rejects_unsafe_paths_and_missing_or_extra_browser_identity() -> None:
    authenticator = LocalSessionAuthenticator(SecretStr(TOKEN))
    invalid_sources = (
        command_source(executablePath=None),
        command_source(profileDirectory=None),
        command_source(headless=None),
        command_source(executablePath="relative/browser"),
        command_source(profileDirectory="/private/../profile"),
        command_source(profileDirectory="/private/\u202eprofile"),
        logout_command_source(headless=False),
    )
    for source in invalid_sources:
        with pytest.raises(PlatformCommandRejected):
            read_platform_command(BytesIO(source), authenticator)
    assert (
        read_platform_command(
            BytesIO(logout_command_source(executablePath=None)),
            authenticator,
        ).executable_path
        is None
    )


def test_worker_validates_dependencies_stop_writer_and_close_failures() -> None:
    class Operation:
        def __init__(self, *, handle_failure: bool = False, close_failure: bool = False) -> None:
            self.handle_failure = handle_failure
            self.close_failure = close_failure

        def handle(self, command: PlatformCommand) -> str:
            if self.handle_failure:
                raise RuntimeError("private operation")
            return "awaiting_scan"

        def close(self) -> None:
            if self.close_failure:
                raise RuntimeError("private close")

    authenticator = LocalSessionAuthenticator(SecretStr(TOKEN))
    valid = {
        "input_stream": BytesIO(command_source()),
        "authenticator": authenticator,
        "operation": Operation(),
        "result_output": StringIO(),
    }
    invalid = (
        {"input_stream": object()},
        {"authenticator": object()},
        {"operation": object()},
        {"result_output": None},
        {"result_writer": lambda **kwargs: None},
        {"result_output": object()},
    )
    for overrides in invalid:
        with pytest.raises(PlatformCommandRejected):
            PlatformCommandWorker(**(valid | overrides))  # type: ignore[arg-type]

    with pytest.raises(PlatformCommandRejected):
        PlatformCommandWorker(**valid).run(object())  # type: ignore[arg-type]

    written: list[dict[str, str]] = []
    PlatformCommandWorker(
        input_stream=BytesIO(command_source()),
        authenticator=authenticator,
        operation=Operation(),
        result_writer=lambda **value: written.append(value),
    ).run(threading.Event())
    assert written == [{"command_id": COMMAND_ID, "state": "awaiting_scan"}]

    with pytest.raises(PlatformCommandRejected):
        PlatformCommandWorker(
            input_stream=BytesIO(command_source()),
            authenticator=authenticator,
            operation=Operation(handle_failure=True),
            result_output=StringIO(),
        ).run(threading.Event())
    with pytest.raises(PlatformCommandRejected):
        PlatformCommandWorker(
            input_stream=BytesIO(),
            authenticator=authenticator,
            operation=Operation(close_failure=True),
            result_output=StringIO(),
        ).run(threading.Event())

    stopped = threading.Event()
    stopped.set()
    PlatformCommandWorker(
        input_stream=BytesIO(command_source()),
        authenticator=authenticator,
        operation=Operation(close_failure=True),
        result_output=StringIO(),
    ).run(stopped)


def test_douyin_operation_fails_closed_for_invalid_dependencies_and_commands(
    tmp_path: Path,
) -> None:
    class Reporter:
        def observe(self, window: object, *, sequence: int, recovered: bool) -> str:
            return f"health-{sequence}-{recovered}"

        def record_logout(self, *, sequence: int) -> str:
            return f"logout-{sequence}"

    for arguments in (
        {"health_reporter": object(), "outbound": Queue()},
        {"health_reporter": Reporter(), "outbound": object()},
        {"health_reporter": Reporter(), "outbound": Queue(), "runtime_factory": object()},
        {"health_reporter": Reporter(), "outbound": Queue(), "flow_factory": object()},
        {"health_reporter": Reporter(), "outbound": Queue(), "sequence_source": object()},
    ):
        with pytest.raises(PlatformCommandRejected):
            DouyinLoginCommandOperation(**arguments)  # type: ignore[arg-type]

    operation = DouyinLoginCommandOperation(
        health_reporter=cast(Any, Reporter()),
        outbound=Queue(),
    )
    with pytest.raises(PlatformCommandRejected):
        operation.handle(object())  # type: ignore[arg-type]
    logout = PlatformCommand.model_validate(json.loads(logout_command_source()))
    assert operation.handle(logout) == "logged_out"
    operation.close()

    executable = tmp_path / "browser"
    executable.write_bytes(b"browser")
    executable.chmod(0o700)
    profile = tmp_path / "profile"
    profile.mkdir()
    valid = PlatformCommand.model_validate(
        json.loads(
            command_source(
                executablePath=str(executable),
                profileDirectory=str(profile),
            )
        )
    )

    class Runtime:
        def __init__(self, *, close_failure: bool = False) -> None:
            self.close_failure = close_failure
            self.closed = 0

        def start(self, request: object) -> None:
            pass

        def close(self) -> None:
            self.closed += 1
            if self.close_failure:
                raise RuntimeError("private runtime close")

    class Observation:
        def __init__(self, state: object) -> None:
            self.state = type("State", (), {"value": state})()

    class Flow:
        def __init__(
            self,
            state: object = "awaiting_scan",
            *,
            close_failure: bool = False,
        ) -> None:
            self.state = state
            self.close_failure = close_failure

        def begin(self) -> Observation:
            return Observation(self.state)

        def recheck(self) -> Observation:
            return Observation(self.state)

        def active_window(self) -> object:
            return object()

        def close(self) -> None:
            if self.close_failure:
                raise RuntimeError("private flow close")

    def make_operation(
        *,
        flow_factory: Any = lambda runtime: Flow(),
        runtime_factory: Any = Runtime,
        sequences: tuple[object, ...] = (1,),
    ) -> DouyinLoginCommandOperation:
        return DouyinLoginCommandOperation(
            health_reporter=cast(Any, Reporter()),
            outbound=Queue(),
            runtime_factory=runtime_factory,
            flow_factory=flow_factory,
            sequence_source=cast(Any, iter(sequences).__next__),
        )

    missing_identity = valid.model_copy(update={"profile_directory": None})
    unsupported = valid.model_copy(update={"command_type": "private.command"})
    for candidate in (missing_identity, unsupported):
        with pytest.raises(PlatformCommandRejected):
            make_operation().handle(candidate)

    for bad_state in ("private", 0):
        with pytest.raises(PlatformCommandRejected):
            make_operation(flow_factory=lambda runtime, state=bad_state: Flow(state)).handle(valid)
    for sequence in (0, True):
        with pytest.raises(PlatformCommandRejected):
            make_operation(sequences=(sequence,)).handle(valid)

    runtime = Runtime()

    def broken_flow_factory(runtime: Runtime) -> Flow:
        raise RuntimeError("private flow construction")

    with pytest.raises(PlatformCommandRejected):
        make_operation(
            flow_factory=broken_flow_factory,
            runtime_factory=lambda: runtime,
        ).handle(valid)
    assert runtime.closed == 1

    no_flow = make_operation(flow_factory=lambda runtime: None)
    with pytest.raises(PlatformCommandRejected):
        no_flow.handle(valid)
    with pytest.raises(PlatformCommandRejected):
        make_operation(flow_factory=lambda runtime: None).handle(
            valid.model_copy(update={"command_type": "douyin.login.recheck"})
        )

    recheck = valid.model_copy(update={"command_type": "douyin.login.recheck"})
    assert make_operation().handle(recheck) == "awaiting_scan"
    identity_mismatch = recheck.model_copy(update={"profile_directory": str(tmp_path / "other")})
    active = make_operation(sequences=(1, 2))
    assert active.handle(valid) == "awaiting_scan"
    with pytest.raises(PlatformCommandRejected):
        active.handle(identity_mismatch)

    strict_close = make_operation()
    strict_close._flow = Flow(close_failure=True)  # type: ignore[assignment]
    strict_close._runtime = Runtime(close_failure=True)
    with pytest.raises(PlatformCommandRejected):
        strict_close.close()

    class FailingLease:
        @staticmethod
        def close() -> None:
            raise RuntimeError("private lease close")

    lease_close = make_operation()
    lease_close._browser_lease = cast(Any, FailingLease())
    with pytest.raises(PlatformCommandRejected):
        lease_close.close()

    explicit_rejection = make_operation()
    explicit_rejection._flow = Flow(close_failure=True)  # type: ignore[assignment]
    with pytest.raises(PlatformCommandRejected):
        explicit_rejection.handle(valid)


def test_douyin_operation_rejects_a_flow_that_disappears_during_observation(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "browser"
    executable.write_bytes(b"browser")
    executable.chmod(0o700)
    profile = tmp_path / "profile"
    profile.mkdir()
    command = PlatformCommand.model_validate(
        json.loads(
            command_source(
                executablePath=str(executable),
                profileDirectory=str(profile),
            )
        )
    )

    class Reporter:
        def observe(self, window: object, *, sequence: int, recovered: bool) -> str:
            return "health"

    class Runtime:
        def start(self, request: object) -> None:
            pass

        def close(self) -> None:
            pass

    operation: DouyinLoginCommandOperation

    class Flow:
        def begin(self) -> object:
            operation._flow = None
            return type(
                "Observation", (), {"state": type("State", (), {"value": "awaiting_scan"})()}
            )()

        def recheck(self) -> object:
            raise AssertionError

        def active_window(self) -> object:
            return object()

        def close(self) -> None:
            pass

    operation = DouyinLoginCommandOperation(
        health_reporter=cast(Any, Reporter()),
        outbound=Queue(),
        runtime_factory=Runtime,
        flow_factory=cast(Any, lambda runtime: Flow()),
        sequence_source=lambda: 1,
    )
    with pytest.raises(PlatformCommandRejected):
        operation.handle(command)


def test_reader_and_result_writer_fail_closed_on_invalid_io_and_oversized_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authenticator = LocalSessionAuthenticator(SecretStr(TOKEN))
    with pytest.raises(PlatformCommandRejected):
        read_platform_command(BytesIO(command_source()), object())  # type: ignore[arg-type]
    for source in (b"", b"{}", b"x" * (16 * 1024 + 1)):
        with pytest.raises(PlatformCommandRejected):
            read_platform_command(BytesIO(source), authenticator)

    class BrokenOutput:
        def write(self, value: str) -> None:
            raise OSError("private output")

        def flush(self) -> None:
            raise AssertionError

    with pytest.raises(PlatformCommandRejected):
        write_platform_command_result(
            cast(Any, BrokenOutput()),
            authenticator,
            command_id=COMMAND_ID,
            state="logged_out",
        )

    monkeypatch.setattr(
        LocalSessionAuthenticator,
        "proof_for_command_result",
        lambda self, **kwargs: "atlcp1." + "A" * 5000,
    )
    with pytest.raises(PlatformCommandRejected):
        write_platform_command_result(
            StringIO(),
            authenticator,
            command_id=COMMAND_ID,
            state="logged_out",
        )
