from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from automation_tool.executor.browser_runtime import BrowserLaunchRequest, BrowserRuntime
from automation_tool.executor.rpa.douyin.login import (
    DOUYIN_QR_LOGIN_FLOW_VERSION,
    DouyinQrLoginEvidence,
    DouyinQrLoginFlow,
    DouyinQrLoginObservation,
    DouyinQrLoginRejected,
    DouyinQrLoginState,
)


class FakeLocator:
    def __init__(
        self,
        visible: bool,
        *,
        fail: bool = False,
        wait_callback: Callable[[], None] | None = None,
        wait_fail: bool = False,
    ) -> None:
        self._visible = visible
        self._fail = fail
        self._wait_callback = wait_callback
        self._wait_fail = wait_fail

    @property
    def first(self) -> FakeLocator:
        return self

    def is_visible(self) -> bool:
        if self._fail:
            raise RuntimeError("private page failure")
        return self._visible

    def wait_for(self, *, state: str, timeout: float) -> None:
        assert state == "visible"
        assert timeout == 10_000
        if self._wait_fail:
            raise RuntimeError("private wait failure")
        if self._wait_callback is not None:
            self._wait_callback()
            return
        if not self._visible:
            raise PlaywrightTimeoutError("bounded QR wait")


class FakePage:
    def __init__(self, visible_selectors: set[str] | None = None) -> None:
        self.url = "about:blank"
        self.visible_selectors = set() if visible_selectors is None else visible_selectors
        self.fail = False
        self.failed_selectors: set[str] = set()
        self.wait_failure_selectors: set[str] = set()
        self.selectors_after_wait: set[str] | None = None
        self.fail_navigation = False
        self.closed = False
        self.navigations: list[str] = []

    def goto(self, url: str, **_options: object) -> None:
        if self.fail_navigation:
            raise RuntimeError("private navigation failure")
        self.url = url
        self.navigations.append(url)

    def locator(self, selector: str) -> FakeLocator:
        wait_callback: Callable[[], None] | None = None
        selectors_after_wait = self.selectors_after_wait
        if selectors_after_wait is not None:

            def replace_selectors() -> None:
                self.visible_selectors = selectors_after_wait

            wait_callback = replace_selectors
        return FakeLocator(
            selector in self.visible_selectors,
            fail=self.fail or selector in self.failed_selectors,
            wait_callback=wait_callback,
            wait_fail=selector in self.wait_failure_selectors,
        )

    def close(self, **_options: object) -> None:
        self.closed = True


class FakeContext:
    def __init__(self, login_page: FakePage) -> None:
        self.login_page = login_page
        self.pages: list[FakePage] = []

    def set_default_timeout(self, _timeout: float) -> None: ...

    def set_default_navigation_timeout(self, _timeout: float) -> None: ...

    def new_page(self) -> FakePage:
        self.pages.append(self.login_page)
        return self.login_page

    def close(self, **_options: object) -> None:
        self.pages.clear()


class FakeChromium:
    def __init__(self, context: FakeContext) -> None:
        self.context = context

    def launch_persistent_context(self, *_args: object, **_kwargs: object) -> FakeContext:
        return self.context


class FakePlaywright:
    def __init__(self, context: FakeContext) -> None:
        self.chromium = FakeChromium(context)

    def stop(self) -> None: ...


def running_runtime(tmp_path: Path, page: FakePage) -> BrowserRuntime:
    profile = tmp_path / "profile"
    profile.mkdir(mode=0o700, parents=True)
    context = FakeContext(page)
    runtime = BrowserRuntime(starter=lambda: cast(Any, FakePlaywright(context)))
    runtime.start(
        BrowserLaunchRequest(
            executable_path=Path(sys.executable).resolve(strict=True),
            profile_directory=profile,
        )
    )
    return runtime


