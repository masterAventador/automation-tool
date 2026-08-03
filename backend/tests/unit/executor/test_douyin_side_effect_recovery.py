from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest

from automation_tool.executor.browser_runtime import BrowserWindow
from automation_tool.executor.ledger import ExecutorLedger
from automation_tool.executor.rpa.douyin.comment_action import (
    comment_action_verification_fingerprint,
)
from automation_tool.executor.rpa.douyin.direct_message_action import (
    direct_message_action_verification_fingerprint,
)
from automation_tool.executor.rpa.douyin.page_anchors import VISIBLE_MATCH_ENGINE
from automation_tool.executor.rpa.douyin.side_effect_recovery import (
    DOUYIN_SIDE_EFFECT_RECOVERY_VERSION,
    DouyinSideEffectRecovery,
    DouyinSideEffectRecoveryEvidence,
    DouyinSideEffectRecoveryReceipt,
    DouyinSideEffectRecoveryRejected,
    DouyinSideEffectRecoveryState,
)
from automation_tool.executor.side_effect_ledger import LocalSideEffect, SideEffectState
from automation_tool.protocol import (
    ACTION_AUTHORIZATION_VERSION,
    ActionAuthorizationClaims,
    DouyinSearchExposureAction,
    ProtocolActionId,
    ProtocolExecutionAttemptId,
    ProtocolExecutorId,
    ProtocolInstallationId,
    ProtocolTargetId,
    ProtocolTaskId,
    action_authorization_idempotency_key,
)

NOW = datetime(2026, 7, 21, 2, 0, tzinfo=UTC)
INSTALLATION_ID = ProtocolInstallationId("123e4567-e89b-42d3-a456-426614174005")
EXECUTOR_ID = ProtocolExecutorId("123e4567-e89b-42d3-a456-426614174006")
ATTEMPT_ID = ProtocolExecutionAttemptId("123e4567-e89b-42d3-a456-426614174007")
TASK_ID = ProtocolTaskId("123e4567-e89b-42d3-a456-426614174008")
COMMENT_URL = "https://www.douyin.com/video/7351234567890123456"
MESSAGE_URL = "https://www.douyin.com/user/creator-001"
COMMENT_INPUT = 'textarea[aria-label="留下你的精彩评论"]'
SECOND_COMMENT_INPUT = 'textarea[placeholder="留下你的精彩评论"]'
COMMENT_SUBMIT = 'button[aria-label="发表评论"]'
COMMENT_FINAL = '[role="status"]:has-text("评论成功")'
MESSAGE_ENTRY = 'button[aria-label="私信"]'
MESSAGE_INPUT = 'textarea[aria-label="发送私信"]'
MESSAGE_SEND = 'button[aria-label="发送私信"]'
MESSAGE_FINAL = '[role="status"]:has-text("私信发送成功")'
MESSAGING_NOT_ALLOWED = '[role="alert"]:has-text("暂时无法私信")'
FOLLOW_REQUIRED = '[role="alert"]:has-text("关注后才能私信")'
LOGIN_DIALOG = '[role="dialog"]:has-text("扫码登录")'
BLOCKING_DIALOG = '[role="dialog"]'


class Clock:
    def __init__(self, value: object = NOW + timedelta(seconds=4)) -> None:
        self.value = value

    def now(self) -> datetime:
        if isinstance(self.value, BaseException):
            raise self.value
        return cast(datetime, self.value)


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


class Page:
    def __init__(
        self,
        *,
        url: str,
        visible_selectors: set[str],
        locator_failure: bool = False,
    ) -> None:
        self.url = url
        self.visible_selectors = visible_selectors
        self.locator_failure = locator_failure
        self.requested_selectors: list[str] = []

    def locator(self, selector: str) -> Locator:
        self.requested_selectors.append(selector)
        return Locator(selector, self)


def resource_id(index: int, kind: type[str]) -> str:
    return kind(str(UUID(f"723e4567-e89b-42d3-a456-{index:012d}")))


