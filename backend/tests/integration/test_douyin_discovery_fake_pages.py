from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from conftest import assert_private_profile_directory, create_private_profile_directory

from automation_tool.executor.browser_authority import BrowserLaunchAuthority
from automation_tool.executor.browser_diagnostic_artifact import (
    BROWSER_DIAGNOSTIC_SCREENSHOT_POLICY,
    BROWSER_DIAGNOSTIC_TRACE_POLICY,
    MAX_BROWSER_DIAGNOSTIC_SCREENSHOT_BYTES,
    MAX_BROWSER_DIAGNOSTIC_TRACE_BYTES,
)
from automation_tool.executor.browser_runtime import (
    BrowserLaunchRequest,
    BrowserRuntime,
    BrowserWindow,
)
from automation_tool.executor.command_processor import ExecutorCommandProcessor
from automation_tool.executor.discovery_operation import ProductionDouyinDiscoveryOperation
from automation_tool.executor.ledger import ExecutorLedger
from automation_tool.executor.local_artifact import LocalArtifactStore
from automation_tool.executor.rpa.douyin.page_version import (
    DOUYIN_HOME_URL,
    DOUYIN_SESSION_PROBE_URL,
    douyin_search_results_url,
)
from automation_tool.protocol import PlatformSessionState, TaskDiscoveryCompletedEnvelope

MACOS_CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "douyin_discovery_pages"
NOW = datetime(2026, 7, 20, 6, 0, tzinfo=UTC)
INSTALLATION_ID = "123e4567-e89b-42d3-a456-426614174003"
EXECUTOR_ID = "123e4567-e89b-42d3-a456-426614174004"
UNKNOWN_URL = "https://www.douyin.com/unsupported/fake-version"


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str
    target_limit: int
    outcome: str
    evidence: str
    candidate_count: int
    artifact_count: int = 0
    capture_successful_diagnostics: bool = False


SCENARIOS = (
    Scenario(
        "normal",
        2,
        "completed",
        "candidates_extracted",
        2,
        capture_successful_diagnostics=True,
    ),
    Scenario("empty", 2, "failed", "no_candidates", 0),
    Scenario("dialog", 2, "handoff_required", "blocking_dialog", 0),
    Scenario("login_redirect", 2, "login_required", "login_required", 0),
    Scenario("unknown_version", 2, "handoff_required", "page_version_unknown", 0, 1),
    Scenario("infinite_scroll", 100, "completed", "candidates_extracted", 21),
)


class Clock:
    @staticmethod
    def now() -> datetime:
        return NOW


class Ids:
    def __init__(self, scenario_index: int) -> None:
        self.value = scenario_index * 100

    def __call__(self) -> UUID:
        self.value += 1
        return UUID(f"923e4567-e89b-42d3-a456-{self.value:012d}")


class FakePageRuntime:
    def __init__(self, scenario: Scenario) -> None:
        self.runtime = BrowserRuntime()
        self.scenario = scenario

    def start(self, request: object) -> None:
        if not isinstance(request, BrowserLaunchRequest):
            raise AssertionError("D6-15 runtime received an invalid browser request")
        self.runtime.start(request)

    def primary_window(self) -> BrowserWindow:
        window = self.runtime.primary_window()
        page = cast(Any, window.playwright_page)
        expected_results = douyin_search_results_url("自动化运营")

        def fulfill(route: Any) -> None:
            url = route.request.url
            if url == DOUYIN_HOME_URL and self.scenario.name == "login_redirect":
                route.fulfill(status=302, headers={"location": DOUYIN_SESSION_PROBE_URL})
                return
            if url == DOUYIN_HOME_URL and self.scenario.name == "unknown_version":
                route.fulfill(status=302, headers={"location": UNKNOWN_URL})
                return
            fixture_name = {
                DOUYIN_HOME_URL: (
                    "home-dialog.html" if self.scenario.name == "dialog" else "home.html"
                ),
                DOUYIN_SESSION_PROBE_URL: "login.html",
                UNKNOWN_URL: "unknown-version.html",
                expected_results: {
                    "normal": "results-normal.html",
                    "empty": "results-empty.html",
                    "infinite_scroll": "results-infinite-scroll.html",
                }.get(self.scenario.name, "results-empty.html"),
            }.get(url)
            if fixture_name is None:
                route.fulfill(status=404, content_type="text/plain", body="not found")
                return
            route.fulfill(
                status=200,
                content_type="text/html",
                body=(FIXTURE_ROOT / fixture_name).read_text(encoding="utf-8"),
            )

        page.route("https://www.douyin.com/**", fulfill)
        return window

    def close(self) -> None:
        self.runtime.close()


