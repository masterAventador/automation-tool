"""Single-shot Douyin comment execution behind signed and durable local authority."""

from __future__ import annotations

import hashlib
import json
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol, cast, runtime_checkable

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from automation_tool.executor.action_authorization import ActionAuthorizationExpectation
from automation_tool.executor.action_gate import (
    ActionGateLimited,
    ActionGateRejected,
    ExecutorActionGate,
    LocalActionLimitReason,
)
from automation_tool.executor.browser_runtime import BrowserWindow
from automation_tool.executor.ledger import ExecutorLedger
from automation_tool.executor.rpa.douyin.comment_page import (
    DOUYIN_COMMENT_PAGE_SELECTOR_VERSION,
    DouyinCommentPage,
    DouyinCommentPageEvidence,
    DouyinCommentPageObservation,
    DouyinCommentPageState,
)
from automation_tool.executor.side_effect_ledger import LocalSideEffect, SideEffectState
from automation_tool.protocol import (
    ACTION_MESSAGE_TEMPLATE_VERSION,
    ActionMessageTemplate,
    DouyinCandidateSummary,
    DouyinSearchExposureAction,
    ProtocolActionId,
    ProtocolTargetId,
)

DOUYIN_COMMENT_ACTION_EXECUTION_VERSION = "douyin.comment-action-execution.v1"
_EFFECT_FINGERPRINT_DOMAIN = "automation-tool.douyin.comment-effect.v1"
_VERIFICATION_FINGERPRINT_DOMAIN = b"automation-tool.douyin.comment-verification.v1\0"
_PAGE_READY_TIMEOUT_MILLISECONDS = 10_000
_ACTION_TIMEOUT_MILLISECONDS = 15_000
_FINAL_TIMEOUT_MILLISECONDS = 10_000


class DouyinCommentActionRejected(RuntimeError):
    def __init__(self) -> None:
        super().__init__("douyin comment action execution is unavailable")


class DouyinCommentActionState(StrEnum):
    NOT_DISPATCHED = "not_dispatched"
    VERIFIED = "verified"
    OUTCOME_UNCERTAIN = "outcome_uncertain"


class DouyinCommentActionEvidence(StrEnum):
    ADMISSION_REJECTED = "admission_rejected"
    LOCAL_EMERGENCY_STOP = "local_emergency_stop"
    LOCAL_MINIMUM_INTERVAL = "local_minimum_interval"
    LOCAL_TASK_ACTION_LIMIT = "local_task_action_limit"
    LEDGER_UNAVAILABLE = "ledger_unavailable"
    READY_LOGIN_REQUIRED = "ready_login_required"
    READY_DIALOG_BLOCKED = "ready_dialog_blocked"
    READY_TIMED_OUT = "ready_timed_out"
    READY_PAGE_VERSION_UNKNOWN = "ready_page_version_unknown"
    READY_CONFLICTING_ANCHORS = "ready_conflicting_anchors"
    READY_PAGE_UNAVAILABLE = "ready_page_unavailable"
    STALE_CONFIRMATION = "stale_confirmation"
    PREPARE_UNAVAILABLE = "prepare_unavailable"
    DISPATCH_PERMISSION_REJECTED = "dispatch_permission_rejected"
    DISPATCH_TIMED_OUT = "dispatch_timed_out"
    DISPATCH_UNAVAILABLE = "dispatch_unavailable"
    FINAL_LOGIN_REQUIRED = "final_login_required"
    FINAL_DIALOG_BLOCKED = "final_dialog_blocked"
    FINAL_TIMED_OUT = "final_timed_out"
    FINAL_PAGE_VERSION_UNKNOWN = "final_page_version_unknown"
    FINAL_CONFLICTING_ANCHORS = "final_conflicting_anchors"
    FINAL_PAGE_UNAVAILABLE = "final_page_unavailable"
    VERIFICATION_UNAVAILABLE = "verification_unavailable"
    COMMENT_CONFIRMED = "comment_confirmed"
    REPLAY_VERIFIED = "replay_verified"
    REPLAY_UNCERTAIN = "replay_uncertain"