def ledger(state_directory: Path) -> ExecutorLedger:
    return ExecutorLedger(
        state_directory=state_directory,
        installation_id=str(INSTALLATION_ID),
        executor_id=str(EXECUTOR_ID),
    )


def seed(
    state_directory: Path,
    index: int,
    *,
    action: DouyinSearchExposureAction,
    state: SideEffectState,
) -> tuple[ExecutorLedger, ProtocolActionId, bytes]:
    opened = ledger(state_directory)
    action_id = ProtocolActionId(resource_id(index, str))
    claims = ActionAuthorizationClaims(
        version=ACTION_AUTHORIZATION_VERSION,
        action_id=action_id,
        target_id=ProtocolTargetId(resource_id(index + 100, str)),
        execution_attempt_id=ATTEMPT_ID,
        task_id=TASK_ID,
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
        platform="douyin",
        action=action,
        idempotency_key=action_authorization_idempotency_key(action_id),
        authorized_at=NOW - timedelta(seconds=1),
        deadline_at=NOW + timedelta(minutes=4),
    )
    opened.admit_action(
        claims=claims,
        authorization_fingerprint=hashlib.sha256(f"authorization-{index}".encode()).digest(),
        admitted_at=NOW,
        minimum_interval_seconds=1,
        task_action_limit=100,
    )
    fingerprint = hashlib.sha256(f"effect-{index}".encode()).digest()
    opened.prepare_side_effect(
        action_id=str(action_id),
        effect_fingerprint=fingerprint,
        prepared_at=NOW + timedelta(seconds=1),
    )
    if state is not SideEffectState.PREPARED:
        opened.begin_side_effect_dispatch(
            action_id=str(action_id),
            effect_fingerprint=fingerprint,
            dispatched_at=NOW + timedelta(seconds=2),
        )
    if state is SideEffectState.VERIFIED:
        opened.verify_side_effect(
            action_id=str(action_id),
            effect_fingerprint=fingerprint,
            verification_fingerprint=hashlib.sha256(b"existing proof").digest(),
            verified_at=NOW + timedelta(seconds=3),
        )
    elif state is SideEffectState.UNCERTAIN:
        opened.mark_side_effect_uncertain(
            action_id=str(action_id),
            effect_fingerprint=fingerprint,
            uncertain_at=NOW + timedelta(seconds=3),
        )
    return opened, action_id, fingerprint


def recover(
    opened: ExecutorLedger,
    action_id: ProtocolActionId,
    page: Page,
    *,
    clock: Clock | None = None,
) -> DouyinSideEffectRecoveryReceipt:
    return DouyinSideEffectRecovery(
        window=BrowserWindow._for_runtime(object(), cast(Any, page)),
        ledger=opened,
        clock=Clock() if clock is None else clock,
    ).run(action_id=action_id)


@pytest.mark.parametrize(
    ("action", "url", "final_selector", "evidence"),
    (
        (
            DouyinSearchExposureAction.COMMENT,
            COMMENT_URL,
            COMMENT_FINAL,
            DouyinSideEffectRecoveryEvidence.COMMENT_CONFIRMED,
        ),
        (
            DouyinSearchExposureAction.DIRECT_MESSAGE,
            MESSAGE_URL,
            MESSAGE_FINAL,
            DouyinSideEffectRecoveryEvidence.MESSAGE_CONFIRMED,
        ),
    ),
)
def test_dispatched_effect_is_read_only_verified_from_final_page_fact(
    tmp_path: Path,
    action: DouyinSearchExposureAction,
    url: str,
    final_selector: str,
    evidence: DouyinSideEffectRecoveryEvidence,
) -> None:
    opened, action_id, fingerprint = seed(
        tmp_path / action.value,
        1,
        action=action,
        state=SideEffectState.DISPATCHED,
    )
    page = Page(url=url, visible_selectors={final_selector})

    receipt = recover(opened, action_id, page)

    assert receipt.state is DouyinSideEffectRecoveryState.VERIFIED
    assert receipt.evidence is evidence
    assert receipt.side_effect_state is SideEffectState.VERIFIED
    assert receipt.side_effect_revision == 3
    assert receipt.replayed is False
    assert receipt.completed is True and receipt.circuit_open is False
    assert str(action_id) not in repr(receipt)
    persisted = opened.get_side_effect(str(action_id))
    assert persisted is not None
    expected = (
        comment_action_verification_fingerprint(fingerprint)
        if action is DouyinSearchExposureAction.COMMENT
        else direct_message_action_verification_fingerprint(fingerprint)
    )
    assert persisted.verification_fingerprint == expected


