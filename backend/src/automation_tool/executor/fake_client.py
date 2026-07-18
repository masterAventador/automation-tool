"""Real WebSocket transport for the no-side-effect FakeExecutor."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import timedelta
from urllib.parse import urlsplit

from pydantic import BaseModel
from websockets.sync.client import connect
from websockets.typing import Subprotocol

from automation_tool.executor.fake import FakeExecutorEngine
from automation_tool.protocol import (
    EXECUTOR_WEBSOCKET_SUBPROTOCOL,
    MAX_EXECUTOR_MESSAGE_BYTES,
)

_EXECUTOR_PATH = "/api/v1/executors/connect"


class FakeExecutorTransportRejected(ConnectionError):
    """The fake transport cannot make safe progress without leaking details."""

    def __init__(self) -> None:
        super().__init__("Fake Executor transport is unavailable")


@dataclass(frozen=True, slots=True)
class FakeExecutorClientConfiguration:
    websocket_url: str
    session_token: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.websocket_url) is not str
            or not 1 <= len(self.websocket_url) <= 2048
            or type(self.session_token) is not str
            or not 1 <= len(self.session_token) <= 4096
            or any(character.isspace() for character in self.session_token)
        ):
            raise FakeExecutorTransportRejected
        try:
            parsed = urlsplit(self.websocket_url)
            _ = parsed.port
        except ValueError:
            raise FakeExecutorTransportRejected from None
        if (
            parsed.scheme not in {"ws", "wss"}
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.path != _EXECUTOR_PATH
            or parsed.query
            or parsed.fragment
        ):
            raise FakeExecutorTransportRejected


def _positive_seconds(value: object) -> float:
    if not isinstance(value, timedelta):
        raise FakeExecutorTransportRejected
    seconds = value.total_seconds()
    if seconds <= 0:
        raise FakeExecutorTransportRejected
    return seconds


def _wire(message: BaseModel) -> str:
    try:
        dumped = message.model_dump(mode="json")
        return json.dumps(
            dumped,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except Exception:
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

    def run(self, *, max_commands: int) -> int:
        if type(max_commands) is not int or not 1 <= max_commands <= 1000:
            raise FakeExecutorTransportRejected
        processed = 0
        failed = False
        try:
            with connect(
                self._configuration.websocket_url,
                subprotocols=[Subprotocol(EXECUTOR_WEBSOCKET_SUBPROTOCOL)],
                additional_headers={
                    "Authorization": f"Bearer {self._configuration.session_token}",
                },
                compression=None,
                proxy=None,
                open_timeout=self._open_timeout,
                close_timeout=self._close_timeout,
                max_size=MAX_EXECUTOR_MESSAGE_BYTES,
            ) as websocket:
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


__all__ = [
    "FakeExecutorClient",
    "FakeExecutorClientConfiguration",
    "FakeExecutorTransportRejected",
]