_NO_EFFECT_EVIDENCE = frozenset(
    {
        DouyinCommentActionEvidence.ADMISSION_REJECTED,
        DouyinCommentActionEvidence.LOCAL_EMERGENCY_STOP,
        DouyinCommentActionEvidence.LOCAL_MINIMUM_INTERVAL,
        DouyinCommentActionEvidence.LOCAL_TASK_ACTION_LIMIT,
        DouyinCommentActionEvidence.LEDGER_UNAVAILABLE,
    }
)
_PREPARED_EVIDENCE = frozenset(
    {
        DouyinCommentActionEvidence.READY_LOGIN_REQUIRED,
        DouyinCommentActionEvidence.READY_DIALOG_BLOCKED,
        DouyinCommentActionEvidence.READY_TIMED_OUT,
        DouyinCommentActionEvidence.READY_PAGE_VERSION_UNKNOWN,
        DouyinCommentActionEvidence.READY_CONFLICTING_ANCHORS,
        DouyinCommentActionEvidence.READY_PAGE_UNAVAILABLE,
        DouyinCommentActionEvidence.STALE_CONFIRMATION,
        DouyinCommentActionEvidence.PREPARE_UNAVAILABLE,
        DouyinCommentActionEvidence.DISPATCH_PERMISSION_REJECTED,
    }
)
_POST_DISPATCH_EVIDENCE = frozenset(
    {
        DouyinCommentActionEvidence.DISPATCH_TIMED_OUT,
        DouyinCommentActionEvidence.DISPATCH_UNAVAILABLE,
        DouyinCommentActionEvidence.FINAL_LOGIN_REQUIRED,
        DouyinCommentActionEvidence.FINAL_DIALOG_BLOCKED,
        DouyinCommentActionEvidence.FINAL_TIMED_OUT,
        DouyinCommentActionEvidence.FINAL_PAGE_VERSION_UNKNOWN,
        DouyinCommentActionEvidence.FINAL_CONFLICTING_ANCHORS,
        DouyinCommentActionEvidence.FINAL_PAGE_UNAVAILABLE,
        DouyinCommentActionEvidence.VERIFICATION_UNAVAILABLE,
        DouyinCommentActionEvidence.REPLAY_UNCERTAIN,
    }
)


