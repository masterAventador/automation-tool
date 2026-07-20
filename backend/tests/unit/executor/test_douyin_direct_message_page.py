from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any, cast

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

import automation_tool.executor.rpa.douyin.direct_message_page as direct_message_page_module
from automation_tool.executor.browser_runtime import BrowserWindow
from automation_tool.executor.rpa.douyin.direct_message_page import (
    DOUYIN_DIRECT_MESSAGE_PAGE_SELECTOR_VERSION,
    DouyinDirectMessagePage,
    DouyinDirectMessagePageEvidence,
    DouyinDirectMessagePageObservation,
    DouyinDirectMessagePageRejected,
    DouyinDirectMessagePageState,
)
from automation_tool.executor.rpa.douyin.page_version import (
    DOUYIN_SESSION_PROBE_URL,
    DouyinPageEntry,
    DouyinPageVersion,
    DouyinPageVersionModel,
)

PROFILE_URL = "https://www.douyin.com/user/creator-001"
MESSAGE_ENTRY = 'button[aria-label="私信"]'
MESSAGE_INPUT = 'textarea[aria-label="发送私信"]'
MESSAGE_SEND = 'button[aria-label="发送私信"]'
FINAL_CONFIRMATION = '[role="status"]:has-text("私信发送成功")'
PERMISSION_DENIED = '[role="alert"]:has-text("暂时无法私信")'
FOLLOW_REQUIRED = '[role="alert"]:has-text("关注后才能私信")'
LOGIN_DIALOG = '[role="dialog"]:has-text("扫码登录")'
BLOCKING_DIALOG = '[role="dialog"]'
RISK_CHALLENGE = 'iframe[src^="https://rmc.bytedance.com/verifycenter/captcha/"]'


class FakeLocator:
    def __init__(self, selector: str, page: FakePage) -> None:
        self.selector = selector
        self.page = page

    @property
    def first(self) -> FakeLocator:
        return self

    def count(self) -> int:
        if self.selector in self.page.failed_selectors:
            raise RuntimeError("private failure")
        if self.selector in self.page.invalid_count_selectors:
            return cast(int, True)
        return sum(
            selector in self.page.visible_selectors for selector in self.selector.split(", ")
        )

    def is_visible(self) -> bool:
        return self.count() > 0

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
        if not self.is_visible():
            raise PlaywrightTimeoutError("private wait timeout")


