from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, cast

import pytest

from automation_tool.executor.browser_runtime import BrowserLaunchRequest, BrowserRuntime
from automation_tool.executor.rpa.douyin.browse import (
    DouyinBrowseExecution,
    DouyinBrowseExecutionEvidence,
    DouyinBrowseExecutionState,
)
from automation_tool.executor.rpa.douyin.page_version import douyin_user_profile_url
from automation_tool.protocol import (
    DouyinCandidate,
    DouyinCandidateSource,
    DouyinCandidateSummary,
)

MACOS_CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "douyin_browse_pages"


def candidate(target_id: str) -> DouyinCandidate:
    return DouyinCandidate(
        platform_target_id=target_id,
        summary=DouyinCandidateSummary(display_name="隔离目标", public_handle=None),
        source=DouyinCandidateSource.GENERAL_SEARCH_AUTHOR,
        page_revision=1,
    )


def test_production_runtime_browses_fake_profiles_headlessly_without_sending_and_closes(
    tmp_path: Path,
) -> None:
    if sys.platform != "darwin" or not MACOS_CHROME.is_file():
        pytest.skip("A7-10 system Chrome fake-page acceptance currently requires macOS Chrome")
    profile = tmp_path / "automation-tool-a7-10-profile"
    profile.mkdir(mode=0o700)
    documents = {
        "ready-001": (FIXTURE_ROOT / "profile-ready.html").read_text(encoding="utf-8"),
        "login-001": (FIXTURE_ROOT / "profile-login.html").read_text(encoding="utf-8"),
        "blocked-001": (FIXTURE_ROOT / "profile-blocked.html").read_text(encoding="utf-8"),
        "drift-001": (FIXTURE_ROOT / "profile-drift.html").read_text(encoding="utf-8"),
    }
    runtime = BrowserRuntime()

    with runtime.running(
        BrowserLaunchRequest(
            executable_path=MACOS_CHROME,
            profile_directory=profile,
            headless=True,
        )
    ):
        window = runtime.primary_window()
        page = cast(Any, window.playwright_page)
        routed_urls: list[str] = []

        def fulfill(route: Any) -> None:
            routed_urls.append(route.request.url)
            target_id = route.request.url.rsplit("/", 1)[-1]
            document = documents.get(target_id)
            route.fulfill(
                status=200 if document is not None else 404,
                content_type="text/html",
                body=document or "not found",
            )

        page.route("https://www.douyin.com/user/**", fulfill)

        ready = DouyinBrowseExecution(window, candidate("ready-001")).run(
            cancellation_requested=lambda: False
        )
        assert ready.state is DouyinBrowseExecutionState.COMPLETED
        assert ready.evidence is DouyinBrowseExecutionEvidence.PROFILE_VISIBLE
        assert page.url == douyin_user_profile_url("ready-001")
        assert page.evaluate("window.__browseSideEffects") == 0

        login = DouyinBrowseExecution(window, candidate("login-001")).run(
            cancellation_requested=lambda: False
        )
        assert login.state is DouyinBrowseExecutionState.LOGIN_REQUIRED

        blocked = DouyinBrowseExecution(window, candidate("blocked-001")).run(
            cancellation_requested=lambda: False
        )
        assert blocked.state is DouyinBrowseExecutionState.DIALOG_BLOCKED

        drift = DouyinBrowseExecution(window, candidate("drift-001")).run(
            cancellation_requested=lambda: False
        )
        assert drift.state is DouyinBrowseExecutionState.UNKNOWN
        assert drift.evidence is DouyinBrowseExecutionEvidence.CONFLICTING_ANCHORS
        assert routed_urls == [
            douyin_user_profile_url("ready-001"),
            douyin_user_profile_url("login-001"),
            douyin_user_profile_url("blocked-001"),
            douyin_user_profile_url("drift-001"),
        ]

    assert not runtime.is_running
    assert os.stat(profile).st_mode & 0o777 == 0o700


def test_browse_fake_page_corpus_is_closed_and_local() -> None:
    expected = {
        "profile-ready.html",
        "profile-login.html",
        "profile-blocked.html",
        "profile-drift.html",
    }
    assert {path.name for path in FIXTURE_ROOT.iterdir()} == expected
    for path in FIXTURE_ROOT.iterdir():
        source = path.read_text(encoding="utf-8")
        assert 1 <= len(source.encode("utf-8")) <= 8 * 1024
        assert "http://" not in source
        assert "https://" not in source
        assert "fetch(" not in source.lower()
