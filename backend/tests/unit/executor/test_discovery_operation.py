from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

import automation_tool.executor.discovery_operation as discovery_module
from automation_tool.executor.browser_authority import (
    BrowserLaunchAuthority,
    BrowserLaunchAuthorityRejected,
)
from automation_tool.executor.browser_runtime import BrowserLaunchRequest, BrowserWindow
from automation_tool.executor.discovery_operation import (
    DouyinDiscoveryExecutionResult,
    DouyinDiscoveryOperationRejected,
    DouyinDiscoveryOperationState,
    ProductionDouyinDiscoveryOperation,
)
from automation_tool.executor.ledger import ExecutorLedger
from automation_tool.executor.page_drift_artifact import PageDriftArtifactRejected
from automation_tool.executor.rpa.douyin.bounded_scroll import (
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
    DouyinSearchExecutionEvidence,
    DouyinSearchExecutionObservation,
    DouyinSearchExecutionState,
)
from automation_tool.protocol import (
    DouyinCandidate,
    DouyinCandidateSource,
    DouyinCandidateSummary,
    DouyinDiscoveryCommandPayload,
    PlatformSessionState,
)

INSTALLATION_ID = "123e4567-e89b-42d3-a456-426614174003"
EXECUTOR_ID = "123e4567-e89b-42d3-a456-426614174004"
NOW = datetime(2026, 7, 19, 16, 0, tzinfo=UTC)


def browser_request(tmp_path: Path) -> BrowserLaunchRequest:
    tmp_path.mkdir(parents=True, exist_ok=True)
    executable = tmp_path / "browser"
    executable.write_bytes(b"browser")
    executable.chmod(0o700)
    profile = tmp_path / "profile"
    profile.mkdir(mode=0o700)
    return BrowserLaunchRequest(
        executable_path=executable,
        profile_directory=profile,
        headless=True,
    )


def payload() -> DouyinDiscoveryCommandPayload:
    return DouyinDiscoveryCommandPayload.model_validate(
        {
            "discovery_version": "douyin.discovery.v1",
            "keyword": "自动化运营",
            "target_limit": 2,
            "page_revision": 7,
        }
    )


def candidate(index: int) -> DouyinCandidate:
    return DouyinCandidate(
        platform_target_id=f"author-{index}",
        summary=DouyinCandidateSummary(
            display_name=f"目标 {index}",
            public_handle=f"target_{index}",
        ),
        source=DouyinCandidateSource.GENERAL_SEARCH_AUTHOR,
        page_revision=7,
    )


def healthy_ledger(tmp_path: Path) -> ExecutorLedger:
    ledger = ExecutorLedger(
        state_directory=tmp_path / "state",
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
    )
    ledger.record_platform_session(
        platform="douyin",
        state=PlatformSessionState.HEALTHY,
        observed_at=NOW,
        advance_epoch=True,
    )
    return ledger


def test_browser_authority_retains_only_local_authorized_identity_and_leases_exclusively(
    tmp_path: Path,
) -> None:
    authority = BrowserLaunchAuthority()
    request = browser_request(tmp_path)

    with pytest.raises(BrowserLaunchAuthorityRejected), authority.lease():
        raise AssertionError

    authority.authorize(request)
    with authority.lease() as leased:
        assert leased == request
        with pytest.raises(BrowserLaunchAuthorityRejected), authority.lease():
            raise AssertionError
        with pytest.raises(BrowserLaunchAuthorityRejected):
            authority.revoke()

    authority.revoke()
    with pytest.raises(BrowserLaunchAuthorityRejected), authority.lease():
        raise AssertionError
    assert str(request.executable_path) not in repr(authority)
    assert str(request.profile_directory) not in repr(authority)


def test_browser_authority_revalidates_and_releases_explicit_leases(tmp_path: Path) -> None:
    authority = BrowserLaunchAuthority()
    request = browser_request(tmp_path / "valid")
    with pytest.raises(BrowserLaunchAuthorityRejected):
        authority.authorize(cast(Any, object()))

    authority.authorize(request)
    lease = authority.acquire()
    assert lease.__enter__() is lease
    assert repr(lease) == "BrowserLaunchLease(<redacted>)"
    with pytest.raises(BrowserLaunchAuthorityRejected):
        authority.authorize(request)
    lease.__exit__(None, None, None)
    lease.close()

    invalid = browser_request(tmp_path / "invalid-authorize")
    invalid.executable_path.unlink()
    with pytest.raises(BrowserLaunchAuthorityRejected):
        authority.authorize(invalid)

    stale = browser_request(tmp_path / "invalid-acquire")
    authority.authorize(stale)
    stale.profile_directory.rmdir()
    with pytest.raises(BrowserLaunchAuthorityRejected):
        authority.acquire()