class FakePage:
    def __init__(
        self,
        *,
        url: str = PROFILE_URL,
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
        return FakeLocator(selector, self)


def window(page: FakePage) -> BrowserWindow:
    return BrowserWindow._for_runtime(object(), cast(Any, page))


class FailingUrlPage(FakePage):
    def __init__(self) -> None:
        self._url = PROFILE_URL
        super().__init__()

    @property
    def url(self) -> str:
        raise RuntimeError("private URL failure")

    @url.setter
    def url(self, value: str) -> None:
        self._url = value


class DriftingEntryPage(FakePage):
    def __init__(self, *, fail: bool) -> None:
        super().__init__(visible_selectors={MESSAGE_ENTRY})
        self._entry_requests = 0
        self._fail = fail

    def locator(self, selector: str) -> FakeLocator:
        if selector.startswith(MESSAGE_ENTRY):
            self._entry_requests += 1
            if self._entry_requests == 2:
                if self._fail:
                    self.failed_selectors.add(selector)
                else:
                    self.visible_selectors.remove(MESSAGE_ENTRY)
        return super().locator(selector)


@pytest.mark.parametrize(
    ("selectors", "state", "evidence"),
    (
        (
            {MESSAGE_ENTRY},
            DouyinDirectMessagePageState.PROFILE_READY,
            DouyinDirectMessagePageEvidence.ENTER_CONVERSATION_VISIBLE,
        ),
        (
            {MESSAGE_INPUT, MESSAGE_SEND},
            DouyinDirectMessagePageState.CONVERSATION_READY,
            DouyinDirectMessagePageEvidence.INPUT_AND_SEND_VISIBLE,
        ),
        (
            {MESSAGE_INPUT, MESSAGE_SEND, FINAL_CONFIRMATION},
            DouyinDirectMessagePageState.CONFIRMED,
            DouyinDirectMessagePageEvidence.FINAL_CONFIRMATION_VISIBLE,
        ),
        (
            {PERMISSION_DENIED},
            DouyinDirectMessagePageState.PERMISSION_DENIED,
            DouyinDirectMessagePageEvidence.MESSAGING_NOT_ALLOWED,
        ),
        (
            {FOLLOW_REQUIRED},
            DouyinDirectMessagePageState.PERMISSION_DENIED,
            DouyinDirectMessagePageEvidence.FOLLOW_REQUIRED,
        ),
    ),
)
def test_profile_message_states_and_permissions_are_closed(
    selectors: set[str],
    state: DouyinDirectMessagePageState,
    evidence: DouyinDirectMessagePageEvidence,
) -> None:
    observation = DouyinDirectMessagePage(window(FakePage(visible_selectors=selectors))).observe()

    assert observation == DouyinDirectMessagePageObservation(
        page_version=DouyinPageVersion.WEB_V1,
        entry=DouyinPageEntry.USER_PROFILE,
        state=state,
        evidence=evidence,
    )
    assert observation.selector_version == DOUYIN_DIRECT_MESSAGE_PAGE_SELECTOR_VERSION
    assert observation.circuit_open is (state is DouyinDirectMessagePageState.PERMISSION_DENIED)
    assert PROFILE_URL not in repr(observation)
    assert DouyinPageVersionModel().check(PROFILE_URL).entry is DouyinPageEntry.USER_PROFILE


def test_accessors_only_open_in_their_exact_reobserved_state() -> None:
    page = FakePage(visible_selectors={MESSAGE_ENTRY})
    message_page = DouyinDirectMessagePage(window(page))
    assert MESSAGE_ENTRY in cast(FakeLocator, message_page.enter_conversation()).selector
    with pytest.raises(DouyinDirectMessagePageRejected):
        message_page.message_input()

    page.visible_selectors = {MESSAGE_INPUT, MESSAGE_SEND}
    assert MESSAGE_INPUT in cast(FakeLocator, message_page.message_input()).selector
    assert MESSAGE_SEND in cast(FakeLocator, message_page.message_send()).selector

    page.visible_selectors = {FINAL_CONFIRMATION}
    assert FINAL_CONFIRMATION in cast(FakeLocator, message_page.final_confirmation()).selector

    page.visible_selectors = {FOLLOW_REQUIRED}
    assert FOLLOW_REQUIRED in cast(FakeLocator, message_page.permission_notice()).selector

    page.visible_selectors = {PERMISSION_DENIED}
    assert PERMISSION_DENIED in cast(FakeLocator, message_page.permission_notice()).selector

    page.visible_selectors = {MESSAGE_ENTRY}
    with pytest.raises(DouyinDirectMessagePageRejected):
        message_page.permission_notice()


def test_unknown_route_never_queries_private_message_dom() -> None:
    page = FakePage(
        url="https://www.douyin.com/search/test?type=general",
        visible_selectors={MESSAGE_ENTRY, MESSAGE_INPUT, MESSAGE_SEND},
    )

    observation = DouyinDirectMessagePage(window(page)).observe()

    assert observation.state is DouyinDirectMessagePageState.UNKNOWN
    assert observation.evidence is DouyinDirectMessagePageEvidence.PAGE_VERSION_UNKNOWN
    assert page.requested_selectors == []


def test_incompatible_route_and_login_redirect_stop_before_dom_query() -> None:
    incompatible = FakePage(
        url="https://example.com/user/creator-001",
        visible_selectors={MESSAGE_ENTRY},
    )
    unknown = DouyinDirectMessagePage(window(incompatible)).observe()
    assert unknown.page_version is DouyinPageVersion.UNKNOWN
    assert unknown.evidence is DouyinDirectMessagePageEvidence.PAGE_VERSION_UNKNOWN
    assert incompatible.requested_selectors == []

    redirect = FakePage(url=DOUYIN_SESSION_PROBE_URL)
    login = DouyinDirectMessagePage(window(redirect)).observe()
    assert login.state is DouyinDirectMessagePageState.LOGIN_REQUIRED
    assert login.evidence is DouyinDirectMessagePageEvidence.LOGIN_REDIRECT
    assert redirect.requested_selectors == []


@pytest.mark.parametrize("blocking", (BLOCKING_DIALOG, RISK_CHALLENGE))
def test_login_and_blocking_evidence_take_priority_over_message_anchors(
    blocking: str,
) -> None:
    login_page = FakePage(
        visible_selectors={MESSAGE_ENTRY, MESSAGE_INPUT, MESSAGE_SEND, LOGIN_DIALOG, blocking}
    )
    login = DouyinDirectMessagePage(window(login_page)).observe()
    assert login.state is DouyinDirectMessagePageState.LOGIN_REQUIRED
    assert login.evidence is DouyinDirectMessagePageEvidence.LOGIN_DIALOG
    assert login.circuit_open is True

    blocked_page = FakePage(
        visible_selectors={MESSAGE_ENTRY, MESSAGE_INPUT, MESSAGE_SEND, blocking}
    )
    blocked = DouyinDirectMessagePage(window(blocked_page)).observe()
    assert blocked.state is DouyinDirectMessagePageState.DIALOG_BLOCKED
    assert blocked.evidence is DouyinDirectMessagePageEvidence.BLOCKING_DIALOG
    assert blocked.circuit_open is True


def test_partial_duplicate_conflicting_and_driver_failure_fail_closed() -> None:
    for selectors, evidence in (
        ({MESSAGE_INPUT}, DouyinDirectMessagePageEvidence.REQUIRED_ANCHOR_MISSING),
        (
            {MESSAGE_ENTRY, MESSAGE_INPUT},
            DouyinDirectMessagePageEvidence.CONFLICTING_ANCHORS,
        ),
        (
            {PERMISSION_DENIED, FOLLOW_REQUIRED},
            DouyinDirectMessagePageEvidence.CONFLICTING_ANCHORS,
        ),
        (
            {MESSAGE_ENTRY, '[role="button"][aria-label="私信"]'},
            DouyinDirectMessagePageEvidence.CONFLICTING_ANCHORS,
        ),
    ):
        observation = DouyinDirectMessagePage(
            window(FakePage(visible_selectors=selectors))
        ).observe()
        assert observation.state is DouyinDirectMessagePageState.UNKNOWN
        assert observation.evidence is evidence

    failed = FakePage(visible_selectors={MESSAGE_ENTRY})
    failed.failed_selectors.add(
        'button[aria-label="私信"], [role="button"][aria-label="私信"], '
        '[data-e2e="direct-message-entry"]'
    )
    assert DouyinDirectMessagePage(window(failed)).observe().evidence is (
        DouyinDirectMessagePageEvidence.PAGE_UNAVAILABLE
    )

    invalid = FakePage(visible_selectors={MESSAGE_ENTRY})
    invalid.invalid_count_selectors.add(
        'button[aria-label="私信"], [role="button"][aria-label="私信"], '
        '[data-e2e="direct-message-entry"]'
    )
    assert DouyinDirectMessagePage(window(invalid)).observe().evidence is (
        DouyinDirectMessagePageEvidence.PAGE_UNAVAILABLE
    )


def test_url_failures_and_mid_access_drift_are_safe() -> None:
    unavailable = DouyinDirectMessagePage(window(FailingUrlPage())).observe()
    assert unavailable.page_version is DouyinPageVersion.UNKNOWN
    assert unavailable.evidence is DouyinDirectMessagePageEvidence.PAGE_UNAVAILABLE

    for fail in (False, True):
        with pytest.raises(DouyinDirectMessagePageRejected):
            DouyinDirectMessagePage(window(DriftingEntryPage(fail=fail))).enter_conversation()

    drifted = FakePage(visible_selectors={MESSAGE_ENTRY})
    message_page = DouyinDirectMessagePage(window(drifted))
    drifted.visible_selectors.clear()
    with pytest.raises(DouyinDirectMessagePageRejected):
        message_page.enter_conversation()


def test_constructor_repr_observation_and_waits_are_bounded() -> None:
    with pytest.raises(DouyinDirectMessagePageRejected):
        DouyinDirectMessagePage(cast(BrowserWindow, object()))
    page = FakePage()
    entry_group = (
        'button[aria-label="私信"], [role="button"][aria-label="私信"], '
        '[data-e2e="direct-message-entry"]'
    )
    page.wait_callbacks[entry_group] = lambda: page.visible_selectors.add(MESSAGE_ENTRY)
    message_page = DouyinDirectMessagePage(window(page))
    assert message_page.wait_for_profile_ready(timeout_milliseconds=100).state is (
        DouyinDirectMessagePageState.PROFILE_READY
    )
    assert repr(message_page) == "DouyinDirectMessagePage(<redacted>)"
    ready = message_page.observe()
    assert ready.profile_ready is True
    assert ready.conversation_ready is False
    assert ready.confirmed is False
    valid = DouyinDirectMessagePageObservation(
        page_version=DouyinPageVersion.WEB_V1,
        entry=DouyinPageEntry.USER_PROFILE,
        state=DouyinDirectMessagePageState.PROFILE_READY,
        evidence=DouyinDirectMessagePageEvidence.ENTER_CONVERSATION_VISIBLE,
    )
    for invalid_observation in (
        {"state": cast(DouyinDirectMessagePageState, "profile_ready")},
        {"selector_version": "private"},
        {"evidence": DouyinDirectMessagePageEvidence.FINAL_CONFIRMATION_VISIBLE},
        {"entry": DouyinPageEntry.HOME},
    ):
        with pytest.raises(DouyinDirectMessagePageRejected):
            replace(valid, **invalid_observation)
    for invalid in (0, 60_001, True):
        with pytest.raises(DouyinDirectMessagePageRejected):
            message_page.wait_for_final(timeout_milliseconds=invalid)


def test_bounded_waits_cover_transitions_timeouts_failures_and_early_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry_group = (
        'button[aria-label="私信"], [role="button"][aria-label="私信"], '
        '[data-e2e="direct-message-entry"]'
    )
    input_group = (
        'textarea[aria-label="发送私信"], textarea[placeholder="发送私信"], '
        '[contenteditable="true"][data-e2e="direct-message-input"]'
    )
    send_group = (
        'button[aria-label="发送私信"], [role="button"][aria-label="发送私信"], '
        '[data-e2e="direct-message-send"]'
    )
    final_group = (
        '[role="status"]:has-text("私信发送成功"), [data-e2e="direct-message-send-success"]'
    )

    conversation = FakePage(visible_selectors={MESSAGE_ENTRY})

    def show_message_input() -> None:
        conversation.visible_selectors.discard(MESSAGE_ENTRY)
        conversation.visible_selectors.add(MESSAGE_INPUT)

    conversation.wait_callbacks[input_group] = show_message_input
    conversation.wait_callbacks[send_group] = lambda: conversation.visible_selectors.add(
        MESSAGE_SEND
    )
    conversation_page = DouyinDirectMessagePage(window(conversation))
    assert conversation_page.wait_for_conversation_ready(
        timeout_milliseconds=100
    ).conversation_ready

    conversation.visible_selectors = {MESSAGE_INPUT, MESSAGE_SEND}
    conversation.wait_callbacks[final_group] = lambda: conversation.visible_selectors.add(
        FINAL_CONFIRMATION
    )
    assert conversation_page.wait_for_final(timeout_milliseconds=100).confirmed

    timed_out = DouyinDirectMessagePage(window(FakePage())).wait_for_profile_ready(
        timeout_milliseconds=100
    )
    assert timed_out.evidence is DouyinDirectMessagePageEvidence.REQUIRED_ANCHOR_MISSING

    failed = FakePage()
    failed.failed_wait_selectors.add(entry_group)
    unavailable = DouyinDirectMessagePage(window(failed)).wait_for_profile_ready(
        timeout_milliseconds=100
    )
    assert unavailable.evidence is DouyinDirectMessagePageEvidence.PAGE_UNAVAILABLE

    non_profile = DouyinDirectMessagePage(
        window(FakePage(url="https://www.douyin.com/search/test?type=general"))
    )
    assert non_profile.wait_for_profile_ready(timeout_milliseconds=100).evidence is (
        DouyinDirectMessagePageEvidence.PAGE_VERSION_UNKNOWN
    )

    denied = DouyinDirectMessagePage(window(FakePage(visible_selectors={FOLLOW_REQUIRED})))
    assert denied.wait_for_final(timeout_milliseconds=100).state is (
        DouyinDirectMessagePageState.PERMISSION_DENIED
    )

    premature = FakePage(visible_selectors={MESSAGE_INPUT, MESSAGE_SEND})
    premature.premature_wait_selectors.add(final_group)
    assert (
        DouyinDirectMessagePage(window(premature)).wait_for_final(timeout_milliseconds=100).state
        is DouyinDirectMessagePageState.CONVERSATION_READY
    )

    exhausted = FakePage()
    exhausted.premature_wait_selectors.update({input_group, send_group})
    assert (
        DouyinDirectMessagePage(window(exhausted))
        .wait_for_conversation_ready(timeout_milliseconds=100)
        .evidence
        is DouyinDirectMessagePageEvidence.REQUIRED_ANCHOR_MISSING
    )

    ticks = iter((0.0, 1.0))
    monkeypatch.setattr(direct_message_page_module, "monotonic", lambda: next(ticks))
    expired = DouyinDirectMessagePage(window(FakePage())).wait_for_profile_ready(
        timeout_milliseconds=100
    )
    assert expired.evidence is DouyinDirectMessagePageEvidence.REQUIRED_ANCHOR_MISSING
