from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from automation_tool.executor.action_authorization import (
    ActionAuthorizationExpectation,
    Ed25519ActionAuthorizationVerifier,
)
from automation_tool.executor.action_gate import (
    ActionGateLimited,
    ExecutorActionGate,
    LocalActionHardPolicy,
    LocalActionLimitReason,
)
from automation_tool.executor.browser_runtime import BrowserWindow
from automation_tool.executor.ledger import ExecutorLedger
from automation_tool.executor.rpa.douyin.direct_message_action import (
    DouyinDirectMessageActionEvidence,
    DouyinDirectMessageActionExecution,
    DouyinDirectMessageActionIntent,
    DouyinDirectMessageActionReceipt,
    DouyinDirectMessageActionRejected,
    DouyinDirectMessageActionState,
)
from automation_tool.executor.rpa.douyin.page_anchors import VISIBLE_MATCH_ENGINE
from automation_tool.executor.side_effect_ledger import SideEffectState
from automation_tool.protocol import (
    ACTION_AUTHORIZATION_VERSION,
    ActionAuthorizationClaims,
    ActionMessageTemplate,
    DouyinCandidateSummary,
    DouyinSearchExposureAction,
    ProtocolActionId,
    ProtocolExecutionAttemptId,
    ProtocolExecutorId,
    ProtocolInstallationId,
    ProtocolTargetId,
    ProtocolTaskId,
    action_authorization_idempotency_key,
    action_authorization_signing_input,
    encode_action_authorization_token,
)

NOW = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)
PRIVATE_KEY = bytes(range(32))
INSTALLATION_ID = ProtocolInstallationId("123e4567-e89b-42d3-a456-426614174005")
EXECUTOR_ID = ProtocolExecutorId("123e4567-e89b-42d3-a456-426614174006")
ATTEMPT_ID = ProtocolExecutionAttemptId("123e4567-e89b-42d3-a456-426614174007")
TASK_ID = ProtocolTaskId("123e4567-e89b-42d3-a456-426614174008")
PROFILE_URL = "https://www.douyin.com/user/creator-001"
MESSAGE_ENTRY = 'button[aria-label="私信"]'
MESSAGE_INPUT = 'textarea[aria-label="发送私信"]'
MESSAGE_SEND = 'button[aria-label="发送私信"]'
FINAL_CONFIRMATION = '[role="status"]:has-text("私信发送成功")'
LOGIN_DIALOG = '[role="dialog"]:has-text("扫码登录")'
BLOCKING_DIALOG = '[role="dialog"]'
MESSAGING_NOT_ALLOWED = '[role="alert"]:has-text("暂时无法私信")'
FOLLOW_REQUIRED = '[role="alert"]:has-text("关注后才能私信")'
SECOND_MESSAGE_INPUT = 'textarea[placeholder="发送私信"]'


class Clock:
    def __init__(self, value: object = NOW) -> None:
        self.value = value

    def now(self) -> datetime:
        return cast(datetime, self.value)


class SequenceClock(Clock):
    def __init__(self, values: list[object]) -> None:
        self.values = iter(values)

    def now(self) -> datetime:
        value = next(self.values)
        if isinstance(value, BaseException):
            raise value
        return cast(datetime, value)


class BrokenTimezone(tzinfo):
    def utcoffset(self, value: datetime | None) -> timedelta | None:
        raise RuntimeError("private timezone failure")

    def dst(self, value: datetime | None) -> timedelta | None:
        return None

    def tzname(self, value: datetime | None) -> str | None:
        return None


class Locator:
    def __init__(self, selector: str, page: Page) -> None:
        self.selector = selector
        self.page = page

    @property
    def first(self) -> Locator:
        return self

    def locator(self, selector: str) -> Locator:
        """Every element this page models is on screen, so the filter keeps them all."""
        assert selector == VISIBLE_MATCH_ENGINE
        return self

    def count(self) -> int:
        if self.page.locator_failure:
            raise RuntimeError("private locator failure")
        return sum(
            selector in self.page.visible_selectors for selector in self.selector.split(", ")
        )

    def wait_for(self, *, state: str, timeout: float) -> None:
        assert state == "visible"
        assert timeout > 0

    def fill(self, value: str, *, timeout: float) -> None:
        assert timeout > 0
        if self.page.fill_failure is not None:
            raise self.page.fill_failure
        self.page.filled.append(value)
        if self.page.after_fill_selectors is not None:
            self.page.visible_selectors = self.page.after_fill_selectors

    def click(self, *, timeout: float) -> None:
        assert timeout > 0
        if MESSAGE_ENTRY in self.selector:
            self.page.entry_clicks += 1
            if self.page.entry_click_failure is not None:
                raise self.page.entry_click_failure
            self.page.visible_selectors = self.page.after_entry_selectors
            return
        self.page.clicks += 1
        if self.page.click_failure is not None:
            raise self.page.click_failure
        self.page.visible_selectors = self.page.after_click_selectors
        if self.page.after_click_url is not None:
            self.page.url = self.page.after_click_url