@pytest.mark.parametrize(
    ("selectors", "state", "evidence"),
    [
        (
            {
                '[data-e2e="login-button"]',
                'text="扫码登录"',
                'text="如何扫码"',
                'img[aria-label="二维码"], img[alt="二维码"]',
            },
            DouyinQrLoginState.AWAITING_SCAN,
            DouyinQrLoginEvidence.QR_VISIBLE,
        ),
        (
            {'[data-e2e="login-button"]', "text=/^扫码成功/"},
            DouyinQrLoginState.AWAITING_CONFIRMATION,
            DouyinQrLoginEvidence.QR_SCANNED,
        ),
        (
            {'[data-e2e="login-button"]', "text=/^二维码已(?:失效|过期)/"},
            DouyinQrLoginState.QR_EXPIRED,
            DouyinQrLoginEvidence.QR_EXPIRED,
        ),
        (
            {'[data-e2e="login-button"]'},
            DouyinQrLoginState.LOGIN_REQUIRED,
            DouyinQrLoginEvidence.SESSION_MISSING,
        ),
        (
            {'[data-e2e="login-expired"]'},
            DouyinQrLoginState.LOGIN_REQUIRED,
            DouyinQrLoginEvidence.SESSION_EXPIRED,
        ),
        (
            {'[data-e2e="user-info"]'},
            DouyinQrLoginState.HEALTHY,
            DouyinQrLoginEvidence.SESSION_HEALTHY,
        ),
        (
            {'iframe[src^="https://rmc.bytedance.com/verifycenter/captcha/"]'},
            DouyinQrLoginState.HANDOFF_REQUIRED,
            DouyinQrLoginEvidence.RISK_CHALLENGE,
        ),
        (
            set(),
            DouyinQrLoginState.UNKNOWN,
            DouyinQrLoginEvidence.INSUFFICIENT,
        ),
    ],
)
def test_begin_opens_one_dedicated_window_and_derives_only_page_facts(
    tmp_path: Path,
    selectors: set[str],
    state: DouyinQrLoginState,
    evidence: DouyinQrLoginEvidence,
) -> None:
    page = FakePage(selectors)
    runtime = running_runtime(tmp_path, page)
    flow = DouyinQrLoginFlow(runtime)
    try:
        observation = flow.begin()

        assert len(runtime.windows()) == 1
        assert page.navigations == ["https://www.douyin.com/user/self"]
        assert observation.state is state
        assert observation.evidence is evidence
        assert observation.circuit_open is (state is not DouyinQrLoginState.HEALTHY)
        assert observation.flow_version == DOUYIN_QR_LOGIN_FLOW_VERSION
        assert "douyin.com" not in repr(observation)
    finally:
        runtime.close()


def test_recheck_observes_scan_confirmation_and_real_session_health(
    tmp_path: Path,
) -> None:
    page = FakePage(
        {
            '[data-e2e="login-button"]',
            'text="扫码登录"',
            'text="如何扫码"',
            'img[aria-label="二维码"], img[alt="二维码"]',
        }
    )
    runtime = running_runtime(tmp_path, page)
    flow = DouyinQrLoginFlow(runtime)
    try:
        assert flow.begin().state is DouyinQrLoginState.AWAITING_SCAN

        page.visible_selectors = {'[data-e2e="login-button"]', "text=/^扫码成功/"}
        assert flow.recheck().state is DouyinQrLoginState.AWAITING_CONFIRMATION
        page.visible_selectors = {'[data-e2e="user-info"]'}
        healthy = flow.recheck()

        assert healthy.state is DouyinQrLoginState.HEALTHY
        assert not healthy.circuit_open
        assert page.navigations == ["https://www.douyin.com/user/self"]
    finally:
        runtime.close()


