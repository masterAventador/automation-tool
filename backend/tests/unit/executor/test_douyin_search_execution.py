from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from automation_tool.executor.browser_runtime import BrowserWindow
from automation_tool.executor.rpa.douyin.page_version import (
    DOUYIN_HOME_URL,
    douyin_search_results_url,
)
from automation_tool.executor.rpa.douyin.search import (
    DOUYIN_SEARCH_EXECUTION_VERSION,
    DouyinSearchExecution,
    DouyinSearchExecutionEvidence,
    DouyinSearchExecutionObservation,
    DouyinSearchExecutionRejected,
    DouyinSearchExecutionState,
)
from automation_tool.protocol import DouyinSearchInput

SEARCH_INPUT = 'input[aria-label="搜索"]'
SEARCH_BUTTON = 'button[aria-label="搜索"]'
RESULT_LIST = '[role="feed"]'
LOGIN_DIALOG = '[role="dialog"]:has-text("扫码登录")'
BLOCKING_DIALOG = '[role="dialog"]'


class FakeLocator:
    def __init__(self, page: FakePage, selector: str) -> None:
        self._page = page
        self.selector = selector

    @property
    def first(self) -> FakeLocator:
        return self

    def is_visible(self) -> bool:
        if self._page.fail_visibility:
            raise RuntimeError("private visibility failure")
        return any(
            candidate in self._page.visible_selectors for candidate in self.selector.split(", ")
        )

    def wait_for(self, *, state: str, timeout: float) -> None:
        assert state == "visible"
        self._page.waits.append((self.selector, timeout))
        if self.selector in self._page.wait_failures:
            raise RuntimeError("private wait failure")
        callback = self._page.wait_callbacks.get(self.selector)
        if callback is not None:
            callback()
        if not self.is_visible():
            raise PlaywrightTimeoutError("private bounded wait")

    def fill(self, value: str, *, timeout: float) -> None:
        self._page.fills.append((self.selector, value, timeout))
        if self._page.fill_timeout:
            raise PlaywrightTimeoutError("private fill timeout")
        if self._page.action_failure:
            raise RuntimeError("private fill failure")

    def click(self, *, timeout: float, no_wait_after: bool) -> None:
        self._page.clicks.append((self.selector, timeout, no_wait_after))
        if self._page.click_timeout:
            raise PlaywrightTimeoutError("private click timeout")
        if self._page.action_failure:
            raise RuntimeError("private click failure")


class FakePage:
    def __init__(self) -> None:
        self.url = "about:blank"
        self.visible_selectors: set[str] = set()
        self.wait_callbacks: dict[str, Callable[[], None]] = {}
        self.wait_failures: set[str] = set()
        self.waits: list[tuple[str, float]] = []
        self.navigations: list[tuple[str, str, float]] = []
        self.fills: list[tuple[str, str, float]] = []
        self.clicks: list[tuple[str, float, bool]] = []
        self.url_waits: list[tuple[str, str, float]] = []
        self.goto_timeout = False
        self.goto_failure = False
        self.fill_timeout = False
        self.click_timeout = False
        self.action_failure = False
        self.url_timeout = False
        self.url_failure = False
        self.fail_visibility = False
        self.after_url_wait: Callable[[], None] | None = None

    def goto(self, url: str, *, wait_until: str, timeout: float) -> None:
        self.navigations.append((url, wait_until, timeout))
        if self.goto_timeout:
            raise PlaywrightTimeoutError("private navigation timeout")
        if self.goto_failure:
            raise RuntimeError("private navigation failure")
        self.url = url

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector)

    def wait_for_url(self, url: str, *, wait_until: str, timeout: float) -> None:
        self.url_waits.append((url, wait_until, timeout))
        if self.url_timeout:
            raise PlaywrightTimeoutError("private result URL timeout")
        if self.url_failure:
            raise RuntimeError("private URL wait failure")
        self.url = url
        self.visible_selectors.clear()
        if self.after_url_wait is not None:
            self.after_url_wait()


def window(page: FakePage) -> BrowserWindow:
    return BrowserWindow._for_runtime(object(), cast(Any, page))


def request() -> DouyinSearchInput:
    return DouyinSearchInput(keyword="新能源汽车", target_limit=20)


def ready_home(page: FakePage) -> None:
    page.visible_selectors = {SEARCH_INPUT, SEARCH_BUTTON}


def ready_results(page: FakePage) -> None:
    page.visible_selectors = {RESULT_LIST}


