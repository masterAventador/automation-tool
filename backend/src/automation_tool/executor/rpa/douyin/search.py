"""Single-shot, bounded Douyin search execution over the versioned Page Object."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, cast

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from automation_tool.executor.browser_runtime import BrowserWindow
from automation_tool.executor.rpa.douyin.page_version import (
    DOUYIN_HOME_URL,
    douyin_search_results_url,
)
from automation_tool.executor.rpa.douyin.search_page import (
    DouyinSearchPage,
    DouyinSearchPageEvidence,
    DouyinSearchPageObservation,
    DouyinSearchPageState,
)
from automation_tool.protocol import DouyinSearchInput

DOUYIN_SEARCH_EXECUTION_VERSION = "douyin.search-execution.v1"
_NAVIGATION_TIMEOUT_MILLISECONDS = 30_000
_PAGE_READY_TIMEOUT_MILLISECONDS = 10_000
_ACTION_TIMEOUT_MILLISECONDS = 15_000
_RESULT_URL_TIMEOUT_MILLISECONDS = 30_000


class DouyinSearchExecutionRejected(RuntimeError):
    """The search execution cannot run inside its fixed safety boundary."""

    def __init__(self) -> None:
        super().__init__("douyin search execution is unavailable")


class DouyinSearchExecutionState(StrEnum):
    SUCCEEDED = "succeeded"
    LOGIN_REQUIRED = "login_required"
    DIALOG_BLOCKED = "dialog_blocked"
    TIMED_OUT = "timed_out"
    UNKNOWN = "unknown"


class DouyinSearchExecutionEvidence(StrEnum):
    RESULTS_READY = "results_ready"
    LOGIN_REQUIRED = "login_required"
    BLOCKING_DIALOG = "blocking_dialog"
    NAVIGATION_TIMED_OUT = "navigation_timed_out"
    HOME_READY_TIMED_OUT = "home_ready_timed_out"
    ACTION_TIMED_OUT = "action_timed_out"
    RESULT_URL_TIMED_OUT = "result_url_timed_out"
    RESULTS_READY_TIMED_OUT = "results_ready_timed_out"
    PAGE_VERSION_UNKNOWN = "page_version_unknown"
    CONFLICTING_ANCHORS = "conflicting_anchors"
    PAGE_UNAVAILABLE = "page_unavailable"


_ALLOWED_OBSERVATIONS = frozenset(
    {
        (DouyinSearchExecutionState.SUCCEEDED, DouyinSearchExecutionEvidence.RESULTS_READY),
        (
            DouyinSearchExecutionState.LOGIN_REQUIRED,
            DouyinSearchExecutionEvidence.LOGIN_REQUIRED,
        ),
        (
            DouyinSearchExecutionState.DIALOG_BLOCKED,
            DouyinSearchExecutionEvidence.BLOCKING_DIALOG,
        ),
        *(
            (DouyinSearchExecutionState.TIMED_OUT, evidence)
            for evidence in (
                DouyinSearchExecutionEvidence.NAVIGATION_TIMED_OUT,
                DouyinSearchExecutionEvidence.HOME_READY_TIMED_OUT,
                DouyinSearchExecutionEvidence.ACTION_TIMED_OUT,
                DouyinSearchExecutionEvidence.RESULT_URL_TIMED_OUT,
                DouyinSearchExecutionEvidence.RESULTS_READY_TIMED_OUT,
            )
        ),
        *(
            (DouyinSearchExecutionState.UNKNOWN, evidence)
            for evidence in (
                DouyinSearchExecutionEvidence.PAGE_VERSION_UNKNOWN,
                DouyinSearchExecutionEvidence.CONFLICTING_ANCHORS,
                DouyinSearchExecutionEvidence.PAGE_UNAVAILABLE,
            )
        ),
    }
)


@dataclass(frozen=True, slots=True, repr=False)
class DouyinSearchExecutionObservation:
    state: DouyinSearchExecutionState
    evidence: DouyinSearchExecutionEvidence
    execution_version: str = DOUYIN_SEARCH_EXECUTION_VERSION

    def __post_init__(self) -> None:
        if (
            not isinstance(self.state, DouyinSearchExecutionState)
            or not isinstance(self.evidence, DouyinSearchExecutionEvidence)
            or self.execution_version != DOUYIN_SEARCH_EXECUTION_VERSION
            or (self.state, self.evidence) not in _ALLOWED_OBSERVATIONS
        ):
            raise DouyinSearchExecutionRejected

    @property
    def succeeded(self) -> bool:
        return self.state is DouyinSearchExecutionState.SUCCEEDED

    @property
    def circuit_open(self) -> bool:
        return not self.succeeded

    def __repr__(self) -> str:
        return (
            "DouyinSearchExecutionObservation("
            f"state={self.state.value!r}, evidence={self.evidence.value!r}, "
            f"execution_version={self.execution_version!r}, "
            f"circuit_open={self.circuit_open!r})"
        )


class _ActionLocator(Protocol):
    def fill(self, value: str, *, timeout: float) -> None: ...

    def click(self, *, timeout: float, no_wait_after: bool) -> None: ...


class _SearchPage(Protocol):
    def goto(self, url: str, *, wait_until: str, timeout: float) -> object: ...

    def wait_for_url(self, url: str, *, wait_until: str, timeout: float) -> None: ...


class DouyinSearchExecution:
    """Execute exactly one search without retries or unrelated page actions."""

    def __init__(self, window: BrowserWindow, search: DouyinSearchInput) -> None:
        if not isinstance(window, BrowserWindow) or not isinstance(search, DouyinSearchInput):
            raise DouyinSearchExecutionRejected
        self._page = cast(_SearchPage, window.playwright_page)
        self._search_page = DouyinSearchPage(window)
        self._search = search
        self._executed = False

    def __repr__(self) -> str:
        return "DouyinSearchExecution(<redacted>)"

    def run(self) -> DouyinSearchExecutionObservation:
        if self._executed:
            raise DouyinSearchExecutionRejected
        self._executed = True
        try:
            self._page.goto(
                DOUYIN_HOME_URL,
                wait_until="domcontentloaded",
                timeout=_NAVIGATION_TIMEOUT_MILLISECONDS,
            )
        except PlaywrightTimeoutError:
            return _result(
                DouyinSearchExecutionState.TIMED_OUT,
                DouyinSearchExecutionEvidence.NAVIGATION_TIMED_OUT,
            )
        except Exception:
            return _unavailable()

        home = self._search_page.wait_for_home_ready(
            timeout_milliseconds=_PAGE_READY_TIMEOUT_MILLISECONDS
        )
        if home.state is not DouyinSearchPageState.HOME_READY:
            return _from_page_observation(
                home,
                timeout_evidence=DouyinSearchExecutionEvidence.HOME_READY_TIMED_OUT,
            )

        try:
            search_input = cast(_ActionLocator, self._search_page.search_input())
            search_submit = cast(_ActionLocator, self._search_page.search_submit())
            search_input.fill(self._search.keyword, timeout=_ACTION_TIMEOUT_MILLISECONDS)
            search_submit.click(
                timeout=_ACTION_TIMEOUT_MILLISECONDS,
                no_wait_after=True,
            )
        except PlaywrightTimeoutError:
            return _result(
                DouyinSearchExecutionState.TIMED_OUT,
                DouyinSearchExecutionEvidence.ACTION_TIMED_OUT,
            )
        except Exception:
            return _unavailable()

        expected_url = douyin_search_results_url(self._search.keyword)
        try:
            self._page.wait_for_url(
                expected_url,
                wait_until="domcontentloaded",
                timeout=_RESULT_URL_TIMEOUT_MILLISECONDS,
            )
        except PlaywrightTimeoutError:
            return _result(
                DouyinSearchExecutionState.TIMED_OUT,
                DouyinSearchExecutionEvidence.RESULT_URL_TIMED_OUT,
            )
        except Exception:
            return _unavailable()

        results = self._search_page.wait_for_results_ready(
            timeout_milliseconds=_PAGE_READY_TIMEOUT_MILLISECONDS
        )
        if results.state is not DouyinSearchPageState.RESULTS_READY:
            return _from_page_observation(
                results,
                timeout_evidence=DouyinSearchExecutionEvidence.RESULTS_READY_TIMED_OUT,
            )
        try:
            self._search_page.result_list()
        except Exception:
            return _unavailable()
        return _result(
            DouyinSearchExecutionState.SUCCEEDED,
            DouyinSearchExecutionEvidence.RESULTS_READY,
        )


def _from_page_observation(
    observation: DouyinSearchPageObservation,
    *,
    timeout_evidence: DouyinSearchExecutionEvidence,
) -> DouyinSearchExecutionObservation:
    if observation.state is DouyinSearchPageState.LOGIN_REQUIRED:
        return _result(
            DouyinSearchExecutionState.LOGIN_REQUIRED,
            DouyinSearchExecutionEvidence.LOGIN_REQUIRED,
        )
    if observation.state is DouyinSearchPageState.DIALOG_BLOCKED:
        return _result(
            DouyinSearchExecutionState.DIALOG_BLOCKED,
            DouyinSearchExecutionEvidence.BLOCKING_DIALOG,
        )
    evidence = {
        DouyinSearchPageEvidence.REQUIRED_ANCHOR_MISSING: timeout_evidence,
        DouyinSearchPageEvidence.PAGE_VERSION_UNKNOWN: (
            DouyinSearchExecutionEvidence.PAGE_VERSION_UNKNOWN
        ),
        DouyinSearchPageEvidence.CONFLICTING_ANCHORS: (
            DouyinSearchExecutionEvidence.CONFLICTING_ANCHORS
        ),
        DouyinSearchPageEvidence.PAGE_UNAVAILABLE: DouyinSearchExecutionEvidence.PAGE_UNAVAILABLE,
    }.get(observation.evidence, DouyinSearchExecutionEvidence.PAGE_UNAVAILABLE)
    state = (
        DouyinSearchExecutionState.TIMED_OUT
        if evidence is timeout_evidence
        else DouyinSearchExecutionState.UNKNOWN
    )
    return _result(state, evidence)


def _unavailable() -> DouyinSearchExecutionObservation:
    return _result(
        DouyinSearchExecutionState.UNKNOWN,
        DouyinSearchExecutionEvidence.PAGE_UNAVAILABLE,
    )


def _result(
    state: DouyinSearchExecutionState,
    evidence: DouyinSearchExecutionEvidence,
) -> DouyinSearchExecutionObservation:
    return DouyinSearchExecutionObservation(state=state, evidence=evidence)


__all__ = [
    "DOUYIN_SEARCH_EXECUTION_VERSION",
    "DouyinSearchExecution",
    "DouyinSearchExecutionEvidence",
    "DouyinSearchExecutionObservation",
    "DouyinSearchExecutionRejected",
    "DouyinSearchExecutionState",
]
