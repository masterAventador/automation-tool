from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from automation_tool.executor.browser_runtime import BrowserWindow
from automation_tool.executor.rpa.douyin.page_anchors import VISIBLE_MATCH_ENGINE
from automation_tool.executor.rpa.douyin.publish_page import (
    DOUYIN_PUBLISH_ENTRY_URL,
    DOUYIN_PUBLISH_FORM_ANCHORS,
    DOUYIN_PUBLISH_FORM_ROUTE,
    DOUYIN_PUBLISH_MANAGE_URL,
    DOUYIN_PUBLISH_PAGE_SELECTOR_VERSION,
    DOUYIN_PUBLISH_UPLOAD_ROUTE,
    MAX_DOUYIN_PUBLISH_WORKS_READ,
    DouyinPublishPage,
    DouyinPublishPageEvidence,
    DouyinPublishPageObservation,
    DouyinPublishPageRejected,
    DouyinPublishPageState,
    DouyinPublishRoute,
    DouyinPublishRouteModel,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
CONTRACT_PATH = REPOSITORY_ROOT / "contracts/publishing/douyin-browser-use-preflight.v1.json"

FORM_URL = "https://creator.douyin.com/creator-micro/content/post/video"
ARTIFACT_INPUT = '[data-e2e="publish-artifact-input"]'
TITLE_INPUT = '[data-e2e="publish-title-input"]'
DESCRIPTION_INPUT = '[data-e2e="publish-description-input"]'
SUBMIT_CONTROL = '[data-e2e="publish-submit"]'
ACCOUNT_NAME = '[data-e2e="publish-account-name"]'
LOGIN_PANEL = '[data-e2e="login-panel"]'
BLOCKING_DIALOG = '[role="dialog"]'
RISK_CHALLENGE = 'iframe[src^="https://rmc.bytedance.com/verifycenter/captcha/"]'
FORM_SELECTORS = (TITLE_INPUT, DESCRIPTION_INPUT, SUBMIT_CONTROL)


class FakeLocator:
    """Models grouped matches plus Playwright's visible-only filtering."""

    def __init__(
        self,
        selector: str,
        page: FakePage,
        *,
        wait_callback: Callable[[], None] | None = None,
        visible_only: bool = False,
        first_only: bool = False,
    ) -> None:
        self.selector = selector
        self._page = page
        self._wait_callback = wait_callback
        self._visible_only = visible_only
        self._first_only = first_only

    @property
    def first(self) -> FakeLocator:
        return self._derived(visible_only=self._visible_only, first_only=True)

    def locator(self, selector: str) -> FakeLocator:
        assert selector == VISIBLE_MATCH_ENGINE
        return self._derived(visible_only=True, first_only=self._first_only)

    def _derived(self, *, visible_only: bool, first_only: bool) -> FakeLocator:
        return type(self)(
            self.selector,
            self._page,
            wait_callback=self._wait_callback,
            visible_only=visible_only,
            first_only=first_only,
        )

    def _matches(self) -> list[bool]:
        matches: list[bool] = []
        for candidate in self.selector.split(", "):
            matches.extend(False for _ in range(candidate in self._page.hidden_selectors))
            matches.extend(True for _ in range(candidate in self._page.visible_selectors))
        matches = [match for match in matches if match or not self._visible_only]
        return matches[:1] if self._first_only else matches

    def count(self) -> int:
        if self.selector in self._page.failed_selectors:
            raise RuntimeError("private count failure")
        return len(self._matches())

    def is_visible(self) -> bool:
        if self.selector in self._page.failed_selectors:
            raise RuntimeError("private visibility failure")
        return any(self._matches())

    def fill(self, value: str, *, timeout: float) -> None:
        assert timeout > 0
        for selector in self.selector.split(", "):
            self._page.filled[selector] = value

    def inner_text(self) -> str:
        for selector in self.selector.split(", "):
            if selector in self._page.texts:
                return self._page.texts[selector]
        return ""

    def click(self, *, timeout: float) -> None:
        assert timeout > 0
        # Playwright presses the one element the group resolved to, not the group.
        for selector in self.selector.split(", "):
            if selector not in self._page.visible_selectors:
                continue
            failure = self._page.click_failures.get(selector)
            if failure is not None:
                raise failure
            self._page.clicked.append(selector)

    def element_handles(self) -> list[FakeHandle]:
        """One resolution of the whole match set, the way Playwright snapshots."""
        self._page.handle_resolutions += 1
        return [
            FakeHandle(index, text, self._page)
            for index, (text, visible) in enumerate(self._page.work_titles)
            if visible or not self._visible_only
        ]

    def is_enabled(self) -> bool:
        if self.selector in self._page.disabled_selectors:
            return False
        if self.selector in self._page.failed_selectors:
            raise RuntimeError("private enabled failure")
        return True

    def wait_for(self, *, state: str, timeout: float) -> None:
        assert state == "visible"
        assert timeout > 0
        if self._wait_callback is not None:
            self._wait_callback()
        matches = self._matches()
        if not matches or not matches[0]:
            self._page.wait_timeouts.append(self.selector)
            raise PlaywrightTimeoutError("private wait timeout")


class FakeHandle:
    """A resolved node snapshot: it never re-evaluates its selector."""

    def __init__(self, index: int, text: str, page: FakePage) -> None:
        self._index = index
        self._text = text
        self._page = page

    def inner_text(self) -> str:
        if self._page.failing_handle_index == self._index:
            raise RuntimeError("private row read failure")
        return self._text

    def dispose(self) -> None:
        self._page.disposed.append(self._index)


class FakePage:
    def __init__(
        self,
        *,
        url: str = DOUYIN_PUBLISH_ENTRY_URL,
        visible_selectors: set[str] | None = None,
    ) -> None:
        self.url = url
        self.visible_selectors = set() if visible_selectors is None else visible_selectors
        self.hidden_selectors: set[str] = set()
        self.texts: dict[str, str] = {}
        self.filled: dict[str, str] = {}
        self.failed_selectors: set[str] = set()
        self.disabled_selectors: set[str] = set()
        self.wait_callbacks: dict[str, Callable[[], None]] = {}
        self.wait_timeouts: list[str] = []
        self.navigations: list[str] = []
        self.clicked: list[str] = []
        self.requested_selectors: list[str] = []
        self.work_titles: list[tuple[str, bool]] = []
        self.disposed: list[int] = []
        self.handle_resolutions = 0
        self.failing_handle_index: int | None = None
        self.click_failures: dict[str, BaseException] = {}
        self.navigation_callbacks: dict[str, Callable[[], None]] = {}

    def locator(self, selector: str) -> FakeLocator:
        self.requested_selectors.append(selector)
        return FakeLocator(selector, self, wait_callback=self.wait_callbacks.get(selector))

    def goto(self, url: str, *, wait_until: str, timeout: float) -> None:
        assert wait_until == "domcontentloaded"
        assert timeout > 0
        self.navigations.append(url)
        self.url = url
        callback = self.navigation_callbacks.get(url)
        if callback is not None:
            callback()


class FailingUrlPage(FakePage):
    @property
    def url(self) -> str:
        raise RuntimeError("private URL failure")

    @url.setter
    def url(self, value: str) -> None:
        self._url = value


def window(page: FakePage) -> BrowserWindow:
    return BrowserWindow._for_runtime(object(), cast(Any, page))


def upload_page(**visible: bool) -> FakePage:
    selectors = {ARTIFACT_INPUT} if visible.get("artifact", True) else set()
    return FakePage(url=DOUYIN_PUBLISH_ENTRY_URL, visible_selectors=selectors)


def form_page() -> FakePage:
    return FakePage(url=FORM_URL, visible_selectors=set(FORM_SELECTORS))


def test_contract_pins_the_publish_surface_routes() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    surface = contract["surface"]
    assert surface["entryUrl"] == DOUYIN_PUBLISH_ENTRY_URL
    assert surface["uploadRoute"] == DOUYIN_PUBLISH_UPLOAD_ROUTE
    assert surface["formRoute"] == DOUYIN_PUBLISH_FORM_ROUTE
    assert contract["stopBeforeSubmit"] is True


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (DOUYIN_PUBLISH_ENTRY_URL, DouyinPublishRoute.UPLOAD_ENTRY),
        (FORM_URL, DouyinPublishRoute.POST_FORM),
        # A page revision that moves the publish flow elsewhere is never guessed.
        ("https://creator.douyin.com/creator-micro/content", DouyinPublishRoute.UNKNOWN),
        (f"{DOUYIN_PUBLISH_ENTRY_URL}?from=x", DouyinPublishRoute.UNKNOWN),
        ("https://www.douyin.com/creator-micro/content/upload", DouyinPublishRoute.UNKNOWN),
        ("http://creator.douyin.com/creator-micro/content/upload", DouyinPublishRoute.UNKNOWN),
        (
            "https://creator.douyin.com:8443/creator-micro/content/upload",
            DouyinPublishRoute.UNKNOWN,
        ),
        (
            "https://user@creator.douyin.com/creator-micro/content/upload",
            DouyinPublishRoute.UNKNOWN,
        ),
        ("https://creator.douyin.com/creator-micro/content/upload#x", DouyinPublishRoute.UNKNOWN),
        (
            "https://creator.douyin.com.evil.test/creator-micro/content/upload",
            DouyinPublishRoute.UNKNOWN,
        ),
        (f"{DOUYIN_PUBLISH_ENTRY_URL}\u202e", DouyinPublishRoute.UNKNOWN),
        (f"{DOUYIN_PUBLISH_ENTRY_URL}/../post", DouyinPublishRoute.UNKNOWN),
        ("", DouyinPublishRoute.UNKNOWN),
        (DOUYIN_PUBLISH_ENTRY_URL.encode(), DouyinPublishRoute.UNKNOWN),
    ],
)
def test_route_model_recognizes_only_the_frozen_publish_routes(
    url: object, expected: DouyinPublishRoute
) -> None:
    assert DouyinPublishRouteModel().check(url) is expected


