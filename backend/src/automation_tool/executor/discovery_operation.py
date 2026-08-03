"""Closed result contract for one read-only Douyin discovery execution."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from automation_tool.executor.browser_authority import BrowserLaunchAuthority
from automation_tool.executor.browser_diagnostic_artifact import (
    BrowserDiagnosticArtifactRejected,
    BrowserDiagnosticArtifactStore,
    BrowserDiagnosticCapturePolicy,
    BrowserDiagnosticStage,
)
from automation_tool.executor.browser_runtime import (
    BrowserLaunchRequest,
    BrowserRuntime,
    BrowserWindow,
)
from automation_tool.executor.ledger import ExecutorLedger
from automation_tool.executor.page_drift_artifact import (
    PageDriftArtifactRejected,
    PageDriftArtifactStore,
)
from automation_tool.executor.rpa.douyin.bounded_scroll import (
    DouyinBoundedScroll,
    DouyinBoundedScrollEvidence,
    DouyinBoundedScrollObservation,
    DouyinBoundedScrollState,
)
from automation_tool.executor.rpa.douyin.candidate_extraction import (
    DouyinCandidateExtraction,
    DouyinCandidateExtractionEvidence,
    DouyinCandidateExtractionObservation,
    DouyinCandidateExtractionState,
)
from automation_tool.executor.rpa.douyin.search import (
    DouyinSearchExecution,
    DouyinSearchExecutionObservation,
    DouyinSearchExecutionState,
)
from automation_tool.protocol import (
    MAX_TASK_TARGET_LIMIT,
    DouyinCandidate,
    DouyinDiscoveryCommandPayload,
    PlatformSessionState,
)


class DouyinDiscoveryOperationRejected(RuntimeError):
    def __init__(self) -> None:
        super().__init__("douyin discovery operation is unavailable")


class DouyinDiscoveryOperationState(StrEnum):
    COMPLETED = "completed"
    LOGIN_REQUIRED = "login_required"
    HANDOFF_REQUIRED = "handoff_required"
    FAILED = "failed"


_EVIDENCE_BY_STATE = {
    DouyinDiscoveryOperationState.COMPLETED: frozenset({"candidates_extracted"}),
    DouyinDiscoveryOperationState.LOGIN_REQUIRED: frozenset({"login_required"}),
    DouyinDiscoveryOperationState.HANDOFF_REQUIRED: frozenset(
        {"blocking_dialog", "page_version_unknown", "conflicting_anchors"}
    ),
    DouyinDiscoveryOperationState.FAILED: frozenset(
        {
            "no_candidates",
            "navigation_timed_out",
            "home_ready_timed_out",
            "action_timed_out",
            "result_url_timed_out",
            "results_ready_timed_out",
            "results_unavailable",
            "privacy_rejected",
            "result_count_decreased",
            "cancellation_unavailable",
            "cancellation_requested",
            "page_unavailable",
        }
    ),
}


@dataclass(frozen=True, slots=True, repr=False)
class DouyinDiscoveryExecutionResult:
    state: DouyinDiscoveryOperationState
    evidence: str
    page_revision: int
    candidates: tuple[DouyinCandidate, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.state, DouyinDiscoveryOperationState)
            or type(self.evidence) is not str
            or self.evidence not in _EVIDENCE_BY_STATE[self.state]
            or type(self.page_revision) is not int
            or self.page_revision <= 0
            or type(self.candidates) is not tuple
            or len(self.candidates) > MAX_TASK_TARGET_LIMIT
            or any(
                not isinstance(candidate, DouyinCandidate)
                or candidate.page_revision != self.page_revision
                for candidate in self.candidates
            )
            or (self.state is DouyinDiscoveryOperationState.COMPLETED and not self.candidates)
            or (self.state is not DouyinDiscoveryOperationState.COMPLETED and bool(self.candidates))
        ):
            raise DouyinDiscoveryOperationRejected

    def __repr__(self) -> str:
        return (
            "DouyinDiscoveryExecutionResult("
            f"state={self.state.value!r}, evidence={self.evidence!r}, "
            f"page_revision={self.page_revision!r}, candidate_count={len(self.candidates)!r})"
        )


@runtime_checkable
class DouyinDiscoveryOperation(Protocol):
    def run(
        self,
        payload: DouyinDiscoveryCommandPayload,
        *,
        cancellation_requested: Callable[[], bool],
    ) -> DouyinDiscoveryExecutionResult: ...


class _Runtime(Protocol):
    def start(self, request: BrowserLaunchRequest) -> None: ...

    def primary_window(self) -> BrowserWindow: ...

    def close(self) -> None: ...


class _Search(Protocol):
    def run(self) -> DouyinSearchExecutionObservation: ...


class _Scroll(Protocol):
    def run(self) -> DouyinBoundedScrollObservation: ...


class _Extraction(Protocol):
    def run(self) -> DouyinCandidateExtractionObservation: ...


def _default_extraction(
    window: BrowserWindow,
    maximum: int,
    page_revision: int,
) -> DouyinCandidateExtraction:
    return DouyinCandidateExtraction(
        window,
        maximum=maximum,
        page_revision=page_revision,
    )


class ProductionDouyinDiscoveryOperation:
    """Run the existing search, bounded-scroll, and privacy extraction adapters once."""

    def __init__(
        self,
        *,
        ledger: ExecutorLedger,
        browser_authority: BrowserLaunchAuthority,
        runtime_factory: Callable[[], _Runtime] = BrowserRuntime,
        search_factory: Callable[[BrowserWindow, object], _Search] = DouyinSearchExecution,
        scroll_factory: Callable[
            [BrowserWindow, object, DouyinSearchExecutionObservation, Callable[[], bool]],
            _Scroll,
        ] = DouyinBoundedScroll,
        extraction_factory: Callable[[BrowserWindow, int, int], _Extraction] = _default_extraction,
        page_drift_artifacts: PageDriftArtifactStore | None = None,
        browser_diagnostic_artifacts: BrowserDiagnosticArtifactStore | None = None,
        capture_successful_diagnostics: bool = False,
    ) -> None:
        if (
            not isinstance(ledger, ExecutorLedger)
            or not isinstance(browser_authority, BrowserLaunchAuthority)
            or not callable(runtime_factory)
            or not callable(search_factory)
            or not callable(scroll_factory)
            or not callable(extraction_factory)
            or type(capture_successful_diagnostics) is not bool
        ):
            raise DouyinDiscoveryOperationRejected
        resolved_artifacts = (
            PageDriftArtifactStore(state_directory=ledger.database_path.parent)
            if page_drift_artifacts is None
            else page_drift_artifacts
        )
        if not isinstance(resolved_artifacts, PageDriftArtifactStore):
            raise DouyinDiscoveryOperationRejected
        resolved_diagnostics = browser_diagnostic_artifacts
        if resolved_diagnostics is None:
            with suppress(BrowserDiagnosticArtifactRejected):
                resolved_diagnostics = BrowserDiagnosticArtifactStore(
                    state_directory=ledger.database_path.parent
                )
        elif not isinstance(resolved_diagnostics, BrowserDiagnosticArtifactStore):
            raise DouyinDiscoveryOperationRejected
        self._ledger = ledger
        self._browser_authority = browser_authority
        self._runtime_factory = runtime_factory
        self._search_factory = search_factory
        self._scroll_factory = scroll_factory
        self._extraction_factory = extraction_factory
        self._page_drift_artifacts = resolved_artifacts
        self._browser_diagnostic_artifacts = resolved_diagnostics
        self._browser_diagnostic_policy = BrowserDiagnosticCapturePolicy(
            capture_successful_runs=capture_successful_diagnostics
        )

    def run(
        self,
        payload: DouyinDiscoveryCommandPayload,
        *,
        cancellation_requested: Callable[[], bool],
    ) -> DouyinDiscoveryExecutionResult:
        if not isinstance(payload, DouyinDiscoveryCommandPayload) or not callable(
            cancellation_requested
        ):
            raise DouyinDiscoveryOperationRejected
        try:
            session = self._ledger.get_platform_session("douyin")
        except Exception:
            return _failed(payload.page_revision, "page_unavailable")
        if session is None or session.state is not PlatformSessionState.HEALTHY:
            return _result(
                DouyinDiscoveryOperationState.LOGIN_REQUIRED,
                "login_required",
                payload.page_revision,
            )

        result: DouyinDiscoveryExecutionResult | None = None
        try:
            with self._browser_authority.lease() as request:
                runtime = self._runtime_factory()
                window: BrowserWindow | None = None
                stage = BrowserDiagnosticStage.SEARCH
                try:
                    runtime.start(request)
                    window = runtime.primary_window()
                    search_input = payload.to_search_input()
                    search = self._search_factory(window, search_input).run()
                    result = _from_search(search, payload.page_revision)
                    if result is not None and result.evidence in {
                        "page_version_unknown",
                        "conflicting_anchors",
                    }:
                        self._capture_page_drift(result)
                    if result is None:
                        stage = BrowserDiagnosticStage.SCROLL
                        scroll = self._scroll_factory(
                            window,
                            search_input,
                            search,
                            cancellation_requested,
                        ).run()
                        result = _from_scroll(scroll, payload.page_revision)
                    if result is None:
                        stage = BrowserDiagnosticStage.EXTRACTION
                        extraction = self._extraction_factory(
                            window,
                            payload.target_limit,
                            payload.page_revision,
                        ).run()
                        result = _from_extraction(extraction, payload.page_revision)
                except Exception:
                    result = _failed(payload.page_revision, "page_unavailable")
                finally:
                    if result is not None and window is not None:
                        self._capture_browser_diagnostics(window, result, stage)
                    runtime.close()
        except Exception:
            result = _failed(payload.page_revision, "page_unavailable")
        return result or _failed(payload.page_revision, "page_unavailable")

    def _capture_page_drift(self, result: DouyinDiscoveryExecutionResult) -> None:
        with suppress(PageDriftArtifactRejected):
            self._page_drift_artifacts.capture(
                evidence=result.evidence,
                page_revision=result.page_revision,
                stage="search",
            )

    def _capture_browser_diagnostics(
        self,
        window: BrowserWindow,
        result: DouyinDiscoveryExecutionResult,
        stage: BrowserDiagnosticStage,
    ) -> None:
        trigger = self._browser_diagnostic_policy.trigger(
            failed=result.state is not DouyinDiscoveryOperationState.COMPLETED
        )
        if trigger is None or self._browser_diagnostic_artifacts is None:
            return
        with suppress(BrowserDiagnosticArtifactRejected):
            self._browser_diagnostic_artifacts.capture(
                window=window,
                trigger=trigger,
                stage=stage,
                page_revision=result.page_revision,
            )


def _from_search(
    observation: DouyinSearchExecutionObservation,
    page_revision: int,
) -> DouyinDiscoveryExecutionResult | None:
    if not isinstance(observation, DouyinSearchExecutionObservation):
        return _failed(page_revision, "page_unavailable")
    if observation.state is DouyinSearchExecutionState.SUCCEEDED:
        return None
    if observation.state is DouyinSearchExecutionState.LOGIN_REQUIRED:
        return _result(
            DouyinDiscoveryOperationState.LOGIN_REQUIRED,
            "login_required",
            page_revision,
        )
    if observation.state is DouyinSearchExecutionState.DIALOG_BLOCKED:
        return _result(
            DouyinDiscoveryOperationState.HANDOFF_REQUIRED,
            "blocking_dialog",
            page_revision,
        )
    evidence = observation.evidence.value
    if evidence in {"page_version_unknown", "conflicting_anchors"}:
        return _result(
            DouyinDiscoveryOperationState.HANDOFF_REQUIRED,
            evidence,
            page_revision,
        )
    return _failed(page_revision, evidence)


def _from_scroll(
    observation: DouyinBoundedScrollObservation,
    page_revision: int,
) -> DouyinDiscoveryExecutionResult | None:
    if not isinstance(observation, DouyinBoundedScrollObservation):
        return _failed(page_revision, "page_unavailable")
    if observation.state is DouyinBoundedScrollState.COMPLETED:
        return None
    if observation.evidence is DouyinBoundedScrollEvidence.LOGIN_REQUIRED:
        return _result(
            DouyinDiscoveryOperationState.LOGIN_REQUIRED,
            "login_required",
            page_revision,
        )
    if observation.evidence is DouyinBoundedScrollEvidence.BLOCKING_DIALOG:
        return _result(
            DouyinDiscoveryOperationState.HANDOFF_REQUIRED,
            "blocking_dialog",
            page_revision,
        )
    return _failed(page_revision, observation.evidence.value)


def _from_extraction(
    observation: DouyinCandidateExtractionObservation,
    page_revision: int,
) -> DouyinDiscoveryExecutionResult:
    if not isinstance(observation, DouyinCandidateExtractionObservation):
        return _failed(page_revision, "page_unavailable")
    if observation.state is DouyinCandidateExtractionState.COMPLETED:
        if not observation.candidates:
            return _failed(page_revision, "no_candidates")
        return _result(
            DouyinDiscoveryOperationState.COMPLETED,
            DouyinCandidateExtractionEvidence.CANDIDATES_EXTRACTED.value,
            page_revision,
            observation.candidates,
        )
    if observation.evidence is DouyinCandidateExtractionEvidence.LOGIN_REQUIRED:
        return _result(
            DouyinDiscoveryOperationState.LOGIN_REQUIRED,
            "login_required",
            page_revision,
        )
    if observation.evidence is DouyinCandidateExtractionEvidence.BLOCKING_DIALOG:
        return _result(
            DouyinDiscoveryOperationState.HANDOFF_REQUIRED,
            "blocking_dialog",
            page_revision,
        )
    return _failed(page_revision, observation.evidence.value)


def _failed(page_revision: int, evidence: str) -> DouyinDiscoveryExecutionResult:
    allowed = _EVIDENCE_BY_STATE[DouyinDiscoveryOperationState.FAILED]
    safe_evidence = evidence if evidence in allowed else "page_unavailable"
    return _result(
        DouyinDiscoveryOperationState.FAILED,
        safe_evidence,
        page_revision,
    )


def _result(
    state: DouyinDiscoveryOperationState,
    evidence: str,
    page_revision: int,
    candidates: tuple[DouyinCandidate, ...] = (),
) -> DouyinDiscoveryExecutionResult:
    return DouyinDiscoveryExecutionResult(
        state=state,
        evidence=evidence,
        page_revision=page_revision,
        candidates=candidates,
    )


__all__ = [
    "DouyinDiscoveryExecutionResult",
    "DouyinDiscoveryOperation",
    "DouyinDiscoveryOperationRejected",
    "DouyinDiscoveryOperationState",
    "ProductionDouyinDiscoveryOperation",
]
