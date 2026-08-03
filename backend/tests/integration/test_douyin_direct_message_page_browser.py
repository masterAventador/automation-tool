from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from conftest import assert_private_profile_directory, create_private_profile_directory

from automation_tool.executor.browser_runtime import BrowserLaunchRequest, BrowserRuntime
from automation_tool.executor.rpa.douyin.direct_message_page import (
    DouyinDirectMessagePage,
    DouyinDirectMessagePageEvidence,
    DouyinDirectMessagePageState,
)

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "douyin_direct_message_pages"
ACTION_URL = "https://www.douyin.com/user/creator-001"
PERMISSION_URL = "https://www.douyin.com/user/creator-002"
DRIFT_URL = "https://www.douyin.com/user/creator-003"


def test_production_direct_message_page_uses_headless_fake_pages_and_closes(
    tmp_path: Path,
    staged_embedded_chromium: Path,
) -> None:
    profile = tmp_path / "automation-tool-a7-09-profile"
    create_private_profile_directory(profile)
    documents = {
        ACTION_URL: (FIXTURE_ROOT / "message-action.html").read_text(encoding="utf-8"),
        PERMISSION_URL: (FIXTURE_ROOT / "message-permission.html").read_text(encoding="utf-8"),
        DRIFT_URL: (FIXTURE_ROOT / "message-drift.html").read_text(encoding="utf-8"),
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

        page.route("https://www.douyin.com/user/**", fulfill)
        page.goto(ACTION_URL, wait_until="domcontentloaded")
        message_page = DouyinDirectMessagePage(window)
        assert message_page.wait_for_profile_ready(timeout_milliseconds=1_000).profile_ready
        cast(Any, message_page.enter_conversation()).click()
        assert message_page.wait_for_conversation_ready(
            timeout_milliseconds=1_000
        ).conversation_ready
        cast(Any, message_page.message_input()).fill("A7-09 隔离页面私信")
        cast(Any, message_page.message_send()).click()
        final = message_page.wait_for_final(timeout_milliseconds=1_000)
        assert final.state is DouyinDirectMessagePageState.CONFIRMED
        assert final.evidence is DouyinDirectMessagePageEvidence.FINAL_CONFIRMATION_VISIBLE
        assert cast(Any, message_page.final_confirmation()).is_visible()
        assert "隔离页面私信" not in repr(final)

        page.goto(PERMISSION_URL, wait_until="domcontentloaded")
        permission = DouyinDirectMessagePage(window).observe()
        assert permission.state is DouyinDirectMessagePageState.PERMISSION_DENIED
        assert permission.evidence is DouyinDirectMessagePageEvidence.FOLLOW_REQUIRED

        page.goto(DRIFT_URL, wait_until="domcontentloaded")
        drift = DouyinDirectMessagePage(window).observe()
        assert drift.state is DouyinDirectMessagePageState.UNKNOWN
        assert drift.evidence is DouyinDirectMessagePageEvidence.CONFLICTING_ANCHORS

    assert not runtime.is_running
    assert_private_profile_directory(profile)


def test_direct_message_fake_page_corpus_is_closed_and_local() -> None:
    expected = {"message-action.html", "message-permission.html", "message-drift.html"}
    assert {path.name for path in FIXTURE_ROOT.iterdir()} == expected
    for path in FIXTURE_ROOT.iterdir():
        source = path.read_text(encoding="utf-8")
        assert 1 <= len(source.encode("utf-8")) <= 8 * 1024
        assert "http://" not in source
        assert "https://" not in source
        assert "fetch(" not in source.lower()
