"""PB-06: the one confirmed Douyin publish click and its independent evidence.

Every path here answers one of two questions: was the button pressed, and do we
know what happened afterwards. A path that presses without a matching operator
confirmation and a granted ledger dispatch is a duplicate post; a path that
reports success without the works list agreeing is a lie. Both are asserted.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from test_douyin_publish_page import (
    ACCOUNT_NAME,
    FORM_URL,
    LOGIN_PANEL,
    RISK_CHALLENGE,
    SUBMIT_CONTROL,
    WORK_LIST,
    FakePage,
    window,
)

from automation_tool.executor.action_gate import LocalActionHardPolicy
from automation_tool.executor.browser_surface_lease import BrowserSurfaceLeaseManager
from automation_tool.executor.browser_use_safety import (
    BrowserUseSafetyRejected,
    SideEffectConfirmationGate,
)
from automation_tool.executor.ledger import EXECUTOR_LEDGER_SCHEMA_VERSION, ExecutorLedger
from automation_tool.executor.platform_commands import (
    DOUYIN_PUBLISH_DISPATCH_COMMAND,
    PUBLISH_DISPATCH_RESULT_FOR_STATE,
)
from automation_tool.executor.rpa.douyin.publish_artifact import DouyinPublishArtifact
from automation_tool.executor.rpa.douyin.publish_page import (
    DOUYIN_PUBLISH_MANAGE_ROUTE,
    DOUYIN_PUBLISH_MANAGE_URL,
    DOUYIN_PUBLISH_PAGE_SELECTOR_VERSION,
    MAX_DOUYIN_PUBLISH_WORKS_READ,
)
from automation_tool.executor.rpa.douyin.publish_preflight import (
    DouyinPublishPreflightEvidence,
    DouyinPublishPreflightIntent,
    DouyinPublishPreflightReceipt,
    DouyinPublishPreflightState,
)
from automation_tool.executor.rpa.douyin.publish_release import (
    DOUYIN_PUBLISH_CONFIRMATION_ACTION,
    DOUYIN_PUBLISH_RELEASE_FLOW_VERSION,
    DouyinPublishConfirmation,
    DouyinPublishRelease,
    DouyinPublishReleaseEvidence,
    DouyinPublishReleaseReceipt,
    DouyinPublishReleaseRejected,
    DouyinPublishReleaseState,
)
from automation_tool.executor.side_effect_ledger import SideEffectState

NOW = datetime(2026, 7, 25, 8, 0, tzinfo=UTC)
JOB_ID = "423e4567-e89b-42d3-a456-426614174001"
OTHER_JOB_ID = "423e4567-e89b-42d3-a456-426614174002"
INSTALLATION_ID = "123e4567-e89b-42d3-a456-426614174005"
EXECUTOR_ID = "123e4567-e89b-42d3-a456-426614174006"
TITLE = "自动化运营工具发布验收标题"
DESCRIPTION = "自动化运营工具发布验收简介"
ACCOUNT = "自动化运营测试账号"
FORM_SELECTORS = (
    '[data-e2e="publish-title-input"]',
    '[data-e2e="publish-description-input"]',
    SUBMIT_CONTROL,
)
SUBMIT_GROUP = ", ".join(('[data-e2e="publish-submit"]', 'button:text-is("发布")'))
MINIMUM_INTERVAL = timedelta(seconds=60)
CONTRACT_PATH = (
    Path(__file__).resolve().parents[4] / "contracts/publishing/douyin-single-dispatch.v1.json"
)


class Clock:
    def __init__(self) -> None:
        self.moment = NOW

    def now(self) -> datetime:
        self.moment += timedelta(seconds=1)
        return self.moment


def artifact(tmp_path: Path) -> DouyinPublishArtifact:
    return DouyinPublishArtifact(
        path=tmp_path / "clip.mp4",
        media_type="video/mp4",
        size_bytes=1024,
        sha256="a" * 64,
    )


def intent(tmp_path: Path, *, title: str = TITLE) -> DouyinPublishPreflightIntent:
    return DouyinPublishPreflightIntent(
        artifact=artifact(tmp_path),
        title=title,
        description=DESCRIPTION,
    )


def ready_receipt(source: DouyinPublishPreflightIntent) -> DouyinPublishPreflightReceipt:
    return DouyinPublishPreflightReceipt(
        state=DouyinPublishPreflightState.PRE_SUBMIT_READY,
        evidence=DouyinPublishPreflightEvidence.PRE_SUBMIT_CONFIRMED,
        content_hash=source.content_hash,
        target_account=ACCOUNT,
    )


def confirmed_by_operator(
    gate: SideEffectConfirmationGate,
    source: DouyinPublishPreflightIntent,
    *,
    publish_job_id: str = JOB_ID,
    content_hash: str | None = None,
    target_account: str = ACCOUNT,
) -> DouyinPublishConfirmation:
    """Walk the real critical-point gate: summary shown, operator says yes."""
    digest = source.content_hash if content_hash is None else content_hash
    approval = gate.present(
        action=DOUYIN_PUBLISH_CONFIRMATION_ACTION,
        target_account=target_account,
        content_hash=digest,
    )
    assert target_account in approval.summary
    assert digest[:12] in approval.summary
    return DouyinPublishConfirmation(
        publish_job_id=publish_job_id,
        content_hash=digest,
        target_account=target_account,
        dispatch_token=gate.authorize_dispatch(approval.confirmation_id, confirmed=True),
    )


def publish_page(*, listed: tuple[str, ...] = (TITLE,)) -> FakePage:
    """A ready form whose works list is only populated once it is navigated to."""
    page = FakePage(url=FORM_URL, visible_selectors=set(FORM_SELECTORS))
    page.visible_selectors.add(ACCOUNT_NAME)
    page.texts[ACCOUNT_NAME] = ACCOUNT

    def show_works_list() -> None:
        page.visible_selectors.clear()
        page.visible_selectors.add(WORK_LIST)
        page.work_titles = [(title, True) for title in listed]

    page.navigation_callbacks[DOUYIN_PUBLISH_MANAGE_URL] = show_works_list
    return page


def ledger_for(tmp_path: Path) -> ExecutorLedger:
    return ExecutorLedger(
        state_directory=tmp_path / "ledger",
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
    )


def release(
    page: FakePage,
    opened: ExecutorLedger,
    *,
    clock: Clock | None = None,
    lease: BrowserSurfaceLeaseManager | None = None,
    gate: SideEffectConfirmationGate | None = None,
) -> DouyinPublishRelease:
    return DouyinPublishRelease(
        window=window(page),
        lease=lease or BrowserSurfaceLeaseManager(),
        ledger=opened,
        clock=clock or Clock(),
        policy=LocalActionHardPolicy(minimum_interval=MINIMUM_INTERVAL, task_action_limit=10),
        confirmation_gate=gate or SideEffectConfirmationGate(),
    )


def run(
    page: FakePage,
    opened: ExecutorLedger,
    source: DouyinPublishPreflightIntent,
    *,
    confirmed: DouyinPublishConfirmation | None = None,
    receipt: DouyinPublishPreflightReceipt | None = None,
    clock: Clock | None = None,
    lease: BrowserSurfaceLeaseManager | None = None,
    gate: SideEffectConfirmationGate | None = None,
) -> Any:
    gate = gate or SideEffectConfirmationGate()
    return release(page, opened, clock=clock, lease=lease, gate=gate).run(
        receipt=receipt or ready_receipt(source),
        intent=source,
        confirmation=confirmed or confirmed_by_operator(gate, source),
    )


def test_a_confirmed_publish_presses_once_and_is_proven_by_the_works_list(
    tmp_path: Path,
) -> None:
    page = publish_page()
    opened = ledger_for(tmp_path)
    source = intent(tmp_path)

    receipt = run(page, opened, source)

    assert receipt.state is DouyinPublishReleaseState.VERIFIED
    assert receipt.evidence is DouyinPublishReleaseEvidence.WORK_LISTED
    assert receipt.publish_job_id == JOB_ID
    assert receipt.dispatch_state is SideEffectState.VERIFIED
    assert receipt.dispatch_revision == 3
    assert receipt.replayed is False
    assert receipt.flow_version == DOUYIN_PUBLISH_RELEASE_FLOW_VERSION
    assert receipt.selector_version == DOUYIN_PUBLISH_PAGE_SELECTOR_VERSION
    assert page.clicked == [SUBMIT_CONTROL]
    assert page.navigations == [DOUYIN_PUBLISH_MANAGE_URL]
    recorded = opened.get_publish_dispatch(JOB_ID)
    assert recorded is not None
    assert recorded.state is SideEffectState.VERIFIED


@pytest.mark.parametrize(
    "drift",
    [
        pytest.param({"content_hash": "b" * 64}, id="content"),
        pytest.param({"target_account": "别的账号"}, id="account"),
    ],
)
def test_a_confirmation_that_does_not_match_what_was_shown_never_presses(
    tmp_path: Path,
    drift: dict[str, str],
) -> None:
    page = publish_page()
    opened = ledger_for(tmp_path)
    source = intent(tmp_path)
    gate = SideEffectConfirmationGate()

    receipt = run(
        page,
        opened,
        source,
        gate=gate,
        confirmed=confirmed_by_operator(gate, source, **drift),
    )

    assert receipt.state is DouyinPublishReleaseState.NOT_DISPATCHED
    assert receipt.evidence is DouyinPublishReleaseEvidence.STALE_CONFIRMATION
    assert receipt.dispatch_state is None
    assert page.clicked == []
    assert opened.get_publish_dispatch(JOB_ID) is None


def test_a_confirmation_for_content_the_preflight_did_not_fill_never_presses(
    tmp_path: Path,
) -> None:
    """The receipt, the filled intent and the confirmation must be the same thing."""
    page = publish_page()
    opened = ledger_for(tmp_path)
    source = intent(tmp_path)
    edited = intent(tmp_path, title="改过的标题")

    gate = SideEffectConfirmationGate()
    receipt = run(
        page,
        opened,
        edited,
        gate=gate,
        receipt=ready_receipt(source),
        confirmed=confirmed_by_operator(gate, source),
    )

    assert receipt.evidence is DouyinPublishReleaseEvidence.STALE_CONFIRMATION
    assert page.clicked == []


@pytest.mark.parametrize(
    "state",
    [DouyinPublishPreflightState.BLOCKED, DouyinPublishPreflightState.HANDOFF_REQUIRED],
)
def test_a_publish_that_never_reached_the_pre_submit_point_is_refused(
    tmp_path: Path,
    state: DouyinPublishPreflightState,
) -> None:
    page = publish_page()
    opened = ledger_for(tmp_path)
    source = intent(tmp_path)
    unusable = DouyinPublishPreflightReceipt(
        state=state,
        evidence=(
            DouyinPublishPreflightEvidence.LOGIN_REQUIRED
            if state is DouyinPublishPreflightState.HANDOFF_REQUIRED
            else DouyinPublishPreflightEvidence.PAGE_DRIFT
        ),
    )

    with pytest.raises(DouyinPublishReleaseRejected):
        run(page, opened, source, receipt=unusable)

    assert page.clicked == []


def test_a_surface_owned_by_another_controller_never_presses(tmp_path: Path) -> None:
    page = publish_page()
    opened = ledger_for(tmp_path)
    source = intent(tmp_path)
    lease = BrowserSurfaceLeaseManager()
    lease.begin_takeover(
        cdp_url="http://127.0.0.1:53210",
        timeout_seconds=30,
        pause_confirmed=True,
    )

    receipt = run(page, opened, source, lease=lease)

    assert receipt.state is DouyinPublishReleaseState.NOT_DISPATCHED
    assert receipt.evidence is DouyinPublishReleaseEvidence.SURFACE_LOST
    assert page.clicked == []
    assert opened.get_publish_dispatch(JOB_ID) is None


def test_a_form_that_drifted_since_the_confirmation_never_presses(tmp_path: Path) -> None:
    page = publish_page()
    page.visible_selectors.discard('[data-e2e="publish-title-input"]')
    opened = ledger_for(tmp_path)
    source = intent(tmp_path)

    receipt = run(page, opened, source)

    assert receipt.state is DouyinPublishReleaseState.NOT_DISPATCHED
    assert receipt.evidence is DouyinPublishReleaseEvidence.FORM_NOT_READY
    assert page.clicked == []


def test_a_disabled_publish_button_is_reported_instead_of_pressed(tmp_path: Path) -> None:
    page = publish_page()
    page.disabled_selectors.add(SUBMIT_GROUP)
    opened = ledger_for(tmp_path)
    source = intent(tmp_path)

    receipt = run(page, opened, source)

    assert receipt.state is DouyinPublishReleaseState.NOT_DISPATCHED
    assert receipt.evidence is DouyinPublishReleaseEvidence.SUBMIT_CONTROL_DISABLED
    assert page.clicked == []


def test_the_emergency_stop_reaches_publishing_too(tmp_path: Path) -> None:
    page = publish_page()
    opened = ledger_for(tmp_path)
    opened.engage_action_emergency_stop(changed_at=NOW)
    source = intent(tmp_path)

    receipt = run(page, opened, source)

    assert receipt.state is DouyinPublishReleaseState.NOT_DISPATCHED
    assert receipt.evidence is DouyinPublishReleaseEvidence.LEDGER_UNAVAILABLE
    assert page.clicked == []


def test_a_publish_inside_the_local_minimum_interval_never_presses(tmp_path: Path) -> None:
    opened = ledger_for(tmp_path)
    source = intent(tmp_path)
    first = publish_page()
    run(first, opened, source)

    second = publish_page()
    second_gate = SideEffectConfirmationGate()
    receipt = run(
        second,
        opened,
        source,
        gate=second_gate,
        confirmed=confirmed_by_operator(second_gate, source, publish_job_id=OTHER_JOB_ID),
    )

    assert receipt.state is DouyinPublishReleaseState.NOT_DISPATCHED
    assert receipt.evidence is DouyinPublishReleaseEvidence.DISPATCH_PERMISSION_REJECTED
    assert second.clicked == []


def test_a_click_that_times_out_is_uncertain_rather_than_failed(tmp_path: Path) -> None:
    page = publish_page()
    page.click_failures[SUBMIT_CONTROL] = PlaywrightTimeoutError("private click timeout")
    opened = ledger_for(tmp_path)
    source = intent(tmp_path)

    receipt = run(page, opened, source)

    assert receipt.state is DouyinPublishReleaseState.OUTCOME_UNCERTAIN
    assert receipt.evidence is DouyinPublishReleaseEvidence.DISPATCH_TIMED_OUT
    assert receipt.dispatch_state is SideEffectState.UNCERTAIN
    assert receipt.dispatch_revision == 3
    assert page.navigations == []


def test_a_click_that_fails_outright_is_still_uncertain(tmp_path: Path) -> None:
    page = publish_page()
    page.click_failures[SUBMIT_CONTROL] = RuntimeError("private click failure")
    opened = ledger_for(tmp_path)
    source = intent(tmp_path)

    receipt = run(page, opened, source)

    assert receipt.state is DouyinPublishReleaseState.OUTCOME_UNCERTAIN
    assert receipt.evidence is DouyinPublishReleaseEvidence.DISPATCH_UNAVAILABLE


@pytest.mark.parametrize(
    ("selector", "evidence"),
    [
        (LOGIN_PANEL, DouyinPublishReleaseEvidence.WORKS_LIST_LOGIN_REQUIRED),
        (RISK_CHALLENGE, DouyinPublishReleaseEvidence.WORKS_LIST_RISK_CHALLENGE),
    ],
)
def test_a_works_list_behind_a_challenge_cannot_confirm_anything(
    tmp_path: Path,
    selector: str,
    evidence: DouyinPublishReleaseEvidence,
) -> None:
    page = publish_page()
    original = page.navigation_callbacks[DOUYIN_PUBLISH_MANAGE_URL]

    def challenge() -> None:
        original()
        page.visible_selectors.add(selector)

    page.navigation_callbacks[DOUYIN_PUBLISH_MANAGE_URL] = challenge
    opened = ledger_for(tmp_path)
    source = intent(tmp_path)

    receipt = run(page, opened, source)

    assert receipt.state is DouyinPublishReleaseState.OUTCOME_UNCERTAIN
    assert receipt.evidence is evidence
    assert page.clicked == [SUBMIT_CONTROL]


def test_a_works_list_that_never_loads_leaves_the_outcome_uncertain(tmp_path: Path) -> None:
    page = publish_page()
    page.navigation_callbacks[DOUYIN_PUBLISH_MANAGE_URL] = lambda: page.visible_selectors.clear()
    opened = ledger_for(tmp_path)
    source = intent(tmp_path)

    receipt = run(page, opened, source)

    assert receipt.state is DouyinPublishReleaseState.OUTCOME_UNCERTAIN
    assert receipt.evidence is DouyinPublishReleaseEvidence.WORKS_LIST_UNAVAILABLE


def test_a_work_that_is_not_listed_is_uncertain_not_a_clean_failure(tmp_path: Path) -> None:
    """The click already happened; absence proves nothing about the platform."""
    page = publish_page(listed=("别的作品",))
    opened = ledger_for(tmp_path)
    source = intent(tmp_path)

    receipt = run(page, opened, source)

    assert receipt.state is DouyinPublishReleaseState.OUTCOME_UNCERTAIN
    assert receipt.evidence is DouyinPublishReleaseEvidence.WORK_NOT_LISTED
    assert receipt.dispatch_state is SideEffectState.UNCERTAIN


def test_two_works_sharing_the_title_cannot_identify_this_publish(tmp_path: Path) -> None:
    page = publish_page(listed=(TITLE, TITLE))
    opened = ledger_for(tmp_path)
    source = intent(tmp_path)

    receipt = run(page, opened, source)

    assert receipt.state is DouyinPublishReleaseState.OUTCOME_UNCERTAIN
    assert receipt.evidence is DouyinPublishReleaseEvidence.WORK_LISTED_AMBIGUOUSLY


def test_one_release_object_can_never_press_twice(tmp_path: Path) -> None:
    page = publish_page()
    opened = ledger_for(tmp_path)
    source = intent(tmp_path)
    gate = SideEffectConfirmationGate()
    execution = release(page, opened, gate=gate)
    receipt = ready_receipt(source)
    confirmed = confirmed_by_operator(gate, source)

    execution.run(receipt=receipt, intent=source, confirmation=confirmed)

    with pytest.raises(DouyinPublishReleaseRejected):
        execution.run(receipt=receipt, intent=source, confirmation=confirmed)
    assert page.clicked == [SUBMIT_CONTROL]


def test_a_repeated_job_reports_the_recorded_outcome_without_pressing_again(
    tmp_path: Path,
) -> None:
    opened = ledger_for(tmp_path)
    source = intent(tmp_path)
    run(publish_page(), opened, source)

    replayed_page = publish_page()
    receipt = run(replayed_page, opened, source)

    assert receipt.state is DouyinPublishReleaseState.VERIFIED
    assert receipt.evidence is DouyinPublishReleaseEvidence.REPLAY_VERIFIED
    assert receipt.replayed is True
    assert replayed_page.clicked == []


def test_a_repeated_job_that_ended_uncertain_stays_uncertain(tmp_path: Path) -> None:
    opened = ledger_for(tmp_path)
    source = intent(tmp_path)
    run(publish_page(listed=()), opened, source)

    replayed_page = publish_page()
    receipt = run(replayed_page, opened, source)

    assert receipt.state is DouyinPublishReleaseState.OUTCOME_UNCERTAIN
    assert receipt.evidence is DouyinPublishReleaseEvidence.REPLAY_UNCERTAIN
    assert receipt.replayed is True
    assert replayed_page.clicked == []


def test_the_receipt_and_the_confirmation_redact_themselves(tmp_path: Path) -> None:
    source = intent(tmp_path)
    gate = SideEffectConfirmationGate()
    confirmed = confirmed_by_operator(gate, source)
    receipt = run(publish_page(), ledger_for(tmp_path), source)

    assert ACCOUNT not in repr(confirmed)
    assert source.content_hash not in repr(confirmed)
    assert confirmed.dispatch_token not in repr(confirmed)
    assert "redacted" in repr(confirmed)
    assert TITLE not in repr(receipt)
    assert "verified" in repr(receipt)


@pytest.mark.parametrize(
    ("publish_job_id", "content_hash", "target_account", "dispatch_token"),
    [
        ("not-a-uuid", "a" * 64, ACCOUNT, "f" * 64),
        (JOB_ID, "A" * 64, ACCOUNT, "f" * 64),
        (JOB_ID, "a" * 63, ACCOUNT, "f" * 64),
        (JOB_ID, "a" * 64, "账号‮", "f" * 64),
        (JOB_ID, "a" * 64, "  ", "f" * 64),
        (JOB_ID, "a" * 64, "x" * 65, "f" * 64),
        (JOB_ID, "a" * 64, ACCOUNT, ""),
        (JOB_ID, "a" * 64, ACCOUNT, "not hex"),
    ],
)
def test_a_confirmation_outside_the_frozen_shape_is_refused(
    publish_job_id: str,
    content_hash: str,
    target_account: str,
    dispatch_token: str,
) -> None:
    with pytest.raises(DouyinPublishReleaseRejected):
        DouyinPublishConfirmation(
            publish_job_id=publish_job_id,
            content_hash=content_hash,
            target_account=target_account,
            dispatch_token=dispatch_token,
        )


def test_a_token_the_operator_never_approved_never_presses(tmp_path: Path) -> None:
    """Only the critical-point gate can mint a token, and only after a yes."""
    page = publish_page()
    opened = ledger_for(tmp_path)
    source = intent(tmp_path)

    receipt = run(
        page,
        opened,
        source,
        confirmed=DouyinPublishConfirmation(
            publish_job_id=JOB_ID,
            content_hash=source.content_hash,
            target_account=ACCOUNT,
            dispatch_token="f" * 64,
        ),
    )

    assert receipt.state is DouyinPublishReleaseState.NOT_DISPATCHED
    assert receipt.evidence is DouyinPublishReleaseEvidence.STALE_CONFIRMATION
    assert page.clicked == []
    # The job was recorded before the approval was checked, but it never got a
    # dispatch, so nothing was pressed and the row stays spendable by a real one.
    recorded = opened.get_publish_dispatch(JOB_ID)
    assert recorded is not None
    assert recorded.state is SideEffectState.PREPARED
    assert recorded.dispatched_at is None


def test_a_declined_confirmation_yields_no_token_at_all(tmp_path: Path) -> None:
    gate = SideEffectConfirmationGate()
    source = intent(tmp_path)
    approval = gate.present(
        action=DOUYIN_PUBLISH_CONFIRMATION_ACTION,
        target_account=ACCOUNT,
        content_hash=source.content_hash,
    )

    with pytest.raises(BrowserUseSafetyRejected):
        gate.authorize_dispatch(approval.confirmation_id, confirmed=False)


def test_one_approval_can_never_be_spent_on_two_publishes(tmp_path: Path) -> None:
    opened = ledger_for(tmp_path)
    source = intent(tmp_path)
    gate = SideEffectConfirmationGate()
    confirmed = confirmed_by_operator(gate, source)
    run(publish_page(), opened, source, gate=gate, confirmed=confirmed)

    replayed_page = publish_page()
    receipt = run(
        replayed_page,
        opened,
        source,
        gate=gate,
        confirmed=DouyinPublishConfirmation(
            publish_job_id=OTHER_JOB_ID,
            content_hash=confirmed.content_hash,
            target_account=confirmed.target_account,
            dispatch_token=confirmed.dispatch_token,
        ),
    )

    assert receipt.state is DouyinPublishReleaseState.NOT_DISPATCHED
    assert receipt.evidence is DouyinPublishReleaseEvidence.STALE_CONFIRMATION
    assert replayed_page.clicked == []


def test_the_release_refuses_dependencies_it_cannot_trust(tmp_path: Path) -> None:
    opened = ledger_for(tmp_path)
    with pytest.raises(DouyinPublishReleaseRejected):
        DouyinPublishRelease(
            window=cast(Any, object()),
            lease=BrowserSurfaceLeaseManager(),
            ledger=opened,
            clock=Clock(),
            policy=LocalActionHardPolicy(
                minimum_interval=MINIMUM_INTERVAL,
                task_action_limit=10,
            ),
            confirmation_gate=SideEffectConfirmationGate(),
        )


def test_the_contract_pins_every_outcome_the_code_can_produce() -> None:
    """A new evidence value must be classified in the contract, not invented."""
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

    assert contract["version"] == DOUYIN_PUBLISH_RELEASE_FLOW_VERSION
    assert contract["selectorVersion"] == DOUYIN_PUBLISH_PAGE_SELECTOR_VERSION
    assert contract["confirmation"]["action"] == DOUYIN_PUBLISH_CONFIRMATION_ACTION
    assert contract["dispatch"]["maximumClicks"] == 1
    assert contract["dispatch"]["ledgerSchemaVersion"] == EXECUTOR_LEDGER_SCHEMA_VERSION
    assert contract["verification"]["route"] == DOUYIN_PUBLISH_MANAGE_ROUTE
    assert contract["verification"]["maximumRowsRead"] == MAX_DOUYIN_PUBLISH_WORKS_READ
    assert contract["localCommand"]["commandType"] == DOUYIN_PUBLISH_DISPATCH_COMMAND

    declared = {value for outcome in contract["outcomes"].values() for value in outcome}
    assert declared == {evidence.value for evidence in DouyinPublishReleaseEvidence}
    assert set(contract["outcomes"]) == {state.value for state in DouyinPublishReleaseState}
    assert set(contract["dispatch"]["states"]) == {state.value for state in SideEffectState}
    assert set(contract["localCommand"]["resultStates"]) == set(
        PUBLISH_DISPATCH_RESULT_FOR_STATE.values()
    )


def _receipt_values(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "publish_job_id": JOB_ID,
        "state": DouyinPublishReleaseState.NOT_DISPATCHED,
        "evidence": DouyinPublishReleaseEvidence.SURFACE_LOST,
        "dispatch_state": None,
        "dispatch_revision": None,
        "replayed": False,
    }
    values.update(overrides)
    return values


def test_a_release_receipt_must_describe_an_outcome_that_could_have_happened() -> None:
    """The four shapes are the whole vocabulary; a mixed one would claim two things."""
    cases: list[tuple[str, dict[str, Any]]] = [
        ("a job id that is not one", {"publish_job_id": "not-a-job"}),
        ("a state from outside the set", {"state": "not_dispatched"}),
        ("evidence from outside the set", {"evidence": "surface_lost"}),
        ("another flow version", {"flow_version": "douyin-publish-release.v0"}),
        ("another selector version", {"selector_version": "douyin-publish-page.v0"}),
        ("a replay flag that is not a bool", {"replayed": 0}),
        (
            "not dispatched yet carrying a dispatch revision",
            {"dispatch_state": SideEffectState.DISPATCHED, "dispatch_revision": 2},
        ),
        (
            "verified without the settled revision",
            {
                "state": DouyinPublishReleaseState.VERIFIED,
                "evidence": DouyinPublishReleaseEvidence.WORK_LISTED,
                "dispatch_state": SideEffectState.VERIFIED,
                "dispatch_revision": 2,
            },
        ),
        (
            "a replay flag that disagrees with its evidence",
            {
                "state": DouyinPublishReleaseState.VERIFIED,
                "evidence": DouyinPublishReleaseEvidence.WORK_LISTED,
                "dispatch_state": SideEffectState.VERIFIED,
                "dispatch_revision": 3,
                "replayed": True,
            },
        ),
    ]
    for label, overrides in cases:
        with pytest.raises(DouyinPublishReleaseRejected):
            DouyinPublishReleaseReceipt(**_receipt_values(**overrides))
        assert label


def test_only_a_verified_release_counts_as_published() -> None:
    """Anything else leaves the circuit open: the operator has to look before retrying."""
    unsettled = DouyinPublishReleaseReceipt(**_receipt_values())
    assert unsettled.published is False
    assert unsettled.circuit_open is True

    settled = DouyinPublishReleaseReceipt(
        **_receipt_values(
            state=DouyinPublishReleaseState.VERIFIED,
            evidence=DouyinPublishReleaseEvidence.WORK_LISTED,
            dispatch_state=SideEffectState.VERIFIED,
            dispatch_revision=3,
            replayed=False,
        )
    )
    assert settled.published is True
    assert settled.circuit_open is False


def test_neither_the_release_nor_its_clock_prints_anything_private(tmp_path: Path) -> None:
    from automation_tool.executor.rpa.douyin.publish_release import SystemPublishReleaseClock

    page = publish_page()
    opened = ledger_for(tmp_path)
    assert repr(release(page, opened)) == "DouyinPublishRelease(<redacted>)"

    assert repr(SystemPublishReleaseClock()) == "SystemPublishReleaseClock()"


def test_a_release_built_on_a_policy_it_cannot_use_is_refused(tmp_path: Path) -> None:
    """The interval and the action budget are what keep this from becoming a flood."""
    page = publish_page()
    opened = ledger_for(tmp_path)
    with pytest.raises(DouyinPublishReleaseRejected):
        DouyinPublishRelease(
            window=window(page),
            lease=BrowserSurfaceLeaseManager(),
            ledger=opened,
            clock=Clock(),
            policy=cast(Any, object()),
            confirmation_gate=SideEffectConfirmationGate(),
        )


def test_a_fingerprint_is_only_built_from_a_real_content_digest() -> None:
    from automation_tool.executor.rpa.douyin.publish_release import (
        publish_verification_fingerprint,
    )

    assert len(publish_verification_fingerprint("a" * 64)) == 32
    for label, value in [("not a digest", "not-a-digest"), ("too short", "a" * 63)]:
        with pytest.raises(DouyinPublishReleaseRejected):
            publish_verification_fingerprint(value)
        assert label


def test_a_clock_that_answers_with_a_useless_moment_is_refused(tmp_path: Path) -> None:
    """Every rate-limit decision is made against this, so a naive moment cannot be one."""
    page = publish_page()
    opened = ledger_for(tmp_path)

    class _Naive:
        def now(self) -> datetime:
            return datetime(2026, 7, 25, 8, 0)

    class _Offset:
        def now(self) -> datetime:
            return datetime(2026, 7, 25, 8, 0, tzinfo=timezone(timedelta(hours=8)))

    class _Exploding:
        def now(self) -> datetime:
            raise RuntimeError("clock defect")

    for label, clock in [
        ("a moment with no zone", _Naive()),
        ("a moment in another zone", _Offset()),
        ("a clock that raises", _Exploding()),
    ]:
        releasing = release(page, opened, clock=cast(Any, clock))
        with pytest.raises(DouyinPublishReleaseRejected):
            releasing._now()
        assert label

    assert release(page, opened, clock=Clock())._now().tzinfo is UTC
