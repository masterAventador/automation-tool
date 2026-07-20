#!/usr/bin/env python3
"""Run the formal read-only Douyin discovery against one authorized private Profile."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from automation_tool.executor.browser_authority import BrowserLaunchAuthority
from automation_tool.executor.browser_runtime import BrowserLaunchRequest, BrowserRuntime
from automation_tool.executor.command_processor import ExecutorCommandProcessor
from automation_tool.executor.discovery_operation import (
    DouyinDiscoveryExecutionResult,
    ProductionDouyinDiscoveryOperation,
)
from automation_tool.executor.ledger import ExecutorLedger
from automation_tool.executor.rpa.douyin.session import (
    DOUYIN_SESSION_PROBE_URL,
    DouyinSessionDetector,
    DouyinSessionState,
)
from automation_tool.protocol import (
    DouyinDiscoveryCommandPayload,
    PlatformSessionState,
    TaskDiscoveryBatchEnvelope,
    TaskDiscoveryCompletedEnvelope,
)

_PROFILE_ENVIRONMENT = "AUTOMATION_TOOL_D616_PROFILE_DIRECTORY"
_MACOS_CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
_INSTALLATION_ID = "123e4567-e89b-42d3-a456-426614174003"
_EXECUTOR_ID = "123e4567-e89b-42d3-a456-426614174004"
_TASK_ID = "123e4567-e89b-42d3-a456-426614174005"
_ATTEMPT_ID = "123e4567-e89b-42d3-a456-426614174006"
_ERROR = "D6-16 real discovery acceptance is unavailable"


class _RecordingDiscovery:
    def __init__(self, operation: ProductionDouyinDiscoveryOperation) -> None:
        self._operation = operation
        self.result: DouyinDiscoveryExecutionResult | None = None

    def run(
        self,
        payload: DouyinDiscoveryCommandPayload,
        *,
        cancellation_requested: Callable[[], bool],
    ) -> DouyinDiscoveryExecutionResult:
        self.result = self._operation.run(
            payload,
            cancellation_requested=cancellation_requested,
        )
        return self.result


def _request() -> BrowserLaunchRequest:
    profile_source = os.environ.get(_PROFILE_ENVIRONMENT)
    if profile_source is None:
        raise RuntimeError
    profile = Path(profile_source)
    if (
        sys.platform != "darwin"
        or not _MACOS_CHROME.is_file()
        or not profile.is_absolute()
        or not profile.is_dir()
        or profile.is_symlink()
    ):
        raise RuntimeError
    return BrowserLaunchRequest(
        executable_path=_MACOS_CHROME,
        profile_directory=profile,
        headless=True,
    )


def _healthy(request: BrowserLaunchRequest) -> bool:
    runtime = BrowserRuntime()
    with runtime.running(request):
        window = runtime.primary_window()
        page = cast(Any, window.playwright_page)
        page.goto(
            DOUYIN_SESSION_PROBE_URL,
            wait_until="domcontentloaded",
            timeout=30_000,
        )
        detector = DouyinSessionDetector()
        deadline = time.monotonic() + 30
        previous: DouyinSessionState | None = None
        while time.monotonic() < deadline:
            observation = detector.check(window)
            if observation.state is not previous:
                print(f"d616.session.{observation.state.value}", flush=True)
                previous = observation.state
            if observation.state is DouyinSessionState.HEALTHY:
                return True
            if observation.state in {
                DouyinSessionState.EXPIRED,
                DouyinSessionState.MISSING,
                DouyinSessionState.RISK,
            }:
                return False
            time.sleep(1)
        return False


def _command(now: datetime) -> str:
    return json.dumps(
        {
            "protocol_version": "1.0",
            "message_id": str(uuid4()),
            "message_type": "task.discover",
            "sent_at": now.isoformat().replace("+00:00", "Z"),
            "deadline_at": (now + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
            "installation_id": _INSTALLATION_ID,
            "executor_id": _EXECUTOR_ID,
            "correlation_id": str(uuid4()),
            "idempotency_key": f"task:discover:d616:{uuid4()}",
            "sequence": 1,
            "payload": {
                "discovery_version": "douyin.discovery.v1",
                "keyword": "新能源汽车",
                "target_limit": 5,
                "page_revision": 1,
            },
            "task_id": _TASK_ID,
            "execution_attempt_id": _ATTEMPT_ID,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _discover(request: BrowserLaunchRequest) -> tuple[str, str, int]:
    now = datetime.now(UTC)
    stage = "state"
    discovery: _RecordingDiscovery | None = None
    with tempfile.TemporaryDirectory(prefix="automation-tool-d616-") as directory:
        try:
            ledger = ExecutorLedger(
                state_directory=Path(directory).resolve() / "executor-state",
                installation_id=_INSTALLATION_ID,
                executor_id=_EXECUTOR_ID,
            )
            stage = "platform_session"
            ledger.record_platform_session(
                platform="douyin",
                state=PlatformSessionState.HEALTHY,
                observed_at=now,
                advance_epoch=True,
            )
            stage = "browser_authority"
            authority = BrowserLaunchAuthority()
            authority.authorize(request)
            stage = "discovery_operation"
            discovery = _RecordingDiscovery(
                ProductionDouyinDiscoveryOperation(
                    ledger=ledger,
                    browser_authority=authority,
                )
            )
            stage = "command_processor"
            processor = ExecutorCommandProcessor(
                ledger=ledger,
                installation_id=_INSTALLATION_ID,
                executor_id=_EXECUTOR_ID,
                discovery_operation=discovery,
            )
            stage = "command"
            messages = processor.handle(_command(now))
        except Exception:
            if discovery is not None and discovery.result is not None:
                print(
                    f"d616.adapter.{discovery.result.state.value}.{discovery.result.evidence}",
                    flush=True,
                )
            print(f"d616.stage.{stage}.unavailable", flush=True)
            raise
        completed = messages[-1]
        if not isinstance(completed, TaskDiscoveryCompletedEnvelope):
            raise RuntimeError
        candidate_count = sum(
            len(message.payload.candidates)
            for message in messages
            if isinstance(message, TaskDiscoveryBatchEnvelope)
        )
        if candidate_count != completed.payload.candidate_count:
            raise RuntimeError
        return completed.payload.outcome, completed.payload.evidence, candidate_count


def main() -> int:
    try:
        request = _request()
        if not _healthy(request):
            print(_ERROR, file=sys.stderr, flush=True)
            return 2
        outcome, evidence, candidate_count = _discover(request)
        print(f"d616.discovery.{outcome}.{evidence}", flush=True)
        print(f"d616.candidates.{candidate_count}", flush=True)
        if outcome != "completed" or evidence != "candidates_extracted" or candidate_count == 0:
            print(_ERROR, file=sys.stderr, flush=True)
            return 3
        return 0
    except Exception:
        print(_ERROR, file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
