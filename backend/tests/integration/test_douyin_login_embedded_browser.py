"""EB-11: the Douyin login and session chain on the embedded Chromium.

Drives the production ``DouyinQrLoginFlow`` and ``DouyinSessionDetector``
against the digest-verified staged embedded Chromium (never a system
browser) inside a fresh EB-09-style private profile, using the self-built
fixture pages. The real Douyin QR scan itself needs a controlled account and
stays a pending real-account acceptance item in the task ledger.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, cast

from automation_tool.executor.browser_runtime import BrowserLaunchRequest, BrowserRuntime
from automation_tool.executor.rpa.douyin.login import (
    DouyinQrLoginEvidence,
    DouyinQrLoginFlow,
    DouyinQrLoginState,
)
from automation_tool.executor.rpa.douyin.session import (
    DouyinSessionDetector,
    DouyinSessionEvidence,
    DouyinSessionState,
)

BACKEND_ROOT = Path(__file__).resolve().parents[2]
LOGIN_FIXTURE = BACKEND_ROOT / "tests/fixtures/douyin_qr_login_states.html"
SESSION_FIXTURE = BACKEND_ROOT / "tests/fixtures/douyin_session_states.html"
PROBE_URL_PATTERN = "https://www.douyin.com/user/self*"


def _private_profile(tmp_path: Path) -> Path:
    profile = tmp_path / "automation-tool-eb-11-profile"
    profile.mkdir(mode=0o700)
    return profile


def _launch(executable: Path, profile: Path) -> BrowserLaunchRequest:
    return BrowserLaunchRequest(
        executable_path=executable,
        profile_directory=profile,
        headless=True,
    )


def test_embedded_chromium_runs_the_complete_qr_flow_states(
    tmp_path: Path, staged_embedded_chromium: Path
) -> None:
    profile = _private_profile(tmp_path)
    fixture = LOGIN_FIXTURE.read_text(encoding="utf-8")
    runtime = BrowserRuntime()
    with runtime.running(_launch(staged_embedded_chromium, profile)):
        page = cast(Any, runtime.primary_window().playwright_page)
        page.context.route(
            PROBE_URL_PATTERN,
            lambda route: route.fulfill(
                status=200, content_type="text/html", body=fixture
            ),
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
        handoffs = []
        for state in ("captcha", "slider", "risk"):
            login_page.evaluate("state => window.setState(state)", state)
            handoffs.append(flow.recheck())

        assert (awaiting_scan.state, awaiting_scan.evidence) == (
            DouyinQrLoginState.AWAITING_SCAN,
            DouyinQrLoginEvidence.QR_VISIBLE,
        )
        assert awaiting_confirmation.state is DouyinQrLoginState.AWAITING_CONFIRMATION
        assert healthy.state is DouyinQrLoginState.HEALTHY
        assert not healthy.circuit_open
        assert expired.state is DouyinQrLoginState.QR_EXPIRED
        # Captcha, slider and risk challenges always hand off to a human; never bypassed.
        assert all(
            handoff.state is DouyinQrLoginState.HANDOFF_REQUIRED
            and handoff.evidence is DouyinQrLoginEvidence.RISK_CHALLENGE
            and handoff.circuit_open
            for handoff in handoffs
        )
        flow.close()
        assert len(runtime.windows()) == 1
    assert not runtime.is_running
    assert os.stat(profile).st_mode & 0o777 == 0o700


def test_embedded_chromium_probes_session_health_and_invalidation(
    tmp_path: Path, staged_embedded_chromium: Path
) -> None:
    profile = _private_profile(tmp_path)
    fixture = SESSION_FIXTURE.read_text(encoding="utf-8")
    runtime = BrowserRuntime()
    with runtime.running(_launch(staged_embedded_chromium, profile)):
        window = runtime.primary_window()
        page = cast(Any, window.playwright_page)
        page.route(
            "https://www.douyin.com/automation-tool-eb-11-fixture*",
            lambda route: route.fulfill(
                status=200, content_type="text/html", body=fixture
            ),
        )
        expected = {
            "healthy": (
                DouyinSessionState.HEALTHY,
                DouyinSessionEvidence.AUTHENTICATED_SHELL,
            ),
            # Login expiry must surface explicitly for human takeover; never replayed silently.
            "expired": (DouyinSessionState.EXPIRED, DouyinSessionEvidence.LOGIN_EXPIRED),
            "missing": (DouyinSessionState.MISSING, DouyinSessionEvidence.LOGIN_ENTRY),
            "risk": (DouyinSessionState.RISK, DouyinSessionEvidence.RISK_CHALLENGE),
        }
        for state, result in expected.items():
            page.goto(
                f"https://www.douyin.com/automation-tool-eb-11-fixture?state={state}",
                wait_until="domcontentloaded",
                timeout=30_000,
            )
            observation = DouyinSessionDetector().check(window)
            assert (observation.state, observation.evidence) == result
    assert not runtime.is_running


_STATE_PAGE_URL = "https://www.douyin.com/automation-tool-eb-11-state"
_STATE_PAGE_BODY = "<!doctype html><title>eb-11</title>"


def _open_state_page(runtime: BrowserRuntime) -> Any:
    page = cast(Any, runtime.primary_window().playwright_page)
    page.route(
        f"{_STATE_PAGE_URL}*",
        lambda route: route.fulfill(
            status=200, content_type="text/html", body=_STATE_PAGE_BODY
        ),
    )
    page.goto(_STATE_PAGE_URL, wait_until="domcontentloaded", timeout=30_000)
    return page


def test_embedded_profile_restart_reuses_the_persisted_login_state(
    tmp_path: Path, staged_embedded_chromium: Path
) -> None:
    """Restart reuse: login-state evidence persists across two launches of one profile."""
    profile = _private_profile(tmp_path)
    marker = "automation-tool-eb11-session"
    runtime = BrowserRuntime()
    with runtime.running(_launch(staged_embedded_chromium, profile)):
        page = _open_state_page(runtime)
        page.evaluate(
            "marker => window.localStorage.setItem('eb11', marker)", marker
        )
    assert not runtime.is_running

    second = BrowserRuntime()
    with second.running(_launch(staged_embedded_chromium, profile)):
        page = _open_state_page(second)
        persisted = page.evaluate("window.localStorage.getItem('eb11')")
        assert persisted == marker, "profile restart must reuse persisted state"
    assert not second.is_running
    assert os.stat(profile).st_mode & 0o777 == 0o700


def test_logout_clears_the_profile_session_evidence(
    tmp_path: Path, staged_embedded_chromium: Path
) -> None:
    """Logout: after clearing session evidence a restart must not reuse the old state."""
    profile = _private_profile(tmp_path)
    runtime = BrowserRuntime()
    with runtime.running(_launch(staged_embedded_chromium, profile)):
        page = _open_state_page(runtime)
        page.evaluate("window.localStorage.setItem('eb11', 'logged-in')")
        page.context.clear_cookies()
        page.evaluate("window.localStorage.clear()")
    assert not runtime.is_running

    second = BrowserRuntime()
    with second.running(_launch(staged_embedded_chromium, profile)):
        page = _open_state_page(second)
        assert page.evaluate("window.localStorage.getItem('eb11')") is None
    assert not second.is_running


def test_production_login_command_surface_drives_the_embedded_chromium(
    tmp_path: Path, staged_embedded_chromium: Path
) -> None:
    """Production command surface: open/recheck/logout run on the embedded Chromium."""
    import json
    from queue import Queue

    from pydantic import SecretStr

    from automation_tool.executor.authentication import LocalSessionAuthenticator
    from automation_tool.executor.browser_authority import BrowserLaunchAuthority
    from automation_tool.executor.ledger import ExecutorLedger
    from automation_tool.executor.platform_commands import (
        DouyinLoginCommandOperation,
        PlatformCommand,
    )
    from automation_tool.executor.rpa.douyin.health import DouyinSessionHealthReporter

    token = "".join(f"{value:02x}" for value in range(32))
    command_id = "123e4567-e89b-42d3-a456-426614174005"
    authenticator = LocalSessionAuthenticator(SecretStr(token))
    profile = _private_profile(tmp_path)
    fixture = LOGIN_FIXTURE.read_text(encoding="utf-8")
    runtimes: list[BrowserRuntime] = []

    class RoutingRuntime(BrowserRuntime):
        def start(self, request: BrowserLaunchRequest) -> None:
            super().start(request)
            page = cast(Any, self.primary_window().playwright_page)
            page.context.route(
                PROBE_URL_PATTERN,
                lambda route: route.fulfill(
                    status=200, content_type="text/html", body=fixture
                ),
            )

    def runtime_factory() -> BrowserRuntime:
        runtime = RoutingRuntime()
        runtimes.append(runtime)
        return runtime

    outbound: Queue[object] = Queue()
    operation = DouyinLoginCommandOperation(
        health_reporter=DouyinSessionHealthReporter(
            ledger=ExecutorLedger(
                state_directory=tmp_path / "ledger",
                installation_id="123e4567-e89b-42d3-a456-426614174003",
                executor_id="123e4567-e89b-42d3-a456-426614174004",
            )
        ),
        outbound=outbound,
        runtime_factory=runtime_factory,
        browser_authority=BrowserLaunchAuthority(),
    )

    def login_command(command_type: str) -> PlatformCommand:
        return PlatformCommand.model_validate(
            {
                "authenticationProof": authenticator.proof_for_command(
                    command_id=command_id,
                    command_type=command_type,
                    executable_path=str(staged_embedded_chromium),
                    profile_directory=str(profile),
                    headless=True,
                ),
                "commandId": command_id,
                "commandType": command_type,
                "executablePath": str(staged_embedded_chromium),
                "headless": True,
                "profileDirectory": str(profile),
                "protocolVersion": "1.0",
            }
        )

    try:
        assert operation.handle(login_command("douyin.login.open")) == "awaiting_scan"
        login_page = cast(Any, runtimes[-1].windows()[-1].playwright_page)
        login_page.evaluate("window.setState('healthy')")
        assert operation.handle(login_command("douyin.login.recheck")) == "healthy"
        logout = PlatformCommand.model_validate(
            {
                "authenticationProof": authenticator.proof_for_session_command(
                    command_id=command_id,
                    command_type="douyin.logout.complete",
                ),
                "commandId": command_id,
                "commandType": "douyin.logout.complete",
                "protocolVersion": "1.0",
            }
        )
        assert operation.handle(logout) == "logged_out"
    finally:
        operation.close()

    envelopes = []
    while not outbound.empty():
        envelopes.append(outbound.get_nowait())
    assert len(envelopes) == 3, "open + recheck + logout must each report health"
    def dump(envelope: object) -> object:
        model_dump = getattr(envelope, "model_dump", None)
        return model_dump() if callable(model_dump) else str(envelope)

    serialized = json.dumps(
        [dump(envelope) for envelope in envelopes], default=str, ensure_ascii=False
    )
    assert str(profile) not in serialized, "profile path must never enter envelopes"
    assert str(staged_embedded_chromium) not in serialized
    assert not runtimes[-1].is_running