@pytest.mark.parametrize(
    ("action", "url", "selectors", "locator_failure", "evidence"),
    (
        (
            DouyinSearchExposureAction.COMMENT,
            COMMENT_URL,
            {COMMENT_INPUT, COMMENT_SUBMIT},
            False,
            DouyinSideEffectRecoveryEvidence.FINAL_TIMED_OUT,
        ),
        (
            DouyinSearchExposureAction.COMMENT,
            COMMENT_URL,
            {LOGIN_DIALOG},
            False,
            DouyinSideEffectRecoveryEvidence.LOGIN_REQUIRED,
        ),
        (
            DouyinSearchExposureAction.COMMENT,
            COMMENT_URL,
            {BLOCKING_DIALOG},
            False,
            DouyinSideEffectRecoveryEvidence.DIALOG_BLOCKED,
        ),
        (
            DouyinSearchExposureAction.COMMENT,
            "https://www.douyin.com/live",
            set(),
            False,
            DouyinSideEffectRecoveryEvidence.PAGE_VERSION_UNKNOWN,
        ),
        (
            DouyinSearchExposureAction.COMMENT,
            COMMENT_URL,
            {COMMENT_INPUT, SECOND_COMMENT_INPUT, COMMENT_SUBMIT},
            False,
            DouyinSideEffectRecoveryEvidence.CONFLICTING_ANCHORS,
        ),
        (
            DouyinSearchExposureAction.COMMENT,
            COMMENT_URL,
            set(),
            False,
            DouyinSideEffectRecoveryEvidence.FINAL_TIMED_OUT,
        ),
        (
            DouyinSearchExposureAction.COMMENT,
            COMMENT_URL,
            set(),
            True,
            DouyinSideEffectRecoveryEvidence.PAGE_UNAVAILABLE,
        ),
        (
            DouyinSearchExposureAction.DIRECT_MESSAGE,
            MESSAGE_URL,
            {MESSAGE_ENTRY},
            False,
            DouyinSideEffectRecoveryEvidence.FINAL_TIMED_OUT,
        ),
        (
            DouyinSearchExposureAction.DIRECT_MESSAGE,
            MESSAGE_URL,
            {MESSAGE_INPUT, MESSAGE_SEND},
            False,
            DouyinSideEffectRecoveryEvidence.FINAL_TIMED_OUT,
        ),
        (
            DouyinSearchExposureAction.DIRECT_MESSAGE,
            MESSAGE_URL,
            {MESSAGING_NOT_ALLOWED},
            False,
            DouyinSideEffectRecoveryEvidence.MESSAGING_NOT_ALLOWED,
        ),
        (
            DouyinSearchExposureAction.DIRECT_MESSAGE,
            MESSAGE_URL,
            {FOLLOW_REQUIRED},
            False,
            DouyinSideEffectRecoveryEvidence.FOLLOW_REQUIRED,
        ),
        (
            DouyinSearchExposureAction.DIRECT_MESSAGE,
            MESSAGE_URL,
            {LOGIN_DIALOG},
            False,
            DouyinSideEffectRecoveryEvidence.LOGIN_REQUIRED,
        ),
        (
            DouyinSearchExposureAction.DIRECT_MESSAGE,
            MESSAGE_URL,
            {BLOCKING_DIALOG},
            False,
            DouyinSideEffectRecoveryEvidence.DIALOG_BLOCKED,
        ),
        (
            DouyinSearchExposureAction.DIRECT_MESSAGE,
            "https://www.douyin.com/live",
            set(),
            False,
            DouyinSideEffectRecoveryEvidence.PAGE_VERSION_UNKNOWN,
        ),
        (
            DouyinSearchExposureAction.DIRECT_MESSAGE,
            MESSAGE_URL,
            {MESSAGE_ENTRY, MESSAGE_INPUT},
            False,
            DouyinSideEffectRecoveryEvidence.CONFLICTING_ANCHORS,
        ),
        (
            DouyinSearchExposureAction.DIRECT_MESSAGE,
            MESSAGE_URL,
            set(),
            False,
            DouyinSideEffectRecoveryEvidence.FINAL_TIMED_OUT,
        ),
        (
            DouyinSearchExposureAction.DIRECT_MESSAGE,
            MESSAGE_URL,
            set(),
            True,
            DouyinSideEffectRecoveryEvidence.PAGE_UNAVAILABLE,
        ),
    ),
)
def test_unconfirmed_page_fact_settles_uncertain_without_any_action(
    tmp_path: Path,
    action: DouyinSearchExposureAction,
    url: str,
    selectors: set[str],
    locator_failure: bool,
    evidence: DouyinSideEffectRecoveryEvidence,
) -> None:
    opened, action_id, _ = seed(
        tmp_path / f"{action.value}-{evidence.value}-{locator_failure}",
        10,
        action=action,
        state=SideEffectState.DISPATCHED,
    )
    page = Page(url=url, visible_selectors=selectors, locator_failure=locator_failure)

    receipt = recover(opened, action_id, page)

    assert receipt.state is DouyinSideEffectRecoveryState.OUTCOME_UNCERTAIN
    assert receipt.evidence is evidence
    assert receipt.side_effect_state is SideEffectState.UNCERTAIN
    assert receipt.side_effect_revision == 3
    assert receipt.replayed is False
    assert receipt.completed is False and receipt.circuit_open is True


