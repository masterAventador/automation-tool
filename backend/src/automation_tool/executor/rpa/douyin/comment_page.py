"""Versioned, non-executing Douyin comment Page Object."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from time import monotonic
from typing import Protocol, cast

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from automation_tool.executor.browser_runtime import BrowserWindow
from automation_tool.executor.rpa.douyin.page_anchors import (
    AnchorConflict,
    AnchorLocator,
    any_visible,
    unique_visible,
    visible_matches,
)
from automation_tool.executor.rpa.douyin.page_version import (
    DouyinPageEntry,
    DouyinPageObservation,
    DouyinPageVersion,
    DouyinPageVersionModel,
)
from automation_tool.executor.rpa.douyin.session import DOUYIN_RISK_CHALLENGE_SELECTORS

DOUYIN_COMMENT_PAGE_SELECTOR_VERSION = "douyin.comment-page.v1"

_COMMENT_INPUT_SELECTORS = (
    'textarea[aria-label="留下你的精彩评论"]',
    'textarea[placeholder="留下你的精彩评论"]',
    '[contenteditable="true"][data-e2e="comment-input"]',
    '[data-e2e="comment-textarea"]',
)
_COMMENT_SUBMIT_SELECTORS = (
    'button[aria-label="发表评论"]',
    '[role="button"][aria-label="发表评论"]',
    '[data-e2e="comment-submit"]',
)
_FINAL_CONFIRMATION_SELECTORS = (
    '[role="status"]:has-text("评论成功")',
    '[data-e2e="comment-publish-success"]',
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


class DouyinCommentPageRejected(RuntimeError):
    def __init__(self) -> None:
        super().__init__("douyin comment page is unavailable")


class DouyinCommentPageState(StrEnum):
    READY = "ready"
    CONFIRMED = "confirmed"
    LOGIN_REQUIRED = "login_required"
    DIALOG_BLOCKED = "dialog_blocked"
    UNKNOWN = "unknown"


class DouyinCommentPageEvidence(StrEnum):
    INPUT_AND_SUBMIT_VISIBLE = "input_and_submit_visible"
    FINAL_CONFIRMATION_VISIBLE = "final_confirmation_visible"
    LOGIN_REDIRECT = "login_redirect"
    LOGIN_DIALOG = "login_dialog"
    BLOCKING_DIALOG = "blocking_dialog"
    REQUIRED_ANCHOR_MISSING = "required_anchor_missing"
    CONFLICTING_ANCHORS = "conflicting_anchors"
    PAGE_VERSION_UNKNOWN = "page_version_unknown"
    PAGE_UNAVAILABLE = "page_unavailable"


_ALLOWED_OBSERVATIONS = frozenset(
    {
        *(
            (
                DouyinPageVersion.WEB_V1,
                DouyinPageEntry.VIDEO_DETAIL,
                state,
                evidence,
            )
            for state, evidence in (
                (
                    DouyinCommentPageState.READY,
                    DouyinCommentPageEvidence.INPUT_AND_SUBMIT_VISIBLE,
                ),
                (
                    DouyinCommentPageState.CONFIRMED,
                    DouyinCommentPageEvidence.FINAL_CONFIRMATION_VISIBLE,
                ),
                (
                    DouyinCommentPageState.LOGIN_REQUIRED,
                    DouyinCommentPageEvidence.LOGIN_DIALOG,
                ),
                (
                    DouyinCommentPageState.DIALOG_BLOCKED,
                    DouyinCommentPageEvidence.BLOCKING_DIALOG,
                ),
                *(
                    (DouyinCommentPageState.UNKNOWN, unknown_evidence)
                    for unknown_evidence in (
                        DouyinCommentPageEvidence.REQUIRED_ANCHOR_MISSING,
                        DouyinCommentPageEvidence.CONFLICTING_ANCHORS,
                        DouyinCommentPageEvidence.PAGE_UNAVAILABLE,
                    )
                ),
            )
        ),
        (
            DouyinPageVersion.WEB_V1,
            DouyinPageEntry.SESSION_PROBE,
            DouyinCommentPageState.LOGIN_REQUIRED,
            DouyinCommentPageEvidence.LOGIN_REDIRECT,
        ),
        *(
            (
                DouyinPageVersion.WEB_V1,
                entry,
                DouyinCommentPageState.UNKNOWN,
                DouyinCommentPageEvidence.PAGE_VERSION_UNKNOWN,
            )
            for entry in (
                DouyinPageEntry.HOME,
                DouyinPageEntry.SEARCH_RESULTS,
                DouyinPageEntry.USER_PROFILE,
            )
        ),
        (
            DouyinPageVersion.UNKNOWN,
            DouyinPageEntry.UNKNOWN,
            DouyinCommentPageState.UNKNOWN,
            DouyinCommentPageEvidence.PAGE_VERSION_UNKNOWN,
        ),
        (
            DouyinPageVersion.UNKNOWN,
            DouyinPageEntry.UNKNOWN,
            DouyinCommentPageState.UNKNOWN,
            DouyinCommentPageEvidence.PAGE_UNAVAILABLE,
        ),
    }
)


@dataclass(frozen=True, slots=True, repr=False)
class DouyinCommentPageObservation:
    page_version: DouyinPageVersion
    entry: DouyinPageEntry
    state: DouyinCommentPageState
    evidence: DouyinCommentPageEvidence
    selector_version: str = DOUYIN_COMMENT_PAGE_SELECTOR_VERSION

    def __post_init__(self) -> None:
        if (
            not isinstance(self.page_version, DouyinPageVersion)
            or not isinstance(self.entry, DouyinPageEntry)
            or not isinstance(self.state, DouyinCommentPageState)
            or not isinstance(self.evidence, DouyinCommentPageEvidence)
            or self.selector_version != DOUYIN_COMMENT_PAGE_SELECTOR_VERSION
            or (self.page_version, self.entry, self.state, self.evidence)
            not in _ALLOWED_OBSERVATIONS
        ):
            raise DouyinCommentPageRejected

    @property
    def ready(self) -> bool:
        return self.state is DouyinCommentPageState.READY

    @property
    def confirmed(self) -> bool:
        return self.state is DouyinCommentPageState.CONFIRMED

    @property
    def circuit_open(self) -> bool:
        return self.state not in {
            DouyinCommentPageState.READY,
            DouyinCommentPageState.CONFIRMED,
        }

    def __repr__(self) -> str:
        return (
            "DouyinCommentPageObservation("
            f"page_version={self.page_version.value!r}, entry={self.entry.value!r}, "
            f"state={self.state.value!r}, evidence={self.evidence.value!r}, "
            f"selector_version={self.selector_version!r}, "
            f"circuit_open={self.circuit_open!r})"
        )


class _WaitLocator(Protocol):
    @property
    def first(self) -> _WaitLocator: ...

    def wait_for(self, *, state: str, timeout: float) -> None: ...


class _Page(Protocol):
    @property
    def url(self) -> str: ...

    def locator(self, selector: str) -> AnchorLocator: ...


class DouyinCommentPage:
    """Own comment anchors without performing text entry or platform actions."""

    def __init__(self, window: BrowserWindow) -> None:
        if not isinstance(window, BrowserWindow):
            raise DouyinCommentPageRejected
        self._page = cast(_Page, window.playwright_page)
        self._versions = DouyinPageVersionModel()

    def __repr__(self) -> str:
        return "DouyinCommentPage(<redacted>)"

    def observe(self) -> DouyinCommentPageObservation:
        try:
            version = self._versions.check(self._page.url)
        except Exception:
            return self._page_unavailable()
        if not version.compatible:
            return _observation(
                version,
                DouyinCommentPageState.UNKNOWN,
                DouyinCommentPageEvidence.PAGE_VERSION_UNKNOWN,
            )
        if version.entry is DouyinPageEntry.SESSION_PROBE:
            return _observation(
                version,
                DouyinCommentPageState.LOGIN_REQUIRED,
                DouyinCommentPageEvidence.LOGIN_REDIRECT,
            )
        if version.entry is not DouyinPageEntry.VIDEO_DETAIL:
            return _observation(
                version,
                DouyinCommentPageState.UNKNOWN,
                DouyinCommentPageEvidence.PAGE_VERSION_UNKNOWN,
            )
        try:
            if any_visible(self._page, _LOGIN_DIALOG_SELECTORS):
                return _observation(
                    version,
                    DouyinCommentPageState.LOGIN_REQUIRED,
                    DouyinCommentPageEvidence.LOGIN_DIALOG,
                )
            if any_visible(self._page, _BLOCKING_DIALOG_SELECTORS):
                return _observation(
                    version,
                    DouyinCommentPageState.DIALOG_BLOCKED,
                    DouyinCommentPageEvidence.BLOCKING_DIALOG,
                )
            if unique_visible(self._page, _FINAL_CONFIRMATION_SELECTORS) is not None:
                return _observation(
                    version,
                    DouyinCommentPageState.CONFIRMED,
                    DouyinCommentPageEvidence.FINAL_CONFIRMATION_VISIBLE,
                )
            comment_input = unique_visible(self._page, _COMMENT_INPUT_SELECTORS)
            comment_submit = unique_visible(self._page, _COMMENT_SUBMIT_SELECTORS)
        except AnchorConflict:
            return _observation(
                version,
                DouyinCommentPageState.UNKNOWN,
                DouyinCommentPageEvidence.CONFLICTING_ANCHORS,
            )
        except Exception:
            return _observation(
                version,
                DouyinCommentPageState.UNKNOWN,
                DouyinCommentPageEvidence.PAGE_UNAVAILABLE,
            )
        if comment_input is not None and comment_submit is not None:
            return _observation(
                version,
                DouyinCommentPageState.READY,
                DouyinCommentPageEvidence.INPUT_AND_SUBMIT_VISIBLE,
            )
        return _observation(
            version,
            DouyinCommentPageState.UNKNOWN,
            DouyinCommentPageEvidence.REQUIRED_ANCHOR_MISSING,
        )

    def comment_input(self) -> AnchorLocator:
        self._require_state(DouyinCommentPageState.READY)
        return self._require_locator(_COMMENT_INPUT_SELECTORS)

    def comment_submit(self) -> AnchorLocator:
        self._require_state(DouyinCommentPageState.READY)
        return self._require_locator(_COMMENT_SUBMIT_SELECTORS)

    def final_confirmation(self) -> AnchorLocator:
        self._require_state(DouyinCommentPageState.CONFIRMED)
        return self._require_locator(_FINAL_CONFIRMATION_SELECTORS)

    def wait_for_ready(self, *, timeout_milliseconds: int) -> DouyinCommentPageObservation:
        return self._wait_for(
            expected=DouyinCommentPageState.READY,
            anchor_groups=(_COMMENT_INPUT_SELECTORS, _COMMENT_SUBMIT_SELECTORS),
            timeout_milliseconds=timeout_milliseconds,
        )

    def wait_for_final(self, *, timeout_milliseconds: int) -> DouyinCommentPageObservation:
        return self._wait_for(
            expected=DouyinCommentPageState.CONFIRMED,
            anchor_groups=(_FINAL_CONFIRMATION_SELECTORS,),
            timeout_milliseconds=timeout_milliseconds,
        )

    def _require_state(self, expected: DouyinCommentPageState) -> None:
        if self.observe().state is not expected:
            raise DouyinCommentPageRejected

    def _require_locator(self, selectors: tuple[str, ...]) -> AnchorLocator:
        try:
            locator = unique_visible(self._page, selectors)
        except Exception:
            raise DouyinCommentPageRejected from None
        if locator is None:
            raise DouyinCommentPageRejected
        return locator

    def _wait_for(
        self,
        *,
        expected: DouyinCommentPageState,
        anchor_groups: tuple[tuple[str, ...], ...],
        timeout_milliseconds: int,
    ) -> DouyinCommentPageObservation:
        if (
            type(timeout_milliseconds) is not int
            or not 1 <= timeout_milliseconds <= _MAX_WAIT_MILLISECONDS
        ):
            raise DouyinCommentPageRejected
        observation = self.observe()
        if observation.state is expected or not _can_wait(observation, expected):
            return observation
        deadline = monotonic() + timeout_milliseconds / 1_000
        for selectors in anchor_groups:
            remaining = (deadline - monotonic()) * 1_000
            if remaining <= 0:
                return self.observe()
            try:
                cast(
                    _WaitLocator,
                    visible_matches(self._page, ", ".join(selectors)),
                ).first.wait_for(state="visible", timeout=remaining)
            except PlaywrightTimeoutError:
                return self.observe()
            except Exception:
                return self._page_unavailable()
            observation = self.observe()
            if observation.state is expected or not _can_wait(observation, expected):
                return observation
        return observation

    def _page_unavailable(self) -> DouyinCommentPageObservation:
        try:
            version = self._versions.check(self._page.url)
        except Exception:
            version = self._versions.check("")
        return _observation(
            version,
            DouyinCommentPageState.UNKNOWN,
            DouyinCommentPageEvidence.PAGE_UNAVAILABLE,
        )


def _can_wait(
    observation: DouyinCommentPageObservation,
    expected: DouyinCommentPageState,
) -> bool:
    if (
        observation.page_version is not DouyinPageVersion.WEB_V1
        or observation.entry is not DouyinPageEntry.VIDEO_DETAIL
    ):
        return False
    if expected is DouyinCommentPageState.READY:
        return (
            observation.state is DouyinCommentPageState.UNKNOWN
            and observation.evidence is DouyinCommentPageEvidence.REQUIRED_ANCHOR_MISSING
        )
    return expected is DouyinCommentPageState.CONFIRMED and (
        observation.state is DouyinCommentPageState.READY
        or (
            observation.state is DouyinCommentPageState.UNKNOWN
            and observation.evidence is DouyinCommentPageEvidence.REQUIRED_ANCHOR_MISSING
        )
    )


def _observation(
    version: DouyinPageObservation,
    state: DouyinCommentPageState,
    evidence: DouyinCommentPageEvidence,
) -> DouyinCommentPageObservation:
    return DouyinCommentPageObservation(
        page_version=version.version,
        entry=version.entry,
        state=state,
        evidence=evidence,
    )


__all__ = [
    "DOUYIN_COMMENT_PAGE_SELECTOR_VERSION",
    "DouyinCommentPage",
    "DouyinCommentPageEvidence",
    "DouyinCommentPageObservation",
    "DouyinCommentPageRejected",
    "DouyinCommentPageState",
]
