from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any, cast

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

import automation_tool.executor.rpa.douyin.comment_page as comment_page_module
from automation_tool.executor.browser_runtime import BrowserWindow
from automation_tool.executor.rpa.douyin.comment_page import (
    DOUYIN_COMMENT_PAGE_SELECTOR_VERSION,
    DouyinCommentPage,
    DouyinCommentPageEvidence,
    DouyinCommentPageObservation,
    DouyinCommentPageRejected,
    DouyinCommentPageState,
)
from automation_tool.executor.rpa.douyin.page_version import (
    DOUYIN_SESSION_PROBE_URL,
    DouyinPageEntry,
    DouyinPageVersion,
    DouyinPageVersionModel,
)

VIDEO_URL = "https://www.douyin.com/video/7351234567890123456"
SEARCH_URL = "https://www.douyin.com/search/test?type=general"
COMMENT_INPUT = 'textarea[aria-label="留下你的精彩评论"]'
COMMENT_SUBMIT = 'button[aria-label="发表评论"]'
FINAL_CONFIRMATION = '[role="status"]:has-text("评论成功")'
LOGIN_DIALOG = '[role="dialog"]:has-text("扫码登录")'
BLOCKING_DIALOG = '[role="dialog"]'
RISK_CHALLENGE = 'iframe[src^="https://rmc.bytedance.com/verifycenter/captcha/"]'


class FakeLocator:
    def __init__(
        self,
        selector: str,
        page: FakePage,
        *,
        wait_callback: Callable[[], None] | None = None,
    ) -> None:
        self.selector = selector
        self._page = page
        self._wait_callback = wait_callback

    @property
    def first(self) -> FakeLocator:
        return self

    def count(self) -> int:
        if self.selector in self._page.failed_selectors:
            raise RuntimeError("private count failure")
        if self.selector in self._page.invalid_count_selectors:
            return cast(int, True)
        return sum(
            selector in self._page.visible_selectors for selector in self.selector.split(", ")
        )

    def is_visible(self) -> bool:
        if self.selector in self._page.failed_selectors:
            raise RuntimeError("private visibility failure")
        return self.count() > 0

    def wait_for(self, *, state: str, timeout: float) -> None:
        assert state == "visible"
        assert timeout > 0
        if self.selector in self._page.failed_wait_selectors:
            raise RuntimeError("private wait failure")
        if self.selector in self._page.premature_wait_selectors:
            return
        if self._wait_callback is not None:
            self._wait_callback()
        if not self.is_visible():
            raise PlaywrightTimeoutError("private wait timeout")


class FakePage:
    def __init__(
        self,
        *,
        url: str = VIDEO_URL,
        visible_selectors: set[str] | None = None,
    ) -> None:
        self.url = url
        self.visible_selectors = set() if visible_selectors is None else visible_selectors
        self.failed_selectors: set[str] = set()
        self.invalid_count_selectors: set[str] = set()
        self.failed_wait_selectors: set[str] = set()
        self.premature_wait_selectors: set[str] = set()
        self.wait_callbacks: dict[str, Callable[[], None]] = {}
        self.requested_selectors: list[str] = []

    def locator(self, selector: str) -> FakeLocator:
        self.requested_selectors.append(selector)
        return FakeLocator(
            selector,
            self,
            wait_callback=self.wait_callbacks.get(selector),
        )


def window(page: FakePage) -> BrowserWindow:
    return BrowserWindow._for_runtime(object(), cast(Any, page))


class FailingUrlPage(FakePage):
    def __init__(self) -> None:
        self._url = VIDEO_URL
        super().__init__()

    @property
    def url(self) -> str:
        raise RuntimeError("private URL failure")

    @url.setter
    def url(self, value: str) -> None:
        self._url = value


