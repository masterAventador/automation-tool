from __future__ import annotations

import json
import queue
import threading
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from io import BytesIO, StringIO
from itertools import count
from typing import cast
from uuid import UUID

import pytest
from pydantic import SecretStr
from websockets.sync.server import Server, ServerConnection, serve
from websockets.typing import Subprotocol

from automation_tool.executor.authentication import LocalSessionAuthenticator
from automation_tool.executor.bootstrap import read_executor_bootstrap
from automation_tool.executor.runtime import (
    ExecutorProcessRejected,
    ExecutorProcessReporter,
    LocalExecutorProcess,
    RuntimeMetadata,
)
from automation_tool.protocol import (
    EXECUTOR_WEBSOCKET_SUBPROTOCOL,
    ExecutorLifecycleEnvelope,
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


def bootstrap(port: int) -> object:
    source = json.dumps(
        {
            "bootstrap_version": "1",
            "websocket_url": f"ws://127.0.0.1:{port}/api/v1/executors/connect",
            "local_session_token": LOCAL_SESSION_TOKEN,
            "session_token": "private-session",
            "installation_id": INSTALLATION_ID,
            "executor_id": EXECUTOR_ID,
            "heartbeat_interval_seconds": 1,
        },
        separators=(",", ":"),
    )
    return read_executor_bootstrap(BytesIO((source + "\n").encode()))


def process_for(
    port: int,
    *,
    output: StringIO | None = None,
    clock: object = FixedClock(),
    id_source: object = None,
) -> tuple[LocalExecutorProcess, StringIO]:
    target = output or StringIO()
    values: dict[str, object] = {
        "bootstrap": bootstrap(port),
        "metadata": RuntimeMetadata(
            executor_version="0.1.0",
            platform="macos",
            architecture="arm64",
        ),
        "reporter": reporter(target),
        "clock": clock,
        "open_timeout": timedelta(milliseconds=100),
        "close_timeout": timedelta(milliseconds=100),
    }
    if id_source is not None:
        values["id_source"] = id_source
    return LocalExecutorProcess(**values), target  # type: ignore[arg-type]


def test_process_sends_formal_hello_and_heartbeat_then_stops_cleanly() -> None:
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
        process, output = process_for(port, id_source=DeterministicIds())
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


@pytest.mark.parametrize("server_message", ("{}", b"private-binary-command"))
def test_process_rejects_every_post_hello_application_frame(server_message: str | bytes) -> None:
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
        process, output = process_for(port)
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


def test_process_rejects_transport_clock_ids_and_constructor_values_without_leakage() -> None:
    process, _ = process_for(9)
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
        broken, _ = process_for(9, clock=clock, id_source=id_source)
        with pytest.raises(ExecutorProcessRejected):
            broken.run(threading.Event())

    valid, output = process_for(9)
    with pytest.raises(ExecutorProcessRejected):
        valid.run(cast(threading.Event, object()))
    for values in (
        {"bootstrap": object()},
        {"metadata": object()},
        {"reporter": object()},
        {"clock": object()},
        {"id_source": object()},
        {"open_timeout": timedelta(0)},
        {"close_timeout": cast(timedelta, object())},
    ):
        arguments: dict[str, object] = {
            "bootstrap": valid.bootstrap,
            "metadata": valid.metadata,
            "reporter": reporter(output),
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
) -> None:
    observed: queue.Queue[object] = queue.Queue()

    def handler(connection: ServerConnection) -> None:
        try:
            observed.put(connection.recv(timeout=2))
        except Exception:
            observed.put(True)

    server, thread, port = run_server(handler)
    try:
        process, _ = process_for(port, clock=clock, id_source=id_source)
        with pytest.raises(ExecutorProcessRejected):
            process.run(threading.Event())
        assert observed.get(timeout=2) is True
    finally:
        server.shutdown()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_invalid_lifecycle_shape_is_collapsed_to_the_process_error() -> None:
    process, _ = process_for(9, id_source=DeterministicIds())

    with pytest.raises(ExecutorProcessRejected):
        process._lifecycle(message_type="private.invalid", sequence=1)


def test_explicit_falsy_clock_is_not_silently_replaced_by_the_system_clock() -> None:
    class FalsyClock(FixedClock):
        def __bool__(self) -> bool:
            return False

    process, _ = process_for(9, clock=FalsyClock(), id_source=DeterministicIds())

    hello = process._lifecycle(message_type="executor.hello", sequence=1)

    assert hello.sent_at == NOW


@pytest.mark.parametrize(
    "mode",
    ("already-stopped", "stop-during-timeout", "close-after-stop", "frame-after-stop"),
)
def test_stop_races_are_graceful_without_claiming_health(mode: str) -> None:
    stop = threading.Event()
    observed: queue.Queue[object] = queue.Queue()
    if mode == "already-stopped":
        stop.set()

    def handler(connection: ServerConnection) -> None:
        try:
            hello = parse_executor_message(connection.recv(timeout=2))
            observed.put(hello)
            if mode != "already-stopped":
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
        process, output = process_for(port)
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


def test_server_close_without_stop_is_a_fixed_failure() -> None:
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
        process, _ = process_for(port)
        with pytest.raises(ExecutorProcessRejected):
            process.run(threading.Event())
        assert observed.get(timeout=2) is True
    finally:
        server.shutdown()
        thread.join(timeout=2)
    assert not thread.is_alive()
