from __future__ import annotations

import json
from io import BytesIO
from typing import cast

import pytest
from pydantic import SecretStr

from automation_tool.executor.bootstrap import (
    MAX_EXECUTOR_BOOTSTRAP_BYTES,
    ExecutorBootstrapRejected,
    read_executor_bootstrap,
)

INSTALLATION_ID = "123e4567-e89b-42d3-a456-426614174003"
EXECUTOR_ID = "123e4567-e89b-42d3-a456-426614174004"
SESSION_TOKEN = "atds1.private-session-material"
LOCAL_SESSION_TOKEN = "01" * 32


def bootstrap_source(**overrides: object) -> bytes:
    payload: dict[str, object] = {
        "bootstrap_version": "1",
        "websocket_url": "ws://127.0.0.1:8765/api/v1/executors/connect",
        "local_session_token": LOCAL_SESSION_TOKEN,
        "session_token": SESSION_TOKEN,
        "installation_id": INSTALLATION_ID,
        "executor_id": EXECUTOR_ID,
        "heartbeat_interval_seconds": 1,
    }
    payload.update(overrides)
    return (json.dumps(payload, separators=(",", ":")) + "\n").encode()


def test_bootstrap_reads_one_bounded_line_and_keeps_the_session_secret() -> None:
    stream = BytesIO(bootstrap_source() + b"second-line-is-not-consumed\n")

    bootstrap = read_executor_bootstrap(stream)

    assert bootstrap.bootstrap_version == "1"
    assert bootstrap.websocket_url == "ws://127.0.0.1:8765/api/v1/executors/connect"
    assert isinstance(bootstrap.local_session_token, SecretStr)
    assert bootstrap.local_session_token.get_secret_value() == LOCAL_SESSION_TOKEN
    assert isinstance(bootstrap.session_token, SecretStr)
    assert bootstrap.session_token.get_secret_value() == SESSION_TOKEN
    assert str(bootstrap.installation_id) == INSTALLATION_ID
    assert str(bootstrap.executor_id) == EXECUTOR_ID
    assert bootstrap.heartbeat_interval_seconds == 1
    assert stream.readline() == b"second-line-is-not-consumed\n"
    assert SESSION_TOKEN not in repr(bootstrap)
    assert SESSION_TOKEN not in bootstrap.model_dump_json()
    assert LOCAL_SESSION_TOKEN not in repr(bootstrap)
    assert LOCAL_SESSION_TOKEN not in bootstrap.model_dump_json()

    remote = read_executor_bootstrap(
        BytesIO(bootstrap_source(websocket_url="wss://demo.example.com/api/v1/executors/connect"))
    )
    assert remote.websocket_url == "wss://demo.example.com/api/v1/executors/connect"


@pytest.mark.parametrize(
    "source",
    (
        b"",
        b"{}",
        b"{}\n",
        b"[]\n",
        b"not-json\n",
        b"\xff\n",
        b'{"bootstrap_version":"1","bootstrap_version":"1"}\n',
        bootstrap_source(bootstrap_version="2"),
        bootstrap_source(extra="forbidden"),
        bootstrap_source(websocket_url="http://127.0.0.1:8765/api/v1/executors/connect"),
        bootstrap_source(websocket_url="ws://example.com/api/v1/executors/connect"),
        bootstrap_source(websocket_url="ws://localhost:8765/api/v1/executors/connect"),
        bootstrap_source(websocket_url="ws://127.0.0.1:0/api/v1/executors/connect"),
        bootstrap_source(websocket_url="wss://demo.example.com:8443/api/v1/executors/connect"),
        bootstrap_source(websocket_url="wss://user@demo.example.com/api/v1/executors/connect"),
        bootstrap_source(websocket_url="wss://demo.example.com/wrong"),
        bootstrap_source(websocket_url="wss://demo.example.com/api/v1/executors/connect?q=1"),
        bootstrap_source(session_token=""),
        bootstrap_source(session_token="private token"),
        bootstrap_source(session_token="x" * 4097),
        bootstrap_source(local_session_token="0" * 63),
        bootstrap_source(local_session_token="A" * 64),
        bootstrap_source(local_session_token="g" * 64),
        bootstrap_source(installation_id="not-a-uuid"),
        bootstrap_source(executor_id="123e4567-e89b-12d3-a456-426614174004"),
        bootstrap_source(heartbeat_interval_seconds=cast(int, True)),
        bootstrap_source(heartbeat_interval_seconds=0),
        bootstrap_source(heartbeat_interval_seconds=61),
    ),
)
def test_bootstrap_rejects_malformed_or_unsafe_input_without_reflection(source: bytes) -> None:
    with pytest.raises(
        ExecutorBootstrapRejected,
        match=r"^Local Executor bootstrap is rejected$",
    ) as captured:
        read_executor_bootstrap(BytesIO(source))

    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert "private" not in str(captured.value).lower()


def test_bootstrap_rejects_bytes_over_the_hard_limit() -> None:
    source = b"{" + b"x" * MAX_EXECUTOR_BOOTSTRAP_BYTES + b"}\n"

    with pytest.raises(ExecutorBootstrapRejected):
        read_executor_bootstrap(BytesIO(source))
