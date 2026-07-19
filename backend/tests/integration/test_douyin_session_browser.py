from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, cast

import pytest

from automation_tool.executor.browser_runtime import (
    BrowserLaunchRequest,
    BrowserRuntime,
)
from automation_tool.executor.rpa.douyin.session import (
    DOUYIN_SESSION_PROBE_URL,
    DouyinSessionDetector,
    DouyinSessionEvidence,
    DouyinSessionState,
)

BACKEND_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = BACKEND_ROOT / "tests/fixtures/douyin_session_states.html"
MACOS_CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


def test_isolated_official_origin_pages_use_the_production_browser_detector(
    tmp_path: Path,
) -> None:
    if sys.platform != "darwin" or not MACOS_CHROME.is_file():
        pytest.skip("B5-09 isolated system Chrome acceptance currently requires macOS Chrome")
    profile = tmp_path / "automation-tool-b5-09-profile"
    profile.mkdir(mode=0o700)
    fixture = FIXTURE.read_text(encoding="utf-8")
    runtime = BrowserRuntime()

    with runtime.running(
        BrowserLaunchRequest(
            executable_path=MACOS_CHROME,
            profile_directory=profile,
        )
    ):
        window = runtime.primary_window()
        page = cast(Any, window.playwright_page)
        page.route(
            "https://www.douyin.com/automation-tool-b5-09-fixture*",
            lambda route: route.fulfill(status=200, content_type="text/html", body=fixture),
        )
        expected = {
            "risk": (DouyinSessionState.RISK, DouyinSessionEvidence.RISK_CHALLENGE),
            "expired": (DouyinSessionState.EXPIRED, DouyinSessionEvidence.LOGIN_EXPIRED),
            "healthy": (
                DouyinSessionState.HEALTHY,
                DouyinSessionEvidence.AUTHENTICATED_SHELL,
            ),
            "missing": (DouyinSessionState.MISSING, DouyinSessionEvidence.LOGIN_ENTRY),
            "unknown": (DouyinSessionState.UNKNOWN, DouyinSessionEvidence.INSUFFICIENT),
            "conflicting": (DouyinSessionState.UNKNOWN, DouyinSessionEvidence.CONFLICTING),
        }
        for state, result in expected.items():
            page.goto(
                f"https://www.douyin.com/automation-tool-b5-09-fixture?state={state}",
                wait_until="domcontentloaded",
                timeout=30_000,
            )
            observation = DouyinSessionDetector().check(window)
            assert (observation.state, observation.evidence) == result

    assert not runtime.is_running
    assert os.stat(profile).st_mode & 0o777 == 0o700


@pytest.mark.skipif(
    os.environ.get("AUTOMATION_TOOL_B509_LIVE") != "1",
    reason="explicit read-only public Douyin acceptance",
)
def test_live_public_douyin_page_is_detected_as_missing_or_risk(tmp_path: Path) -> None:
    if sys.platform != "darwin" or not MACOS_CHROME.is_file():
        pytest.skip("B5-09 live public acceptance currently requires macOS Chrome")
    profile = tmp_path / "automation-tool-b5-09-live-profile"
    profile.mkdir(mode=0o700)
    runtime = BrowserRuntime()

    with runtime.running(
        BrowserLaunchRequest(
            executable_path=MACOS_CHROME,
            profile_directory=profile,
        )
    ):
        window = runtime.primary_window()
        page = cast(Any, window.playwright_page)
        page.goto(
            DOUYIN_SESSION_PROBE_URL,
            wait_until="domcontentloaded",
            timeout=30_000,
        )
        observation = DouyinSessionDetector().check(window)

        assert observation.state in {
            DouyinSessionState.MISSING,
            DouyinSessionState.RISK,
        }
        assert observation.evidence in {
            DouyinSessionEvidence.LOGIN_ENTRY,
            DouyinSessionEvidence.RISK_CHALLENGE,
        }
        assert observation.circuit_open

    assert not runtime.is_running
