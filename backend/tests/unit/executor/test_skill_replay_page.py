"""The real ReplayPage adapter's semantics, driven by a scripted page.

The adapter's contract is what the SA real-browser acceptance
(`tests/integration/test_skill_replay_embedded_browser.py`) relies on; these
tests pin each branch without a browser so the coverage gate holds:

* exactly-one-visible anchoring — zero, several, and hidden matches all refuse;
* bounded polling — conditions settle within the timeout or answer False;
* mid-navigation churn (driver errors) counts as "not yet", never as "absent";
* unknown vocabulary is a loud programming error, not a lookup miss.
"""

from __future__ import annotations

import time
from typing import cast

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page

from automation_tool.executor.skill_replay_page import PlaywrightReplayPage


class ScriptedLocator:
    def __init__(self, page: ScriptedPage, visibilities: list[bool]) -> None:
        self._page = page
        self._visibilities = visibilities
        self.actions: list[tuple[str, object]] = []

    def count(self) -> int:
        if self._page.churn_remaining > 0:
            self._page.churn_remaining -= 1
            raise PlaywrightError("Execution context was destroyed")
        return len(self._visibilities)

    def nth(self, index: int) -> ScriptedLocator:
        child = ScriptedLocator(self._page, [self._visibilities[index]])
        child.actions = self.actions
        return child

    def is_visible(self) -> bool:
        return self._visibilities[0]

    def click(self, *, timeout: float) -> None:
        self.actions.append(("click", timeout))

    def fill(self, value: str, *, timeout: float) -> None:
        self.actions.append(("fill", value))

    def press(self, key: str, *, timeout: float) -> None:
        self.actions.append(("press", key))

    def wait_for(self, *, state: str, timeout: float) -> None:
        self.actions.append(("wait_for", state))


class ScriptedMouse:
    def __init__(self) -> None:
        self.wheels: list[tuple[int, int]] = []

    def wheel(self, delta_x: int, delta_y: int) -> None:
        self.wheels.append((delta_x, delta_y))


class ScriptedPage:
    """Answers the exact Playwright surface the adapter touches."""

    def __init__(
        self, *, visibilities: list[bool], url: str = "https://www.douyin.com/portal"
    ) -> None:
        self.url = url
        self.mouse = ScriptedMouse()
        self.churn_remaining = 0
        self.locator = ScriptedLocator(self, visibilities)
        self.role_queries: list[tuple[str, str, bool]] = []
        self.waited_milliseconds = 0

    def get_by_role(self, role: str, *, name: str, exact: bool) -> ScriptedLocator:
        self.role_queries.append((role, name, exact))
        return self.locator

    def wait_for_timeout(self, milliseconds: float) -> None:
        self.waited_milliseconds += int(milliseconds)
        time.sleep(milliseconds / 1_000)


def adapter(page: ScriptedPage, *, timeout: int = 1) -> PlaywrightReplayPage:
    return PlaywrightReplayPage(cast(Page, page), action_timeout_seconds=timeout)


class TestConstruction:
    def test_the_timeout_must_stay_within_the_contract_bounds(self) -> None:
        page = ScriptedPage(visibilities=[True])
        with pytest.raises(ValueError, match="1..120"):
            PlaywrightReplayPage(cast(Page, page), action_timeout_seconds=0)
        with pytest.raises(ValueError, match="1..120"):
            PlaywrightReplayPage(cast(Page, page), action_timeout_seconds=121)


class TestFind:
    def test_exactly_one_visible_match_is_the_anchor(self) -> None:
        page = ScriptedPage(visibilities=[True])
        handle = adapter(page).find("button", "发布内容")
        assert handle is not None
        assert page.role_queries == [("button", "发布内容", True)]

    def test_zero_visible_matches_answer_none_after_the_deadline(self) -> None:
        page = ScriptedPage(visibilities=[False])
        assert adapter(page).find("button", "发布内容") is None
        assert page.waited_milliseconds > 0

    def test_several_visible_matches_are_ambiguity_not_an_anchor(self) -> None:
        page = ScriptedPage(visibilities=[True, True])
        assert adapter(page).find("button", "发布内容") is None

    def test_churn_is_retried_until_the_page_settles(self) -> None:
        page = ScriptedPage(visibilities=[True])
        page.churn_remaining = 2
        assert adapter(page).find("button", "发布内容") is not None

    def test_a_role_outside_the_vocabulary_fails_loud(self) -> None:
        page = ScriptedPage(visibilities=[True])
        with pytest.raises(ValueError, match="closed goal vocabulary"):
            adapter(page).find("banner", "页头")


