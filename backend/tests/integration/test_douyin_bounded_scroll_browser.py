from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, cast

import pytest

from automation_tool.executor.browser_runtime import BrowserLaunchRequest, BrowserRuntime
from automation_tool.executor.rpa.douyin.bounded_scroll import (
    DouyinBoundedScroll,
    DouyinBoundedScrollEvidence,
    DouyinBoundedScrollState,
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
RESULT_DOCUMENT = (FIXTURE_ROOT / "results-infinite-scroll.html").read_text(encoding="utf-8")


def test_production_search_then_bounded_scroll_uses_headless_browser_and_closes(
    tmp_path: Path,
) -> None:
    if sys.platform != "darwin" or not MACOS_CHROME.is_file():
        pytest.skip("D6-05 system Chrome acceptance currently requires macOS Chrome")
    profile = tmp_path / "automation-tool-d6-05-profile"
    profile.mkdir(mode=0o700)
    search = DouyinSearchInput(keyword="新能源汽车", target_limit=3)
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
        scroll_observation = DouyinBoundedScroll(
            window,
            search,
            search_observation,
            lambda: False,
        ).run()

        assert scroll_observation.state is DouyinBoundedScrollState.COMPLETED
        assert scroll_observation.evidence is DouyinBoundedScrollEvidence.TARGET_LIMIT_REACHED
        assert scroll_observation.rounds_completed == 2
        assert scroll_observation.target_count == 3
        assert page.url == expected_url

    assert not runtime.is_running
    assert os.stat(profile).st_mode & 0o777 == 0o700
