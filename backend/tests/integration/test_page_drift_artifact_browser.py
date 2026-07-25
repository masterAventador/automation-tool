from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar, cast
from uuid import UUID

from conftest import assert_private_profile_directory, create_private_profile_directory

from automation_tool.executor.browser_authority import BrowserLaunchAuthority
from automation_tool.executor.browser_diagnostic_artifact import (
    BROWSER_DIAGNOSTIC_RETENTION_SECONDS,
    BROWSER_DIAGNOSTIC_SCREENSHOT_POLICY,
    BROWSER_DIAGNOSTIC_TRACE_POLICY,
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
from automation_tool.executor.page_drift_artifact import (
    PAGE_DRIFT_ARTIFACT_POLICY,
    PAGE_DRIFT_ARTIFACT_RETENTION_SECONDS,
    PageDriftArtifactStore,
)
from automation_tool.executor.rpa.douyin.page_version import DOUYIN_HOME_URL
from automation_tool.protocol import PlatformSessionState, TaskDiscoveryCompletedEnvelope

NOW = datetime(2026, 7, 20, 5, 30, tzinfo=UTC)
INSTALLATION_ID = "123e4567-e89b-42d3-a456-426614174003"
EXECUTOR_ID = "123e4567-e89b-42d3-a456-426614174004"
TASK_ID = "123e4567-e89b-42d3-a456-426614174005"
ATTEMPT_ID = "123e4567-e89b-42d3-a456-426614174006"
CONFLICTING_HOME = """<!doctype html>
<html lang="zh-CN"><body>
  <input aria-label="搜索" />
  <button aria-label="搜索">搜索</button>
  <main role="feed"><article>页面契约漂移</article></main>
</body></html>
"""


class Clock:
    @staticmethod
    def now() -> datetime:
        return NOW


class Ids:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> UUID:
        self.value += 1
        return UUID(f"923e4567-e89b-42d3-a456-{self.value:012d}")


class RoutedRuntime:
    instances: ClassVar[list[RoutedRuntime]] = []

    def __init__(self) -> None:
        self.runtime = BrowserRuntime()
        self.__class__.instances.append(self)

    def start(self, request: BrowserLaunchRequest) -> None:
        self.runtime.start(request)

    def primary_window(self) -> BrowserWindow:
        window = self.runtime.primary_window()
        page = cast(Any, window.playwright_page)
        page.route(
            DOUYIN_HOME_URL,
            lambda route: route.fulfill(
                status=200,
                content_type="text/html",
                body=CONFLICTING_HOME,
            ),
        )
        return window

    def close(self) -> None:
        self.runtime.close()


def discovery_command() -> str:
    return json.dumps(
        {
            "protocol_version": "1.0",
            "message_id": "323e4567-e89b-42d3-a456-426614174001",
            "message_type": "task.discover",
            "sent_at": NOW.isoformat().replace("+00:00", "Z"),
            "deadline_at": (NOW + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
            "installation_id": INSTALLATION_ID,
            "executor_id": EXECUTOR_ID,
            "correlation_id": "323e4567-e89b-42d3-a456-426614174002",
            "idempotency_key": "task:discover:d614-browser",
            "sequence": 1,
            "payload": {
                "discovery_version": "douyin.discovery.v1",
                "keyword": "自动化运营私密关键词",
                "target_limit": 2,
                "page_revision": 7,
            },
            "task_id": TASK_ID,
            "execution_attempt_id": ATTEMPT_ID,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def test_formal_discover_command_captures_bounded_drift_and_handoffs_headlessly(
    tmp_path: Path, staged_embedded_chromium: Path,
) -> None:
    RoutedRuntime.instances.clear()
    profile = tmp_path / "automation-tool-d6-14-profile"
    create_private_profile_directory(profile)
    state = tmp_path / "automation-tool-d6-14-state"
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
            executable_path=staged_embedded_chromium,
            profile_directory=profile,
            headless=True,
        )
    )
    stale_page_drift = PageDriftArtifactStore(
        state_directory=state,
        clock=Clock(),
        id_source=Ids(),
    ).capture(
        evidence="page_version_unknown",
        page_revision=6,
        stage="search",
    )
    stale_screenshot = LocalArtifactStore(
        root_directory=state,
        policy=BROWSER_DIAGNOSTIC_SCREENSHOT_POLICY,
        id_source=Ids(),
    ).capture(b"stale-redacted-screenshot")
    trace_store = LocalArtifactStore(
        root_directory=state,
        policy=BROWSER_DIAGNOSTIC_TRACE_POLICY,
        id_source=Ids(),
    )

    def stale_trace_payload(artifact_id: UUID) -> bytes:
        return json.dumps(
            {
                "artifact_id": str(artifact_id),
                "artifact_version": "executor.browser-diagnostic-trace.v1",
                "captured_at": NOW.isoformat().replace("+00:00", "Z"),
                "operation": "douyin_target_discovery",
                "page_revision": 6,
                "platform": "douyin",
                "redaction_version": "browser-skeleton.v1",
                "screenshot_artifact_id": str(stale_screenshot.artifact_id),
                "stage": "search",
                "trigger": "failure",
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    stale_trace = trace_store.capture_generated(stale_trace_payload)
    expired = (
        time.time()
        - max(
            PAGE_DRIFT_ARTIFACT_RETENTION_SECONDS,
            BROWSER_DIAGNOSTIC_RETENTION_SECONDS,
        )
        - 1
    )
    for reference in (stale_page_drift, stale_screenshot, stale_trace):
        os.utime(state / reference.relative_path, (expired, expired))
    processor = ExecutorCommandProcessor(
        ledger=ledger,
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
        clock=Clock(),
        id_source=Ids(),
        discovery_operation=ProductionDouyinDiscoveryOperation(
            ledger=ledger,
            browser_authority=authority,
            runtime_factory=cast(Any, RoutedRuntime),
        ),
    )

    messages = processor.handle(discovery_command())

    completed = messages[-1]
    assert isinstance(completed, TaskDiscoveryCompletedEnvelope)
    assert completed.payload.outcome == "handoff_required"
    assert completed.payload.evidence == "conflicting_anchors"
    artifacts = tuple((state / "artifacts/evidence/page-drift").glob("*.json"))
    assert len(artifacts) == 1
    local_artifacts = LocalArtifactStore(
        root_directory=state,
        policy=PAGE_DRIFT_ARTIFACT_POLICY,
    )
    reference = local_artifacts.resolve(UUID(artifacts[0].stem))
    assert local_artifacts.list_references() == (reference,)
    assert reference.artifact_id != stale_page_drift.artifact_id
    artifact_source = local_artifacts.read(reference).decode("utf-8")
    assert reference.relative_path == (
        f"artifacts/evidence/page-drift/{reference.artifact_id}.json"
    )
    assert not Path(reference.relative_path).is_absolute()
    assert len(artifact_source.encode("utf-8")) <= 2048
    assert "自动化运营私密关键词" not in artifact_source
    assert "页面契约漂移" not in artifact_source
    assert "url" not in artifact_source.lower()
    screenshot_store = LocalArtifactStore(
        root_directory=state,
        policy=BROWSER_DIAGNOSTIC_SCREENSHOT_POLICY,
    )
    current_screenshots = screenshot_store.list_references()
    current_traces = trace_store.list_references()
    assert len(current_screenshots) == 1
    assert len(current_traces) == 1
    assert current_screenshots[0].artifact_id != stale_screenshot.artifact_id
    assert current_traces[0].artifact_id != stale_trace.artifact_id
    current_trace = json.loads(trace_store.read(current_traces[0]))
    assert current_trace["screenshot_artifact_id"] == str(current_screenshots[0].artifact_id)
    assert len(RoutedRuntime.instances) == 1
    assert not RoutedRuntime.instances[0].runtime.is_running
    assert_private_profile_directory(profile)
