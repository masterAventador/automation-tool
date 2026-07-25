from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

import automation_tool.executor.rpa.douyin.search_page as search_page_module
from automation_tool.executor.browser_runtime import BrowserWindow
from automation_tool.executor.rpa.douyin.page_anchors import VISIBLE_MATCH_ENGINE
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
SEARCH_INPUT_PLACEHOLDER = 'input[placeholder="搜索"]'
SEARCH_BUTTON = 'button[aria-label="搜索"]'
SEARCH_BUTTON_FALLBACK = '[data-e2e="searchbar-button"]'
RESULT_LIST = '[role="feed"]'
RESULT_ITEM = '[role="feed"] > article'
RESULT_ITEM_FALLBACK = '[data-e2e="search-result-item"]'
LOGIN_DIALOG = '[role="dialog"]:has-text("扫码登录")'
BLOCKING_DIALOG = '[role="dialog"]'
RISK_CHALLENGE = 'iframe[src^="https://rmc.bytedance.com/verifycenter/captcha/"]'
VIDEO_DETAIL_URL = "https://www.douyin.com/video/7351234567890123456"
INPUT_GROUP = 'input[aria-label="搜索"], input[placeholder="搜索"], [data-e2e="searchbar-input"]'
SUBMIT_GROUP = (
    'button[aria-label="搜索"], [role="button"][aria-label="搜索"], [data-e2e="searchbar-button"]'
)
RESULT_GROUP = '[role="feed"], [data-e2e="search-result-list"], [data-e2e="scroll-list"]'


class FakeLocator:
    """Models grouped matches plus Playwright's visible-only filtering."""

    def __init__(
        self,
        selector: str,
        page: FakePage,
        *,
        visible_only: bool = False,
        first_only: bool = False,
    ) -> None:
        self.selector = selector
        self.page = page
        self.visible_only = visible_only
        self.first_only = first_only

    def locator(self, selector: str) -> FakeLocator:
        assert selector == VISIBLE_MATCH_ENGINE
        return self._derived(visible_only=True, first_only=self.first_only)

    @property
    def first(self) -> FakeLocator:
        return self._derived(visible_only=self.visible_only, first_only=True)

    def is_visible(self) -> bool:
        self._require_healthy("visibility")
        matched = self._matched()
        return bool(matched) and matched[0]

    def count(self) -> int:
        self._require_healthy("count")
        for candidate in self._selectors():
            if candidate in self.page.invalid_counts:
                return cast(int, self.page.invalid_counts[candidate])
        return len(self._matched())

    def wait_for(self, *, state: str, timeout: float) -> None:
        assert state == "visible"
        assert timeout > 0
        if self.selector in self.page.wait_failure_selectors:
            raise RuntimeError("private wait failure")
        callback = self.page.wait_callbacks.get(self.selector)
        if callback is not None:
            callback()
        if self.selector in self.page.premature_wait_selectors:
            return
        matched = self._matched()
        if not matched or not matched[0]:
            self.page.wait_timeouts.append(self.selector)
            raise PlaywrightTimeoutError("private wait timeout")

    def _selectors(self) -> list[str]:
        return self.selector.split(", ")

    def _matched(self) -> list[bool]:
        """Visibility of every matched element, in document order."""
        matched = [
            visible for selector in self._selectors() for visible in self.page.elements(selector)
        ]
        if self.visible_only:
            matched = [visible for visible in matched if visible]
        return matched[:1] if self.first_only else matched

    def _require_healthy(self, kind: str) -> None:
        if any(selector in self.page.failed_selectors for selector in self._selectors()):
            raise RuntimeError(f"private {kind} failure")

    def _derived(self, *, visible_only: bool, first_only: bool) -> FakeLocator:
        return type(self)(
            self.selector,
            self.page,
            visible_only=visible_only,
            first_only=first_only,
        )


class FakePage:
    def __init__(
        self,
        *,
        url: str = DOUYIN_HOME_URL,
        visible_selectors: set[str] | None = None,
        hidden_selectors: set[str] | None = None,
        failed_selectors: set[str] | None = None,
    ) -> None:
        self.url = url
        self.visible_selectors = set() if visible_selectors is None else visible_selectors
        self.hidden_selectors = set() if hidden_selectors is None else hidden_selectors
        self.failed_selectors = set() if failed_selectors is None else failed_selectors
        self.visible_counts: dict[str, int] = {}
        self.invalid_counts: dict[str, object] = {}
        self.requested_selectors: list[str] = []
        self.wait_callbacks: dict[str, Callable[[], None]] = {}
        self.wait_failure_selectors: set[str] = set()
        self.premature_wait_selectors: set[str] = set()
        self.wait_timeouts: list[str] = []

    def elements(self, selector: str) -> tuple[bool, ...]:
        """Like a single-page app, a hidden placeholder precedes the real element."""
        placeholder = (False,) if selector in self.hidden_selectors else ()
        rendered = self.visible_counts.get(selector, 1 if selector in self.visible_selectors else 0)
        return placeholder + (True,) * rendered

    def locator(self, selector: str) -> FakeLocator:
        self.requested_selectors.append(selector)
        return FakeLocator(selector, self)


