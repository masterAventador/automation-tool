from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, cast

import pytest

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

MACOS_CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
HOME_DOCUMENT = """<!doctype html>
<html lang="zh-CN">
  <head><meta charset="utf-8" /></head>
  <body>
    <input aria-label="搜索" />
    <button aria-label="搜索" onclick="runSearch()">搜索</button>
    <script>
      function runSearch() {
        const keyword = document.querySelector('input[aria-label="搜索"]').value;
        window.location.href = `/search/${encodeURIComponent(keyword)}?type=general`;
      }
    </script>
  </body>
</html>
"""
RESULT_DOCUMENT = """<!doctype html>
<html lang="zh-CN"><body><main role="feed">确定性搜索结果</main></body></html>
"""


def test_production_browser_runtime_executes_search_headlessly_and_closes(
    tmp_path: Path,
) -> None:
    if sys.platform != "darwin" or not MACOS_CHROME.is_file():
        pytest.skip("D6-04 system Chrome acceptance currently requires macOS Chrome")
    profile = tmp_path / "automation-tool-d6-04-profile"
    profile.mkdir(mode=0o700)
    search = DouyinSearchInput(keyword="新能源汽车", target_limit=20)
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
    assert os.stat(profile).st_mode & 0o777 == 0o700
