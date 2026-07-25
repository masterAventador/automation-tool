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
from automation_tool.executor.rpa.douyin.page_anchors import VISIBLE_MATCH_ENGINE
from automation_tool.executor.rpa.douyin.page_version import (
    DOUYIN_SESSION_PROBE_URL,
    DouyinPageEntry,
    DouyinPageVersion,
    DouyinPageVersionModel,
)

VIDEO_URL = "https://www.douyin.com/video/7351234567890123456"
SEARCH_URL = "https://www.douyin.com/search/test?type=general"
COMMENT_INPUT = 'textarea[aria-label="留下你的精彩评论"]'
COMMENT_INPUT_PLACEHOLDER = 'textarea[placeholder="留下你的精彩评论"]'
COMMENT_SUBMIT = 'button[aria-label="发表评论"]'
FINAL_CONFIRMATION = '[role="status"]:has-text("评论成功")'
LOGIN_DIALOG = '[role="dialog"]:has-text("扫码登录")'
BLOCKING_DIALOG = '[role="dialog"]'
RISK_CHALLENGE = 'iframe[src^="https://rmc.bytedance.com/verifycenter/captcha/"]'
INPUT_GROUP = (
    'textarea[aria-label="留下你的精彩评论"], '
    'textarea[placeholder="留下你的精彩评论"], '
    '[contenteditable="true"][data-e2e="comment-input"], '
    '[data-e2e="comment-textarea"]'
)
SUBMIT_GROUP = (
    'button[aria-label="发表评论"], '
    '[role="button"][aria-label="发表评论"], '
    '[data-e2e="comment-submit"]'
)
FINAL_GROUP = '[role="status"]:has-text("评论成功"), [data-e2e="comment-publish-success"]'


class FakeLocator:
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

    def count(self) -> int:
        if self.selector in self.page.failed_selectors:
            raise RuntimeError("private count failure")
        if self.selector in self.page.invalid_count_selectors:
            return cast(int, True)
        return len(self._matched())

    def wait_for(self, *, state: str, timeout: float) -> None:
        assert state == "visible"
        assert timeout > 0
        if self.selector in self.page.failed_wait_selectors:
            raise RuntimeError("private wait failure")
        if self.selector in self.page.premature_wait_selectors:
            return
        callback = self.page.wait_callbacks.get(self.selector)
        if callback is not None:
            callback()
        matched = self._matched()
        if not matched or not matched[0]:
            self.page.wait_timeouts.append(self.selector)
            raise PlaywrightTimeoutError("private wait timeout")

    def _matched(self) -> list[bool]:
        """Visibility of every matched element, in document order."""
        matched = [
            visible
            for selector in self.selector.split(", ")
            for visible in self.page.elements(selector)
        ]
        if self.visible_only:
            matched = [visible for visible in matched if visible]
        return matched[:1] if self.first_only else matched

    def _derived(self, *, visible_only: bool, first_only: bool) -> FakeLocator:
        return FakeLocator(
            self.selector,
            self.page,
            visible_only=visible_only,
            first_only=first_only,
        )


class FakePage:
    def __init__(
        self,
        *,
        url: str = VIDEO_URL,
        visible_selectors: set[str] | None = None,
        hidden_selectors: set[str] | None = None,
    ) -> None:
        self.url = url
        self.visible_selectors = set() if visible_selectors is None else visible_selectors
        self.hidden_selectors = set() if hidden_selectors is None else hidden_selectors
        self.failed_selectors: set[str] = set()
        self.invalid_count_selectors: set[str] = set()
        self.failed_wait_selectors: set[str] = set()
        self.premature_wait_selectors: set[str] = set()
        self.wait_callbacks: dict[str, Callable[[], None]] = {}
        self.wait_timeouts: list[str] = []
        self.requested_selectors: list[str] = []

    def elements(self, selector: str) -> tuple[bool, ...]:
        """Like a single-page app, a hidden placeholder precedes the real element."""
        placeholder = (False,) if selector in self.hidden_selectors else ()
        rendered = (True,) if selector in self.visible_selectors else ()
        return placeholder + rendered

    def locator(self, selector: str) -> FakeLocator:
        self.requested_selectors.append(selector)
        return FakeLocator(selector, self)


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
    ("state", "evidence"),
    (
        (DouyinCommentPageState.LOGIN_REQUIRED, DouyinCommentPageEvidence.LOGIN_DIALOG),
        (DouyinCommentPageState.DIALOG_BLOCKED, DouyinCommentPageEvidence.BLOCKING_DIALOG),
    ),
)
def test_a_hidden_placeholder_never_hides_the_handoff_dialog_behind_it(
    state: DouyinCommentPageState,
    evidence: DouyinCommentPageEvidence,
) -> None:
    """A pre-rendered placeholder must not mask the dialog that stops the run."""
    dialog = LOGIN_DIALOG if state is DouyinCommentPageState.LOGIN_REQUIRED else BLOCKING_DIALOG
    page = FakePage(
        visible_selectors={COMMENT_INPUT, COMMENT_SUBMIT, dialog},
        hidden_selectors={dialog},
    )

    observation = DouyinCommentPage(window(page)).observe()

    assert observation.state is state
    assert observation.evidence is evidence
    assert observation.circuit_open is True


