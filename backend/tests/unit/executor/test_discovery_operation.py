from __future__ import annotations

import json
import struct
import zlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

import automation_tool.executor.discovery_operation as discovery_module
from automation_tool.executor.browser_authority import (
    BrowserLaunchAuthority,
    BrowserLaunchAuthorityRejected,
)
from automation_tool.executor.browser_diagnostic_artifact import BrowserDiagnosticArtifactStore
from automation_tool.executor.browser_runtime import BrowserLaunchRequest, BrowserWindow
from automation_tool.executor.discovery_operation import (
    DouyinDiscoveryExecutionResult,
    DouyinDiscoveryOperationRejected,
    DouyinDiscoveryOperationState,
    ProductionDouyinDiscoveryOperation,
)
from automation_tool.executor.ledger import ExecutorLedger
from automation_tool.executor.page_drift_artifact import PageDriftArtifactRejected
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


def diagnostic_png() -> bytes:
    def chunk(kind: bytes, source: bytes) -> bytes:
        checksum = zlib.crc32(kind + source) & 0xFFFFFFFF
        return struct.pack(">I", len(source)) + kind + source + struct.pack(">I", checksum)

    return b"".join(
        (
            b"\x89PNG\r\n\x1a\n",
            chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)),
            chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00")),
            chunk(b"IEND", b""),
        )
    )


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


def test_production_discovery_orchestrates_search_extract_and_closes_runtime(
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
        extraction_factory=lambda _window, _maximum, _revision: Extraction(),
    )

    result = operation.run(payload(), cancellation_requested=lambda: False)

    assert result.state is DouyinDiscoveryOperationState.COMPLETED
    assert result.candidates == (candidate(1), candidate(2))
    assert [name for name, _value in calls] == [
        "runtime.start",
        "runtime.window",
        "search",
        "extract",
        "runtime.close",
    ]
    assert calls[0][1] == request
    with authority.lease():
        pass


@pytest.mark.parametrize(
    ("search_state", "search_evidence", "expected_state", "expected_evidence"),
    (
        # 技能待录制/修复：自动化不能安全驱动 → 转人工。
        (
            DouyinSearchExecutionState.UNKNOWN,
            DouyinSearchExecutionEvidence.PAGE_VERSION_UNKNOWN,
            DouyinDiscoveryOperationState.HANDOFF_REQUIRED,
            "page_version_unknown",
        ),
        (
            DouyinSearchExecutionState.TIMED_OUT,
            DouyinSearchExecutionEvidence.NAVIGATION_TIMED_OUT,
            DouyinDiscoveryOperationState.FAILED,
            "navigation_timed_out",
        ),
        (
            DouyinSearchExecutionState.UNKNOWN,
            DouyinSearchExecutionEvidence.PAGE_UNAVAILABLE,
            DouyinDiscoveryOperationState.FAILED,
            "page_unavailable",
        ),
    ),
)
def test_production_discovery_maps_search_circuit_breakers(
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
        extraction_factory=cast(Any, forbidden),
    )

    result = operation.run(payload(), cancellation_requested=lambda: False)

    assert result.state is expected_state
    assert result.evidence == expected_evidence
    assert result.candidates == ()
    assert downstream_calls == 0