def test_over_long_route_is_rejected() -> None:
    assert (
        DouyinPublishRouteModel().check(f"{DOUYIN_PUBLISH_ENTRY_URL}{'a' * 4096}")
        is DouyinPublishRoute.UNKNOWN
    )


def test_upload_entry_with_the_artifact_input_is_awaiting_artifact() -> None:
    observation = DouyinPublishPage(window(upload_page())).observe()
    assert observation.route is DouyinPublishRoute.UPLOAD_ENTRY
    assert observation.state is DouyinPublishPageState.AWAITING_ARTIFACT
    assert observation.evidence is DouyinPublishPageEvidence.ARTIFACT_INPUT_VISIBLE
    assert observation.selector_version == DOUYIN_PUBLISH_PAGE_SELECTOR_VERSION
    assert not observation.circuit_open
    assert not observation.handoff_required


def test_post_form_with_all_anchors_is_form_ready() -> None:
    observation = DouyinPublishPage(window(form_page())).observe()
    assert observation.route is DouyinPublishRoute.POST_FORM
    assert observation.state is DouyinPublishPageState.FORM_READY
    assert observation.evidence is DouyinPublishPageEvidence.FORM_ANCHORS_VISIBLE
    assert not observation.circuit_open


def test_missing_form_anchor_is_page_drift_not_a_guess() -> None:
    page = form_page()
    page.visible_selectors.discard(TITLE_INPUT)
    observation = DouyinPublishPage(window(page)).observe()
    assert observation.state is DouyinPublishPageState.UNKNOWN
    assert observation.evidence is DouyinPublishPageEvidence.REQUIRED_ANCHOR_MISSING
    assert observation.circuit_open


