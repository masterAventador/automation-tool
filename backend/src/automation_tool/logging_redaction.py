"""Shared fail-closed redaction for process logs and Executor diagnostics."""

from __future__ import annotations

import re
from typing import Final

REDACTED_LOG_VALUE: Final = "[REDACTED]"

_BIDI_CODEPOINTS: Final = frozenset(range(0x202A, 0x202F)) | frozenset(range(0x2066, 0x206A))
_SECRET_KEYS: Final = (
    r"access[_-]?token|refresh[_-]?token|local[_-]?session[_-]?token|"
    r"control[_-]?plane[_-]?session|session[_-]?token|api[_-]?key|"
    r"private[_-]?key|authorization|credential|password|secret|cookie|"
    r"session[_-]?cookie|token|sid_tt|sessionid(?:_ss)?|web_session|"
    r"passport_csrf_token|a1|page[_-]?(?:content|text)|html|dom|"
    r"comment[_-]?text|direct[_-]?message|message[_-]?text|"
    r"request[_-]?body|response[_-]?body"
)
_COLON_SECRET_KEYS: Final = (
    r"access[_-]?token|refresh[_-]?token|local[_-]?session[_-]?token|"
    r"control[_-]?plane[_-]?session|session[_-]?token|private[_-]?key|"
    r"credential|password|secret|session[_-]?cookie|token|sid_tt|"
    r"sessionid(?:_ss)?|web_session|passport_csrf_token|a1|"
    r"page[_-]?(?:content|text)|html|dom|comment[_-]?text|"
    r"direct[_-]?message|message[_-]?text|request[_-]?body|response[_-]?body"
)
_HEADER_PATTERN: Final = re.compile(
    r"\b(authorization|proxy-authorization|cookie|set-cookie|x-api-key):\s*[^\r\n]+",
    re.IGNORECASE,
)
_JSON_SECRET_PATTERN: Final = re.compile(
    rf"[\"']({_SECRET_KEYS})[\"']\s*:\s*(?:\"[^\"]*\"|'[^']*'|[^,\s}}\]]+)",
    re.IGNORECASE,
)
_ASSIGNMENT_PATTERN: Final = re.compile(
    rf"\b({_SECRET_KEYS})\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;}}\]]+)",
    re.IGNORECASE,
)
_COLON_ASSIGNMENT_PATTERN: Final = re.compile(
    rf"(^|[\s,{{])({_COLON_SECRET_KEYS})\s*:\s*"
    rf"(?:\"[^\"]*\"|'[^']*'|[^\s,;}}\]]+)",
    re.IGNORECASE,
)
_BEARER_PATTERN: Final = re.compile(r"\bbearer\s+[^\s,;\"']+", re.IGNORECASE)
_CREDENTIAL_ENVELOPE_PATTERN: Final = re.compile(
    r"\bat(?:dc|ds|lep|ems)1\.[a-z0-9._~-]+", re.IGNORECASE
)
_RAW_256_BIT_HEX_PATTERN: Final = re.compile(r"\b[0-9a-f]{64}\b", re.IGNORECASE)
_URL_PATTERN: Final = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s\"'<>]+", re.IGNORECASE)
_INLINE_DATA_PATTERN: Final = re.compile(
    r"\bdata:[a-z0-9.+-]+/[a-z0-9.+-]+[^,\s]*,[^\s]+", re.IGNORECASE
)
_PRIVATE_POSIX_PATH_PATTERN: Final = re.compile(
    r"(?:/private)?/(?:users|home|root|tmp|var/folders|volumes)(?:/[^\s\"'<>]*)?",
    re.IGNORECASE,
)
_PRIVATE_WINDOWS_PATH_PATTERN: Final = re.compile(r"\b[a-z]:[\\/][^\s\"'<>]+", re.IGNORECASE)


def redact_log_text(value: object) -> str:
    """Remove secrets, external content, URLs and private paths from one log value."""

    if type(value) is not str:
        return REDACTED_LOG_VALUE
    safe = "".join(" " if _unsafe_character(character) else character for character in value)
    safe = _JSON_SECRET_PATTERN.sub(
        lambda match: f'"{match.group(1)}":"{REDACTED_LOG_VALUE}"', safe
    )
    safe = _INLINE_DATA_PATTERN.sub(REDACTED_LOG_VALUE, safe)
    safe = _URL_PATTERN.sub(REDACTED_LOG_VALUE, safe)
    safe = _CREDENTIAL_ENVELOPE_PATTERN.sub(REDACTED_LOG_VALUE, safe)
    safe = _ASSIGNMENT_PATTERN.sub(lambda match: f"{match.group(1)}={REDACTED_LOG_VALUE}", safe)
    safe = _COLON_ASSIGNMENT_PATTERN.sub(
        lambda match: f"{match.group(1)}{match.group(2)}={REDACTED_LOG_VALUE}", safe
    )
    safe = _BEARER_PATTERN.sub(f"Bearer {REDACTED_LOG_VALUE}", safe)
    safe = _RAW_256_BIT_HEX_PATTERN.sub(REDACTED_LOG_VALUE, safe)
    safe = _PRIVATE_POSIX_PATH_PATTERN.sub(REDACTED_LOG_VALUE, safe)
    safe = _PRIVATE_WINDOWS_PATH_PATTERN.sub(REDACTED_LOG_VALUE, safe)
    return _HEADER_PATTERN.sub(lambda match: f"{match.group(1)}: {REDACTED_LOG_VALUE}", safe)


def _unsafe_character(character: str) -> bool:
    codepoint = ord(character)
    return codepoint <= 0x1F or codepoint == 0x7F or codepoint in _BIDI_CODEPOINTS


__all__ = ["REDACTED_LOG_VALUE", "redact_log_text"]