def test_production_discovery_orchestrates_search_scroll_extract_and_closes_runtime(
    tmp_path: Path,
) -> None:
    authority = BrowserLaunchAuthority()
    request = browser_request(tmp_path)
    authority.authorize(request)
    ledger = healthy_ledger(tmp_path)
    calls: list[tuple[str, object]] = []

    class Runtime:
        def start(self, received: BrowserLaunchRequest) -> None:
            calls.append(("runtime.start", received))

        def primary_window(self) -> object:
            window = object()
            calls.append(("runtime.window", window))
            return window

        def close(self) -> None:
            calls.append(("runtime.close", True))

    class Search:
        def run(self) -> DouyinSearchExecutionObservation:
            calls.append(("search", True))
            return DouyinSearchExecutionObservation(
                state=DouyinSearchExecutionState.SUCCEEDED,
                evidence=DouyinSearchExecutionEvidence.RESULTS_READY,
            )

    class Scroll:
        def run(self) -> DouyinBoundedScrollObservation:
            calls.append(("scroll", True))
            return DouyinBoundedScrollObservation(
                state=DouyinBoundedScrollState.COMPLETED,
                evidence=DouyinBoundedScrollEvidence.TARGET_LIMIT_REACHED,
                rounds_completed=1,
                target_count=2,
                target_limit=2,
            )

    class Extraction:
        def run(self) -> DouyinCandidateExtractionObservation:
            calls.append(("extract", True))
            return DouyinCandidateExtractionObservation(
                state=DouyinCandidateExtractionState.COMPLETED,
                evidence=DouyinCandidateExtractionEvidence.CANDIDATES_EXTRACTED,
                candidates=(candidate(1), candidate(2)),
                requested_limit=2,
                page_revision=7,
            )

    operation = ProductionDouyinDiscoveryOperation(
        ledger=ledger,
        browser_authority=authority,
        runtime_factory=cast(Any, Runtime),
        search_factory=lambda _window, _search: Search(),
        scroll_factory=lambda _window, _search, _result, _cancel: Scroll(),
        extraction_factory=lambda _window, _maximum, _revision: Extraction(),
    )

    result = operation.run(payload(), cancellation_requested=lambda: False)

    assert result.state is DouyinDiscoveryOperationState.COMPLETED
    assert result.candidates == (candidate(1), candidate(2))
    assert [name for name, _value in calls] == [
        "runtime.start",
        "runtime.window",
        "search",
        "scroll",
        "extract",
        "runtime.close",
    ]
    assert calls[0][1] == request
    with authority.lease():
        pass


@pytest.mark.parametrize(
    ("search_state", "search_evidence", "expected_state", "expected_evidence"),
    (
        (
            DouyinSearchExecutionState.LOGIN_REQUIRED,
            DouyinSearchExecutionEvidence.LOGIN_REQUIRED,
            DouyinDiscoveryOperationState.LOGIN_REQUIRED,
            "login_required",
        ),
        (
            DouyinSearchExecutionState.DIALOG_BLOCKED,
            DouyinSearchExecutionEvidence.BLOCKING_DIALOG,
            DouyinDiscoveryOperationState.HANDOFF_REQUIRED,
            "blocking_dialog",
        ),
        (
            DouyinSearchExecutionState.UNKNOWN,
            DouyinSearchExecutionEvidence.PAGE_UNAVAILABLE,
            DouyinDiscoveryOperationState.FAILED,
            "page_unavailable",
        ),
    ),
)
def test_production_discovery_maps_search_circuit_breakers_without_scrolling(
    tmp_path: Path,
    search_state: DouyinSearchExecutionState,
    search_evidence: DouyinSearchExecutionEvidence,
    expected_state: DouyinDiscoveryOperationState,
    expected_evidence: str,
) -> None:
    authority = BrowserLaunchAuthority()
    authority.authorize(browser_request(tmp_path))
    downstream_calls = 0

    class Runtime:
        def start(self, _request: BrowserLaunchRequest) -> None:
            pass

        @staticmethod
        def primary_window() -> object:
            return object()

        def close(self) -> None:
            pass

    class Search:
        def run(self) -> DouyinSearchExecutionObservation:
            return DouyinSearchExecutionObservation(
                state=search_state,
                evidence=search_evidence,
            )

    def forbidden(*_arguments: object) -> object:
        nonlocal downstream_calls
        downstream_calls += 1
        raise AssertionError("downstream discovery ran after an open circuit")

    operation = ProductionDouyinDiscoveryOperation(
        ledger=healthy_ledger(tmp_path),
        browser_authority=authority,
        runtime_factory=cast(Any, Runtime),
        search_factory=lambda _window, _search: Search(),
        scroll_factory=cast(Any, forbidden),
        extraction_factory=cast(Any, forbidden),
    )

    result = operation.run(payload(), cancellation_requested=lambda: False)

    assert result.state is expected_state
    assert result.evidence == expected_evidence
    assert result.candidates == ()
    assert downstream_calls == 0