class ChangingAnchorPage(FakePage):
    def __init__(self, *, fail_after_observation: bool) -> None:
        super().__init__(visible_selectors={SEARCH_INPUT, SEARCH_BUTTON})
        self.fail_after_observation = fail_after_observation
        self._input_requests = 0

    def locator(self, selector: str) -> FakeLocator:
        if selector.startswith(SEARCH_INPUT):
            self._input_requests += 1
            if self._input_requests > 1:
                if self.fail_after_observation:
                    self.failed_selectors.add(SEARCH_INPUT)
                else:
                    self.visible_selectors.discard(SEARCH_INPUT)
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
    assert cast(FakeLocator, home.search_input()).selector == INPUT_GROUP
    assert cast(FakeLocator, home.search_submit()).selector == SUBMIT_GROUP

    result_page = FakePage(url=SEARCH_RESULTS_URL, visible_selectors={RESULT_LIST})
    results = DouyinSearchPage(window(result_page))
    assert cast(FakeLocator, results.result_list()).selector == RESULT_GROUP


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
    ("dialog", "state"),
    (
        (LOGIN_DIALOG, DouyinSearchPageState.LOGIN_REQUIRED),
        (BLOCKING_DIALOG, DouyinSearchPageState.DIALOG_BLOCKED),
        (RISK_CHALLENGE, DouyinSearchPageState.DIALOG_BLOCKED),
    ),
)
def test_a_hidden_placeholder_never_hides_the_handoff_dialog_behind_it(
    dialog: str,
    state: DouyinSearchPageState,
) -> None:
    """The fail-open regression: a placeholder must not mask a captcha or login panel.

    A single-page app pre-renders hidden template nodes, so the first element
    matching a handoff selector is routinely the placeholder. Probing only that
    first match reports "no dialog", and the run keeps searching while a
    verification challenge or an expired-login panel owns the screen. Rule 5 of
    the project baseline allows exactly one answer here: stop and hand over.
    """
    page = FakePage(
        visible_selectors={SEARCH_INPUT, SEARCH_BUTTON, dialog},
        hidden_selectors={dialog},
    )

    observation = DouyinSearchPage(window(page)).observe()

    assert observation.state is state
    assert observation.evidence is (
        DouyinSearchPageEvidence.LOGIN_DIALOG
        if state is DouyinSearchPageState.LOGIN_REQUIRED
        else DouyinSearchPageEvidence.BLOCKING_DIALOG
    )
    assert observation.ready is False
    assert observation.circuit_open is True


@pytest.mark.parametrize(
    ("url", "visible", "hidden", "state", "evidence"),
    (
        (
            DOUYIN_HOME_URL,
            {SEARCH_INPUT, SEARCH_BUTTON},
            {SEARCH_INPUT, SEARCH_BUTTON},
            DouyinSearchPageState.HOME_READY,
            DouyinSearchPageEvidence.SEARCH_ENTRY_VISIBLE,
        ),
        (
            DOUYIN_HOME_URL,
            {SEARCH_INPUT, SEARCH_BUTTON},
            {SEARCH_INPUT_PLACEHOLDER, SEARCH_BUTTON_FALLBACK},
            DouyinSearchPageState.HOME_READY,
            DouyinSearchPageEvidence.SEARCH_ENTRY_VISIBLE,
        ),
        (
            SEARCH_RESULTS_URL,
            {RESULT_LIST},
            {RESULT_LIST},
            DouyinSearchPageState.RESULTS_READY,
            DouyinSearchPageEvidence.RESULT_LIST_VISIBLE,
        ),
    ),
)
def test_hidden_placeholders_neither_hide_nor_duplicate_a_ready_anchor(
    url: str,
    visible: set[str],
    hidden: set[str],
    state: DouyinSearchPageState,
    evidence: DouyinSearchPageEvidence,
) -> None:
    """Hidden template nodes are neither a missing anchor nor a second anchor."""
    page = FakePage(url=url, visible_selectors=visible, hidden_selectors=hidden)

    observation = DouyinSearchPage(window(page)).observe()

    assert observation.state is state
    assert observation.evidence is evidence
    assert observation.ready is True