def test_expired_login_panel_requires_human_handoff() -> None:
    page = form_page()
    page.visible_selectors.add(LOGIN_PANEL)
    observation = DouyinPublishPage(window(page)).observe()
    assert observation.state is DouyinPublishPageState.LOGIN_REQUIRED
    assert observation.evidence is DouyinPublishPageEvidence.LOGIN_PANEL
    assert observation.handoff_required
    assert observation.circuit_open


def test_risk_challenge_always_hands_off_and_is_never_bypassed() -> None:
    page = form_page()
    page.visible_selectors.add(RISK_CHALLENGE)
    observation = DouyinPublishPage(window(page)).observe()
    assert observation.state is DouyinPublishPageState.RISK_CHALLENGE
    assert observation.evidence is DouyinPublishPageEvidence.RISK_CHALLENGE
    assert observation.handoff_required


def test_blocking_overlay_stops_the_flow() -> None:
    page = form_page()
    page.visible_selectors.add(BLOCKING_DIALOG)
    observation = DouyinPublishPage(window(page)).observe()
    assert observation.state is DouyinPublishPageState.DIALOG_BLOCKED
    assert observation.evidence is DouyinPublishPageEvidence.BLOCKING_DIALOG
    assert observation.circuit_open
    assert not observation.handoff_required


def test_conflicting_anchor_duplicates_are_reported_as_drift() -> None:
    page = form_page()
    page.visible_selectors.add('input[placeholder*="作品标题"]')
    observation = DouyinPublishPage(window(page)).observe()
    assert observation.state is DouyinPublishPageState.UNKNOWN
    assert observation.evidence is DouyinPublishPageEvidence.CONFLICTING_ANCHORS


def test_unknown_route_never_reports_a_publish_state() -> None:
    page = FakePage(url="https://creator.douyin.com/creator-micro/home")
    observation = DouyinPublishPage(window(page)).observe()
    assert observation.route is DouyinPublishRoute.UNKNOWN
    assert observation.state is DouyinPublishPageState.UNKNOWN
    assert observation.evidence is DouyinPublishPageEvidence.ROUTE_UNKNOWN