class Page:
    def __init__(
        self,
        *,
        url: str = PROFILE_URL,
        visible_selectors: set[str] | None = None,
    ) -> None:
        self.url = url
        self.visible_selectors = (
            {MESSAGE_INPUT, MESSAGE_SEND} if visible_selectors is None else visible_selectors
        )
        self.filled: list[str] = []
        self.clicks = 0
        self.entry_clicks = 0
        self.requested_selectors: list[str] = []
        self.locator_failure = False
        self.fill_failure: BaseException | None = None
        self.click_failure: BaseException | None = None
        self.entry_click_failure: BaseException | None = None
        self.after_entry_selectors = {MESSAGE_INPUT, MESSAGE_SEND}
        self.after_fill_selectors: set[str] | None = None
        self.after_click_selectors = {FINAL_CONFIRMATION}
        self.after_click_url: str | None = None

    def locator(self, selector: str) -> Locator:
        self.requested_selectors.append(selector)
        return Locator(selector, self)


def resource_id(index: int, kind: type[str]) -> str:
    return kind(str(UUID(f"423e4567-e89b-42d3-a456-{index:012d}")))


def authorization(
    index: int,
    *,
    action: DouyinSearchExposureAction = DouyinSearchExposureAction.DIRECT_MESSAGE,
    task_id: ProtocolTaskId = TASK_ID,
) -> tuple[str, ActionAuthorizationExpectation]:
    action_id = ProtocolActionId(resource_id(index, str))
    claims = ActionAuthorizationClaims(
        version=ACTION_AUTHORIZATION_VERSION,
        action_id=action_id,
        target_id=ProtocolTargetId(resource_id(index + 100, str)),
        execution_attempt_id=ATTEMPT_ID,
        task_id=task_id,
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
        platform="douyin",
        action=action,
        idempotency_key=action_authorization_idempotency_key(action_id),
        authorized_at=NOW - timedelta(seconds=1),
        deadline_at=NOW + timedelta(minutes=4),
    )
    token = encode_action_authorization_token(
        claims,
        Ed25519PrivateKey.from_private_bytes(PRIVATE_KEY).sign(
            action_authorization_signing_input(claims)
        ),
    )
    return token, ActionAuthorizationExpectation(
        action_id=claims.action_id,
        target_id=claims.target_id,
        execution_attempt_id=claims.execution_attempt_id,
        task_id=claims.task_id,
        installation_id=claims.installation_id,
        executor_id=claims.executor_id,
        platform=claims.platform,
        action=claims.action,
        idempotency_key=claims.idempotency_key,
    )


def dependencies(
    state_directory: Path,
    *,
    clock: Clock | None = None,
    minimum_interval: timedelta = timedelta(seconds=1),
    task_action_limit: int = 100,
) -> tuple[ExecutorActionGate, ExecutorLedger, Clock]:
    clock = Clock() if clock is None else clock
    ledger = ExecutorLedger(
        state_directory=state_directory,
        installation_id=str(INSTALLATION_ID),
        executor_id=str(EXECUTOR_ID),
    )
    gate = ExecutorActionGate(
        ledger=ledger,
        verifier=Ed25519ActionAuthorizationVerifier(
            public_key=Ed25519PrivateKey.from_private_bytes(PRIVATE_KEY)
            .public_key()
            .public_bytes_raw(),
            clock=clock,
        ),
        policy=LocalActionHardPolicy(
            minimum_interval=minimum_interval, task_action_limit=task_action_limit
        ),
        clock=clock,
    )
    return gate, ledger, clock