@pytest.mark.parametrize(
    ("url", "visible"),
    (
        (DOUYIN_HOME_URL, {SEARCH_INPUT, SEARCH_INPUT_PLACEHOLDER, SEARCH_BUTTON}),
        (DOUYIN_HOME_URL, {SEARCH_INPUT, SEARCH_BUTTON, SEARCH_BUTTON_FALLBACK}),
        (SEARCH_RESULTS_URL, {RESULT_LIST, '[data-e2e="search-result-list"]'}),
    ),
)
def test_two_visible_anchors_in_one_group_are_reported_as_conflicting(
    url: str,
    visible: set[str],
) -> None:
    """Acting on the arbitrary first of two visible anchors is a silent side effect."""
    page = FakePage(url=url, visible_selectors=visible)
    search_page = DouyinSearchPage(window(page))

    observation = search_page.observe()

    assert observation.state is DouyinSearchPageState.UNKNOWN
    assert observation.evidence is DouyinSearchPageEvidence.CONFLICTING_ANCHORS
    assert observation.circuit_open is True


def test_a_hidden_skeleton_item_is_neither_counted_nor_read_as_a_candidate() -> None:
    """An unrendered skeleton row would inflate the count and shift every index."""
    page = FakePage(url=SEARCH_RESULTS_URL, visible_selectors={RESULT_LIST})
    page.hidden_selectors.add(RESULT_ITEM)
    page.visible_counts[RESULT_ITEM] = 2

    assert DouyinSearchPage(window(page)).result_item_count(maximum=20) == 2


def test_waiting_is_satisfied_by_the_visible_anchor_behind_a_hidden_placeholder() -> None:
    """Pinning the wait to the first match can only ever time out.

    Entering the wait means no anchor is visible yet, so the first match is the
    hidden placeholder. It never becomes visible, and the real element inserted
    behind it is never resolved, so the wait burns its full budget while the
    page has in fact been ready for most of it.
    """
    page = FakePage(hidden_selectors={SEARCH_INPUT, SEARCH_BUTTON})
    page.wait_callbacks[INPUT_GROUP] = lambda: page.visible_selectors.add(SEARCH_INPUT)
    page.wait_callbacks[SUBMIT_GROUP] = lambda: page.visible_selectors.add(SEARCH_BUTTON)

    ready = DouyinSearchPage(window(page)).wait_for_home_ready(timeout_milliseconds=1_000)

    assert ready.state is DouyinSearchPageState.HOME_READY
    assert page.wait_timeouts == []


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

    page.visible_counts[RESULT_ITEM_FALLBACK] = 12
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
        result_page.invalid_counts[RESULT_ITEM] = invalid_count
        with pytest.raises(DouyinSearchPageRejected):
            search_page.result_item_count(maximum=20)
    result_page.invalid_counts.clear()
    result_page.failed_selectors.add(RESULT_ITEM)
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

    page.wait_callbacks[INPUT_GROUP] = lambda: page.visible_selectors.add(SEARCH_INPUT)
    page.wait_callbacks[SUBMIT_GROUP] = lambda: page.visible_selectors.add(SEARCH_BUTTON)
    ready = search_page.wait_for_home_ready(timeout_milliseconds=1_000)
    assert ready.state is DouyinSearchPageState.HOME_READY


def test_wait_failure_is_page_unavailable_and_unknown_version_stays_closed() -> None:
    page = FakePage()
    page.wait_failure_selectors.add(INPUT_GROUP)
    unavailable = DouyinSearchPage(window(page)).wait_for_home_ready(timeout_milliseconds=1_000)
    assert unavailable.evidence is DouyinSearchPageEvidence.PAGE_UNAVAILABLE

    def drift() -> None:
        page.url = "https://www.douyin.com/live"
        raise RuntimeError("private drift")

    page.wait_failure_selectors.clear()
    page.wait_callbacks[INPUT_GROUP] = drift
    unknown = DouyinSearchPage(window(page)).wait_for_home_ready(timeout_milliseconds=1_000)
    assert unknown.evidence is DouyinSearchPageEvidence.PAGE_VERSION_UNKNOWN


def test_wait_returns_last_missing_observation_when_anchor_wakes_without_visibility() -> None:
    page = FakePage(url=SEARCH_RESULTS_URL)
    page.premature_wait_selectors.add(RESULT_GROUP)

    observation = DouyinSearchPage(window(page)).wait_for_results_ready(timeout_milliseconds=1_000)

    assert observation.evidence is DouyinSearchPageEvidence.REQUIRED_ANCHOR_MISSING


def test_elapsed_wait_and_failed_url_read_remain_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    ticks = iter((0.0, 1.0))
    monkeypatch.setattr(search_page_module, "monotonic", lambda: next(ticks))
    elapsed = DouyinSearchPage(window(FakePage())).wait_for_home_ready(timeout_milliseconds=1_000)
    assert elapsed.evidence is DouyinSearchPageEvidence.REQUIRED_ANCHOR_MISSING
    monkeypatch.undo()

    page = FailingSecondUrlPage()
    page.wait_failure_selectors.add(INPUT_GROUP)
    unavailable = DouyinSearchPage(window(page)).wait_for_home_ready(timeout_milliseconds=1_000)
    assert unavailable.evidence is DouyinSearchPageEvidence.PAGE_VERSION_UNKNOWN