def test_unreadable_page_is_reported_as_unavailable() -> None:
    observation = DouyinPublishPage(window(FailingUrlPage())).observe()
    assert observation.state is DouyinPublishPageState.UNKNOWN
    assert observation.evidence is DouyinPublishPageEvidence.PAGE_UNAVAILABLE


def test_locator_failure_is_reported_as_unavailable() -> None:
    page = form_page()
    page.failed_selectors.add(", ".join(_selectors_of(page, TITLE_INPUT)))
    observation = DouyinPublishPage(window(page)).observe()
    assert observation.state is DouyinPublishPageState.UNKNOWN
    assert observation.evidence is DouyinPublishPageEvidence.PAGE_UNAVAILABLE


def _selectors_of(page: FakePage, anchor: str) -> tuple[str, ...]:
    from automation_tool.executor.rpa.douyin.publish_page import DOUYIN_PUBLISH_TITLE_SELECTORS

    assert anchor in DOUYIN_PUBLISH_TITLE_SELECTORS
    return DOUYIN_PUBLISH_TITLE_SELECTORS


def test_anchors_require_the_matching_state() -> None:
    page = upload_page()
    publish_page = DouyinPublishPage(window(page))
    assert publish_page.artifact_input() is not None
    with pytest.raises(DouyinPublishPageRejected):
        publish_page.title_input()
    with pytest.raises(DouyinPublishPageRejected):
        publish_page.description_input()
    with pytest.raises(DouyinPublishPageRejected):
        publish_page.submit_enabled()


def test_form_anchors_and_submit_state_are_available_on_the_form() -> None:
    page = form_page()
    publish_page = DouyinPublishPage(window(page))
    assert publish_page.title_input() is not None
    assert publish_page.description_input() is not None
    assert publish_page.submit_enabled() is True
    page.disabled_selectors.add(", ".join(_submit_selectors()))
    assert publish_page.submit_enabled() is False
    with pytest.raises(DouyinPublishPageRejected):
        publish_page.artifact_input()


def _submit_selectors() -> tuple[str, ...]:
    from automation_tool.executor.rpa.douyin.publish_page import DOUYIN_PUBLISH_SUBMIT_SELECTORS

    return DOUYIN_PUBLISH_SUBMIT_SELECTORS


def test_open_entry_navigates_to_the_frozen_entry_url() -> None:
    page = FakePage(url="https://creator.douyin.com/creator-micro/home")
    page.wait_callbacks[", ".join(_artifact_selectors())] = lambda: page.visible_selectors.add(
        ARTIFACT_INPUT
    )

    def goto(url: str, *, wait_until: str, timeout: float) -> None:
        assert wait_until == "domcontentloaded"
        assert timeout > 0
        page.navigations.append(url)
        page.url = url

    page.goto = goto  # type: ignore[method-assign]
    observation = DouyinPublishPage(window(page)).open_entry(timeout_milliseconds=1_000)
    assert page.navigations == [DOUYIN_PUBLISH_ENTRY_URL]
    assert observation.state is DouyinPublishPageState.AWAITING_ARTIFACT


def _artifact_selectors() -> tuple[str, ...]:
    from automation_tool.executor.rpa.douyin.publish_page import DOUYIN_PUBLISH_ARTIFACT_SELECTORS

    return DOUYIN_PUBLISH_ARTIFACT_SELECTORS


def test_wait_for_form_returns_the_drifted_observation_without_guessing() -> None:
    page = FakePage(url=FORM_URL)
    observation = DouyinPublishPage(window(page)).wait_for_form(timeout_milliseconds=10)
    assert observation.state is DouyinPublishPageState.UNKNOWN
    assert observation.evidence is DouyinPublishPageEvidence.REQUIRED_ANCHOR_MISSING


def test_wait_timeouts_are_bounded() -> None:
    publish_page = DouyinPublishPage(window(form_page()))
    for invalid in (0, -1, 10**9, 1.5, "1000"):
        with pytest.raises(DouyinPublishPageRejected):
            publish_page.wait_for_form(timeout_milliseconds=cast(Any, invalid))


def test_illegal_observations_are_rejected() -> None:
    with pytest.raises(DouyinPublishPageRejected):
        DouyinPublishPageObservation(
            route=DouyinPublishRoute.UNKNOWN,
            state=DouyinPublishPageState.FORM_READY,
            evidence=DouyinPublishPageEvidence.FORM_ANCHORS_VISIBLE,
        )
    with pytest.raises(DouyinPublishPageRejected):
        DouyinPublishPageObservation(
            route=DouyinPublishRoute.POST_FORM,
            state=DouyinPublishPageState.FORM_READY,
            evidence=DouyinPublishPageEvidence.FORM_ANCHORS_VISIBLE,
            selector_version="douyin.publish-page.v0",
        )


