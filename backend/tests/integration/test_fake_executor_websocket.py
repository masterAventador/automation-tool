from __future__ import annotations

import json
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import count
from typing import cast
from uuid import UUID

import pytest
from pydantic import BaseModel
from websockets.sync.server import Server, ServerConnection, serve
from websockets.typing import Subprotocol

from automation_tool.executor.fake import FakeExecutorEngine, FakeExecutorScenario
from automation_tool.executor.fake_client import (
    FakeExecutorClient,
    FakeExecutorClientConfiguration,
    FakeExecutorTransportRejected,
    _wire,
)
from automation_tool.protocol import (
    EXECUTOR_WEBSOCKET_SUBPROTOCOL,
    ExecutorLifecycleEnvelope,
    TaskCommandResultEnvelope,
    TaskEventEnvelope,
    parse_executor_message,
)

NOW = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)
INSTALLATION_ID = "123e4567-e89b-42d3-a456-426614174003"
EXECUTOR_ID = "123e4567-e89b-42d3-a456-426614174004"
TASK_ID = "123e4567-e89b-42d3-a456-426614174005"
ATTEMPT_ID = "123e4567-e89b-42d3-a456-426614174006"


@dataclass
class MutableClock:
    value: datetime = NOW

    def now(self) -> datetime:
        return self.value


class DeterministicIds:
    def __init__(self) -> None:
        self._values = count(1)

    def __call__(self) -> UUID:
        return UUID(f"923e4567-e89b-42d3-a456-{next(self._values):012d}")


