from __future__ import annotations

import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

import pytest

from automation_tool.executor.browser_runtime import BrowserLaunchRequest, BrowserRuntime
from automation_tool.executor.rpa.douyin.candidate_extraction import (
    DouyinCandidateExtraction,
    DouyinCandidateExtractionEvidence,
    DouyinCandidateExtractionState,
)
from automation_tool.executor.rpa.douyin.page_version import (
    DOUYIN_HOME_URL,
    douyin_search_results_url,
)
from automation_tool.executor.rpa.douyin.search import DouyinSearchExecution
from automation_tool.protocol import DouyinSearchInput

MACOS_CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "douyin_discovery_pages"
HOME_DOCUMENT = (FIXTURE_ROOT / "home.html").read_text(encoding="utf-8")
RESULT_DOCUMENT = (FIXTURE_ROOT / "results-normal.html").read_text(encoding="utf-8")


def test_production_search_then_candidate_privacy_boundary_uses_headless_browser_and_closes(
    tmp_path: Path,
) -> None:
    if sys.platform != "darwin" or not MACOS_CHROME.is_file():
        pytest.skip("D6-07 system Chrome acceptance currently requires macOS Chrome")
    profile = tmp_path / "automation-tool-d6-07-profile"
    profile.mkdir(mode=0o700)
    search = DouyinSearchInput(keyword="新能源汽车", target_limit=2)
    expected_url = douyin_search_results_url(search.keyword)
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

        def fulfill_official_page(route: Any) -> None:
            document = HOME_DOCUMENT if route.request.url == DOUYIN_HOME_URL else RESULT_DOCUMENT
            route.fulfill(status=200, content_type="text/html", body=document)

        page.route(DOUYIN_HOME_URL, fulfill_official_page)
        page.route(expected_url, fulfill_official_page)
        search_observation = DouyinSearchExecution(window, search).run()
        assert search_observation.succeeded is True

        observation = DouyinCandidateExtraction(
            window,
            maximum=search.target_limit,
            page_revision=11,
        ).run()

        assert observation.state is DouyinCandidateExtractionState.COMPLETED
        assert observation.evidence is DouyinCandidateExtractionEvidence.CANDIDATES_EXTRACTED
        assert [candidate.platform_target_id for candidate in observation.candidates] == [
            "creator-001",
            "creator-002",
        ]
        assert [candidate.summary.display_name for candidate in observation.candidates] == [
            "创作者甲",
            "创作者乙",
        ]
        serialized = repr(asdict(observation))
        for private in (
            "page-secret",
            "private-page-body",
            "avatar-secret",
            "private-phone",
            "private-article-copy",
            "https://www.douyin.com/user/",
        ):
            assert private not in serialized

    assert not runtime.is_running
    assert os.stat(profile).st_mode & 0o777 == 0o700
