from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

import automation_tool.executor.rpa.douyin.search_page as search_page_module
from automation_tool.executor.browser_runtime import BrowserWindow
from automation_tool.executor.rpa.douyin.page_version import (
    DOUYIN_HOME_URL,
    DOUYIN_SEARCH_ENTRY_URL,
    DOUYIN_SESSION_PROBE_URL,
    DouyinPageEntry,
    DouyinPageVersion,
)
from automation_tool.executor.rpa.douyin.search_page import (
    DOUYIN_SEARCH_PAGE_SELECTOR_VERSION,
    DouyinSearchPage,
    DouyinSearchPageEvidence,
    DouyinSearchPageObservation,
    DouyinSearchPageRejected,
    DouyinSearchPageState,
)

SEARCH_RESULTS_URL = f"{DOUYIN_SEARCH_ENTRY_URL}/keyword?type=general"
SEARCH_INPUT = 'input[aria-label="搜索"]'
SEARCH_BUTTON = 'button[aria-label="搜索"]'
RESULT_LIST = '[role="feed"]'
RESULT_ITEM = '[role="feed"] > article'
RESULT_ITEM_FALLBACK = '[data-e2e="search-result-item"]'
LOGIN_DIALOG = '[role="dialog"]:has-text("扫码登录")'
BLOCKING_DIALOG = '[role="dialog"]'
RISK_CHALLENGE = 'iframe[src^="https://rmc.bytedance.com/verifycenter/captcha/"]'
VIDEO_DETAIL_URL = "https://www.douyin.com/video/7351234567890123456"


class FakeLocator:
    def __init__(
        self,
        selector: str,
        *,
        visible: bool,
        fail: bool = False,
        wait_callback: Callable[[], None] | None = None,
        wait_failure: bool = False,
        selector_counts: dict[str, object] | None = None,
        failed_count_selectors: set[str] | None = None,
    ) -> None:
        self.selector = selector
        self.visible = visible
        self.fail = fail
        self.wait_callback = wait_callback
        self.wait_failure = wait_failure
        self._selector_counts = {} if selector_counts is None else selector_counts
        self._failed_count_selectors = (
            set() if failed_count_selectors is None else failed_count_selectors
        )

    @property
    def first(self) -> FakeLocator:
        return self

    def is_visible(self) -> bool:
        if self.fail:
            raise RuntimeError("private page failure")
        return self.visible

    def wait_for(self, *, state: str, timeout: float) -> None:
        assert state == "visible"
        assert timeout > 0
        if self.wait_failure:
            raise RuntimeError("private wait failure")
        if self.wait_callback is not None:
            self.wait_callback()
            return
        if not self.visible:
            raise PlaywrightTimeoutError("private wait timeout")

    def count(self) -> int:
        if self.selector in self._failed_count_selectors:
            raise RuntimeError("private count failure")
        return cast(int, self._selector_counts.get(self.selector, 0))


class FakePage:
    def __init__(
        self,
        *,
        url: str = DOUYIN_HOME_URL,
        visible_selectors: set[str] | None = None,
        failed_selectors: set[str] | None = None,
    ) -> None:
        self.url = url
        self.visible_selectors = set() if visible_selectors is None else visible_selectors
        self.failed_selectors = set() if failed_selectors is None else failed_selectors
        self.requested_selectors: list[str] = []
        self.wait_callbacks: dict[str, Callable[[], None]] = {}
        self.wait_failure_selectors: set[str] = set()
        self.selector_counts: dict[str, object] = {}
        self.failed_count_selectors: set[str] = set()

    def locator(self, selector: str) -> FakeLocator:
        self.requested_selectors.append(selector)
        return FakeLocator(
            selector,
            visible=selector in self.visible_selectors
            or any(candidate in self.visible_selectors for candidate in selector.split(", ")),
            fail=selector in self.failed_selectors,
            wait_callback=self.wait_callbacks.get(selector),
            wait_failure=selector in self.wait_failure_selectors,
            selector_counts=self.selector_counts,
            failed_count_selectors=self.failed_count_selectors,
        )