@pytest.mark.parametrize(
    ("state", "expected_state", "evidence", "replayed"),
    (
        (
            SideEffectState.PREPARED,
            DouyinSideEffectRecoveryState.NOT_DISPATCHED,
            DouyinSideEffectRecoveryEvidence.PREPARED_NOT_DISPATCHED,
            False,
        ),
        (
            SideEffectState.VERIFIED,
            DouyinSideEffectRecoveryState.VERIFIED,
            DouyinSideEffectRecoveryEvidence.ALREADY_VERIFIED,
            True,
        ),
        (
            SideEffectState.UNCERTAIN,
            DouyinSideEffectRecoveryState.OUTCOME_UNCERTAIN,
            DouyinSideEffectRecoveryEvidence.ALREADY_UNCERTAIN,
            True,
        ),
    ),
)
def test_non_dispatched_or_terminal_fact_returns_without_dom_access(
    tmp_path: Path,
    state: SideEffectState,
    expected_state: DouyinSideEffectRecoveryState,
    evidence: DouyinSideEffectRecoveryEvidence,
    replayed: bool,
) -> None:
    opened, action_id, _ = seed(
        tmp_path / state.value,
        30,
        action=DouyinSearchExposureAction.COMMENT,
        state=state,
    )
    page = Page(url=COMMENT_URL, visible_selectors={COMMENT_FINAL})

    receipt = recover(opened, action_id, page)

    assert receipt.state is expected_state
    assert receipt.evidence is evidence
    assert receipt.replayed is replayed
    assert page.requested_selectors == []


@pytest.mark.parametrize(
    ("module_path", "action", "url", "final_selector"),
    (
        (
            "automation_tool.executor.rpa.douyin.comment_page.DouyinCommentPage",
            DouyinSearchExposureAction.COMMENT,
            COMMENT_URL,
            COMMENT_FINAL,
        ),
        (
            "automation_tool.executor.rpa.douyin.direct_message_page.DouyinDirectMessagePage",
            DouyinSearchExposureAction.DIRECT_MESSAGE,
            MESSAGE_URL,
            MESSAGE_FINAL,
        ),
    ),
)
def test_page_observation_exception_settles_uncertain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    module_path: str,
    action: DouyinSearchExposureAction,
    url: str,
    final_selector: str,
) -> None:
    opened, action_id, _ = seed(
        tmp_path / action.value,
        40,
        action=action,
        state=SideEffectState.DISPATCHED,
    )

    def reject_wait(*args: object, **kwargs: object) -> object:
        raise RuntimeError("private page failure")

    monkeypatch.setattr(f"{module_path}.wait_for_final", reject_wait)
    receipt = recover(
        opened,
        action_id,
        Page(url=url, visible_selectors={final_selector}),
    )
    assert receipt.evidence is DouyinSideEffectRecoveryEvidence.PAGE_UNAVAILABLE
    assert receipt.side_effect_state is SideEffectState.UNCERTAIN


