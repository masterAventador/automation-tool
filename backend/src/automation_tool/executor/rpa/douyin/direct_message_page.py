"""Versioned, non-executing Douyin direct-message Page Object."""

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

DOUYIN_DIRECT_MESSAGE_PAGE_SELECTOR_VERSION = "douyin.direct-message-page.v1"

_MESSAGE_ENTRY_SELECTORS = (
    'button[aria-label="私信"]',
    '[role="button"][aria-label="私信"]',
    '[data-e2e="direct-message-entry"]',
)
_MESSAGE_INPUT_SELECTORS = (
    'textarea[aria-label="发送私信"]',
    'textarea[placeholder="发送私信"]',
    '[contenteditable="true"][data-e2e="direct-message-input"]',
)
_MESSAGE_SEND_SELECTORS = (
    'button[aria-label="发送私信"]',
    '[role="button"][aria-label="发送私信"]',
    '[data-e2e="direct-message-send"]',
)
_FINAL_CONFIRMATION_SELECTORS = (
    '[role="status"]:has-text("私信发送成功")',
    '[data-e2e="direct-message-send-success"]',
)
_MESSAGING_NOT_ALLOWED_SELECTORS = (
    '[role="alert"]:has-text("暂时无法私信")',
    '[data-e2e="direct-message-unavailable"]',
)
_FOLLOW_REQUIRED_SELECTORS = (
    '[role="alert"]:has-text("关注后才能私信")',
    '[data-e2e="direct-message-follow-required"]',
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


class DouyinDirectMessagePageRejected(RuntimeError):
    def __init__(self) -> None:
        super().__init__("douyin direct-message page is unavailable")


class DouyinDirectMessagePageState(StrEnum):
    PROFILE_READY = "profile_ready"
    CONVERSATION_READY = "conversation_ready"
    CONFIRMED = "confirmed"
    PERMISSION_DENIED = "permission_denied"
    LOGIN_REQUIRED = "login_required"
    DIALOG_BLOCKED = "dialog_blocked"
    UNKNOWN = "unknown"


class DouyinDirectMessagePageEvidence(StrEnum):
    ENTER_CONVERSATION_VISIBLE = "enter_conversation_visible"
    INPUT_AND_SEND_VISIBLE = "input_and_send_visible"
    FINAL_CONFIRMATION_VISIBLE = "final_confirmation_visible"
    MESSAGING_NOT_ALLOWED = "messaging_not_allowed"
    FOLLOW_REQUIRED = "follow_required"
    LOGIN_REDIRECT = "login_redirect"
    LOGIN_DIALOG = "login_dialog"
    BLOCKING_DIALOG = "blocking_dialog"
    REQUIRED_ANCHOR_MISSING = "required_anchor_missing"
    CONFLICTING_ANCHORS = "conflicting_anchors"
    PAGE_VERSION_UNKNOWN = "page_version_unknown"
    PAGE_UNAVAILABLE = "page_unavailable"


_PROFILE_OBSERVATIONS = (
    (
        DouyinDirectMessagePageState.PROFILE_READY,
        DouyinDirectMessagePageEvidence.ENTER_CONVERSATION_VISIBLE,
    ),
    (
        DouyinDirectMessagePageState.CONVERSATION_READY,
        DouyinDirectMessagePageEvidence.INPUT_AND_SEND_VISIBLE,
    ),
    (
        DouyinDirectMessagePageState.CONFIRMED,
        DouyinDirectMessagePageEvidence.FINAL_CONFIRMATION_VISIBLE,
    ),
    (
        DouyinDirectMessagePageState.PERMISSION_DENIED,
        DouyinDirectMessagePageEvidence.MESSAGING_NOT_ALLOWED,
    ),
    (
        DouyinDirectMessagePageState.PERMISSION_DENIED,
        DouyinDirectMessagePageEvidence.FOLLOW_REQUIRED,
    ),
    (
        DouyinDirectMessagePageState.LOGIN_REQUIRED,
        DouyinDirectMessagePageEvidence.LOGIN_DIALOG,
    ),
    (
        DouyinDirectMessagePageState.DIALOG_BLOCKED,
        DouyinDirectMessagePageEvidence.BLOCKING_DIALOG,
    ),
    *(
        (DouyinDirectMessagePageState.UNKNOWN, evidence)
        for evidence in (
            DouyinDirectMessagePageEvidence.REQUIRED_ANCHOR_MISSING,
            DouyinDirectMessagePageEvidence.CONFLICTING_ANCHORS,
            DouyinDirectMessagePageEvidence.PAGE_UNAVAILABLE,
        )
    ),
)
_ALLOWED_OBSERVATIONS = frozenset(
    {
        *(
            (
                DouyinPageVersion.WEB_V1,
                DouyinPageEntry.USER_PROFILE,
                state,
                evidence,
            )
            for state, evidence in _PROFILE_OBSERVATIONS
        ),
        (
            DouyinPageVersion.WEB_V1,
            DouyinPageEntry.SESSION_PROBE,
            DouyinDirectMessagePageState.LOGIN_REQUIRED,
            DouyinDirectMessagePageEvidence.LOGIN_REDIRECT,
        ),
        *(
            (
                DouyinPageVersion.WEB_V1,
                entry,
                DouyinDirectMessagePageState.UNKNOWN,
                DouyinDirectMessagePageEvidence.PAGE_VERSION_UNKNOWN,
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
            DouyinDirectMessagePageState.UNKNOWN,
            DouyinDirectMessagePageEvidence.PAGE_VERSION_UNKNOWN,
        ),
        (
            DouyinPageVersion.UNKNOWN,
            DouyinPageEntry.UNKNOWN,
            DouyinDirectMessagePageState.UNKNOWN,
            DouyinDirectMessagePageEvidence.PAGE_UNAVAILABLE,
        ),
    }
)


@dataclass(frozen=True, slots=True, repr=False)
class DouyinDirectMessagePageObservation:
    page_version: DouyinPageVersion
    entry: DouyinPageEntry
    state: DouyinDirectMessagePageState
    evidence: DouyinDirectMessagePageEvidence
    selector_version: str = DOUYIN_DIRECT_MESSAGE_PAGE_SELECTOR_VERSION

    def __post_init__(self) -> None:
        if (
            not isinstance(self.page_version, DouyinPageVersion)
            or not isinstance(self.entry, DouyinPageEntry)
            or not isinstance(self.state, DouyinDirectMessagePageState)
            or not isinstance(self.evidence, DouyinDirectMessagePageEvidence)
            or self.selector_version != DOUYIN_DIRECT_MESSAGE_PAGE_SELECTOR_VERSION
            or (self.page_version, self.entry, self.state, self.evidence)
            not in _ALLOWED_OBSERVATIONS
        ):
            raise DouyinDirectMessagePageRejected

    @property
    def profile_ready(self) -> bool:
        return self.state is DouyinDirectMessagePageState.PROFILE_READY

    @property
    def conversation_ready(self) -> bool:
        return self.state is DouyinDirectMessagePageState.CONVERSATION_READY

    @property
    def confirmed(self) -> bool:
        return self.state is DouyinDirectMessagePageState.CONFIRMED

    @property
    def circuit_open(self) -> bool:
        return self.state not in {
            DouyinDirectMessagePageState.PROFILE_READY,
            DouyinDirectMessagePageState.CONVERSATION_READY,
            DouyinDirectMessagePageState.CONFIRMED,
        }

    def __repr__(self) -> str:
        return (
            "DouyinDirectMessagePageObservation("
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


class DouyinDirectMessagePage:
    """Own message anchors and permission states without performing actions."""

    def __init__(self, window: BrowserWindow) -> None:
        if not isinstance(window, BrowserWindow):
            raise DouyinDirectMessagePageRejected
        self._page = cast(_Page, window.playwright_page)
        self._versions = DouyinPageVersionModel()

    def __repr__(self) -> str:
        return "DouyinDirectMessagePage(<redacted>)"

    def observe(self) -> DouyinDirectMessagePageObservation:
        try:
            version = self._versions.check(self._page.url)
        except Exception:
            return self._page_unavailable()
        if not version.compatible:
            return _observation(
                version,
                DouyinDirectMessagePageState.UNKNOWN,
                DouyinDirectMessagePageEvidence.PAGE_VERSION_UNKNOWN,
            )
        if version.entry is DouyinPageEntry.SESSION_PROBE:
            return _observation(
                version,
                DouyinDirectMessagePageState.LOGIN_REQUIRED,
                DouyinDirectMessagePageEvidence.LOGIN_REDIRECT,
            )
        if version.entry is not DouyinPageEntry.USER_PROFILE:
            return _observation(
                version,
                DouyinDirectMessagePageState.UNKNOWN,
                DouyinDirectMessagePageEvidence.PAGE_VERSION_UNKNOWN,
            )
        try:
            result = self._observe_profile(version)
        except AnchorConflict:
            return _observation(
                version,
                DouyinDirectMessagePageState.UNKNOWN,
                DouyinDirectMessagePageEvidence.CONFLICTING_ANCHORS,
            )
        except Exception:
            return _observation(
                version,
                DouyinDirectMessagePageState.UNKNOWN,
                DouyinDirectMessagePageEvidence.PAGE_UNAVAILABLE,
            )
        return result

    def _observe_profile(
        self, version: DouyinPageObservation
    ) -> DouyinDirectMessagePageObservation:
        if any_visible(self._page, _LOGIN_DIALOG_SELECTORS):
            return _observation(
                version,
                DouyinDirectMessagePageState.LOGIN_REQUIRED,
                DouyinDirectMessagePageEvidence.LOGIN_DIALOG,
            )
        if any_visible(self._page, _BLOCKING_DIALOG_SELECTORS):
            return _observation(
                version,
                DouyinDirectMessagePageState.DIALOG_BLOCKED,
                DouyinDirectMessagePageEvidence.BLOCKING_DIALOG,
            )
        unavailable = unique_visible(self._page, _MESSAGING_NOT_ALLOWED_SELECTORS)
        follow_required = unique_visible(self._page, _FOLLOW_REQUIRED_SELECTORS)
        if unavailable is not None and follow_required is not None:
            raise AnchorConflict
        if unavailable is not None:
            return _observation(
                version,
                DouyinDirectMessagePageState.PERMISSION_DENIED,
                DouyinDirectMessagePageEvidence.MESSAGING_NOT_ALLOWED,
            )
        if follow_required is not None:
            return _observation(
                version,
                DouyinDirectMessagePageState.PERMISSION_DENIED,
                DouyinDirectMessagePageEvidence.FOLLOW_REQUIRED,
            )
        if unique_visible(self._page, _FINAL_CONFIRMATION_SELECTORS) is not None:
            return _observation(
                version,
                DouyinDirectMessagePageState.CONFIRMED,
                DouyinDirectMessagePageEvidence.FINAL_CONFIRMATION_VISIBLE,
            )
        entry = unique_visible(self._page, _MESSAGE_ENTRY_SELECTORS)
        message_input = unique_visible(self._page, _MESSAGE_INPUT_SELECTORS)
        message_send = unique_visible(self._page, _MESSAGE_SEND_SELECTORS)
        if message_input is not None and message_send is not None:
            return _observation(
                version,
                DouyinDirectMessagePageState.CONVERSATION_READY,
                DouyinDirectMessagePageEvidence.INPUT_AND_SEND_VISIBLE,
            )
        if entry is not None and message_input is None and message_send is None:
            return _observation(
                version,
                DouyinDirectMessagePageState.PROFILE_READY,
                DouyinDirectMessagePageEvidence.ENTER_CONVERSATION_VISIBLE,
            )
        if entry is not None and (message_input is not None or message_send is not None):
            raise AnchorConflict
        return _observation(
            version,
            DouyinDirectMessagePageState.UNKNOWN,
            DouyinDirectMessagePageEvidence.REQUIRED_ANCHOR_MISSING,
        )

    def enter_conversation(self) -> AnchorLocator:
        self._require_state(DouyinDirectMessagePageState.PROFILE_READY)
        return self._require_locator(_MESSAGE_ENTRY_SELECTORS)

    def message_input(self) -> AnchorLocator:
        self._require_state(DouyinDirectMessagePageState.CONVERSATION_READY)
        return self._require_locator(_MESSAGE_INPUT_SELECTORS)

    def message_send(self) -> AnchorLocator:
        self._require_state(DouyinDirectMessagePageState.CONVERSATION_READY)
        return self._require_locator(_MESSAGE_SEND_SELECTORS)

    def final_confirmation(self) -> AnchorLocator:
        self._require_state(DouyinDirectMessagePageState.CONFIRMED)
        return self._require_locator(_FINAL_CONFIRMATION_SELECTORS)

    def permission_notice(self) -> AnchorLocator:
        observation = self.observe()
        if observation.state is not DouyinDirectMessagePageState.PERMISSION_DENIED:
            raise DouyinDirectMessagePageRejected
        selectors = (
            _FOLLOW_REQUIRED_SELECTORS
            if observation.evidence is DouyinDirectMessagePageEvidence.FOLLOW_REQUIRED
            else _MESSAGING_NOT_ALLOWED_SELECTORS
        )
        return self._require_locator(selectors)

    def wait_for_profile_ready(
        self, *, timeout_milliseconds: int
    ) -> DouyinDirectMessagePageObservation:
        return self._wait_for(
            expected=DouyinDirectMessagePageState.PROFILE_READY,
            anchor_groups=(_MESSAGE_ENTRY_SELECTORS,),
            timeout_milliseconds=timeout_milliseconds,
        )

    def wait_for_conversation_ready(
        self, *, timeout_milliseconds: int
    ) -> DouyinDirectMessagePageObservation:
        return self._wait_for(
            expected=DouyinDirectMessagePageState.CONVERSATION_READY,
            anchor_groups=(_MESSAGE_INPUT_SELECTORS, _MESSAGE_SEND_SELECTORS),
            timeout_milliseconds=timeout_milliseconds,
        )

    def wait_for_final(self, *, timeout_milliseconds: int) -> DouyinDirectMessagePageObservation:
        return self._wait_for(
            expected=DouyinDirectMessagePageState.CONFIRMED,
            anchor_groups=(_FINAL_CONFIRMATION_SELECTORS,),
            timeout_milliseconds=timeout_milliseconds,
        )

    def _require_state(self, expected: DouyinDirectMessagePageState) -> None:
        if self.observe().state is not expected:
            raise DouyinDirectMessagePageRejected

    def _require_locator(self, selectors: tuple[str, ...]) -> AnchorLocator:
        try:
            locator = unique_visible(self._page, selectors)
        except Exception:
            raise DouyinDirectMessagePageRejected from None
        if locator is None:
            raise DouyinDirectMessagePageRejected
        return locator

    def _wait_for(
        self,
        *,
        expected: DouyinDirectMessagePageState,
        anchor_groups: tuple[tuple[str, ...], ...],
        timeout_milliseconds: int,
    ) -> DouyinDirectMessagePageObservation:
        if (
            type(timeout_milliseconds) is not int
            or not 1 <= timeout_milliseconds <= _MAX_WAIT_MILLISECONDS
        ):
            raise DouyinDirectMessagePageRejected
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

    def _page_unavailable(self) -> DouyinDirectMessagePageObservation:
        try:
            version = self._versions.check(self._page.url)
        except Exception:
            version = self._versions.check("")
        return _observation(
            version,
            DouyinDirectMessagePageState.UNKNOWN,
            DouyinDirectMessagePageEvidence.PAGE_UNAVAILABLE,
        )


def _can_wait(
    observation: DouyinDirectMessagePageObservation,
    expected: DouyinDirectMessagePageState,
) -> bool:
    if (
        observation.page_version is not DouyinPageVersion.WEB_V1
        or observation.entry is not DouyinPageEntry.USER_PROFILE
    ):
        return False
    allowed = {
        DouyinDirectMessagePageState.PROFILE_READY: {DouyinDirectMessagePageState.UNKNOWN},
        DouyinDirectMessagePageState.CONVERSATION_READY: {
            DouyinDirectMessagePageState.PROFILE_READY,
            DouyinDirectMessagePageState.UNKNOWN,
        },
        DouyinDirectMessagePageState.CONFIRMED: {
            DouyinDirectMessagePageState.CONVERSATION_READY,
            DouyinDirectMessagePageState.UNKNOWN,
        },
    }.get(expected, set())
    return observation.state in allowed and (
        observation.state is not DouyinDirectMessagePageState.UNKNOWN
        or observation.evidence is DouyinDirectMessagePageEvidence.REQUIRED_ANCHOR_MISSING
    )


def _observation(
    version: DouyinPageObservation,
    state: DouyinDirectMessagePageState,
    evidence: DouyinDirectMessagePageEvidence,
) -> DouyinDirectMessagePageObservation:
    return DouyinDirectMessagePageObservation(
        page_version=version.version,
        entry=version.entry,
        state=state,
        evidence=evidence,
    )


__all__ = [
    "DOUYIN_DIRECT_MESSAGE_PAGE_SELECTOR_VERSION",
    "DouyinDirectMessagePage",
    "DouyinDirectMessagePageEvidence",
    "DouyinDirectMessagePageObservation",
    "DouyinDirectMessagePageRejected",
    "DouyinDirectMessagePageState",
]