def test_platform_challenges_remain_in_handoff_until_page_health_is_rechecked(
    tmp_path: Path,
) -> None:
    page = FakePage({'iframe[src^="https://rmc.bytedance.com/verifycenter/captcha/"]'})
    runtime = running_runtime(tmp_path, page)
    flow = DouyinQrLoginFlow(runtime)
    try:
        initial = flow.begin()
        page.visible_selectors = {'iframe[src*="/verifycenter/captcha/"]'}
        still_blocked = flow.recheck()
        page.visible_selectors = {'[data-e2e="user-info"]'}
        resolved = flow.recheck()

        assert initial.state is DouyinQrLoginState.HANDOFF_REQUIRED
        assert still_blocked.state is DouyinQrLoginState.HANDOFF_REQUIRED
        assert initial.evidence is DouyinQrLoginEvidence.RISK_CHALLENGE
        assert still_blocked.evidence is DouyinQrLoginEvidence.RISK_CHALLENGE
        assert initial.circuit_open and still_blocked.circuit_open
        assert resolved.state is DouyinQrLoginState.HEALTHY
        assert not resolved.circuit_open
        assert page.navigations == ["https://www.douyin.com/user/self"]
    finally:
        runtime.close()


def test_begin_bounded_wait_rechecks_async_health_and_fails_closed_on_wait_error(
    tmp_path: Path,
) -> None:
    page = FakePage({'[data-e2e="login-button"]'})
    page.selectors_after_wait = {'[data-e2e="user-info"]'}
    runtime = running_runtime(tmp_path / "ready", page)
    try:
        observation = DouyinQrLoginFlow(runtime).begin()

        assert observation.state is DouyinQrLoginState.HEALTHY
        assert observation.evidence is DouyinQrLoginEvidence.SESSION_HEALTHY
    finally:
        runtime.close()

    failed_page = FakePage({'[data-e2e="login-button"]'})
    failed_page.wait_failure_selectors = {
        '[data-e2e="user-avatar"], [data-e2e="user-info"], '
        'img[aria-label="二维码"], img[alt="二维码"]'
    }
    failed_runtime = running_runtime(tmp_path / "failed", failed_page)
    try:
        unavailable = DouyinQrLoginFlow(failed_runtime).begin()

        assert unavailable.state is DouyinQrLoginState.UNKNOWN
        assert unavailable.evidence is DouyinQrLoginEvidence.PAGE_UNAVAILABLE
    finally:
        failed_runtime.close()


def test_recheck_falls_back_to_the_protected_page_only_for_insufficient_home_facts(
    tmp_path: Path,
) -> None:
    page = FakePage({'[data-e2e="user-info"]'})
    runtime = running_runtime(tmp_path, page)
    flow = DouyinQrLoginFlow(runtime)
    try:
        assert flow.begin().state is DouyinQrLoginState.HEALTHY
        page.url = "https://www.douyin.com/"
        page.visible_selectors.clear()

        observation = flow.recheck()

        assert observation.state is DouyinQrLoginState.UNKNOWN
        assert page.navigations == [
            "https://www.douyin.com/user/self",
            "https://www.douyin.com/user/self",
        ]
    finally:
        runtime.close()


def test_conflicting_qr_facts_and_page_failures_are_unknown(tmp_path: Path) -> None:
    page = FakePage(
        {
            '[data-e2e="login-button"]',
            "text=/^扫码成功/",
            "text=/^二维码已(?:失效|过期)/",
        }
    )
    runtime = running_runtime(tmp_path, page)
    flow = DouyinQrLoginFlow(runtime)
    try:
        conflict = flow.begin()
        page.fail = True
        unavailable = flow.recheck()

        assert conflict.state is DouyinQrLoginState.UNKNOWN
        assert conflict.evidence is DouyinQrLoginEvidence.CONFLICTING
        assert unavailable.state is DouyinQrLoginState.UNKNOWN
        assert unavailable.evidence is DouyinQrLoginEvidence.PAGE_UNAVAILABLE
    finally:
        runtime.close()


def test_navigation_failure_is_fixed_unknown_and_remains_recheckable(tmp_path: Path) -> None:
    page = FakePage()
    page.fail_navigation = True
    runtime = running_runtime(tmp_path, page)
    flow = DouyinQrLoginFlow(runtime)
    try:
        unavailable = flow.begin()
        page.fail_navigation = False
        page.visible_selectors = {'[data-e2e="user-info"]'}
        recovered = flow.recheck()

        assert unavailable.state is DouyinQrLoginState.UNKNOWN
        assert unavailable.evidence is DouyinQrLoginEvidence.PAGE_UNAVAILABLE
        assert recovered.state is DouyinQrLoginState.HEALTHY
        assert recovered.evidence is DouyinQrLoginEvidence.SESSION_HEALTHY
    finally:
        runtime.close()