def test_search_uses_one_exact_bounded_action_sequence_and_page_object() -> None:
    page = FakePage()
    ready_home(page)
    page.after_url_wait = lambda: ready_results(page)

    observation = DouyinSearchExecution(window(page), request()).run()

    expected_url = douyin_search_results_url(request().keyword)
    assert page.navigations == [(DOUYIN_HOME_URL, "domcontentloaded", 30_000)]
    assert page.fills == [(SEARCH_INPUT, "新能源汽车", 15_000)]
    assert page.clicks == [(SEARCH_BUTTON, 15_000, True)]
    assert page.url_waits == [(expected_url, "domcontentloaded", 30_000)]
    assert observation == DouyinSearchExecutionObservation(
        state=DouyinSearchExecutionState.SUCCEEDED,
        evidence=DouyinSearchExecutionEvidence.RESULTS_READY,
    )
    assert observation.execution_version == DOUYIN_SEARCH_EXECUTION_VERSION
    assert observation.succeeded is True
    assert observation.circuit_open is False
    assert "新能源汽车" not in repr(observation)


def test_slow_home_and_result_anchors_are_waited_for_within_fixed_bounds() -> None:
    page = FakePage()
    input_group = (
        'input[aria-label="搜索"], input[placeholder="搜索"], [data-e2e="searchbar-input"]'
    )
    submit_group = (
        'button[aria-label="搜索"], [role="button"][aria-label="搜索"], '
        '[data-e2e="searchbar-button"]'
    )
    result_group = '[role="feed"], [data-e2e="search-result-list"], [data-e2e="scroll-list"]'
    page.wait_callbacks[input_group] = lambda: page.visible_selectors.add(SEARCH_INPUT)
    page.wait_callbacks[submit_group] = lambda: page.visible_selectors.add(SEARCH_BUTTON)

    def begin_slow_results() -> None:
        page.visible_selectors.clear()
        page.wait_callbacks[result_group] = lambda: page.visible_selectors.add(RESULT_LIST)

    page.after_url_wait = begin_slow_results

    observation = DouyinSearchExecution(window(page), request()).run()

    assert observation.state is DouyinSearchExecutionState.SUCCEEDED
    assert [selector for selector, _timeout in page.waits] == [
        input_group,
        submit_group,
        result_group,
    ]
    assert all(0 < timeout <= 10_000 for _selector, timeout in page.waits)


@pytest.mark.parametrize(
    ("failure", "evidence", "fills", "clicks"),
    (
        ("goto_timeout", DouyinSearchExecutionEvidence.NAVIGATION_TIMED_OUT, 0, 0),
        ("fill_timeout", DouyinSearchExecutionEvidence.ACTION_TIMED_OUT, 1, 0),
        ("click_timeout", DouyinSearchExecutionEvidence.ACTION_TIMED_OUT, 1, 1),
        ("url_timeout", DouyinSearchExecutionEvidence.RESULT_URL_TIMED_OUT, 1, 1),
    ),
)
def test_each_network_or_action_timeout_fails_closed_without_retry(
    failure: str,
    evidence: DouyinSearchExecutionEvidence,
    fills: int,
    clicks: int,
) -> None:
    page = FakePage()
    ready_home(page)
    setattr(page, failure, True)

    observation = DouyinSearchExecution(window(page), request()).run()

    assert observation.state is DouyinSearchExecutionState.TIMED_OUT
    assert observation.evidence is evidence
    assert observation.succeeded is False
    assert observation.circuit_open is True
    assert len(page.fills) == fills
    assert len(page.clicks) == clicks


def test_home_and_result_anchor_timeouts_are_distinct_and_do_not_retry() -> None:
    home_page = FakePage()
    home = DouyinSearchExecution(window(home_page), request()).run()
    assert home.state is DouyinSearchExecutionState.TIMED_OUT
    assert home.evidence is DouyinSearchExecutionEvidence.HOME_READY_TIMED_OUT
    assert home_page.fills == []
    assert home_page.clicks == []

    result_page = FakePage()
    ready_home(result_page)
    results = DouyinSearchExecution(window(result_page), request()).run()
    assert results.state is DouyinSearchExecutionState.TIMED_OUT
    assert results.evidence is DouyinSearchExecutionEvidence.RESULTS_READY_TIMED_OUT
    assert len(result_page.clicks) == 1


@pytest.mark.parametrize(
    ("selectors", "state", "evidence"),
    (
        (
            {LOGIN_DIALOG, BLOCKING_DIALOG},
            DouyinSearchExecutionState.LOGIN_REQUIRED,
            DouyinSearchExecutionEvidence.LOGIN_REQUIRED,
        ),
        (
            {BLOCKING_DIALOG},
            DouyinSearchExecutionState.DIALOG_BLOCKED,
            DouyinSearchExecutionEvidence.BLOCKING_DIALOG,
        ),
    ),
)
def test_login_or_blocking_dialog_stops_before_any_search_action(
    selectors: set[str],
    state: DouyinSearchExecutionState,
    evidence: DouyinSearchExecutionEvidence,
) -> None:
    page = FakePage()
    page.visible_selectors = selectors

    observation = DouyinSearchExecution(window(page), request()).run()

    assert observation.state is state
    assert observation.evidence is evidence
    assert page.fills == []
    assert page.clicks == []