def test_page_object_requires_a_runtime_owned_window() -> None:
    with pytest.raises(DouyinPublishPageRejected):
        DouyinPublishPage(cast(Any, object()))


def test_observation_repr_exposes_no_page_content() -> None:
    observation = DouyinPublishPage(window(form_page())).observe()
    text = repr(observation)
    assert "form_ready" in text
    assert repr(DouyinPublishPage(window(form_page()))) == "DouyinPublishPage(<redacted>)"


def test_route_model_repr_names_the_selector_version() -> None:
    assert DOUYIN_PUBLISH_PAGE_SELECTOR_VERSION in repr(DouyinPublishRouteModel())


def test_anchor_lookup_failures_are_rejected_not_guessed() -> None:
    page = form_page()
    publish_page = DouyinPublishPage(window(page))
    page.failed_selectors.add(", ".join(_submit_selectors()))
    with pytest.raises(DouyinPublishPageRejected):
        publish_page.submit_enabled()


def test_disabled_lookup_failure_is_rejected() -> None:
    page = form_page()
    publish_page = DouyinPublishPage(window(page))
    page.visible_selectors.discard(SUBMIT_CONTROL)
    with pytest.raises(DouyinPublishPageRejected):
        publish_page.submit_enabled()


class FailingWaitLocator(FakeLocator):
    def wait_for(self, *, state: str, timeout: float) -> None:
        raise RuntimeError("private wait failure")


def test_non_timeout_wait_failure_reports_page_unavailable() -> None:
    class FailingWaitPage(FakePage):
        def locator(self, selector: str) -> FakeLocator:
            return FailingWaitLocator(selector, self)

    page = FailingWaitPage(url=FORM_URL)
    observation = DouyinPublishPage(window(page)).wait_for_form(timeout_milliseconds=1_000)
    assert observation.evidence is DouyinPublishPageEvidence.PAGE_UNAVAILABLE


def test_invalid_locator_count_is_reported_as_unavailable() -> None:
    class InvalidCountLocator(FakeLocator):
        def locator(self, selector: str) -> FakeLocator:
            visible = super().locator(selector)
            visible.count = lambda: cast(int, "many")  # type: ignore[method-assign]
            return visible

    class InvalidCountPage(FakePage):
        def locator(self, selector: str) -> FakeLocator:
            return InvalidCountLocator(selector, self)

    page = InvalidCountPage(url=FORM_URL)
    observation = DouyinPublishPage(window(page)).observe()
    assert observation.evidence is DouyinPublishPageEvidence.PAGE_UNAVAILABLE


def test_unreadable_url_during_a_wait_still_reports_unavailable() -> None:
    class BreakingWaitLocator(FakeLocator):
        def wait_for(self, *, state: str, timeout: float) -> None:
            cast(Any, self._page).broken = True
            raise RuntimeError("private wait failure")

    class UnreadableAfterWaitPage(FakePage):
        def __init__(self) -> None:
            super().__init__(url=FORM_URL)
            self.broken = False

        def locator(self, selector: str) -> FakeLocator:
            return BreakingWaitLocator(selector, self)

        @property
        def url(self) -> str:
            if self.broken:
                raise RuntimeError("private URL failure")
            return FORM_URL

        @url.setter
        def url(self, value: str) -> None:
            self._url = value

    observation = DouyinPublishPage(window(UnreadableAfterWaitPage())).wait_for_form(
        timeout_milliseconds=1_000
    )
    assert observation.evidence is DouyinPublishPageEvidence.PAGE_UNAVAILABLE


def test_a_hidden_placeholder_never_hides_a_visible_challenge() -> None:
    """Regression: a hidden pre-rendered challenge node must not fail open."""
    page = upload_page()
    page.hidden_selectors.add(RISK_CHALLENGE)
    page.visible_selectors.add(RISK_CHALLENGE)

    observation = DouyinPublishPage(window(page)).observe()

    assert observation.state is DouyinPublishPageState.RISK_CHALLENGE
    assert observation.handoff_required