def test_final_recheck_and_verification_failure_never_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened, action_id, _ = seed(
        tmp_path / "final",
        50,
        action=DouyinSearchExposureAction.COMMENT,
        state=SideEffectState.DISPATCHED,
    )

    def reject_final(*args: object, **kwargs: object) -> object:
        raise RuntimeError("private final drift")

    monkeypatch.setattr(
        "automation_tool.executor.rpa.douyin.comment_page.DouyinCommentPage.final_confirmation",
        reject_final,
    )
    drifted = recover(
        opened,
        action_id,
        Page(url=COMMENT_URL, visible_selectors={COMMENT_FINAL}),
    )
    assert drifted.evidence is DouyinSideEffectRecoveryEvidence.VERIFICATION_UNAVAILABLE
    assert drifted.side_effect_state is SideEffectState.UNCERTAIN

    second, second_id, _ = seed(
        tmp_path / "verify",
        51,
        action=DouyinSearchExposureAction.DIRECT_MESSAGE,
        state=SideEffectState.DISPATCHED,
    )

    def reject_verify(*args: object, **kwargs: object) -> object:
        raise RuntimeError("private ledger failure")

    monkeypatch.setattr(ExecutorLedger, "verify_side_effect", reject_verify)
    failed = recover(
        second,
        second_id,
        Page(url=MESSAGE_URL, visible_selectors={MESSAGE_FINAL}),
    )
    assert failed.evidence is DouyinSideEffectRecoveryEvidence.VERIFICATION_UNAVAILABLE
    assert failed.side_effect_state is SideEffectState.UNCERTAIN


def test_message_final_recheck_failure_settles_uncertain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened, action_id, _ = seed(
        tmp_path / "state",
        52,
        action=DouyinSearchExposureAction.DIRECT_MESSAGE,
        state=SideEffectState.DISPATCHED,
    )

    def reject_final(*args: object, **kwargs: object) -> object:
        raise RuntimeError("private final drift")

    monkeypatch.setattr(
        "automation_tool.executor.rpa.douyin.direct_message_page."
        "DouyinDirectMessagePage.final_confirmation",
        reject_final,
    )
    receipt = recover(
        opened,
        action_id,
        Page(url=MESSAGE_URL, visible_selectors={MESSAGE_FINAL}),
    )
    assert receipt.evidence is DouyinSideEffectRecoveryEvidence.VERIFICATION_UNAVAILABLE
    assert receipt.side_effect_state is SideEffectState.UNCERTAIN


def test_settlement_failure_keeps_dispatched_fact_and_original_page_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened, action_id, _ = seed(
        tmp_path / "state",
        60,
        action=DouyinSearchExposureAction.COMMENT,
        state=SideEffectState.DISPATCHED,
    )

    def reject_settlement(*args: object, **kwargs: object) -> object:
        raise RuntimeError("private settlement")

    monkeypatch.setattr(ExecutorLedger, "mark_side_effect_uncertain", reject_settlement)
    receipt = recover(
        opened,
        action_id,
        Page(url=COMMENT_URL, visible_selectors={COMMENT_INPUT, COMMENT_SUBMIT}),
    )
    assert receipt.evidence is DouyinSideEffectRecoveryEvidence.FINAL_TIMED_OUT
    assert receipt.side_effect_state is SideEffectState.DISPATCHED
    assert receipt.side_effect_revision == 2


