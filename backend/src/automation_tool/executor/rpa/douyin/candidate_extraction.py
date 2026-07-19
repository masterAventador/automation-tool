"""Privacy-preserving Candidate extraction from one versioned Douyin result page."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from automation_tool.executor.browser_runtime import BrowserWindow
from automation_tool.executor.rpa.douyin.search_page import (
    DouyinSearchPage,
    DouyinSearchPageEvidence,
    DouyinSearchPagePrivacyRejected,
    DouyinSearchPageState,
)
from automation_tool.protocol import (
    MAX_TASK_TARGET_LIMIT,
    DouyinCandidate,
)
from automation_tool.protocol.limits import MAX_CROSS_RUNTIME_SEQUENCE

DOUYIN_CANDIDATE_EXTRACTION_VERSION = "douyin.candidate-extraction.v1"


class DouyinCandidateExtractionRejected(RuntimeError):
    """Candidate extraction cannot run through the bounded privacy boundary."""

    def __init__(self) -> None:
        super().__init__("douyin candidate extraction is unavailable")


class DouyinCandidateExtractionState(StrEnum):
    COMPLETED = "completed"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class DouyinCandidateExtractionEvidence(StrEnum):
    CANDIDATES_EXTRACTED = "candidates_extracted"
    LOGIN_REQUIRED = "login_required"
    BLOCKING_DIALOG = "blocking_dialog"
    RESULTS_UNAVAILABLE = "results_unavailable"
    PRIVACY_REJECTED = "privacy_rejected"
    PAGE_UNAVAILABLE = "page_unavailable"


_BLOCKED_EVIDENCE = frozenset(
    {
        DouyinCandidateExtractionEvidence.LOGIN_REQUIRED,
        DouyinCandidateExtractionEvidence.BLOCKING_DIALOG,
    }
)
_UNKNOWN_EVIDENCE = frozenset(
    {
        DouyinCandidateExtractionEvidence.RESULTS_UNAVAILABLE,
        DouyinCandidateExtractionEvidence.PRIVACY_REJECTED,
        DouyinCandidateExtractionEvidence.PAGE_UNAVAILABLE,
    }
)


@dataclass(frozen=True, slots=True, repr=False)
class DouyinCandidateExtractionObservation:
    state: DouyinCandidateExtractionState
    evidence: DouyinCandidateExtractionEvidence
    candidates: tuple[DouyinCandidate, ...]
    requested_limit: int
    page_revision: int
    extraction_version: str = DOUYIN_CANDIDATE_EXTRACTION_VERSION

    def __post_init__(self) -> None:
        if (
            not isinstance(self.state, DouyinCandidateExtractionState)
            or not isinstance(self.evidence, DouyinCandidateExtractionEvidence)
            or type(self.candidates) is not tuple
            or type(self.requested_limit) is not int
            or not 1 <= self.requested_limit <= MAX_TASK_TARGET_LIMIT
            or type(self.page_revision) is not int
            or not 1 <= self.page_revision <= MAX_CROSS_RUNTIME_SEQUENCE
            or self.extraction_version != DOUYIN_CANDIDATE_EXTRACTION_VERSION
            or len(self.candidates) > self.requested_limit
            or any(
                not isinstance(candidate, DouyinCandidate)
                or candidate.page_revision != self.page_revision
                for candidate in self.candidates
            )
            or not self._state_matches_payload()
        ):
            raise DouyinCandidateExtractionRejected

    def _state_matches_payload(self) -> bool:
        if self.state is DouyinCandidateExtractionState.COMPLETED:
            return self.evidence is DouyinCandidateExtractionEvidence.CANDIDATES_EXTRACTED
        if self.candidates:
            return False
        return (
            self.state is DouyinCandidateExtractionState.BLOCKED
            and self.evidence in _BLOCKED_EVIDENCE
        ) or (
            self.state is DouyinCandidateExtractionState.UNKNOWN
            and self.evidence in _UNKNOWN_EVIDENCE
        )

    @property
    def candidate_count(self) -> int:
        return len(self.candidates)

    @property
    def completed(self) -> bool:
        return self.state is DouyinCandidateExtractionState.COMPLETED

    @property
    def circuit_open(self) -> bool:
        return not self.completed

    def __repr__(self) -> str:
        return (
            "DouyinCandidateExtractionObservation("
            f"state={self.state.value!r}, evidence={self.evidence.value!r}, "
            f"candidate_count={self.candidate_count!r}, "
            f"requested_limit={self.requested_limit!r}, "
            f"page_revision={self.page_revision!r}, "
            f"extraction_version={self.extraction_version!r}, "
            f"circuit_open={self.circuit_open!r})"
        )


class DouyinCandidateExtraction:
    """Return only validated Candidates; raw DOM facts never leave the Page Object."""

    def __init__(
        self,
        window: BrowserWindow,
        *,
        maximum: int,
        page_revision: int,
    ) -> None:
        if (
            not isinstance(window, BrowserWindow)
            or type(maximum) is not int
            or not 1 <= maximum <= MAX_TASK_TARGET_LIMIT
            or type(page_revision) is not int
            or not 1 <= page_revision <= MAX_CROSS_RUNTIME_SEQUENCE
        ):
            raise DouyinCandidateExtractionRejected
        self._search_page = DouyinSearchPage(window)
        self._maximum = maximum
        self._page_revision = page_revision
        self._executed = False

    def __repr__(self) -> str:
        return "DouyinCandidateExtraction(<redacted>)"

    def run(self) -> DouyinCandidateExtractionObservation:
        if self._executed:
            raise DouyinCandidateExtractionRejected
        self._executed = True
        try:
            page = self._search_page.observe()
        except Exception:
            return self._unknown(DouyinCandidateExtractionEvidence.PAGE_UNAVAILABLE)
        if page.state is not DouyinSearchPageState.RESULTS_READY:
            return self._page_result(page.state, page.evidence)
        try:
            candidates = self._search_page.candidate_items(
                maximum=self._maximum,
                page_revision=self._page_revision,
            )
        except DouyinSearchPagePrivacyRejected:
            return self._unknown(DouyinCandidateExtractionEvidence.PRIVACY_REJECTED)
        except Exception:
            return self._unknown(DouyinCandidateExtractionEvidence.PAGE_UNAVAILABLE)
        return self._result(
            DouyinCandidateExtractionState.COMPLETED,
            DouyinCandidateExtractionEvidence.CANDIDATES_EXTRACTED,
            candidates,
        )

    def _page_result(
        self,
        state: DouyinSearchPageState,
        evidence: DouyinSearchPageEvidence,
    ) -> DouyinCandidateExtractionObservation:
        if state is DouyinSearchPageState.LOGIN_REQUIRED:
            return self._result(
                DouyinCandidateExtractionState.BLOCKED,
                DouyinCandidateExtractionEvidence.LOGIN_REQUIRED,
            )
        if state is DouyinSearchPageState.DIALOG_BLOCKED:
            return self._result(
                DouyinCandidateExtractionState.BLOCKED,
                DouyinCandidateExtractionEvidence.BLOCKING_DIALOG,
            )
        mapped = (
            DouyinCandidateExtractionEvidence.PAGE_UNAVAILABLE
            if evidence is DouyinSearchPageEvidence.PAGE_UNAVAILABLE
            else DouyinCandidateExtractionEvidence.RESULTS_UNAVAILABLE
        )
        return self._unknown(mapped)

    def _unknown(
        self,
        evidence: DouyinCandidateExtractionEvidence,
    ) -> DouyinCandidateExtractionObservation:
        return self._result(DouyinCandidateExtractionState.UNKNOWN, evidence)

    def _result(
        self,
        state: DouyinCandidateExtractionState,
        evidence: DouyinCandidateExtractionEvidence,
        candidates: tuple[DouyinCandidate, ...] = (),
    ) -> DouyinCandidateExtractionObservation:
        return DouyinCandidateExtractionObservation(
            state=state,
            evidence=evidence,
            candidates=candidates,
            requested_limit=self._maximum,
            page_revision=self._page_revision,
        )


__all__ = [
    "DOUYIN_CANDIDATE_EXTRACTION_VERSION",
    "DouyinCandidateExtraction",
    "DouyinCandidateExtractionEvidence",
    "DouyinCandidateExtractionObservation",
    "DouyinCandidateExtractionRejected",
    "DouyinCandidateExtractionState",
]
