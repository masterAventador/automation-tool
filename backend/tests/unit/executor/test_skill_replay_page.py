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


def _element(
    visible: bool, box: dict[str, float] | None
) -> dict[str, object]:
    return {"visible": visible, "box": box}


class ScriptedLocator:
    def __init__(
        self, page: ScriptedPage, elements: list[dict[str, object]], kind: str = "role"
    ) -> None:
        self._page = page
        self._elements = elements
        self._kind = kind
        self.actions: list[tuple[str, object]] = []

    def count(self) -> int:
        if self._kind == "role" and self._page.churn_remaining > 0:
            self._page.churn_remaining -= 1
            raise PlaywrightError("Execution context was destroyed")
        if self._kind == "text" and self._page.text_churn_remaining > 0:
            self._page.text_churn_remaining -= 1
            raise PlaywrightError("Execution context was destroyed")
        return len(self._elements)

    def nth(self, index: int) -> ScriptedLocator:
        child = ScriptedLocator(self._page, [self._elements[index]], self._kind)
        child.actions = self.actions
        return child

    def is_visible(self) -> bool:
        return bool(self._elements[0]["visible"])

    def bounding_box(self) -> dict[str, float] | None:
        # 盒抖动只作用于 role 锚点：让 _box_of 的 except 分支被真正踩到，
        # 而不是被更早的文字上下文 bounding_box 抢先消费。
        if self._kind == "role" and self._page.box_churn_remaining > 0:
            self._page.box_churn_remaining -= 1
            raise PlaywrightError("Execution context was destroyed")
        box = self._elements[0]["box"]
        return box if box is None or isinstance(box, dict) else None

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


def _default_box(index: int) -> dict[str, float]:
    return {"x": 0.0, "y": 100.0 * index, "width": 50.0, "height": 20.0}


class ScriptedPage:
    """Answers the exact Playwright surface the adapter touches."""

    def __init__(
        self,
        *,
        visibilities: list[bool],
        boxes: list[dict[str, float] | None] | None = None,
        texts: dict[str, list[dict[str, object]]] | None = None,
        url: str = "https://www.douyin.com/portal",
    ) -> None:
        self.url = url
        self.mouse = ScriptedMouse()
        self.churn_remaining = 0
        self.text_churn_remaining = 0
        self.box_churn_remaining = 0
        resolved_boxes = boxes or [_default_box(index) for index in range(len(visibilities))]
        self.locator = ScriptedLocator(
            self,
            [
                _element(visible, box)
                for visible, box in zip(visibilities, resolved_boxes, strict=True)
            ],
        )
        self.texts = texts or {}
        self.role_queries: list[tuple[str, str, bool]] = []
        self.waited_milliseconds = 0

    def get_by_role(self, role: str, *, name: str, exact: bool) -> ScriptedLocator:
        self.role_queries.append((role, name, exact))
        return self.locator

    def get_by_text(self, text: str) -> ScriptedLocator:
        return ScriptedLocator(self, self.texts.get(text, []), "text")

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


CONTEXT_BOX: dict[str, float] = {"x": 100.0, "y": 100.0, "width": 20.0, "height": 10.0}


def _near(page: ScriptedPage, relative_position: str | None = None) -> object | None:
    return adapter(page).find(
        "link", "查看更多", near_text="热榜", relative_position=relative_position
    )


