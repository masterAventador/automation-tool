"""Closed, non-rendering message-template policy shared across action runtimes."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from automation_tool.protocol.safe_text import is_unsafe_text

ACTION_MESSAGE_TEMPLATE_VERSION: Final = "action-message-template.v1"
MAX_ACTION_MESSAGE_TEMPLATE_CHARACTERS: Final = 500
_VARIABLE_PATTERN = re.compile(r"\{\{([a-z][a-z0-9_]*)\}\}")


class ActionMessageTemplateRejected(ValueError):
    def __init__(self) -> None:
        super().__init__("Action message template is invalid")


class ActionMessageVariable(StrEnum):
    TARGET_DISPLAY_NAME = "target_display_name"


@dataclass(frozen=True, slots=True, repr=False)
class ActionMessageTemplate:
    """Validate one exact template without expanding target data."""

    source: str
    _variables: tuple[ActionMessageVariable, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        try:
            if (
                type(self.source) is not str
                or not self.source
                or self.source.strip() != self.source
                or is_unsafe_text(
                    self.source,
                    maximum_characters=MAX_ACTION_MESSAGE_TEMPLATE_CHARACTERS,
                )
            ):
                raise ValueError
            matches = tuple(_VARIABLE_PATTERN.finditer(self.source))
            literal = _VARIABLE_PATTERN.sub("", self.source)
            if not literal.strip() or "{" in literal or "}" in literal:
                raise ValueError
            variables = tuple(
                dict.fromkeys(ActionMessageVariable(match.group(1)) for match in matches)
            )
            object.__setattr__(self, "_variables", variables)
        except (TypeError, ValueError):
            raise ActionMessageTemplateRejected from None

    @property
    def version(self) -> str:
        return ACTION_MESSAGE_TEMPLATE_VERSION

    @property
    def variables(self) -> tuple[ActionMessageVariable, ...]:
        return self._variables

    def __repr__(self) -> str:
        return "ActionMessageTemplate(<redacted>)"


__all__ = [
    "ACTION_MESSAGE_TEMPLATE_VERSION",
    "MAX_ACTION_MESSAGE_TEMPLATE_CHARACTERS",
    "ActionMessageTemplate",
    "ActionMessageTemplateRejected",
    "ActionMessageVariable",
]
