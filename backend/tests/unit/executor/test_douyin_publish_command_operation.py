"""PB-07: the command frames that drive one publish, from preflight to the click.

The operation owns the operations browser between the two frames: the preflight
fills the form and stops, the approval it presents is what the operator answers,
and only a dispatch frame carrying that same approval may press publish. What is
asserted here is the seam between the frames and the browser lifecycle around
it - the preflight and release rules themselves are asserted in their own files.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from test_douyin_publish_page import (
    ACCOUNT_NAME,
    ARTIFACT_INPUT,
    FORM_URL,
    LOGIN_PANEL,
    SUBMIT_CONTROL,
    WORK_LIST,
    FakeLocator,
    FakePage,
)

from automation_tool.executor.browser_authority import BrowserLaunchAuthority
from automation_tool.executor.browser_runtime import BrowserLaunchRequest, BrowserWindow
from automation_tool.executor.browser_use_safety import SideEffectConfirmationGate
from automation_tool.executor.ledger import ExecutorLedger
from automation_tool.executor.platform_commands import (
    DOUYIN_PUBLISH_DISPATCH_COMMAND,
    DOUYIN_PUBLISH_PREFLIGHT_COMMAND,
    DOUYIN_PUBLISH_RELEASE_COMMAND,
    DouyinPublishPreflightCommandOperation,
    PlatformCommand,
    PlatformCommandRejected,
)
from automation_tool.executor.rpa.douyin.publish_page import (
    DOUYIN_PUBLISH_ENTRY_URL,
    DOUYIN_PUBLISH_MANAGE_URL,
)

COMMAND_ID = "123e4567-e89b-42d3-a456-426614174005"
OTHER_COMMAND_ID = "123e4567-e89b-42d3-a456-426614174007"
JOB_ID = "423e4567-e89b-42d3-a456-426614174001"
INSTALLATION_ID = "123e4567-e89b-42d3-a456-426614174005"
EXECUTOR_ID = "123e4567-e89b-42d3-a456-426614174006"
PROOF = "a" * 50
TITLE = "自动化运营工具发布验收标题"
DESCRIPTION = "自动化运营工具发布验收简介"
ACCOUNT = "自动化运营测试账号"
FORM_SELECTORS = (
    '[data-e2e="publish-title-input"]',
    '[data-e2e="publish-description-input"]',
    SUBMIT_CONTROL,
)
ARTIFACT_PAYLOAD = b"\x00\x00\x00\x18ftypmp42automation-tool-pb-07-fixture"
SUBMIT_GROUP = ", ".join(('[data-e2e="publish-submit"]', 'button:text-is("发布")'))


class _FakeRuntime:
    """A runtime that hands out one window over the publish page fake."""

    def __init__(self, page: FakePage, *, fail_start: bool = False) -> None:
        self.page = page
        self.fail_start = fail_start
        self.started: list[BrowserLaunchRequest] = []
        self.close_calls = 0

    def start(self, request: BrowserLaunchRequest) -> None:
        self.started.append(request)
        if self.fail_start:
            raise RuntimeError("private launch failure")

    def primary_window(self) -> BrowserWindow:
        return BrowserWindow._for_runtime(object(), cast(Any, self.page))

    def close(self) -> None:
        self.close_calls += 1


def browser_paths(tmp_path: Path) -> tuple[Path, Path]:
    executable = tmp_path / "packaged-browser"
    executable.write_bytes(b"browser")
    executable.chmod(0o700)
    profile = tmp_path / "operations-profile"
    profile.mkdir(mode=0o700, exist_ok=True)
    return executable, profile


def artifact(tmp_path: Path) -> Path:
    path = tmp_path / "clip.mp4"
    path.write_bytes(ARTIFACT_PAYLOAD)
    path.chmod(0o600)
    return path


def preflight_command(tmp_path: Path, **overrides: object) -> PlatformCommand:
    executable, profile = browser_paths(tmp_path)
    values: dict[str, object] = {
        "authenticationProof": PROOF,
        "artifactPath": str(artifact(tmp_path)),
        "commandId": COMMAND_ID,
        "commandType": DOUYIN_PUBLISH_PREFLIGHT_COMMAND,
        "description": DESCRIPTION,
        "executablePath": str(executable),
        "headless": True,
        "profileDirectory": str(profile),
        "protocolVersion": "1.0",
        "publishJobId": JOB_ID,
        "title": TITLE,
    }
    return PlatformCommand.model_validate(values | overrides)


def dispatch_command(confirmation_id: str, **overrides: object) -> PlatformCommand:
    values: dict[str, object] = {
        "authenticationProof": PROOF,
        "commandId": OTHER_COMMAND_ID,
        "commandType": DOUYIN_PUBLISH_DISPATCH_COMMAND,
        "confirmationId": confirmation_id,
        "protocolVersion": "1.0",
        "publishJobId": JOB_ID,
    }
    return PlatformCommand.model_validate(values | overrides)


def release_command() -> PlatformCommand:
    return PlatformCommand.model_validate(
        {
            "authenticationProof": PROOF,
            "commandId": OTHER_COMMAND_ID,
            "commandType": DOUYIN_PUBLISH_RELEASE_COMMAND,
            "protocolVersion": "1.0",
        }
    )


class _JourneyLocator(FakeLocator):
    """Adds the one upload the publish page fake does not model on its own."""

    def set_input_files(self, files: object, *, timeout: float) -> None:
        assert timeout > 0
        cast(_PublishJourneyPage, self._page).on_upload()


class _PublishJourneyPage(FakePage):
    """The whole surface one publish walks: entry, upload, form, then works list."""

    def __init__(self, *, form_selectors: set[str] | None = None) -> None:
        super().__init__(url=DOUYIN_PUBLISH_ENTRY_URL, visible_selectors={ARTIFACT_INPUT})
        self._form_selectors = set(FORM_SELECTORS) if form_selectors is None else form_selectors

    def locator(self, selector: str) -> FakeLocator:
        self.requested_selectors.append(selector)
        return _JourneyLocator(selector, self)

    def on_upload(self) -> None:
        self.url = FORM_URL
        self.visible_selectors = self._form_selectors | {ACCOUNT_NAME}
        self.texts[ACCOUNT_NAME] = ACCOUNT


def publish_page(**arguments: Any) -> _PublishJourneyPage:
    """The journey above, with the works list appearing once it is navigated to."""
    page = _PublishJourneyPage(**arguments)

    def show_works_list() -> None:
        page.visible_selectors.clear()
        page.visible_selectors.add(WORK_LIST)
        page.work_titles = [(TITLE, True)]

    page.navigation_callbacks[DOUYIN_PUBLISH_MANAGE_URL] = show_works_list
    return page


def operation(tmp_path: Path, runtime: _FakeRuntime) -> DouyinPublishPreflightCommandOperation:
    return DouyinPublishPreflightCommandOperation(
        ledger=ExecutorLedger(
            state_directory=tmp_path / "ledger",
            installation_id=INSTALLATION_ID,
            executor_id=EXECUTOR_ID,
        ),
        runtime_factory=lambda: cast(Any, runtime),
        browser_authority=BrowserLaunchAuthority(),
    )


def test_a_ready_preflight_holds_the_window_and_presents_the_terms_to_approve(
    tmp_path: Path,
) -> None:
    runtime = _FakeRuntime(publish_page())
    operating = operation(tmp_path, runtime)

    state = operating.handle(preflight_command(tmp_path))

    assert state == "publish_pre_submit_ready"
    assert runtime.started != []
    assert runtime.close_calls == 0
    receipt = operating.latest_receipt()
    assert receipt is not None and receipt.ready
    approval = operating.latest_approval()
    assert approval is not None
    assert operating.pending_approval() == (approval.confirmation_id, ACCOUNT)
    operating.close()


def test_a_blocked_preflight_closes_the_window_and_leaves_nothing_to_approve(
    tmp_path: Path,
) -> None:
    """A page the operator has to finish by hand is not a dispatch target."""
    page = publish_page()
    page.disabled_selectors.add(SUBMIT_GROUP)
    runtime = _FakeRuntime(page)
    operating = operation(tmp_path, runtime)

    state = operating.handle(preflight_command(tmp_path))

    assert state == "publish_blocked"
    assert runtime.close_calls == 1
    assert operating.latest_approval() is None
    assert operating.pending_approval() is None
    operating.close()


def test_a_handoff_preflight_keeps_the_window_open_for_the_operator(tmp_path: Path) -> None:
    page = publish_page()
    page.visible_selectors.add(LOGIN_PANEL)
    runtime = _FakeRuntime(page)
    operating = operation(tmp_path, runtime)

    state = operating.handle(preflight_command(tmp_path))

    assert state == "publish_handoff_required"
    assert runtime.close_calls == 0
    operating.close()


def test_a_browser_that_will_not_start_is_a_receipt_not_a_dead_executor(
    tmp_path: Path,
) -> None:
    runtime = _FakeRuntime(publish_page(), fail_start=True)
    operating = operation(tmp_path, runtime)

    state = operating.handle(preflight_command(tmp_path))

    assert state == "publish_blocked"
    assert runtime.close_calls == 1
    operating.close()


def test_the_approved_dispatch_presses_once_and_voids_the_filled_form(
    tmp_path: Path,
) -> None:
    page = publish_page()
    runtime = _FakeRuntime(page)
    operating = operation(tmp_path, runtime)
    assert operating.handle(preflight_command(tmp_path)) == "publish_pre_submit_ready"
    approval = operating.latest_approval()
    assert approval is not None

    state = operating.handle(dispatch_command(approval.confirmation_id))

    assert state == "publish_verified"
    assert page.clicked == [SUBMIT_CONTROL]
    assert operating.latest_receipt() is None
    assert operating.pending_approval() is None
    operating.close()


def test_a_dispatch_naming_another_approval_never_presses(tmp_path: Path) -> None:
    page = publish_page()
    runtime = _FakeRuntime(page)
    operating = operation(tmp_path, runtime)
    operating.handle(preflight_command(tmp_path))

    state = operating.handle(dispatch_command("423e4567-e89b-42d3-a456-426614174009"))

    assert state == "publish_not_dispatched"
    assert page.clicked == []
    operating.close()


def test_a_dispatch_after_the_surface_went_back_to_the_operator_never_presses(
    tmp_path: Path,
) -> None:
    """Releasing the surface voids the ready receipt; the click has nothing to press."""
    page = publish_page()
    runtime = _FakeRuntime(page)
    operating = operation(tmp_path, runtime)
    operating.handle(preflight_command(tmp_path))
    approval = operating.latest_approval()
    assert approval is not None

    assert operating.handle(release_command()) == "publish_released"

    assert operating.handle(dispatch_command(approval.confirmation_id)) == "publish_not_dispatched"
    assert page.clicked == []
    operating.close()


def test_one_approval_cannot_be_spent_on_two_dispatch_frames(tmp_path: Path) -> None:
    page = publish_page()
    runtime = _FakeRuntime(page)
    operating = operation(tmp_path, runtime)
    operating.handle(preflight_command(tmp_path))
    approval = operating.latest_approval()
    assert approval is not None
    operating.handle(dispatch_command(approval.confirmation_id))

    assert operating.handle(dispatch_command(approval.confirmation_id)) == "publish_not_dispatched"
    assert page.clicked == [SUBMIT_CONTROL]
    operating.close()


def test_a_command_the_operation_does_not_own_is_refused(tmp_path: Path) -> None:
    operating = operation(tmp_path, _FakeRuntime(publish_page()))

    with pytest.raises(PlatformCommandRejected):
        operating.handle(
            PlatformCommand.model_validate(
                {
                    "authenticationProof": PROOF,
                    "commandId": COMMAND_ID,
                    "commandType": "douyin.logout.complete",
                    "protocolVersion": "1.0",
                }
            )
        )
    with pytest.raises(PlatformCommandRejected):
        operating.handle(cast(Any, object()))
    operating.close()


def test_a_dispatch_command_may_not_restate_what_was_already_filled() -> None:
    """Restated content could differ from what the operator saw and approved."""
    for restated in (
        {"title": TITLE},
        {"description": DESCRIPTION},
        {"artifactPath": "/private/clip.mp4"},
        {"headless": True},
    ):
        with pytest.raises(ValueError, match="must not restate publish content"):
            dispatch_command("423e4567-e89b-42d3-a456-426614174009", **restated)


def test_only_the_dispatch_command_carries_a_confirmation(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="only the dispatch command carries a confirmation"):
        preflight_command(tmp_path, confirmationId="423e4567-e89b-42d3-a456-426614174009")


def test_a_preflight_missing_the_content_it_must_fill_is_refused(tmp_path: Path) -> None:
    for absent in ("artifactPath", "title", "description", "publishJobId"):
        with pytest.raises(ValueError, match="requires browser identity and content"):
            preflight_command(tmp_path, **{absent: None})


def test_an_account_this_executor_cannot_name_leaves_nothing_to_approve(
    tmp_path: Path,
) -> None:
    """Nobody is asked to approve posting to an account we could not read off the page."""
    page = publish_page()
    page.visible_selectors.discard(ACCOUNT_NAME)
    page.texts.pop(ACCOUNT_NAME, None)
    runtime = _FakeRuntime(page)
    operating = operation(tmp_path, runtime)
    page.on_upload = lambda: _upload_without_account(page)  # type: ignore[method-assign]

    state = operating.handle(preflight_command(tmp_path))

    assert state == "publish_pre_submit_ready"
    assert operating.latest_approval() is None
    assert operating.pending_approval() is None
    operating.close()


def _upload_without_account(page: _PublishJourneyPage) -> None:
    page.url = FORM_URL
    page.visible_selectors = set(FORM_SELECTORS)


class _GateThatCannotPresent(SideEffectConfirmationGate):
    def present(self, **arguments: Any) -> Any:
        raise RuntimeError("private confirmation failure")


def test_a_summary_that_cannot_be_presented_leaves_the_publish_undispatchable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The form stays filled and visible; what is missing is anything to approve."""
    runtime = _FakeRuntime(publish_page())
    operating = operation(tmp_path, runtime)
    monkeypatch.setattr(operating, "_confirmations", _GateThatCannotPresent())

    state = operating.handle(preflight_command(tmp_path))

    assert state == "publish_pre_submit_ready"
    assert operating.latest_approval() is None
    operating.close()


