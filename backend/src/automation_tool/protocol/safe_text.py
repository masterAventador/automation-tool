"""Shared rejection rules for text that crosses or persists beyond trust boundaries."""

import re

SHA256_HEX_CHARACTERS = 64

_LOWERCASE_HEX_DIGITS = frozenset("0123456789abcdef")
_CONTROL_OR_BIDI_PATTERN = re.compile(r"[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]")
_PRIVATE_POSIX_PATH_PATTERN = re.compile(
    r"(?:^|[\s\"'=])/(?:users|home|root|tmp|var/folders)(?:/|$)",
    re.IGNORECASE,
)
_WINDOWS_ABSOLUTE_PATH_PATTERN = re.compile(r"(?:^|[\s\"'=])[a-z]:[\\/]", re.IGNORECASE)
_INLINE_DATA_URI_PATTERN = re.compile(r"\bdata:[a-z0-9.+-]+/[a-z0-9.+-]+[^,]*,", re.IGNORECASE)
_SENSITIVE_ASSIGNMENT_PATTERN = re.compile(
    r"(?:^|[^a-z0-9_])(?:access[_-]?token|api[_-]?key|authorization|cookie|credential|"
    r"password|private[_-]?key|refresh[_-]?token|secret|session[_-]?cookie|token)\s*[:=]",
    re.IGNORECASE,
)


def contains_control_or_bidi(value: str) -> bool:
    """Return whether text contains controls that can corrupt logs or visual order."""
    return _CONTROL_OR_BIDI_PATTERN.search(value) is not None


def is_unsafe_text(value: str, *, maximum_characters: int) -> bool:
    """Apply the shared secret, path, inline-data, and control-text policy."""
    folded = value.casefold()
    return (
        len(value) > maximum_characters
        or contains_control_or_bidi(value)
        or "bearer " in folded
        or "file://" in folded
        or _SENSITIVE_ASSIGNMENT_PATTERN.search(value) is not None
        or _INLINE_DATA_URI_PATTERN.search(value) is not None
        or _PRIVATE_POSIX_PATH_PATTERN.search(value) is not None
        or _WINDOWS_ABSOLUTE_PATH_PATTERN.search(value) is not None
    )


def is_sha256_hex(value: object) -> bool:
    """Return whether text is exactly one canonical lowercase SHA-256 digest.

    Digests are the one publish projection allowed to persist and to cross the
    local command boundary, so every producer and consumer has to agree on the
    same shape - an uppercase or truncated variant must not compare equal to
    the digest it was derived from.
    """
    return (
        type(value) is str
        and len(value) == SHA256_HEX_CHARACTERS
        and all(character in _LOWERCASE_HEX_DIGITS for character in value)
    )


__all__ = [
    "SHA256_HEX_CHARACTERS",
    "contains_control_or_bidi",
    "is_sha256_hex",
    "is_unsafe_text",
]