def test_waiting_is_satisfied_by_the_visible_anchor_behind_a_hidden_placeholder() -> None:
    """Pinning the wait to the first match can only ever burn the whole budget.

    Entering the wait means the form anchors are not visible yet, so the first
    match is the pre-rendered placeholder. It never becomes visible, and the
    real field inserted behind it is never resolved, so a form that is in fact
    ready is reported as a drifted page after the full timeout.
    """
    page = FakePage(url=FORM_URL)
    page.hidden_selectors.update(FORM_SELECTORS)
    for selectors in DOUYIN_PUBLISH_FORM_ANCHORS:
        group = ", ".join(selectors)
        page.wait_callbacks[group] = lambda anchors=selectors: page.visible_selectors.update(  # type: ignore[misc]
            anchor for anchor in anchors if anchor in FORM_SELECTORS
        )

    observation = DouyinPublishPage(window(page)).wait_for_form(timeout_milliseconds=1_000)

    assert observation.state is DouyinPublishPageState.FORM_READY
    assert page.wait_timeouts == []


def test_a_hidden_duplicate_anchor_is_not_a_conflict() -> None:
    page = form_page()
    page.hidden_selectors.add('input[placeholder*="作品标题"]')

    observation = DouyinPublishPage(window(page)).observe()

    assert observation.state is DouyinPublishPageState.FORM_READY


def test_form_fields_do_not_hand_the_underlying_locator_back() -> None:
    """No attribute on a field walks back to the locator that can click."""
    page = form_page()
    publish_page = DouyinPublishPage(window(page))
    controls = (
        publish_page.title_input(),
        publish_page.description_input(),
        DouyinPublishPage(window(upload_page())).artifact_input(),
    )
    for control in controls:
        # The locator is not reachable through attributes, __dict__ or slots.
        assert not hasattr(control, "_locator")
        assert not hasattr(control, "__dict__")
        exposed = [getattr(control, name) for name in dir(control) if not name.startswith("__")]
        assert all(not isinstance(value, FakeLocator) for value in exposed)
        for forbidden in ("click", "press", "evaluate", "dblclick", "tap", "goto"):
            assert not hasattr(control, forbidden), forbidden
        assert "redacted" in repr(control)


def test_the_fields_still_write_through_to_the_real_locator() -> None:
    page = form_page()
    DouyinPublishPage(window(page)).title_input().fill("标题", timeout=1_000)
    assert page.filled[TITLE_INPUT] == "标题"


def test_target_account_is_read_through_the_page_object_anchors() -> None:
    page = form_page()
    page.visible_selectors.add(ACCOUNT_NAME)
    page.texts[ACCOUNT_NAME] = "运营账号"
    assert DouyinPublishPage(window(page)).target_account() == "运营账号"


def test_absent_or_ambiguous_account_anchor_reports_nothing() -> None:
    page = form_page()
    assert DouyinPublishPage(window(page)).target_account() is None
    page.visible_selectors.add(ACCOUNT_NAME)
    page.visible_selectors.add('[data-e2e="creator-account-name"]')
    assert DouyinPublishPage(window(page)).target_account() is None


# --- PB-06: the single click and the independent works-list evidence ---------


WORK_LIST = '[data-e2e="publish-work-list"]'
WORK_TITLE = '[data-e2e="publish-work-title"]'
MANAGE_URL = "https://creator.douyin.com/creator-micro/content/manage"


def works_page(*titles: str) -> FakePage:
    page = FakePage(url=MANAGE_URL, visible_selectors={WORK_LIST})
    page.work_titles = [(title, True) for title in titles]
    return page


def test_the_manage_route_is_recognized_as_its_own_frozen_route() -> None:
    assert DouyinPublishRouteModel().check(MANAGE_URL) is DouyinPublishRoute.MANAGE_LIST


def test_the_works_list_is_reached_through_the_frozen_manage_route() -> None:
    page = works_page()

    observation = DouyinPublishPage(window(page)).open_works_list(timeout_milliseconds=1_000)

    assert page.navigations == [MANAGE_URL]
    assert observation.state is DouyinPublishPageState.WORKS_LIST_READY
    assert observation.evidence is DouyinPublishPageEvidence.WORK_LIST_VISIBLE


@pytest.mark.parametrize(
    ("selector", "state"),
    [
        (LOGIN_PANEL, DouyinPublishPageState.LOGIN_REQUIRED),
        (RISK_CHALLENGE, DouyinPublishPageState.RISK_CHALLENGE),
        (BLOCKING_DIALOG, DouyinPublishPageState.DIALOG_BLOCKED),
    ],
)
def test_the_works_list_reports_the_same_handoff_states_as_the_form(
    selector: str,
    state: DouyinPublishPageState,
) -> None:
    page = works_page()
    page.visible_selectors.add(selector)

    assert DouyinPublishPage(window(page)).observe().state is state