@dataclass(frozen=True, slots=True, repr=False)
class DouyinCommentActionIntent:
    authorization: ActionAuthorizationExpectation
    message_template: ActionMessageTemplate
    target_summary: DouyinCandidateSummary
    _rendered_message: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        try:
            if (
                not isinstance(self.authorization, ActionAuthorizationExpectation)
                or self.authorization.action is not DouyinSearchExposureAction.COMMENT
                or not isinstance(self.message_template, ActionMessageTemplate)
                or not isinstance(self.target_summary, DouyinCandidateSummary)
            ):
                raise ValueError
            rendered = self.message_template.source.replace(
                "{{target_display_name}}", self.target_summary.display_name
            )
            rendered_policy = ActionMessageTemplate(source=rendered)
            if rendered_policy.variables:
                raise ValueError
            object.__setattr__(self, "_rendered_message", rendered)
        except Exception:
            raise DouyinCommentActionRejected from None

    def __repr__(self) -> str:
        return "DouyinCommentActionIntent(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class DouyinCommentActionReceipt:
    action_id: ProtocolActionId
    target_id: ProtocolTargetId
    state: DouyinCommentActionState
    evidence: DouyinCommentActionEvidence
    side_effect_state: SideEffectState | None
    side_effect_revision: int | None
    replayed: bool
    execution_version: str = DOUYIN_COMMENT_ACTION_EXECUTION_VERSION

    def __post_init__(self) -> None:
        no_effect = (
            self.state is DouyinCommentActionState.NOT_DISPATCHED
            and self.evidence in _NO_EFFECT_EVIDENCE
            and self.side_effect_state is None
            and self.side_effect_revision is None
            and self.replayed is False
        )
        prepared = (
            self.state is DouyinCommentActionState.NOT_DISPATCHED
            and self.evidence in _PREPARED_EVIDENCE
            and self.side_effect_state is SideEffectState.PREPARED
            and self.side_effect_revision == 1
            and self.replayed is False
        )
        verified = (
            self.state is DouyinCommentActionState.VERIFIED
            and self.evidence
            in {
                DouyinCommentActionEvidence.COMMENT_CONFIRMED,
                DouyinCommentActionEvidence.REPLAY_VERIFIED,
            }
            and self.side_effect_state is SideEffectState.VERIFIED
            and self.side_effect_revision == 3
            and self.replayed is (self.evidence is DouyinCommentActionEvidence.REPLAY_VERIFIED)
        )
        uncertain = (
            self.state is DouyinCommentActionState.OUTCOME_UNCERTAIN
            and self.evidence in _POST_DISPATCH_EVIDENCE
            and (
                (
                    self.side_effect_state is SideEffectState.DISPATCHED
                    and self.side_effect_revision == 2
                )
                or (
                    self.side_effect_state is SideEffectState.UNCERTAIN
                    and self.side_effect_revision == 3
                )
            )
            and self.replayed is (self.evidence is DouyinCommentActionEvidence.REPLAY_UNCERTAIN)
        )
        if (
            type(self.action_id) is not ProtocolActionId
            or type(self.target_id) is not ProtocolTargetId
            or not isinstance(self.state, DouyinCommentActionState)
            or not isinstance(self.evidence, DouyinCommentActionEvidence)
            or self.execution_version != DOUYIN_COMMENT_ACTION_EXECUTION_VERSION
            or type(self.replayed) is not bool
            or not (no_effect or prepared or verified or uncertain)
        ):
            raise DouyinCommentActionRejected

    @property
    def completed(self) -> bool:
        return self.state is DouyinCommentActionState.VERIFIED

    @property
    def circuit_open(self) -> bool:
        return not self.completed

    def __repr__(self) -> str:
        side_effect_state = None if self.side_effect_state is None else self.side_effect_state.value
        return (
            "DouyinCommentActionReceipt("
            f"state={self.state.value!r}, evidence={self.evidence.value!r}, "
            f"side_effect_state={side_effect_state!r}, "
            f"side_effect_revision={self.side_effect_revision!r}, replayed={self.replayed!r}, "
            f"execution_version={self.execution_version!r}, <redacted>)"
        )


@runtime_checkable
class DouyinCommentActionClock(Protocol):
    def now(self) -> datetime: ...


class _CommentInput(Protocol):
    def fill(self, value: str, *, timeout: float) -> None: ...


class _CommentSubmit(Protocol):
    def click(self, *, timeout: float) -> None: ...