class TestNearTextDisambiguation:
    """真实门户 8%~21% 的链接名重复；nearText 是把它们区分开的语义上下文。"""

    def test_the_strictly_closest_qualifier_wins(self) -> None:
        page = ScriptedPage(
            visibilities=[True, True],
            boxes=[
                {"x": 100.0, "y": 400.0, "width": 20.0, "height": 10.0},  # 远
                {"x": 100.0, "y": 130.0, "width": 20.0, "height": 10.0},  # 近
            ],
            texts={"热榜": [_element(True, CONTEXT_BOX)]},
        )
        handle = _near(page)
        assert handle is not None
        assert handle.bounding_box() == {"x": 100.0, "y": 130.0, "width": 20.0, "height": 10.0}

    def test_an_exact_tie_is_ambiguity_not_a_coin_toss(self) -> None:
        page = ScriptedPage(
            visibilities=[True, True],
            boxes=[
                # 上下文中心 y=105；两候选中心 y=145 与 y=65，距离同为 40 像素。
                {"x": 100.0, "y": 140.0, "width": 20.0, "height": 10.0},
                {"x": 100.0, "y": 60.0, "width": 20.0, "height": 10.0},
            ],
            texts={"热榜": [_element(True, CONTEXT_BOX)]},
        )
        assert _near(page) is None

    def test_a_unique_candidate_out_of_context_refuses(self) -> None:
        # 上下文文字不在页面上：goal 承诺「在这段文字附近」，文字没了就是漂移，
        # 即使候选唯一也不能点。
        page = ScriptedPage(visibilities=[True], texts={})
        assert _near(page) is None

    def test_invisible_or_boxless_context_occurrences_do_not_count(self) -> None:
        page = ScriptedPage(
            visibilities=[True],
            texts={"热榜": [_element(False, CONTEXT_BOX), _element(True, None)]},
        )
        assert _near(page) is None

    def test_a_boxless_candidate_cannot_qualify(self) -> None:
        page = ScriptedPage(
            visibilities=[True],
            boxes=[None],
            texts={"热榜": [_element(True, CONTEXT_BOX)]},
        )
        assert _near(page) is None

    def test_a_direction_filter_leaves_the_single_qualifier(self) -> None:
        page = ScriptedPage(
            visibilities=[True, True],
            boxes=[
                {"x": 100.0, "y": 60.0, "width": 20.0, "height": 10.0},  # above — 违约
                {"x": 100.0, "y": 400.0, "width": 20.0, "height": 10.0},  # below — 合约
            ],
            texts={"热榜": [_element(True, CONTEXT_BOX)]},
        )
        handle = _near(page, "below")
        assert handle is not None
        assert handle.bounding_box() == {"x": 100.0, "y": 400.0, "width": 20.0, "height": 10.0}

    @pytest.mark.parametrize(
        ("relative_position", "satisfying", "violating"),
        [
            ("above", {"x": 100.0, "y": 40.0}, {"x": 100.0, "y": 400.0}),
            ("below", {"x": 100.0, "y": 400.0}, {"x": 100.0, "y": 40.0}),
            ("left_of", {"x": 20.0, "y": 100.0}, {"x": 400.0, "y": 100.0}),
            ("right_of", {"x": 400.0, "y": 100.0}, {"x": 20.0, "y": 100.0}),
        ],
    )
    def test_each_direction_admits_and_rejects(
        self,
        relative_position: str,
        satisfying: dict[str, float],
        violating: dict[str, float],
    ) -> None:
        def boxed(position: dict[str, float]) -> dict[str, float]:
            return {**position, "width": 20.0, "height": 10.0}

        winner = ScriptedPage(
            visibilities=[True],
            boxes=[boxed(satisfying)],
            texts={"热榜": [_element(True, CONTEXT_BOX)]},
        )
        assert _near(winner, relative_position) is not None
        loser = ScriptedPage(
            visibilities=[True],
            boxes=[boxed(violating)],
            texts={"热榜": [_element(True, CONTEXT_BOX)]},
        )
        assert _near(loser, relative_position) is None

    def test_inside_requires_full_containment(self) -> None:
        container: dict[str, float] = {"x": 0.0, "y": 0.0, "width": 300.0, "height": 300.0}
        inside = ScriptedPage(
            visibilities=[True],
            boxes=[{"x": 10.0, "y": 10.0, "width": 20.0, "height": 10.0}],
            texts={"热榜": [_element(True, container)]},
        )
        assert _near(inside, "inside") is not None
        overflowing = ScriptedPage(
            visibilities=[True],
            boxes=[{"x": 290.0, "y": 10.0, "width": 20.0, "height": 10.0}],
            texts={"热榜": [_element(True, container)]},
        )
        assert _near(overflowing, "inside") is None

    def test_a_relative_position_without_near_text_is_unverifiable(self) -> None:
        # 拒绝而不是无声忽略：方向约束没有锚定对象就无法验证。
        page = ScriptedPage(visibilities=[True])
        assert (
            adapter(page).find(
                "link", "查看更多", near_text=None, relative_position="below"
            )
            is None
        )

    def test_a_relative_position_outside_the_vocabulary_fails_loud(self) -> None:
        page = ScriptedPage(visibilities=[True])
        with pytest.raises(ValueError, match="closed vocabulary"):
            adapter(page).find(
                "link", "查看更多", near_text="热榜", relative_position="behind"
            )

    def test_text_lookup_churn_is_retried_until_it_settles(self) -> None:
        page = ScriptedPage(
            visibilities=[True],
            boxes=[{"x": 100.0, "y": 130.0, "width": 20.0, "height": 10.0}],
            texts={"热榜": [_element(True, CONTEXT_BOX)]},
        )
        page.text_churn_remaining = 1
        assert _near(page) is not None

    def test_candidate_box_churn_is_retried_until_it_settles(self) -> None:
        page = ScriptedPage(
            visibilities=[True],
            boxes=[{"x": 100.0, "y": 130.0, "width": 20.0, "height": 10.0}],
            texts={"热榜": [_element(True, CONTEXT_BOX)]},
        )
        page.box_churn_remaining = 1
        assert _near(page) is not None


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