def intent(
    expected: ActionAuthorizationExpectation,
    source: str = "固定私信内容",
) -> DouyinDirectMessageActionIntent:
    return DouyinDirectMessageActionIntent(
        authorization=expected,
        message_template=ActionMessageTemplate(source=source),
        target_summary=DouyinCandidateSummary(display_name="目标账号", public_handle=None),
    )


def execute(
    page: Page,
    gate: ExecutorActionGate,
    ledger: ExecutorLedger,
    clock: Clock,
    token: str,
    action_intent: DouyinDirectMessageActionIntent,
) -> DouyinDirectMessageActionReceipt:
    return DouyinDirectMessageActionExecution(
        window=BrowserWindow._for_runtime(object(), cast(Any, page)),
        action_gate=gate,
        ledger=ledger,
        clock=clock,
    ).run(token=token, intent=action_intent)


def test_direct_message_action_admits_prepares_clicks_once_and_verifies(tmp_path: Path) -> None:
    token, expected = authorization(1)
    gate, ledger, clock = dependencies(tmp_path / "state")
    page = Page()
    action_intent = DouyinDirectMessageActionIntent(
        authorization=expected,
        message_template=ActionMessageTemplate(source="您好 {{target_display_name}} 内容很有启发"),
        target_summary=DouyinCandidateSummary(display_name="目标账号", public_handle=None),
    )

    receipt = execute(page, gate, ledger, clock, token, action_intent)

    assert receipt.state is DouyinDirectMessageActionState.VERIFIED
    assert receipt.evidence is DouyinDirectMessageActionEvidence.MESSAGE_CONFIRMED
    assert receipt.side_effect_state is SideEffectState.VERIFIED
    assert receipt.replayed is False
    assert page.filled == ["您好 目标账号 内容很有启发"]
    assert page.clicks == 1
    persisted = ledger.get_side_effect(str(expected.action_id))
    assert persisted is not None
    assert persisted.state is SideEffectState.VERIFIED
    assert len(persisted.effect_fingerprint) == 32
    assert "您好" not in ledger.database_path.read_bytes().decode("utf-8", errors="ignore")
    assert receipt.completed is True and receipt.circuit_open is False
    assert str(expected.action_id) not in repr(receipt)


def test_profile_entry_is_recoverable_and_send_remains_the_only_dispatch(
    tmp_path: Path,
) -> None:
    token, expected = authorization(2)
    gate, ledger, clock = dependencies(tmp_path / "entry")
    page = Page(visible_selectors={MESSAGE_ENTRY})

    receipt = execute(page, gate, ledger, clock, token, intent(expected))

    assert receipt.evidence is DouyinDirectMessageActionEvidence.MESSAGE_CONFIRMED
    assert page.entry_clicks == 1
    assert page.clicks == 1


@pytest.mark.parametrize(
    ("failure", "evidence"),
    (
        (
            PlaywrightTimeoutError("private timeout"),
            DouyinDirectMessageActionEvidence.ENTER_CONVERSATION_TIMED_OUT,
        ),
        (
            RuntimeError("private failure"),
            DouyinDirectMessageActionEvidence.ENTER_CONVERSATION_UNAVAILABLE,
        ),
    ),
)
def test_entry_failure_stays_prepared_and_can_resume_from_conversation(
    tmp_path: Path,
    failure: BaseException,
    evidence: DouyinDirectMessageActionEvidence,
) -> None:
    token, expected = authorization(3)
    gate, ledger, clock = dependencies(tmp_path / evidence.value)
    profile = Page(visible_selectors={MESSAGE_ENTRY})
    profile.entry_click_failure = failure

    first = execute(profile, gate, ledger, clock, token, intent(expected))
    assert first.evidence is evidence
    assert first.side_effect_state is SideEffectState.PREPARED
    assert profile.entry_clicks == 1 and profile.clicks == 0

    resumed = Page()
    second = execute(resumed, gate, ledger, clock, token, intent(expected))
    assert second.evidence is DouyinDirectMessageActionEvidence.MESSAGE_CONFIRMED
    assert resumed.entry_clicks == 0 and resumed.clicks == 1


def test_entry_without_conversation_anchor_times_out_before_send(tmp_path: Path) -> None:
    token, expected = authorization(4)
    gate, ledger, clock = dependencies(tmp_path / "conversation-timeout")
    page = Page(visible_selectors={MESSAGE_ENTRY})
    page.after_entry_selectors = {MESSAGE_ENTRY}

    receipt = execute(page, gate, ledger, clock, token, intent(expected))

    assert receipt.evidence is DouyinDirectMessageActionEvidence.READY_TIMED_OUT
    assert receipt.side_effect_state is SideEffectState.PREPARED
    assert page.entry_clicks == 1 and page.clicks == 0


