from __future__ import annotations

import base64
import json
from io import BytesIO
from pathlib import Path
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
STATE_DIRECTORY = str((Path.cwd() / ".automation-tool-executor-test").resolve())
ACTION_PUBLIC_KEY = base64.urlsafe_b64encode(bytes(range(32))).rstrip(b"=").decode("ascii")


def bootstrap_source(**overrides: object) -> bytes:
    payload: dict[str, object] = {
        "bootstrap_version": "1",
        "websocket_url": "ws://127.0.0.1:8765/api/v1/executors/connect",
        "local_session_token": LOCAL_SESSION_TOKEN,
        "session_token": SESSION_TOKEN,
        "installation_id": INSTALLATION_ID,
        "executor_id": EXECUTOR_ID,
        "heartbeat_interval_seconds": 1,
        "state_directory": STATE_DIRECTORY,
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
    assert bootstrap.state_directory == STATE_DIRECTORY
    assert bootstrap.crash_recovery is False
    assert bootstrap.capture_successful_diagnostics is False
    assert stream.readline() == b"second-line-is-not-consumed\n"
    assert SESSION_TOKEN not in repr(bootstrap)
    assert SESSION_TOKEN not in bootstrap.model_dump_json()
    assert LOCAL_SESSION_TOKEN not in repr(bootstrap)
    assert LOCAL_SESSION_TOKEN not in bootstrap.model_dump_json()

    remote = read_executor_bootstrap(
        BytesIO(bootstrap_source(websocket_url="wss://demo.example.com/api/v1/executors/connect"))
    )
    assert remote.websocket_url == "wss://demo.example.com/api/v1/executors/connect"

    recovered = read_executor_bootstrap(BytesIO(bootstrap_source(crash_recovery=True)))
    assert recovered.crash_recovery is True
    diagnostics = read_executor_bootstrap(
        BytesIO(bootstrap_source(capture_successful_diagnostics=True))
    )
    assert diagnostics.capture_successful_diagnostics is True


def test_bootstrap_accepts_one_complete_trusted_action_runtime_configuration() -> None:
    bootstrap = read_executor_bootstrap(
        BytesIO(
            bootstrap_source(
                action_runtime={
                    "authorization_public_key": ACTION_PUBLIC_KEY,
                    "minimum_interval_seconds": 30,
                    "task_action_limit": 20,
                }
            )
        )
    )

    assert bootstrap.action_runtime is not None
    assert bootstrap.action_runtime.authorization_public_key_bytes() == bytes(range(32))
    assert bootstrap.action_runtime.minimum_interval_seconds == 30
    assert bootstrap.action_runtime.task_action_limit == 20


@pytest.mark.parametrize(
    "action_runtime",
    (
        {},
        {"authorization_public_key": ACTION_PUBLIC_KEY},
        {
            "authorization_public_key": ACTION_PUBLIC_KEY,
            "minimum_interval_seconds": 30,
            "task_action_limit": 20,
            "extra": True,
        },
        {
            "authorization_public_key": ACTION_PUBLIC_KEY + "=",
            "minimum_interval_seconds": 30,
            "task_action_limit": 20,
        },
        {
            "authorization_public_key": "A" * 42,
            "minimum_interval_seconds": 30,
            "task_action_limit": 20,
        },
        {
            "authorization_public_key": "!" + ("A" * 42),
            "minimum_interval_seconds": 30,
            "task_action_limit": 20,
        },
        {
            "authorization_public_key": base64.urlsafe_b64encode(bytes(32))
            .rstrip(b"=")
            .decode("ascii"),
            "minimum_interval_seconds": 30,
            "task_action_limit": 20,
        },
        {
            "authorization_public_key": ACTION_PUBLIC_KEY,
            "minimum_interval_seconds": 0,
            "task_action_limit": 20,
        },
        {
            "authorization_public_key": ACTION_PUBLIC_KEY,
            "minimum_interval_seconds": 3601,
            "task_action_limit": 20,
        },
        {
            "authorization_public_key": ACTION_PUBLIC_KEY,
            "minimum_interval_seconds": 30,
            "task_action_limit": 0,
        },
        {
            "authorization_public_key": ACTION_PUBLIC_KEY,
            "minimum_interval_seconds": 30,
            "task_action_limit": 101,
        },
    ),
)
def test_bootstrap_rejects_partial_or_invalid_action_runtime_configuration(
    action_runtime: object,
) -> None:
    with pytest.raises(ExecutorBootstrapRejected):
        read_executor_bootstrap(BytesIO(bootstrap_source(action_runtime=action_runtime)))


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
        bootstrap_source(crash_recovery=cast(bool, 1)),
        bootstrap_source(crash_recovery="true"),
        bootstrap_source(capture_successful_diagnostics=cast(bool, 1)),
        bootstrap_source(capture_successful_diagnostics="true"),
        bootstrap_source(state_directory="relative/executor-state"),
        bootstrap_source(state_directory="/"),
        bootstrap_source(state_directory="/tmp/../private-state"),
        bootstrap_source(state_directory="/tmp/private\nstate"),
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
