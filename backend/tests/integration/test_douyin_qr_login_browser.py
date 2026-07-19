from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, cast

import pytest

from automation_tool.executor.browser_runtime import BrowserLaunchRequest, BrowserRuntime
from automation_tool.executor.rpa.douyin.login import (
    DouyinQrLoginEvidence,
    DouyinQrLoginFlow,
    DouyinQrLoginState,
)

BACKEND_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = BACKEND_ROOT / "tests/fixtures/douyin_qr_login_states.html"
MACOS_CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


def require_macos_chrome() -> None:
    if sys.platform != "darwin" or not MACOS_CHROME.is_file():
        pytest.skip("B5-10 system browser acceptance currently requires macOS Chrome")


def test_real_system_chrome_uses_one_dedicated_window_for_the_complete_qr_flow(
    tmp_path: Path,
) -> None:
    require_macos_chrome()
    profile = tmp_path / "automation-tool-b5-10-profile"
    profile.mkdir(mode=0o700)
    fixture = FIXTURE.read_text(encoding="utf-8")
    runtime = BrowserRuntime()

    with runtime.running(
        BrowserLaunchRequest(
            executable_path=MACOS_CHROME,
            profile_directory=profile,
        )
    ):
        initial_page = cast(Any, runtime.primary_window().playwright_page)
        initial_page.context.route(
            "https://www.douyin.com/user/self*",
            lambda route: route.fulfill(status=200, content_type="text/html", body=fixture),
        )
        flow = DouyinQrLoginFlow(runtime)

        awaiting_scan = flow.begin()
        login_page = cast(Any, runtime.windows()[-1].playwright_page)
        login_page.evaluate("window.setState('confirmation')")
        awaiting_confirmation = flow.recheck()
        login_page.evaluate("window.setState('healthy')")
        healthy = flow.recheck()
        login_page.evaluate("window.setState('qr-expired')")
        expired = flow.recheck()
        login_page.evaluate("window.setState('conflicting')")
        conflicting = flow.recheck()
        login_page.evaluate("window.setState('risk')")
        risk = flow.recheck()

        assert len(runtime.windows()) == 2
        assert (awaiting_scan.state, awaiting_scan.evidence) == (
            DouyinQrLoginState.AWAITING_SCAN,
            DouyinQrLoginEvidence.QR_VISIBLE,
        )
        assert awaiting_confirmation.state is DouyinQrLoginState.AWAITING_CONFIRMATION
        assert healthy.state is DouyinQrLoginState.HEALTHY
        assert not healthy.circuit_open
        assert expired.state is DouyinQrLoginState.QR_EXPIRED
        assert conflicting.state is DouyinQrLoginState.UNKNOWN
        assert risk.state is DouyinQrLoginState.RISK
        assert all(
            observation.circuit_open
            for observation in (
                awaiting_scan,
                awaiting_confirmation,
                expired,
                conflicting,
                risk,
            )
        )

        flow.close()
        assert len(runtime.windows()) == 1

    assert not runtime.is_running
    assert os.stat(profile).st_mode & 0o777 == 0o700


@pytest.mark.skipif(
    os.environ.get("AUTOMATION_TOOL_B510_LIVE") != "1",
    reason="explicit read-only live Douyin QR acceptance",
)
def test_live_blank_profile_opens_the_real_douyin_qr_panel(tmp_path: Path) -> None:
    require_macos_chrome()
    profile = tmp_path / "automation-tool-b5-10-live-profile"
    profile.mkdir(mode=0o700)
    runtime = BrowserRuntime()

    with runtime.running(
        BrowserLaunchRequest(
            executable_path=MACOS_CHROME,
            profile_directory=profile,
        )
    ):
        flow = DouyinQrLoginFlow(runtime)
        observation = flow.begin()

        assert observation.state is DouyinQrLoginState.AWAITING_SCAN
        assert observation.evidence is DouyinQrLoginEvidence.QR_VISIBLE
        assert observation.circuit_open
        flow.close()

    assert not runtime.is_running


@pytest.mark.skipif(
    not os.environ.get("AUTOMATION_TOOL_B510_REAL_PROFILE"),
    reason="explicit user-authorized persistent Profile acceptance",
)
def test_user_authorized_persistent_profile_reuses_the_real_login() -> None:
    require_macos_chrome()
    profile = Path(os.environ["AUTOMATION_TOOL_B510_REAL_PROFILE"])
    runtime = BrowserRuntime()

    with runtime.running(
        BrowserLaunchRequest(
            executable_path=MACOS_CHROME,
            profile_directory=profile,
        )
    ):
        flow = DouyinQrLoginFlow(runtime)
        observation = flow.begin()

        assert observation.state is DouyinQrLoginState.HEALTHY
        assert observation.evidence is DouyinQrLoginEvidence.SESSION_HEALTHY
        assert not observation.circuit_open
        flow.close()

    assert not runtime.is_running