def test_unknown_page_or_dynamic_anchor_failure_stops_without_second_click() -> None:
    page = FakePage()
    ready_home(page)
    page.action_failure = True

    observation = DouyinSearchExecution(window(page), request()).run()

    assert observation.state is DouyinSearchExecutionState.UNKNOWN
    assert observation.evidence is DouyinSearchExecutionEvidence.PAGE_UNAVAILABLE
    assert len(page.fills) == 1
    assert page.clicks == []

    unknown_page = FakePage()
    ready_home(unknown_page)
    unknown_page.after_url_wait = lambda: ready_results(unknown_page)
    expected = douyin_search_results_url(request().keyword)

    def drift_url(url: str, *, wait_until: str, timeout: float) -> None:
        unknown_page.url_waits.append((url, wait_until, timeout))
        unknown_page.url = "https://www.douyin.com/live"

    unknown_page.wait_for_url = drift_url  # type: ignore[method-assign]
    unknown = DouyinSearchExecution(window(unknown_page), request()).run()
    assert expected in unknown_page.url_waits[0]
    assert unknown.state is DouyinSearchExecutionState.UNKNOWN
    assert unknown.evidence is DouyinSearchExecutionEvidence.PAGE_VERSION_UNKNOWN
    assert len(unknown_page.clicks) == 1


@pytest.mark.parametrize("failure", ("goto_failure", "url_failure"))
def test_unexpected_navigation_failures_are_redacted_and_closed(failure: str) -> None:
    page = FakePage()
    ready_home(page)
    setattr(page, failure, True)

    observation = DouyinSearchExecution(window(page), request()).run()

    assert observation.state is DouyinSearchExecutionState.UNKNOWN
    assert observation.evidence is DouyinSearchExecutionEvidence.PAGE_UNAVAILABLE
    assert "private" not in repr(observation)


def test_conflicting_or_unavailable_home_facts_are_closed_before_actions() -> None:
    conflicting = FakePage()
    conflicting.visible_selectors = {SEARCH_INPUT, SEARCH_BUTTON, RESULT_LIST}
    conflict = DouyinSearchExecution(window(conflicting), request()).run()
    assert conflict.state is DouyinSearchExecutionState.UNKNOWN
    assert conflict.evidence is DouyinSearchExecutionEvidence.CONFLICTING_ANCHORS
    assert conflicting.clicks == []

    failed = FakePage()
    failed.fail_visibility = True
    unavailable = DouyinSearchExecution(window(failed), request()).run()
    assert unavailable.evidence is DouyinSearchExecutionEvidence.PAGE_UNAVAILABLE
    assert failed.clicks == []


def test_final_result_anchor_is_rechecked_before_success(monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage()
    ready_home(page)
    page.after_url_wait = lambda: ready_results(page)

    def reject_result(_page_object: object) -> None:
        raise RuntimeError("private disappearing result")

    monkeypatch.setattr(
        "automation_tool.executor.rpa.douyin.search_page.DouyinSearchPage.result_list",
        reject_result,
    )
    observation = DouyinSearchExecution(window(page), request()).run()
    assert observation.state is DouyinSearchExecutionState.UNKNOWN
    assert observation.evidence is DouyinSearchExecutionEvidence.PAGE_UNAVAILABLE


def test_execution_rejects_raw_input_forged_observation_and_second_run() -> None:
    page = FakePage()
    ready_home(page)
    page.after_url_wait = lambda: ready_results(page)
    execution = DouyinSearchExecution(window(page), request())
    assert repr(execution) == "DouyinSearchExecution(<redacted>)"
    assert execution.run().succeeded

    with pytest.raises(DouyinSearchExecutionRejected, match="execution is unavailable"):
        execution.run()
    with pytest.raises(DouyinSearchExecutionRejected, match="execution is unavailable"):
        DouyinSearchExecution(window(page), cast(DouyinSearchInput, "keyword"))
    with pytest.raises(DouyinSearchExecutionRejected, match="execution is unavailable"):
        DouyinSearchExecution(cast(BrowserWindow, object()), request())
    with pytest.raises(DouyinSearchExecutionRejected, match="execution is unavailable"):
        DouyinSearchExecutionObservation(
            state=DouyinSearchExecutionState.SUCCEEDED,
            evidence=DouyinSearchExecutionEvidence.PAGE_UNAVAILABLE,
        )