def test_settlement_race_replays_the_winning_terminal_fact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened, action_id, _ = seed(
        tmp_path / "uncertain",
        70,
        action=DouyinSearchExposureAction.COMMENT,
        state=SideEffectState.DISPATCHED,
    )
    original_uncertain = ExecutorLedger.mark_side_effect_uncertain

    def race_uncertain(current: ExecutorLedger, **kwargs: object) -> LocalSideEffect:
        original_uncertain(current, **cast(Any, kwargs))
        return original_uncertain(current, **cast(Any, kwargs))

    monkeypatch.setattr(ExecutorLedger, "mark_side_effect_uncertain", race_uncertain)
    uncertain = recover(
        opened,
        action_id,
        Page(url=COMMENT_URL, visible_selectors={COMMENT_INPUT, COMMENT_SUBMIT}),
    )
    assert uncertain.evidence is DouyinSideEffectRecoveryEvidence.ALREADY_UNCERTAIN
    assert uncertain.replayed is True

    verified_ledger, verified_id, fingerprint = seed(
        tmp_path / "verified",
        71,
        action=DouyinSearchExposureAction.COMMENT,
        state=SideEffectState.DISPATCHED,
    )
    original_verify = ExecutorLedger.verify_side_effect

    def verify_then_raise(current: ExecutorLedger, **kwargs: object) -> object:
        original_verify(current, **cast(Any, kwargs))
        raise RuntimeError("private post-commit failure")

    monkeypatch.setattr(ExecutorLedger, "verify_side_effect", verify_then_raise)
    verified = recover(
        verified_ledger,
        verified_id,
        Page(url=COMMENT_URL, visible_selectors={COMMENT_FINAL}),
    )
    assert verified.evidence is DouyinSideEffectRecoveryEvidence.ALREADY_VERIFIED
    assert verified.replayed is True
    persisted = verified_ledger.get_side_effect(str(verified_id))
    assert persisted is not None
    assert persisted.verification_fingerprint == comment_action_verification_fingerprint(
        fingerprint
    )


def test_verification_and_settlement_same_terminal_replays_are_projected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verified_ledger, verified_id, _ = seed(
        tmp_path / "verified",
        72,
        action=DouyinSearchExposureAction.COMMENT,
        state=SideEffectState.DISPATCHED,
    )
    original_verify = ExecutorLedger.verify_side_effect

    def race_verify(current: ExecutorLedger, **kwargs: object) -> LocalSideEffect:
        original_verify(current, **cast(Any, kwargs))
        return original_verify(current, **cast(Any, kwargs))

    monkeypatch.setattr(ExecutorLedger, "verify_side_effect", race_verify)
    verified = recover(
        verified_ledger,
        verified_id,
        Page(url=COMMENT_URL, visible_selectors={COMMENT_FINAL}),
    )
    assert verified.evidence is DouyinSideEffectRecoveryEvidence.ALREADY_VERIFIED
    assert verified.replayed is True

    uncertain_ledger, uncertain_id, _ = seed(
        tmp_path / "uncertain",
        73,
        action=DouyinSearchExposureAction.COMMENT,
        state=SideEffectState.DISPATCHED,
    )
    original_uncertain = ExecutorLedger.mark_side_effect_uncertain

    def settle_then_raise(current: ExecutorLedger, **kwargs: object) -> object:
        original_uncertain(current, **cast(Any, kwargs))
        raise RuntimeError("private post-commit failure")

    monkeypatch.setattr(ExecutorLedger, "mark_side_effect_uncertain", settle_then_raise)
    uncertain = recover(
        uncertain_ledger,
        uncertain_id,
        Page(url=COMMENT_URL, visible_selectors={COMMENT_INPUT, COMMENT_SUBMIT}),
    )
    assert uncertain.evidence is DouyinSideEffectRecoveryEvidence.ALREADY_UNCERTAIN
    assert uncertain.replayed is True


