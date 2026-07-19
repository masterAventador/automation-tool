"""Minimal Local Executor process lifecycle and formal health traffic."""

from __future__ import annotations

import json
import platform as host_platform
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import TextIOBase
from typing import Literal, Protocol, TextIO, runtime_checkable
from uuid import RFC_4122, UUID, uuid4

from websockets.sync.client import ClientConnection

from automation_tool import __version__
from automation_tool.executor.authentication import LocalSessionAuthenticator
from automation_tool.executor.bootstrap import ExecutorBootstrap
from automation_tool.executor.command_processor import (
    ExecutorCommandProcessor,
    ExecutorOutboundMessage,
)
from automation_tool.executor.transport import (
    ExecutorTransportRejected,
    connect_executor_websocket,
    positive_seconds,
    serialize_executor_message,
)
from automation_tool.protocol import EXECUTOR_PROTOCOL_VERSION, ExecutorLifecycleEnvelope

_VERSION_PATTERN = re.compile(
    r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?"
    r"(?:\+[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?$"
)
_MESSAGE_DEADLINE = timedelta(seconds=30)


class ExecutorProcessRejected(ConnectionError):
    """The Local Executor cannot start or continue without weakening its boundary."""

    def __init__(self) -> None:
        super().__init__("Local Executor process is unavailable")


@dataclass(frozen=True, slots=True)
class RuntimeMetadata:
    executor_version: str
    platform: Literal["macos", "windows"]
    architecture: Literal["arm64", "x86_64"]

    def __post_init__(self) -> None:
        if (
            type(self.executor_version) is not str
            or _VERSION_PATTERN.fullmatch(self.executor_version) is None
            or self.platform not in {"macos", "windows"}
            or self.architecture not in {"arm64", "x86_64"}
        ):
            raise ExecutorProcessRejected

    @classmethod
    def detect(
        cls,
        *,
        system_name: str | None = None,
        machine_name: str | None = None,
    ) -> RuntimeMetadata:
        system = host_platform.system() if system_name is None else system_name
        machine = host_platform.machine() if machine_name is None else machine_name
        if system == "Darwin":
            normalized_platform: Literal["macos", "windows"] = "macos"
        elif system == "Windows":
            normalized_platform = "windows"
        else:
            raise ExecutorProcessRejected
        if machine in {"arm64", "aarch64"}:
            normalized_architecture: Literal["arm64", "x86_64"] = "arm64"
        elif machine in {"AMD64", "x86_64"}:
            normalized_architecture = "x86_64"
        else:
            raise ExecutorProcessRejected
        return cls(
            executor_version=__version__,
            platform=normalized_platform,
            architecture=normalized_architecture,
        )

    @staticmethod
    def now() -> datetime:
        return datetime.now(UTC)


