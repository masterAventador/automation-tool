"""PB-05: the production publish command surface on the embedded Chromium.

Every element of this chain is the real one: the digest-verified staged
embedded Chromium process, the real Playwright driver, a fresh 0o700 private
operations profile, the real ``BrowserLaunchAuthority``, the real
``BrowserSurfaceLeaseManager`` and the real ``PlatformCommandWorker``
reading authenticated stdin command frames and
writing authenticated result frames. The only substitution is the Douyin
publish page itself, which is served from the self-built fixture because this
machine has no controlled Douyin account; that acceptance item stays pending
in the task ledger.
"""

from __future__ import annotations

import io
import json
import threading
from collections.abc import Iterator
from contextlib import suppress
from pathlib import Path
from queue import Queue
from typing import Any, cast

import pytest
from conftest import (
    assert_private_profile_directory,
    create_private_profile_directory,
    process_ids_matching,
    terminate_process,
)
from pydantic import SecretStr

from automation_tool.executor.authentication import LocalSessionAuthenticator
from automation_tool.executor.browser_authority import BrowserLaunchAuthority
from automation_tool.executor.browser_runtime import (
    BrowserLaunchRequest,
    BrowserRuntime,
    BrowserRuntimeRejected,
)
from automation_tool.executor.browser_surface_lease import BrowserSurfaceLeaseManager
from automation_tool.executor.cli import build_platform_command_router
from automation_tool.executor.ledger import ExecutorLedger
from automation_tool.executor.platform_commands import (
    DOUYIN_PUBLISH_PREFLIGHT_COMMAND,
    DOUYIN_PUBLISH_RELEASE_COMMAND,
    DouyinPublishPreflightCommandOperation,
    PlatformCommandRouter,
    PlatformCommandWorker,
)
from automation_tool.executor.rpa.douyin.publish_page import (
    DOUYIN_PUBLISH_ENTRY_URL,
    DOUYIN_PUBLISH_HOST,
)
from automation_tool.executor.rpa.douyin.publish_preflight import (
    DOUYIN_PUBLISH_PREFLIGHT_FLOW_VERSION,
    DouyinPublishPreflightEvidence,
    DouyinPublishPreflightState,
)

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PUBLISH_FIXTURE = BACKEND_ROOT / "tests/fixtures/douyin_publish_states.html"
PUBLISH_ROUTE_PATTERN = f"https://{DOUYIN_PUBLISH_HOST}/creator-micro/content/**"
CHALLENGE_ROUTE_PATTERN = "https://rmc.bytedance.com/**"
TOKEN = "".join(f"{value:02x}" for value in range(32))
TITLE = "自动化运营工具发布前测试标题"
DESCRIPTION = "自动化运营工具发布前测试简介 停在提交前"
ARTIFACT_PAYLOAD = b"\x00\x00\x00\x18ftypmp42automation-tool-pb-05-embedded-fixture"