def command(message_type: str, *, sequence: int) -> str:
    return json.dumps(
        {
            "protocol_version": "1.0",
            "message_id": f"323e4567-e89b-42d3-a456-{sequence:012d}",
            "message_type": message_type,
            "sent_at": NOW.isoformat().replace("+00:00", "Z"),
            "deadline_at": (NOW + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
            "installation_id": INSTALLATION_ID,
            "executor_id": EXECUTOR_ID,
            "correlation_id": "323e4567-e89b-42d3-a456-426614174002",
            "idempotency_key": f"task:fake:{message_type}:{sequence}",
            "sequence": sequence,
            "payload": {},
            "task_id": TASK_ID,
            "execution_attempt_id": ATTEMPT_ID,
        },
        separators=(",", ":"),
    )


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


def test_fake_client_uses_real_websocket_and_sends_formal_hello_result_and_events() -> None:
    captured: queue.Queue[object] = queue.Queue()

    def handler(connection: ServerConnection) -> None:
        try:
            assert connection.subprotocol == EXECUTOR_WEBSOCKET_SUBPROTOCOL
            assert connection.request is not None
            assert connection.request.headers["authorization"] == "Bearer private-session"
            hello = parse_executor_message(connection.recv(timeout=2))
            assert isinstance(hello, ExecutorLifecycleEnvelope)
            assert hello.message_type == "executor.hello"
            connection.send(command("task.offer", sequence=1))
            messages = tuple(parse_executor_message(connection.recv(timeout=2)) for _ in range(6))
            captured.put(messages)
        except Exception as error:
            captured.put(error)

    server, thread, port = run_server(handler)
    try:
        configuration = FakeExecutorClientConfiguration(
            websocket_url=f"ws://127.0.0.1:{port}/api/v1/executors/connect",
            session_token="private-session",
        )
        client = FakeExecutorClient(
            configuration=configuration,
            engine=FakeExecutorEngine(
                installation_id=INSTALLATION_ID,
                executor_id=EXECUTOR_ID,
                scenario=FakeExecutorScenario.SUCCEED,
                clock=MutableClock(),
                id_source=DeterministicIds(),
            ),
        )

        assert client.run(max_commands=1) == 1
        messages = captured.get(timeout=2)
        if isinstance(messages, Exception):
            raise messages
        typed = cast(
            tuple[TaskCommandResultEnvelope | TaskEventEnvelope, ...],
            messages,
        )
        assert isinstance(typed[0], TaskCommandResultEnvelope)
        assert all(isinstance(message, TaskEventEnvelope) for message in typed[1:])
        assert [message.message_type for message in typed] == [
            "task.accept",
            "task.started",
            "step.started",
            "step.progress",
            "step.completed",
            "task.completed",
        ]
    finally:
        server.shutdown()
        thread.join(timeout=2)
    assert not thread.is_alive()


@pytest.mark.parametrize(
    ("scenario", "expected_types"),
    (
        (
            FakeExecutorScenario.PARTIAL,
            (
                "task.accept",
                "task.started",
                "step.started",
                "step.completed",
                "step.failed",
                "task.partially_completed",
            ),
        ),
        (
            FakeExecutorScenario.FAIL,
            (
                "task.accept",
                "task.started",
                "step.started",
                "step.failed",
                "task.failed",
            ),
        ),
        (
            FakeExecutorScenario.LOGIN_REQUIRED,
            ("task.accept", "session.login_required"),
        ),
        (
            FakeExecutorScenario.HANDOFF,
            ("task.accept", "task.started", "handoff.requested"),
        ),
        (
            FakeExecutorScenario.OUTCOME_UNCERTAIN,
            ("task.accept", "task.started", "task.outcome_uncertain"),
        ),
        (FakeExecutorScenario.REJECT, ("task.reject",)),
    ),
)
def test_every_offer_scenario_replays_over_real_websocket(
    scenario: FakeExecutorScenario,
    expected_types: tuple[str, ...],
) -> None:
    captured: queue.Queue[object] = queue.Queue()

    def handler(connection: ServerConnection) -> None:
        try:
            parse_executor_message(connection.recv(timeout=2))
            connection.send(command("task.offer", sequence=1))
            messages = tuple(
                parse_executor_message(connection.recv(timeout=2)) for _ in expected_types
            )
            captured.put(messages)
        except Exception as error:
            captured.put(error)

    server, thread, port = run_server(handler)
    try:
        client = FakeExecutorClient(
            configuration=FakeExecutorClientConfiguration(
                websocket_url=f"ws://127.0.0.1:{port}/api/v1/executors/connect",
                session_token="private-session",
            ),
            engine=FakeExecutorEngine(
                installation_id=INSTALLATION_ID,
                executor_id=EXECUTOR_ID,
                scenario=scenario,
                clock=MutableClock(),
                id_source=DeterministicIds(),
            ),
        )

        assert client.run(max_commands=1) == 1
        messages = captured.get(timeout=2)
        if isinstance(messages, Exception):
            raise messages
        typed = cast(
            tuple[TaskCommandResultEnvelope | TaskEventEnvelope, ...],
            messages,
        )
        assert tuple(message.message_type for message in typed) == expected_types
    finally:
        server.shutdown()
        thread.join(timeout=2)
    assert not thread.is_alive()


@pytest.mark.parametrize("terminal_command", ("task.cancel", "task.emergency_stop"))
def test_every_control_command_replays_over_real_websocket(terminal_command: str) -> None:
    captured: queue.Queue[object] = queue.Queue()
    plan = (
        ("task.offer", ("task.accept", "task.started", "step.started")),
        ("task.pause", ("task.control_ack", "task.paused")),
        ("task.resume", ("task.control_ack", "task.resumed")),
        (terminal_command, ("task.control_ack", "task.cancelled")),
    )

    def handler(connection: ServerConnection) -> None:
        try:
            parse_executor_message(connection.recv(timeout=2))
            observed: list[object] = []
            for sequence, (message_type, expected_types) in enumerate(plan, start=1):
                connection.send(command(message_type, sequence=sequence))
                batch = tuple(
                    parse_executor_message(connection.recv(timeout=2)) for _ in expected_types
                )
                if tuple(message.message_type for message in batch) != expected_types:
                    raise RuntimeError("Fake Executor emitted an unexpected control batch")
                observed.extend(batch)
            captured.put(tuple(observed))
        except Exception as error:
            captured.put(error)

    server, thread, port = run_server(handler)
    try:
        client = FakeExecutorClient(
            configuration=FakeExecutorClientConfiguration(
                websocket_url=f"ws://127.0.0.1:{port}/api/v1/executors/connect",
                session_token="private-session",
            ),
            engine=FakeExecutorEngine(
                installation_id=INSTALLATION_ID,
                executor_id=EXECUTOR_ID,
                scenario=FakeExecutorScenario.HOLD,
                clock=MutableClock(),
                id_source=DeterministicIds(),
            ),
        )

        assert client.run(max_commands=4) == 4
        messages = captured.get(timeout=2)
        if isinstance(messages, Exception):
            raise messages
        assert len(cast(tuple[object, ...], messages)) == 9
    finally:
        server.shutdown()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_fake_client_configuration_and_transport_fail_closed_without_secret() -> None:
    invalid_urls = (
        "",
        "http://127.0.0.1/api/v1/executors/connect",
        "ws:///api/v1/executors/connect",
        "ws://user@127.0.0.1/api/v1/executors/connect",
        "ws://127.0.0.1/wrong",
        "ws://127.0.0.1/api/v1/executors/connect?private=true",
        "ws://127.0.0.1/api/v1/executors/connect#private",
        "ws://127.0.0.1:not-a-port/api/v1/executors/connect",
        "wss://" + ("a" * 2048) + "/api/v1/executors/connect",
    )
    for url in invalid_urls:
        with pytest.raises(FakeExecutorTransportRejected):
            FakeExecutorClientConfiguration(
                websocket_url=url,
                session_token="private-session",
            )
    with pytest.raises(FakeExecutorTransportRejected):
        FakeExecutorClientConfiguration(
            websocket_url="ws://127.0.0.1/api/v1/executors/connect",
            session_token="private session",
        )
    for invalid_token in ("", "a" * 4097, cast(str, object())):
        with pytest.raises(FakeExecutorTransportRejected):
            FakeExecutorClientConfiguration(
                websocket_url="ws://127.0.0.1/api/v1/executors/connect",
                session_token=invalid_token,
            )
    with pytest.raises(FakeExecutorTransportRejected):
        FakeExecutorClientConfiguration(
            websocket_url=cast(str, object()),
            session_token="private-session",
        )

    configuration = FakeExecutorClientConfiguration(
        websocket_url="ws://127.0.0.1:9/api/v1/executors/connect",
        session_token="private-session",
    )
    assert "private-session" not in repr(configuration)
    client = FakeExecutorClient(
        configuration=configuration,
        engine=FakeExecutorEngine(
            installation_id=INSTALLATION_ID,
            executor_id=EXECUTOR_ID,
            scenario=FakeExecutorScenario.REJECT,
        ),
        open_timeout=timedelta(milliseconds=50),
    )
    with pytest.raises(
        FakeExecutorTransportRejected,
        match=r"^Fake Executor transport is unavailable$",
    ) as captured:
        client.run(max_commands=1)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert "private" not in str(captured.value)


def test_fake_client_constructor_and_wire_serialization_fail_closed() -> None:
    configuration = FakeExecutorClientConfiguration(
        websocket_url="wss://example.com/api/v1/executors/connect",
        session_token="private-session",
    )
    engine = FakeExecutorEngine(
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
        scenario=FakeExecutorScenario.REJECT,
    )
    with pytest.raises(FakeExecutorTransportRejected):
        FakeExecutorClient(
            configuration=cast(FakeExecutorClientConfiguration, object()),
            engine=engine,
        )
    with pytest.raises(FakeExecutorTransportRejected):
        FakeExecutorClient(
            configuration=configuration,
            engine=cast(FakeExecutorEngine, object()),
        )
    with pytest.raises(FakeExecutorTransportRejected):
        FakeExecutorClient(
            configuration=configuration,
            engine=engine,
            open_timeout=cast(timedelta, object()),
        )
    with pytest.raises(FakeExecutorTransportRejected):
        FakeExecutorClient(
            configuration=configuration,
            engine=engine,
            close_timeout=timedelta(0),
        )

    class BrokenWireModel:
        def model_dump(self, *, mode: str) -> object:
            raise RuntimeError(mode)

    with pytest.raises(FakeExecutorTransportRejected):
        _wire(cast(BaseModel, BrokenWireModel()))


def test_fake_client_rejects_binary_commands_and_invalid_run_limits() -> None:
    captured: queue.Queue[object] = queue.Queue()

    def handler(connection: ServerConnection) -> None:
        try:
            parse_executor_message(connection.recv(timeout=2))
            connection.send(b"private-binary-command")
            captured.put(True)
        except Exception as error:
            captured.put(error)

    server, thread, port = run_server(handler)
    try:
        client = FakeExecutorClient(
            configuration=FakeExecutorClientConfiguration(
                websocket_url=f"ws://127.0.0.1:{port}/api/v1/executors/connect",
                session_token="private-session",
            ),
            engine=FakeExecutorEngine(
                installation_id=INSTALLATION_ID,
                executor_id=EXECUTOR_ID,
                scenario=FakeExecutorScenario.HOLD,
            ),
        )
        with pytest.raises(FakeExecutorTransportRejected):
            client.run(max_commands=1)
        for invalid in (0, -1, True, 1001):
            with pytest.raises(FakeExecutorTransportRejected):
                client.run(max_commands=invalid)
        assert captured.get(timeout=2) is True
    finally:
        server.shutdown()
        thread.join(timeout=2)