def test_recheck_navigation_failure_and_qr_locator_failure_are_fixed_unknown(
    tmp_path: Path,
) -> None:
    page = FakePage()
    runtime = running_runtime(tmp_path, page)
    flow = DouyinQrLoginFlow(runtime)
    try:
        assert flow.begin().evidence is DouyinQrLoginEvidence.INSUFFICIENT
        page.fail_navigation = True
        failed_navigation = flow.recheck()

        assert failed_navigation.state is DouyinQrLoginState.UNKNOWN
        assert failed_navigation.evidence is DouyinQrLoginEvidence.PAGE_UNAVAILABLE

        page.fail_navigation = False
        page.visible_selectors = {'[data-e2e="login-button"]'}
        page.failed_selectors = {"text=/^二维码已(?:失效|过期)/"}
        unavailable_qr = flow.recheck()

        assert unavailable_qr.state is DouyinQrLoginState.UNKNOWN
        assert unavailable_qr.evidence is DouyinQrLoginEvidence.PAGE_UNAVAILABLE
    finally:
        runtime.close()


def test_lifecycle_rejects_injected_runtime_or_caller_state_and_closes_its_window(
    tmp_path: Path,
) -> None:
    with pytest.raises(DouyinQrLoginRejected):
        DouyinQrLoginFlow(cast(Any, object()))

    page = FakePage({'[data-e2e="login-button"]'})
    runtime = running_runtime(tmp_path, page)
    flow = DouyinQrLoginFlow(runtime)
    try:
        with pytest.raises(DouyinQrLoginRejected):
            flow.recheck()
        flow.begin()
        with pytest.raises(DouyinQrLoginRejected):
            flow.begin()

        flow.close()
        flow.close()

        assert page.closed
        with pytest.raises(DouyinQrLoginRejected):
            flow.recheck()
        assert repr(flow) == "DouyinQrLoginFlow(<redacted>)"
    finally:
        runtime.close()


def test_window_open_and_close_failures_are_fixed_and_close_before_begin_is_terminal(
    tmp_path: Path,
) -> None:
    stopped_runtime = BrowserRuntime()
    with pytest.raises(
        DouyinQrLoginRejected,
        match=r"^douyin QR login is unavailable$",
    ):
        DouyinQrLoginFlow(stopped_runtime).begin()

    first_page = FakePage()
    first_runtime = running_runtime(tmp_path / "first", first_page)
    closed_before_begin = DouyinQrLoginFlow(first_runtime)
    closed_before_begin.close()
    closed_before_begin.close()
    with pytest.raises(DouyinQrLoginRejected):
        closed_before_begin.begin()
    first_runtime.close()

    second_page = FakePage({'[data-e2e="user-info"]'})
    second_runtime = running_runtime(tmp_path / "second", second_page)
    flow = DouyinQrLoginFlow(second_runtime)
    flow.begin()
    second_runtime.close()

    with pytest.raises(DouyinQrLoginRejected):
        flow.close()


@pytest.mark.parametrize(
    "changes",
    [
        {"state": cast(Any, "healthy")},
        {"evidence": cast(Any, "session_healthy")},
        {"flow_version": "douyin.qr-login.v3"},
    ],
)
def test_observation_rejects_changed_or_untyped_contracts(changes: dict[str, object]) -> None:
    values: dict[str, object] = {
        "state": DouyinQrLoginState.HEALTHY,
        "evidence": DouyinQrLoginEvidence.SESSION_HEALTHY,
        "flow_version": DOUYIN_QR_LOGIN_FLOW_VERSION,
    }
    values.update(changes)

    with pytest.raises(DouyinQrLoginRejected):
        DouyinQrLoginObservation(**cast(Any, values))
