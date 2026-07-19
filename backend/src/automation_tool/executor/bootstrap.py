"""Strict one-shot stdin bootstrap for the packaged Local Executor."""

from __future__ import annotations

from typing import Annotated, BinaryIO, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator

from automation_tool.executor.authentication import (
    LocalSessionAuthenticationRejected,
    require_local_session_token,
)
from automation_tool.executor.transport import (
    ExecutorTransportRejected,
    parse_executor_websocket_url,
    require_executor_session_token,
)
from automation_tool.protocol import ProtocolExecutorId, ProtocolInstallationId
from automation_tool.protocol.json_object import decode_bounded_json_object

MAX_EXECUTOR_BOOTSTRAP_BYTES = 16 * 1024


class ExecutorBootstrapRejected(ValueError):
    """Bootstrap input is absent, malformed, unsafe, or unsupported."""

    def __init__(self) -> None:
        super().__init__("Local Executor bootstrap is rejected")


class ExecutorBootstrap(BaseModel):
    """The only configuration accepted from the Tauri parent over stdin."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    bootstrap_version: Literal["1"]
    websocket_url: Annotated[str, Field(min_length=1, max_length=2048)]
    local_session_token: SecretStr
    session_token: SecretStr
    installation_id: ProtocolInstallationId
    executor_id: ProtocolExecutorId
    heartbeat_interval_seconds: Annotated[int, Field(ge=1, le=60)]

    @field_validator("websocket_url")
    @classmethod
    def require_safe_endpoint(cls, value: str) -> str:
        try:
            parsed = parse_executor_websocket_url(value)
        except ExecutorTransportRejected:
            raise ValueError("invalid Executor endpoint") from None
        if parsed.scheme == "ws":
            if (
                parsed.hostname != "127.0.0.1"
                or parsed.port is None
                or not 1 <= parsed.port <= 65535
            ):
                raise ValueError("plain WebSocket must use loopback")
        elif parsed.port not in {None, 443}:
            raise ValueError("secure WebSocket must use the standard port")
        return value

    @field_validator("session_token", mode="before")
    @classmethod
    def require_safe_session(cls, value: object) -> object:
        try:
            return require_executor_session_token(value)
        except ExecutorTransportRejected:
            raise ValueError("invalid Executor session") from None

    @field_validator("local_session_token", mode="before")
    @classmethod
    def require_safe_local_session(cls, value: object) -> object:
        try:
            return require_local_session_token(value)
        except LocalSessionAuthenticationRejected:
            raise ValueError("invalid local Executor session") from None


def read_executor_bootstrap(stream: BinaryIO) -> ExecutorBootstrap:
    """Read exactly one newline-terminated bootstrap without consuming later stdin."""

    bootstrap: ExecutorBootstrap | None = None
    failed = False
    try:
        source = stream.readline(MAX_EXECUTOR_BOOTSTRAP_BYTES + 1)
        if (
            type(source) is not bytes
            or not source.endswith(b"\n")
            or len(source) > MAX_EXECUTOR_BOOTSTRAP_BYTES
        ):
            raise ValueError("invalid bootstrap framing")
        decoded = decode_bounded_json_object(
            source[:-1],
            maximum_bytes=MAX_EXECUTOR_BOOTSTRAP_BYTES,
        )
        bootstrap = ExecutorBootstrap.model_validate(decoded)
    except (AttributeError, OSError, TypeError, UnicodeError, ValueError, ValidationError):
        failed = True
    if failed or bootstrap is None:
        raise ExecutorBootstrapRejected
    return bootstrap


__all__ = [
    "MAX_EXECUTOR_BOOTSTRAP_BYTES",
    "ExecutorBootstrap",
    "ExecutorBootstrapRejected",
    "read_executor_bootstrap",
]
