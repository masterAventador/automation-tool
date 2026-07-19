"""Visible Douyin QR login flow driven only by BrowserRuntime page facts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, cast

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from automation_tool.executor.browser_runtime import (
    BrowserRuntime,
    BrowserWindow,
)
from automation_tool.executor.rpa.douyin.session import (
    DOUYIN_SESSION_HEALTHY_SELECTORS,
    DOUYIN_SESSION_PROBE_URL,
    DouyinSessionDetector,
    DouyinSessionEvidence,
    DouyinSessionObservation,
    DouyinSessionState,
)

DOUYIN_QR_LOGIN_FLOW_VERSION = "douyin.qr-login.v2"
_NAVIGATION_TIMEOUT_MILLISECONDS = 30_000
_QR_READY_TIMEOUT_MILLISECONDS = 10_000
_QR_READY_SELECTOR = 'img[aria-label="二维码"], img[alt="二维码"]'
_READY_FACTS_SELECTOR = ", ".join((*DOUYIN_SESSION_HEALTHY_SELECTORS, _QR_READY_SELECTOR))

_QR_VISIBLE_SELECTORS = (
    'text="扫码登录"',
    'text="如何扫码"',
    _QR_READY_SELECTOR,
)
_QR_CONFIRMATION_SELECTORS = (
    "text=/^扫码成功/",
    "text=/^已扫描/",
    'text="请在手机上确认登录"',
    'text="请在抖音中确认登录"',
)
_QR_EXPIRED_SELECTORS = (
    "text=/^二维码已(?:失效|过期)/",
    'text="二维码已过期\uff0c请刷新"',
    'text="点击刷新"',
)


class DouyinQrLoginRejected(RuntimeError):
    """The local QR workflow cannot continue inside its fixed trust boundary."""

    def __init__(self) -> None:
        super().__init__("douyin QR login is unavailable")


class DouyinQrLoginState(StrEnum):
    LOGIN_REQUIRED = "login_required"
    AWAITING_SCAN = "awaiting_scan"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    QR_EXPIRED = "qr_expired"
    HEALTHY = "healthy"
    HANDOFF_REQUIRED = "handoff_required"
    UNKNOWN = "unknown"


class DouyinQrLoginEvidence(StrEnum):
    SESSION_MISSING = "session_missing"
    SESSION_EXPIRED = "session_expired"
    QR_VISIBLE = "qr_visible"
    QR_SCANNED = "qr_scanned"
    QR_EXPIRED = "qr_expired"
    SESSION_HEALTHY = "session_healthy"
    RISK_CHALLENGE = "risk_challenge"
    CONFLICTING = "conflicting"
    INSUFFICIENT = "insufficient"
    PAGE_UNAVAILABLE = "page_unavailable"


@dataclass(frozen=True, slots=True, repr=False)
class DouyinQrLoginObservation:
    state: DouyinQrLoginState
    evidence: DouyinQrLoginEvidence
    flow_version: str = DOUYIN_QR_LOGIN_FLOW_VERSION

    def __post_init__(self) -> None:
        if (
            not isinstance(self.state, DouyinQrLoginState)
            or not isinstance(self.evidence, DouyinQrLoginEvidence)
            or self.flow_version != DOUYIN_QR_LOGIN_FLOW_VERSION
        ):
            raise DouyinQrLoginRejected

    @property
    def circuit_open(self) -> bool:
        return self.state is not DouyinQrLoginState.HEALTHY

    def __repr__(self) -> str:
        return (
            "DouyinQrLoginObservation("
            f"state={self.state.value!r}, evidence={self.evidence.value!r}, "
            f"flow_version={self.flow_version!r}, circuit_open={self.circuit_open!r})"
        )


class _Locator(Protocol):
    @property
    def first(self) -> _Locator: ...

    def is_visible(self) -> bool: ...

    def wait_for(self, *, state: str, timeout: float) -> None: ...


class _LoginPage(Protocol):
    def goto(
        self,
        url: str,
        *,
        wait_until: str,
        timeout: float,
    ) -> object: ...

    def locator(self, selector: str) -> _Locator: ...


class DouyinQrLoginFlow:
    """Own one dedicated visible browser window and explicitly recheck its page."""

    def __init__(self, runtime: BrowserRuntime) -> None:
        if not isinstance(runtime, BrowserRuntime):
            raise DouyinQrLoginRejected
        self._runtime = runtime
        self._window: BrowserWindow | None = None
        self._closed = False
        self._detector = DouyinSessionDetector()

    def __repr__(self) -> str:
        return "DouyinQrLoginFlow(<redacted>)"

    def begin(self) -> DouyinQrLoginObservation:
        if self._closed or self._window is not None:
            raise DouyinQrLoginRejected
        try:
            self._window = self._runtime.open_window()
        except Exception:
            raise DouyinQrLoginRejected from None
        if not self._navigate_to_probe(self._window):
            return _observation(
                DouyinQrLoginState.UNKNOWN,
                DouyinQrLoginEvidence.PAGE_UNAVAILABLE,
            )
        observation = self._observe(self._window)
        if (
            observation.state
            not in {
                DouyinQrLoginState.LOGIN_REQUIRED,
                DouyinQrLoginState.UNKNOWN,
            }
            or observation.evidence is DouyinQrLoginEvidence.CONFLICTING
        ):
            return observation
        if not self._wait_for_ready_fact(self._window):
            return _observation(
                DouyinQrLoginState.UNKNOWN,
                DouyinQrLoginEvidence.PAGE_UNAVAILABLE,
            )
        return self._observe(self._window)

    def recheck(self) -> DouyinQrLoginObservation:
        window = self._require_active_window()
        observation = self._observe(window)
        if (
            observation.state is not DouyinQrLoginState.UNKNOWN
            or observation.evidence is DouyinQrLoginEvidence.CONFLICTING
        ):
            return observation
        if not self._navigate_to_probe(window):
            return _observation(
                DouyinQrLoginState.UNKNOWN,
                DouyinQrLoginEvidence.PAGE_UNAVAILABLE,
            )
        return self._observe(window)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        window = self._window
        self._window = None
        if window is None:
            return
        try:
            self._runtime.close_window(window)
        except Exception:
            raise DouyinQrLoginRejected from None

    def _require_active_window(self) -> BrowserWindow:
        if self._closed or self._window is None:
            raise DouyinQrLoginRejected
        return self._window

    @staticmethod
    def _navigate_to_probe(window: BrowserWindow) -> bool:
        page = cast(_LoginPage, window.playwright_page)
        try:
            page.goto(
                DOUYIN_SESSION_PROBE_URL,
                wait_until="domcontentloaded",
                timeout=_NAVIGATION_TIMEOUT_MILLISECONDS,
            )
        except Exception:
            return False
        return True

    @staticmethod
    def _wait_for_ready_fact(window: BrowserWindow) -> bool:
        page = cast(_LoginPage, window.playwright_page)
        try:
            page.locator(_READY_FACTS_SELECTOR).first.wait_for(
                state="visible",
                timeout=_QR_READY_TIMEOUT_MILLISECONDS,
            )
        except PlaywrightTimeoutError:
            pass
        except Exception:
            return False
        return True

    def _observe(self, window: BrowserWindow) -> DouyinQrLoginObservation:
        session = self._detector.check(window)
        if session.state is DouyinSessionState.HEALTHY:
            return _observation(
                DouyinQrLoginState.HEALTHY,
                DouyinQrLoginEvidence.SESSION_HEALTHY,
            )
        if session.state is DouyinSessionState.RISK:
            return _observation(
                DouyinQrLoginState.HANDOFF_REQUIRED,
                DouyinQrLoginEvidence.RISK_CHALLENGE,
            )
        if session.state in {DouyinSessionState.MISSING, DouyinSessionState.EXPIRED}:
            return self._observe_login_page(window, session)
        return _unknown_session_observation(session)

    @staticmethod
    def _observe_login_page(
        window: BrowserWindow,
        session: DouyinSessionObservation,
    ) -> DouyinQrLoginObservation:
        page = cast(_LoginPage, window.playwright_page)
        try:
            qr_expired = _any_visible(page, _QR_EXPIRED_SELECTORS)
            qr_confirmation = _any_visible(page, _QR_CONFIRMATION_SELECTORS)
            qr_visible = _all_visible(page, _QR_VISIBLE_SELECTORS)
        except Exception:
            return _observation(
                DouyinQrLoginState.UNKNOWN,
                DouyinQrLoginEvidence.PAGE_UNAVAILABLE,
            )
        if qr_expired and qr_confirmation:
            return _observation(
                DouyinQrLoginState.UNKNOWN,
                DouyinQrLoginEvidence.CONFLICTING,
            )
        if qr_expired:
            return _observation(
                DouyinQrLoginState.QR_EXPIRED,
                DouyinQrLoginEvidence.QR_EXPIRED,
            )
        if qr_confirmation:
            return _observation(
                DouyinQrLoginState.AWAITING_CONFIRMATION,
                DouyinQrLoginEvidence.QR_SCANNED,
            )
        if qr_visible:
            return _observation(
                DouyinQrLoginState.AWAITING_SCAN,
                DouyinQrLoginEvidence.QR_VISIBLE,
            )
        if session.state is DouyinSessionState.EXPIRED:
            return _observation(
                DouyinQrLoginState.LOGIN_REQUIRED,
                DouyinQrLoginEvidence.SESSION_EXPIRED,
            )
        return _observation(
            DouyinQrLoginState.LOGIN_REQUIRED,
            DouyinQrLoginEvidence.SESSION_MISSING,
        )


def _unknown_session_observation(
    session: DouyinSessionObservation,
) -> DouyinQrLoginObservation:
    evidence = {
        DouyinSessionEvidence.CONFLICTING: DouyinQrLoginEvidence.CONFLICTING,
        DouyinSessionEvidence.PAGE_UNAVAILABLE: DouyinQrLoginEvidence.PAGE_UNAVAILABLE,
    }.get(session.evidence, DouyinQrLoginEvidence.INSUFFICIENT)
    return _observation(DouyinQrLoginState.UNKNOWN, evidence)


def _any_visible(page: _LoginPage, selectors: tuple[str, ...]) -> bool:
    return any(page.locator(selector).first.is_visible() for selector in selectors)


def _all_visible(page: _LoginPage, selectors: tuple[str, ...]) -> bool:
    return all(page.locator(selector).first.is_visible() for selector in selectors)


def _observation(
    state: DouyinQrLoginState,
    evidence: DouyinQrLoginEvidence,
) -> DouyinQrLoginObservation:
    return DouyinQrLoginObservation(state=state, evidence=evidence)


__all__ = [
    "DOUYIN_QR_LOGIN_FLOW_VERSION",
    "DouyinQrLoginEvidence",
    "DouyinQrLoginFlow",
    "DouyinQrLoginObservation",
    "DouyinQrLoginRejected",
    "DouyinQrLoginState",
]
