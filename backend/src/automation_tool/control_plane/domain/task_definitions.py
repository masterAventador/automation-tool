"""Closed Task definitions that can be persisted without arbitrary payloads."""

from __future__ import annotations

from dataclasses import dataclass

from automation_tool.protocol import (
    MAX_ACTION_MESSAGE_TEMPLATE_CHARACTERS,
    MAX_SEARCH_KEYWORD_CHARACTERS,
    MAX_TASK_TARGET_LIMIT,
    ActionMessageTemplate,
    ActionMessageTemplateRejected,
    DouyinSearchExposureAction,
    DouyinSearchInput,
    DouyinSearchInputRejected,
)

DOUYIN_SEARCH_EXPOSURE_TEMPLATE = "douyin.search_exposure.v1"
MAX_MESSAGE_TEMPLATE_CHARACTERS = MAX_ACTION_MESSAGE_TEMPLATE_CHARACTERS
MAX_TASK_INTERVAL_SECONDS = 3600


class InvalidTaskDefinition(ValueError):
    def __init__(self) -> None:
        super().__init__("Task definition is invalid")


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
        try:
            DouyinSearchInput(
                keyword=self.search_keyword,
                target_limit=self.target_limit,
            )
        except DouyinSearchInputRejected:
            raise InvalidTaskDefinition from None
        if not isinstance(self.action, DouyinSearchExposureAction):
            raise InvalidTaskDefinition
        if self.action is DouyinSearchExposureAction.BROWSE:
            if self.message_template is not None:
                raise InvalidTaskDefinition
        elif self.message_template is None:
            raise InvalidTaskDefinition
        else:
            try:
                ActionMessageTemplate(source=self.message_template)
            except ActionMessageTemplateRejected:
                raise InvalidTaskDefinition from None
        if (
            type(self.minimum_interval_seconds) is not int
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