def test_a_manage_page_without_its_list_anchor_is_not_evidence_of_anything() -> None:
    page = FakePage(url=MANAGE_URL)

    observation = DouyinPublishPage(window(page)).observe()

    assert observation.state is DouyinPublishPageState.UNKNOWN
    assert observation.evidence is DouyinPublishPageEvidence.REQUIRED_ANCHOR_MISSING


def test_one_visible_work_with_the_confirmed_title_is_counted() -> None:
    page = works_page("其他作品", "确认过的标题")

    assert DouyinPublishPage(window(page)).works_titled("确认过的标题") == 1


def test_no_work_with_the_confirmed_title_counts_zero() -> None:
    page = works_page("其他作品")

    assert DouyinPublishPage(window(page)).works_titled("确认过的标题") == 0


def test_two_works_sharing_the_confirmed_title_are_both_counted() -> None:
    """The caller has to treat an ambiguous list as uncertain, not as success."""
    page = works_page("确认过的标题", "确认过的标题")

    assert DouyinPublishPage(window(page)).works_titled("确认过的标题") == 2


def test_a_hidden_work_row_is_not_independent_evidence() -> None:
    page = works_page("其他作品")
    page.work_titles.append(("确认过的标题", False))

    assert DouyinPublishPage(window(page)).works_titled("确认过的标题") == 0


def test_surrounding_whitespace_in_a_rendered_title_still_matches() -> None:
    page = works_page("\n  确认过的标题\t ")

    assert DouyinPublishPage(window(page)).works_titled("确认过的标题") == 1


def test_the_confirmed_title_is_never_spliced_into_a_selector() -> None:
    """A title is user content; interpolating it would be selector injection."""
    hostile = '"], [data-e2e="publish-work-title'
    page = works_page(hostile)

    assert DouyinPublishPage(window(page)).works_titled(hostile) == 1
    assert all(hostile not in selector for selector in page.requested_selectors)


def test_every_row_snapshot_is_released_even_when_a_read_fails() -> None:
    page = works_page("确认过的标题", "第二个作品")
    page.failing_handle_index = 1

    with pytest.raises(RuntimeError):
        DouyinPublishPage(window(page)).works_titled("确认过的标题")

    assert page.disposed == [0, 1]


def test_the_rows_are_read_from_one_snapshot_not_re_resolved_per_field() -> None:
    """A list that grows mid-read must not shift which row a title came from."""
    page = works_page("确认过的标题")

    DouyinPublishPage(window(page)).works_titled("确认过的标题")

    assert page.handle_resolutions == 1


def test_an_unbounded_works_list_is_refused_instead_of_read_row_by_row() -> None:
    page = works_page(*[f"作品{index}" for index in range(MAX_DOUYIN_PUBLISH_WORKS_READ + 1)])

    with pytest.raises(DouyinPublishPageRejected):
        DouyinPublishPage(window(page)).works_titled("作品0")

    assert page.disposed == list(range(MAX_DOUYIN_PUBLISH_WORKS_READ + 1))


def test_the_works_list_cannot_be_read_from_the_form_page() -> None:
    with pytest.raises(DouyinPublishPageRejected):
        DouyinPublishPage(window(form_page())).works_titled("确认过的标题")


def test_the_submit_control_is_only_reachable_from_a_ready_form() -> None:
    with pytest.raises(DouyinPublishPageRejected):
        DouyinPublishPage(window(upload_page())).submit_control()


def test_the_submit_control_presses_the_real_publish_button_once() -> None:
    page = form_page()

    DouyinPublishPage(window(page)).submit_control().click(timeout=1_000)

    assert page.clicked == [SUBMIT_CONTROL]


def test_the_submit_control_hands_back_nothing_but_the_click() -> None:
    control = DouyinPublishPage(window(form_page())).submit_control()

    assert not hasattr(control, "__dict__")
    assert not hasattr(control, "_locator")
    for forbidden in ("fill", "press", "evaluate", "goto", "set_input_files"):
        assert not hasattr(control, forbidden), forbidden
    exposed = [getattr(control, name) for name in dir(control) if not name.startswith("__")]
    assert all(not isinstance(value, FakeLocator) for value in exposed)
    assert "redacted" in repr(control)


def _works_list_page(*, titles: tuple[tuple[str, bool], ...] = (("已发布作品", True),)) -> FakePage:
    page = FakePage(url=DOUYIN_PUBLISH_MANAGE_URL, visible_selectors={WORK_LIST})
    page.work_titles = list(titles)
    return page


def test_counting_works_refuses_a_title_that_is_not_text() -> None:
    """The title is operator content and is compared in Python, never spliced in."""
    page = DouyinPublishPage(window(_works_list_page()))

    with pytest.raises(DouyinPublishPageRejected):
        page.works_titled(cast(Any, 1))