def test_hidden_placeholders_beside_one_visible_anchor_are_not_a_conflict() -> None:
    """Hidden template nodes are not a second anchor, so the page stays usable."""
    page = FakePage(
        visible_selectors={COMMENT_INPUT, COMMENT_SUBMIT},
        hidden_selectors={COMMENT_INPUT_PLACEHOLDER, '[data-e2e="comment-submit"]'},
    )
    comment_page = DouyinCommentPage(window(page))

    observation = comment_page.observe()

    assert observation.state is DouyinCommentPageState.READY
    assert observation.evidence is DouyinCommentPageEvidence.INPUT_AND_SUBMIT_VISIBLE
    assert COMMENT_INPUT in cast(FakeLocator, comment_page.comment_input()).selector
    assert COMMENT_SUBMIT in cast(FakeLocator, comment_page.comment_submit()).selector


@pytest.mark.parametrize(
    ("selectors", "evidence"),
    (
        (set(), DouyinCommentPageEvidence.REQUIRED_ANCHOR_MISSING),
        ({COMMENT_INPUT}, DouyinCommentPageEvidence.REQUIRED_ANCHOR_MISSING),
        ({COMMENT_SUBMIT}, DouyinCommentPageEvidence.REQUIRED_ANCHOR_MISSING),
        (
            {COMMENT_INPUT, COMMENT_SUBMIT, COMMENT_INPUT_PLACEHOLDER},
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
    failed = FakePage(visible_selectors={COMMENT_INPUT, COMMENT_SUBMIT})
    failed.failed_selectors.add(INPUT_GROUP)
    observation = DouyinCommentPage(window(failed)).observe()
    assert observation.state is DouyinCommentPageState.UNKNOWN
    assert observation.evidence is DouyinCommentPageEvidence.PAGE_UNAVAILABLE

    invalid = FakePage(visible_selectors={COMMENT_INPUT, COMMENT_SUBMIT})
    invalid.invalid_count_selectors.add(INPUT_GROUP)
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


def test_waits_are_satisfied_by_any_visible_match_not_by_the_first_dom_match() -> None:
    """A hidden placeholder must not burn the whole wait budget.

    ``wait_for_final`` runs against a success toast that disappears on its own,
    so a wait that can only be satisfied by the first DOM match reports a
    finished action as unconfirmed.
    """
    page = FakePage(hidden_selectors={COMMENT_INPUT, COMMENT_SUBMIT, FINAL_CONFIRMATION})
    page.wait_callbacks[INPUT_GROUP] = lambda: page.visible_selectors.add(COMMENT_INPUT)
    page.wait_callbacks[SUBMIT_GROUP] = lambda: page.visible_selectors.add(COMMENT_SUBMIT)
    page.wait_callbacks[FINAL_GROUP] = lambda: page.visible_selectors.add(FINAL_CONFIRMATION)
    comment_page = DouyinCommentPage(window(page))

    assert comment_page.wait_for_ready(timeout_milliseconds=100).ready is True
    assert comment_page.wait_for_final(timeout_milliseconds=100).confirmed is True
    assert page.wait_timeouts == []


def test_bounded_waits_reobserve_ready_final_timeout_and_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FakePage()
    page.wait_callbacks[INPUT_GROUP] = lambda: page.visible_selectors.add(COMMENT_INPUT)
    page.wait_callbacks[SUBMIT_GROUP] = lambda: page.visible_selectors.add(COMMENT_SUBMIT)
    comment_page = DouyinCommentPage(window(page))
    assert comment_page.wait_for_ready(timeout_milliseconds=100).ready is True

    page.wait_callbacks[FINAL_GROUP] = lambda: page.visible_selectors.add(FINAL_CONFIRMATION)
    assert comment_page.wait_for_final(timeout_milliseconds=100).confirmed is True

    timed_out = DouyinCommentPage(window(FakePage())).wait_for_ready(timeout_milliseconds=100)
    assert timed_out.evidence is DouyinCommentPageEvidence.REQUIRED_ANCHOR_MISSING

    failed_page = FakePage()
    failed_page.failed_wait_selectors.add(INPUT_GROUP)
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
    premature.premature_wait_selectors.add(FINAL_GROUP)
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
