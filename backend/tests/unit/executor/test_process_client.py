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
from websockets.sync.server import Server, ServerConnection, serve
from websockets.typing import Subprotocol

from automation_tool.executor.authentication import LocalSessionAuthenticator
from automation_tool.executor.bootstrap import read_executor_bootstrap
from automation_tool.executor.command_processor import ExecutorCommandProcessor
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


def bootstrap(port: int, state_directory: Path) -> object:
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
) -> tuple[LocalExecutorProcess, StringIO]:
    target = output or StringIO()
    values: dict[str, object] = {
        "bootstrap": bootstrap(port, state_directory),
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

        def send(self, source: str) -> None:
            if self.fail:
                raise OSError("private socket failure")

    invalid: queue.Queue[object] = queue.Queue()
    invalid.put(object())
    process, _ = process_for(9, tmp_path / "invalid-local-outbox", local_outbox=invalid)
    with pytest.raises(ExecutorProcessRejected):
        process._send_local_outbox(cast(object, Socket()))  # type: ignore[arg-type]

    failing: queue.Queue[object] = queue.Queue()
    failing.put(session_health())
    process, _ = process_for(9, tmp_path / "failing-local-outbox", local_outbox=failing)
    with pytest.raises(ExecutorProcessRejected):
        process._send_local_outbox(cast(object, Socket(fail=True)))  # type: ignore[arg-type]


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
            batch = tuple(parse_executor_message(connection.recv(timeout=2)) for _ in range(6))
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
            "step.started",
            "step.progress",
            "step.completed",
            "task.completed",
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


def test_server_close_without_stop_is_a_fixed_failure(tmp_path: Path) -> None:
    observed: queue.Queue[object] = queue.Queue()

    def handler(connection: ServerConnection) -> None:
        try:
            parse_executor_message(connection.recv(timeout=2))
            connection.close(code=1012, reason="controlled restart")
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