class ExecutorProcessReporter:
    """Write a tiny fixed stdout protocol consumed by the future Rust manager."""

    def __init__(self, output: TextIO, authenticator: LocalSessionAuthenticator) -> None:
        if not isinstance(output, TextIOBase) or not isinstance(
            authenticator, LocalSessionAuthenticator
        ):
            raise ExecutorProcessRejected
        self._output = output
        self._authenticator = authenticator

    def _write(self, event: str) -> None:
        failed = False
        try:
            source = json.dumps(
                {
                    "authenticationProof": self._authenticator.proof_for(event),
                    "event": event,
                    "protocolVersion": EXECUTOR_PROTOCOL_VERSION,
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            binary_output = getattr(self._output, "buffer", None)
            if binary_output is not None:
                binary_output.write((source + "\n").encode("utf-8"))
                binary_output.flush()
            else:
                self._output.write(source + "\n")
                self._output.flush()
        except Exception:
            failed = True
        if failed:
            raise ExecutorProcessRejected

    def healthy(self) -> None:
        self._write("executor.healthy")

    def stopped(self) -> None:
        self._write("executor.stopped")


@runtime_checkable
class ExecutorClock(Protocol):
    def now(self) -> datetime: ...


def _positive_duration(value: object) -> timedelta:
    if not isinstance(value, timedelta):
        raise ExecutorProcessRejected
    failed = False
    try:
        positive_seconds(value)
    except ExecutorTransportRejected:
        failed = True
    if failed:
        raise ExecutorProcessRejected
    return value


class LocalExecutorProcess:
    """Connect, replay durable outcomes, and consume no-side-effect commands."""

    def __init__(
        self,
        *,
        bootstrap: ExecutorBootstrap,
        metadata: RuntimeMetadata,
        reporter: ExecutorProcessReporter,
        command_processor: ExecutorCommandProcessor,
        clock: ExecutorClock | None = None,
        id_source: Callable[[], object] = uuid4,
        open_timeout: timedelta = timedelta(seconds=5),
        close_timeout: timedelta = timedelta(seconds=2),
    ) -> None:
        resolved_clock = metadata if clock is None else clock
        if (
            not isinstance(bootstrap, ExecutorBootstrap)
            or not isinstance(metadata, RuntimeMetadata)
            or not isinstance(reporter, ExecutorProcessReporter)
            or not isinstance(command_processor, ExecutorCommandProcessor)
            or not isinstance(resolved_clock, ExecutorClock)
            or not callable(id_source)
        ):
            raise ExecutorProcessRejected
        self._bootstrap = bootstrap
        self._metadata = metadata
        self._reporter = reporter
        self._command_processor = command_processor
        self._clock = resolved_clock
        self._id_source = id_source
        self._open_timeout = _positive_duration(open_timeout)
        self._close_timeout = _positive_duration(close_timeout)

    @property
    def bootstrap(self) -> ExecutorBootstrap:
        return self._bootstrap

    @property
    def metadata(self) -> RuntimeMetadata:
        return self._metadata

    def _now(self) -> datetime:
        try:
            value = self._clock.now()
            if not isinstance(value, datetime) or value.utcoffset() is None:
                raise ValueError
            return value.astimezone(UTC)
        except Exception:
            raise ExecutorProcessRejected from None

    def _new_id(self) -> str:
        try:
            value = self._id_source()
            if not isinstance(value, UUID) or value.version != 4 or value.variant != RFC_4122:
                raise ValueError
            return str(value)
        except Exception:
            raise ExecutorProcessRejected from None

    def _lifecycle(self, *, message_type: str, sequence: int) -> ExecutorLifecycleEnvelope:
        now = self._now()
        payload = (
            {
                "architecture": self._metadata.architecture,
                "executor_version": self._metadata.executor_version,
                "platform": self._metadata.platform,
            }
            if message_type == "executor.hello"
            else {}
        )
        try:
            return ExecutorLifecycleEnvelope.model_validate(
                {
                    "protocol_version": EXECUTOR_PROTOCOL_VERSION,
                    "message_id": self._new_id(),
                    "message_type": message_type,
                    "sent_at": now,
                    "deadline_at": now + _MESSAGE_DEADLINE,
                    "installation_id": str(self._bootstrap.installation_id),
                    "executor_id": str(self._bootstrap.executor_id),
                    "correlation_id": self._new_id(),
                    "idempotency_key": (
                        f"executor:hello:{self._bootstrap.executor_id}"
                        if message_type == "executor.hello"
                        else f"executor:heartbeat:{self._bootstrap.executor_id}:{sequence}"
                    ),
                    "sequence": sequence,
                    "payload": payload,
                }
            )
        except Exception:
            raise ExecutorProcessRejected from None

    def _send_outbox(
        self,
        websocket: ClientConnection,
        messages: tuple[ExecutorOutboundMessage, ...],
    ) -> None:
        try:
            for message in messages:
                websocket.send(serialize_executor_message(message))
                self._command_processor.mark_delivered(str(message.message_id))
        except Exception:
            raise ExecutorProcessRejected from None

    def run(self, stop: threading.Event) -> None:
        if not isinstance(stop, threading.Event):
            raise ExecutorProcessRejected
        failed = False
        try:
            session_token = self._bootstrap.session_token.get_secret_value()
            websocket = connect_executor_websocket(
                websocket_url=self._bootstrap.websocket_url,
                session_token=session_token,
                open_timeout=self._open_timeout,
                close_timeout=self._close_timeout,
            )
            del session_token
            with websocket:
                websocket.send(
                    serialize_executor_message(
                        self._lifecycle(message_type="executor.hello", sequence=1)
                    )
                )
                self._send_outbox(websocket, self._command_processor.recover_outbox())
                sequence = 1
                healthy = False
                while not stop.is_set():
                    try:
                        source = websocket.recv(timeout=self._bootstrap.heartbeat_interval_seconds)
                    except TimeoutError:
                        if stop.is_set():
                            break
                        sequence += 1
                        websocket.send(
                            serialize_executor_message(
                                self._lifecycle(
                                    message_type="executor.heartbeat",
                                    sequence=sequence,
                                )
                            )
                        )
                        if not healthy:
                            self._reporter.healthy()
                            healthy = True
                        continue
                    except Exception:
                        if stop.is_set():
                            break
                        raise
                    if stop.is_set():
                        break
                    self._send_outbox(websocket, self._command_processor.handle(source))
        except Exception:
            failed = True
        if failed:
            raise ExecutorProcessRejected from None
        self._reporter.stopped()


__all__ = [
    "ExecutorClock",
    "ExecutorProcessRejected",
    "ExecutorProcessReporter",
    "LocalExecutorProcess",
    "RuntimeMetadata",
]