@pytest.mark.parametrize(
    "evidence",
    (
        DouyinSearchExecutionEvidence.PAGE_VERSION_UNKNOWN,
        DouyinSearchExecutionEvidence.CONFLICTING_ANCHORS,
    ),
)
def test_production_discovery_saves_page_drift_artifact_and_enters_handoff(
    tmp_path: Path,
    evidence: DouyinSearchExecutionEvidence,
) -> None:
    authority = BrowserLaunchAuthority()
    authority.authorize(browser_request(tmp_path / "browser"))
    ledger = healthy_ledger(tmp_path / "ledger")

    class Runtime:
        def start(self, _request: BrowserLaunchRequest) -> None:
            pass

        @staticmethod
        def primary_window() -> object:
            return object()

        def close(self) -> None:
            pass

    class Search:
        def run(self) -> DouyinSearchExecutionObservation:
            return DouyinSearchExecutionObservation(
                state=DouyinSearchExecutionState.UNKNOWN,
                evidence=evidence,
            )

    operation = ProductionDouyinDiscoveryOperation(
        ledger=ledger,
        browser_authority=authority,
        runtime_factory=cast(Any, Runtime),
        search_factory=lambda _window, _search: Search(),
    )

    result = operation.run(payload(), cancellation_requested=lambda: False)

    assert result.state is DouyinDiscoveryOperationState.HANDOFF_REQUIRED
    assert result.evidence == evidence.value
    artifacts = tuple((ledger.database_path.parent / "page-drift-artifacts").glob("*.json"))
    assert len(artifacts) == 1
    assert json.loads(artifacts[0].read_text(encoding="utf-8"))["evidence"] == evidence.value


def test_page_drift_artifact_failure_still_enters_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = BrowserLaunchAuthority()
    authority.authorize(browser_request(tmp_path / "browser"))
    ledger = healthy_ledger(tmp_path / "ledger")

    class Runtime:
        def start(self, _request: BrowserLaunchRequest) -> None:
            pass

        @staticmethod
        def primary_window() -> object:
            return object()

        def close(self) -> None:
            pass

    class Search:
        @staticmethod
        def run() -> DouyinSearchExecutionObservation:
            return DouyinSearchExecutionObservation(
                state=DouyinSearchExecutionState.UNKNOWN,
                evidence=DouyinSearchExecutionEvidence.PAGE_VERSION_UNKNOWN,
            )

    monkeypatch.setattr(
        "automation_tool.executor.page_drift_artifact.PageDriftArtifactStore.capture",
        lambda *_arguments, **_keywords: (_ for _ in ()).throw(PageDriftArtifactRejected()),
    )
    result = ProductionDouyinDiscoveryOperation(
        ledger=ledger,
        browser_authority=authority,
        runtime_factory=cast(Any, Runtime),
        search_factory=lambda _window, _search: Search(),
    ).run(payload(), cancellation_requested=lambda: False)

    assert result.state is DouyinDiscoveryOperationState.HANDOFF_REQUIRED
    assert result.evidence == "page_version_unknown"