class DriftingInputPage(FakePage):
    def __init__(self, *, fail: bool) -> None:
        super().__init__(visible_selectors={COMMENT_INPUT, COMMENT_SUBMIT})
        self._input_requests = 0
        self._fail = fail

    def locator(self, selector: str) -> FakeLocator:
        if selector.startswith(COMMENT_INPUT):
            self._input_requests += 1
            if self._input_requests == 2:
                if self._fail:
                    self.failed_selectors.add(selector)
                else:
                    self.visible_selectors.remove(COMMENT_INPUT)
        return super().locator(selector)


def test_video_route_and_complete_comment_anchors_are_one_ready_contract() -> None:
    page = FakePage(visible_selectors={COMMENT_INPUT, COMMENT_SUBMIT})

    observation = DouyinCommentPage(window(page)).observe()

    assert observation == DouyinCommentPageObservation(
        page_version=DouyinPageVersion.WEB_V1,
        entry=DouyinPageEntry.VIDEO_DETAIL,
        state=DouyinCommentPageState.READY,
        evidence=DouyinCommentPageEvidence.INPUT_AND_SUBMIT_VISIBLE,
    )
    assert observation.selector_version == DOUYIN_COMMENT_PAGE_SELECTOR_VERSION
    assert observation.ready is True
    assert observation.confirmed is False
    assert observation.circuit_open is False
    assert VIDEO_URL not in repr(observation)
    version = DouyinPageVersionModel().check(VIDEO_URL)
    assert version.entry is DouyinPageEntry.VIDEO_DETAIL
    assert version.compatible is True


def test_comment_accessors_reobserve_and_expose_only_versioned_anchors() -> None:
    page = FakePage(visible_selectors={COMMENT_INPUT, COMMENT_SUBMIT})
    comment_page = DouyinCommentPage(window(page))

    assert COMMENT_INPUT in cast(FakeLocator, comment_page.comment_input()).selector
    assert COMMENT_SUBMIT in cast(FakeLocator, comment_page.comment_submit()).selector
    with pytest.raises(DouyinCommentPageRejected):
        comment_page.final_confirmation()

    page.visible_selectors = {FINAL_CONFIRMATION}
    assert FINAL_CONFIRMATION in cast(FakeLocator, comment_page.final_confirmation()).selector
    with pytest.raises(DouyinCommentPageRejected):
        comment_page.comment_submit()


def test_final_confirmation_has_a_closed_non_actionable_state() -> None:
    page = FakePage(visible_selectors={COMMENT_INPUT, COMMENT_SUBMIT, FINAL_CONFIRMATION})

    observation = DouyinCommentPage(window(page)).observe()

    assert observation.state is DouyinCommentPageState.CONFIRMED
    assert observation.evidence is DouyinCommentPageEvidence.FINAL_CONFIRMATION_VISIBLE
    assert observation.ready is False
    assert observation.confirmed is True
    assert observation.circuit_open is False


@pytest.mark.parametrize("blocking", (BLOCKING_DIALOG, RISK_CHALLENGE))
def test_login_and_blocking_evidence_take_priority_over_action_anchors(
    blocking: str,
) -> None:
    login_page = FakePage(
        visible_selectors={COMMENT_INPUT, COMMENT_SUBMIT, LOGIN_DIALOG, BLOCKING_DIALOG}
    )
    login = DouyinCommentPage(window(login_page)).observe()
    assert login.state is DouyinCommentPageState.LOGIN_REQUIRED
    assert login.evidence is DouyinCommentPageEvidence.LOGIN_DIALOG
    assert login.circuit_open is True

    blocked_page = FakePage(visible_selectors={COMMENT_INPUT, COMMENT_SUBMIT, blocking})
    blocked = DouyinCommentPage(window(blocked_page)).observe()
    assert blocked.state is DouyinCommentPageState.DIALOG_BLOCKED
    assert blocked.evidence is DouyinCommentPageEvidence.BLOCKING_DIALOG
    assert blocked.circuit_open is True


