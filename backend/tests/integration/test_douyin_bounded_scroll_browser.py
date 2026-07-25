from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from conftest import assert_private_profile_directory, create_private_profile_directory

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

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "douyin_discovery_pages"
HOME_DOCUMENT = (FIXTURE_ROOT / "home.html").read_text(encoding="utf-8")
RESULT_DOCUMENT = (FIXTURE_ROOT / "results-infinite-scroll.html").read_text(encoding="utf-8")


def test_production_search_then_bounded_scroll_uses_headless_browser_and_closes(
    tmp_path: Path, staged_embedded_chromium: Path,
) -> None:
    profile = tmp_path / "automation-tool-d6-05-profile"
    create_private_profile_directory(profile)
    search = DouyinSearchInput(keyword="新能源汽车", target_limit=3)
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
    assert_private_profile_directory(profile)
