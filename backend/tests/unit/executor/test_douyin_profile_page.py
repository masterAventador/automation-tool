from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any, cast

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

import automation_tool.executor.rpa.douyin.profile_page as profile_page_module
from automation_tool.executor.browser_runtime import BrowserWindow
from automation_tool.executor.rpa.douyin.page_version import (
    DOUYIN_SESSION_PROBE_URL,
    DouyinPageEntry,
    DouyinPageVersion,
)
from automation_tool.executor.rpa.douyin.profile_page import (
    DOUYIN_PROFILE_PAGE_SELECTOR_VERSION,
    DouyinProfilePage,
    DouyinProfilePageEvidence,
    DouyinProfilePageObservation,
    DouyinProfilePageRejected,
    DouyinProfilePageState,
)

PROFILE_URL = "https://www.douyin.com/user/creator-001"
PROFILE_ROOT = 'main[aria-label="用户主页"]'
LOGIN_DIALOG = '[role="dialog"]:has-text("扫码登录")'
BLOCKING_DIALOG = '[role="dialog"]'
RISK_CHALLENGE = 'iframe[src^="https://rmc.bytedance.com/verifycenter/captcha/"]'
PROFILE_GROUP = 'main[aria-label="用户主页"], [data-e2e="user-detail"], [data-e2e="user-profile"]'
VIDEO_ENTRY = 'main[aria-label="用户主页"] a[href^="/video/"]'
SECOND_PROFILE_ROOT = '[data-e2e="user-detail"]'
SECOND_VIDEO_ENTRY = '[data-e2e="user-detail"] a[href^="/video/"]'


class FakeLocator:
    def __init__(self, selector: str, page: FakePage) -> None:
        self.selector = selector
        self.page = page

    @property
    def first(self) -> FakeLocator:
        return self

    def count(self) -> int:
        if self.selector in self.page.failed_selectors:
            raise RuntimeError("private count failure")
        if self.selector in self.page.invalid_count_selectors:
            return cast(int, True)
        return sum(
            selector in self.page.visible_selectors for selector in self.selector.split(", ")
        )

    def is_visible(self) -> bool:
        if self.selector in self.page.failed_selectors:
            raise RuntimeError("private visibility failure")
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

    def get_attribute(self, name: str, *, timeout: float) -> str | None:
        assert name == "href"
        assert timeout > 0
        return self.page.attributes.get(self.selector)


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
        self.attributes: dict[str, str] = {}
        self.requested_selectors: list[str] = []

    def locator(self, selector: str) -> FakeLocator:
        self.requested_selectors.append(selector)
        return FakeLocator(selector, self)


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


class DriftingProfilePage(FakePage):
    def __init__(self, *, fail: bool) -> None:
        super().__init__(visible_selectors={PROFILE_ROOT})
        self._profile_requests = 0
        self._fail = fail

    def locator(self, selector: str) -> FakeLocator:
        if selector.startswith(PROFILE_ROOT):
            self._profile_requests += 1
            if self._profile_requests == 2:
                if self._fail:
                    self.failed_selectors.add(selector)
                else:
                    self.visible_selectors.remove(PROFILE_ROOT)
        return super().locator(selector)


def window(page: FakePage) -> BrowserWindow:
    return BrowserWindow._for_runtime(object(), cast(Any, page))


def test_profile_ready_is_one_closed_redacted_contract() -> None:
    observation = DouyinProfilePage(window(FakePage(visible_selectors={PROFILE_ROOT}))).observe()

    assert observation == DouyinProfilePageObservation(
        page_version=DouyinPageVersion.WEB_V1,
        entry=DouyinPageEntry.USER_PROFILE,
        state=DouyinProfilePageState.READY,
        evidence=DouyinProfilePageEvidence.PROFILE_ANCHOR_VISIBLE,
    )
    assert observation.selector_version == DOUYIN_PROFILE_PAGE_SELECTOR_VERSION
    assert observation.ready is True
    assert observation.circuit_open is False
    assert PROFILE_URL not in repr(observation)


