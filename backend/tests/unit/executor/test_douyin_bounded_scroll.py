from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any, cast

import pytest

import automation_tool.executor.rpa.douyin.bounded_scroll as scroll_module
from automation_tool.executor.browser_runtime import BrowserWindow
from automation_tool.executor.rpa.douyin.bounded_scroll import (
    DOUYIN_BOUNDED_SCROLL_VERSION,
    MAX_SCROLL_ROUNDS,
    DouyinBoundedScroll,
    DouyinBoundedScrollEvidence,
    DouyinBoundedScrollObservation,
    DouyinBoundedScrollRejected,
    DouyinBoundedScrollState,
)
from automation_tool.executor.rpa.douyin.page_version import douyin_search_results_url
from automation_tool.executor.rpa.douyin.search import (
    DouyinSearchExecutionEvidence,
    DouyinSearchExecutionObservation,
    DouyinSearchExecutionState,
)
from automation_tool.executor.rpa.douyin.search_page import (
    DouyinSearchPage,
    DouyinSearchPageObservation,
)
from automation_tool.protocol import DouyinSearchInput

RESULT_LIST = '[role="feed"]'
RESULT_ITEM = '[role="feed"] > article'


class FakeLocator:
    def __init__(self, page: FakePage, selector: str) -> None:
        self._page = page
        self._selector = selector

    @property
    def first(self) -> FakeLocator:
        return self

    def is_visible(self) -> bool:
        return any(
            candidate in self._page.visible_selectors for candidate in self._selector.split(", ")
        )

    def count(self) -> int:
        if self._page.count_failure:
            raise RuntimeError("private count failure")
        if self._selector == RESULT_ITEM:
            count = self._page.item_count
            if self._page.counts:
                count = self._page.counts.pop(0)
                self._page.item_count = count
            return count
        return 0


class FakeMouse:
    def __init__(self, page: FakePage) -> None:
        self._page = page

    def wheel(self, delta_x: float, delta_y: float) -> None:
        self._page.wheels.append((delta_x, delta_y))
        if self._page.wheel_failure:
            raise RuntimeError("private wheel failure")
        if self._page.on_wheel is not None:
            self._page.on_wheel()


class FakePage:
    def __init__(self, search: DouyinSearchInput, *, item_count: int = 0) -> None:
        self.url = douyin_search_results_url(search.keyword)
        self.visible_selectors: set[str] = {RESULT_LIST}
        self.item_count = item_count
        self.counts: list[int] = []
        self.count_failure = False
        self.wheel_failure = False
        self.wheels: list[tuple[float, float]] = []
        self.on_wheel: Callable[[], None] | None = None
        self.mouse = FakeMouse(self)

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector)


