"""Fail-closed Douyin session health derived only from visible page facts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, cast
from urllib.parse import urlsplit

from automation_tool.executor.browser_runtime import BrowserWindow
from automation_tool.protocol.safe_text import contains_control_or_bidi

DOUYIN_SESSION_SELECTOR_VERSION = "douyin.session.v1"
DOUYIN_SESSION_PROBE_URL = "https://www.douyin.com/user/self"
_MAX_PAGE_URL_CHARACTERS = 2048

_RISK_SELECTORS = (
    'iframe[src^="https://rmc.bytedance.com/verifycenter/captcha/"]',
    'iframe[src*="/verifycenter/captcha/"]',
    '[data-e2e="captcha-container"]',
)
_EXPIRED_SELECTORS = (
    '[data-e2e="login-expired"]',
    '[data-e2e="session-expired"]',
    'text="登录已过期"',
    'text="登录状态已失效"',
)
_HEALTHY_SELECTORS = (
    '[data-e2e="user-avatar"]',
    '[data-e2e="user-info"]',
)
_MISSING_SELECTORS = (
    '[data-e2e="login-button"]',
    '[data-e2e="login-guide"]',
    'button:text-is("登录")',
    '[role="button"]:text-is("登录")',
    'text="登录"',
)


class DouyinSessionDetectionRejected(RuntimeError):
    """The caller did not provide a BrowserRuntime-owned page window."""

    def __init__(self) -> None:
        super().__init__("douyin session detection is unavailable")


class DouyinSessionState(StrEnum):
    HEALTHY = "healthy"
    EXPIRED = "expired"
    MISSING = "missing"
    RISK = "risk"
    UNKNOWN = "unknown"


class DouyinSessionEvidence(StrEnum):
    AUTHENTICATED_SHELL = "authenticated_shell"
    LOGIN_EXPIRED = "login_expired"
    LOGIN_ENTRY = "login_entry"
    RISK_CHALLENGE = "risk_challenge"
    CONFLICTING = "conflicting"
    INSUFFICIENT = "insufficient"
    ORIGIN_INVALID = "origin_invalid"
    PAGE_UNAVAILABLE = "page_unavailable"


@dataclass(frozen=True, slots=True, repr=False)
class DouyinSessionObservation:
    state: DouyinSessionState
    evidence: DouyinSessionEvidence
    selector_version: str = DOUYIN_SESSION_SELECTOR_VERSION

    def __post_init__(self) -> None:
        if (
            not isinstance(self.state, DouyinSessionState)
            or not isinstance(self.evidence, DouyinSessionEvidence)
            or self.selector_version != DOUYIN_SESSION_SELECTOR_VERSION
        ):
            raise DouyinSessionDetectionRejected

    @property
    def circuit_open(self) -> bool:
        return self.state is not DouyinSessionState.HEALTHY

    def __repr__(self) -> str:
        return (
            "DouyinSessionObservation("
            f"state={self.state.value!r}, evidence={self.evidence.value!r}, "
            f"selector_version={self.selector_version!r}, circuit_open={self.circuit_open!r})"
        )


class _Locator(Protocol):
    @property
    def first(self) -> _Locator: ...

    def is_visible(self) -> bool: ...


class _SessionPage(Protocol):
    @property
    def url(self) -> str: ...

    def locator(self, selector: str) -> _Locator: ...


class DouyinSessionDetector:
    """Classify the current official Douyin page without reading any cookie API."""

    def check(self, window: BrowserWindow) -> DouyinSessionObservation:
        if not isinstance(window, BrowserWindow):
            raise DouyinSessionDetectionRejected
        page = cast(_SessionPage, window.playwright_page)
        try:
            if not _is_official_douyin_url(page.url):
                return _observation(
                    DouyinSessionState.UNKNOWN,
                    DouyinSessionEvidence.ORIGIN_INVALID,
                )
            visible = tuple(
                evidence
                for selectors, evidence in (
                    (_RISK_SELECTORS, DouyinSessionEvidence.RISK_CHALLENGE),
                    (_EXPIRED_SELECTORS, DouyinSessionEvidence.LOGIN_EXPIRED),
                    (_HEALTHY_SELECTORS, DouyinSessionEvidence.AUTHENTICATED_SHELL),
                    (_MISSING_SELECTORS, DouyinSessionEvidence.LOGIN_ENTRY),
                )
                if _any_visible(page, selectors)
            )
        except Exception:
            return _observation(
                DouyinSessionState.UNKNOWN,
                DouyinSessionEvidence.PAGE_UNAVAILABLE,
            )
        if len(visible) > 1:
            return _observation(
                DouyinSessionState.UNKNOWN,
                DouyinSessionEvidence.CONFLICTING,
            )
        if not visible:
            return _observation(
                DouyinSessionState.UNKNOWN,
                DouyinSessionEvidence.INSUFFICIENT,
            )
        evidence = visible[0]
        return _observation(_state_for(evidence), evidence)


def _is_official_douyin_url(source: object) -> bool:
    if (
        type(source) is not str
        or not source
        or len(source) > _MAX_PAGE_URL_CHARACTERS
        or contains_control_or_bidi(source)
    ):
        return False
    try:
        parsed = urlsplit(source)
        return (
            parsed.scheme == "https"
            and parsed.hostname == "www.douyin.com"
            and parsed.port in {None, 443}
            and parsed.username is None
            and parsed.password is None
        )
    except ValueError:
        return False


def _any_visible(page: _SessionPage, selectors: tuple[str, ...]) -> bool:
    return any(page.locator(selector).first.is_visible() for selector in selectors)


def _state_for(evidence: DouyinSessionEvidence) -> DouyinSessionState:
    mapping = {
        DouyinSessionEvidence.RISK_CHALLENGE: DouyinSessionState.RISK,
        DouyinSessionEvidence.LOGIN_EXPIRED: DouyinSessionState.EXPIRED,
        DouyinSessionEvidence.AUTHENTICATED_SHELL: DouyinSessionState.HEALTHY,
        DouyinSessionEvidence.LOGIN_ENTRY: DouyinSessionState.MISSING,
    }
    try:
        return mapping[evidence]
    except (KeyError, TypeError):
        raise DouyinSessionDetectionRejected from None


def _observation(
    state: DouyinSessionState,
    evidence: DouyinSessionEvidence,
) -> DouyinSessionObservation:
    return DouyinSessionObservation(state=state, evidence=evidence)


__all__ = [
    "DOUYIN_SESSION_PROBE_URL",
    "DOUYIN_SESSION_SELECTOR_VERSION",
    "DouyinSessionDetectionRejected",
    "DouyinSessionDetector",
    "DouyinSessionEvidence",
    "DouyinSessionObservation",
    "DouyinSessionState",
]
