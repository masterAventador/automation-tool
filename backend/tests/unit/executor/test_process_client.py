from __future__ import annotations

import json
import queue
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from io import BytesIO, StringIO
from itertools import count
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest
from pydantic import SecretStr
from websockets.exceptions import ConnectionClosedError
from websockets.frames import Close
from websockets.sync.client import ClientConnection
from websockets.sync.server import Server, ServerConnection, serve
from websockets.typing import Subprotocol

import automation_tool.executor.runtime as executor_runtime
import automation_tool.executor.transport as executor_transport
from automation_tool.executor.authentication import LocalSessionAuthenticator
from automation_tool.executor.bootstrap import read_executor_bootstrap
from automation_tool.executor.command_processor import ExecutorCommandProcessor
from automation_tool.executor.diagnostics import ExecutorRecoveryDiagnostics
from automation_tool.executor.ledger import AttemptCheckpointState, ExecutorLedger
from automation_tool.executor.runtime import (
    ExecutorProcessRejected,
    ExecutorProcessReporter,
    LocalExecutorProcess,
    RuntimeMetadata,
)
from automation_tool.protocol import (
    EXECUTOR_WEBSOCKET_SUBPROTOCOL,
    ExecutorEnvelope,
    ExecutorLifecycleEnvelope,
    PlatformSessionHealthEnvelope,
    TaskCommandEnvelope,
    parse_executor_message,
)

NOW = datetime(2026, 7, 19, 9, 0, tzinfo=UTC)
INSTALLATION_ID = "123e4567-e89b-42d3-a456-426614174003"
EXECUTOR_ID = "123e4567-e89b-42d3-a456-426614174004"
LOCAL_SESSION_TOKEN = "04" * 32


def reporter(output: StringIO) -> ExecutorProcessReporter:
    return ExecutorProcessReporter(
        output,
        LocalSessionAuthenticator(SecretStr(LOCAL_SESSION_TOKEN)),
    )


class FixedClock:
    @staticmethod
    def now() -> datetime:
        return NOW


class DeterministicIds:
    def __init__(self) -> None:
        self._values = count(1)

    def __call__(self) -> UUID:
        return UUID(f"923e4567-e89b-42d3-a456-{next(self._values):012d}")


