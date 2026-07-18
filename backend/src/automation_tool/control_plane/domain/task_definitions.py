"""Closed Task definitions that can be persisted without arbitrary payloads."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from automation_tool.protocol.safe_text import is_unsafe_text

DOUYIN_SEARCH_EXPOSURE_TEMPLATE = "douyin.search_exposure.v1"
MAX_SEARCH_KEYWORD_CHARACTERS = 80
MAX_MESSAGE_TEMPLATE_CHARACTERS = 500
MAX_TASK_TARGET_LIMIT = 100
MAX_TASK_INTERVAL_SECONDS = 3600


class DouyinSearchExposureAction(StrEnum):
    BROWSE = "browse"
    COMMENT = "comment"
    DIRECT_MESSAGE = "direct_message"


class InvalidTaskDefinition(ValueError):
    def __init__(self) -> None:
        super().__init__("Task definition is invalid")


def _safe_exact_text(value: object, *, maximum_characters: int) -> str:
    if (
        type(value) is not str
        or not value
        or value.strip() != value
        or is_unsafe_text(value, maximum_characters=maximum_characters)
    ):
        raise InvalidTaskDefinition
    return value


@dataclass(frozen=True, slots=True)
class DouyinSearchExposureDefinition:
    """Versioned MVP definition; discovery and platform actions remain separate."""

    search_keyword: str
    action: DouyinSearchExposureAction
    message_template: str | None
    target_limit: int
    minimum_interval_seconds: int
    maximum_interval_seconds: int
    preview_required: bool
    final_confirmation_required: bool

    @property
    def template(self) -> str:
        return DOUYIN_SEARCH_EXPOSURE_TEMPLATE

    def __post_init__(self) -> None:
        _safe_exact_text(
            self.search_keyword,
            maximum_characters=MAX_SEARCH_KEYWORD_CHARACTERS,
        )
        if not isinstance(self.action, DouyinSearchExposureAction):
            raise InvalidTaskDefinition
        if self.action is DouyinSearchExposureAction.BROWSE:
            if self.message_template is not None:
                raise InvalidTaskDefinition
        elif self.message_template is None:
            raise InvalidTaskDefinition
        else:
            _safe_exact_text(
                self.message_template,
                maximum_characters=MAX_MESSAGE_TEMPLATE_CHARACTERS,
            )
        if (
            type(self.target_limit) is not int
            or not 1 <= self.target_limit <= MAX_TASK_TARGET_LIMIT
            or type(self.minimum_interval_seconds) is not int
            or not 1 <= self.minimum_interval_seconds <= MAX_TASK_INTERVAL_SECONDS
            or type(self.maximum_interval_seconds) is not int
            or not self.minimum_interval_seconds
            <= self.maximum_interval_seconds
            <= MAX_TASK_INTERVAL_SECONDS
            or self.preview_required is not True
            or self.final_confirmation_required is not True
        ):
            raise InvalidTaskDefinition


__all__ = [
    "DOUYIN_SEARCH_EXPOSURE_TEMPLATE",
    "MAX_MESSAGE_TEMPLATE_CHARACTERS",
    "MAX_SEARCH_KEYWORD_CHARACTERS",
    "MAX_TASK_INTERVAL_SECONDS",
    "MAX_TASK_TARGET_LIMIT",
    "DouyinSearchExposureAction",
    "DouyinSearchExposureDefinition",
    "InvalidTaskDefinition",
]
