from __future__ import annotations

import re
from typing import Any, cast

import pytest
from automation_tool.executor.rpa.douyin import (
    comment_page,
    direct_message_page,
    profile_page,
    publish_page,
    search_page,
)
from automation_tool.executor.rpa.douyin.page_anchors import (
    VISIBLE_MATCH_ENGINE,
    AnchorConflict,
    any_visible,
    unique_visible,
)

SELECTORS = ('[data-e2e="captcha-container"]', 'iframe[src*="/verifycenter/captcha/"]')
ENGINE_PREFIX = re.compile(r"^\s*[a-zA-Z_][a-zA-Z0-9_-]*\s*=")
UNIQUE_VISIBLE_GROUPS = (
    comment_page._COMMENT_INPUT_SELECTORS,
    comment_page._COMMENT_SUBMIT_SELECTORS,
    comment_page._FINAL_CONFIRMATION_SELECTORS,
    direct_message_page._MESSAGE_ENTRY_SELECTORS,
    direct_message_page._MESSAGE_INPUT_SELECTORS,
    direct_message_page._MESSAGE_SEND_SELECTORS,
    direct_message_page._FINAL_CONFIRMATION_SELECTORS,
    direct_message_page._MESSAGING_NOT_ALLOWED_SELECTORS,
    direct_message_page._FOLLOW_REQUIRED_SELECTORS,
    profile_page._PROFILE_ROOT_SELECTORS,
    search_page._SEARCH_INPUT_SELECTORS,
    search_page._SEARCH_SUBMIT_SELECTORS,
    search_page._RESULT_LIST_SELECTORS,
    search_page._CANDIDATE_AUTHOR_SELECTORS,
    search_page._CANDIDATE_NAME_SELECTORS,
    publish_page.DOUYIN_PUBLISH_ARTIFACT_SELECTORS,
    publish_page.DOUYIN_PUBLISH_TITLE_SELECTORS,
    publish_page.DOUYIN_PUBLISH_DESCRIPTION_SELECTORS,
    publish_page.DOUYIN_PUBLISH_SUBMIT_SELECTORS,
    publish_page.DOUYIN_PUBLISH_ACCOUNT_SELECTORS,
)


@pytest.mark.parametrize("group", UNIQUE_VISIBLE_GROUPS)
def test_every_deduplicated_group_stays_css_only(group: tuple[str, ...]) -> None:
    """Comma-joining a Playwright engine selector silently breaks the whole group.

    ``unique_visible`` joins its group into one selector, which only the CSS
    engine can parse. A group that mixes in ``text=`` or another engine prefix
    turns every probe of that page object into ``page_unavailable``.

    Playwright reaches a non-CSS engine three ways, and comma-joining breaks
    on all three: an explicit prefix, a bare XPath it auto-detects, and the
    ``>>`` chaining operator.
    """
    assert group
    for selector in group:
        assert not ENGINE_PREFIX.match(selector), selector
        assert not selector.lstrip().startswith(("//", "..")), selector
        assert ">>" not in selector, selector


class FakeLocator:
    def __init__(self, elements: list[bool], *, fail: bool = False) -> None:
        self.elements = elements
        self.fail = fail

    def locator(self, selector: str) -> FakeLocator:
        assert selector == VISIBLE_MATCH_ENGINE
        return FakeLocator([element for element in self.elements if element], fail=self.fail)

    def count(self) -> int:
        if self.fail:
            raise RuntimeError("private count failure")
        return len(self.elements)

    @property
    def first(self) -> FakeLocator:
        return self


class FakePage:
    def __init__(self, elements: list[bool], *, fail: bool = False) -> None:
        self.elements = elements
        self.fail = fail
        self.requested: list[str] = []

    def locator(self, selector: str) -> FakeLocator:
        self.requested.append(selector)
        return FakeLocator(self.elements, fail=self.fail)


def page(*visible: bool) -> Any:
    return FakePage(list(visible))


def test_a_hidden_first_match_never_hides_a_visible_later_match() -> None:
    """The regression that let a hidden placeholder mask a real challenge."""
    assert any_visible(page(False, True), SELECTORS) is True


def test_no_visible_match_reports_absent() -> None:
    assert any_visible(page(False, False), SELECTORS) is False
    assert any_visible(page(), SELECTORS) is False


def test_unique_visible_returns_the_single_visible_match() -> None:
    assert unique_visible(page(False, True), SELECTORS) is not None


def test_unique_visible_reports_absence_without_raising() -> None:
    assert unique_visible(page(False, False), SELECTORS) is None


def test_several_visible_matches_are_an_anchor_conflict() -> None:
    with pytest.raises(AnchorConflict):
        unique_visible(page(True, True), SELECTORS)


def test_hidden_duplicates_are_not_an_anchor_conflict() -> None:
    assert unique_visible(page(True, False, False), SELECTORS) is not None


def test_probe_failures_propagate_instead_of_reporting_absence() -> None:
    broken = FakePage([True], fail=True)
    with pytest.raises(RuntimeError):
        any_visible(cast(Any, broken), SELECTORS)


def test_invalid_counts_are_rejected() -> None:
    class InvalidCountLocator(FakeLocator):
        def locator(self, selector: str) -> FakeLocator:
            visible = super().locator(selector)
            visible.count = lambda: cast(int, "many")  # type: ignore[method-assign]
            return visible

    class InvalidCountPage(FakePage):
        def locator(self, selector: str) -> FakeLocator:
            return InvalidCountLocator(self.elements)

    with pytest.raises(ValueError):
        any_visible(cast(Any, InvalidCountPage([True])), SELECTORS)


def test_mixed_engine_groups_are_probed_one_selector_at_a_time() -> None:
    """Playwright engines such as `text=` cannot be comma-joined into one group."""
    probe = FakePage([False])
    any_visible(cast(Any, probe), SELECTORS)
    assert probe.requested == list(SELECTORS)


def test_anchor_uniqueness_uses_one_deduplicated_css_group() -> None:
    probe = FakePage([True])
    unique_visible(cast(Any, probe), SELECTORS)
    assert probe.requested == [", ".join(SELECTORS)]