def test_verification_race_losing_to_uncertain_returns_terminal_fact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened, action_id, _ = seed(
        tmp_path / "state",
        80,
        action=DouyinSearchExposureAction.DIRECT_MESSAGE,
        state=SideEffectState.DISPATCHED,
    )
    original = ExecutorLedger.mark_side_effect_uncertain

    def uncertain_then_reject(current: ExecutorLedger, *args: object, **kwargs: object) -> object:
        effect = current.get_side_effect(str(action_id))
        assert effect is not None
        original(
            current,
            action_id=effect.action_id,
            effect_fingerprint=effect.effect_fingerprint,
            uncertain_at=NOW + timedelta(seconds=4),
        )
        raise RuntimeError("private terminal race")

    monkeypatch.setattr(ExecutorLedger, "verify_side_effect", uncertain_then_reject)
    receipt = recover(
        opened,
        action_id,
        Page(url=MESSAGE_URL, visible_selectors={MESSAGE_FINAL}),
    )
    assert receipt.evidence is DouyinSideEffectRecoveryEvidence.ALREADY_UNCERTAIN
    assert receipt.replayed is True


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
def test_invalid_clock_cannot_settle_and_preserves_dispatched(
    tmp_path: Path,
    invalid_time: object,
) -> None:
    opened, action_id, _ = seed(
        tmp_path / type(invalid_time).__name__,
        90,
        action=DouyinSearchExposureAction.COMMENT,
        state=SideEffectState.DISPATCHED,
    )
    receipt = recover(
        opened,
        action_id,
        Page(url=COMMENT_URL, visible_selectors={COMMENT_INPUT, COMMENT_SUBMIT}),
        clock=Clock(invalid_time),
    )
    assert receipt.state is DouyinSideEffectRecoveryState.OUTCOME_UNCERTAIN
    assert receipt.side_effect_state is SideEffectState.DISPATCHED


def test_current_read_failure_uses_the_original_dispatched_fact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened, action_id, _ = seed(
        tmp_path / "state",
        100,
        action=DouyinSearchExposureAction.COMMENT,
        state=SideEffectState.DISPATCHED,
    )
    original_get = ExecutorLedger.get_side_effect
    calls = 0

    def first_then_reject(current: ExecutorLedger, requested: str) -> LocalSideEffect | None:
        nonlocal calls
        calls += 1
        if calls == 1:
            return original_get(current, requested)
        raise RuntimeError("private read failure")

    def reject_settlement(*args: object, **kwargs: object) -> object:
        raise RuntimeError("private settlement")

    monkeypatch.setattr(ExecutorLedger, "get_side_effect", first_then_reject)
    monkeypatch.setattr(ExecutorLedger, "mark_side_effect_uncertain", reject_settlement)
    receipt = recover(
        opened,
        action_id,
        Page(url=COMMENT_URL, visible_selectors={COMMENT_INPUT, COMMENT_SUBMIT}),
    )
    assert receipt.side_effect_state is SideEffectState.DISPATCHED
    assert calls == 2


def test_missing_current_read_uses_the_original_dispatched_fact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened, action_id, _ = seed(
        tmp_path / "state",
        101,
        action=DouyinSearchExposureAction.COMMENT,
        state=SideEffectState.DISPATCHED,
    )
    original_get = ExecutorLedger.get_side_effect
    calls = 0

    def first_then_missing(current: ExecutorLedger, requested: str) -> LocalSideEffect | None:
        nonlocal calls
        calls += 1
        return original_get(current, requested) if calls == 1 else None

    def reject_settlement(*args: object, **kwargs: object) -> object:
        raise RuntimeError("private settlement")

    monkeypatch.setattr(ExecutorLedger, "get_side_effect", first_then_missing)
    monkeypatch.setattr(ExecutorLedger, "mark_side_effect_uncertain", reject_settlement)
    receipt = recover(
        opened,
        action_id,
        Page(url=COMMENT_URL, visible_selectors={COMMENT_INPUT, COMMENT_SUBMIT}),
    )
    assert receipt.side_effect_state is SideEffectState.DISPATCHED
    assert calls == 2