class TestHolds:
    def test_url_matches_compares_the_current_path(self) -> None:
        page = ScriptedPage(visibilities=[True], url="https://www.douyin.com/a/b?q=1")
        assert adapter(page).holds("url_matches", pattern="/a/b") is True
        assert adapter(page).holds("url_matches", pattern="/other") is False

    def test_url_matches_needs_a_pattern(self) -> None:
        page = ScriptedPage(visibilities=[True])
        with pytest.raises(ValueError, match="pattern"):
            adapter(page).holds("url_matches")

    def test_element_visible_polls_until_it_holds(self) -> None:
        page = ScriptedPage(visibilities=[True])
        page.churn_remaining = 1
        assert adapter(page).holds("element_visible", role="dialog", name="发布成功")

    def test_element_visible_answers_false_at_the_deadline(self) -> None:
        page = ScriptedPage(visibilities=[False])
        assert (
            adapter(page).holds("element_visible", role="dialog", name="发布成功")
            is False
        )

    def test_element_absent_holds_only_with_zero_visible_matches(self) -> None:
        gone = ScriptedPage(visibilities=[False])
        assert adapter(gone).holds("element_absent", role="dialog", name="活动弹窗")
        present = ScriptedPage(visibilities=[True])
        assert (
            adapter(present).holds("element_absent", role="dialog", name="活动弹窗")
            is False
        )

    def test_element_conditions_need_role_and_name(self) -> None:
        page = ScriptedPage(visibilities=[True])
        with pytest.raises(ValueError, match="role and a name"):
            adapter(page).holds("element_visible", name="发布成功")
        with pytest.raises(ValueError, match="role and a name"):
            adapter(page).holds("element_absent", role="dialog")

    def test_an_unknown_condition_kind_fails_loud(self) -> None:
        page = ScriptedPage(visibilities=[True])
        with pytest.raises(ValueError, match="condition kind"):
            adapter(page).holds("text_matches", pattern="/x")


class TestAct:
    def test_click_fill_press_and_wait_run_on_the_handle(self) -> None:
        page = ScriptedPage(visibilities=[True])
        replay = adapter(page)
        handle = replay.find("button", "发布内容")
        replay.act("click", handle, None)
        replay.act("fill", handle, "验收标题")
        replay.act("press_key", handle, "Enter")
        replay.act("wait", handle, None)
        recorded = [action for action, _ in page.locator.actions]
        assert recorded == ["click", "fill", "press", "wait_for"]
        assert ("fill", "验收标题") in page.locator.actions
        assert ("press", "Enter") in page.locator.actions

    def test_scroll_moves_the_wheel_by_direction(self) -> None:
        page = ScriptedPage(visibilities=[True])
        replay = adapter(page)
        handle = replay.find("listitem", "更多作品")
        replay.act("scroll", handle, "down")
        replay.act("scroll", handle, "up")
        assert page.mouse.wheels == [(0, 600), (0, -600)]

    def test_payload_carrying_actions_refuse_a_missing_payload(self) -> None:
        page = ScriptedPage(visibilities=[True])
        replay = adapter(page)
        handle = replay.find("textbox", "标题内容")
        with pytest.raises(ValueError, match="parameter value"):
            replay.act("fill", handle, None)
        with pytest.raises(ValueError, match="key"):
            replay.act("press_key", handle, None)
        with pytest.raises(ValueError, match="direction"):
            replay.act("scroll", handle, "sideways")

    def test_an_unknown_action_kind_fails_loud(self) -> None:
        page = ScriptedPage(visibilities=[True])
        replay = adapter(page)
        handle = replay.find("button", "发布内容")
        with pytest.raises(ValueError, match="action kind"):
            replay.act("hover", handle, None)


class TestCurrentPath:
    def test_the_path_is_the_bare_url_path(self) -> None:
        page = ScriptedPage(visibilities=[True], url="https://www.douyin.com/a?s=1#f")
        assert adapter(page).current_path() == "/a"

    def test_an_empty_path_is_the_root(self) -> None:
        page = ScriptedPage(visibilities=[True], url="https://www.douyin.com")
        assert adapter(page).current_path() == "/"
