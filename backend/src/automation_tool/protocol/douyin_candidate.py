"""Versioned minimum candidate model shared by discovery trust boundaries."""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256
from typing import Self

from automation_tool.protocol.limits import MAX_CROSS_RUNTIME_SEQUENCE
from automation_tool.protocol.safe_text import is_unsafe_text

DOUYIN_CANDIDATE_VERSION = "douyin.candidate.v1"
MAX_DOUYIN_TARGET_ID_CHARACTERS = 128
MAX_CANDIDATE_DISPLAY_NAME_CHARACTERS = 80
MAX_CANDIDATE_PUBLIC_HANDLE_CHARACTERS = 64

_CANDIDATE_KEY_PREFIX = "atdck1_"
_CANDIDATE_KEY_DOMAIN = b"automation-tool.douyin.candidate-key.v1\0"
_CANDIDATE_KEY_PATTERN = re.compile(r"atdck1_[A-Za-z0-9_-]{43}")
_PLATFORM_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


class DouyinCandidateRejected(ValueError):
    """A candidate is not safe or canonical enough to cross a boundary."""

    def __init__(self) -> None:
        super().__init__("Douyin candidate is invalid")


class DouyinCandidateSource(StrEnum):
    GENERAL_SEARCH_AUTHOR = "general_search_author"


@dataclass(frozen=True, slots=True, repr=False)
class DouyinCandidateKey:
    """A non-secret, domain-separated stable key for one platform target."""

    value: str

    def __post_init__(self) -> None:
        if type(self.value) is not str or _CANDIDATE_KEY_PATTERN.fullmatch(self.value) is None:
            raise DouyinCandidateRejected

    @classmethod
    def parse(cls, value: object) -> Self:
        if isinstance(value, cls):
            return value
        if type(value) is not str:
            raise DouyinCandidateRejected
        return cls(value)

    def __str__(self) -> str:
        return self.value

    def __repr__(self) -> str:
        return "DouyinCandidateKey(<stable>)"


@dataclass(frozen=True, slots=True, repr=False)
class DouyinCandidateSummary:
    """Only the two user-visible labels needed by the first preview."""

    display_name: str
    public_handle: str | None

    def __post_init__(self) -> None:
        if not _valid_display_name(self.display_name) or not _valid_public_handle(
            self.public_handle
        ):
            raise DouyinCandidateRejected

    def __repr__(self) -> str:
        return "DouyinCandidateSummary(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class DouyinCandidate:
    """One bounded discovery fact, not a page dump or action authorization."""

    platform_target_id: str
    summary: DouyinCandidateSummary
    source: DouyinCandidateSource
    page_revision: int
    dedupe_key: DouyinCandidateKey = field(init=False)

    def __post_init__(self) -> None:
        if (
            not _valid_platform_identifier(
                self.platform_target_id,
                maximum_characters=MAX_DOUYIN_TARGET_ID_CHARACTERS,
            )
            or not isinstance(self.summary, DouyinCandidateSummary)
            or not isinstance(self.source, DouyinCandidateSource)
            or type(self.page_revision) is not int
            or not 1 <= self.page_revision <= MAX_CROSS_RUNTIME_SEQUENCE
        ):
            raise DouyinCandidateRejected
        object.__setattr__(self, "dedupe_key", _derive_key(self.platform_target_id))

    @property
    def version(self) -> str:
        return DOUYIN_CANDIDATE_VERSION

    def __repr__(self) -> str:
        return (
            "DouyinCandidate("
            f"source={self.source.value!r}, page_revision={self.page_revision!r}, "
            "<redacted>)"
        )


def _valid_display_name(value: object) -> bool:
    return (
        type(value) is str
        and bool(value)
        and value.strip() == value
        and not is_unsafe_text(
            value,
            maximum_characters=MAX_CANDIDATE_DISPLAY_NAME_CHARACTERS,
        )
    )


def _valid_public_handle(value: object) -> bool:
    return value is None or _valid_platform_identifier(
        value,
        maximum_characters=MAX_CANDIDATE_PUBLIC_HANDLE_CHARACTERS,
    )


def _valid_platform_identifier(value: object, *, maximum_characters: int) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= maximum_characters
        and _PLATFORM_IDENTIFIER_PATTERN.fullmatch(value) is not None
    )


def _derive_key(platform_target_id: str) -> DouyinCandidateKey:
    digest = sha256(_CANDIDATE_KEY_DOMAIN + platform_target_id.encode("ascii")).digest()
    encoded = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return DouyinCandidateKey(f"{_CANDIDATE_KEY_PREFIX}{encoded}")


__all__ = [
    "DOUYIN_CANDIDATE_VERSION",
    "MAX_CANDIDATE_DISPLAY_NAME_CHARACTERS",
    "MAX_CANDIDATE_PUBLIC_HANDLE_CHARACTERS",
    "MAX_DOUYIN_TARGET_ID_CHARACTERS",
    "DouyinCandidate",
    "DouyinCandidateKey",
    "DouyinCandidateRejected",
    "DouyinCandidateSource",
    "DouyinCandidateSummary",
]