def test_recovery_constructor_run_and_receipt_contracts_are_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened, action_id, _ = seed(
        tmp_path / "state",
        110,
        action=DouyinSearchExposureAction.COMMENT,
        state=SideEffectState.DISPATCHED,
    )
    window = BrowserWindow._for_runtime(
        object(),
        cast(Any, Page(url=COMMENT_URL, visible_selectors={COMMENT_FINAL})),
    )
    recovery = DouyinSideEffectRecovery(window=window, ledger=opened, clock=Clock())
    assert repr(recovery) == "DouyinSideEffectRecovery(<redacted>)"
    receipt = recovery.run(action_id=action_id)
    assert DOUYIN_SIDE_EFFECT_RECOVERY_VERSION == "douyin.side-effect-recovery.v1"
    assert "723e4567" not in repr(receipt)
    with pytest.raises(DouyinSideEffectRecoveryRejected):
        recovery.run(action_id=action_id)

    for values in (
        {"window": object(), "ledger": opened, "clock": Clock()},
        {"window": window, "ledger": object(), "clock": Clock()},
        {"window": window, "ledger": opened, "clock": object()},
    ):
        with pytest.raises(DouyinSideEffectRecoveryRejected):
            DouyinSideEffectRecovery(**values)  # type: ignore[arg-type]

    with pytest.raises(DouyinSideEffectRecoveryRejected):
        DouyinSideEffectRecovery(window=window, ledger=opened, clock=Clock()).run(
            action_id=cast(ProtocolActionId, str(action_id))
        )

    missing = ProtocolActionId(resource_id(999, str))
    with pytest.raises(DouyinSideEffectRecoveryRejected):
        DouyinSideEffectRecovery(window=window, ledger=opened, clock=Clock()).run(action_id=missing)

    def reject_read(*args: object, **kwargs: object) -> object:
        raise RuntimeError("private ledger failure")

    monkeypatch.setattr(ExecutorLedger, "get_side_effect", reject_read)
    with pytest.raises(DouyinSideEffectRecoveryRejected):
        DouyinSideEffectRecovery(window=window, ledger=opened, clock=Clock()).run(
            action_id=action_id
        )

    for changes in (
        {"action_id": cast(ProtocolActionId, str(receipt.action_id))},
        {"target_id": cast(ProtocolTargetId, str(receipt.target_id))},
        {"action": DouyinSearchExposureAction.BROWSE},
        {"state": cast(DouyinSideEffectRecoveryState, "verified")},
        {"evidence": DouyinSideEffectRecoveryEvidence.ALREADY_VERIFIED},
        {"side_effect_state": SideEffectState.DISPATCHED},
        {"side_effect_revision": True},
        {"replayed": True},
        {"recovery_version": "private"},
    ):
        with pytest.raises(DouyinSideEffectRecoveryRejected):
            replace(receipt, **changes)


def test_a_crashed_process_settles_uncertain_because_it_has_no_page_to_read(
    tmp_path: Path,
) -> None:
    """After a crash the window is gone; a dispatched effect can only stay uncertain."""
    opened, action_id, _ = seed(
        tmp_path / "recovery",
        60,
        action=DouyinSearchExposureAction.COMMENT,
        state=SideEffectState.DISPATCHED,
    )

    recovery = DouyinSideEffectRecovery.without_page_context(ledger=opened, clock=Clock())
    receipt = recovery.run(action_id=action_id)

    assert receipt.state is DouyinSideEffectRecoveryState.OUTCOME_UNCERTAIN
    assert receipt.evidence is DouyinSideEffectRecoveryEvidence.PAGE_UNAVAILABLE
    assert repr(recovery) == "DouyinSideEffectRecovery(<redacted>)"
    recorded = opened.get_side_effect(str(action_id))
    assert recorded is not None
    assert recorded.state is SideEffectState.UNCERTAIN


def test_a_pageless_recovery_still_refuses_dependencies_it_cannot_trust(tmp_path: Path) -> None:
    opened = ledger(tmp_path / "recovery")
    for arguments in (
        {"ledger": cast(Any, object()), "clock": Clock()},
        {"ledger": opened, "clock": cast(Any, object())},
    ):
        with pytest.raises(DouyinSideEffectRecoveryRejected):
            DouyinSideEffectRecovery.without_page_context(**arguments)