@pytest.mark.parametrize(
    ("selector", "evidence"),
    (
        (
            MESSAGING_NOT_ALLOWED,
            DouyinDirectMessageActionEvidence.READY_MESSAGING_NOT_ALLOWED,
        ),
        (FOLLOW_REQUIRED, DouyinDirectMessageActionEvidence.READY_FOLLOW_REQUIRED),
    ),
)
def test_permission_change_after_entry_preserves_the_exact_reason(
    tmp_path: Path,
    selector: str,
    evidence: DouyinDirectMessageActionEvidence,
) -> None:
    token, expected = authorization(5)
    gate, ledger, clock = dependencies(tmp_path / evidence.value)
    page = Page(visible_selectors={MESSAGE_ENTRY})
    page.after_entry_selectors = {selector}

    receipt = execute(page, gate, ledger, clock, token, intent(expected))

    assert receipt.evidence is evidence
    assert receipt.side_effect_state is SideEffectState.PREPARED
    assert page.entry_clicks == 1 and page.clicks == 0


def test_verified_and_uncertain_replays_never_touch_the_page(tmp_path: Path) -> None:
    token, expected = authorization(10)
    gate, ledger, clock = dependencies(tmp_path / "verified")
    action_intent = intent(expected)
    first_page = Page()
    assert execute(first_page, gate, ledger, clock, token, action_intent).completed

    replay_page = Page()
    replay = execute(replay_page, gate, ledger, clock, token, action_intent)
    assert replay.evidence is DouyinDirectMessageActionEvidence.REPLAY_VERIFIED
    assert replay.replayed is True
    assert replay.side_effect_state is SideEffectState.VERIFIED
    assert replay_page.requested_selectors == []
    assert replay_page.clicks == 0

    uncertain_token, uncertain_expected = authorization(11)
    uncertain_gate, uncertain_ledger, uncertain_clock = dependencies(tmp_path / "uncertain")
    failing_page = Page()
    failing_page.click_failure = RuntimeError("private click failure")
    first_uncertain = execute(
        failing_page,
        uncertain_gate,
        uncertain_ledger,
        uncertain_clock,
        uncertain_token,
        intent(uncertain_expected),
    )
    assert first_uncertain.state is DouyinDirectMessageActionState.OUTCOME_UNCERTAIN
    assert first_uncertain.evidence is DouyinDirectMessageActionEvidence.DISPATCH_UNAVAILABLE
    assert first_uncertain.side_effect_state is SideEffectState.UNCERTAIN
    assert failing_page.clicks == 1

    uncertain_replay_page = Page()
    uncertain_replay = execute(
        uncertain_replay_page,
        uncertain_gate,
        uncertain_ledger,
        uncertain_clock,
        uncertain_token,
        intent(uncertain_expected),
    )
    assert uncertain_replay.evidence is DouyinDirectMessageActionEvidence.REPLAY_UNCERTAIN
    assert uncertain_replay.replayed is True
    assert uncertain_replay_page.requested_selectors == []
    assert uncertain_replay_page.clicks == 0


def test_prepared_retry_can_dispatch_once_but_changed_copy_is_rejected(tmp_path: Path) -> None:
    token, expected = authorization(20)
    gate, ledger, clock = dependencies(tmp_path / "retry")
    blocked = Page(visible_selectors={MESSAGE_INPUT, MESSAGE_SEND, BLOCKING_DIALOG})
    first = execute(blocked, gate, ledger, clock, token, intent(expected))
    assert first.evidence is DouyinDirectMessageActionEvidence.READY_DIALOG_BLOCKED
    assert first.side_effect_state is SideEffectState.PREPARED
    assert blocked.filled == [] and blocked.clicks == 0

    ready = Page()
    second = execute(ready, gate, ledger, clock, token, intent(expected))
    assert second.evidence is DouyinDirectMessageActionEvidence.MESSAGE_CONFIRMED
    assert ready.clicks == 1

    other_token, other_expected = authorization(21)
    other_gate, other_ledger, other_clock = dependencies(tmp_path / "changed")
    first_copy = execute(
        Page(visible_selectors={BLOCKING_DIALOG}),
        other_gate,
        other_ledger,
        other_clock,
        other_token,
        intent(other_expected, "第一版私信"),
    )
    assert first_copy.side_effect_state is SideEffectState.PREPARED
    changed_page = Page()
    changed = execute(
        changed_page,
        other_gate,
        other_ledger,
        other_clock,
        other_token,
        intent(other_expected, "第二版私信"),
    )
    assert changed.evidence is DouyinDirectMessageActionEvidence.LEDGER_UNAVAILABLE
    assert changed_page.requested_selectors == [] and changed_page.clicks == 0


