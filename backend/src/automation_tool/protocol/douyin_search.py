"""Shared fail-closed input policy for Douyin target discovery."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from automation_tool.protocol.safe_text import is_unsafe_text

DOUYIN_SEARCH_INPUT_VERSION = "douyin.search-input.v1"
MAX_SEARCH_KEYWORD_CHARACTERS = 80
MAX_TASK_TARGET_LIMIT = 100


class DouyinSearchExposureAction(StrEnum):
    BROWSE = "browse"
    COMMENT = "comment"
    DIRECT_MESSAGE = "direct_message"


class DouyinSearchInputRejected(ValueError):
    """The discovery input cannot cross the Control Plane/Executor boundary."""

    def __init__(self) -> None:
        super().__init__("Douyin search input is invalid")


@dataclass(frozen=True, slots=True, repr=False)
class DouyinSearchInput:
    """Exact keyword and bound accepted by both Control Plane and Executor."""

    keyword: str
    target_limit: int

    def __post_init__(self) -> None:
        if (
            type(self.keyword) is not str
            or not self.keyword
            or self.keyword.strip() != self.keyword
            or is_unsafe_text(
                self.keyword,
                maximum_characters=MAX_SEARCH_KEYWORD_CHARACTERS,
            )
            or type(self.target_limit) is not int
            or not 1 <= self.target_limit <= MAX_TASK_TARGET_LIMIT
        ):
            raise DouyinSearchInputRejected

    @property
    def version(self) -> str:
        return DOUYIN_SEARCH_INPUT_VERSION

    def __repr__(self) -> str:
        return "DouyinSearchInput(<redacted>)"


__all__ = [
    "DOUYIN_SEARCH_INPUT_VERSION",
    "MAX_SEARCH_KEYWORD_CHARACTERS",
    "MAX_TASK_TARGET_LIMIT",
    "DouyinSearchExposureAction",
    "DouyinSearchInput",
    "DouyinSearchInputRejected",
]
