"""Console entry for the independently packaged Local Executor process."""

from __future__ import annotations

import signal
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import FrameType
from typing import BinaryIO, TextIO

from automation_tool.executor.authentication import (
    LocalSessionAuthenticationRejected,
    LocalSessionAuthenticator,
)
from automation_tool.executor.bootstrap import ExecutorBootstrapRejected, read_executor_bootstrap
from automation_tool.executor.command_processor import (
    ExecutorCommandProcessor,
    ExecutorCommandRejected,
)
from automation_tool.executor.ledger import ExecutorLedger, ExecutorLedgerRejected
from automation_tool.executor.runtime import (
    ExecutorProcessRejected,
    ExecutorProcessReporter,
    LocalExecutorProcess,
    RuntimeMetadata,
)


@contextmanager
def stop_signal_event() -> Iterator[threading.Event]:
    """Install process-local stop handlers and restore the caller's handlers."""

    stop = threading.Event()
    previous_int = signal.getsignal(signal.SIGINT)
    previous_term = signal.getsignal(signal.SIGTERM)
    break_signal = getattr(signal, "SIGBREAK", None)
    previous_break = signal.getsignal(break_signal) if isinstance(break_signal, int) else None

    def request_stop(_signum: int, _frame: FrameType | None) -> None:
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    if isinstance(break_signal, int):
        signal.signal(break_signal, request_stop)
    try:
        yield stop
    finally:
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)
        if isinstance(break_signal, int) and previous_break is not None:
            signal.signal(break_signal, previous_break)


def _fixed_error(error: TextIO, message: str) -> None:
    try:
        binary_error = getattr(error, "buffer", None)
        if binary_error is not None:
            binary_error.write((message + "\n").encode("utf-8"))
            binary_error.flush()
            return
        error.write(message + "\n")
        error.flush()
    except Exception:
        pass


def run_executor(stdin: BinaryIO, stdout: TextIO, stderr: TextIO) -> int:
    """Run one Executor lifetime and map every failure to a fixed exit contract."""

    try:
        bootstrap = read_executor_bootstrap(stdin)
    except ExecutorBootstrapRejected:
        _fixed_error(stderr, "Local Executor bootstrap is rejected")
        return 2
    try:
        authenticator = LocalSessionAuthenticator(bootstrap.local_session_token)
        try:
            ledger = ExecutorLedger(
                state_directory=Path(bootstrap.state_directory),
                installation_id=str(bootstrap.installation_id),
                executor_id=str(bootstrap.executor_id),
            )
            command_processor = ExecutorCommandProcessor(
                ledger=ledger,
                installation_id=str(bootstrap.installation_id),
                executor_id=str(bootstrap.executor_id),
            )
            process = LocalExecutorProcess(
                bootstrap=bootstrap,
                metadata=RuntimeMetadata.detect(),
                reporter=ExecutorProcessReporter(stdout, authenticator),
                command_processor=command_processor,
            )
            with stop_signal_event() as stop:
                process.run(stop)
        finally:
            authenticator.close()
    except (
        ExecutorLedgerRejected,
        ExecutorCommandRejected,
        ExecutorProcessRejected,
        LocalSessionAuthenticationRejected,
    ):
        _fixed_error(stderr, "Local Executor process is unavailable")
        return 1
    return 0


def main() -> None:
    raise SystemExit(run_executor(sys.stdin.buffer, sys.stdout, sys.stderr))


__all__ = ["main", "run_executor", "stop_signal_event"]
