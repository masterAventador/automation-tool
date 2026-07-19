"""Real WebSocket transport for the no-side-effect FakeExecutor."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import timedelta

from pydantic import BaseModel
from websockets.sync.client import ClientConnection

from automation_tool.executor.fake import FakeExecutorEngine
from automation_tool.executor.transport import (
    ExecutorTransportRejected,
    connect_executor_websocket,
    parse_executor_websocket_url,
    positive_seconds,
    require_executor_session_token,
    serialize_executor_message,
)
from automation_tool.protocol import (
    TaskCommandEnvelope,
    parse_executor_message,
)


class FakeExecutorTransportRejected(ConnectionError):
    """The fake transport cannot make safe progress without leaking details."""

    def __init__(self) -> None:
        super().__init__("Fake Executor transport is unavailable")


@dataclass(frozen=True, slots=True)
class FakeExecutorClientConfiguration:
    websocket_url: str
    session_token: str = field(repr=False)

    def __post_init__(self) -> None:
        try:
            parse_executor_websocket_url(self.websocket_url)
            require_executor_session_token(self.session_token)
        except ExecutorTransportRejected:
            raise FakeExecutorTransportRejected from None


def _positive_seconds(value: object) -> float:
    try:
        return positive_seconds(value)
    except ExecutorTransportRejected:
        raise FakeExecutorTransportRejected from None


def _wire(message: BaseModel) -> str:
    try:
        return serialize_executor_message(message)
    except ExecutorTransportRejected:
        raise FakeExecutorTransportRejected from None


class FakeExecutorClient:
    """Connect through the production subprotocol and replay a bounded command count."""

    def __init__(
        self,
        *,
        configuration: FakeExecutorClientConfiguration,
        engine: FakeExecutorEngine,
        open_timeout: timedelta = timedelta(seconds=5),
        close_timeout: timedelta = timedelta(seconds=2),
    ) -> None:
        if not isinstance(configuration, FakeExecutorClientConfiguration) or not isinstance(
            engine, FakeExecutorEngine
        ):
            raise FakeExecutorTransportRejected
        self._configuration = configuration
        self._engine = engine
        self._open_timeout = _positive_seconds(open_timeout)
        self._close_timeout = _positive_seconds(close_timeout)

    def _connect(self) -> ClientConnection:
        return connect_executor_websocket(
            websocket_url=self._configuration.websocket_url,
            session_token=self._configuration.session_token,
            open_timeout=timedelta(seconds=self._open_timeout),
            close_timeout=timedelta(seconds=self._close_timeout),
        )

    def run(self, *, max_commands: int) -> int:
        if type(max_commands) is not int or not 1 <= max_commands <= 1000:
            raise FakeExecutorTransportRejected
        processed = 0
        failed = False
        try:
            with self._connect() as websocket:
                websocket.send(_wire(self._engine.build_hello()))
                while processed < max_commands:
                    source = websocket.recv()
                    if type(source) is not str:
                        raise FakeExecutorTransportRejected
                    messages = self._engine.handle(source)
                    for message in messages:
                        websocket.send(_wire(message))
                    processed += 1
        except Exception:
            failed = True
        if failed:
            raise FakeExecutorTransportRejected
        return processed

    def run_reconnecting(
        self,
        *,
        max_commands: int,
        max_reconnects: int,
        reconnect_delay: timedelta = timedelta(milliseconds=100),
    ) -> int:
        """Replay through bounded reconnects, counting each stable command once."""

        if (
            type(max_commands) is not int
            or not 1 <= max_commands <= 1000
            or type(max_reconnects) is not int
            or not 1 <= max_reconnects <= 1000
        ):
            raise FakeExecutorTransportRejected
        delay_seconds = _positive_seconds(reconnect_delay)
        processed_message_ids: set[str] = set()
        reconnects = 0
        failed = False
        while len(processed_message_ids) < max_commands:
            try:
                with self._connect() as websocket:
                    websocket.send(_wire(self._engine.build_hello(sequence=reconnects + 1)))
                    while len(processed_message_ids) < max_commands:
                        source = websocket.recv()
                        if type(source) is not str:
                            raise FakeExecutorTransportRejected
                        parsed = parse_executor_message(source)
                        if not isinstance(parsed, TaskCommandEnvelope):
                            raise FakeExecutorTransportRejected
                        messages = self._engine.handle(source)
                        for message in messages:
                            websocket.send(_wire(message))
                        processed_message_ids.add(str(parsed.message_id))
            except Exception:
                if reconnects >= max_reconnects:
                    failed = True
                    break
                reconnects += 1
                time.sleep(delay_seconds)
        if failed:
            raise FakeExecutorTransportRejected
        return len(processed_message_ids)


__all__ = [
    "FakeExecutorClient",
    "FakeExecutorClientConfiguration",
    "FakeExecutorTransportRejected",
]