class ChangingAnchorPage(FakePage):
    def __init__(self, *, fail_after_observation: bool) -> None:
        super().__init__(visible_selectors={SEARCH_INPUT, SEARCH_BUTTON})
        self.fail_after_observation = fail_after_observation
        self._input_requests = 0

    def locator(self, selector: str) -> FakeLocator:
        if selector == SEARCH_INPUT:
            self._input_requests += 1
            if self._input_requests > 1:
                return FakeLocator(
                    selector,
                    visible=False,
                    fail=self.fail_after_observation,
                )
        return super().locator(selector)


class FailingSecondUrlPage(FakePage):
    def __init__(self) -> None:
        self._url = DOUYIN_HOME_URL
        self._url_reads = 0
        super().__init__()
        self._url = DOUYIN_HOME_URL

    @property
    def url(self) -> str:
        self._url_reads += 1
        if self._url_reads > 1:
            raise RuntimeError("private URL failure")
        return self._url

    @url.setter
    def url(self, value: str) -> None:
        self._url = value


def window(page: FakePage) -> BrowserWindow:
    return BrowserWindow._for_runtime(object(), cast(Any, page))


@pytest.mark.parametrize(
    ("url", "selectors", "entry", "state", "evidence"),
    (
        (
            DOUYIN_HOME_URL,
            {SEARCH_INPUT, SEARCH_BUTTON},
            DouyinPageEntry.HOME,
            DouyinSearchPageState.HOME_READY,
            DouyinSearchPageEvidence.SEARCH_ENTRY_VISIBLE,
        ),
        (
            SEARCH_RESULTS_URL,
            {RESULT_LIST},
            DouyinPageEntry.SEARCH_RESULTS,
            DouyinSearchPageState.RESULTS_READY,
            DouyinSearchPageEvidence.RESULT_LIST_VISIBLE,
        ),
    ),
)
def test_known_route_and_matching_anchors_are_ready(
    url: str,
    selectors: set[str],
    entry: DouyinPageEntry,
    state: DouyinSearchPageState,
    evidence: DouyinSearchPageEvidence,
) -> None:
    observation = DouyinSearchPage(window(FakePage(url=url, visible_selectors=selectors))).observe()

    assert observation == DouyinSearchPageObservation(
        page_version=DouyinPageVersion.WEB_V1,
        entry=entry,
        state=state,
        evidence=evidence,
    )
    assert observation.selector_version == DOUYIN_SEARCH_PAGE_SELECTOR_VERSION
    assert observation.ready is True
    assert observation.circuit_open is False


def test_accessors_return_only_visible_versioned_anchors() -> None:
    home_page = FakePage(visible_selectors={SEARCH_INPUT, SEARCH_BUTTON})
    home = DouyinSearchPage(window(home_page))
    assert cast(FakeLocator, home.search_input()).selector == SEARCH_INPUT
    assert cast(FakeLocator, home.search_submit()).selector == SEARCH_BUTTON

    result_page = FakePage(url=SEARCH_RESULTS_URL, visible_selectors={RESULT_LIST})
    results = DouyinSearchPage(window(result_page))
    assert cast(FakeLocator, results.result_list()).selector == RESULT_LIST


def test_session_entry_is_a_login_redirect_without_dom_guessing() -> None:
    page = FakePage(url=DOUYIN_SESSION_PROBE_URL)

    observation = DouyinSearchPage(window(page)).observe()

    assert observation.state is DouyinSearchPageState.LOGIN_REQUIRED
    assert observation.evidence is DouyinSearchPageEvidence.LOGIN_REDIRECT
    assert observation.entry is DouyinPageEntry.SESSION_PROBE
    assert observation.circuit_open is True
    assert page.requested_selectors == []


def test_video_detail_is_known_but_never_mistaken_for_a_search_page() -> None:
    observation = DouyinSearchPage(window(FakePage(url=VIDEO_DETAIL_URL))).observe()

    assert observation.page_version is DouyinPageVersion.WEB_V1
    assert observation.entry is DouyinPageEntry.VIDEO_DETAIL
    assert observation.state is DouyinSearchPageState.UNKNOWN
    assert observation.evidence is DouyinSearchPageEvidence.REQUIRED_ANCHOR_MISSING
    assert observation.circuit_open is True


