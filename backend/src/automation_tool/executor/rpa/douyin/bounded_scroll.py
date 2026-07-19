"""Bounded, cancellable result scrolling after one confirmed Douyin search."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from time import monotonic, sleep
from typing import Protocol, cast

from automation_tool.executor.browser_runtime import BrowserWindow
from automation_tool.executor.rpa.douyin.search import (
    DouyinSearchExecutionObservation,
)
from automation_tool.executor.rpa.douyin.search_page import (
    DouyinSearchPage,
    DouyinSearchPageEvidence,
    DouyinSearchPageObservation,
    DouyinSearchPageState,
)
from automation_tool.protocol import MAX_TASK_TARGET_LIMIT, DouyinSearchInput

DOUYIN_BOUNDED_SCROLL_VERSION = "douyin.bounded-scroll.v1"
MAX_SCROLL_ROUNDS = 20
_SCROLL_DELTA_Y = 800.0
_RESULT_SETTLE_TIMEOUT_SECONDS = 3.0
_RESULT_POLL_INTERVAL_SECONDS = 0.1


class DouyinBoundedScrollRejected(RuntimeError):
    """The bounded result-scroll operation cannot run safely."""

    def __init__(self) -> None:
        super().__init__("douyin bounded scroll is unavailable")


class DouyinBoundedScrollState(StrEnum):
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class DouyinBoundedScrollEvidence(StrEnum):
    TARGET_LIMIT_REACHED = "target_limit_reached"
    ROUND_LIMIT_REACHED = "round_limit_reached"
    NO_NEW_RESULTS = "no_new_results"
    CANCELLATION_REQUESTED = "cancellation_requested"
    CANCELLATION_UNAVAILABLE = "cancellation_unavailable"
    LOGIN_REQUIRED = "login_required"
    BLOCKING_DIALOG = "blocking_dialog"
    RESULTS_UNAVAILABLE = "results_unavailable"
    RESULT_COUNT_DECREASED = "result_count_decreased"
    PAGE_UNAVAILABLE = "page_unavailable"


_COMPLETED_EVIDENCE = frozenset(
    {
        DouyinBoundedScrollEvidence.TARGET_LIMIT_REACHED,
        DouyinBoundedScrollEvidence.ROUND_LIMIT_REACHED,
        DouyinBoundedScrollEvidence.NO_NEW_RESULTS,
    }
)
_BLOCKED_EVIDENCE = frozenset(
    {
        DouyinBoundedScrollEvidence.LOGIN_REQUIRED,
        DouyinBoundedScrollEvidence.BLOCKING_DIALOG,
    }
)
_UNKNOWN_EVIDENCE = frozenset(
    {
        DouyinBoundedScrollEvidence.CANCELLATION_UNAVAILABLE,
        DouyinBoundedScrollEvidence.RESULTS_UNAVAILABLE,
        DouyinBoundedScrollEvidence.RESULT_COUNT_DECREASED,
        DouyinBoundedScrollEvidence.PAGE_UNAVAILABLE,
    }
)


@dataclass(frozen=True, slots=True, repr=False)
class DouyinBoundedScrollObservation:
    state: DouyinBoundedScrollState
    evidence: DouyinBoundedScrollEvidence
    rounds_completed: int
    target_count: int
    target_limit: int
    scroll_version: str = DOUYIN_BOUNDED_SCROLL_VERSION

    def __post_init__(self) -> None:
        if (
            not isinstance(self.state, DouyinBoundedScrollState)
            or not isinstance(self.evidence, DouyinBoundedScrollEvidence)
            or type(self.rounds_completed) is not int
            or not 0 <= self.rounds_completed <= MAX_SCROLL_ROUNDS
            or type(self.target_count) is not int
            or type(self.target_limit) is not int
            or not 1 <= self.target_limit <= MAX_TASK_TARGET_LIMIT
            or not 0 <= self.target_count <= self.target_limit
            or self.scroll_version != DOUYIN_BOUNDED_SCROLL_VERSION
            or not self._state_matches_evidence()
            or not self._completion_bounds_match()
        ):
            raise DouyinBoundedScrollRejected

    def _state_matches_evidence(self) -> bool:
        return (
            (
                self.state is DouyinBoundedScrollState.COMPLETED
                and self.evidence in _COMPLETED_EVIDENCE
            )
            or (
                self.state is DouyinBoundedScrollState.CANCELLED
                and self.evidence is DouyinBoundedScrollEvidence.CANCELLATION_REQUESTED
            )
            or (
                self.state is DouyinBoundedScrollState.BLOCKED
                and self.evidence in _BLOCKED_EVIDENCE
            )
            or (
                self.state is DouyinBoundedScrollState.UNKNOWN
                and self.evidence in _UNKNOWN_EVIDENCE
            )
        )

    def _completion_bounds_match(self) -> bool:
        if self.evidence is DouyinBoundedScrollEvidence.TARGET_LIMIT_REACHED:
            return self.target_count == self.target_limit
        if self.evidence is DouyinBoundedScrollEvidence.ROUND_LIMIT_REACHED:
            return (
                self.rounds_completed == MAX_SCROLL_ROUNDS and self.target_count < self.target_limit
            )
        if self.evidence is DouyinBoundedScrollEvidence.NO_NEW_RESULTS:
            return self.rounds_completed >= 1 and self.target_count < self.target_limit
        return True

    @property
    def completed(self) -> bool:
        return self.state is DouyinBoundedScrollState.COMPLETED

    @property
    def circuit_open(self) -> bool:
        return not self.completed

    def __repr__(self) -> str:
        return (
            "DouyinBoundedScrollObservation("
            f"state={self.state.value!r}, evidence={self.evidence.value!r}, "
            f"rounds_completed={self.rounds_completed!r}, "
            f"target_count={self.target_count!r}, target_limit={self.target_limit!r}, "
            f"scroll_version={self.scroll_version!r}, circuit_open={self.circuit_open!r})"
        )


class _Mouse(Protocol):
    def wheel(self, delta_x: float, delta_y: float) -> None: ...


class _ScrollPage(Protocol):
    @property
    def mouse(self) -> _Mouse: ...


class DouyinBoundedScroll:
    """Scroll at most twenty times and stop on cancellation or no growth."""

    def __init__(
        self,
        window: BrowserWindow,
        search: DouyinSearchInput,
        search_execution: DouyinSearchExecutionObservation,
        cancellation_requested: Callable[[], bool],
    ) -> None:
        if (
            not isinstance(window, BrowserWindow)
            or not isinstance(search, DouyinSearchInput)
            or not isinstance(search_execution, DouyinSearchExecutionObservation)
            or not search_execution.succeeded
            or not callable(cancellation_requested)
        ):
            raise DouyinBoundedScrollRejected
        self._page = cast(_ScrollPage, window.playwright_page)
        self._search_page = DouyinSearchPage(window)
        self._search = search
        self._cancellation_requested = cancellation_requested
        self._executed = False

    def __repr__(self) -> str:
        return "DouyinBoundedScroll(<redacted>)"

    def run(self) -> DouyinBoundedScrollObservation:
        if self._executed:
            raise DouyinBoundedScrollRejected
        self._executed = True
        cancellation = self._check_cancellation()
        if cancellation is not False:
            return self._cancellation_result(cancellation, rounds=0, count=0)
        page = self._observe_page()
        if page is None:
            return self._unknown(DouyinBoundedScrollEvidence.PAGE_UNAVAILABLE, 0, 0)
        if page.state is not DouyinSearchPageState.RESULTS_READY:
            return self._page_result(page, rounds=0, count=0)
        count = self._count_results()
        if count is None:
            return self._unknown(DouyinBoundedScrollEvidence.PAGE_UNAVAILABLE, 0, 0)
        if count >= self._search.target_limit:
            return self._completed(DouyinBoundedScrollEvidence.TARGET_LIMIT_REACHED, 0, count)

        for round_index in range(MAX_SCROLL_ROUNDS):
            cancellation = self._check_cancellation()
            if cancellation is not False:
                return self._cancellation_result(
                    cancellation,
                    rounds=round_index,
                    count=count,
                )
            try:
                self._page.mouse.wheel(0.0, _SCROLL_DELTA_Y)
            except Exception:
                return self._unknown(
                    DouyinBoundedScrollEvidence.PAGE_UNAVAILABLE,
                    round_index,
                    count,
                )
            rounds = round_index + 1
            deadline = monotonic() + _RESULT_SETTLE_TIMEOUT_SECONDS
            while True:
                cancellation = self._check_cancellation()
                if cancellation is not False:
                    return self._cancellation_result(cancellation, rounds=rounds, count=count)
                page = self._observe_page()
                if page is None:
                    return self._unknown(
                        DouyinBoundedScrollEvidence.PAGE_UNAVAILABLE,
                        rounds,
                        count,
                    )
                if page.state is not DouyinSearchPageState.RESULTS_READY:
                    return self._page_result(page, rounds=rounds, count=count)
                current = self._count_results()
                if current is None:
                    return self._unknown(
                        DouyinBoundedScrollEvidence.PAGE_UNAVAILABLE,
                        rounds,
                        count,
                    )
                if current < count:
                    return self._unknown(
                        DouyinBoundedScrollEvidence.RESULT_COUNT_DECREASED,
                        rounds,
                        current,
                    )
                if current > count:
                    count = current
                    break
                remaining = deadline - monotonic()
                if remaining <= 0:
                    return self._completed(
                        DouyinBoundedScrollEvidence.NO_NEW_RESULTS,
                        rounds,
                        count,
                    )
                sleep(min(_RESULT_POLL_INTERVAL_SECONDS, remaining))
            cancellation = self._check_cancellation()
            if cancellation is not False:
                return self._cancellation_result(cancellation, rounds=rounds, count=count)
            if count >= self._search.target_limit:
                return self._completed(
                    DouyinBoundedScrollEvidence.TARGET_LIMIT_REACHED,
                    rounds,
                    count,
                )
        return self._completed(
            DouyinBoundedScrollEvidence.ROUND_LIMIT_REACHED,
            MAX_SCROLL_ROUNDS,
            count,
        )

    def _observe_page(self) -> DouyinSearchPageObservation | None:
        try:
            return self._search_page.observe()
        except Exception:
            return None

    def _count_results(self) -> int | None:
        try:
            return self._search_page.result_item_count(maximum=self._search.target_limit)
        except Exception:
            return None

    def _check_cancellation(self) -> bool | None:
        try:
            result = self._cancellation_requested()
        except Exception:
            return None
        return result if type(result) is bool else None

    def _cancellation_result(
        self,
        cancellation: bool | None,
        *,
        rounds: int,
        count: int,
    ) -> DouyinBoundedScrollObservation:
        if cancellation is True:
            return self._result(
                DouyinBoundedScrollState.CANCELLED,
                DouyinBoundedScrollEvidence.CANCELLATION_REQUESTED,
                rounds,
                count,
            )
        return self._unknown(
            DouyinBoundedScrollEvidence.CANCELLATION_UNAVAILABLE,
            rounds,
            count,
        )

    def _page_result(
        self,
        page: DouyinSearchPageObservation,
        *,
        rounds: int,
        count: int,
    ) -> DouyinBoundedScrollObservation:
        if page.state is DouyinSearchPageState.LOGIN_REQUIRED:
            return self._result(
                DouyinBoundedScrollState.BLOCKED,
                DouyinBoundedScrollEvidence.LOGIN_REQUIRED,
                rounds,
                count,
            )
        if page.state is DouyinSearchPageState.DIALOG_BLOCKED:
            return self._result(
                DouyinBoundedScrollState.BLOCKED,
                DouyinBoundedScrollEvidence.BLOCKING_DIALOG,
                rounds,
                count,
            )
        evidence = (
            DouyinBoundedScrollEvidence.PAGE_UNAVAILABLE
            if page.evidence is DouyinSearchPageEvidence.PAGE_UNAVAILABLE
            else DouyinBoundedScrollEvidence.RESULTS_UNAVAILABLE
        )
        return self._unknown(evidence, rounds, count)

    def _completed(
        self,
        evidence: DouyinBoundedScrollEvidence,
        rounds: int,
        count: int,
    ) -> DouyinBoundedScrollObservation:
        return self._result(DouyinBoundedScrollState.COMPLETED, evidence, rounds, count)

    def _unknown(
        self,
        evidence: DouyinBoundedScrollEvidence,
        rounds: int,
        count: int,
    ) -> DouyinBoundedScrollObservation:
        return self._result(DouyinBoundedScrollState.UNKNOWN, evidence, rounds, count)

    def _result(
        self,
        state: DouyinBoundedScrollState,
        evidence: DouyinBoundedScrollEvidence,
        rounds: int,
        count: int,
    ) -> DouyinBoundedScrollObservation:
        return DouyinBoundedScrollObservation(
            state=state,
            evidence=evidence,
            rounds_completed=rounds,
            target_count=count,
            target_limit=self._search.target_limit,
        )


__all__ = [
    "DOUYIN_BOUNDED_SCROLL_VERSION",
    "MAX_SCROLL_ROUNDS",
    "DouyinBoundedScroll",
    "DouyinBoundedScrollEvidence",
    "DouyinBoundedScrollObservation",
    "DouyinBoundedScrollRejected",
    "DouyinBoundedScrollState",
]
