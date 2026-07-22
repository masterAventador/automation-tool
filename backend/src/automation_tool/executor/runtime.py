"""Minimal Local Executor process lifecycle and formal health traffic."""

from __future__ import annotations

import json
import math
import platform as host_platform
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import TextIOBase
from queue import Empty, Queue
from typing import Literal, Protocol, TextIO, runtime_checkable
from uuid import RFC_4122, UUID, uuid4

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import ClientConnection

from automation_tool import __version__
from automation_tool.executor.authentication import LocalSessionAuthenticator
from automation_tool.executor.bootstrap import ExecutorBootstrap
from automation_tool.executor.command_processor import (
    ExecutorCommandExpired,
    ExecutorCommandProcessor,
    ExecutorOutboundMessage,
)
from automation_tool.executor.diagnostics import ExecutorRecoveryDiagnostics
from automation_tool.executor.transport import (
    ExecutorTransportRejected,
    connect_executor_websocket,
    positive_seconds,
    serialize_executor_message,
)
from automation_tool.protocol import (
    EXECUTOR_PROTOCOL_VERSION,
    ExecutorLifecycleEnvelope,
    PlatformSessionHealthEnvelope,
)

_VERSION_PATTERN = re.compile(
    r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?"
    r"(?:\+[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?$"
)
_MESSAGE_DEADLINE = timedelta(seconds=30)
_CONTROL_PLANE_RESTART_CLOSE_CODE = 1012
_MAX_RESTART_RECONNECT_ATTEMPTS = 1000
_MAX_RESTART_RECONNECT_DELAY = timedelta(seconds=5)
_DEFAULT_SUSPEND_GAP_THRESHOLD = timedelta(seconds=5)
_MAX_SUSPEND_GAP_THRESHOLD = timedelta(minutes=10)


class ExecutorProcessRejected(ConnectionError):
    """The Local Executor cannot start or continue without weakening its boundary."""

    def __init__(self) -> None:
        super().__init__("Local Executor process is unavailable")


class _ExecutorNetworkDisconnected(ExecutorProcessRejected):
    pass


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
        self._lock = threading.Lock()

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
            with self._lock:
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

    def platform_command_result(self, *, command_id: str, state: str) -> None:
        from automation_tool.executor.platform_commands import (
            write_platform_command_result,
        )

        try:
            with self._lock:
                write_platform_command_result(
                    self._output,
                    self._authenticator,
                    command_id=command_id,
                    state=state,
                )
        except Exception:
            raise ExecutorProcessRejected from None


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


def _restart_reconnect_delay(value: object) -> timedelta:
    duration = _positive_duration(value)
    if duration > _MAX_RESTART_RECONNECT_DELAY:
        raise ExecutorProcessRejected
    return duration


def _is_control_plane_restart(error: BaseException) -> bool:
    return (
        isinstance(error, ConnectionClosed)
        and error.rcvd is not None
        and error.rcvd.code == _CONTROL_PLANE_RESTART_CLOSE_CODE
    )