@pytest.mark.parametrize("blocking", (BLOCKING_DIALOG, RISK_CHALLENGE))
def test_login_and_blocking_evidence_take_priority_over_profile_anchor(
    blocking: str,
) -> None:
    login = DouyinProfilePage(
        window(FakePage(visible_selectors={PROFILE_ROOT, LOGIN_DIALOG, blocking}))
    ).observe()
    assert login.state is DouyinProfilePageState.LOGIN_REQUIRED
    assert login.evidence is DouyinProfilePageEvidence.LOGIN_DIALOG

    blocked = DouyinProfilePage(
        window(FakePage(visible_selectors={PROFILE_ROOT, blocking}))
    ).observe()
    assert blocked.state is DouyinProfilePageState.DIALOG_BLOCKED
    assert blocked.evidence is DouyinProfilePageEvidence.BLOCKING_DIALOG


def test_non_profile_routes_stop_before_dom_queries() -> None:
    for url, state, evidence in (
        (
            DOUYIN_SESSION_PROBE_URL,
            DouyinProfilePageState.LOGIN_REQUIRED,
            DouyinProfilePageEvidence.LOGIN_REDIRECT,
        ),
        (
            "https://www.douyin.com/search/test?type=general",
            DouyinProfilePageState.UNKNOWN,
            DouyinProfilePageEvidence.PAGE_VERSION_UNKNOWN,
        ),
        (
            "https://example.com/user/creator-001",
            DouyinProfilePageState.UNKNOWN,
            DouyinProfilePageEvidence.PAGE_VERSION_UNKNOWN,
        ),
    ):
        page = FakePage(url=url, visible_selectors={PROFILE_ROOT})
        observation = DouyinProfilePage(window(page)).observe()
        assert observation.state is state
        assert observation.evidence is evidence
        assert page.requested_selectors == []


def test_missing_duplicate_bad_count_and_driver_failures_are_closed() -> None:
    missing = DouyinProfilePage(window(FakePage())).observe()
    assert missing.state is DouyinProfilePageState.UNKNOWN
    assert missing.evidence is DouyinProfilePageEvidence.REQUIRED_ANCHOR_MISSING

    duplicate = DouyinProfilePage(
        window(
            FakePage(
                visible_selectors={PROFILE_ROOT, '[data-e2e="user-detail"]'},
            )
        )
    ).observe()
    assert duplicate.evidence is DouyinProfilePageEvidence.CONFLICTING_ANCHORS

    for attribute in ("failed_selectors", "invalid_count_selectors"):
        page = FakePage(visible_selectors={PROFILE_ROOT})
        getattr(page, attribute).add(PROFILE_GROUP)
        unavailable = DouyinProfilePage(window(page)).observe()
        assert unavailable.evidence is DouyinProfilePageEvidence.PAGE_UNAVAILABLE


def test_profile_accessor_reobserves_and_rejects_mid_access_drift() -> None:
    page = FakePage(visible_selectors={PROFILE_ROOT})
    profile_page = DouyinProfilePage(window(page))
    assert PROFILE_ROOT in cast(FakeLocator, profile_page.profile_root()).selector
    page.visible_selectors.clear()
    with pytest.raises(DouyinProfilePageRejected):
        profile_page.profile_root()

    for fail in (False, True):
        with pytest.raises(DouyinProfilePageRejected):
            DouyinProfilePage(window(DriftingProfilePage(fail=fail))).profile_root()


