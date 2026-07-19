"""Shared authenticated WebSocket transport primitives for Executor clients."""

from __future__ import annotations

import json
from datetime import timedelta
from urllib.parse import SplitResult, urlsplit

from pydantic import BaseModel
from websockets.sync.client import ClientConnection, connect
from websockets.typing import Subprotocol

from automation_tool.protocol import (
    EXECUTOR_WEBSOCKET_SUBPROTOCOL,
    MAX_EXECUTOR_MESSAGE_BYTES,
)

EXECUTOR_WEBSOCKET_PATH = "/api/v1/executors/connect"


class ExecutorTransportRejected(ConnectionError):
    """A transport value or operation cannot be used safely."""

    def __init__(self) -> None:
        super().__init__("Executor transport is unavailable")


def parse_executor_websocket_url(value: object) -> SplitResult:
    """Validate the common structural requirements for the Executor endpoint."""

    if type(value) is not str or not 1 <= len(value) <= 2048:
        raise ExecutorTransportRejected
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        raise ExecutorTransportRejected from None
    if (
        parsed.scheme not in {"ws", "wss"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != EXECUTOR_WEBSOCKET_PATH
        or parsed.query
        or parsed.fragment
    ):
        raise ExecutorTransportRejected
    return parsed


def require_executor_session_token(value: object) -> str:
    """Return one bounded bearer without retaining it in an error."""

    if (
        type(value) is not str
        or not 1 <= len(value) <= 4096
        or any(character.isspace() for character in value)
    ):
        raise ExecutorTransportRejected
    return value


def positive_seconds(value: object) -> float:
    if not isinstance(value, timedelta):
        raise ExecutorTransportRejected
    seconds = value.total_seconds()
    if seconds <= 0:
        raise ExecutorTransportRejected
    return seconds


def serialize_executor_message(message: BaseModel) -> str:
    try:
        dumped = message.model_dump(mode="json")
        return json.dumps(
            dumped,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except Exception:
        raise ExecutorTransportRejected from None


def connect_executor_websocket(
    *,
    websocket_url: str,
    session_token: str,
    open_timeout: timedelta,
    close_timeout: timedelta,
) -> ClientConnection:
    """Open the exact production subprotocol without proxying or compression."""

    parse_executor_websocket_url(websocket_url)
    token = require_executor_session_token(session_token)
    return connect(
        websocket_url,
        subprotocols=[Subprotocol(EXECUTOR_WEBSOCKET_SUBPROTOCOL)],
        additional_headers={"Authorization": f"Bearer {token}"},
        compression=None,
        proxy=None,
        open_timeout=positive_seconds(open_timeout),
        close_timeout=positive_seconds(close_timeout),
        max_size=MAX_EXECUTOR_MESSAGE_BYTES,
    )


__all__ = [
    "EXECUTOR_WEBSOCKET_PATH",
    "ExecutorTransportRejected",
    "connect_executor_websocket",
    "parse_executor_websocket_url",
    "positive_seconds",
    "require_executor_session_token",
    "serialize_executor_message",
]