def _is_recoverable_transport_error(error: BaseException) -> bool:
    if isinstance(error, _ExecutorNetworkDisconnected):
        return True
    if isinstance(error, (ExecutorProcessRejected, ExecutorTransportRejected)):
        return False
    if isinstance(error, ConnectionClosed):
        return _is_control_plane_restart(error) or (error.rcvd is None and error.sent is None)
    return isinstance(error, (OSError, TimeoutError))


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
        local_outbox: Queue[object] | None = None,
        restart_reconnect_attempts: int = 120,
        restart_reconnect_delay: timedelta = timedelta(milliseconds=250),
        monotonic_source: Callable[[], float] = time.monotonic,
        suspend_gap_threshold: timedelta = _DEFAULT_SUSPEND_GAP_THRESHOLD,
        diagnostics: ExecutorRecoveryDiagnostics | None = None,
    ) -> None:
        resolved_clock = metadata if clock is None else clock
        if (
            not isinstance(bootstrap, ExecutorBootstrap)
            or not isinstance(metadata, RuntimeMetadata)
            or not isinstance(reporter, ExecutorProcessReporter)
            or not isinstance(command_processor, ExecutorCommandProcessor)
            or not isinstance(resolved_clock, ExecutorClock)
            or not callable(id_source)
            or (local_outbox is not None and not isinstance(local_outbox, Queue))
            or type(restart_reconnect_attempts) is not int
            or not 1 <= restart_reconnect_attempts <= _MAX_RESTART_RECONNECT_ATTEMPTS
            or not callable(monotonic_source)
            or (
                diagnostics is not None and not isinstance(diagnostics, ExecutorRecoveryDiagnostics)
            )
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
        self._local_outbox = local_outbox
        self._pending_local_message: PlatformSessionHealthEnvelope | None = None
        self._restart_reconnect_attempts = restart_reconnect_attempts
        self._restart_reconnect_delay = _restart_reconnect_delay(restart_reconnect_delay)
        self._monotonic_source = monotonic_source
        self._suspend_gap_threshold = _positive_duration(suspend_gap_threshold)
        if self._suspend_gap_threshold > _MAX_SUSPEND_GAP_THRESHOLD:
            raise ExecutorProcessRejected
        self._diagnostics = diagnostics
        try:
            self._command_processor.ledger.set_transport_connected(False)
        except Exception:
            raise ExecutorProcessRejected from None

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

    def _monotonic_now(self) -> float:
        try:
            value = self._monotonic_source()
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError
            return float(value)
        except Exception:
            raise ExecutorProcessRejected from None

    def _suspension_detected(self, previous: float, current: float) -> bool:
        if current < previous:
            raise ExecutorProcessRejected
        return current - previous >= self._suspend_gap_threshold.total_seconds()

    def _report_suspension(self) -> None:
        if self._diagnostics is not None:
            self._diagnostics.system_suspension_detected()

    def _report_expired_command(self) -> None:
        if self._diagnostics is not None:
            self._diagnostics.command_deadline_expired()

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
        except ConnectionClosed:
            raise
        except (OSError, TimeoutError):
            raise _ExecutorNetworkDisconnected from None
        except Exception:
            raise ExecutorProcessRejected from None

    def _drain_durable_outbox(
        self,
        websocket: ClientConnection,
        messages: tuple[ExecutorOutboundMessage, ...],
    ) -> None:
        pending = messages
        while pending:
            self._send_outbox(websocket, pending)
            pending = self._command_processor.pending_outbox()

    def _send_local_outbox(self, websocket: ClientConnection) -> None:
        if self._local_outbox is None:
            return
        try:
            while True:
                candidate: object = self._pending_local_message
                if candidate is None:
                    candidate = self._local_outbox.get_nowait()
                if not isinstance(candidate, PlatformSessionHealthEnvelope):
                    raise ValueError
                message = candidate
                try:
                    websocket.send(serialize_executor_message(message))
                except ConnectionClosed:
                    self._pending_local_message = message
                    raise
                except (OSError, TimeoutError):
                    self._pending_local_message = message
                    raise _ExecutorNetworkDisconnected from None
                self._pending_local_message = None
        except Empty:
            return
        except _ExecutorNetworkDisconnected:
            raise
        except ConnectionClosed:
            raise
        except Exception:
            raise ExecutorProcessRejected from None

    def run(self, stop: threading.Event) -> None:
        if not isinstance(stop, threading.Event):
            raise ExecutorProcessRejected
        failed = False
        healthy = False
        recovering_transport = False
        recovering_from_suspension = False
        reconnect_attempts = 0
        reconnect_delay_seconds = self._restart_reconnect_delay.total_seconds()
        first_connection = True
        while first_connection or not stop.is_set():  # pragma: no branch
            first_connection = False
            if recovering_transport:
                if reconnect_attempts >= self._restart_reconnect_attempts:
                    failed = True
                    break
                reconnect_attempts += 1
            try:
                session_token = self._bootstrap.session_token.get_secret_value()
                try:
                    websocket = connect_executor_websocket(
                        websocket_url=self._bootstrap.websocket_url,
                        session_token=session_token,
                        open_timeout=self._open_timeout,
                        close_timeout=self._close_timeout,
                    )
                finally:
                    del session_token
            except Exception as error:
                if not _is_recoverable_transport_error(error):
                    failed = True
                    break
                if not recovering_transport:
                    recovering_transport = True
                    reconnect_attempts = 0
                if stop.wait(reconnect_delay_seconds):
                    break
                continue
            try:
                with websocket:
                    websocket.send(
                        serialize_executor_message(
                            self._lifecycle(message_type="executor.hello", sequence=1)
                        )
                    )
                    self._drain_durable_outbox(
                        websocket,
                        self._command_processor.recover_outbox(),
                    )
                    self._send_outbox(websocket, self._command_processor.poll_controls())
                    self._command_processor.ledger.set_transport_connected(True)
                    if self._command_processor.emergency_stop_received():
                        if self._bootstrap.local_emergency_stop:
                            self._reporter.healthy()
                        self._command_processor.ledger.set_transport_connected(False)
                        self._reporter.stopped()
                        return
                    if stop.is_set():
                        break
                    heartbeat_interval = float(self._bootstrap.heartbeat_interval_seconds)
                    last_monotonic = self._monotonic_now()
                    heartbeat_deadline = last_monotonic + heartbeat_interval
                    sequence = 2
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
                    if recovering_transport:
                        recovering_transport = False
                        reconnect_attempts = 0
                        if recovering_from_suspension:
                            if self._diagnostics is not None:
                                self._diagnostics.transport_recovered()
                            recovering_from_suspension = False
                    while not stop.is_set():
                        current_monotonic = self._monotonic_now()
                        if self._suspension_detected(last_monotonic, current_monotonic):
                            self._report_suspension()
                            recovering_from_suspension = True
                            raise _ExecutorNetworkDisconnected
                        last_monotonic = current_monotonic
                        self._send_outbox(websocket, self._command_processor.poll_controls())
                        self._send_local_outbox(websocket)
                        remaining = heartbeat_deadline - current_monotonic
                        receive_timeout = max(0.001, min(0.25, remaining))
                        try:
                            source = websocket.recv(timeout=receive_timeout)
                        except TimeoutError:
                            source = None
                        except Exception:
                            if stop.is_set():
                                break
                            raise
                        if stop.is_set():
                            break
                        current_monotonic = self._monotonic_now()
                        if self._suspension_detected(last_monotonic, current_monotonic):
                            self._report_suspension()
                            recovering_from_suspension = True
                            raise _ExecutorNetworkDisconnected
                        last_monotonic = current_monotonic
                        if current_monotonic >= heartbeat_deadline:
                            sequence += 1
                            websocket.send(
                                serialize_executor_message(
                                    self._lifecycle(
                                        message_type="executor.heartbeat",
                                        sequence=sequence,
                                    )
                                )
                            )
                            heartbeat_deadline = current_monotonic + heartbeat_interval
                        if source is None:
                            continue
                        try:
                            outcome = self._command_processor.handle(source)
                        except ExecutorCommandExpired:
                            self._report_expired_command()
                            continue
                        self._send_outbox(websocket, outcome)
                        last_monotonic = self._monotonic_now()
                        if self._command_processor.emergency_stop_received():
                            break
            except Exception as error:
                if stop.is_set():
                    break
                try:
                    self._command_processor.ledger.set_transport_connected(False)
                except Exception:
                    failed = True
                    break
                if not _is_recoverable_transport_error(error):
                    failed = True
                    break
                if not recovering_transport:
                    recovering_transport = True
                    reconnect_attempts = 0
                if stop.wait(reconnect_delay_seconds):
                    break
                continue
            break
        try:
            self._command_processor.ledger.set_transport_connected(False)
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