def test_first_video_entry_is_validated_without_clicking_or_exposing_the_href() -> None:
    page = FakePage(visible_selectors={PROFILE_ROOT, VIDEO_ENTRY})
    page.attributes[VIDEO_ENTRY] = "/video/7351234567890123456"

    entry = DouyinProfilePage(window(page)).first_video_entry()

    assert cast(FakeLocator, entry).selector.startswith(VIDEO_ENTRY)
    for href in (None, "/video/0", "/video/private", "https://evil.example/video/1"):
        page.attributes[VIDEO_ENTRY] = cast(str, href)
        if href is None:
            page.attributes.pop(VIDEO_ENTRY)
        with pytest.raises(DouyinProfilePageRejected):
            DouyinProfilePage(window(page)).first_video_entry()

    fallback = FakePage(visible_selectors={SECOND_PROFILE_ROOT, SECOND_VIDEO_ENTRY})
    fallback.attributes[SECOND_VIDEO_ENTRY] = "/video/7351234567890123456"
    assert cast(FakeLocator, DouyinProfilePage(window(fallback)).first_video_entry()).selector == (
        SECOND_VIDEO_ENTRY
    )

    with pytest.raises(DouyinProfilePageRejected):
        DouyinProfilePage(window(FakePage(visible_selectors={PROFILE_ROOT}))).first_video_entry()
    with pytest.raises(DouyinProfilePageRejected):
        DouyinProfilePage(
            window(FakePage(visible_selectors={PROFILE_ROOT, LOGIN_DIALOG}))
        ).first_video_entry()


def test_bounded_wait_covers_ready_timeout_failure_expiry_and_early_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FakePage()
    page.wait_callbacks[PROFILE_GROUP] = lambda: page.visible_selectors.add(PROFILE_ROOT)
    ready = DouyinProfilePage(window(page)).wait_for_ready(timeout_milliseconds=100)
    assert ready.ready is True

    timed_out = DouyinProfilePage(window(FakePage())).wait_for_ready(timeout_milliseconds=100)
    assert timed_out.evidence is DouyinProfilePageEvidence.REQUIRED_ANCHOR_MISSING

    failed = FakePage()
    failed.failed_wait_selectors.add(PROFILE_GROUP)
    unavailable = DouyinProfilePage(window(failed)).wait_for_ready(timeout_milliseconds=100)
    assert unavailable.evidence is DouyinProfilePageEvidence.PAGE_UNAVAILABLE

    blocked = DouyinProfilePage(
        window(FakePage(visible_selectors={BLOCKING_DIALOG}))
    ).wait_for_ready(timeout_milliseconds=100)
    assert blocked.state is DouyinProfilePageState.DIALOG_BLOCKED

    premature = FakePage()
    premature.premature_wait_selectors.add(PROFILE_GROUP)
    still_missing = DouyinProfilePage(window(premature)).wait_for_ready(timeout_milliseconds=100)
    assert still_missing.evidence is DouyinProfilePageEvidence.REQUIRED_ANCHOR_MISSING

    ticks = iter((0.0, 1.0))
    monkeypatch.setattr(profile_page_module, "monotonic", lambda: next(ticks))
    expired = DouyinProfilePage(window(FakePage())).wait_for_ready(timeout_milliseconds=100)
    assert expired.evidence is DouyinProfilePageEvidence.REQUIRED_ANCHOR_MISSING

    for invalid in (0, 60_001, True):
        with pytest.raises(DouyinProfilePageRejected):
            DouyinProfilePage(window(page)).wait_for_ready(timeout_milliseconds=invalid)


def test_observation_constructor_url_failure_and_repr_are_closed() -> None:
    valid = DouyinProfilePageObservation(
        page_version=DouyinPageVersion.WEB_V1,
        entry=DouyinPageEntry.USER_PROFILE,
        state=DouyinProfilePageState.READY,
        evidence=DouyinProfilePageEvidence.PROFILE_ANCHOR_VISIBLE,
    )
    for changes in (
        {"state": cast(DouyinProfilePageState, "ready")},
        {"selector_version": "private"},
        {"entry": DouyinPageEntry.HOME},
        {"evidence": DouyinProfilePageEvidence.PAGE_UNAVAILABLE},
    ):
        with pytest.raises(DouyinProfilePageRejected):
            replace(valid, **changes)
    with pytest.raises(DouyinProfilePageRejected):
        DouyinProfilePage(cast(BrowserWindow, object()))
    profile_page = DouyinProfilePage(window(FakePage()))
    assert repr(profile_page) == "DouyinProfilePage(<redacted>)"

    unavailable = DouyinProfilePage(window(FailingUrlPage())).observe()
    assert unavailable.page_version is DouyinPageVersion.UNKNOWN
    assert unavailable.evidence is DouyinProfilePageEvidence.PAGE_UNAVAILABLE