@pytest.mark.parametrize("url", (DOUYIN_HOME_URL, SEARCH_RESULTS_URL))
def test_login_dialog_takes_priority_over_its_generic_dialog_shell(url: str) -> None:
    page = FakePage(url=url, visible_selectors={LOGIN_DIALOG, BLOCKING_DIALOG})
    search_page = DouyinSearchPage(window(page))

    observation = search_page.observe()

    assert observation.state is DouyinSearchPageState.LOGIN_REQUIRED
    assert observation.evidence is DouyinSearchPageEvidence.LOGIN_DIALOG
    assert cast(FakeLocator, search_page.login_dialog()).selector == LOGIN_DIALOG
    with pytest.raises(DouyinSearchPageRejected):
        search_page.blocking_dialog()


@pytest.mark.parametrize("url", (DOUYIN_HOME_URL, SEARCH_RESULTS_URL))
@pytest.mark.parametrize("blocking_selector", (BLOCKING_DIALOG, RISK_CHALLENGE))
def test_non_login_dialog_or_risk_challenge_blocks_every_page_anchor(
    url: str,
    blocking_selector: str,
) -> None:
    page = FakePage(
        url=url,
        visible_selectors={SEARCH_INPUT, SEARCH_BUTTON, RESULT_LIST, blocking_selector},
    )
    search_page = DouyinSearchPage(window(page))

    observation = search_page.observe()

    assert observation.state is DouyinSearchPageState.DIALOG_BLOCKED
    assert observation.evidence is DouyinSearchPageEvidence.BLOCKING_DIALOG
    assert cast(FakeLocator, search_page.blocking_dialog()).selector == blocking_selector


@pytest.mark.parametrize(
    ("url", "selectors", "evidence"),
    (
        (DOUYIN_HOME_URL, set(), DouyinSearchPageEvidence.REQUIRED_ANCHOR_MISSING),
        (DOUYIN_HOME_URL, {SEARCH_INPUT}, DouyinSearchPageEvidence.REQUIRED_ANCHOR_MISSING),
        (SEARCH_RESULTS_URL, set(), DouyinSearchPageEvidence.REQUIRED_ANCHOR_MISSING),
        (
            DOUYIN_HOME_URL,
            {SEARCH_INPUT, SEARCH_BUTTON, RESULT_LIST},
            DouyinSearchPageEvidence.CONFLICTING_ANCHORS,
        ),
        (
            SEARCH_RESULTS_URL,
            {SEARCH_INPUT, SEARCH_BUTTON, RESULT_LIST},
            DouyinSearchPageEvidence.CONFLICTING_ANCHORS,
        ),
    ),
)
def test_missing_or_route_conflicting_anchors_fail_closed(
    url: str,
    selectors: set[str],
    evidence: DouyinSearchPageEvidence,
) -> None:
    observation = DouyinSearchPage(window(FakePage(url=url, visible_selectors=selectors))).observe()

    assert observation.state is DouyinSearchPageState.UNKNOWN
    assert observation.evidence is evidence
    assert observation.ready is False
    assert observation.circuit_open is True


def test_unknown_page_version_fails_before_any_selector_query() -> None:
    page = FakePage(url="https://www.douyin.com/live", visible_selectors={SEARCH_INPUT})

    observation = DouyinSearchPage(window(page)).observe()

    assert observation.page_version is DouyinPageVersion.UNKNOWN
    assert observation.entry is DouyinPageEntry.UNKNOWN
    assert observation.evidence is DouyinSearchPageEvidence.PAGE_VERSION_UNKNOWN
    assert page.requested_selectors == []


def test_locator_failure_is_safe_and_does_not_reflect_page_details() -> None:
    page = FakePage(visible_selectors={SEARCH_INPUT}, failed_selectors={SEARCH_BUTTON})

    observation = DouyinSearchPage(window(page)).observe()

    assert observation.state is DouyinSearchPageState.UNKNOWN
    assert observation.evidence is DouyinSearchPageEvidence.PAGE_UNAVAILABLE
    assert "private" not in repr(observation)