def test_counting_works_refuses_a_list_it_cannot_resolve() -> None:
    class _Refusing(FakePage):
        def locator(self, selector: str) -> Any:
            raise RuntimeError("private locator failure")

    page = _Refusing(url=DOUYIN_PUBLISH_MANAGE_URL, visible_selectors={WORK_LIST})

    with pytest.raises(DouyinPublishPageRejected):
        DouyinPublishPage(window(page)).works_titled("已发布作品")


def test_a_works_list_longer_than_it_will_read_is_refused() -> None:
    """A page that keeps growing is not scanned without bound."""
    from automation_tool.executor.rpa.douyin.publish_page import MAX_DOUYIN_PUBLISH_WORKS_READ

    page = _works_list_page(
        titles=tuple(("作品", True) for _ in range(MAX_DOUYIN_PUBLISH_WORKS_READ + 1))
    )

    with pytest.raises(DouyinPublishPageRejected):
        DouyinPublishPage(window(page)).works_titled("作品")

    assert len(page.disposed) == MAX_DOUYIN_PUBLISH_WORKS_READ + 1, "every row is released"


def test_a_row_that_cannot_be_read_is_refused_and_the_rest_released() -> None:
    page = _works_list_page(titles=(("作品", True), ("另一个作品", True)))
    page.failing_handle_index = 1

    with pytest.raises(RuntimeError):
        DouyinPublishPage(window(page)).works_titled("作品")

    assert page.disposed == [0, 1]


def test_an_account_name_the_page_will_not_give_up_reads_as_unknown() -> None:
    """It is untrusted text used only to show the operator what they are about to
    publish to; not having it is not a failure, it is not knowing."""

    class _RefusingLocator(FakePage):
        def locator(self, selector: str) -> Any:
            raise RuntimeError("private locator failure")

    assert DouyinPublishPage(window(_RefusingLocator(url=FORM_URL))).target_account() is None

    unreadable = FakePage(url=FORM_URL, visible_selectors={ACCOUNT_NAME})

    class _RefusingText(FakeLocator):
        def inner_text(self) -> str:
            raise RuntimeError("private text failure")

    unreadable.locator = lambda selector: _RefusingText(selector, unreadable)  # type: ignore[method-assign]
    assert DouyinPublishPage(window(unreadable)).target_account() is None


def test_a_submit_control_that_answers_with_a_non_bool_is_refused() -> None:
    """The last check before an irreversible press has to be a real yes or no."""
    page = form_page()

    class _OddLocator(FakeLocator):
        def is_enabled(self) -> bool:
            # Deliberately not a bool: the page has to notice rather than trust it.
            return cast(bool, "yes")

    page.locator = lambda selector: _OddLocator(selector, page)  # type: ignore[method-assign]

    with pytest.raises(DouyinPublishPageRejected):
        DouyinPublishPage(window(page)).submit_enabled()


def test_a_submit_control_that_cannot_be_asked_is_refused() -> None:
    """Playwright raising mid-question is not read as "the button is fine"."""
    from automation_tool.executor.rpa.douyin.publish_page import DOUYIN_PUBLISH_SUBMIT_SELECTORS

    page = form_page()
    # The locator resolves the whole anchor group, so that is what fails.
    page.failed_selectors.add(", ".join(DOUYIN_PUBLISH_SUBMIT_SELECTORS))

    with pytest.raises(DouyinPublishPageRejected):
        DouyinPublishPage(window(page)).submit_enabled()


def test_a_field_asked_for_in_the_wrong_state_is_refused() -> None:
    """Each field belongs to one page state; asking outside it would type into nothing."""
    upload = DouyinPublishPage(window(upload_page()))

    for label, ask in [
        ("a title before the form", upload.title_input),
        ("a description before the form", upload.description_input),
    ]:
        with pytest.raises(DouyinPublishPageRejected):
            ask()
        assert label

    form = DouyinPublishPage(window(form_page()))
    with pytest.raises(DouyinPublishPageRejected):
        form.artifact_input()


def test_navigating_to_the_works_list_that_fails_reads_as_an_unknown_route() -> None:
    """A navigation that did not happen leaves the page wherever it was."""

    class _RefusingNavigation(FakePage):
        def goto(self, url: str, *, wait_until: str, timeout: float) -> None:
            raise RuntimeError("private navigation failure")

    page = _RefusingNavigation(url=FORM_URL, visible_selectors=set(FORM_SELECTORS))
    observation = DouyinPublishPage(window(page)).open_works_list(timeout_milliseconds=100)

    assert observation.route is DouyinPublishRoute.UNKNOWN