def command(scenario_index: int, target_limit: int) -> str:
    suffix = scenario_index + 1
    return json.dumps(
        {
            "protocol_version": "1.0",
            "message_id": f"323e4567-e89b-42d3-a456-{suffix:012d}",
            "message_type": "task.discover",
            "sent_at": NOW.isoformat().replace("+00:00", "Z"),
            "deadline_at": (NOW + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
            "installation_id": INSTALLATION_ID,
            "executor_id": EXECUTOR_ID,
            "correlation_id": f"423e4567-e89b-42d3-a456-{suffix:012d}",
            "idempotency_key": f"task:discover:d615:{scenario_index}",
            "sequence": 1,
            "payload": {
                "discovery_version": "douyin.discovery.v1",
                "keyword": "自动化运营",
                "target_limit": target_limit,
                "page_revision": 7,
            },
            "task_id": f"523e4567-e89b-42d3-a456-{suffix:012d}",
            "execution_attempt_id": f"623e4567-e89b-42d3-a456-{suffix:012d}",
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


@pytest.mark.parametrize(("scenario_index", "scenario"), tuple(enumerate(SCENARIOS)))
def test_formal_discovery_replays_every_fake_page_headlessly(
    tmp_path: Path,
    scenario_index: int,
    scenario: Scenario,
) -> None:
    if sys.platform != "darwin" or not MACOS_CHROME.is_file():
        pytest.skip("D6-15 system Chrome fake-page regression currently requires macOS Chrome")
    profile = tmp_path / f"automation-tool-d6-15-{scenario.name}-profile"
    create_private_profile_directory(profile)
    state = tmp_path / f"automation-tool-d6-15-{scenario.name}-state"
    ledger = ExecutorLedger(
        state_directory=state,
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
    )
    ledger.record_platform_session(
        platform="douyin",
        state=PlatformSessionState.HEALTHY,
        observed_at=NOW - timedelta(seconds=1),
        advance_epoch=True,
    )
    authority = BrowserLaunchAuthority()
    authority.authorize(
        BrowserLaunchRequest(
            executable_path=MACOS_CHROME,
            profile_directory=profile,
            headless=True,
        )
    )
    runtime = FakePageRuntime(scenario)
    processor = ExecutorCommandProcessor(
        ledger=ledger,
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
        clock=Clock(),
        id_source=Ids(scenario_index + 1),
        discovery_operation=ProductionDouyinDiscoveryOperation(
            ledger=ledger,
            browser_authority=authority,
            runtime_factory=lambda: runtime,
            capture_successful_diagnostics=scenario.capture_successful_diagnostics,
        ),
    )

    messages = processor.handle(command(scenario_index, scenario.target_limit))

    completed = messages[-1]
    assert isinstance(completed, TaskDiscoveryCompletedEnvelope)
    assert completed.payload.outcome == scenario.outcome
    assert completed.payload.evidence == scenario.evidence
    assert completed.payload.candidate_count == scenario.candidate_count
    assert len(tuple((state / "artifacts/evidence/page-drift").glob("*.json"))) == (
        scenario.artifact_count
    )
    expected_diagnostics = int(
        scenario.outcome != "completed" or scenario.capture_successful_diagnostics
    )
    screenshot_store = LocalArtifactStore(
        root_directory=state,
        policy=BROWSER_DIAGNOSTIC_SCREENSHOT_POLICY,
    )
    trace_store = LocalArtifactStore(
        root_directory=state,
        policy=BROWSER_DIAGNOSTIC_TRACE_POLICY,
    )
    screenshots = screenshot_store.list_references()
    traces = trace_store.list_references()
    assert len(screenshots) == expected_diagnostics
    assert len(traces) == expected_diagnostics
    if expected_diagnostics:
        screenshot = screenshot_store.read(screenshots[0])
        trace_source = trace_store.read(traces[0])
        trace = json.loads(trace_source)
        assert screenshot.startswith(b"\x89PNG\r\n\x1a\n")
        assert b"tEXt" not in screenshot
        assert b"iTXt" not in screenshot
        assert "自动化运营".encode() not in screenshot
        assert len(screenshot) <= MAX_BROWSER_DIAGNOSTIC_SCREENSHOT_BYTES
        assert len(trace_source) <= MAX_BROWSER_DIAGNOSTIC_TRACE_BYTES
        assert set(trace) == {
            "artifact_id",
            "artifact_version",
            "captured_at",
            "operation",
            "page_revision",
            "platform",
            "redaction_version",
            "screenshot_artifact_id",
            "stage",
            "trigger",
        }
        assert trace["screenshot_artifact_id"] == str(screenshots[0].artifact_id)
        assert trace["trigger"] == (
            "user_enabled" if scenario.outcome == "completed" else "failure"
        )
        assert "自动化运营" not in trace_source.decode("utf-8")
        assert "douyin.com" not in trace_source.decode("utf-8")
    assert not runtime.runtime.is_running
    assert_private_profile_directory(profile)


def test_fake_page_corpus_is_closed_and_contains_no_external_runtime_dependencies() -> None:
    expected = {
        "home.html",
        "home-dialog.html",
        "home-risk-challenge.html",
        "login.html",
        "unknown-version.html",
        "results-normal.html",
        "results-empty.html",
        "results-infinite-scroll.html",
        "results-lazy-rendering.html",
        "results-two-visible-authors.html",
    }

    assert {path.name for path in FIXTURE_ROOT.iterdir()} == expected
    for path in FIXTURE_ROOT.iterdir():
        source = path.read_text(encoding="utf-8")
        assert 1 <= len(source.encode("utf-8")) <= 16 * 1024
        assert "http://" not in source
        assert "https://" not in source
        assert "cookie" not in source.lower()
        assert "localstorage" not in source.lower()
        assert "fetch(" not in source.lower()