@pytest.mark.parametrize(
    ("page", "evidence"),
    (
        (
            Page(visible_selectors={MESSAGE_INPUT, MESSAGE_SEND, LOGIN_DIALOG}),
            DouyinDirectMessageActionEvidence.READY_LOGIN_REQUIRED,
        ),
        (
            Page(visible_selectors={MESSAGING_NOT_ALLOWED}),
            DouyinDirectMessageActionEvidence.READY_MESSAGING_NOT_ALLOWED,
        ),
        (
            Page(visible_selectors={FOLLOW_REQUIRED}),
            DouyinDirectMessageActionEvidence.READY_FOLLOW_REQUIRED,
        ),
        (
            Page(visible_selectors=set()),
            DouyinDirectMessageActionEvidence.READY_TIMED_OUT,
        ),
        (
            Page(url="https://www.douyin.com/live"),
            DouyinDirectMessageActionEvidence.READY_PAGE_VERSION_UNKNOWN,
        ),
        (
            Page(visible_selectors={MESSAGE_INPUT, SECOND_MESSAGE_INPUT, MESSAGE_SEND}),
            DouyinDirectMessageActionEvidence.READY_CONFLICTING_ANCHORS,
        ),
        (
            Page(visible_selectors={FINAL_CONFIRMATION}),
            DouyinDirectMessageActionEvidence.STALE_CONFIRMATION,
        ),
    ),
)
def test_ready_failures_remain_prepared_without_fill_or_click(
    tmp_path: Path,
    page: Page,
    evidence: DouyinDirectMessageActionEvidence,
) -> None:
    token, expected = authorization(30)
    gate, ledger, clock = dependencies(tmp_path / evidence.value)
    receipt = execute(page, gate, ledger, clock, token, intent(expected))
    assert receipt.state is DouyinDirectMessageActionState.NOT_DISPATCHED
    assert receipt.evidence is evidence
    assert receipt.side_effect_state is SideEffectState.PREPARED
    assert page.filled == [] and page.clicks == 0


@pytest.mark.parametrize("failure", (RuntimeError("private"), PlaywrightTimeoutError("private")))
def test_fill_or_pre_dispatch_drift_never_acquires_dispatch_permission(
    tmp_path: Path,
    failure: BaseException,
) -> None:
    token, expected = authorization(40)
    gate, ledger, clock = dependencies(tmp_path / type(failure).__name__)
    page = Page()
    page.fill_failure = failure
    receipt = execute(page, gate, ledger, clock, token, intent(expected))
    assert receipt.evidence is DouyinDirectMessageActionEvidence.PREPARE_UNAVAILABLE
    assert page.clicks == 0
    persisted = ledger.get_side_effect(str(expected.action_id))
    assert persisted is not None and persisted.state is SideEffectState.PREPARED

    drift_token, drift_expected = authorization(41)
    drift_gate, drift_ledger, drift_clock = dependencies(
        tmp_path / f"drift-{type(failure).__name__}"
    )
    drift = Page()
    drift.after_fill_selectors = set()
    drifted = execute(
        drift,
        drift_gate,
        drift_ledger,
        drift_clock,
        drift_token,
        intent(drift_expected),
    )
    assert drifted.evidence is DouyinDirectMessageActionEvidence.PREPARE_UNAVAILABLE
    assert drift.clicks == 0