def test_accessors_reject_wrong_page_state_and_non_runtime_window() -> None:
    page = DouyinSearchPage(window(FakePage()))

    for accessor in (
        page.search_input,
        page.search_submit,
        page.result_list,
        page.login_dialog,
        page.blocking_dialog,
    ):
        with pytest.raises(DouyinSearchPageRejected, match="search page is unavailable"):
            accessor()
    with pytest.raises(DouyinSearchPageRejected, match="search page is unavailable"):
        DouyinSearchPage(cast(BrowserWindow, object()))


@pytest.mark.parametrize("fail_after_observation", (False, True))
def test_accessor_fails_closed_when_anchor_changes_after_observation(
    fail_after_observation: bool,
) -> None:
    search_page = DouyinSearchPage(
        window(ChangingAnchorPage(fail_after_observation=fail_after_observation))
    )

    with pytest.raises(DouyinSearchPageRejected, match="search page is unavailable"):
        search_page.search_input()


@pytest.mark.parametrize(
    "values",
    (
        {
            "page_version": cast(DouyinPageVersion, "douyin.web.v1"),
            "entry": DouyinPageEntry.HOME,
            "state": DouyinSearchPageState.HOME_READY,
            "evidence": DouyinSearchPageEvidence.SEARCH_ENTRY_VISIBLE,
        },
        {
            "page_version": DouyinPageVersion.WEB_V1,
            "entry": DouyinPageEntry.SEARCH_RESULTS,
            "state": DouyinSearchPageState.HOME_READY,
            "evidence": DouyinSearchPageEvidence.SEARCH_ENTRY_VISIBLE,
        },
        {
            "page_version": DouyinPageVersion.UNKNOWN,
            "entry": DouyinPageEntry.UNKNOWN,
            "state": DouyinSearchPageState.RESULTS_READY,
            "evidence": DouyinSearchPageEvidence.RESULT_LIST_VISIBLE,
        },
        {
            "page_version": DouyinPageVersion.WEB_V1,
            "entry": DouyinPageEntry.HOME,
            "state": DouyinSearchPageState.UNKNOWN,
            "evidence": DouyinSearchPageEvidence.PAGE_VERSION_UNKNOWN,
        },
    ),
)
def test_observation_rejects_forged_version_entry_state_or_evidence(
    values: dict[str, object],
) -> None:
    with pytest.raises(DouyinSearchPageRejected, match="search page is unavailable"):
        DouyinSearchPageObservation(**values)  # type: ignore[arg-type]


def test_page_object_repr_is_redacted() -> None:
    search_page = DouyinSearchPage(window(FakePage(url="https://www.douyin.com/private-value")))

    assert repr(search_page) == "DouyinSearchPage(<redacted>)"
    assert "private-value" not in repr(search_page.observe())


def test_result_item_count_is_bounded_and_uses_versioned_fallbacks() -> None:
    page = FakePage(url=SEARCH_RESULTS_URL, visible_selectors={RESULT_LIST})
    search_page = DouyinSearchPage(window(page))
    assert search_page.result_item_count(maximum=20) == 0

    page.selector_counts[RESULT_ITEM_FALLBACK] = 12
    assert search_page.result_item_count(maximum=5) == 5
    assert RESULT_ITEM in page.requested_selectors
    assert RESULT_ITEM_FALLBACK in page.requested_selectors


def test_result_item_count_rejects_invalid_bounds_counts_or_page_state() -> None:
    result_page = FakePage(url=SEARCH_RESULTS_URL, visible_selectors={RESULT_LIST})
    search_page = DouyinSearchPage(window(result_page))
    for maximum in (0, 101, True):
        with pytest.raises(DouyinSearchPageRejected):
            search_page.result_item_count(maximum=maximum)

    for invalid_count in (True, -1):
        result_page.selector_counts[RESULT_ITEM] = invalid_count
        with pytest.raises(DouyinSearchPageRejected):
            search_page.result_item_count(maximum=20)
    result_page.selector_counts.clear()
    result_page.failed_count_selectors.add(RESULT_ITEM)
    with pytest.raises(DouyinSearchPageRejected):
        search_page.result_item_count(maximum=20)

    home_page = DouyinSearchPage(window(FakePage(visible_selectors={SEARCH_INPUT, SEARCH_BUTTON})))
    with pytest.raises(DouyinSearchPageRejected):
        home_page.result_item_count(maximum=20)

    ready = search_page.wait_for_results_ready(timeout_milliseconds=1_000)
    assert ready.state is DouyinSearchPageState.RESULTS_READY