class DouyinCommentActionExecution:
    """Execute one confirmed comment without ever redispatching an admitted action."""

    def __init__(
        self,
        *,
        window: BrowserWindow,
        action_gate: ExecutorActionGate,
        ledger: ExecutorLedger,
        clock: DouyinCommentActionClock,
    ) -> None:
        if (
            not isinstance(window, BrowserWindow)
            or not isinstance(action_gate, ExecutorActionGate)
            or not isinstance(ledger, ExecutorLedger)
            or not isinstance(clock, DouyinCommentActionClock)
        ):
            raise DouyinCommentActionRejected
        self._page = DouyinCommentPage(window)
        self._action_gate = action_gate
        self._ledger = ledger
        self._clock = clock
        self._executed = False

    def __repr__(self) -> str:
        return "DouyinCommentActionExecution(<redacted>)"

    def run(
        self,
        *,
        intent: DouyinCommentActionIntent,
    ) -> DouyinCommentActionReceipt:
        if self._executed or not isinstance(intent, DouyinCommentActionIntent):
            raise DouyinCommentActionRejected
        self._executed = True
        expected = intent.authorization
        try:
            self._action_gate.admit(expected=expected)
        except ActionGateLimited as error:
            return _empty_receipt(expected, _limit_evidence(error.reason))
        except ActionGateRejected:
            return _empty_receipt(expected, DouyinCommentActionEvidence.ADMISSION_REJECTED)

        fingerprint = _effect_fingerprint(intent)
        try:
            prepared = self._ledger.prepare_side_effect(
                action_id=str(expected.action_id),
                effect_fingerprint=fingerprint,
                prepared_at=self._now(),
            )
        except Exception:
            return _empty_receipt(expected, DouyinCommentActionEvidence.LEDGER_UNAVAILABLE)
        replay = _receipt_for_existing(expected, prepared)
        if replay is not None:
            return replay

        ready = self._page.wait_for_ready(timeout_milliseconds=_PAGE_READY_TIMEOUT_MILLISECONDS)
        ready_failure = _ready_evidence(ready)
        if ready_failure is not None:
            return _prepared_receipt(expected, ready_failure)

        try:
            comment_input = cast(_CommentInput, self._page.comment_input())
            comment_input.fill(intent._rendered_message, timeout=_ACTION_TIMEOUT_MILLISECONDS)
            comment_submit = cast(_CommentSubmit, self._page.comment_submit())
        except Exception:
            return _prepared_receipt(expected, DouyinCommentActionEvidence.PREPARE_UNAVAILABLE)

        try:
            dispatched = self._ledger.begin_side_effect_dispatch(
                action_id=str(expected.action_id),
                effect_fingerprint=fingerprint,
                dispatched_at=self._now(),
            )
        except Exception:
            return _prepared_receipt(
                expected, DouyinCommentActionEvidence.DISPATCH_PERMISSION_REJECTED
            )
        replay = _receipt_for_existing(expected, dispatched)
        if replay is not None:
            return replay

        try:
            comment_submit.click(timeout=_ACTION_TIMEOUT_MILLISECONDS)
        except PlaywrightTimeoutError:
            return self._uncertain(
                expected,
                fingerprint,
                DouyinCommentActionEvidence.DISPATCH_TIMED_OUT,
                dispatched,
            )
        except Exception:
            return self._uncertain(
                expected,
                fingerprint,
                DouyinCommentActionEvidence.DISPATCH_UNAVAILABLE,
                dispatched,
            )

        final = self._page.wait_for_final(timeout_milliseconds=_FINAL_TIMEOUT_MILLISECONDS)
        final_failure = _final_evidence(final)
        if final_failure is not None:
            return self._uncertain(expected, fingerprint, final_failure, dispatched)
        try:
            self._page.final_confirmation()
            verified = self._ledger.verify_side_effect(
                action_id=str(expected.action_id),
                effect_fingerprint=fingerprint,
                verification_fingerprint=comment_action_verification_fingerprint(fingerprint),
                verified_at=self._now(),
            )
        except Exception:
            return self._uncertain(
                expected,
                fingerprint,
                DouyinCommentActionEvidence.VERIFICATION_UNAVAILABLE,
                dispatched,
            )
        return _effect_receipt(
            expected,
            DouyinCommentActionState.VERIFIED,
            DouyinCommentActionEvidence.COMMENT_CONFIRMED,
            verified,
            replayed=False,
        )

    def _uncertain(
        self,
        expected: ActionAuthorizationExpectation,
        fingerprint: bytes,
        evidence: DouyinCommentActionEvidence,
        dispatched: LocalSideEffect,
    ) -> DouyinCommentActionReceipt:
        settled = dispatched
        with suppress(Exception):
            settled = self._ledger.mark_side_effect_uncertain(
                action_id=str(expected.action_id),
                effect_fingerprint=fingerprint,
                uncertain_at=self._now(),
            )
        return _effect_receipt(
            expected,
            DouyinCommentActionState.OUTCOME_UNCERTAIN,
            evidence,
            settled,
            replayed=False,
        )

    def _now(self) -> datetime:
        try:
            value = self._clock.now()
            if (
                type(value) is not datetime
                or value.tzinfo is None
                or value.utcoffset() != timedelta(0)
            ):
                raise ValueError
            return value.astimezone(UTC)
        except Exception:
            raise DouyinCommentActionRejected from None


