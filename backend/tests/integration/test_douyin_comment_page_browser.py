from __future__ import annotations

import os
from pathlib import Path
from typing import Any, cast

from automation_tool.executor.browser_runtime import BrowserLaunchRequest, BrowserRuntime
from automation_tool.executor.rpa.douyin.comment_page import (
    DouyinCommentPage,
    DouyinCommentPageEvidence,
    DouyinCommentPageState,
)

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "douyin_comment_pages"
ACTION_URL = "https://www.douyin.com/video/7351234567890123456"
BLOCKED_URL = "https://www.douyin.com/video/7351234567890123457"
DRIFT_URL = "https://www.douyin.com/video/7351234567890123458"


def test_production_comment_page_uses_headless_fake_pages_and_closes(
    tmp_path: Path, staged_embedded_chromium: Path,
) -> None:
    profile = tmp_path / "automation-tool-a7-08-profile"
    profile.mkdir(mode=0o700)
    documents = {
        ACTION_URL: (FIXTURE_ROOT / "comment-action.html").read_text(encoding="utf-8"),
        BLOCKED_URL: (FIXTURE_ROOT / "comment-blocked.html").read_text(encoding="utf-8"),
        DRIFT_URL: (FIXTURE_ROOT / "comment-drift.html").read_text(encoding="utf-8"),
    }
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

        def fulfill(route: Any) -> None:
            document = documents.get(route.request.url)
            if document is None:
                route.fulfill(status=404, content_type="text/plain", body="not found")
                return
            route.fulfill(status=200, content_type="text/html", body=document)

        page.route("https://www.douyin.com/video/**", fulfill)
        page.goto(ACTION_URL, wait_until="domcontentloaded")
        comment_page = DouyinCommentPage(window)
        assert comment_page.wait_for_ready(timeout_milliseconds=1_000).ready is True
        cast(Any, comment_page.comment_input()).fill("A7-08 隔离页面评论")
        cast(Any, comment_page.comment_submit()).click()
        final = comment_page.wait_for_final(timeout_milliseconds=1_000)
        assert final.state is DouyinCommentPageState.CONFIRMED
        assert final.evidence is DouyinCommentPageEvidence.FINAL_CONFIRMATION_VISIBLE
        assert cast(Any, comment_page.final_confirmation()).is_visible()
        assert "隔离页面评论" not in repr(final)

        page.goto(BLOCKED_URL, wait_until="domcontentloaded")
        blocked = DouyinCommentPage(window).observe()
        assert blocked.state is DouyinCommentPageState.DIALOG_BLOCKED
        assert blocked.circuit_open is True

        page.goto(DRIFT_URL, wait_until="domcontentloaded")
        drifted = DouyinCommentPage(window).observe()
        assert drifted.state is DouyinCommentPageState.UNKNOWN
        assert drifted.evidence is DouyinCommentPageEvidence.CONFLICTING_ANCHORS

    assert not runtime.is_running
    assert os.stat(profile).st_mode & 0o777 == 0o700


def test_comment_fake_page_corpus_is_closed_and_local() -> None:
    expected = {"comment-action.html", "comment-blocked.html", "comment-drift.html"}
    assert {path.name for path in FIXTURE_ROOT.iterdir()} == expected
    for path in FIXTURE_ROOT.iterdir():
        source = path.read_text(encoding="utf-8")
        assert 1 <= len(source.encode("utf-8")) <= 8 * 1024
        assert "http://" not in source
        assert "https://" not in source
        assert "fetch(" not in source.lower()