@pytest.fixture(autouse=True)
def virtual_time(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    now = [0.0]
    monkeypatch.setattr(scroll_module, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        scroll_module,
        "sleep",
        lambda seconds: now.__setitem__(0, now[0] + seconds),
    )
    yield


def request(*, target_limit: int = 20) -> DouyinSearchInput:
    return DouyinSearchInput(keyword="新能源汽车", target_limit=target_limit)


def succeeded_search() -> DouyinSearchExecutionObservation:
    return DouyinSearchExecutionObservation(
        state=DouyinSearchExecutionState.SUCCEEDED,
        evidence=DouyinSearchExecutionEvidence.RESULTS_READY,
    )


def window(page: FakePage) -> BrowserWindow:
    return BrowserWindow._for_runtime(object(), cast(Any, page))


def runner(
    page: FakePage,
    search: DouyinSearchInput,
    cancellation_requested: Callable[[], bool] = lambda: False,
) -> DouyinBoundedScroll:
    return DouyinBoundedScroll(
        window(page),
        search,
        succeeded_search(),
        cancellation_requested,
    )


def test_initial_target_limit_completes_without_scrolling() -> None:
    search = request(target_limit=3)
    page = FakePage(search, item_count=9)

    observation = runner(page, search).run()

    assert observation == DouyinBoundedScrollObservation(
        state=DouyinBoundedScrollState.COMPLETED,
        evidence=DouyinBoundedScrollEvidence.TARGET_LIMIT_REACHED,
        rounds_completed=0,
        target_count=3,
        target_limit=3,
    )
    assert observation.scroll_version == DOUYIN_BOUNDED_SCROLL_VERSION
    assert observation.completed is True
    assert observation.circuit_open is False
    assert "新能源汽车" not in repr(observation)
    assert page.wheels == []


def test_each_round_uses_one_fixed_wheel_until_target_limit() -> None:
    search = request(target_limit=5)
    page = FakePage(search, item_count=1)
    page.on_wheel = lambda: setattr(page, "item_count", page.item_count + 2)

    observation = runner(page, search).run()

    assert observation.evidence is DouyinBoundedScrollEvidence.TARGET_LIMIT_REACHED
    assert observation.rounds_completed == 2
    assert observation.target_count == 5
    assert page.wheels == [(0.0, 800.0), (0.0, 800.0)]


def test_no_new_results_stops_after_one_bounded_settle_window() -> None:
    search = request(target_limit=20)
    page = FakePage(search, item_count=2)

    observation = runner(page, search).run()

    assert observation.state is DouyinBoundedScrollState.COMPLETED
    assert observation.evidence is DouyinBoundedScrollEvidence.NO_NEW_RESULTS
    assert observation.rounds_completed == 1
    assert observation.target_count == 2
    assert len(page.wheels) == 1


def test_new_results_in_every_round_stops_at_fixed_round_limit() -> None:
    search = request(target_limit=100)
    page = FakePage(search, item_count=1)
    page.on_wheel = lambda: setattr(page, "item_count", page.item_count + 1)

    observation = runner(page, search).run()

    assert observation.state is DouyinBoundedScrollState.COMPLETED
    assert observation.evidence is DouyinBoundedScrollEvidence.ROUND_LIMIT_REACHED
    assert observation.rounds_completed == MAX_SCROLL_ROUNDS
    assert observation.target_count == MAX_SCROLL_ROUNDS + 1
    assert len(page.wheels) == MAX_SCROLL_ROUNDS


def test_slow_new_results_are_polled_without_extending_the_settle_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    search = request(target_limit=3)
    page = FakePage(search, item_count=1)
    sleeps = [0]
    now = [0.0]

    def sleep_and_publish(seconds: float) -> None:
        sleeps[0] += 1
        now[0] += seconds
        if sleeps[0] == 2:
            page.item_count = 3

    monkeypatch.setattr(scroll_module, "monotonic", lambda: now[0])
    monkeypatch.setattr(scroll_module, "sleep", sleep_and_publish)

    observation = runner(page, search).run()

    assert observation.evidence is DouyinBoundedScrollEvidence.TARGET_LIMIT_REACHED
    assert observation.rounds_completed == 1
    assert sleeps == [2]


def test_cancellation_is_checked_before_and_during_scroll_wait() -> None:
    search = request()
    page = FakePage(search, item_count=1)
    requested = iter((True,))
    before = runner(page, search, lambda: next(requested)).run()
    assert before.state is DouyinBoundedScrollState.CANCELLED
    assert before.evidence is DouyinBoundedScrollEvidence.CANCELLATION_REQUESTED
    assert before.rounds_completed == 0
    assert page.wheels == []

    waiting_page = FakePage(search, item_count=1)
    during = runner(waiting_page, search, lambda: len(waiting_page.wheels) >= 1).run()
    assert during.state is DouyinBoundedScrollState.CANCELLED
    assert during.rounds_completed == 1
    assert len(waiting_page.wheels) == 1


def test_cancellation_is_checked_after_growth_and_before_the_next_round() -> None:
    search = request()
    after_growth_page = FakePage(search, item_count=1)
    after_growth_page.on_wheel = lambda: setattr(after_growth_page, "item_count", 2)
    calls = [0]

    def after_growth_probe() -> bool:
        calls[0] += 1
        return calls[0] == 4

    after_growth = runner(after_growth_page, search, after_growth_probe).run()
    assert after_growth.state is DouyinBoundedScrollState.CANCELLED
    assert after_growth.rounds_completed == 1
    assert after_growth.target_count == 2

    before_next_page = FakePage(search, item_count=1)
    before_next_page.on_wheel = lambda: setattr(
        before_next_page,
        "item_count",
        before_next_page.item_count + 1,
    )
    next_calls = [0]

    def before_next_probe() -> bool:
        next_calls[0] += 1
        return next_calls[0] == 5

    before_next = runner(before_next_page, search, before_next_probe).run()
    assert before_next.state is DouyinBoundedScrollState.CANCELLED
    assert before_next.rounds_completed == 1
    assert len(before_next_page.wheels) == 1


@pytest.mark.parametrize(
    ("selectors", "state", "evidence"),
    (
        (
            {'[role="dialog"]:has-text("扫码登录")'},
            DouyinBoundedScrollState.BLOCKED,
            DouyinBoundedScrollEvidence.LOGIN_REQUIRED,
        ),
        (
            {'[role="dialog"]'},
            DouyinBoundedScrollState.BLOCKED,
            DouyinBoundedScrollEvidence.BLOCKING_DIALOG,
        ),
        (
            set(),
            DouyinBoundedScrollState.UNKNOWN,
            DouyinBoundedScrollEvidence.RESULTS_UNAVAILABLE,
        ),
    ),
)
def test_page_facts_are_rechecked_before_the_first_scroll(
    selectors: set[str],
    state: DouyinBoundedScrollState,
    evidence: DouyinBoundedScrollEvidence,
) -> None:
    search = request()
    page = FakePage(search, item_count=1)
    page.visible_selectors = selectors

    observation = runner(page, search).run()

    assert observation.state is state
    assert observation.evidence is evidence
    assert page.wheels == []


def test_count_decrease_or_browser_failure_stops_without_another_scroll() -> None:
    search = request()
    decreased_page = FakePage(search, item_count=3)
    decreased_page.counts = [3, 2]
    decreased = runner(decreased_page, search).run()
    assert decreased.state is DouyinBoundedScrollState.UNKNOWN
    assert decreased.evidence is DouyinBoundedScrollEvidence.RESULT_COUNT_DECREASED
    assert len(decreased_page.wheels) == 1

    wheel_page = FakePage(search, item_count=1)
    wheel_page.wheel_failure = True
    failed_wheel = runner(wheel_page, search).run()
    assert failed_wheel.evidence is DouyinBoundedScrollEvidence.PAGE_UNAVAILABLE
    assert len(wheel_page.wheels) == 1

    count_page = FakePage(search, item_count=1)
    count_page.count_failure = True
    failed_count = runner(count_page, search).run()
    assert failed_count.evidence is DouyinBoundedScrollEvidence.PAGE_UNAVAILABLE
    assert count_page.wheels == []


def test_page_or_count_failure_after_wheel_stops_at_that_round() -> None:
    search = request()
    blocked_page = FakePage(search, item_count=1)
    blocked_page.on_wheel = lambda: setattr(
        blocked_page,
        "visible_selectors",
        {'[role="dialog"]'},
    )
    blocked = runner(blocked_page, search).run()
    assert blocked.state is DouyinBoundedScrollState.BLOCKED
    assert blocked.evidence is DouyinBoundedScrollEvidence.BLOCKING_DIALOG
    assert blocked.rounds_completed == 1

    failed_page = FakePage(search, item_count=1)
    failed_page.on_wheel = lambda: setattr(failed_page, "count_failure", True)
    unavailable = runner(failed_page, search).run()
    assert unavailable.state is DouyinBoundedScrollState.UNKNOWN
    assert unavailable.evidence is DouyinBoundedScrollEvidence.PAGE_UNAVAILABLE
    assert unavailable.rounds_completed == 1


def test_page_observation_exception_is_redacted_and_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    search = request()
    page = FakePage(search, item_count=1)

    def fail_observation(_page: object) -> object:
        raise RuntimeError("private observation failure")

    monkeypatch.setattr(
        "automation_tool.executor.rpa.douyin.bounded_scroll.DouyinSearchPage.observe",
        fail_observation,
    )
    observation = runner(page, search).run()
    assert observation.state is DouyinBoundedScrollState.UNKNOWN
    assert observation.evidence is DouyinBoundedScrollEvidence.PAGE_UNAVAILABLE
    monkeypatch.undo()

    after_wheel_page = FakePage(search, item_count=1)
    original_observe = DouyinSearchPage.observe
    calls = [0]

    def fail_after_wheel(page_object: DouyinSearchPage) -> DouyinSearchPageObservation:
        calls[0] += 1
        if calls[0] == 3:
            raise RuntimeError("private later observation failure")
        return original_observe(page_object)

    monkeypatch.setattr(DouyinSearchPage, "observe", fail_after_wheel)
    after_wheel = runner(after_wheel_page, search).run()
    assert after_wheel.state is DouyinBoundedScrollState.UNKNOWN
    assert after_wheel.evidence is DouyinBoundedScrollEvidence.PAGE_UNAVAILABLE
    assert after_wheel.rounds_completed == 1


def test_invalid_cancellation_or_second_run_is_rejected_or_closed() -> None:
    search = request()
    page = FakePage(search, item_count=1)
    execution = runner(page, search)
    assert "新能源汽车" not in repr(execution)
    assert execution.run().completed
    with pytest.raises(DouyinBoundedScrollRejected, match="scroll is unavailable"):
        execution.run()

    unavailable = runner(page, search, cast(Callable[[], bool], lambda: "no")).run()
    assert unavailable.state is DouyinBoundedScrollState.UNKNOWN
    assert unavailable.evidence is DouyinBoundedScrollEvidence.CANCELLATION_UNAVAILABLE

    def failed_probe() -> bool:
        raise RuntimeError("private cancellation failure")

    failed = runner(page, search, failed_probe).run()
    assert failed.evidence is DouyinBoundedScrollEvidence.CANCELLATION_UNAVAILABLE


def test_constructor_and_observation_reject_forged_inputs() -> None:
    search = request()
    page = FakePage(search, item_count=1)
    failed_search = DouyinSearchExecutionObservation(
        state=DouyinSearchExecutionState.TIMED_OUT,
        evidence=DouyinSearchExecutionEvidence.RESULT_URL_TIMED_OUT,
    )
    invalid_arguments = (
        (cast(BrowserWindow, object()), search, succeeded_search(), lambda: False),
        (window(page), cast(DouyinSearchInput, object()), succeeded_search(), lambda: False),
        (window(page), search, failed_search, lambda: False),
        (window(page), search, succeeded_search(), cast(Callable[[], bool], object())),
    )
    for arguments in invalid_arguments:
        with pytest.raises(DouyinBoundedScrollRejected, match="scroll is unavailable"):
            DouyinBoundedScroll(*arguments)

    with pytest.raises(DouyinBoundedScrollRejected, match="scroll is unavailable"):
        DouyinBoundedScrollObservation(
            state=DouyinBoundedScrollState.COMPLETED,
            evidence=DouyinBoundedScrollEvidence.TARGET_LIMIT_REACHED,
            rounds_completed=1,
            target_count=1,
            target_limit=2,
        )

    invalid_observations = (
        {
            "state": cast(DouyinBoundedScrollState, "completed"),
            "evidence": DouyinBoundedScrollEvidence.TARGET_LIMIT_REACHED,
            "rounds_completed": 0,
            "target_count": 1,
            "target_limit": 1,
        },
        {
            "state": DouyinBoundedScrollState.COMPLETED,
            "evidence": cast(DouyinBoundedScrollEvidence, "target_limit_reached"),
            "rounds_completed": 0,
            "target_count": 1,
            "target_limit": 1,
        },
        {
            "state": DouyinBoundedScrollState.CANCELLED,
            "evidence": DouyinBoundedScrollEvidence.TARGET_LIMIT_REACHED,
            "rounds_completed": 0,
            "target_count": 1,
            "target_limit": 1,
        },
        {
            "state": DouyinBoundedScrollState.COMPLETED,
            "evidence": DouyinBoundedScrollEvidence.ROUND_LIMIT_REACHED,
            "rounds_completed": 19,
            "target_count": 1,
            "target_limit": 2,
        },
        {
            "state": DouyinBoundedScrollState.COMPLETED,
            "evidence": DouyinBoundedScrollEvidence.NO_NEW_RESULTS,
            "rounds_completed": 0,
            "target_count": 1,
            "target_limit": 2,
        },
    )
    for values in invalid_observations:
        with pytest.raises(DouyinBoundedScrollRejected):
            DouyinBoundedScrollObservation(**values)  # type: ignore[arg-type]