def test_bounded_waits_reject_invalid_timeout_and_return_ready_or_timeout_fact() -> None:
    page = FakePage()
    search_page = DouyinSearchPage(window(page))
    for timeout in (0, 60_001, True):
        with pytest.raises(DouyinSearchPageRejected):
            search_page.wait_for_home_ready(timeout_milliseconds=timeout)

    timed_out = search_page.wait_for_home_ready(timeout_milliseconds=1_000)
    assert timed_out.evidence is DouyinSearchPageEvidence.REQUIRED_ANCHOR_MISSING

    input_group = (
        'input[aria-label="搜索"], input[placeholder="搜索"], [data-e2e="searchbar-input"]'
    )
    submit_group = (
        'button[aria-label="搜索"], [role="button"][aria-label="搜索"], '
        '[data-e2e="searchbar-button"]'
    )
    page.wait_callbacks[input_group] = lambda: page.visible_selectors.add(SEARCH_INPUT)
    page.wait_callbacks[submit_group] = lambda: page.visible_selectors.add(SEARCH_BUTTON)
    ready = search_page.wait_for_home_ready(timeout_milliseconds=1_000)
    assert ready.state is DouyinSearchPageState.HOME_READY


def test_wait_failure_is_page_unavailable_and_unknown_version_stays_closed() -> None:
    input_group = (
        'input[aria-label="搜索"], input[placeholder="搜索"], [data-e2e="searchbar-input"]'
    )
    page = FakePage()
    page.wait_failure_selectors.add(input_group)
    unavailable = DouyinSearchPage(window(page)).wait_for_home_ready(timeout_milliseconds=1_000)
    assert unavailable.evidence is DouyinSearchPageEvidence.PAGE_UNAVAILABLE

    def drift() -> None:
        page.url = "https://www.douyin.com/live"
        raise RuntimeError("private drift")

    page.wait_failure_selectors.clear()
    page.wait_callbacks[input_group] = drift
    unknown = DouyinSearchPage(window(page)).wait_for_home_ready(timeout_milliseconds=1_000)
    assert unknown.evidence is DouyinSearchPageEvidence.PAGE_VERSION_UNKNOWN


def test_wait_returns_last_missing_observation_when_anchor_wakes_without_visibility() -> None:
    result_group = '[role="feed"], [data-e2e="search-result-list"], [data-e2e="scroll-list"]'
    page = FakePage(url=SEARCH_RESULTS_URL)
    page.wait_callbacks[result_group] = lambda: None

    observation = DouyinSearchPage(window(page)).wait_for_results_ready(timeout_milliseconds=1_000)

    assert observation.evidence is DouyinSearchPageEvidence.REQUIRED_ANCHOR_MISSING


def test_elapsed_wait_and_failed_url_read_remain_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    ticks = iter((0.0, 1.0))
    monkeypatch.setattr(search_page_module, "monotonic", lambda: next(ticks))
    elapsed = DouyinSearchPage(window(FakePage())).wait_for_home_ready(timeout_milliseconds=1_000)
    assert elapsed.evidence is DouyinSearchPageEvidence.REQUIRED_ANCHOR_MISSING
    monkeypatch.undo()

    page = FailingSecondUrlPage()
    input_group = (
        'input[aria-label="搜索"], input[placeholder="搜索"], [data-e2e="searchbar-input"]'
    )
    page.wait_failure_selectors.add(input_group)
    unavailable = DouyinSearchPage(window(page)).wait_for_home_ready(timeout_milliseconds=1_000)
    assert unavailable.evidence is DouyinSearchPageEvidence.PAGE_VERSION_UNKNOWN