@pytest.mark.parametrize(
    ("selectors", "evidence"),
    (
        (set(), DouyinCommentPageEvidence.REQUIRED_ANCHOR_MISSING),
        ({COMMENT_INPUT}, DouyinCommentPageEvidence.REQUIRED_ANCHOR_MISSING),
        ({COMMENT_SUBMIT}, DouyinCommentPageEvidence.REQUIRED_ANCHOR_MISSING),
        (
            {COMMENT_INPUT, COMMENT_SUBMIT, 'textarea[placeholder="留下你的精彩评论"]'},
            DouyinCommentPageEvidence.CONFLICTING_ANCHORS,
        ),
    ),
)
def test_missing_or_duplicate_action_anchors_fail_closed(
    selectors: set[str],
    evidence: DouyinCommentPageEvidence,
) -> None:
    observation = DouyinCommentPage(window(FakePage(visible_selectors=selectors))).observe()

    assert observation.state is DouyinCommentPageState.UNKNOWN
    assert observation.evidence is evidence
    assert observation.circuit_open is True


def test_non_video_route_is_rejected_before_any_dom_query() -> None:
    page = FakePage(
        url=SEARCH_URL,
        visible_selectors={COMMENT_INPUT, COMMENT_SUBMIT},
    )

    observation = DouyinCommentPage(window(page)).observe()

    assert observation.page_version is DouyinPageVersion.WEB_V1
    assert observation.entry is DouyinPageEntry.SEARCH_RESULTS
    assert observation.state is DouyinCommentPageState.UNKNOWN
    assert observation.evidence is DouyinCommentPageEvidence.PAGE_VERSION_UNKNOWN
    assert page.requested_selectors == []

    incompatible = FakePage(
        url="https://example.com/private",
        visible_selectors={COMMENT_INPUT, COMMENT_SUBMIT},
    )
    unknown = DouyinCommentPage(window(incompatible)).observe()
    assert unknown.page_version is DouyinPageVersion.UNKNOWN
    assert unknown.evidence is DouyinCommentPageEvidence.PAGE_VERSION_UNKNOWN
    assert incompatible.requested_selectors == []

    redirect = FakePage(url=DOUYIN_SESSION_PROBE_URL)
    login = DouyinCommentPage(window(redirect)).observe()
    assert login.state is DouyinCommentPageState.LOGIN_REQUIRED
    assert login.evidence is DouyinCommentPageEvidence.LOGIN_REDIRECT
    assert redirect.requested_selectors == []


def test_page_failures_bad_counts_and_mid_access_drift_are_safe() -> None:
    input_group = (
        'textarea[aria-label="留下你的精彩评论"], '
        'textarea[placeholder="留下你的精彩评论"], '
        '[contenteditable="true"][data-e2e="comment-input"], '
        '[data-e2e="comment-textarea"]'
    )
    failed = FakePage(visible_selectors={COMMENT_INPUT, COMMENT_SUBMIT})
    failed.failed_selectors.add(input_group)
    observation = DouyinCommentPage(window(failed)).observe()
    assert observation.state is DouyinCommentPageState.UNKNOWN
    assert observation.evidence is DouyinCommentPageEvidence.PAGE_UNAVAILABLE

    invalid = FakePage(visible_selectors={COMMENT_INPUT, COMMENT_SUBMIT})
    invalid.invalid_count_selectors.add(input_group)
    assert DouyinCommentPage(window(invalid)).observe().evidence is (
        DouyinCommentPageEvidence.PAGE_UNAVAILABLE
    )

    drifted = FakePage(visible_selectors={COMMENT_INPUT, COMMENT_SUBMIT})
    comment_page = DouyinCommentPage(window(drifted))
    drifted.visible_selectors.remove(COMMENT_SUBMIT)
    with pytest.raises(DouyinCommentPageRejected):
        comment_page.comment_input()

    for fail in (False, True):
        with pytest.raises(DouyinCommentPageRejected):
            DouyinCommentPage(window(DriftingInputPage(fail=fail))).comment_input()

    unavailable_url = DouyinCommentPage(window(FailingUrlPage())).observe()
    assert unavailable_url.page_version is DouyinPageVersion.UNKNOWN
    assert unavailable_url.evidence is DouyinCommentPageEvidence.PAGE_UNAVAILABLE