def _effect_fingerprint(intent: DouyinCommentActionIntent) -> bytes:
    expected = intent.authorization
    encoded = json.dumps(
        {
            "action": expected.action.value,
            "actionId": str(expected.action_id),
            "domain": _EFFECT_FINGERPRINT_DOMAIN,
            "executionAttemptId": str(expected.execution_attempt_id),
            "executorId": str(expected.executor_id),
            "idempotencyKey": str(expected.idempotency_key),
            "installationId": str(expected.installation_id),
            "message": intent._rendered_message,
            "messageTemplateVersion": ACTION_MESSAGE_TEMPLATE_VERSION,
            "platform": expected.platform,
            "targetId": str(expected.target_id),
            "taskId": str(expected.task_id),
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(encoded).digest()


def comment_action_verification_fingerprint(effect_fingerprint: bytes) -> bytes:
    return hashlib.sha256(
        _VERIFICATION_FINGERPRINT_DOMAIN
        + effect_fingerprint
        + b"\0"
        + DOUYIN_COMMENT_PAGE_SELECTOR_VERSION.encode("ascii")
        + b"\0final_confirmation_visible"
    ).digest()


def _limit_evidence(reason: LocalActionLimitReason) -> DouyinCommentActionEvidence:
    return {
        LocalActionLimitReason.EMERGENCY_STOP: DouyinCommentActionEvidence.LOCAL_EMERGENCY_STOP,
        LocalActionLimitReason.MINIMUM_INTERVAL: (
            DouyinCommentActionEvidence.LOCAL_MINIMUM_INTERVAL
        ),
        LocalActionLimitReason.TASK_ACTION_LIMIT: (
            DouyinCommentActionEvidence.LOCAL_TASK_ACTION_LIMIT
        ),
    }[reason]


def _ready_evidence(
    observation: DouyinCommentPageObservation,
) -> DouyinCommentActionEvidence | None:
    if observation.state is DouyinCommentPageState.READY:
        return None
    if observation.state is DouyinCommentPageState.CONFIRMED:
        return DouyinCommentActionEvidence.STALE_CONFIRMATION
    if observation.state is DouyinCommentPageState.LOGIN_REQUIRED:
        return DouyinCommentActionEvidence.READY_LOGIN_REQUIRED
    if observation.state is DouyinCommentPageState.DIALOG_BLOCKED:
        return DouyinCommentActionEvidence.READY_DIALOG_BLOCKED
    return {
        DouyinCommentPageEvidence.REQUIRED_ANCHOR_MISSING: (
            DouyinCommentActionEvidence.READY_TIMED_OUT
        ),
        DouyinCommentPageEvidence.PAGE_VERSION_UNKNOWN: (
            DouyinCommentActionEvidence.READY_PAGE_VERSION_UNKNOWN
        ),
        DouyinCommentPageEvidence.CONFLICTING_ANCHORS: (
            DouyinCommentActionEvidence.READY_CONFLICTING_ANCHORS
        ),
        DouyinCommentPageEvidence.PAGE_UNAVAILABLE: (
            DouyinCommentActionEvidence.READY_PAGE_UNAVAILABLE
        ),
    }.get(observation.evidence, DouyinCommentActionEvidence.READY_PAGE_UNAVAILABLE)


def _final_evidence(
    observation: DouyinCommentPageObservation,
) -> DouyinCommentActionEvidence | None:
    if observation.state is DouyinCommentPageState.CONFIRMED:
        return None
    if observation.state is DouyinCommentPageState.LOGIN_REQUIRED:
        return DouyinCommentActionEvidence.FINAL_LOGIN_REQUIRED
    if observation.state is DouyinCommentPageState.DIALOG_BLOCKED:
        return DouyinCommentActionEvidence.FINAL_DIALOG_BLOCKED
    if observation.state is DouyinCommentPageState.READY:
        return DouyinCommentActionEvidence.FINAL_TIMED_OUT
    return {
        DouyinCommentPageEvidence.REQUIRED_ANCHOR_MISSING: (
            DouyinCommentActionEvidence.FINAL_TIMED_OUT
        ),
        DouyinCommentPageEvidence.PAGE_VERSION_UNKNOWN: (
            DouyinCommentActionEvidence.FINAL_PAGE_VERSION_UNKNOWN
        ),
        DouyinCommentPageEvidence.CONFLICTING_ANCHORS: (
            DouyinCommentActionEvidence.FINAL_CONFLICTING_ANCHORS
        ),
        DouyinCommentPageEvidence.PAGE_UNAVAILABLE: (
            DouyinCommentActionEvidence.FINAL_PAGE_UNAVAILABLE
        ),
    }.get(observation.evidence, DouyinCommentActionEvidence.FINAL_PAGE_UNAVAILABLE)


def _receipt_for_existing(
    expected: ActionAuthorizationExpectation,
    effect: LocalSideEffect,
) -> DouyinCommentActionReceipt | None:
    if not effect.replayed or effect.state is SideEffectState.PREPARED:
        return None
    if effect.state is SideEffectState.VERIFIED:
        return _effect_receipt(
            expected,
            DouyinCommentActionState.VERIFIED,
            DouyinCommentActionEvidence.REPLAY_VERIFIED,
            effect,
            replayed=True,
        )
    return _effect_receipt(
        expected,
        DouyinCommentActionState.OUTCOME_UNCERTAIN,
        DouyinCommentActionEvidence.REPLAY_UNCERTAIN,
        effect,
        replayed=True,
    )


def _empty_receipt(
    expected: ActionAuthorizationExpectation,
    evidence: DouyinCommentActionEvidence,
) -> DouyinCommentActionReceipt:
    return DouyinCommentActionReceipt(
        action_id=expected.action_id,
        target_id=expected.target_id,
        state=DouyinCommentActionState.NOT_DISPATCHED,
        evidence=evidence,
        side_effect_state=None,
        side_effect_revision=None,
        replayed=False,
    )


def _prepared_receipt(
    expected: ActionAuthorizationExpectation,
    evidence: DouyinCommentActionEvidence,
) -> DouyinCommentActionReceipt:
    return DouyinCommentActionReceipt(
        action_id=expected.action_id,
        target_id=expected.target_id,
        state=DouyinCommentActionState.NOT_DISPATCHED,
        evidence=evidence,
        side_effect_state=SideEffectState.PREPARED,
        side_effect_revision=1,
        replayed=False,
    )


def _effect_receipt(
    expected: ActionAuthorizationExpectation,
    state: DouyinCommentActionState,
    evidence: DouyinCommentActionEvidence,
    effect: LocalSideEffect,
    *,
    replayed: bool,
) -> DouyinCommentActionReceipt:
    return DouyinCommentActionReceipt(
        action_id=expected.action_id,
        target_id=expected.target_id,
        state=state,
        evidence=evidence,
        side_effect_state=effect.state,
        side_effect_revision=effect.revision,
        replayed=replayed,
    )


__all__ = [
    "DOUYIN_COMMENT_ACTION_EXECUTION_VERSION",
    "DouyinCommentActionClock",
    "DouyinCommentActionEvidence",
    "DouyinCommentActionExecution",
    "DouyinCommentActionIntent",
    "DouyinCommentActionReceipt",
    "DouyinCommentActionRejected",
    "DouyinCommentActionState",
    "comment_action_verification_fingerprint",
]
