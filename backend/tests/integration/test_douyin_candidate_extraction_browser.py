from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

from conftest import assert_private_profile_directory, create_private_profile_directory

from automation_tool.executor.browser_runtime import BrowserLaunchRequest, BrowserRuntime
from automation_tool.executor.rpa.douyin.candidate_extraction import (
    DouyinCandidateExtraction,
    DouyinCandidateExtractionEvidence,
    DouyinCandidateExtractionState,
)
from automation_tool.executor.rpa.douyin.page_anchors import (
    unique_visible_in_snapshot,
    visible_matches,
)
from automation_tool.executor.rpa.douyin.page_version import (
    DOUYIN_HOME_URL,
    douyin_search_results_url,
)
from automation_tool.executor.rpa.douyin.search import DouyinSearchExecution
from automation_tool.protocol import DouyinSearchInput

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "douyin_discovery_pages"
HOME_DOCUMENT = (FIXTURE_ROOT / "home.html").read_text(encoding="utf-8")
RESULT_DOCUMENT = (FIXTURE_ROOT / "results-normal.html").read_text(encoding="utf-8")
LAZY_DOCUMENT = (FIXTURE_ROOT / "results-lazy-rendering.html").read_text(encoding="utf-8")
TWO_AUTHOR_DOCUMENT = (FIXTURE_ROOT / "results-two-visible-authors.html").read_text(
    encoding="utf-8"
)
ROW_SELECTOR = '[role="feed"] > article'
AUTHOR_SELECTORS = ('[data-e2e="search-result-author"]',)
FIELD_TIMEOUT_MILLISECONDS = 3_000


def route_official_pages(page: Any, *, results: str, results_url: str) -> None:
    """Serve the official home and result routes from local fixtures only."""

    def fulfill(route: Any) -> None:
        document = HOME_DOCUMENT if route.request.url == DOUYIN_HOME_URL else results
        route.fulfill(status=200, content_type="text/html", body=document)

    page.route(DOUYIN_HOME_URL, fulfill)
    page.route(results_url, fulfill)


def test_production_search_then_candidate_privacy_boundary_uses_headless_browser_and_closes(
    tmp_path: Path,
    staged_embedded_chromium: Path,
) -> None:
    profile = tmp_path / "automation-tool-d6-07-profile"
    create_private_profile_directory(profile)
    search = DouyinSearchInput(keyword="新能源汽车", target_limit=2)
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
    assert_private_profile_directory(profile)


def test_lazy_rendering_placeholders_inside_a_pinned_row_still_read_the_real_author(
    tmp_path: Path,
    staged_embedded_chromium: Path,
) -> None:
    """Pinning a row must not lose the visible filter that hides template nodes.

    A row snapshot has no ``locator``, so its fields are resolved through
    ``query_selector_all`` with the visible engine chained on. Only a real
    browser can show that the chained form filters the same way: a hidden
    skeleton row above the card must not be counted, and the card's own hidden
    author and name placeholders must not look like the real ones.
    """
    profile = tmp_path / "automation-tool-lazy-profile"
    create_private_profile_directory(profile)
    search = DouyinSearchInput(keyword="新能源汽车", target_limit=2)
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
        route_official_pages(
            page,
            results=LAZY_DOCUMENT,
            results_url=douyin_search_results_url(search.keyword),
        )
        assert DouyinSearchExecution(window, search).run().succeeded is True

        observation = DouyinCandidateExtraction(
            window,
            maximum=search.target_limit,
            page_revision=12,
        ).run()

        assert observation.state is DouyinCandidateExtractionState.COMPLETED
        assert observation.evidence is DouyinCandidateExtractionEvidence.CANDIDATES_EXTRACTED
        assert observation.candidate_count == 1
        candidate = observation.candidates[0]
        assert (candidate.platform_target_id, candidate.summary.display_name) == (
            "creator-001",
            "创作者甲",
        )
        assert candidate.summary.public_handle == "creator.one"
        assert "avatar-secret" not in repr(asdict(observation))

    assert not runtime.is_running


def test_two_visible_authors_in_one_real_card_stop_the_whole_read(
    tmp_path: Path,
    staged_embedded_chromium: Path,
) -> None:
    """Two creators on one card leave no way to say who an action would reach."""
    profile = tmp_path / "automation-tool-two-author-profile"
    create_private_profile_directory(profile)
    search = DouyinSearchInput(keyword="新能源汽车", target_limit=2)
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
        route_official_pages(
            page,
            results=TWO_AUTHOR_DOCUMENT,
            results_url=douyin_search_results_url(search.keyword),
        )
        assert DouyinSearchExecution(window, search).run().succeeded is True

        observation = DouyinCandidateExtraction(
            window,
            maximum=search.target_limit,
            page_revision=12,
        ).run()

        assert observation.state is DouyinCandidateExtractionState.UNKNOWN
        assert observation.evidence is DouyinCandidateExtractionEvidence.PAGE_UNAVAILABLE
        assert observation.candidates == ()

    assert not runtime.is_running


def test_a_pinned_row_keeps_its_own_fields_when_the_feed_reveals_a_row_above_it(
    tmp_path: Path,
    staged_embedded_chromium: Path,
) -> None:
    """The browser behaviour the candidate reader rests on, checked directly.

    A locator re-runs its selector on every read, so revealing a skeleton row
    moves ``nth(0)`` onto a different card mid-read. The fix assumes a row
    snapshot does not follow the feed, and that its fields keep resolving inside
    itself. Neither holds by inspection, and no page script can prove it either:
    Playwright evaluates these reads in an isolated world, where a patched
    ``HTMLElement.prototype`` in the page is not visible. So it is asserted here
    against the embedded Chromium instead.
    """
    profile = tmp_path / "automation-tool-pinned-row-profile"
    create_private_profile_directory(profile)
    results_url = douyin_search_results_url("新能源汽车")
    runtime = BrowserRuntime()

    with runtime.running(
        BrowserLaunchRequest(
            executable_path=staged_embedded_chromium,
            profile_directory=profile,
            headless=True,
        )
    ):
        page = cast(Any, runtime.primary_window().playwright_page)
        route_official_pages(page, results=LAZY_DOCUMENT, results_url=results_url)
        page.goto(results_url)

        rows = cast(Any, visible_matches(page, ROW_SELECTOR))
        assert rows.count() == 1
        pinned = rows.nth(0).element_handle(timeout=FIELD_TIMEOUT_MILLISECONDS)
        try:
            page.evaluate("() => document.querySelector('#skeleton-row').removeAttribute('hidden')")
            assert rows.count() == 2
            assert rows.nth(0).get_attribute("id") == "skeleton-row"

            author = cast(Any, unique_visible_in_snapshot(pinned, AUTHOR_SELECTORS))
            assert author is not None
            try:
                assert pinned.get_attribute("id") == "loaded-row"
                assert author.get_attribute("data-user-id") == "creator-001"
            finally:
                author.dispose()
        finally:
            pinned.dispose()

    assert not runtime.is_running
