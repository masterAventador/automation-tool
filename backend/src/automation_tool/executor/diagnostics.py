"""Fail-closed redaction for Local Executor diagnostic text."""

from __future__ import annotations

import re
import threading
from io import TextIOBase
from typing import TextIO

_REDACTED = "[REDACTED]"
_BIDI_CODEPOINTS = frozenset(range(0x202A, 0x202F)) | frozenset(range(0x2066, 0x206A))
_SECRET_KEYS = (
    r"access[_-]?token|refresh[_-]?token|local[_-]?session[_-]?token|"
    r"control[_-]?plane[_-]?session|session[_-]?token|api[_-]?key|"
    r"private[_-]?key|authorization|credential|password|secret|cookie|"
    r"session[_-]?cookie|token|sid_tt|sessionid(?:_ss)?|web_session|"
    r"passport_csrf_token|a1"
)
_COLON_SECRET_KEYS = (
    r"access[_-]?token|refresh[_-]?token|local[_-]?session[_-]?token|"
    r"control[_-]?plane[_-]?session|session[_-]?token|private[_-]?key|"
    r"credential|password|secret|session[_-]?cookie|token|sid_tt|"
    r"sessionid(?:_ss)?|web_session|passport_csrf_token|a1"
)
_HEADER_PATTERN = re.compile(
    r"\b(authorization|proxy-authorization|cookie|set-cookie|x-api-key):\s*[^\r\n]+",
    re.IGNORECASE,
)
_JSON_SECRET_PATTERN = re.compile(
    rf"[\"']({_SECRET_KEYS})[\"']\s*:\s*(?:\"[^\"]*\"|'[^']*'|[^,\s}}\]]+)",
    re.IGNORECASE,
)
_ASSIGNMENT_PATTERN = re.compile(
    rf"\b({_SECRET_KEYS})\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;}}\]]+)",
    re.IGNORECASE,
)
_COLON_ASSIGNMENT_PATTERN = re.compile(
    rf"(^|[\s,{{])({_COLON_SECRET_KEYS})\s*:\s*"
    rf"(?:\"[^\"]*\"|'[^']*'|[^\s,;}}\]]+)",
    re.IGNORECASE,
)
_BEARER_PATTERN = re.compile(r"\bbearer\s+[^\s,;\"']+", re.IGNORECASE)
_CREDENTIAL_ENVELOPE_PATTERN = re.compile(r"\bat(?:dc|ds|lep|ems)1\.[a-z0-9._~-]+", re.IGNORECASE)
_RAW_256_BIT_HEX_PATTERN = re.compile(r"\b[0-9a-f]{64}\b", re.IGNORECASE)
_URL_USERINFO_PATTERN = re.compile(r"\b((?:https?|wss?)://)[^/\s:@]+:[^@/\s]+@", re.IGNORECASE)
_URL_QUERY_PATTERN = re.compile(r"\b((?:https?|wss?)://[^\s?#]+)\?[^\s#]*", re.IGNORECASE)
_FILE_URL_PATTERN = re.compile(r"\bfile://[^\s\"'<>]+", re.IGNORECASE)
_INLINE_DATA_PATTERN = re.compile(r"\bdata:[a-z0-9.+-]+/[a-z0-9.+-]+[^,\s]*,[^\s]+", re.IGNORECASE)
_PRIVATE_POSIX_PATH_PATTERN = re.compile(
    r"(?:/private)?/(?:users|home|root|tmp|var/folders)(?:/[^\s\"'<>]*)?",
    re.IGNORECASE,
)
_PRIVATE_WINDOWS_PATH_PATTERN = re.compile(r"\b[a-z]:[\\/][^\s\"'<>]+", re.IGNORECASE)
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
    safe = "".join(" " if _unsafe_character(character) else character for character in value)
    safe = _JSON_SECRET_PATTERN.sub(lambda match: f'"{match.group(1)}":"{_REDACTED}"', safe)
    safe = _URL_USERINFO_PATTERN.sub(lambda match: f"{match.group(1)}{_REDACTED}@", safe)
    safe = _URL_QUERY_PATTERN.sub(lambda match: f"{match.group(1)}?{_REDACTED}", safe)
    safe = _FILE_URL_PATTERN.sub(_REDACTED, safe)
    safe = _INLINE_DATA_PATTERN.sub(_REDACTED, safe)
    safe = _CREDENTIAL_ENVELOPE_PATTERN.sub(_REDACTED, safe)
    safe = _ASSIGNMENT_PATTERN.sub(lambda match: f"{match.group(1)}={_REDACTED}", safe)
    safe = _COLON_ASSIGNMENT_PATTERN.sub(
        lambda match: f"{match.group(1)}{match.group(2)}={_REDACTED}", safe
    )
    safe = _BEARER_PATTERN.sub(f"Bearer {_REDACTED}", safe)
    safe = _RAW_256_BIT_HEX_PATTERN.sub(_REDACTED, safe)
    safe = _PRIVATE_POSIX_PATH_PATTERN.sub(_REDACTED, safe)
    safe = _PRIVATE_WINDOWS_PATH_PATTERN.sub(_REDACTED, safe)
    return _HEADER_PATTERN.sub(lambda match: f"{match.group(1)}: {_REDACTED}", safe)


def _unsafe_character(character: str) -> bool:
    codepoint = ord(character)
    return codepoint <= 0x1F or codepoint == 0x7F or codepoint in _BIDI_CODEPOINTS


__all__ = ["ExecutorRecoveryDiagnostics", "redact_diagnostic_line"]
