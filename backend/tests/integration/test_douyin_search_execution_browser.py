from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from conftest import assert_private_profile_directory, create_private_profile_directory

from automation_tool.executor.browser_runtime import BrowserLaunchRequest, BrowserRuntime
from automation_tool.executor.rpa.douyin.page_version import (
    DOUYIN_HOME_URL,
    douyin_search_results_url,
)
from automation_tool.executor.rpa.douyin.search import (
    DouyinSearchExecution,
    DouyinSearchExecutionEvidence,
    DouyinSearchExecutionState,
)
from automation_tool.protocol import DouyinSearchInput

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "douyin_discovery_pages"
HOME_DOCUMENT = (FIXTURE_ROOT / "home.html").read_text(encoding="utf-8")
RESULT_DOCUMENT = (FIXTURE_ROOT / "results-normal.html").read_text(encoding="utf-8")


def test_production_browser_runtime_executes_search_headlessly_and_closes(
    tmp_path: Path, staged_embedded_chromium: Path,
) -> None:
    profile = tmp_path / "automation-tool-d6-04-profile"
    create_private_profile_directory(profile)
    search = DouyinSearchInput(keyword="新能源汽车", target_limit=20)
    expected_url = douyin_search_results_url(search.keyword)
    runtime = BrowserRuntime()

    with runtime.running(
        BrowserLaunchRequest(
            executable_path=staged_embedded_chromium,
            profile_directory=profile,
            headless=True,
        )
    ):
        window = runtime.primary_window()
        page = cast(Any, window.playwright_page)
        routed_urls: list[str] = []

        def fulfill_official_page(route: Any) -> None:
            url = route.request.url
            routed_urls.append(url)
            if url == DOUYIN_HOME_URL:
                route.fulfill(status=200, content_type="text/html", body=HOME_DOCUMENT)
            elif url == expected_url:
                route.fulfill(status=200, content_type="text/html", body=RESULT_DOCUMENT)
            else:
                route.fulfill(status=404, content_type="text/plain", body="not found")

        page.route(DOUYIN_HOME_URL, fulfill_official_page)
        page.route(expected_url, fulfill_official_page)
        observation = DouyinSearchExecution(window, search).run()

        assert observation.state is DouyinSearchExecutionState.SUCCEEDED
        assert observation.evidence is DouyinSearchExecutionEvidence.RESULTS_READY
        assert page.url == expected_url
        assert routed_urls == [DOUYIN_HOME_URL, expected_url]

    assert not runtime.is_running
    assert_private_profile_directory(profile)
