from __future__ import annotations

from typing import Any

import pytest

from automation_tool.control_plane.domain import (
    DouyinSearchExposureAction,
    DouyinSearchExposureDefinition,
    InvalidTaskDefinition,
)
from automation_tool.protocol import (
    ACTION_MESSAGE_TEMPLATE_VERSION,
    MAX_ACTION_MESSAGE_TEMPLATE_CHARACTERS,
    ActionMessageTemplate,
    ActionMessageTemplateRejected,
    ActionMessageVariable,
)


def definition(message_template: Any) -> DouyinSearchExposureDefinition:
    return DouyinSearchExposureDefinition(
        search_keyword="新能源汽车",
        action=DouyinSearchExposureAction.COMMENT,
        message_template=message_template,
        target_limit=10,
        minimum_interval_seconds=30,
        maximum_interval_seconds=90,
        preview_required=True,
        final_confirmation_required=True,
    )


@pytest.mark.parametrize(
    "source",
    (
        "内容很有启发",
        "您好 {{target_display_name}} 内容很有启发",
        "{{target_display_name}} 您好 期待更多分享",
        "😀" * 500,
    ),
)
def test_template_accepts_fixed_copy_and_one_closed_target_variable(source: str) -> None:
    template = ActionMessageTemplate(source=source)

    assert template.source == source
    assert template.version == ACTION_MESSAGE_TEMPLATE_VERSION
    assert definition(source).message_template == source


def test_template_reports_unique_variables_in_first_use_order() -> None:
    template = ActionMessageTemplate(
        source="您好 {{target_display_name}} 再次问候 {{target_display_name}}"
    )

    assert template.variables == (ActionMessageVariable.TARGET_DISPLAY_NAME,)
    assert ActionMessageVariable.TARGET_DISPLAY_NAME.value == "target_display_name"


@pytest.mark.parametrize(
    "source",
    (
        "",
        " ",
        " leading",
        "trailing\u00a0",
        "line\nbreak",
        "control\u0085character",
        "visual\u202etrap",
        "😀" * 501,
        "password=private-value",
        "Bearer private-value",
        "file:///Users/private-value",
        "data:image/png;base64,private-value",
        "/Users/private-value/message.txt",
        "C:\\Users\\private-value\\message.txt",
        "{{target_display_name}}",
        "{{unknown}}您好",
        "{{ target_display_name }}您好",
        "{{target.display_name}}您好",
        "{target_display_name}您好",
        "{{target_display_name}您好",
        "{{{target_display_name}}}您好",
        "您好 {{target_display_name}} {{unknown}}",
    ),
)
def test_template_and_task_definition_reject_the_same_invalid_copy_without_disclosure(
    source: Any,
) -> None:
    with pytest.raises(ActionMessageTemplateRejected) as template_error:
        ActionMessageTemplate(source=source)
    with pytest.raises(InvalidTaskDefinition) as definition_error:
        definition(source)

    assert template_error.value.__cause__ is None
    assert "private-value" not in repr(template_error.value)
    assert "private-value" not in repr(definition_error.value)


@pytest.mark.parametrize("source", (None, 7, b"copy", True))
def test_template_rejects_non_strings(source: object) -> None:
    with pytest.raises(ActionMessageTemplateRejected):
        ActionMessageTemplate(source=source)  # type: ignore[arg-type]


def test_template_is_immutable_and_repr_redacts_copy() -> None:
    template = ActionMessageTemplate(source="private-message")

    assert repr(template) == "ActionMessageTemplate(<redacted>)"
    with pytest.raises((AttributeError, TypeError)):
        template.source = "replacement"  # type: ignore[misc]


def test_template_constants_are_the_exact_mvp_contract() -> None:
    assert ACTION_MESSAGE_TEMPLATE_VERSION == "action-message-template.v1"
    assert MAX_ACTION_MESSAGE_TEMPLATE_CHARACTERS == 500
