"""Authenticated local platform commands accepted only from the Tauri parent."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path
from queue import Queue
from typing import Annotated, BinaryIO, Literal, Protocol, TextIO, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from automation_tool.executor.authentication import (
    LocalSessionAuthenticationRejected,
    LocalSessionAuthenticator,
)
from automation_tool.executor.browser_runtime import (
    BrowserLaunchRequest,
    BrowserRuntime,
    BrowserWindow,
)
from automation_tool.executor.rpa.douyin.login import (
    DouyinQrLoginFlow,
    DouyinQrLoginState,
)
from automation_tool.protocol import (
    EXECUTOR_PROTOCOL_VERSION,
    MAX_EXECUTOR_SEQUENCE,
    MessageId,
    PlatformSessionHealthEnvelope,
)
from automation_tool.protocol.json_object import decode_bounded_json_object
from automation_tool.protocol.safe_text import contains_control_or_bidi

MAX_PLATFORM_COMMAND_BYTES = 16 * 1024
DOUYIN_QR_LOGIN_FLOW_VERSION = "douyin.qr-login.v2"


class PlatformCommandRejected(ValueError):
    def __init__(self) -> None:
        super().__init__("Local platform command is rejected")


class PlatformCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    authentication_proof: Annotated[
        str, Field(alias="authenticationProof", min_length=50, max_length=50)
    ]
    command_id: MessageId = Field(alias="commandId")
    command_type: Literal["douyin.login.open", "douyin.login.recheck"] = Field(alias="commandType")
    executable_path: Annotated[str, Field(min_length=1, max_length=4096)] = Field(
        alias="executablePath"
    )
    headless: bool
    profile_directory: Annotated[str, Field(min_length=1, max_length=4096)] = Field(
        alias="profileDirectory"
    )
    protocol_version: Literal["1.0"] = Field(alias="protocolVersion")

    @field_validator("executable_path", "profile_directory")
    @classmethod
    def require_safe_absolute_path_shape(cls, value: str) -> str:
        path = Path(value)
        if (
            contains_control_or_bidi(value)
            or not path.is_absolute()
            or path.parent == path
            or any(part in {".", ".."} for part in path.parts)
        ):
            raise ValueError("invalid local path")
        return value

    def __repr__(self) -> str:
        return "PlatformCommand(<redacted>)"


@runtime_checkable
class PlatformCommandOperation(Protocol):
    def handle(self, command: PlatformCommand) -> str: ...

    def close(self) -> None: ...


class _BinaryLineReader(Protocol):
    def readline(self, limit: int = -1) -> bytes: ...


class PlatformCommandWorker:
    def __init__(
        self,
        *,
        input_stream: BinaryIO,
        authenticator: LocalSessionAuthenticator,
        operation: PlatformCommandOperation,
        result_output: TextIO | None = None,
        result_writer: Callable[..., None] | None = None,
    ) -> None:
        if (
            not hasattr(input_stream, "readline")
            or not isinstance(authenticator, LocalSessionAuthenticator)
            or not isinstance(operation, PlatformCommandOperation)
            or (result_output is None) == (result_writer is None)
            or (result_output is not None and not hasattr(result_output, "write"))
            or (result_writer is not None and not callable(result_writer))
        ):
            raise PlatformCommandRejected
        self._input = input_stream
        self._authenticator = authenticator
        self._operation = operation
        self._result_output = result_output
        self._result_writer = result_writer

    def run(self, stop: threading.Event) -> None:
        if not isinstance(stop, threading.Event):
            raise PlatformCommandRejected
        try:
            while not stop.is_set():
                source = self._input.readline(MAX_PLATFORM_COMMAND_BYTES + 1)
                if source == b"":
                    break
                command = read_platform_command(_SingleLineStream(source), self._authenticator)
                state = self._operation.handle(command)
                if self._result_writer is not None:
                    self._result_writer(command_id=str(command.command_id), state=state)
                else:
                    write_platform_command_result(
                        self._result_output,  # type: ignore[arg-type]
                        self._authenticator,
                        command_id=str(command.command_id),
                        state=state,
                    )
        except PlatformCommandRejected:
            raise
        except Exception:
            raise PlatformCommandRejected from None
        finally:
            try:
                self._operation.close()
            except Exception:
                if not stop.is_set():
                    raise PlatformCommandRejected from None


class _HealthReporter(Protocol):
    def observe(
        self,
        window: BrowserWindow,
        *,
        sequence: int,
        recovered: bool,
    ) -> PlatformSessionHealthEnvelope: ...


class _LoginFlow(Protocol):
    def begin(self) -> object: ...

    def recheck(self) -> object: ...

    def active_window(self) -> BrowserWindow: ...

    def close(self) -> None: ...


class _Runtime(Protocol):
    def start(self, request: BrowserLaunchRequest) -> None: ...

    def close(self) -> None: ...


class DouyinLoginCommandOperation:
    """Own one thread-confined QR flow and queue its non-sensitive health facts."""

    def __init__(
        self,
        *,
        health_reporter: _HealthReporter,
        outbound: Queue[object],
        runtime_factory: Callable[[], _Runtime] = BrowserRuntime,
        flow_factory: Callable[[_Runtime], _LoginFlow] = DouyinQrLoginFlow,  # type: ignore[assignment]
        sequence_source: Callable[[], int] | None = None,
    ) -> None:
        if (
            not hasattr(health_reporter, "observe")
            or not isinstance(outbound, Queue)
            or not callable(runtime_factory)
            or not callable(flow_factory)
            or (sequence_source is not None and not callable(sequence_source))
        ):
            raise PlatformCommandRejected
        self._health_reporter = health_reporter
        self._outbound = outbound
        self._runtime_factory = runtime_factory
        self._flow_factory = flow_factory
        self._sequence_source = sequence_source or self._next_wall_sequence
        self._last_sequence = 0
        self._runtime: _Runtime | None = None
        self._flow: _LoginFlow | None = None
        self._launch_identity: tuple[str, str, bool] | None = None

    def handle(self, command: PlatformCommand) -> str:
        if not isinstance(command, PlatformCommand):
            raise PlatformCommandRejected
        identity = (
            command.executable_path,
            command.profile_directory,
            command.headless,
        )
        try:
            if command.command_type == "douyin.login.open":
                self._close_active()
                self._begin(command, identity)
                flow = self._flow
                if flow is None:
                    raise ValueError
                observation = flow.begin()
            elif command.command_type == "douyin.login.recheck":
                if self._flow is None:
                    self._begin(command, identity)
                    flow = self._flow
                    if flow is None:
                        raise ValueError
                    observation = flow.begin()
                else:
                    if identity != self._launch_identity:
                        raise ValueError
                    observation = self._flow.recheck()
            else:
                raise ValueError
            state: object = getattr(getattr(observation, "state", None), "value", None)
            if type(state) is not str or state not in {
                "login_required",
                "awaiting_scan",
                "awaiting_confirmation",
                "qr_expired",
                "healthy",
                "handoff_required",
                "unknown",
            }:
                raise ValueError
            sequence = self._sequence_source()
            if (
                type(sequence) is not int
                or not 1 <= sequence <= MAX_EXECUTOR_SEQUENCE
                or sequence <= self._last_sequence
            ):
                raise ValueError
            self._last_sequence = sequence
            flow = self._flow
            if flow is None:
                raise ValueError
            message = self._health_reporter.observe(
                flow.active_window(),
                sequence=sequence,
                recovered=state == DouyinQrLoginState.HEALTHY.value,
            )
            self._outbound.put(message)
            if state == DouyinQrLoginState.HEALTHY.value:
                self._close_active()
            return state
        except PlatformCommandRejected:
            raise
        except Exception:
            self._close_active(best_effort=True)
            raise PlatformCommandRejected from None

    def _begin(
        self,
        command: PlatformCommand,
        identity: tuple[str, str, bool],
    ) -> None:
        runtime = self._runtime_factory()
        runtime.start(
            BrowserLaunchRequest(
                executable_path=Path(command.executable_path),
                profile_directory=Path(command.profile_directory),
                headless=command.headless,
            )
        )
        try:
            flow = self._flow_factory(runtime)
        except Exception:
            runtime.close()
            raise
        self._runtime = runtime
        self._flow = flow
        self._launch_identity = identity

    def _next_wall_sequence(self) -> int:
        return max(self._last_sequence + 1, time.time_ns() // 1_000)

    def _close_active(self, *, best_effort: bool = False) -> None:
        flow = self._flow
        runtime = self._runtime
        self._flow = None
        self._runtime = None
        self._launch_identity = None
        failed = False
        if flow is not None:
            try:
                flow.close()
            except Exception:
                failed = True
        if runtime is not None:
            try:
                runtime.close()
            except Exception:
                failed = True
        if failed and not best_effort:
            raise PlatformCommandRejected

    def close(self) -> None:
        self._close_active()


class _SingleLineStream:
    def __init__(self, source: bytes) -> None:
        self._source = source

    def readline(self, _limit: int = -1) -> bytes:
        source = self._source
        self._source = b""
        return source


def read_platform_command(
    stream: _BinaryLineReader,
    authenticator: LocalSessionAuthenticator,
) -> PlatformCommand:
    try:
        if not isinstance(authenticator, LocalSessionAuthenticator):
            raise ValueError
        source = stream.readline(MAX_PLATFORM_COMMAND_BYTES + 1)
        if (
            type(source) is not bytes
            or not source.endswith(b"\n")
            or len(source) > MAX_PLATFORM_COMMAND_BYTES
        ):
            raise ValueError
        decoded = decode_bounded_json_object(source[:-1], maximum_bytes=MAX_PLATFORM_COMMAND_BYTES)
        command = PlatformCommand.model_validate(decoded)
        authenticator.verify_command(
            command_id=str(command.command_id),
            command_type=command.command_type,
            executable_path=command.executable_path,
            profile_directory=command.profile_directory,
            headless=command.headless,
            presented_proof=command.authentication_proof,
        )
        return command
    except (
        AttributeError,
        LocalSessionAuthenticationRejected,
        OSError,
        TypeError,
        UnicodeError,
        ValueError,
        ValidationError,
    ):
        raise PlatformCommandRejected from None


def write_platform_command_result(
    output: TextIO,
    authenticator: LocalSessionAuthenticator,
    *,
    command_id: str,
    state: str,
) -> None:
    import json

    try:
        proof = authenticator.proof_for_command_result(
            command_id=command_id,
            state=state,
        )
        source = json.dumps(
            {
                "authenticationProof": proof,
                "commandId": command_id,
                "event": "platform.command.completed",
                "flowVersion": DOUYIN_QR_LOGIN_FLOW_VERSION,
                "platform": "douyin",
                "protocolVersion": EXECUTOR_PROTOCOL_VERSION,
                "state": state,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        if len(source.encode("utf-8")) > 4096:
            raise ValueError
        output.write(source + "\n")
        output.flush()
    except Exception:
        raise PlatformCommandRejected from None


__all__ = [
    "DOUYIN_QR_LOGIN_FLOW_VERSION",
    "MAX_PLATFORM_COMMAND_BYTES",
    "DouyinLoginCommandOperation",
    "PlatformCommand",
    "PlatformCommandOperation",
    "PlatformCommandRejected",
    "PlatformCommandWorker",
    "read_platform_command",
    "write_platform_command_result",
]
