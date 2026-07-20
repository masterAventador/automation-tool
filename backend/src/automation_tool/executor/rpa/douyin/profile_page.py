"""Versioned, non-executing Douyin user-profile Page Object."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from time import monotonic
from typing import Protocol, cast

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from automation_tool.executor.browser_runtime import BrowserWindow
from automation_tool.executor.rpa.douyin.page_version import (
    DouyinPageEntry,
    DouyinPageObservation,
    DouyinPageVersion,
    DouyinPageVersionModel,
)
from automation_tool.executor.rpa.douyin.session import DOUYIN_RISK_CHALLENGE_SELECTORS

DOUYIN_PROFILE_PAGE_SELECTOR_VERSION = "douyin.profile-page.v1"

_PROFILE_ROOT_SELECTORS = (
    'main[aria-label="用户主页"]',
    '[data-e2e="user-detail"]',
    '[data-e2e="user-profile"]',
)
_LOGIN_DIALOG_SELECTORS = (
    '[role="dialog"]:has-text("扫码登录")',
    '[data-e2e="login-modal"]',
    '[data-e2e="login-panel"]',
)
_BLOCKING_DIALOG_SELECTORS = (
    *DOUYIN_RISK_CHALLENGE_SELECTORS,
    '[role="dialog"]',
    '[data-e2e="modal"]',
)
_MAX_WAIT_MILLISECONDS = 60_000


class DouyinProfilePageRejected(RuntimeError):
    def __init__(self) -> None:
        super().__init__("douyin profile page is unavailable")


class DouyinProfilePageState(StrEnum):
    READY = "ready"
    LOGIN_REQUIRED = "login_required"
    DIALOG_BLOCKED = "dialog_blocked"
    UNKNOWN = "unknown"


class DouyinProfilePageEvidence(StrEnum):
    PROFILE_ANCHOR_VISIBLE = "profile_anchor_visible"
    LOGIN_REDIRECT = "login_redirect"
    LOGIN_DIALOG = "login_dialog"
    BLOCKING_DIALOG = "blocking_dialog"
    REQUIRED_ANCHOR_MISSING = "required_anchor_missing"
    CONFLICTING_ANCHORS = "conflicting_anchors"
    PAGE_VERSION_UNKNOWN = "page_version_unknown"
    PAGE_UNAVAILABLE = "page_unavailable"


_PROFILE_OBSERVATIONS = (
    (
        DouyinProfilePageState.READY,
        DouyinProfilePageEvidence.PROFILE_ANCHOR_VISIBLE,
    ),
    (
        DouyinProfilePageState.LOGIN_REQUIRED,
        DouyinProfilePageEvidence.LOGIN_DIALOG,
    ),
    (
        DouyinProfilePageState.DIALOG_BLOCKED,
        DouyinProfilePageEvidence.BLOCKING_DIALOG,
    ),
    *(
        (DouyinProfilePageState.UNKNOWN, evidence)
        for evidence in (
            DouyinProfilePageEvidence.REQUIRED_ANCHOR_MISSING,
            DouyinProfilePageEvidence.CONFLICTING_ANCHORS,
            DouyinProfilePageEvidence.PAGE_UNAVAILABLE,
        )
    ),
)
_ALLOWED_OBSERVATIONS = frozenset(
    {
        *(
            (DouyinPageVersion.WEB_V1, DouyinPageEntry.USER_PROFILE, state, evidence)
            for state, evidence in _PROFILE_OBSERVATIONS
        ),
        (
            DouyinPageVersion.WEB_V1,
            DouyinPageEntry.SESSION_PROBE,
            DouyinProfilePageState.LOGIN_REQUIRED,
            DouyinProfilePageEvidence.LOGIN_REDIRECT,
        ),
        *(
            (
                DouyinPageVersion.WEB_V1,
                entry,
                DouyinProfilePageState.UNKNOWN,
                DouyinProfilePageEvidence.PAGE_VERSION_UNKNOWN,
            )
            for entry in (
                DouyinPageEntry.HOME,
                DouyinPageEntry.SEARCH_RESULTS,
                DouyinPageEntry.VIDEO_DETAIL,
            )
        ),
        (
            DouyinPageVersion.UNKNOWN,
            DouyinPageEntry.UNKNOWN,
            DouyinProfilePageState.UNKNOWN,
            DouyinProfilePageEvidence.PAGE_VERSION_UNKNOWN,
        ),
        (
            DouyinPageVersion.UNKNOWN,
            DouyinPageEntry.UNKNOWN,
            DouyinProfilePageState.UNKNOWN,
            DouyinProfilePageEvidence.PAGE_UNAVAILABLE,
        ),
    }
)


@dataclass(frozen=True, slots=True, repr=False)
class DouyinProfilePageObservation:
    page_version: DouyinPageVersion
    entry: DouyinPageEntry
    state: DouyinProfilePageState
    evidence: DouyinProfilePageEvidence
    selector_version: str = DOUYIN_PROFILE_PAGE_SELECTOR_VERSION

    def __post_init__(self) -> None:
        if (
            not isinstance(self.page_version, DouyinPageVersion)
            or not isinstance(self.entry, DouyinPageEntry)
            or not isinstance(self.state, DouyinProfilePageState)
            or not isinstance(self.evidence, DouyinProfilePageEvidence)
            or self.selector_version != DOUYIN_PROFILE_PAGE_SELECTOR_VERSION
            or (self.page_version, self.entry, self.state, self.evidence)
            not in _ALLOWED_OBSERVATIONS
        ):
            raise DouyinProfilePageRejected

    @property
    def ready(self) -> bool:
        return self.state is DouyinProfilePageState.READY

    @property
    def circuit_open(self) -> bool:
        return not self.ready

    def __repr__(self) -> str:
        return (
            "DouyinProfilePageObservation("
            f"page_version={self.page_version.value!r}, entry={self.entry.value!r}, "
            f"state={self.state.value!r}, evidence={self.evidence.value!r}, "
            f"selector_version={self.selector_version!r}, "
            f"circuit_open={self.circuit_open!r})"
        )


class _AnchorConflict(RuntimeError):
    pass


class _Locator(Protocol):
    @property
    def first(self) -> _Locator: ...

    def count(self) -> int: ...

    def is_visible(self) -> bool: ...

    def wait_for(self, *, state: str, timeout: float) -> None: ...


class _Page(Protocol):
    @property
    def url(self) -> str: ...

    def locator(self, selector: str) -> _Locator: ...


class DouyinProfilePage:
    """Own generic profile readiness without any interaction selector."""

    def __init__(self, window: BrowserWindow) -> None:
        if not isinstance(window, BrowserWindow):
            raise DouyinProfilePageRejected
        self._page = cast(_Page, window.playwright_page)
        self._versions = DouyinPageVersionModel()

    def __repr__(self) -> str:
        return "DouyinProfilePage(<redacted>)"

    def observe(self) -> DouyinProfilePageObservation:
        try:
            version = self._versions.check(self._page.url)
        except Exception:
            return self._page_unavailable()
        if not version.compatible:
            return _observation(
                version,
                DouyinProfilePageState.UNKNOWN,
                DouyinProfilePageEvidence.PAGE_VERSION_UNKNOWN,
            )
        if version.entry is DouyinPageEntry.SESSION_PROBE:
            return _observation(
                version,
                DouyinProfilePageState.LOGIN_REQUIRED,
                DouyinProfilePageEvidence.LOGIN_REDIRECT,
            )
        if version.entry is not DouyinPageEntry.USER_PROFILE:
            return _observation(
                version,
                DouyinProfilePageState.UNKNOWN,
                DouyinProfilePageEvidence.PAGE_VERSION_UNKNOWN,
            )
        try:
            return self._observe_profile(version)
        except _AnchorConflict:
            return _observation(
                version,
                DouyinProfilePageState.UNKNOWN,
                DouyinProfilePageEvidence.CONFLICTING_ANCHORS,
            )
        except Exception:
            return _observation(
                version,
                DouyinProfilePageState.UNKNOWN,
                DouyinProfilePageEvidence.PAGE_UNAVAILABLE,
            )

    def _observe_profile(self, version: DouyinPageObservation) -> DouyinProfilePageObservation:
        if _unique_visible_locator(self._page, _LOGIN_DIALOG_SELECTORS) is not None:
            return _observation(
                version,
                DouyinProfilePageState.LOGIN_REQUIRED,
                DouyinProfilePageEvidence.LOGIN_DIALOG,
            )
        if _unique_visible_locator(self._page, _BLOCKING_DIALOG_SELECTORS) is not None:
            return _observation(
                version,
                DouyinProfilePageState.DIALOG_BLOCKED,
                DouyinProfilePageEvidence.BLOCKING_DIALOG,
            )
        if _unique_visible_locator(self._page, _PROFILE_ROOT_SELECTORS) is not None:
            return _observation(
                version,
                DouyinProfilePageState.READY,
                DouyinProfilePageEvidence.PROFILE_ANCHOR_VISIBLE,
            )
        return _observation(
            version,
            DouyinProfilePageState.UNKNOWN,
            DouyinProfilePageEvidence.REQUIRED_ANCHOR_MISSING,
        )

    def profile_root(self) -> _Locator:
        if self.observe().state is not DouyinProfilePageState.READY:
            raise DouyinProfilePageRejected
        try:
            locator = _unique_visible_locator(self._page, _PROFILE_ROOT_SELECTORS)
        except Exception:
            raise DouyinProfilePageRejected from None
        if locator is None:
            raise DouyinProfilePageRejected
        return locator

    def wait_for_ready(self, *, timeout_milliseconds: int) -> DouyinProfilePageObservation:
        if (
            type(timeout_milliseconds) is not int
            or not 1 <= timeout_milliseconds <= _MAX_WAIT_MILLISECONDS
        ):
            raise DouyinProfilePageRejected
        observation = self.observe()
        if observation.state is DouyinProfilePageState.READY or not _can_wait(observation):
            return observation
        deadline = monotonic() + timeout_milliseconds / 1_000
        remaining = (deadline - monotonic()) * 1_000
        if remaining <= 0:
            return self.observe()
        try:
            self._page.locator(", ".join(_PROFILE_ROOT_SELECTORS)).first.wait_for(
                state="visible",
                timeout=remaining,
            )
        except PlaywrightTimeoutError:
            return self.observe()
        except Exception:
            return self._page_unavailable()
        return self.observe()

    def _page_unavailable(self) -> DouyinProfilePageObservation:
        try:
            version = self._versions.check(self._page.url)
        except Exception:
            version = self._versions.check("")
        return _observation(
            version,
            DouyinProfilePageState.UNKNOWN,
            DouyinProfilePageEvidence.PAGE_UNAVAILABLE,
        )


def _unique_visible_locator(page: _Page, selectors: tuple[str, ...]) -> _Locator | None:
    locator = page.locator(", ".join(selectors))
    count = locator.count()
    if type(count) is not int or count < 0:
        raise ValueError
    if count > 1:
        raise _AnchorConflict
    if count == 0:
        return None
    first = locator.first
    return first if first.is_visible() else None


def _can_wait(observation: DouyinProfilePageObservation) -> bool:
    return (
        observation.page_version is DouyinPageVersion.WEB_V1
        and observation.entry is DouyinPageEntry.USER_PROFILE
        and observation.state is DouyinProfilePageState.UNKNOWN
        and observation.evidence is DouyinProfilePageEvidence.REQUIRED_ANCHOR_MISSING
    )


def _observation(
    version: DouyinPageObservation,
    state: DouyinProfilePageState,
    evidence: DouyinProfilePageEvidence,
) -> DouyinProfilePageObservation:
    return DouyinProfilePageObservation(
        page_version=version.version,
        entry=version.entry,
        state=state,
        evidence=evidence,
    )


__all__ = [
    "DOUYIN_PROFILE_PAGE_SELECTOR_VERSION",
    "DouyinProfilePage",
    "DouyinProfilePageEvidence",
    "DouyinProfilePageObservation",
    "DouyinProfilePageRejected",
    "DouyinProfilePageState",
]