def test_bounded_waits_reobserve_ready_final_timeout_and_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_group = (
        'textarea[aria-label="留下你的精彩评论"], '
        'textarea[placeholder="留下你的精彩评论"], '
        '[contenteditable="true"][data-e2e="comment-input"], '
        '[data-e2e="comment-textarea"]'
    )
    submit_group = (
        'button[aria-label="发表评论"], '
        '[role="button"][aria-label="发表评论"], '
        '[data-e2e="comment-submit"]'
    )
    final_group = '[role="status"]:has-text("评论成功"), [data-e2e="comment-publish-success"]'
    page = FakePage()
    page.wait_callbacks[input_group] = lambda: page.visible_selectors.add(COMMENT_INPUT)
    page.wait_callbacks[submit_group] = lambda: page.visible_selectors.add(COMMENT_SUBMIT)
    comment_page = DouyinCommentPage(window(page))
    assert comment_page.wait_for_ready(timeout_milliseconds=100).ready is True

    page.wait_callbacks[final_group] = lambda: page.visible_selectors.add(FINAL_CONFIRMATION)
    assert comment_page.wait_for_final(timeout_milliseconds=100).confirmed is True

    timed_out = DouyinCommentPage(window(FakePage())).wait_for_ready(timeout_milliseconds=100)
    assert timed_out.evidence is DouyinCommentPageEvidence.REQUIRED_ANCHOR_MISSING

    failed_page = FakePage()
    failed_page.failed_wait_selectors.add(input_group)
    unavailable = DouyinCommentPage(window(failed_page)).wait_for_ready(timeout_milliseconds=100)
    assert unavailable.evidence is DouyinCommentPageEvidence.PAGE_UNAVAILABLE

    for invalid_timeout in (0, 60_001, True):
        with pytest.raises(DouyinCommentPageRejected):
            comment_page.wait_for_ready(timeout_milliseconds=invalid_timeout)

    non_video = DouyinCommentPage(window(FakePage(url=SEARCH_URL)))
    assert non_video.wait_for_ready(timeout_milliseconds=100).evidence is (
        DouyinCommentPageEvidence.PAGE_VERSION_UNKNOWN
    )

    premature = FakePage(visible_selectors={COMMENT_INPUT, COMMENT_SUBMIT})
    premature.premature_wait_selectors.add(final_group)
    still_ready = DouyinCommentPage(window(premature)).wait_for_final(timeout_milliseconds=100)
    assert still_ready.state is DouyinCommentPageState.READY

    ticks = iter((0.0, 1.0))
    monkeypatch.setattr(comment_page_module, "monotonic", lambda: next(ticks))
    expired = DouyinCommentPage(window(FakePage())).wait_for_ready(timeout_milliseconds=100)
    assert expired.evidence is DouyinCommentPageEvidence.REQUIRED_ANCHOR_MISSING


def test_observation_and_constructor_are_closed_and_redacted() -> None:
    valid = DouyinCommentPageObservation(
        page_version=DouyinPageVersion.WEB_V1,
        entry=DouyinPageEntry.VIDEO_DETAIL,
        state=DouyinCommentPageState.READY,
        evidence=DouyinCommentPageEvidence.INPUT_AND_SUBMIT_VISIBLE,
    )
    for invalid in (
        {"state": cast(DouyinCommentPageState, "ready")},
        {"selector_version": "private"},
        {"evidence": DouyinCommentPageEvidence.FINAL_CONFIRMATION_VISIBLE},
        {"entry": DouyinPageEntry.HOME},
    ):
        with pytest.raises(DouyinCommentPageRejected):
            replace(valid, **invalid)
    with pytest.raises(DouyinCommentPageRejected):
        DouyinCommentPage(cast(BrowserWindow, object()))
    assert repr(DouyinCommentPage(window(FakePage()))) == "DouyinCommentPage(<redacted>)"