def test_an_approval_the_gate_no_longer_holds_is_reported_not_pressed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = publish_page()
    operating = operation(tmp_path, _FakeRuntime(page))
    operating.handle(preflight_command(tmp_path))
    approval = operating.latest_approval()
    assert approval is not None
    monkeypatch.setattr(operating, "_confirmations", SideEffectConfirmationGate())

    assert operating.handle(dispatch_command(approval.confirmation_id)) == "publish_not_dispatched"
    assert page.clicked == []
    operating.close()


class _LedgerThatCannotBind(ExecutorLedger):
    def bind_action_hard_policy(self, **arguments: Any) -> Any:
        raise RuntimeError("private ledger failure")


def test_a_release_that_cannot_be_built_is_a_protocol_failure_not_a_silent_skip(
    tmp_path: Path,
) -> None:
    """Nothing was pressed and nothing is known; this must not read as "not dispatched"."""
    page = publish_page()
    runtime = _FakeRuntime(page)
    operating = DouyinPublishPreflightCommandOperation(
        ledger=ExecutorLedger(
            state_directory=tmp_path / "ledger",
            installation_id=INSTALLATION_ID,
            executor_id=EXECUTOR_ID,
        ),
        runtime_factory=lambda: cast(Any, runtime),
        browser_authority=BrowserLaunchAuthority(),
    )
    operating.handle(preflight_command(tmp_path))
    approval = operating.latest_approval()
    assert approval is not None
    operating._ledger = _LedgerThatCannotBind(
        state_directory=tmp_path / "ledger",
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
    )

    with pytest.raises(PlatformCommandRejected):
        operating.handle(dispatch_command(approval.confirmation_id))
    assert page.clicked == []
    operating.close()


def test_a_preflight_frame_that_bypassed_the_model_is_refused_and_closes_the_browser(
    tmp_path: Path,
) -> None:
    """`handle` is public: it re-proves what the frame must carry instead of trusting it."""
    runtime = _FakeRuntime(publish_page())
    operating = operation(tmp_path, runtime)
    bypassed = PlatformCommand.model_construct(
        authentication_proof=PROOF,
        command_id=COMMAND_ID,
        command_type=DOUYIN_PUBLISH_PREFLIGHT_COMMAND,
        protocol_version="1.0",
        publish_job_id=JOB_ID,
    )

    with pytest.raises(PlatformCommandRejected):
        operating.handle(bypassed)
    assert runtime.started == []
    operating.close()


def test_a_browser_that_will_not_close_is_reported_when_the_caller_asked_to_close(
    tmp_path: Path,
) -> None:
    class _RuntimeThatCannotClose(_FakeRuntime):
        def close(self) -> None:
            super().close()
            raise RuntimeError("private close failure")

    runtime = _RuntimeThatCannotClose(publish_page())
    operating = operation(tmp_path, runtime)
    operating.handle(preflight_command(tmp_path))

    with pytest.raises(PlatformCommandRejected):
        operating.close()
    assert runtime.close_calls == 1
