"""Fail-closed redaction for Local Executor diagnostic text."""

from __future__ import annotations

import threading
from io import TextIOBase
from typing import TextIO

from automation_tool.logging_redaction import redact_log_text

_RECOVERY_DIAGNOSTIC_PREFIX = "executor.recovery "
_RECOVERY_DIAGNOSTIC_CODES = frozenset(
    {
        "browser_window_recovered",
        "browser_window_unavailable",
        "command_deadline_expired",
        "system_suspension_detected",
        "transport_recovered",
    }
)


class ExecutorRecoveryDiagnostics:
    """Write only fixed recovery facts for the Rust-owned bounded stderr queue."""

    def __init__(self, output: TextIO) -> None:
        if not isinstance(output, TextIOBase):
            raise ValueError("invalid Executor diagnostic output")
        self._output = output
        self._lock = threading.Lock()
        self._browser_unavailable = False

    def system_suspension_detected(self) -> None:
        self._write("system_suspension_detected")

    def command_deadline_expired(self) -> None:
        self._write("command_deadline_expired")

    def transport_recovered(self) -> None:
        self._write("transport_recovered")

    def browser_window_unavailable(self) -> None:
        with self._lock:
            if self._browser_unavailable:
                return
            self._browser_unavailable = True
            self._write_locked("browser_window_unavailable")

    def browser_window_available(self) -> None:
        with self._lock:
            if not self._browser_unavailable:
                return
            self._browser_unavailable = False
            self._write_locked("browser_window_recovered")

    def _write(self, code: str) -> None:
        with self._lock:
            self._write_locked(code)

    def _write_locked(self, code: str) -> None:
        if code not in _RECOVERY_DIAGNOSTIC_CODES:  # pragma: no cover - private fixed calls
            return
        try:
            self._output.write(f"{_RECOVERY_DIAGNOSTIC_PREFIX}{code}\n")
            self._output.flush()
        except Exception:
            return


def redact_diagnostic_line(value: str) -> str:
    """Return a single-line diagnostic with secrets and private paths removed."""
    return redact_log_text(value)


__all__ = ["ExecutorRecoveryDiagnostics", "redact_diagnostic_line"]