def test_production_discovery_requires_healthy_local_session_and_authorized_browser(
    tmp_path: Path,
) -> None:
    ledger = ExecutorLedger(
        state_directory=tmp_path / "state",
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
    )
    operation = ProductionDouyinDiscoveryOperation(
        ledger=ledger,
        browser_authority=BrowserLaunchAuthority(),
    )

    result = operation.run(payload(), cancellation_requested=lambda: False)

    assert result.state is DouyinDiscoveryOperationState.LOGIN_REQUIRED
    assert result.evidence == "login_required"

    unavailable = ProductionDouyinDiscoveryOperation(
        ledger=healthy_ledger(tmp_path / "healthy"),
        browser_authority=BrowserLaunchAuthority(),
    ).run(payload(), cancellation_requested=lambda: False)
    assert unavailable.state is DouyinDiscoveryOperationState.FAILED
    assert unavailable.evidence == "page_unavailable"


def test_discovery_result_and_operation_inputs_fail_closed(tmp_path: Path) -> None:
    valid = DouyinDiscoveryExecutionResult(
        state=DouyinDiscoveryOperationState.COMPLETED,
        evidence="candidates_extracted",
        page_revision=7,
        candidates=(candidate(1),),
    )
    assert "candidate_count=1" in repr(valid)
    invalid_values = (
        {"state": cast(Any, "completed")},
        {"evidence": "private"},
        {"page_revision": 0},
        {"candidates": cast(Any, [candidate(1)])},
        {"candidates": ()},
        {
            "state": DouyinDiscoveryOperationState.FAILED,
            "evidence": "page_unavailable",
            "candidates": (candidate(1),),
        },
    )
    for changes in invalid_values:
        values: dict[str, object] = {
            "state": valid.state,
            "evidence": valid.evidence,
            "page_revision": valid.page_revision,
            "candidates": valid.candidates,
        }
        values.update(changes)
        with pytest.raises(DouyinDiscoveryOperationRejected):
            DouyinDiscoveryExecutionResult(**cast(Any, values))

    authority = BrowserLaunchAuthority()
    ledger = healthy_ledger(tmp_path / "invalid-operation")
    with pytest.raises(DouyinDiscoveryOperationRejected):
        ProductionDouyinDiscoveryOperation(ledger=cast(Any, object()), browser_authority=authority)
    with pytest.raises(DouyinDiscoveryOperationRejected):
        ProductionDouyinDiscoveryOperation(
            ledger=ledger,
            browser_authority=authority,
            page_drift_artifacts=cast(Any, object()),
        )
    operation = ProductionDouyinDiscoveryOperation(
        ledger=ledger,
        browser_authority=authority,
    )
    with pytest.raises(DouyinDiscoveryOperationRejected):
        operation.run(cast(Any, object()), cancellation_requested=lambda: False)
    with pytest.raises(DouyinDiscoveryOperationRejected):
        operation.run(payload(), cancellation_requested=cast(Any, None))


