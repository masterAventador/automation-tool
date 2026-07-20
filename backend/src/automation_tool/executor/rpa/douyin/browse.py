"""Single-shot, read-only Douyin target-profile navigation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, cast

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from automation_tool.executor.browser_runtime import BrowserWindow
from automation_tool.executor.rpa.douyin.page_version import douyin_user_profile_url
from automation_tool.executor.rpa.douyin.profile_page import (
    DouyinProfilePage,
    DouyinProfilePageEvidence,
    DouyinProfilePageObservation,
    DouyinProfilePageState,
)
from automation_tool.protocol import DouyinCandidate

DOUYIN_BROWSE_EXECUTION_VERSION = "douyin.browse-execution.v1"
_NAVIGATION_TIMEOUT_MILLISECONDS = 30_000
_PAGE_READY_TIMEOUT_MILLISECONDS = 10_000


class DouyinBrowseExecutionRejected(RuntimeError):
    def __init__(self) -> None:
        super().__init__("douyin browse execution is unavailable")


class DouyinBrowseExecutionState(StrEnum):
    COMPLETED = "completed"
    LOGIN_REQUIRED = "login_required"
    DIALOG_BLOCKED = "dialog_blocked"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    UNKNOWN = "unknown"


class DouyinBrowseExecutionEvidence(StrEnum):
    PROFILE_VISIBLE = "profile_visible"
    LOGIN_REQUIRED = "login_required"
    BLOCKING_DIALOG = "blocking_dialog"
    CANCELLATION_REQUESTED = "cancellation_requested"
    CANCELLATION_UNAVAILABLE = "cancellation_unavailable"
    NAVIGATION_TIMED_OUT = "navigation_timed_out"
    PROFILE_READY_TIMED_OUT = "profile_ready_timed_out"
    PAGE_VERSION_UNKNOWN = "page_version_unknown"
    CONFLICTING_ANCHORS = "conflicting_anchors"
    PAGE_UNAVAILABLE = "page_unavailable"


_ALLOWED_OBSERVATIONS = frozenset(
    {
        (
            DouyinBrowseExecutionState.COMPLETED,
            DouyinBrowseExecutionEvidence.PROFILE_VISIBLE,
        ),
        (
            DouyinBrowseExecutionState.LOGIN_REQUIRED,
            DouyinBrowseExecutionEvidence.LOGIN_REQUIRED,
        ),
        (
            DouyinBrowseExecutionState.DIALOG_BLOCKED,
            DouyinBrowseExecutionEvidence.BLOCKING_DIALOG,
        ),
        (
            DouyinBrowseExecutionState.CANCELLED,
            DouyinBrowseExecutionEvidence.CANCELLATION_REQUESTED,
        ),
        *(
            (DouyinBrowseExecutionState.TIMED_OUT, evidence)
            for evidence in (
                DouyinBrowseExecutionEvidence.NAVIGATION_TIMED_OUT,
                DouyinBrowseExecutionEvidence.PROFILE_READY_TIMED_OUT,
            )
        ),
        *(
            (DouyinBrowseExecutionState.UNKNOWN, evidence)
            for evidence in (
                DouyinBrowseExecutionEvidence.CANCELLATION_UNAVAILABLE,
                DouyinBrowseExecutionEvidence.PAGE_VERSION_UNKNOWN,
                DouyinBrowseExecutionEvidence.CONFLICTING_ANCHORS,
                DouyinBrowseExecutionEvidence.PAGE_UNAVAILABLE,
            )
        ),
    }
)


@dataclass(frozen=True, slots=True, repr=False)
class DouyinBrowseExecutionObservation:
    state: DouyinBrowseExecutionState
    evidence: DouyinBrowseExecutionEvidence
    execution_version: str = DOUYIN_BROWSE_EXECUTION_VERSION

    def __post_init__(self) -> None:
        if (
            not isinstance(self.state, DouyinBrowseExecutionState)
            or not isinstance(self.evidence, DouyinBrowseExecutionEvidence)
            or self.execution_version != DOUYIN_BROWSE_EXECUTION_VERSION
            or (self.state, self.evidence) not in _ALLOWED_OBSERVATIONS
        ):
            raise DouyinBrowseExecutionRejected

    @property
    def completed(self) -> bool:
        return self.state is DouyinBrowseExecutionState.COMPLETED

    @property
    def circuit_open(self) -> bool:
        return not self.completed

    def __repr__(self) -> str:
        return (
            "DouyinBrowseExecutionObservation("
            f"state={self.state.value!r}, evidence={self.evidence.value!r}, "
            f"execution_version={self.execution_version!r}, "
            f"circuit_open={self.circuit_open!r})"
        )


class _BrowsePage(Protocol):
    def goto(self, url: str, *, wait_until: str, timeout: float) -> object: ...


class DouyinBrowseExecution:
    """Navigate to one discovered target without triggering any page action."""

    def __init__(self, window: BrowserWindow, candidate: DouyinCandidate) -> None:
        if not isinstance(window, BrowserWindow) or not isinstance(candidate, DouyinCandidate):
            raise DouyinBrowseExecutionRejected
        self._page = cast(_BrowsePage, window.playwright_page)
        self._profile_page = DouyinProfilePage(window)
        self._candidate = candidate
        self._executed = False

    def __repr__(self) -> str:
        return "DouyinBrowseExecution(<redacted>)"

    def run(
        self,
        *,
        cancellation_requested: Callable[[], bool],
    ) -> DouyinBrowseExecutionObservation:
        if self._executed or not callable(cancellation_requested):
            raise DouyinBrowseExecutionRejected
        self._executed = True
        cancelled = _cancellation_requested(cancellation_requested)
        if cancelled is None:
            return _unavailable_cancellation()
        if cancelled:
            return _cancelled()

        target_url = douyin_user_profile_url(self._candidate.platform_target_id)
        try:
            self._page.goto(
                target_url,
                wait_until="domcontentloaded",
                timeout=_NAVIGATION_TIMEOUT_MILLISECONDS,
            )
        except PlaywrightTimeoutError:
            return _result(
                DouyinBrowseExecutionState.TIMED_OUT,
                DouyinBrowseExecutionEvidence.NAVIGATION_TIMED_OUT,
            )
        except Exception:
            return _unavailable()

        cancelled = _cancellation_requested(cancellation_requested)
        if cancelled is None:
            return _unavailable_cancellation()
        if cancelled:
            return _cancelled()

        profile = self._profile_page.wait_for_ready(
            timeout_milliseconds=_PAGE_READY_TIMEOUT_MILLISECONDS
        )
        result = _from_page_observation(profile)
        if result is not None:
            return result

        cancelled = _cancellation_requested(cancellation_requested)
        if cancelled is None:
            return _unavailable_cancellation()
        if cancelled:
            return _cancelled()
        try:
            self._profile_page.profile_root()
        except Exception:
            return _unavailable()
        return _result(
            DouyinBrowseExecutionState.COMPLETED,
            DouyinBrowseExecutionEvidence.PROFILE_VISIBLE,
        )


def _from_page_observation(
    observation: DouyinProfilePageObservation,
) -> DouyinBrowseExecutionObservation | None:
    if observation.state is DouyinProfilePageState.READY:
        return None
    if observation.state is DouyinProfilePageState.LOGIN_REQUIRED:
        return _result(
            DouyinBrowseExecutionState.LOGIN_REQUIRED,
            DouyinBrowseExecutionEvidence.LOGIN_REQUIRED,
        )
    if observation.state is DouyinProfilePageState.DIALOG_BLOCKED:
        return _result(
            DouyinBrowseExecutionState.DIALOG_BLOCKED,
            DouyinBrowseExecutionEvidence.BLOCKING_DIALOG,
        )
    evidence = {
        DouyinProfilePageEvidence.REQUIRED_ANCHOR_MISSING: (
            DouyinBrowseExecutionEvidence.PROFILE_READY_TIMED_OUT
        ),
        DouyinProfilePageEvidence.PAGE_VERSION_UNKNOWN: (
            DouyinBrowseExecutionEvidence.PAGE_VERSION_UNKNOWN
        ),
        DouyinProfilePageEvidence.CONFLICTING_ANCHORS: (
            DouyinBrowseExecutionEvidence.CONFLICTING_ANCHORS
        ),
        DouyinProfilePageEvidence.PAGE_UNAVAILABLE: (
            DouyinBrowseExecutionEvidence.PAGE_UNAVAILABLE
        ),
    }.get(observation.evidence, DouyinBrowseExecutionEvidence.PAGE_UNAVAILABLE)
    state = (
        DouyinBrowseExecutionState.TIMED_OUT
        if evidence is DouyinBrowseExecutionEvidence.PROFILE_READY_TIMED_OUT
        else DouyinBrowseExecutionState.UNKNOWN
    )
    return _result(state, evidence)


def _cancellation_requested(check: Callable[[], bool]) -> bool | None:
    try:
        value = check()
    except Exception:
        return None
    return value if type(value) is bool else None


def _cancelled() -> DouyinBrowseExecutionObservation:
    return _result(
        DouyinBrowseExecutionState.CANCELLED,
        DouyinBrowseExecutionEvidence.CANCELLATION_REQUESTED,
    )


def _unavailable_cancellation() -> DouyinBrowseExecutionObservation:
    return _result(
        DouyinBrowseExecutionState.UNKNOWN,
        DouyinBrowseExecutionEvidence.CANCELLATION_UNAVAILABLE,
    )


def _unavailable() -> DouyinBrowseExecutionObservation:
    return _result(
        DouyinBrowseExecutionState.UNKNOWN,
        DouyinBrowseExecutionEvidence.PAGE_UNAVAILABLE,
    )


def _result(
    state: DouyinBrowseExecutionState,
    evidence: DouyinBrowseExecutionEvidence,
) -> DouyinBrowseExecutionObservation:
    return DouyinBrowseExecutionObservation(state=state, evidence=evidence)


__all__ = [
    "DOUYIN_BROWSE_EXECUTION_VERSION",
    "DouyinBrowseExecution",
    "DouyinBrowseExecutionEvidence",
    "DouyinBrowseExecutionObservation",
    "DouyinBrowseExecutionRejected",
    "DouyinBrowseExecutionState",
]