class _PublishHarness:
    """The real command surface with only the publish page served from a fixture."""

    def __init__(self, tmp_path: Path, executable: Path) -> None:
        self.executable = executable
        self.profile = create_private_profile_directory(tmp_path / "automation-tool-pb-05-profile")
        self.artifact = tmp_path / "automation-tool-pb-05-clip.mp4"
        self.artifact.write_bytes(ARTIFACT_PAYLOAD)
        self.artifact.chmod(0o600)
        self.page_state = "default"
        self.runtimes: list[BrowserRuntime] = []
        self.page_facts: dict[str, Any] = {}
        self.fail_next_close = False
        self.fail_next_start = False
        self.authenticator = LocalSessionAuthenticator(SecretStr(TOKEN))
        self.control_plane_outbox: Queue[object] = Queue()
        self.surface_lease = BrowserSurfaceLeaseManager()
        self.authority = BrowserLaunchAuthority()
        self.ledger = ExecutorLedger(
            state_directory=tmp_path / "ledger",
            installation_id="123e4567-e89b-42d3-a456-426614174003",
            executor_id="123e4567-e89b-42d3-a456-426614174004",
        )
        self.operation = DouyinPublishPreflightCommandOperation(
            browser_authority=self.authority,
            surface_lease=self.surface_lease,
            runtime_factory=self._runtime,
        )
        self._fixture = PUBLISH_FIXTURE.read_text(encoding="utf-8")
        self._commands: list[bytes] = []

    def _runtime(self) -> BrowserRuntime:
        harness = self

        class FixtureRoutedRuntime(BrowserRuntime):
            """The production runtime with the publish page served locally."""

            def start(self, request: BrowserLaunchRequest) -> None:
                if harness.fail_next_start:
                    # What a profile still locked by the previous Chromium does.
                    harness.fail_next_start = False
                    raise BrowserRuntimeRejected
                super().start(request)
                context = cast(Any, self.primary_window().playwright_page).context
                context.route(
                    PUBLISH_ROUTE_PATTERN,
                    lambda route: route.fulfill(
                        status=200,
                        content_type="text/html; charset=utf-8",
                        body=harness._fixture.replace("__INITIAL_STATE__", harness.page_state),
                    ),
                )
                context.route(
                    CHALLENGE_ROUTE_PATTERN,
                    lambda route: route.fulfill(
                        status=200,
                        content_type="text/html; charset=utf-8",
                        body="<!doctype html><title>challenge</title>",
                    ),
                )

            def close(self) -> None:
                # Snapshot the real page facts before the production close path runs.
                harness.page_facts = _page_facts(self)
                super().close()
                if harness.fail_next_close:
                    # Exactly what BrowserRuntime.close() raises when the context
                    # or the driver could not be shut down cleanly.
                    harness.fail_next_close = False
                    raise BrowserRuntimeRejected

        runtime = FixtureRoutedRuntime()
        self.runtimes.append(runtime)
        return runtime

    def command(self, *, command_id: str, publish_job_id: str) -> bytes:
        payload = {
            "artifactPath": str(self.artifact),
            "commandId": command_id,
            "commandType": DOUYIN_PUBLISH_PREFLIGHT_COMMAND,
            "description": DESCRIPTION,
            "executablePath": str(self.executable),
            "headless": True,
            "profileDirectory": str(self.profile),
            "protocolVersion": "1.0",
            "publishJobId": publish_job_id,
            "title": TITLE,
        }
        payload["authenticationProof"] = self.authenticator.proof_for_publish_command(
            command_id=command_id,
            command_type=DOUYIN_PUBLISH_PREFLIGHT_COMMAND,
            executable_path=str(self.executable),
            profile_directory=str(self.profile),
            headless=True,
            publish_job_id=publish_job_id,
            artifact_path=str(self.artifact),
            title=TITLE,
            description=DESCRIPTION,
        )
        return (
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
        ).encode("utf-8")

    def production_router(self) -> PlatformCommandRouter:
        """Assemble the real production router over the fixture-routed runtime."""
        return build_platform_command_router(
            ledger=self.ledger,
            browser_authority=self.authority,
            local_outbox=self.control_plane_outbox,
            runtime_factory=self._runtime,
        )

    def session_frame(self, *, command_id: str, command_type: str) -> bytes:
        payload = {
            "authenticationProof": self.authenticator.proof_for_session_command(
                command_id=command_id,
                command_type=command_type,
            ),
            "commandId": command_id,
            "commandType": command_type,
            "protocolVersion": "1.0",
        }
        return (
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
        ).encode("utf-8")

    def logout_frame(self, *, command_id: str) -> bytes:
        return self.session_frame(command_id=command_id, command_type="douyin.logout.complete")

    def release_frame(self, *, command_id: str) -> bytes:
        return self.session_frame(
            command_id=command_id, command_type=DOUYIN_PUBLISH_RELEASE_COMMAND
        )

    def run_worker(
        self,
        frames: list[bytes],
        *,
        operation: Any = None,
        before_frame: dict[int, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Drive the real worker over authenticated stdin frames.

        `before_frame` runs a callback just before the worker reads frame N,
        which is how a user action between two commands is modelled.
        """
        output = io.StringIO()
        worker = PlatformCommandWorker(
            input_stream=cast(Any, _FrameStream(frames, before_frame or {})),
            authenticator=self.authenticator,
            operation=operation or self.operation,
            result_output=output,
        )
        worker.run(threading.Event())
        return [json.loads(line) for line in output.getvalue().splitlines()]

    def close_held_browser(self) -> None:
        """Simulate the user closing the visible operations window."""
        page = cast(Any, self.runtimes[-1].primary_window().playwright_page)
        page.context.close()

    def kill_held_browser(self) -> None:
        """Simulate the operations browser dying under the executor."""
        for pid in process_ids_matching(str(self.profile)):
            terminate_process(pid)

    def page(self) -> Any:
        return cast(Any, self.runtimes[-1].primary_window().playwright_page)

    def facts(self) -> dict[str, Any]:
        return self.page_facts

    def close(self) -> None:
        for runtime in self.runtimes:
            with suppress(Exception):
                runtime.close()


class _FrameStream:
    """A stdin stream that lets a callback run between two command frames."""

    def __init__(self, frames: list[bytes], before_frame: dict[int, Any]) -> None:
        self._frames = list(frames)
        self._before_frame = before_frame
        self._index = 0

    def readline(self, _limit: int = -1) -> bytes:
        callback = self._before_frame.get(self._index)
        if callback is not None:
            callback()
        if self._index >= len(self._frames):
            return b""
        frame = self._frames[self._index]
        self._index += 1
        return frame


@pytest.fixture
def harness(tmp_path: Path, staged_embedded_chromium: Path) -> Iterator[_PublishHarness]:
    created = _PublishHarness(tmp_path, staged_embedded_chromium)
    try:
        yield created
    finally:
        created.close()
        assert not any(runtime.is_running for runtime in created.runtimes)
        assert process_ids_matching(str(created.profile)) == set()


def _page_facts(runtime: BrowserRuntime) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    try:
        page = cast(Any, runtime.primary_window().playwright_page)
        facts["url"] = page.url
        facts["submitClicks"] = page.evaluate("window.__submitClicks")
        facts["uploadedFileName"] = page.evaluate("window.__uploadedFileName")
        facts["uploadedSize"] = page.evaluate("window.__uploadedSize")
        facts["title"] = _input_value(page, '[data-e2e="publish-title-input"]')
        facts["description"] = _input_value(page, '[data-e2e="publish-description-input"]')
    except Exception:
        facts.setdefault("url", None)
    return facts


def _input_value(page: Any, selector: str) -> str | None:
    locator = page.locator(selector)
    return cast(str, locator.input_value()) if locator.count() == 1 else None


def _result(document: dict[str, Any], harness: _PublishHarness) -> str:
    """Verify the authenticated result frame and return its state."""
    expected = harness.authenticator.proof_for_command_result(
        command_id=document["commandId"],
        state=document["state"],
    )
    assert document["authenticationProof"] == expected
    assert document["platform"] == "douyin"
    assert document["flowVersion"] == DOUYIN_PUBLISH_PREFLIGHT_FLOW_VERSION
    return cast(str, document["state"])


COMMAND_ID = "123e4567-e89b-42d3-a456-426614174010"
PUBLISH_JOB_ID = "123e4567-e89b-42d3-a456-426614174011"
LOGOUT_COMMAND_ID = "123e4567-e89b-42d3-a456-426614174012"
RELEASE_COMMAND_ID = "123e4567-e89b-42d3-a456-426614174013"
SECOND_COMMAND_ID = "123e4567-e89b-42d3-a456-426614174014"
SECOND_PUBLISH_JOB_ID = "123e4567-e89b-42d3-a456-426614174015"


def test_production_command_surface_reaches_pre_submit_and_never_submits(
    harness: _PublishHarness,
) -> None:
    """The full production chain fills the publish form and stops before submission."""
    results = harness.run_worker(
        [harness.command(command_id=COMMAND_ID, publish_job_id=PUBLISH_JOB_ID)]
    )

    assert len(results) == 1
    assert _result(results[0], harness) == "publish_pre_submit_ready"

    facts = harness.facts()
    assert facts["url"] == "https://creator.douyin.com/creator-micro/content/post/video"
    assert facts["uploadedFileName"] == harness.artifact.name
    assert facts["uploadedSize"] == len(ARTIFACT_PAYLOAD)
    assert facts["title"] == TITLE
    assert facts["description"] == DESCRIPTION
    # PB-05 hard boundary: the publish control was never pressed.
    assert facts["submitClicks"] == 0

    receipt = harness.operation.latest_receipt()
    assert receipt is not None
    assert receipt.state is DouyinPublishPreflightState.PRE_SUBMIT_READY
    assert receipt.evidence is DouyinPublishPreflightEvidence.PRE_SUBMIT_CONFIRMED
    assert len(receipt.content_hash or "") == 64
    assert receipt.target_account == "自动化运营测试账号"

    serialized = repr(receipt)
    assert str(harness.profile) not in serialized
    assert str(harness.executable) not in serialized
    assert TITLE not in serialized
    assert_private_profile_directory(harness.profile)


@pytest.mark.parametrize(
    ("page_state", "expected_state", "expected_evidence"),
    [
        (
            "login-expired",
            "publish_handoff_required",
            DouyinPublishPreflightEvidence.LOGIN_REQUIRED,
        ),
        ("captcha", "publish_handoff_required", DouyinPublishPreflightEvidence.RISK_CHALLENGE),
        ("slider", "publish_handoff_required", DouyinPublishPreflightEvidence.RISK_CHALLENGE),
        ("risk", "publish_handoff_required", DouyinPublishPreflightEvidence.RISK_CHALLENGE),
        ("mask", "publish_blocked", DouyinPublishPreflightEvidence.DIALOG_BLOCKED),
        (
            "submit-locked",
            "publish_blocked",
            DouyinPublishPreflightEvidence.SUBMIT_CONTROL_DISABLED,
        ),
    ],
)
def test_production_command_surface_failure_matrix_on_the_real_browser(
    harness: _PublishHarness,
    page_state: str,
    expected_state: str,
    expected_evidence: DouyinPublishPreflightEvidence,
) -> None:
    """Login expiry, captcha/slider/risk and overlays stop the real chain, never bypassed."""
    harness.page_state = page_state
    results = harness.run_worker(
        [harness.command(command_id=COMMAND_ID, publish_job_id=PUBLISH_JOB_ID)]
    )
    assert _result(results[0], harness) == expected_state
    receipt = harness.operation.latest_receipt()
    assert receipt is not None
    assert receipt.evidence is expected_evidence
    assert receipt.content_hash is None


def test_page_revision_that_removes_the_upload_anchor_stops_the_real_chain(
    harness: _PublishHarness,
) -> None:
    """A revised publish page is reported as drift instead of being guessed around."""
    harness.page_state = "entry-drift"
    results = harness.run_worker(
        [harness.command(command_id=COMMAND_ID, publish_job_id=PUBLISH_JOB_ID)]
    )
    assert _result(results[0], harness) == "publish_blocked"
    receipt = harness.operation.latest_receipt()
    assert receipt is not None
    assert receipt.evidence is DouyinPublishPreflightEvidence.PAGE_DRIFT


def test_a_browser_use_takeover_denies_the_preflight_before_any_browser_starts(
    harness: _PublishHarness,
) -> None:
    """While the surface is leased to Browser Use the preflight refuses to act."""
    harness.surface_lease.begin_takeover(
        cdp_url="http://127.0.0.1:45123",
        timeout_seconds=60,
        pause_confirmed=True,
    )
    results = harness.run_worker(
        [harness.command(command_id=COMMAND_ID, publish_job_id=PUBLISH_JOB_ID)]
    )
    assert _result(results[0], harness) == "publish_blocked"
    receipt = harness.operation.latest_receipt()
    assert receipt is not None
    assert receipt.evidence is DouyinPublishPreflightEvidence.SURFACE_NOT_OWNED
    assert harness.runtimes == []


def test_entry_route_is_the_frozen_creator_publish_url(harness: _PublishHarness) -> None:
    """The chain only ever opens the frozen entry route in the operations profile."""
    requested: list[str] = []
    harness.page_state = "default"

    original = harness._runtime

    def instrumented() -> BrowserRuntime:
        runtime = original()
        start = runtime.start

        def wrapped(request: BrowserLaunchRequest) -> None:
            start(request)
            page = cast(Any, runtime.primary_window().playwright_page)
            page.on("request", lambda request: requested.append(request.url))

        runtime.start = wrapped  # type: ignore[method-assign]
        return runtime

    harness.operation = DouyinPublishPreflightCommandOperation(
        browser_authority=BrowserLaunchAuthority(),
        surface_lease=harness.surface_lease,
        runtime_factory=instrumented,
    )
    results = harness.run_worker(
        [harness.command(command_id=COMMAND_ID, publish_job_id=PUBLISH_JOB_ID)]
    )
    assert _result(results[0], harness) == "publish_pre_submit_ready"
    assert DOUYIN_PUBLISH_ENTRY_URL in requested
    assert all(url.startswith(f"https://{DOUYIN_PUBLISH_HOST}/") for url in requested)


@pytest.mark.parametrize(
    ("page_state", "expected_state", "expected_evidence"),
    [
        (
            "hidden-challenge-first",
            "publish_handoff_required",
            DouyinPublishPreflightEvidence.RISK_CHALLENGE,
        ),
        (
            "hidden-mask-first",
            "publish_blocked",
            DouyinPublishPreflightEvidence.DIALOG_BLOCKED,
        ),
    ],
)
def test_a_hidden_placeholder_never_hides_a_visible_challenge(
    harness: _PublishHarness,
    page_state: str,
    expected_state: str,
    expected_evidence: DouyinPublishPreflightEvidence,
) -> None:
    """A hidden first match must not mask the real, visible challenge or overlay."""
    harness.page_state = page_state
    results = harness.run_worker(
        [harness.command(command_id=COMMAND_ID, publish_job_id=PUBLISH_JOB_ID)]
    )
    assert _result(results[0], harness) == expected_state
    receipt = harness.operation.latest_receipt()
    assert receipt is not None
    assert receipt.evidence is expected_evidence
    # Nothing was uploaded or typed while the challenge was on screen.
    assert harness.facts().get("uploadedFileName") is None


def test_logout_still_works_after_a_preflight_holds_the_operations_browser(
    harness: _PublishHarness,
) -> None:
    """A held pre-submit browser must never block logout or the next login."""
    router = harness.production_router()
    results = harness.run_worker(
        [
            harness.command(command_id=COMMAND_ID, publish_job_id=PUBLISH_JOB_ID),
            harness.logout_frame(command_id=LOGOUT_COMMAND_ID),
        ],
        operation=router,
    )
    assert [_result(document, harness) for document in results[:1]] == ["publish_pre_submit_ready"]
    assert results[1]["state"] == "logged_out"
    assert not any(runtime.is_running for runtime in harness.runtimes)


def test_the_release_command_hands_the_operations_browser_back(
    harness: _PublishHarness,
) -> None:
    """The explicit release entry frees the browser and voids the pre-submit receipt."""
    results = harness.run_worker(
        [
            harness.command(command_id=COMMAND_ID, publish_job_id=PUBLISH_JOB_ID),
            harness.release_frame(command_id=RELEASE_COMMAND_ID),
        ]
    )
    assert _result(results[0], harness) == "publish_pre_submit_ready"
    assert _result(results[1], harness) == "publish_released"
    assert harness.operation.latest_receipt() is None
    assert not any(runtime.is_running for runtime in harness.runtimes)


def test_a_user_closed_operations_window_never_terminates_the_executor(
    harness: _PublishHarness,
) -> None:
    """The operations window is meant to be human-closable; closing it must not kill us."""
    results = harness.run_worker(
        [
            harness.command(command_id=COMMAND_ID, publish_job_id=PUBLISH_JOB_ID),
            harness.command(command_id=SECOND_COMMAND_ID, publish_job_id=SECOND_PUBLISH_JOB_ID),
        ],
        # The user closes the visible operations window between the two commands.
        before_frame={1: harness.close_held_browser},
    )
    assert _result(results[0], harness) == "publish_pre_submit_ready"
    assert _result(results[1], harness) == "publish_pre_submit_ready"


def test_a_dead_operations_browser_never_terminates_the_executor(
    harness: _PublishHarness,
) -> None:
    """A browser that died while held must not turn the next command into a process exit."""
    results = harness.run_worker(
        [
            harness.command(command_id=COMMAND_ID, publish_job_id=PUBLISH_JOB_ID),
            harness.command(command_id=SECOND_COMMAND_ID, publish_job_id=SECOND_PUBLISH_JOB_ID),
        ],
        before_frame={1: harness.kill_held_browser},
    )
    assert _result(results[0], harness) == "publish_pre_submit_ready"
    assert _result(results[1], harness) == "publish_pre_submit_ready"


def test_a_failing_close_of_the_held_browser_never_terminates_the_executor(
    harness: _PublishHarness,
) -> None:
    """A browser that cannot be closed must not turn the next command into a process exit."""

    def break_the_next_close() -> None:
        harness.fail_next_close = True

    results = harness.run_worker(
        [
            harness.command(command_id=COMMAND_ID, publish_job_id=PUBLISH_JOB_ID),
            harness.command(command_id=SECOND_COMMAND_ID, publish_job_id=SECOND_PUBLISH_JOB_ID),
        ],
        before_frame={1: break_the_next_close},
    )
    assert _result(results[0], harness) == "publish_pre_submit_ready"
    assert _result(results[1], harness) == "publish_pre_submit_ready"


def test_a_browser_that_cannot_restart_becomes_a_receipt_not_a_process_exit(
    harness: _PublishHarness,
) -> None:
    """Close failed, so the old Chromium still holds the profile and the relaunch fails."""

    def break_the_close_and_the_relaunch() -> None:
        harness.fail_next_close = True
        harness.fail_next_start = True

    results = harness.run_worker(
        [
            harness.command(command_id=COMMAND_ID, publish_job_id=PUBLISH_JOB_ID),
            harness.command(command_id=SECOND_COMMAND_ID, publish_job_id=SECOND_PUBLISH_JOB_ID),
        ],
        before_frame={1: break_the_close_and_the_relaunch},
    )
    assert _result(results[0], harness) == "publish_pre_submit_ready"
    assert _result(results[1], harness) == "publish_blocked"
    receipt = harness.operation.latest_receipt()
    assert receipt is not None
    assert receipt.evidence is DouyinPublishPreflightEvidence.BROWSER_UNAVAILABLE


def test_handoff_keeps_the_operations_window_open_for_the_user(
    harness: _PublishHarness,
) -> None:
    """Captcha/slider/risk/login must pause for a human, not close the window."""
    harness.page_state = "captcha"
    running_at_handoff: list[bool] = []

    def observe_between_frames() -> None:
        running_at_handoff.append(harness.runtimes[-1].is_running)

    results = harness.run_worker(
        [
            harness.command(command_id=COMMAND_ID, publish_job_id=PUBLISH_JOB_ID),
            harness.release_frame(command_id=RELEASE_COMMAND_ID),
        ],
        before_frame={1: observe_between_frames},
    )
    assert _result(results[0], harness) == "publish_handoff_required"
    # The user was told to take over: the visible window must still be there.
    assert running_at_handoff == [True]
    assert _result(results[1], harness) == "publish_released"


def test_a_blocked_page_still_releases_the_operations_window(
    harness: _PublishHarness,
) -> None:
    """A blocking overlay is not a human-takeover state, so the window is released."""
    harness.page_state = "mask"
    running_after_block: list[bool] = []

    def observe_between_frames() -> None:
        running_after_block.append(harness.runtimes[-1].is_running)

    results = harness.run_worker(
        [
            harness.command(command_id=COMMAND_ID, publish_job_id=PUBLISH_JOB_ID),
            harness.release_frame(command_id=RELEASE_COMMAND_ID),
        ],
        before_frame={1: observe_between_frames},
    )
    assert _result(results[0], harness) == "publish_blocked"
    assert running_after_block == [False]


def test_releasing_the_surface_keeps_the_handoff_explanation(
    harness: _PublishHarness,
) -> None:
    """Only a dispatchable pre-submit receipt is voided by a release."""
    harness.page_state = "captcha"
    results = harness.run_worker(
        [
            harness.command(command_id=COMMAND_ID, publish_job_id=PUBLISH_JOB_ID),
            harness.release_frame(command_id=RELEASE_COMMAND_ID),
        ]
    )
    assert _result(results[0], harness) == "publish_handoff_required"
    assert _result(results[1], harness) == "publish_released"
    receipt = harness.operation.latest_receipt()
    assert receipt is not None, "the handoff reason must survive giving the window back"
    assert receipt.evidence is DouyinPublishPreflightEvidence.RISK_CHALLENGE


def test_releasing_the_surface_voids_a_dispatchable_receipt(
    harness: _PublishHarness,
) -> None:
    """A ready receipt describes a page that no longer exists after a release."""
    results = harness.run_worker(
        [
            harness.command(command_id=COMMAND_ID, publish_job_id=PUBLISH_JOB_ID),
            harness.release_frame(command_id=RELEASE_COMMAND_ID),
        ]
    )
    assert _result(results[0], harness) == "publish_pre_submit_ready"
    assert _result(results[1], harness) == "publish_released"
    assert harness.operation.latest_receipt() is None