@pytest.mark.parametrize(
    "evidence",
    (DouyinSearchExecutionEvidence.PAGE_VERSION_UNKNOWN,),
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
    artifacts = tuple(
        (ledger.database_path.parent / "artifacts/evidence/page-drift").glob("*.json")
    )
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


def test_discovery_captures_failed_and_explicitly_enabled_success_diagnostics(
    tmp_path: Path,
) -> None:
    class Page:
        def __init__(self) -> None:
            self.calls = 0

        def screenshot(self, **_options: object) -> bytes:
            self.calls += 1
            return diagnostic_png()

    class Runtime:
        def __init__(self, page: Page) -> None:
            self.page = page
            self.closed = False

        def start(self, _request: BrowserLaunchRequest) -> None:
            pass

        def primary_window(self) -> BrowserWindow:
            return BrowserWindow(object(), cast(Any, self.page))

        def close(self) -> None:
            self.closed = True

    class FailedSearch:
        @staticmethod
        def run() -> DouyinSearchExecutionObservation:
            return DouyinSearchExecutionObservation(
                state=DouyinSearchExecutionState.UNKNOWN,
                evidence=DouyinSearchExecutionEvidence.PAGE_UNAVAILABLE,
            )

    class SuccessfulSearch:
        @staticmethod
        def run() -> DouyinSearchExecutionObservation:
            return DouyinSearchExecutionObservation(
                state=DouyinSearchExecutionState.SUCCEEDED,
                evidence=DouyinSearchExecutionEvidence.RESULTS_READY,
            )

    class SuccessfulExtraction:
        @staticmethod
        def run() -> DouyinCandidateExtractionObservation:
            return DouyinCandidateExtractionObservation(
                state=DouyinCandidateExtractionState.COMPLETED,
                evidence=DouyinCandidateExtractionEvidence.CANDIDATES_EXTRACTED,
                candidates=(candidate(1),),
                requested_limit=2,
                page_revision=7,
            )

    failed_root = tmp_path / "failed"
    failed_authority = BrowserLaunchAuthority()
    failed_authority.authorize(browser_request(failed_root / "browser"))
    failed_page = Page()
    failed_runtime = Runtime(failed_page)
    failed_ledger = healthy_ledger(failed_root / "ledger")
    failed = ProductionDouyinDiscoveryOperation(
        ledger=failed_ledger,
        browser_authority=failed_authority,
        runtime_factory=lambda: failed_runtime,
        search_factory=lambda _window, _search: FailedSearch(),
    ).run(payload(), cancellation_requested=lambda: False)

    assert failed.state is DouyinDiscoveryOperationState.FAILED
    assert failed_page.calls == 1
    assert failed_runtime.closed is True
    assert (
        len(
            tuple(
                (failed_ledger.database_path.parent / "artifacts/diagnostics/screenshots").glob(
                    "*.png"
                )
            )
        )
        == 1
    )
    assert (
        len(
            tuple(
                (failed_ledger.database_path.parent / "artifacts/diagnostics/traces").glob("*.json")
            )
        )
        == 1
    )

    for capture_successful_diagnostics, expected in ((False, 0), (True, 1)):
        root = tmp_path / f"success-{capture_successful_diagnostics}"
        authority = BrowserLaunchAuthority()
        authority.authorize(browser_request(root / "browser"))
        page = Page()
        runtime = Runtime(page)
        ledger = healthy_ledger(root / "ledger")
        completed = ProductionDouyinDiscoveryOperation(
            ledger=ledger,
            browser_authority=authority,
            runtime_factory=cast(Any, lambda runtime=runtime: runtime),
            search_factory=lambda _window, _search: SuccessfulSearch(),
            extraction_factory=lambda _window, _maximum, _revision: SuccessfulExtraction(),
            capture_successful_diagnostics=capture_successful_diagnostics,
        ).run(payload(), cancellation_requested=lambda: False)

        assert completed.state is DouyinDiscoveryOperationState.COMPLETED
        assert page.calls == expected
        assert runtime.closed is True
        assert (
            len(
                tuple(
                    (ledger.database_path.parent / "artifacts/diagnostics/screenshots").glob(
                        "*.png"
                    )
                )
            )
            == expected
        )
        assert (
            len(
                tuple((ledger.database_path.parent / "artifacts/diagnostics/traces").glob("*.json"))
            )
            == expected
        )


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
    with pytest.raises(DouyinDiscoveryOperationRejected):
        ProductionDouyinDiscoveryOperation(
            ledger=ledger,
            browser_authority=authority,
            browser_diagnostic_artifacts=cast(Any, object()),
        )
    with pytest.raises(DouyinDiscoveryOperationRejected):
        ProductionDouyinDiscoveryOperation(
            ledger=ledger,
            browser_authority=authority,
            capture_successful_diagnostics=cast(bool, 1),
        )
    explicit_diagnostics = BrowserDiagnosticArtifactStore(
        state_directory=ledger.database_path.parent
    )
    assert isinstance(
        ProductionDouyinDiscoveryOperation(
            ledger=ledger,
            browser_authority=authority,
            browser_diagnostic_artifacts=explicit_diagnostics,
        ),
        ProductionDouyinDiscoveryOperation,
    )
    operation = ProductionDouyinDiscoveryOperation(
        ledger=ledger,
        browser_authority=authority,
    )
    with pytest.raises(DouyinDiscoveryOperationRejected):
        operation.run(cast(Any, object()), cancellation_requested=lambda: False)
    with pytest.raises(DouyinDiscoveryOperationRejected):
        operation.run(payload(), cancellation_requested=cast(Any, None))


def test_discovery_maps_every_extraction_boundary() -> None:
    revision = 7
    invalid_search = discovery_module._from_search(cast(Any, object()), revision)
    assert invalid_search is not None
    assert invalid_search.evidence == "page_unavailable"

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
                state=DouyinCandidateExtractionState.UNKNOWN,
                evidence=DouyinCandidateExtractionEvidence.RESULTS_UNAVAILABLE,
                candidates=(),
                requested_limit=2,
                page_revision=revision,
            ),
            DouyinDiscoveryOperationState.FAILED,
            "results_unavailable",
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