def test_discovery_maps_every_scroll_and_extraction_boundary() -> None:
    revision = 7
    invalid_search = discovery_module._from_search(cast(Any, object()), revision)
    assert invalid_search is not None
    assert invalid_search.evidence == "page_unavailable"
    scroll_cases = (
        (cast(Any, object()), DouyinDiscoveryOperationState.FAILED, "page_unavailable"),
        (
            DouyinBoundedScrollObservation(
                state=DouyinBoundedScrollState.BLOCKED,
                evidence=DouyinBoundedScrollEvidence.LOGIN_REQUIRED,
                rounds_completed=0,
                target_count=0,
                target_limit=2,
            ),
            DouyinDiscoveryOperationState.LOGIN_REQUIRED,
            "login_required",
        ),
        (
            DouyinBoundedScrollObservation(
                state=DouyinBoundedScrollState.BLOCKED,
                evidence=DouyinBoundedScrollEvidence.BLOCKING_DIALOG,
                rounds_completed=0,
                target_count=0,
                target_limit=2,
            ),
            DouyinDiscoveryOperationState.HANDOFF_REQUIRED,
            "blocking_dialog",
        ),
        (
            DouyinBoundedScrollObservation(
                state=DouyinBoundedScrollState.UNKNOWN,
                evidence=DouyinBoundedScrollEvidence.RESULT_COUNT_DECREASED,
                rounds_completed=1,
                target_count=1,
                target_limit=2,
            ),
            DouyinDiscoveryOperationState.FAILED,
            "result_count_decreased",
        ),
    )
    completed_scroll = DouyinBoundedScrollObservation(
        state=DouyinBoundedScrollState.COMPLETED,
        evidence=DouyinBoundedScrollEvidence.TARGET_LIMIT_REACHED,
        rounds_completed=1,
        target_count=2,
        target_limit=2,
    )
    assert discovery_module._from_scroll(completed_scroll, revision) is None
    for observation, expected_state, expected_evidence in scroll_cases:
        result = discovery_module._from_scroll(observation, revision)
        assert result is not None
        assert result.state is expected_state
        assert result.evidence == expected_evidence

    extraction_cases = (
        (cast(Any, object()), DouyinDiscoveryOperationState.FAILED, "page_unavailable"),
        (
            DouyinCandidateExtractionObservation(
                state=DouyinCandidateExtractionState.COMPLETED,
                evidence=DouyinCandidateExtractionEvidence.CANDIDATES_EXTRACTED,
                candidates=(),
                requested_limit=2,
                page_revision=revision,
            ),
            DouyinDiscoveryOperationState.FAILED,
            "no_candidates",
        ),
        (
            DouyinCandidateExtractionObservation(
                state=DouyinCandidateExtractionState.BLOCKED,
                evidence=DouyinCandidateExtractionEvidence.LOGIN_REQUIRED,
                candidates=(),
                requested_limit=2,
                page_revision=revision,
            ),
            DouyinDiscoveryOperationState.LOGIN_REQUIRED,
            "login_required",
        ),
        (
            DouyinCandidateExtractionObservation(
                state=DouyinCandidateExtractionState.BLOCKED,
                evidence=DouyinCandidateExtractionEvidence.BLOCKING_DIALOG,
                candidates=(),
                requested_limit=2,
                page_revision=revision,
            ),
            DouyinDiscoveryOperationState.HANDOFF_REQUIRED,
            "blocking_dialog",
        ),
        (
            DouyinCandidateExtractionObservation(
                state=DouyinCandidateExtractionState.UNKNOWN,
                evidence=DouyinCandidateExtractionEvidence.PRIVACY_REJECTED,
                candidates=(),
                requested_limit=2,
                page_revision=revision,
            ),
            DouyinDiscoveryOperationState.FAILED,
            "privacy_rejected",
        ),
    )
    for observation, expected_state, expected_evidence in extraction_cases:
        result = discovery_module._from_extraction(observation, revision)
        assert result.state is expected_state
        assert result.evidence == expected_evidence

    extracted = DouyinCandidateExtractionObservation(
        state=DouyinCandidateExtractionState.COMPLETED,
        evidence=DouyinCandidateExtractionEvidence.CANDIDATES_EXTRACTED,
        candidates=(candidate(1),),
        requested_limit=2,
        page_revision=revision,
    )
    assert discovery_module._from_extraction(extracted, revision).candidates == (candidate(1),)
    assert discovery_module._failed(revision, "private").evidence == "page_unavailable"


def test_default_extraction_and_runtime_failure_paths_are_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = BrowserWindow._for_runtime(object(), cast(Any, object()))
    assert isinstance(discovery_module._default_extraction(window, 2, 7), DouyinCandidateExtraction)

    authority = BrowserLaunchAuthority()
    authority.authorize(browser_request(tmp_path))
    ledger = healthy_ledger(tmp_path)

    class Runtime:
        def start(self, _request: BrowserLaunchRequest) -> None:
            raise RuntimeError("private runtime")

        def primary_window(self) -> BrowserWindow:
            raise AssertionError

        def close(self) -> None:
            pass

    failed = ProductionDouyinDiscoveryOperation(
        ledger=ledger,
        browser_authority=authority,
        runtime_factory=cast(Any, Runtime),
    ).run(payload(), cancellation_requested=lambda: False)
    assert failed.state is DouyinDiscoveryOperationState.FAILED

    monkeypatch.setattr(
        ExecutorLedger,
        "get_platform_session",
        lambda *_arguments: (_ for _ in ()).throw(RuntimeError("private ledger")),
    )
    unavailable = ProductionDouyinDiscoveryOperation(
        ledger=ledger,
        browser_authority=authority,
    ).run(payload(), cancellation_requested=lambda: False)
    assert unavailable.evidence == "page_unavailable"
