"""Process-wide fail-closed logging boundary for the Control Plane and Uvicorn."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Final

from automation_tool.logging_redaction import REDACTED_LOG_VALUE, redact_log_text

MAX_CONTROL_PLANE_LOG_MESSAGE_BYTES: Final = 4096
_SAFE_FACTORY_MARKER: Final = "_automation_tool_control_plane_safe_factory"
_CONTROL_PLANE_LOGGER_PREFIX: Final = "automation_tool.control_plane"
_UVICORN_LOGGER_PREFIX: Final = "uvicorn"

LogRecordFactory = Callable[..., logging.LogRecord]


def install_control_plane_log_redaction() -> None:
    """Install one idempotent record factory before any production handler sees a record."""

    previous: LogRecordFactory = logging.getLogRecordFactory()
    if getattr(previous, _SAFE_FACTORY_MARKER, False) is True:
        return

    def safe_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = previous(*args, **kwargs)
        if record.name == "uvicorn.access":
            _replace_record(record, "Control Plane request")
        elif record.name.startswith((_CONTROL_PLANE_LOGGER_PREFIX, _UVICORN_LOGGER_PREFIX)):
            _sanitize_record(record)
        return record

    setattr(safe_factory, _SAFE_FACTORY_MARKER, True)
    logging.setLogRecordFactory(safe_factory)


def _sanitize_record(record: logging.LogRecord) -> None:
    message = redact_log_text(record.msg)
    if record.args:
        message = f"{message} values={REDACTED_LOG_VALUE}"
    if record.exc_info is not None or record.exc_text is not None:
        message = f"{message} exception={REDACTED_LOG_VALUE}"
    if record.stack_info is not None:
        message = f"{message} stack={REDACTED_LOG_VALUE}"
    _replace_record(record, message)


def _replace_record(record: logging.LogRecord, message: str) -> None:
    record.msg = _truncate_utf8(redact_log_text(message), MAX_CONTROL_PLANE_LOG_MESSAGE_BYTES)
    record.args = ()
    record.exc_info = None
    record.exc_text = None
    record.stack_info = None
    record.pathname = REDACTED_LOG_VALUE


def _truncate_utf8(value: str, maximum_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= maximum_bytes:
        return value
    return encoded[:maximum_bytes].decode("utf-8", errors="ignore")


__all__ = ["MAX_CONTROL_PLANE_LOG_MESSAGE_BYTES", "install_control_plane_log_redaction"]
