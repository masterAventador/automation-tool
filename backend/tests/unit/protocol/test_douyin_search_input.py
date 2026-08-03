from __future__ import annotations

from typing import Any

import pytest

from automation_tool.control_plane.domain.task_definitions import (
    DouyinSearchExposureAction,
    DouyinSearchExposureDefinition,
    InvalidTaskDefinition,
)
from automation_tool.protocol import (
    DOUYIN_SEARCH_INPUT_VERSION,
    MAX_SEARCH_KEYWORD_CHARACTERS,
    MAX_TASK_TARGET_LIMIT,
    DouyinSearchInput,
    DouyinSearchInputRejected,
)


def definition(keyword: Any, target_limit: Any) -> DouyinSearchExposureDefinition:
    return DouyinSearchExposureDefinition(
        search_keyword=keyword,
        action=DouyinSearchExposureAction.BROWSE,
        message_template=None,
        target_limit=target_limit,
        minimum_interval_seconds=30,
        maximum_interval_seconds=90,
        preview_required=True,
        final_confirmation_required=True,
    )


@pytest.mark.parametrize(
    ("keyword", "target_limit"),
    (
        ("词", 1),
        ("词" * 80, 100),
        ("😀" * 80, 10),
        ("新能源 汽车", 10),
    ),
)
def test_shared_input_accepts_exact_unicode_boundaries_used_by_server(
    keyword: str,
    target_limit: int,
) -> None:
    value = DouyinSearchInput(keyword=keyword, target_limit=target_limit)

    assert value.keyword == keyword
    assert value.target_limit == target_limit
    assert value.version == DOUYIN_SEARCH_INPUT_VERSION
    assert definition(keyword, target_limit).search_keyword == keyword


@pytest.mark.parametrize(
    ("keyword", "target_limit"),
    (
        ("", 10),
        (" ", 10),
        (" leading", 10),
        ("trailing\u00a0", 10),
        ("line\nbreak", 10),
        ("control\u0085character", 10),
        ("visual\u202etrap", 10),
        ("词" * 81, 10),
        ("😀" * 81, 10),
        ("password=private-value", 10),
        ("file:///private-value", 10),
        ("valid", 0),
        ("valid", 101),
        ("valid", True),
        ("valid", 1.0),
        (None, 10),
        (10, 10),
    ),
)
def test_shared_input_and_server_reject_the_same_invalid_values_without_disclosure(
    keyword: Any,
    target_limit: Any,
) -> None:
    with pytest.raises(DouyinSearchInputRejected) as input_error:
        DouyinSearchInput(keyword=keyword, target_limit=target_limit)
    with pytest.raises(InvalidTaskDefinition) as definition_error:
        definition(keyword, target_limit)

    assert "private-value" not in repr(input_error.value)
    assert "private-value" not in repr(definition_error.value)


def test_input_is_immutable_and_repr_redacts_the_keyword() -> None:
    value = DouyinSearchInput(keyword="private-keyword", target_limit=10)

    assert repr(value) == "DouyinSearchInput(<redacted>)"
    with pytest.raises((AttributeError, TypeError)):
        value.keyword = "replacement"


def test_constraint_constants_are_the_exact_mvp_policy() -> None:
    assert DOUYIN_SEARCH_INPUT_VERSION == "douyin.search-input.v1"
    assert MAX_SEARCH_KEYWORD_CHARACTERS == 80
    assert MAX_TASK_TARGET_LIMIT == 100