def test_dispatch_permission_failure_occurs_after_fill_but_before_click(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token, expected = authorization(50)
    gate, ledger, clock = dependencies(tmp_path / "state")
    page = Page()

    def reject_dispatch(*args: object, **kwargs: object) -> object:
        raise RuntimeError("private ledger failure")

    monkeypatch.setattr(ExecutorLedger, "begin_side_effect_dispatch", reject_dispatch)
    receipt = execute(page, gate, ledger, clock, token, intent(expected))
    assert receipt.evidence is DouyinDirectMessageActionEvidence.DISPATCH_PERMISSION_REJECTED
    assert page.filled == ["固定私信内容"] and page.clicks == 0


def test_dispatch_race_loser_observes_persisted_state_without_click(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token, expected = authorization(51)
    gate, ledger, clock = dependencies(tmp_path / "state")
    page = Page()
    original = ExecutorLedger.begin_side_effect_dispatch

    def lose_dispatch(opened: ExecutorLedger, **kwargs: object) -> object:
        original(opened, **cast(Any, kwargs))
        return original(opened, **cast(Any, kwargs))

    monkeypatch.setattr(ExecutorLedger, "begin_side_effect_dispatch", lose_dispatch)
    receipt = execute(page, gate, ledger, clock, token, intent(expected))
    assert receipt.evidence is DouyinDirectMessageActionEvidence.REPLAY_UNCERTAIN
    assert receipt.side_effect_state is SideEffectState.DISPATCHED
    assert receipt.replayed is True
    assert page.clicks == 0


@pytest.mark.parametrize(
    ("click_failure", "evidence"),
    (
        (
            PlaywrightTimeoutError("private timeout"),
            DouyinDirectMessageActionEvidence.DISPATCH_TIMED_OUT,
        ),
        (RuntimeError("private failure"), DouyinDirectMessageActionEvidence.DISPATCH_UNAVAILABLE),
    ),
)
def test_click_failure_is_settled_uncertain_and_never_retried(
    tmp_path: Path,
    click_failure: BaseException,
    evidence: DouyinDirectMessageActionEvidence,
) -> None:
    token, expected = authorization(60)
    gate, ledger, clock = dependencies(tmp_path / evidence.value)
    page = Page()
    page.click_failure = click_failure
    receipt = execute(page, gate, ledger, clock, token, intent(expected))
    assert receipt.state is DouyinDirectMessageActionState.OUTCOME_UNCERTAIN
    assert receipt.evidence is evidence
    assert receipt.side_effect_state is SideEffectState.UNCERTAIN
    assert page.clicks == 1


@pytest.mark.parametrize(
    ("selectors", "url", "evidence"),
    (
        (
            {MESSAGE_INPUT, MESSAGE_SEND, LOGIN_DIALOG},
            PROFILE_URL,
            DouyinDirectMessageActionEvidence.FINAL_LOGIN_REQUIRED,
        ),
        (
            {MESSAGE_INPUT, MESSAGE_SEND, BLOCKING_DIALOG},
            PROFILE_URL,
            DouyinDirectMessageActionEvidence.FINAL_DIALOG_BLOCKED,
        ),
        (
            {MESSAGE_INPUT, MESSAGE_SEND},
            PROFILE_URL,
            DouyinDirectMessageActionEvidence.FINAL_TIMED_OUT,
        ),
        (
            {MESSAGE_INPUT, MESSAGE_SEND},
            "https://www.douyin.com/live",
            DouyinDirectMessageActionEvidence.FINAL_PAGE_VERSION_UNKNOWN,
        ),
        (
            {MESSAGE_INPUT, SECOND_MESSAGE_INPUT, MESSAGE_SEND},
            PROFILE_URL,
            DouyinDirectMessageActionEvidence.FINAL_CONFLICTING_ANCHORS,
        ),
        (
            {MESSAGING_NOT_ALLOWED},
            PROFILE_URL,
            DouyinDirectMessageActionEvidence.FINAL_MESSAGING_NOT_ALLOWED,
        ),
        (
            {FOLLOW_REQUIRED},
            PROFILE_URL,
            DouyinDirectMessageActionEvidence.FINAL_FOLLOW_REQUIRED,
        ),
    ),
)
def test_unconfirmed_post_click_state_is_uncertain(
    tmp_path: Path,
    selectors: set[str],
    url: str,
    evidence: DouyinDirectMessageActionEvidence,
) -> None:
    token, expected = authorization(70)
    gate, ledger, clock = dependencies(tmp_path / evidence.value)
    page = Page()
    page.after_click_selectors = selectors
    page.after_click_url = url
    receipt = execute(page, gate, ledger, clock, token, intent(expected))
    assert receipt.state is DouyinDirectMessageActionState.OUTCOME_UNCERTAIN
    assert receipt.evidence is evidence
    assert receipt.side_effect_state is SideEffectState.UNCERTAIN
    assert page.clicks == 1


def test_page_and_verification_failures_have_closed_stage_specific_receipts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token, expected = authorization(80)
    gate, ledger, clock = dependencies(tmp_path / "ready-page")
    failed_ready = Page()
    failed_ready.locator_failure = True
    ready_receipt = execute(failed_ready, gate, ledger, clock, token, intent(expected))
    assert ready_receipt.evidence is DouyinDirectMessageActionEvidence.READY_PAGE_UNAVAILABLE

    final_token, final_expected = authorization(81)
    final_gate, final_ledger, final_clock = dependencies(tmp_path / "final-page")
    failed_final = Page()
    failed_final.after_click_selectors = {FINAL_CONFIRMATION}

    def reject_final(_page: object) -> object:
        raise RuntimeError("private final drift")

    monkeypatch.setattr(
        "automation_tool.executor.rpa.douyin.direct_message_page.DouyinDirectMessagePage.final_confirmation",
        reject_final,
    )
    final_receipt = execute(
        failed_final,
        final_gate,
        final_ledger,
        final_clock,
        final_token,
        intent(final_expected),
    )
    assert final_receipt.evidence is DouyinDirectMessageActionEvidence.VERIFICATION_UNAVAILABLE
    assert final_receipt.side_effect_state is SideEffectState.UNCERTAIN


def test_uncertain_receipt_keeps_dispatched_fact_when_settlement_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token, expected = authorization(90)
    gate, ledger, clock = dependencies(tmp_path / "state")
    page = Page()
    page.click_failure = RuntimeError("private")

    def reject_settlement(*args: object, **kwargs: object) -> object:
        raise RuntimeError("private settlement")

    monkeypatch.setattr(ExecutorLedger, "mark_side_effect_uncertain", reject_settlement)
    receipt = execute(page, gate, ledger, clock, token, intent(expected))
    assert receipt.state is DouyinDirectMessageActionState.OUTCOME_UNCERTAIN
    assert receipt.side_effect_state is SideEffectState.DISPATCHED
    assert receipt.side_effect_revision == 2


@pytest.mark.parametrize(
    ("reason", "evidence"),
    (
        (
            LocalActionLimitReason.EMERGENCY_STOP,
            DouyinDirectMessageActionEvidence.LOCAL_EMERGENCY_STOP,
        ),
        (
            LocalActionLimitReason.MINIMUM_INTERVAL,
            DouyinDirectMessageActionEvidence.LOCAL_MINIMUM_INTERVAL,
        ),
        (
            LocalActionLimitReason.TASK_ACTION_LIMIT,
            DouyinDirectMessageActionEvidence.LOCAL_TASK_ACTION_LIMIT,
        ),
    ),
)
def test_local_gate_limits_return_no_effect_receipts_before_dom_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reason: LocalActionLimitReason,
    evidence: DouyinDirectMessageActionEvidence,
) -> None:
    token, expected = authorization(100)
    gate, ledger, clock = dependencies(tmp_path / reason.value)
    page = Page()

    def limited(*args: object, **kwargs: object) -> object:
        raise ActionGateLimited(reason)

    monkeypatch.setattr(ExecutorActionGate, "admit", limited)
    receipt = execute(page, gate, ledger, clock, token, intent(expected))
    assert receipt.evidence is evidence
    assert receipt.side_effect_state is None
    assert page.requested_selectors == []


def test_invalid_token_and_ledger_prepare_failure_never_access_dom(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token, expected = authorization(110)
    gate, ledger, clock = dependencies(tmp_path / "invalid-token")
    rejected_page = Page()
    rejected = execute(rejected_page, gate, ledger, clock, token + "x", intent(expected))
    assert rejected.evidence is DouyinDirectMessageActionEvidence.ADMISSION_REJECTED
    assert rejected_page.requested_selectors == []

    other_token, other_expected = authorization(111)
    other_gate, other_ledger, other_clock = dependencies(tmp_path / "ledger")
    ledger_page = Page()

    def reject_prepare(*args: object, **kwargs: object) -> object:
        raise RuntimeError("private")

    monkeypatch.setattr(ExecutorLedger, "prepare_side_effect", reject_prepare)
    unavailable = execute(
        ledger_page,
        other_gate,
        other_ledger,
        other_clock,
        other_token,
        intent(other_expected),
    )
    assert unavailable.evidence is DouyinDirectMessageActionEvidence.LEDGER_UNAVAILABLE
    assert ledger_page.requested_selectors == []


@pytest.mark.parametrize(
    "invalid_time",
    (
        None,
        NOW.replace(tzinfo=None),
        NOW.astimezone(timezone(timedelta(hours=1))),
        NOW.replace(tzinfo=BrokenTimezone()),
        RuntimeError("private clock failure"),
    ),
)
def test_invalid_execution_clock_fails_before_prepared_and_dom(
    tmp_path: Path,
    invalid_time: object,
) -> None:
    token, expected = authorization(115)
    clock = SequenceClock([NOW, NOW, invalid_time])
    gate, ledger, _ = dependencies(tmp_path / type(invalid_time).__name__, clock=clock)
    page = Page()
    receipt = execute(page, gate, ledger, clock, token, intent(expected))
    assert receipt.evidence is DouyinDirectMessageActionEvidence.LEDGER_UNAVAILABLE
    assert ledger.get_side_effect(str(expected.action_id)) is None
    assert page.requested_selectors == []


def test_intent_execution_and_receipt_contracts_are_closed_and_redacted(
    tmp_path: Path,
) -> None:
    token, expected = authorization(120)
    gate, ledger, clock = dependencies(tmp_path / "state")
    valid_intent = intent(expected, "您好 {{target_display_name}}")
    execution = DouyinDirectMessageActionExecution(
        window=BrowserWindow._for_runtime(object(), cast(Any, Page())),
        action_gate=gate,
        ledger=ledger,
        clock=clock,
    )
    assert repr(valid_intent) == "DouyinDirectMessageActionIntent(<redacted>)"
    assert repr(execution) == "DouyinDirectMessageActionExecution(<redacted>)"
    receipt = execution.run(token=token, intent=valid_intent)
    assert "目标账号" not in repr(receipt)
    with pytest.raises(DouyinDirectMessageActionRejected):
        execution.run(token=token, intent=valid_intent)

    _, comment = authorization(121, action=DouyinSearchExposureAction.COMMENT)
    for values in (
        {
            "authorization": comment,
            "message_template": ActionMessageTemplate(source="固定内容"),
            "target_summary": DouyinCandidateSummary(display_name="目标账号", public_handle=None),
        },
        {
            "authorization": expected,
            "message_template": ActionMessageTemplate(source="x" * 477 + "{{target_display_name}}"),
            "target_summary": DouyinCandidateSummary(display_name="目" * 80, public_handle=None),
        },
        {
            "authorization": expected,
            "message_template": ActionMessageTemplate(source="您好 {{target_display_name}}"),
            "target_summary": DouyinCandidateSummary(
                display_name="{{target_display_name}}", public_handle=None
            ),
        },
    ):
        with pytest.raises(DouyinDirectMessageActionRejected):
            DouyinDirectMessageActionIntent(**values)  # type: ignore[arg-type]

    for values in (
        {"window": object(), "action_gate": gate, "ledger": ledger, "clock": clock},
        {
            "window": BrowserWindow._for_runtime(object(), cast(Any, Page())),
            "action_gate": object(),
            "ledger": ledger,
            "clock": clock,
        },
        {
            "window": BrowserWindow._for_runtime(object(), cast(Any, Page())),
            "action_gate": gate,
            "ledger": object(),
            "clock": clock,
        },
        {
            "window": BrowserWindow._for_runtime(object(), cast(Any, Page())),
            "action_gate": gate,
            "ledger": ledger,
            "clock": object(),
        },
    ):
        with pytest.raises(DouyinDirectMessageActionRejected):
            DouyinDirectMessageActionExecution(**values)  # type: ignore[arg-type]

    for changes in (
        {"action_id": cast(ProtocolActionId, str(expected.action_id))},
        {"target_id": cast(ProtocolTargetId, str(expected.target_id))},
        {"state": cast(DouyinDirectMessageActionState, "verified")},
        {"evidence": DouyinDirectMessageActionEvidence.REPLAY_VERIFIED},
        {"side_effect_state": SideEffectState.DISPATCHED},
        {"side_effect_revision": 2},
        {"replayed": True},
        {"execution_version": "private"},
    ):
        with pytest.raises(DouyinDirectMessageActionRejected):
            replace(receipt, **changes)