def run_server(
    handler: Callable[[ServerConnection], None],
) -> tuple[Server, threading.Thread, int]:
    server = serve(
        handler,
        "127.0.0.1",
        0,
        subprotocols=[Subprotocol(EXECUTOR_WEBSOCKET_SUBPROTOCOL)],
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, int(server.socket.getsockname()[1])


def bootstrap(
    port: int,
    state_directory: Path,
    *,
    local_emergency_stop: bool = False,
) -> object:
    source = json.dumps(
        {
            "bootstrap_version": "1",
            "websocket_url": f"ws://127.0.0.1:{port}/api/v1/executors/connect",
            "local_session_token": LOCAL_SESSION_TOKEN,
            "session_token": "private-session",
            "installation_id": INSTALLATION_ID,
            "executor_id": EXECUTOR_ID,
            "heartbeat_interval_seconds": 1,
            "state_directory": str(state_directory),
            "local_emergency_stop": local_emergency_stop,
        },
        separators=(",", ":"),
    )
    return read_executor_bootstrap(BytesIO((source + "\n").encode()))


def process_for(
    port: int,
    state_directory: Path,
    *,
    output: StringIO | None = None,
    clock: object = FixedClock(),
    id_source: object = None,
    local_outbox: queue.Queue[object] | None = None,
    local_emergency_stop: bool = False,
    restart_reconnect_attempts: int | None = 2,
    restart_reconnect_delay: timedelta | None = timedelta(milliseconds=1),
    monotonic_source: Callable[[], float] | None = None,
    suspend_gap_threshold: timedelta | None = None,
    diagnostic_output: StringIO | None = None,
) -> tuple[LocalExecutorProcess, StringIO]:
    target = output or StringIO()
    values: dict[str, object] = {
        "bootstrap": bootstrap(
            port,
            state_directory,
            local_emergency_stop=local_emergency_stop,
        ),
        "metadata": RuntimeMetadata(
            executor_version="0.1.0",
            platform="macos",
            architecture="arm64",
        ),
        "reporter": reporter(target),
        "command_processor": ExecutorCommandProcessor(
            ledger=ExecutorLedger(
                state_directory=state_directory,
                installation_id=INSTALLATION_ID,
                executor_id=EXECUTOR_ID,
            ),
            installation_id=INSTALLATION_ID,
            executor_id=EXECUTOR_ID,
            clock=FixedClock(),
            id_source=DeterministicIds(),
        ),
        "clock": clock,
        "open_timeout": timedelta(milliseconds=100),
        "close_timeout": timedelta(milliseconds=100),
    }
    if id_source is not None:
        values["id_source"] = id_source
    if local_outbox is not None:
        values["local_outbox"] = local_outbox
    if restart_reconnect_attempts is not None:
        values["restart_reconnect_attempts"] = restart_reconnect_attempts
    if restart_reconnect_delay is not None:
        values["restart_reconnect_delay"] = restart_reconnect_delay
    if monotonic_source is not None:
        values["monotonic_source"] = monotonic_source
    if suspend_gap_threshold is not None:
        values["suspend_gap_threshold"] = suspend_gap_threshold
    if diagnostic_output is not None:
        values["diagnostics"] = ExecutorRecoveryDiagnostics(diagnostic_output)
    return LocalExecutorProcess(**values), target  # type: ignore[arg-type]


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


def offer() -> TaskCommandEnvelope:
    return TaskCommandEnvelope.model_validate(
        {
            "protocol_version": "1.0",
            "message_id": "323e4567-e89b-42d3-a456-426614174001",
            "message_type": "task.offer",
            "sent_at": NOW,
            "deadline_at": NOW + timedelta(minutes=5),
            "installation_id": INSTALLATION_ID,
            "executor_id": EXECUTOR_ID,
            "correlation_id": "323e4567-e89b-42d3-a456-426614174002",
            "idempotency_key": "executor-real:offer:1",
            "sequence": 1,
            "payload": {},
            "task_id": "123e4567-e89b-42d3-a456-426614174005",
            "execution_attempt_id": "123e4567-e89b-42d3-a456-426614174006",
        }
    )


def expired_offer() -> TaskCommandEnvelope:
    return TaskCommandEnvelope.model_validate(
        {
            **offer().model_dump(),
            "sent_at": NOW - timedelta(seconds=2),
            "deadline_at": NOW - timedelta(seconds=1),
        }
    )


def control(
    message_type: str,
    *,
    sequence: int,
    message_id: str,
    correlation_id: str,
) -> TaskCommandEnvelope:
    return TaskCommandEnvelope.model_validate(
        {
            "protocol_version": "1.0",
            "message_id": message_id,
            "message_type": message_type,
            "sent_at": NOW,
            "deadline_at": NOW + timedelta(minutes=5),
            "installation_id": INSTALLATION_ID,
            "executor_id": EXECUTOR_ID,
            "correlation_id": correlation_id,
            "idempotency_key": f"executor-real:{message_type}:{sequence}",
            "sequence": sequence,
            "payload": {},
            "task_id": str(offer().task_id),
            "execution_attempt_id": str(offer().execution_attempt_id),
        }
    )


def test_process_sends_formal_hello_and_heartbeat_then_stops_cleanly(
    tmp_path: Path,
) -> None:
    captured: queue.Queue[object] = queue.Queue()
    stop = threading.Event()

    def handler(connection: ServerConnection) -> None:
        try:
            assert connection.request is not None
            assert connection.request.headers["authorization"] == "Bearer private-session"
            hello = parse_executor_message(connection.recv(timeout=2))
            heartbeat = parse_executor_message(connection.recv(timeout=2))
            second_heartbeat = parse_executor_message(connection.recv(timeout=2))
            captured.put((hello, heartbeat, second_heartbeat))
            stop.set()
        except Exception as error:
            captured.put(error)

    server, thread, port = run_server(handler)
    try:
        process, output = process_for(
            port,
            tmp_path / "executor-state",
            id_source=DeterministicIds(),
        )
        process.run(stop)
        messages = captured.get(timeout=2)
        if isinstance(messages, Exception):
            raise messages
        hello, heartbeat, second_heartbeat = cast(
            tuple[
                ExecutorLifecycleEnvelope,
                ExecutorLifecycleEnvelope,
                ExecutorLifecycleEnvelope,
            ],
            messages,
        )
        assert hello.message_type == "executor.hello"
        assert heartbeat.message_type == "executor.heartbeat"
        assert (hello.sequence, heartbeat.sequence, second_heartbeat.sequence) == (1, 2, 3)
        assert hello.sent_at == NOW
        assert heartbeat.sent_at == NOW
        assert hello.payload == {
            "architecture": "arm64",
            "executor_version": "0.1.0",
            "platform": "macos",
        }
        assert heartbeat.payload == {}
        assert [json.loads(line)["event"] for line in output.getvalue().splitlines()] == [
            "executor.healthy",
            "executor.stopped",
        ]
        assert process._command_processor.ledger.transport_connected() is False
    finally:
        server.shutdown()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_process_drains_local_platform_health_queue_over_the_formal_socket(
    tmp_path: Path,
) -> None:
    observed: queue.Queue[object] = queue.Queue()
    local_outbox: queue.Queue[object] = queue.Queue()
    local_outbox.put(session_health())
    stop = threading.Event()

    def handler(connection: ServerConnection) -> None:
        try:
            parse_executor_message(connection.recv(timeout=2))
            observed.put(parse_executor_message(connection.recv(timeout=2)))
            stop.set()
        except Exception as error:
            observed.put(error)

    server, thread, port = run_server(handler)
    try:
        process, _ = process_for(
            port,
            tmp_path / "executor-state",
            local_outbox=local_outbox,
        )
        process.run(stop)
        message = observed.get(timeout=2)
        if isinstance(message, Exception):
            raise message
        assert isinstance(message, PlatformSessionHealthEnvelope)
        assert message.payload.state.value == "missing"
        assert local_outbox.empty()
    finally:
        server.shutdown()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_local_platform_health_queue_rejects_invalid_messages_and_socket_failures(
    tmp_path: Path,
) -> None:
    class Socket:
        def __init__(self, *, fail: bool = False) -> None:
            self.fail = fail
            self.sources: list[str] = []

        def send(self, source: str) -> None:
            if self.fail:
                raise OSError("private socket failure")
            self.sources.append(source)

    invalid: queue.Queue[object] = queue.Queue()
    invalid.put(object())
    process, _ = process_for(9, tmp_path / "invalid-local-outbox", local_outbox=invalid)
    with pytest.raises(ExecutorProcessRejected) as captured:
        process._send_local_outbox(cast(object, Socket()))  # type: ignore[arg-type]
    assert executor_runtime._is_recoverable_transport_error(captured.value) is False

    failing: queue.Queue[object] = queue.Queue()
    failing.put(session_health())
    process, _ = process_for(9, tmp_path / "failing-local-outbox", local_outbox=failing)
    with pytest.raises(ExecutorProcessRejected) as captured:
        process._send_local_outbox(cast(object, Socket(fail=True)))  # type: ignore[arg-type]
    assert executor_runtime._is_recoverable_transport_error(captured.value) is True
    delivered = Socket()
    process._send_local_outbox(cast(object, delivered))  # type: ignore[arg-type]
    assert len(delivered.sources) == 1
    assert failing.empty()


@pytest.mark.parametrize("server_message", ("{}", b"private-binary-command"))
def test_process_rejects_invalid_post_hello_application_frame(
    server_message: str | bytes,
    tmp_path: Path,
) -> None:
    observed: queue.Queue[object] = queue.Queue()

    def handler(connection: ServerConnection) -> None:
        try:
            parse_executor_message(connection.recv(timeout=2))
            connection.send(server_message)
            observed.put(True)
        except Exception as error:
            observed.put(error)

    server, thread, port = run_server(handler)
    try:
        process, output = process_for(port, tmp_path / "executor-state")
        with pytest.raises(
            ExecutorProcessRejected,
            match=r"^Local Executor process is unavailable$",
        ) as captured:
            process.run(threading.Event())
        assert captured.value.__cause__ is None
        assert captured.value.__context__ is None
        assert output.getvalue() == ""
        assert observed.get(timeout=2) is True
    finally:
        server.shutdown()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_process_consumes_a_formal_offer_and_sends_the_durable_outcome_batch(
    tmp_path: Path,
) -> None:
    observed: queue.Queue[object] = queue.Queue()
    stop = threading.Event()
    offered = offer()

    def handler(connection: ServerConnection) -> None:
        try:
            parse_executor_message(connection.recv(timeout=2))
            connection.send(offered.model_dump_json())
            batch = tuple(parse_executor_message(connection.recv(timeout=2)) for _ in range(2))
            observed.put(batch)
            stop.set()
        except Exception as error:
            observed.put(error)

    server, thread, port = run_server(handler)
    try:
        process, output = process_for(port, tmp_path / "executor-state")
        process.run(stop)
        batch = observed.get(timeout=2)
        if isinstance(batch, Exception):
            raise batch
        messages = cast(tuple[ExecutorEnvelope, ...], batch)
        assert [message.message_type for message in messages] == [
            "task.accept",
            "task.started",
        ]
        assert [json.loads(line)["event"] for line in output.getvalue().splitlines()] == [
            "executor.stopped"
        ]
    finally:
        server.shutdown()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_process_consumes_pause_and_resume_over_the_formal_socket(
    tmp_path: Path,
) -> None:
    observed: queue.Queue[object] = queue.Queue()
    stop = threading.Event()
    state_directory = tmp_path / "pause-state"
    opened = ExecutorLedger(
        state_directory=state_directory,
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
    )
    offered = offer()
    opened.receive_command(offered)
    opened.compare_and_set_checkpoint(
        attempt_id=str(offered.execution_attempt_id),
        expected_revision=1,
        state=AttemptCheckpointState.RUNNING,
        last_event_sequence=2,
    )
    pause = control(
        "task.pause",
        sequence=2,
        message_id="323e4567-e89b-42d3-a456-426614174011",
        correlation_id="323e4567-e89b-42d3-a456-426614174012",
    )
    resume = control(
        "task.resume",
        sequence=3,
        message_id="323e4567-e89b-42d3-a456-426614174013",
        correlation_id="323e4567-e89b-42d3-a456-426614174014",
    )

    def handler(connection: ServerConnection) -> None:
        try:
            parse_executor_message(connection.recv(timeout=2))
            connection.send(pause.model_dump_json())
            paused = tuple(parse_executor_message(connection.recv(timeout=2)) for _ in range(2))
            connection.send(resume.model_dump_json())
            resumed = tuple(parse_executor_message(connection.recv(timeout=2)) for _ in range(2))
            observed.put((*paused, *resumed))
            stop.set()
        except Exception as error:
            observed.put(error)

    server, thread, port = run_server(handler)
    try:
        process, output = process_for(port, state_directory)
        process.run(stop)
        messages = observed.get(timeout=2)
        if isinstance(messages, Exception):
            raise messages
        assert [
            message.message_type for message in cast(tuple[ExecutorEnvelope, ...], messages)
        ] == [
            "task.control_ack",
            "task.paused",
            "task.control_ack",
            "task.resumed",
        ]
        checkpoint = opened.get_checkpoint(str(offered.execution_attempt_id))
        assert checkpoint is not None
        assert checkpoint.state is AttemptCheckpointState.RUNNING
        assert checkpoint.last_event_sequence == 4
        assert [json.loads(line)["event"] for line in output.getvalue().splitlines()] == [
            "executor.stopped"
        ]
    finally:
        server.shutdown()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_process_consumes_cancel_and_confirms_the_safe_terminal_checkpoint(
    tmp_path: Path,
) -> None:
    observed: queue.Queue[object] = queue.Queue()
    stop = threading.Event()
    state_directory = tmp_path / "cancel-state"
    opened = ExecutorLedger(
        state_directory=state_directory,
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
    )
    offered = offer()
    opened.receive_command(offered)
    opened.compare_and_set_checkpoint(
        attempt_id=str(offered.execution_attempt_id),
        expected_revision=1,
        state=AttemptCheckpointState.RUNNING,
        last_event_sequence=2,
    )
    cancel = control(
        "task.cancel",
        sequence=2,
        message_id="323e4567-e89b-42d3-a456-426614174021",
        correlation_id="323e4567-e89b-42d3-a456-426614174022",
    )

    def handler(connection: ServerConnection) -> None:
        try:
            parse_executor_message(connection.recv(timeout=2))
            connection.send(cancel.model_dump_json())
            terminal = tuple(parse_executor_message(connection.recv(timeout=2)) for _ in range(2))
            observed.put(terminal)
            stop.set()
        except Exception as error:
            observed.put(error)

    server, thread, port = run_server(handler)
    try:
        process, output = process_for(port, state_directory)
        process.run(stop)
        messages = observed.get(timeout=2)
        if isinstance(messages, Exception):
            raise messages
        assert [
            message.message_type for message in cast(tuple[ExecutorEnvelope, ...], messages)
        ] == ["task.control_ack", "task.cancelled"]
        checkpoint = opened.get_checkpoint(str(offered.execution_attempt_id))
        assert checkpoint is not None
        assert checkpoint.state is AttemptCheckpointState.TERMINAL
        assert checkpoint.last_command_sequence == 2
        assert checkpoint.last_event_sequence == 3
        assert [json.loads(line)["event"] for line in output.getvalue().splitlines()] == [
            "executor.stopped"
        ]
    finally:
        server.shutdown()
        thread.join(timeout=2)
    assert not thread.is_alive()


@pytest.mark.parametrize(
    ("local_emergency_stop", "expected_lifecycle"),
    (
        (False, ["executor.stopped"]),
        (True, ["executor.healthy", "executor.stopped"]),
    ),
)
def test_process_reports_emergency_stop_uncertain_and_exits_without_network_stop_signal(
    tmp_path: Path,
    local_emergency_stop: bool,
    expected_lifecycle: list[str],
) -> None:
    observed: queue.Queue[object] = queue.Queue()
    state_directory = tmp_path / "emergency-stop-state"
    opened = ExecutorLedger(
        state_directory=state_directory,
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
    )
    offered = offer()
    opened.receive_command(offered)
    opened.compare_and_set_checkpoint(
        attempt_id=str(offered.execution_attempt_id),
        expected_revision=1,
        state=AttemptCheckpointState.RUNNING,
        last_event_sequence=2,
    )
    emergency_stop = control(
        "task.emergency_stop",
        sequence=2,
        message_id="323e4567-e89b-42d3-a456-426614174023",
        correlation_id="323e4567-e89b-42d3-a456-426614174024",
    )

    def handler(connection: ServerConnection) -> None:
        try:
            parse_executor_message(connection.recv(timeout=2))
            connection.send(emergency_stop.model_dump_json())
            terminal = tuple(parse_executor_message(connection.recv(timeout=2)) for _ in range(2))
            observed.put(terminal)
        except Exception as error:
            observed.put(error)

    server, thread, port = run_server(handler)
    try:
        process, output = process_for(
            port,
            state_directory,
            local_emergency_stop=local_emergency_stop,
        )
        process.run(threading.Event())
        messages = observed.get(timeout=2)
        if isinstance(messages, Exception):
            raise messages
        assert [
            message.message_type for message in cast(tuple[ExecutorEnvelope, ...], messages)
        ] == ["task.control_ack", "task.outcome_uncertain"]
        checkpoint = opened.get_checkpoint(str(offered.execution_attempt_id))
        assert checkpoint is not None
        assert checkpoint.state is AttemptCheckpointState.OUTCOME_UNCERTAIN
        assert opened.get_action_emergency_stop().engaged is True
        assert [
            json.loads(line)["event"] for line in output.getvalue().splitlines()
        ] == expected_lifecycle
    finally:
        server.shutdown()
        thread.join(timeout=2)
    assert not thread.is_alive()


@pytest.mark.parametrize(
    ("local_emergency_stop", "expected_lifecycle"),
    (
        (False, ["executor.stopped"]),
        (True, ["executor.healthy", "executor.stopped"]),
    ),
)
def test_emergency_report_process_is_healthy_before_fast_recovery_exit(
    tmp_path: Path,
    local_emergency_stop: bool,
    expected_lifecycle: list[str],
) -> None:
    observed: queue.Queue[object] = queue.Queue()
    state_directory = tmp_path / "emergency-report-state"
    opened = ExecutorLedger(
        state_directory=state_directory,
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
    )
    offered = offer()
    opened.receive_command(offered)
    opened.compare_and_set_checkpoint(
        attempt_id=str(offered.execution_attempt_id),
        expected_revision=1,
        state=AttemptCheckpointState.RUNNING,
        last_event_sequence=2,
    )
    emergency_stop = control(
        "task.emergency_stop",
        sequence=2,
        message_id="323e4567-e89b-42d3-a456-426614174025",
        correlation_id="323e4567-e89b-42d3-a456-426614174026",
    )
    preparer = ExecutorCommandProcessor(
        ledger=opened,
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
        clock=FixedClock(),
        id_source=DeterministicIds(),
    )
    assert [
        message.message_type for message in preparer.handle(emergency_stop.model_dump_json())
    ] == [
        "task.control_ack",
        "task.outcome_uncertain",
    ]

    def handler(connection: ServerConnection) -> None:
        try:
            hello = parse_executor_message(connection.recv(timeout=2))
            recovered = tuple(parse_executor_message(connection.recv(timeout=2)) for _ in range(2))
            observed.put((hello, recovered))
        except Exception as error:
            observed.put(error)

    server, thread, port = run_server(handler)
    try:
        process, output = process_for(
            port,
            state_directory,
            local_emergency_stop=local_emergency_stop,
        )
        process.run(threading.Event())
        messages = observed.get(timeout=2)
        if isinstance(messages, Exception):
            raise messages
        assert [
            json.loads(line)["event"] for line in output.getvalue().splitlines()
        ] == expected_lifecycle
    finally:
        server.shutdown()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_process_collapses_outbox_delivery_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: queue.Queue[object] = queue.Queue()

    def handler(connection: ServerConnection) -> None:
        try:
            parse_executor_message(connection.recv(timeout=2))
            connection.send(offer().model_dump_json())
            observed.put(parse_executor_message(connection.recv(timeout=2)))
        except Exception as error:
            observed.put(error)

    def reject_delivery(_self: object, _message_id: str) -> bool:
        raise RuntimeError("private delivery failure")

    monkeypatch.setattr(ExecutorCommandProcessor, "mark_delivered", reject_delivery)
    server, thread, port = run_server(handler)
    try:
        process, output = process_for(port, tmp_path / "executor-state")
        with pytest.raises(ExecutorProcessRejected) as captured:
            process.run(threading.Event())
        assert captured.value.__context__ is None
        assert output.getvalue() == ""
        message = cast(ExecutorEnvelope, observed.get(timeout=2))
        assert message.message_type == "task.accept"
    finally:
        server.shutdown()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_process_rejects_transport_clock_ids_and_constructor_values_without_leakage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process, _ = process_for(9, tmp_path / "transport-state")
    with pytest.raises(ExecutorProcessRejected) as transport:
        process.run(threading.Event())
    assert transport.value.__cause__ is None
    assert transport.value.__context__ is None

    class FailingClock:
        @staticmethod
        def now() -> datetime:
            raise RuntimeError("private clock failure")

    for clock, id_source in (
        (FailingClock(), DeterministicIds()),
        (FixedClock(), lambda: object()),
    ):
        broken, _ = process_for(
            9,
            tmp_path / f"broken-{id(clock)}",
            clock=clock,
            id_source=id_source,
        )
        with pytest.raises(ExecutorProcessRejected):
            broken.run(threading.Event())

    valid, output = process_for(9, tmp_path / "valid-state")
    with pytest.raises(ExecutorProcessRejected):
        valid.run(cast(threading.Event, object()))
    for values in (
        {"bootstrap": object()},
        {"metadata": object()},
        {"reporter": object()},
        {"command_processor": object()},
        {"clock": object()},
        {"id_source": object()},
        {"open_timeout": timedelta(0)},
        {"close_timeout": cast(timedelta, object())},
        {"restart_reconnect_attempts": 0},
        {"restart_reconnect_attempts": True},
        {"restart_reconnect_attempts": 1001},
        {"restart_reconnect_delay": timedelta(0)},
        {"restart_reconnect_delay": timedelta(seconds=6)},
    ):
        arguments: dict[str, object] = {
            "bootstrap": valid.bootstrap,
            "metadata": valid.metadata,
            "reporter": reporter(output),
            "command_processor": ExecutorCommandProcessor(
                ledger=ExecutorLedger(
                    state_directory=tmp_path / "constructor-state",
                    installation_id=INSTALLATION_ID,
                    executor_id=EXECUTOR_ID,
                ),
                installation_id=INSTALLATION_ID,
                executor_id=EXECUTOR_ID,
            ),
            "clock": FixedClock(),
            "id_source": DeterministicIds(),
            "open_timeout": timedelta(milliseconds=10),
            "close_timeout": timedelta(milliseconds=10),
        }
        arguments.update(values)
        with pytest.raises(ExecutorProcessRejected) as captured:
            LocalExecutorProcess(**arguments)  # type: ignore[arg-type]
        assert captured.value.__cause__ is None
        assert captured.value.__context__ is None

    valid_processor = ExecutorCommandProcessor(
        ledger=ExecutorLedger(
            state_directory=tmp_path / "constructor-network-state",
            installation_id=INSTALLATION_ID,
            executor_id=EXECUTOR_ID,
        ),
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
    )
    with monkeypatch.context() as scoped:
        scoped.setattr(
            valid_processor.ledger,
            "set_transport_connected",
            lambda _connected: (_ for _ in ()).throw(RuntimeError("private ledger failure")),
        )
        with pytest.raises(ExecutorProcessRejected):
            LocalExecutorProcess(
                bootstrap=valid.bootstrap,
                metadata=valid.metadata,
                reporter=reporter(output),
                command_processor=valid_processor,
                clock=FixedClock(),
                id_source=DeterministicIds(),
                open_timeout=timedelta(milliseconds=10),
                close_timeout=timedelta(milliseconds=10),
            )


@pytest.mark.parametrize(
    ("clock", "id_source"),
    (
        (
            cast(object, type("InvalidClock", (), {"now": lambda _self: object()})()),
            DeterministicIds(),
        ),
        (
            cast(
                object,
                type(
                    "NaiveClock",
                    (),
                    {"now": lambda _self: datetime(2026, 7, 19, 9, 0)},
                )(),
            ),
            DeterministicIds(),
        ),
        (FixedClock(), lambda: object()),
        (FixedClock(), lambda: UUID(int=4)),
    ),
)
def test_process_rejects_invalid_clock_or_ids_after_real_connection(
    clock: object,
    id_source: object,
    tmp_path: Path,
) -> None:
    observed: queue.Queue[object] = queue.Queue()

    def handler(connection: ServerConnection) -> None:
        try:
            observed.put(connection.recv(timeout=2))
        except Exception:
            observed.put(True)

    server, thread, port = run_server(handler)
    try:
        process, _ = process_for(
            port,
            tmp_path / "executor-state",
            clock=clock,
            id_source=id_source,
        )
        with pytest.raises(ExecutorProcessRejected):
            process.run(threading.Event())
        assert observed.get(timeout=2) is True
    finally:
        server.shutdown()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_invalid_lifecycle_shape_is_collapsed_to_the_process_error(tmp_path: Path) -> None:
    process, _ = process_for(
        9,
        tmp_path / "executor-state",
        id_source=DeterministicIds(),
    )

    with pytest.raises(ExecutorProcessRejected):
        process._lifecycle(message_type="private.invalid", sequence=1)


def test_explicit_falsy_clock_is_not_silently_replaced_by_the_system_clock(
    tmp_path: Path,
) -> None:
    class FalsyClock(FixedClock):
        def __bool__(self) -> bool:
            return False

    process, _ = process_for(
        9,
        tmp_path / "executor-state",
        clock=FalsyClock(),
        id_source=DeterministicIds(),
    )

    hello = process._lifecycle(message_type="executor.hello", sequence=1)

    assert hello.sent_at == NOW


@pytest.mark.parametrize(
    "mode",
    ("already-stopped", "stop-during-timeout", "close-after-stop", "frame-after-stop"),
)
def test_stop_races_are_graceful_without_claiming_health(
    mode: str,
    tmp_path: Path,
) -> None:
    stop = threading.Event()
    observed: queue.Queue[object] = queue.Queue()
    if mode == "already-stopped":
        stop.set()

    def handler(connection: ServerConnection) -> None:
        try:
            hello = parse_executor_message(connection.recv(timeout=2))
            observed.put(hello)
            if mode == "stop-during-timeout":
                threading.Timer(0.05, stop.set).start()
            elif mode != "already-stopped":
                time.sleep(0.05)
                stop.set()
            if mode == "close-after-stop":
                connection.close(code=1000, reason="controlled stop")
            elif mode == "frame-after-stop":
                connection.send("{}")
            else:
                with suppress(Exception):
                    connection.recv(timeout=2)
        except Exception as error:
            observed.put(error)

    server, thread, port = run_server(handler)
    try:
        process, output = process_for(port, tmp_path / "executor-state")
        process.run(stop)
        hello = observed.get(timeout=2)
        if isinstance(hello, Exception):
            raise hello
        assert isinstance(hello, ExecutorLifecycleEnvelope)
        assert [json.loads(line)["event"] for line in output.getvalue().splitlines()] == [
            "executor.stopped"
        ]
    finally:
        server.shutdown()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_control_plane_restart_reconnects_and_replays_exact_durable_outbox(
    tmp_path: Path,
) -> None:
    observed: queue.Queue[object] = queue.Queue()
    connections = count(1)
    stop = threading.Event()
    offered = offer()

    def handler(connection: ServerConnection) -> None:
        try:
            connection_number = next(connections)
            hello = parse_executor_message(connection.recv(timeout=3))
            if connection_number == 1:
                heartbeat = parse_executor_message(connection.recv(timeout=3))
                connection.send(offered.model_dump_json())
                original = tuple(
                    parse_executor_message(connection.recv(timeout=3)) for _ in range(2)
                )
                observed.put((hello, heartbeat, original))
                connection.close(code=1012, reason="control plane restart")
                return
            recovered = tuple(parse_executor_message(connection.recv(timeout=3)) for _ in range(2))
            connection.send(offered.model_dump_json())
            duplicate = tuple(parse_executor_message(connection.recv(timeout=3)) for _ in range(2))
            recovered_heartbeat = parse_executor_message(connection.recv(timeout=3))
            observed.put((hello, recovered, duplicate, recovered_heartbeat))
            stop.set()
        except Exception as error:
            observed.put(error)
            stop.set()

    server, thread, port = run_server(handler)
    try:
        process, output = process_for(port, tmp_path / "restart-state")
        process.run(stop)
        first = observed.get(timeout=3)
        second = observed.get(timeout=3)
        if isinstance(first, Exception):
            raise first
        if isinstance(second, Exception):
            raise second
        first_hello, heartbeat, original = cast(
            tuple[
                ExecutorLifecycleEnvelope, ExecutorLifecycleEnvelope, tuple[ExecutorEnvelope, ...]
            ],
            first,
        )
        second_hello, recovered, duplicate, recovered_heartbeat = cast(
            tuple[
                ExecutorLifecycleEnvelope,
                tuple[ExecutorEnvelope, ...],
                tuple[ExecutorEnvelope, ...],
                ExecutorLifecycleEnvelope,
            ],
            second,
        )
        assert first_hello.message_type == second_hello.message_type == "executor.hello"
        assert first_hello.idempotency_key == second_hello.idempotency_key
        assert first_hello.message_id != second_hello.message_id
        assert heartbeat.message_type == "executor.heartbeat"
        assert tuple(message.message_id for message in recovered) == tuple(
            message.message_id for message in original
        )
        assert tuple(message.message_id for message in duplicate) == tuple(
            message.message_id for message in original
        )
        assert recovered_heartbeat.message_type == "executor.heartbeat"
        assert [json.loads(line)["event"] for line in output.getvalue().splitlines()] == [
            "executor.healthy",
            "executor.stopped",
        ]
        assert process._command_processor.ledger.transport_connected() is False
    finally:
        server.shutdown()
        thread.join(timeout=3)
    assert not thread.is_alive()


def test_abnormal_network_disconnect_reconnects_and_replays_exact_durable_outbox(
    tmp_path: Path,
) -> None:
    observed: queue.Queue[object] = queue.Queue()
    connections = count(1)
    stop = threading.Event()
    offered = offer()

    def handler(connection: ServerConnection) -> None:
        try:
            connection_number = next(connections)
            hello = parse_executor_message(connection.recv(timeout=3))
            if connection_number == 1:
                heartbeat = parse_executor_message(connection.recv(timeout=3))
                connection.send(offered.model_dump_json())
                original = tuple(
                    parse_executor_message(connection.recv(timeout=3)) for _ in range(2)
                )
                observed.put((hello, heartbeat, original))
                connection.close_socket()
                return
            recovered = tuple(parse_executor_message(connection.recv(timeout=3)) for _ in range(2))
            recovered_heartbeat = parse_executor_message(connection.recv(timeout=3))
            observed.put((hello, recovered, recovered_heartbeat))
            stop.set()
        except Exception as error:
            observed.put(error)
            stop.set()

    server, thread, port = run_server(handler)
    try:
        process, output = process_for(port, tmp_path / "network-recovery-state")
        process.run(stop)
        first = observed.get(timeout=3)
        second = observed.get(timeout=3)
        if isinstance(first, Exception):
            raise first
        if isinstance(second, Exception):
            raise second
        first_hello, heartbeat, original = cast(
            tuple[
                ExecutorLifecycleEnvelope, ExecutorLifecycleEnvelope, tuple[ExecutorEnvelope, ...]
            ],
            first,
        )
        second_hello, recovered, recovered_heartbeat = cast(
            tuple[
                ExecutorLifecycleEnvelope,
                tuple[ExecutorEnvelope, ...],
                ExecutorLifecycleEnvelope,
            ],
            second,
        )
        assert first_hello.message_type == second_hello.message_type == "executor.hello"
        assert heartbeat.message_type == recovered_heartbeat.message_type == "executor.heartbeat"
        assert tuple(message.message_id for message in recovered) == tuple(
            message.message_id for message in original
        )
        assert [json.loads(line)["event"] for line in output.getvalue().splitlines()] == [
            "executor.healthy",
            "executor.stopped",
        ]
        assert process._command_processor.ledger.transport_connected() is False
    finally:
        server.shutdown()
        thread.join(timeout=3)
    assert not thread.is_alive()


def test_suspension_gap_closes_the_stale_transport_and_ignores_expired_command(
    tmp_path: Path,
) -> None:
    class OffsetMonotonic:
        def __init__(self) -> None:
            self._lock = threading.Lock()
            self._offset = 0.0

        def now(self) -> float:
            with self._lock:
                return time.monotonic() + self._offset

        def jump(self, seconds: float) -> None:
            with self._lock:
                self._offset += seconds

    clock = OffsetMonotonic()
    observed: queue.Queue[object] = queue.Queue()
    stop = threading.Event()
    connections = count(1)
    stale = expired_offer()

    def handler(connection: ServerConnection) -> None:
        try:
            connection_number = next(connections)
            hello = parse_executor_message(connection.recv(timeout=3))
            if connection_number == 1:
                heartbeat = parse_executor_message(connection.recv(timeout=3))
                clock.jump(10)
                connection.send(stale.model_dump_json())
                observed.put((hello, heartbeat))
                return
            connection.send(stale.model_dump_json())
            recovered_heartbeat = parse_executor_message(connection.recv(timeout=3))
            observed.put((hello, recovered_heartbeat))
            stop.set()
        except Exception as error:
            observed.put(error)
            stop.set()

    server, thread, port = run_server(handler)
    diagnostics = StringIO()
    state_directory = tmp_path / "suspension-state"
    try:
        process, _ = process_for(
            port,
            state_directory,
            monotonic_source=clock.now,
            suspend_gap_threshold=timedelta(seconds=5),
            diagnostic_output=diagnostics,
        )
        process.run(stop)
        first = observed.get(timeout=3)
        second = observed.get(timeout=3)
        if isinstance(first, Exception):
            raise first
        if isinstance(second, Exception):
            raise second
        assert all(
            message.message_type in {"executor.hello", "executor.heartbeat"}
            for pair in (first, second)
            for message in cast(tuple[ExecutorEnvelope, ...], pair)
        )
        assert (
            process._command_processor.ledger.get_checkpoint(str(stale.execution_attempt_id))
            is None
        )
        assert diagnostics.getvalue().splitlines() == [
            "executor.recovery system_suspension_detected",
            "executor.recovery command_deadline_expired",
            "executor.recovery transport_recovered",
        ]
        assert process._command_processor.ledger.transport_connected() is False
    finally:
        server.shutdown()
        thread.join(timeout=3)
    assert not thread.is_alive()


def test_suspension_gap_after_received_frame_reconnects_before_command_processing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class OffsetMonotonic:
        def __init__(self) -> None:
            self.offset = 0.0

        def now(self) -> float:
            return time.monotonic() + self.offset

    clock = OffsetMonotonic()
    observed: queue.Queue[object] = queue.Queue()
    stop = threading.Event()
    connections = count(1)
    stale = expired_offer()

    def handler(connection: ServerConnection) -> None:
        try:
            connection_number = next(connections)
            hello = parse_executor_message(connection.recv(timeout=3))
            observed.put(hello)
            if connection_number == 1:
                connection.send(stale.model_dump_json())
                with suppress(Exception):
                    connection.recv(timeout=3)
                return
            stop.set()
        except Exception as error:
            observed.put(error)
            stop.set()

    server, thread, port = run_server(handler)
    diagnostics = StringIO()
    try:
        process, _ = process_for(
            port,
            tmp_path / "received-frame-gap-state",
            monotonic_source=clock.now,
            suspend_gap_threshold=timedelta(seconds=5),
            diagnostic_output=diagnostics,
        )
        original_send_local_outbox = process._send_local_outbox
        jumped = False

        def jump_after_pre_receive_checks(websocket: ClientConnection) -> None:
            nonlocal jumped
            original_send_local_outbox(websocket)
            if not jumped:
                jumped = True
                clock.offset += 10

        monkeypatch.setattr(process, "_send_local_outbox", jump_after_pre_receive_checks)
        process.run(stop)
        first = observed.get(timeout=3)
        second = observed.get(timeout=3)
        if isinstance(first, Exception):
            raise first
        if isinstance(second, Exception):
            raise second
        assert cast(ExecutorEnvelope, first).message_type == "executor.hello"
        assert cast(ExecutorEnvelope, second).message_type == "executor.hello"
        assert (
            process._command_processor.ledger.get_checkpoint(str(stale.execution_attempt_id))
            is None
        )
        assert diagnostics.getvalue().splitlines() == [
            "executor.recovery system_suspension_detected"
        ]
    finally:
        server.shutdown()
        thread.join(timeout=3)
    assert not thread.is_alive()


def test_long_command_processing_does_not_impersonate_system_suspension(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class OffsetMonotonic:
        def __init__(self) -> None:
            self.offset = 0.0

        def now(self) -> float:
            return time.monotonic() + self.offset

    clock = OffsetMonotonic()
    observed: queue.Queue[object] = queue.Queue()
    stop = threading.Event()

    def handler(connection: ServerConnection) -> None:
        try:
            hello = parse_executor_message(connection.recv(timeout=3))
            connection.send(offer().model_dump_json())
            batch = tuple(parse_executor_message(connection.recv(timeout=3)) for _ in range(2))
            heartbeat = parse_executor_message(connection.recv(timeout=3))
            observed.put((hello, batch, heartbeat))
            stop.set()
        except Exception as error:
            observed.put(error)
            stop.set()

    server, thread, port = run_server(handler)
    diagnostics = StringIO()
    try:
        process, _ = process_for(
            port,
            tmp_path / "long-command-state",
            monotonic_source=clock.now,
            suspend_gap_threshold=timedelta(seconds=5),
            diagnostic_output=diagnostics,
        )
        original_handle = process._command_processor.handle

        def delayed_handle(source: str | bytes) -> tuple[ExecutorEnvelope, ...]:
            outcome = original_handle(source)
            clock.offset += 10
            return outcome

        monkeypatch.setattr(process._command_processor, "handle", delayed_handle)
        process.run(stop)
        messages = observed.get(timeout=3)
        if isinstance(messages, Exception):
            raise messages
        hello, batch, heartbeat = cast(
            tuple[ExecutorEnvelope, tuple[ExecutorEnvelope, ...], ExecutorEnvelope], messages
        )
        assert hello.message_type == "executor.hello"
        assert len(batch) == 2
        assert heartbeat.message_type == "executor.heartbeat"
        assert diagnostics.getvalue() == ""
    finally:
        server.shutdown()
        thread.join(timeout=3)
    assert not thread.is_alive()


def test_gap_between_heartbeat_iterations_uses_the_same_safe_reconnect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class OffsetMonotonic:
        def __init__(self) -> None:
            self.offset = 0.0

        def now(self) -> float:
            return time.monotonic() + self.offset

    clock = OffsetMonotonic()
    observed: queue.Queue[object] = queue.Queue()
    stop = threading.Event()
    connections = count(1)

    def handler(connection: ServerConnection) -> None:
        try:
            connection_number = next(connections)
            hello = parse_executor_message(connection.recv(timeout=3))
            heartbeat = parse_executor_message(connection.recv(timeout=3))
            observed.put((hello, heartbeat))
            if connection_number == 1:
                with suppress(Exception):
                    connection.recv(timeout=3)
            else:
                stop.set()
        except Exception as error:
            observed.put(error)
            stop.set()

    server, thread, port = run_server(handler)
    try:
        process, output = process_for(
            port,
            tmp_path / "between-heartbeats-state",
            monotonic_source=clock.now,
            suspend_gap_threshold=timedelta(seconds=5),
        )
        original_healthy = process._reporter.healthy

        def healthy_then_suspend() -> None:
            original_healthy()
            clock.offset += 10

        monkeypatch.setattr(process._reporter, "healthy", healthy_then_suspend)
        process.run(stop)
        first = observed.get(timeout=3)
        second = observed.get(timeout=3)
        if isinstance(first, Exception):
            raise first
        if isinstance(second, Exception):
            raise second
        assert all(
            message.message_type in {"executor.hello", "executor.heartbeat"}
            for pair in (first, second)
            for message in cast(tuple[ExecutorEnvelope, ...], pair)
        )
        assert [json.loads(line)["event"] for line in output.getvalue().splitlines()] == [
            "executor.healthy",
            "executor.stopped",
        ]
        process._report_expired_command()
    finally:
        server.shutdown()
        thread.join(timeout=3)
    assert not thread.is_alive()


def test_gap_observed_after_socket_timeout_reconnects_before_heartbeat(
    tmp_path: Path,
) -> None:
    class TimeoutGapMonotonic:
        def __init__(self) -> None:
            self.calls = 0
            self.offset = 0.0

        def now(self) -> float:
            self.calls += 1
            if self.calls == 3:
                self.offset = 10.0
            return time.monotonic() + self.offset

    clock = TimeoutGapMonotonic()
    observed: queue.Queue[object] = queue.Queue()
    stop = threading.Event()
    connections = count(1)

    def handler(connection: ServerConnection) -> None:
        try:
            connection_number = next(connections)
            hello = parse_executor_message(connection.recv(timeout=3))
            if connection_number == 1:
                with suppress(Exception):
                    connection.recv(timeout=3)
                observed.put(hello)
                return
            heartbeat = parse_executor_message(connection.recv(timeout=3))
            observed.put((hello, heartbeat))
            stop.set()
        except Exception as error:
            observed.put(error)
            stop.set()

    server, thread, port = run_server(handler)
    diagnostics = StringIO()
    try:
        process, _ = process_for(
            port,
            tmp_path / "timeout-gap-state",
            monotonic_source=clock.now,
            suspend_gap_threshold=timedelta(seconds=5),
            diagnostic_output=diagnostics,
        )
        process.run(stop)
        first = observed.get(timeout=3)
        second = observed.get(timeout=3)
        if isinstance(first, Exception):
            raise first
        if isinstance(second, Exception):
            raise second
        assert cast(ExecutorEnvelope, first).message_type == "executor.hello"
        assert [message.message_type for message in cast(tuple[ExecutorEnvelope, ...], second)] == [
            "executor.hello",
            "executor.heartbeat",
        ]
        assert diagnostics.getvalue().splitlines() == [
            "executor.recovery system_suspension_detected",
            "executor.recovery transport_recovered",
        ]
    finally:
        server.shutdown()
        thread.join(timeout=3)
    assert not thread.is_alive()


def test_monotonic_clock_and_suspend_threshold_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(ExecutorProcessRejected):
        process_for(
            9,
            tmp_path / "threshold-too-large",
            suspend_gap_threshold=timedelta(minutes=11),
        )

    def failing_source() -> float:
        raise RuntimeError("private monotonic failure")

    for index, source in enumerate(
        (
            lambda: True,
            lambda: cast(float, object()),
            lambda: float("inf"),
            lambda: -1.0,
            failing_source,
        )
    ):
        process, _ = process_for(
            9,
            tmp_path / f"invalid-monotonic-{index}",
            monotonic_source=source,
        )
        with pytest.raises(ExecutorProcessRejected):
            process._monotonic_now()

    process, _ = process_for(9, tmp_path / "backwards-monotonic")
    with pytest.raises(ExecutorProcessRejected):
        process._suspension_detected(2.0, 1.0)


def test_initial_network_outage_retries_but_protocol_close_remains_fixed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0
    original_connect = executor_transport.connect_executor_websocket
    stop = threading.Event()

    def connect_after_outage(**values: object) -> object:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("private network unavailable")
        return original_connect(**values)  # type: ignore[arg-type]

    monkeypatch.setattr(executor_runtime, "connect_executor_websocket", connect_after_outage)

    def handler(connection: ServerConnection) -> None:
        parse_executor_message(connection.recv(timeout=2))
        parse_executor_message(connection.recv(timeout=2))
        stop.set()

    server, thread, port = run_server(handler)
    try:
        process, output = process_for(
            port,
            tmp_path / "initial-network-outage",
            restart_reconnect_delay=timedelta(milliseconds=1),
        )
        process.run(stop)
        assert attempts == 2
        assert [json.loads(line)["event"] for line in output.getvalue().splitlines()] == [
            "executor.healthy",
            "executor.stopped",
        ]
    finally:
        server.shutdown()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_control_plane_restart_reconnect_is_bounded_and_stop_interruptible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection_attempts = 0
    original_connect = executor_transport.connect_executor_websocket

    def counted_connect(**values: object) -> object:
        nonlocal connection_attempts
        connection_attempts += 1
        return original_connect(**values)  # type: ignore[arg-type]

    monkeypatch.setattr(executor_runtime, "connect_executor_websocket", counted_connect)
    server_holder: list[Server] = []
    closed = threading.Event()

    def handler(connection: ServerConnection) -> None:
        try:
            parse_executor_message(connection.recv(timeout=2))
            connection.close(code=1012, reason="control plane restart")
            closed.set()
            threading.Thread(target=server_holder[0].shutdown, daemon=True).start()
        except Exception:
            closed.set()

    server, thread, port = run_server(handler)
    server_holder.append(server)
    try:
        process, _ = process_for(
            port,
            tmp_path / "bounded-restart-state",
            restart_reconnect_attempts=2,
            restart_reconnect_delay=timedelta(milliseconds=50),
        )
        with pytest.raises(ExecutorProcessRejected):
            process.run(threading.Event())
        assert closed.is_set()
        assert connection_attempts == 3
    finally:
        server.shutdown()
        thread.join(timeout=2)
    assert not thread.is_alive()

    stop = threading.Event()
    interrupt_server_holder: list[Server] = []

    def interrupt_handler(connection: ServerConnection) -> None:
        parse_executor_message(connection.recv(timeout=2))
        connection.close(code=1012, reason="control plane restart")
        threading.Thread(
            target=interrupt_server_holder[0].shutdown,
            daemon=True,
        ).start()
        threading.Timer(0.05, stop.set).start()

    interrupt_server, interrupt_thread, interrupt_port = run_server(interrupt_handler)
    interrupt_server_holder.append(interrupt_server)
    try:
        process, output = process_for(
            interrupt_port,
            tmp_path / "interruptible-restart-state",
            restart_reconnect_attempts=100,
            restart_reconnect_delay=timedelta(seconds=1),
        )
        started_at = time.monotonic()
        process.run(stop)
        assert time.monotonic() - started_at < 0.5
        assert [json.loads(line)["event"] for line in output.getvalue().splitlines()] == [
            "executor.stopped"
        ]
    finally:
        interrupt_server.shutdown()
        interrupt_thread.join(timeout=2)
    assert not interrupt_thread.is_alive()


def test_repeated_control_plane_restart_closes_consume_the_bounded_budget(
    tmp_path: Path,
) -> None:
    connection_count = 0

    def handler(connection: ServerConnection) -> None:
        nonlocal connection_count
        connection_count += 1
        parse_executor_message(connection.recv(timeout=2))
        connection.close(code=1012, reason="repeated control plane restart")

    server, thread, port = run_server(handler)
    try:
        process, _ = process_for(
            port,
            tmp_path / "repeated-restart-state",
            restart_reconnect_attempts=2,
            restart_reconnect_delay=timedelta(milliseconds=1),
        )
        with pytest.raises(ExecutorProcessRejected):
            process.run(threading.Event())
        assert connection_count == 3
    finally:
        server.shutdown()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_stop_interrupts_a_failed_connection_attempt_during_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stop = threading.Event()
    attempts = 0
    original_connect = executor_transport.connect_executor_websocket

    def connect_then_stop(**values: object) -> object:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return original_connect(**values)  # type: ignore[arg-type]
        stop.set()
        raise OSError("private unavailable endpoint")

    monkeypatch.setattr(executor_runtime, "connect_executor_websocket", connect_then_stop)

    def handler(connection: ServerConnection) -> None:
        parse_executor_message(connection.recv(timeout=2))
        connection.close(code=1012, reason="control plane restart")

    server, thread, port = run_server(handler)
    try:
        process, output = process_for(
            port,
            tmp_path / "failed-reconnect-stop-state",
            restart_reconnect_attempts=100,
            restart_reconnect_delay=timedelta(seconds=1),
        )
        process.run(stop)
        assert attempts == 2
        assert [json.loads(line)["event"] for line in output.getvalue().splitlines()] == [
            "executor.stopped"
        ]
    finally:
        server.shutdown()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_restart_close_is_preserved_by_both_outbox_senders(tmp_path: Path) -> None:
    class RestartingSocket:
        @staticmethod
        def send(_source: str) -> None:
            raise ConnectionClosedError(
                Close(1012, "control plane restart"),
                None,
            )

    socket = cast(ClientConnection, RestartingSocket())
    process, _ = process_for(9, tmp_path / "durable-outbox-close-state")
    messages = process._command_processor.handle(offer().model_dump_json())
    with pytest.raises(ConnectionClosedError):
        process._send_outbox(socket, messages)

    local_outbox: queue.Queue[object] = queue.Queue()
    local_outbox.put(session_health())
    local_process, _ = process_for(
        9,
        tmp_path / "local-outbox-close-state",
        local_outbox=local_outbox,
    )
    with pytest.raises(ConnectionClosedError):
        local_process._send_local_outbox(socket)

    class DisconnectedSocket:
        @staticmethod
        def send(_source: str) -> None:
            raise OSError("private network outage")

    disconnected = cast(ClientConnection, DisconnectedSocket())
    with pytest.raises(ExecutorProcessRejected) as captured:
        process._send_outbox(disconnected, messages)
    assert executor_runtime._is_recoverable_transport_error(captured.value) is True


def test_fixed_connect_failure_and_transport_gate_storage_failures_do_not_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed, _ = process_for(9, tmp_path / "fixed-connect-failure")
    with monkeypatch.context() as scoped:
        scoped.setattr(
            executor_runtime,
            "connect_executor_websocket",
            lambda **_values: (_ for _ in ()).throw(ValueError("private protocol failure")),
        )
        with pytest.raises(ExecutorProcessRejected):
            fixed.run(threading.Event())

    for mode in ("disconnect", "final"):
        stop = threading.Event()

        def handler(
            connection: ServerConnection,
            current_mode: str = mode,
            current_stop: threading.Event = stop,
        ) -> None:
            parse_executor_message(connection.recv(timeout=2))
            if current_mode == "disconnect":
                connection.close(code=1012, reason="control plane restart")
                return
            parse_executor_message(connection.recv(timeout=2))
            current_stop.set()

        server, thread, port = run_server(handler)
        try:
            process, _ = process_for(port, tmp_path / f"gate-storage-{mode}")

            def fail_offline(connected: bool) -> bool:
                if not connected:
                    raise RuntimeError("private ledger failure")
                return True

            monkeypatch.setattr(
                process._command_processor.ledger,
                "set_transport_connected",
                fail_offline,
            )
            with pytest.raises(ExecutorProcessRejected):
                process.run(stop)
        finally:
            server.shutdown()
            thread.join(timeout=2)
        assert not thread.is_alive()


def test_stop_during_restart_close_from_outbox_is_a_clean_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stop = threading.Event()

    class StopDuringReplay:
        def __init__(self) -> None:
            self.send_count = 0

        def __enter__(self) -> StopDuringReplay:
            return self

        def __exit__(self, *_values: object) -> None:
            return None

        def send(self, _source: str) -> None:
            self.send_count += 1
            if self.send_count == 2:
                stop.set()
                raise ConnectionClosedError(
                    Close(1012, "control plane restart"),
                    None,
                )

    websocket = StopDuringReplay()
    monkeypatch.setattr(
        executor_runtime,
        "connect_executor_websocket",
        lambda **_values: websocket,
    )
    process, output = process_for(9, tmp_path / "stop-during-outbox-state")
    process._command_processor.handle(offer().model_dump_json())

    process.run(stop)

    assert websocket.send_count == 2
    assert [json.loads(line)["event"] for line in output.getvalue().splitlines()] == [
        "executor.stopped"
    ]


def test_non_restart_server_close_without_stop_is_a_fixed_failure(tmp_path: Path) -> None:
    observed: queue.Queue[object] = queue.Queue()

    def handler(connection: ServerConnection) -> None:
        try:
            parse_executor_message(connection.recv(timeout=2))
            connection.close(code=1011, reason="controlled failure")
            observed.put(True)
        except Exception as error:
            observed.put(error)

    server, thread, port = run_server(handler)
    try:
        process, _ = process_for(port, tmp_path / "executor-state")
        with pytest.raises(ExecutorProcessRejected):
            process.run(threading.Event())
        assert observed.get(timeout=2) is True
    finally:
        server.shutdown()
        thread.join(timeout=2)
    assert not thread.is_alive()
