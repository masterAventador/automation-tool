"""Single-shot Douyin direct-message execution behind signed and durable local authority."""

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
from automation_tool.executor.rpa.douyin.direct_message_page import (
    DOUYIN_DIRECT_MESSAGE_PAGE_SELECTOR_VERSION,
    DouyinDirectMessagePage,
    DouyinDirectMessagePageEvidence,
    DouyinDirectMessagePageObservation,
    DouyinDirectMessagePageState,
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

DOUYIN_DIRECT_MESSAGE_ACTION_EXECUTION_VERSION = "douyin.direct-message-action-execution.v1"
_EFFECT_FINGERPRINT_DOMAIN = "automation-tool.douyin.direct-message-effect.v1"
_VERIFICATION_FINGERPRINT_DOMAIN = b"automation-tool.douyin.direct-message-verification.v1\0"
_PAGE_READY_TIMEOUT_MILLISECONDS = 10_000
_ACTION_TIMEOUT_MILLISECONDS = 15_000
_FINAL_TIMEOUT_MILLISECONDS = 10_000


class DouyinDirectMessageActionRejected(RuntimeError):
    def __init__(self) -> None:
        super().__init__("douyin direct-message action execution is unavailable")


class DouyinDirectMessageActionState(StrEnum):
    NOT_DISPATCHED = "not_dispatched"
    VERIFIED = "verified"
    OUTCOME_UNCERTAIN = "outcome_uncertain"


class DouyinDirectMessageActionEvidence(StrEnum):
    ADMISSION_REJECTED = "admission_rejected"
    LOCAL_EMERGENCY_STOP = "local_emergency_stop"
    LOCAL_MINIMUM_INTERVAL = "local_minimum_interval"
    LOCAL_TASK_ACTION_LIMIT = "local_task_action_limit"
    LEDGER_UNAVAILABLE = "ledger_unavailable"
    READY_LOGIN_REQUIRED = "ready_login_required"
    READY_DIALOG_BLOCKED = "ready_dialog_blocked"
    READY_MESSAGING_NOT_ALLOWED = "ready_messaging_not_allowed"
    READY_FOLLOW_REQUIRED = "ready_follow_required"
    READY_TIMED_OUT = "ready_timed_out"
    READY_PAGE_VERSION_UNKNOWN = "ready_page_version_unknown"
    READY_CONFLICTING_ANCHORS = "ready_conflicting_anchors"
    READY_PAGE_UNAVAILABLE = "ready_page_unavailable"
    STALE_CONFIRMATION = "stale_confirmation"
    ENTER_CONVERSATION_TIMED_OUT = "enter_conversation_timed_out"
    ENTER_CONVERSATION_UNAVAILABLE = "enter_conversation_unavailable"
    PREPARE_UNAVAILABLE = "prepare_unavailable"
    DISPATCH_PERMISSION_REJECTED = "dispatch_permission_rejected"
    DISPATCH_TIMED_OUT = "dispatch_timed_out"
    DISPATCH_UNAVAILABLE = "dispatch_unavailable"
    FINAL_LOGIN_REQUIRED = "final_login_required"
    FINAL_DIALOG_BLOCKED = "final_dialog_blocked"
    FINAL_MESSAGING_NOT_ALLOWED = "final_messaging_not_allowed"
    FINAL_FOLLOW_REQUIRED = "final_follow_required"
    FINAL_TIMED_OUT = "final_timed_out"
    FINAL_PAGE_VERSION_UNKNOWN = "final_page_version_unknown"
    FINAL_CONFLICTING_ANCHORS = "final_conflicting_anchors"
    FINAL_PAGE_UNAVAILABLE = "final_page_unavailable"
    VERIFICATION_UNAVAILABLE = "verification_unavailable"
    MESSAGE_CONFIRMED = "message_confirmed"
    REPLAY_VERIFIED = "replay_verified"
    REPLAY_UNCERTAIN = "replay_uncertain"


_NO_EFFECT_EVIDENCE = frozenset(
    {
        DouyinDirectMessageActionEvidence.ADMISSION_REJECTED,
        DouyinDirectMessageActionEvidence.LOCAL_EMERGENCY_STOP,
        DouyinDirectMessageActionEvidence.LOCAL_MINIMUM_INTERVAL,
        DouyinDirectMessageActionEvidence.LOCAL_TASK_ACTION_LIMIT,
        DouyinDirectMessageActionEvidence.LEDGER_UNAVAILABLE,
    }
)
_PREPARED_EVIDENCE = frozenset(
    {
        DouyinDirectMessageActionEvidence.READY_LOGIN_REQUIRED,
        DouyinDirectMessageActionEvidence.READY_DIALOG_BLOCKED,
        DouyinDirectMessageActionEvidence.READY_MESSAGING_NOT_ALLOWED,
        DouyinDirectMessageActionEvidence.READY_FOLLOW_REQUIRED,
        DouyinDirectMessageActionEvidence.READY_TIMED_OUT,
        DouyinDirectMessageActionEvidence.READY_PAGE_VERSION_UNKNOWN,
        DouyinDirectMessageActionEvidence.READY_CONFLICTING_ANCHORS,
        DouyinDirectMessageActionEvidence.READY_PAGE_UNAVAILABLE,
        DouyinDirectMessageActionEvidence.STALE_CONFIRMATION,
        DouyinDirectMessageActionEvidence.ENTER_CONVERSATION_TIMED_OUT,
        DouyinDirectMessageActionEvidence.ENTER_CONVERSATION_UNAVAILABLE,
        DouyinDirectMessageActionEvidence.PREPARE_UNAVAILABLE,
        DouyinDirectMessageActionEvidence.DISPATCH_PERMISSION_REJECTED,
    }
)
_POST_DISPATCH_EVIDENCE = frozenset(
    {
        DouyinDirectMessageActionEvidence.DISPATCH_TIMED_OUT,
        DouyinDirectMessageActionEvidence.DISPATCH_UNAVAILABLE,
        DouyinDirectMessageActionEvidence.FINAL_LOGIN_REQUIRED,
        DouyinDirectMessageActionEvidence.FINAL_DIALOG_BLOCKED,
        DouyinDirectMessageActionEvidence.FINAL_MESSAGING_NOT_ALLOWED,
        DouyinDirectMessageActionEvidence.FINAL_FOLLOW_REQUIRED,
        DouyinDirectMessageActionEvidence.FINAL_TIMED_OUT,
        DouyinDirectMessageActionEvidence.FINAL_PAGE_VERSION_UNKNOWN,
        DouyinDirectMessageActionEvidence.FINAL_CONFLICTING_ANCHORS,
        DouyinDirectMessageActionEvidence.FINAL_PAGE_UNAVAILABLE,
        DouyinDirectMessageActionEvidence.VERIFICATION_UNAVAILABLE,
        DouyinDirectMessageActionEvidence.REPLAY_UNCERTAIN,
    }
)


@dataclass(frozen=True, slots=True, repr=False)
class DouyinDirectMessageActionIntent:
    authorization: ActionAuthorizationExpectation
    message_template: ActionMessageTemplate
    target_summary: DouyinCandidateSummary
    _rendered_message: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        try:
            if (
                not isinstance(self.authorization, ActionAuthorizationExpectation)
                or self.authorization.action is not DouyinSearchExposureAction.DIRECT_MESSAGE
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
            raise DouyinDirectMessageActionRejected from None

    def __repr__(self) -> str:
        return "DouyinDirectMessageActionIntent(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class DouyinDirectMessageActionReceipt:
    action_id: ProtocolActionId
    target_id: ProtocolTargetId
    state: DouyinDirectMessageActionState
    evidence: DouyinDirectMessageActionEvidence
    side_effect_state: SideEffectState | None
    side_effect_revision: int | None
    replayed: bool
    execution_version: str = DOUYIN_DIRECT_MESSAGE_ACTION_EXECUTION_VERSION

    def __post_init__(self) -> None:
        no_effect = (
            self.state is DouyinDirectMessageActionState.NOT_DISPATCHED
            and self.evidence in _NO_EFFECT_EVIDENCE
            and self.side_effect_state is None
            and self.side_effect_revision is None
            and self.replayed is False
        )
        prepared = (
            self.state is DouyinDirectMessageActionState.NOT_DISPATCHED
            and self.evidence in _PREPARED_EVIDENCE
            and self.side_effect_state is SideEffectState.PREPARED
            and self.side_effect_revision == 1
            and self.replayed is False
        )
        verified = (
            self.state is DouyinDirectMessageActionState.VERIFIED
            and self.evidence
            in {
                DouyinDirectMessageActionEvidence.MESSAGE_CONFIRMED,
                DouyinDirectMessageActionEvidence.REPLAY_VERIFIED,
            }
            and self.side_effect_state is SideEffectState.VERIFIED
            and self.side_effect_revision == 3
            and self.replayed
            is (self.evidence is DouyinDirectMessageActionEvidence.REPLAY_VERIFIED)
        )
        uncertain = (
            self.state is DouyinDirectMessageActionState.OUTCOME_UNCERTAIN
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
            and self.replayed
            is (self.evidence is DouyinDirectMessageActionEvidence.REPLAY_UNCERTAIN)
        )
        if (
            type(self.action_id) is not ProtocolActionId
            or type(self.target_id) is not ProtocolTargetId
            or not isinstance(self.state, DouyinDirectMessageActionState)
            or not isinstance(self.evidence, DouyinDirectMessageActionEvidence)
            or self.execution_version != DOUYIN_DIRECT_MESSAGE_ACTION_EXECUTION_VERSION
            or type(self.replayed) is not bool
            or not (no_effect or prepared or verified or uncertain)
        ):
            raise DouyinDirectMessageActionRejected

    @property
    def completed(self) -> bool:
        return self.state is DouyinDirectMessageActionState.VERIFIED

    @property
    def circuit_open(self) -> bool:
        return not self.completed

    def __repr__(self) -> str:
        side_effect_state = None if self.side_effect_state is None else self.side_effect_state.value
        return (
            "DouyinDirectMessageActionReceipt("
            f"state={self.state.value!r}, evidence={self.evidence.value!r}, "
            f"side_effect_state={side_effect_state!r}, "
            f"side_effect_revision={self.side_effect_revision!r}, replayed={self.replayed!r}, "
            f"execution_version={self.execution_version!r}, <redacted>)"
        )


@runtime_checkable
class DouyinDirectMessageActionClock(Protocol):
    def now(self) -> datetime: ...


class _ConversationEntry(Protocol):
    def click(self, *, timeout: float) -> None: ...


class _MessageInput(Protocol):
    def fill(self, value: str, *, timeout: float) -> None: ...


class _MessageSend(Protocol):
    def click(self, *, timeout: float) -> None: ...


class DouyinDirectMessageActionExecution:
    """Execute one confirmed direct message without ever redispatching an admitted action."""

    def __init__(
        self,
        *,
        window: BrowserWindow,
        action_gate: ExecutorActionGate,
        ledger: ExecutorLedger,
        clock: DouyinDirectMessageActionClock,
    ) -> None:
        if (
            not isinstance(window, BrowserWindow)
            or not isinstance(action_gate, ExecutorActionGate)
            or not isinstance(ledger, ExecutorLedger)
            or not isinstance(clock, DouyinDirectMessageActionClock)
        ):
            raise DouyinDirectMessageActionRejected
        self._page = DouyinDirectMessagePage(window)
        self._action_gate = action_gate
        self._ledger = ledger
        self._clock = clock
        self._executed = False

    def __repr__(self) -> str:
        return "DouyinDirectMessageActionExecution(<redacted>)"

    def run(
        self,
        *,
        token: str,
        intent: DouyinDirectMessageActionIntent,
    ) -> DouyinDirectMessageActionReceipt:
        if (
            self._executed
            or type(token) is not str
            or not isinstance(intent, DouyinDirectMessageActionIntent)
        ):
            raise DouyinDirectMessageActionRejected
        self._executed = True
        expected = intent.authorization
        try:
            self._action_gate.admit(token=token, expected=expected)
        except ActionGateLimited as error:
            return _empty_receipt(expected, _limit_evidence(error.reason))
        except ActionGateRejected:
            return _empty_receipt(expected, DouyinDirectMessageActionEvidence.ADMISSION_REJECTED)

        fingerprint = _effect_fingerprint(intent)
        try:
            prepared = self._ledger.prepare_side_effect(
                action_id=str(expected.action_id),
                effect_fingerprint=fingerprint,
                prepared_at=self._now(),
            )
        except Exception:
            return _empty_receipt(expected, DouyinDirectMessageActionEvidence.LEDGER_UNAVAILABLE)
        replay = _receipt_for_existing(expected, prepared)
        if replay is not None:
            return replay

        ready = self._page.wait_for_profile_ready(
            timeout_milliseconds=_PAGE_READY_TIMEOUT_MILLISECONDS
        )
        ready_failure = _ready_evidence(ready)
        if ready_failure is not None:
            return _prepared_receipt(expected, ready_failure)

        if ready.state is DouyinDirectMessagePageState.PROFILE_READY:
            try:
                entry = cast(_ConversationEntry, self._page.enter_conversation())
                entry.click(timeout=_ACTION_TIMEOUT_MILLISECONDS)
            except PlaywrightTimeoutError:
                return _prepared_receipt(
                    expected,
                    DouyinDirectMessageActionEvidence.ENTER_CONVERSATION_TIMED_OUT,
                )
            except Exception:
                return _prepared_receipt(
                    expected,
                    DouyinDirectMessageActionEvidence.ENTER_CONVERSATION_UNAVAILABLE,
                )
            conversation = self._page.wait_for_conversation_ready(
                timeout_milliseconds=_PAGE_READY_TIMEOUT_MILLISECONDS
            )
            conversation_failure = _conversation_evidence(conversation)
            if conversation_failure is not None:
                return _prepared_receipt(expected, conversation_failure)

        try:
            message_input = cast(_MessageInput, self._page.message_input())
            message_input.fill(intent._rendered_message, timeout=_ACTION_TIMEOUT_MILLISECONDS)
            message_send = cast(_MessageSend, self._page.message_send())
        except Exception:
            return _prepared_receipt(
                expected, DouyinDirectMessageActionEvidence.PREPARE_UNAVAILABLE
            )

        try:
            dispatched = self._ledger.begin_side_effect_dispatch(
                action_id=str(expected.action_id),
                effect_fingerprint=fingerprint,
                dispatched_at=self._now(),
            )
        except Exception:
            return _prepared_receipt(
                expected, DouyinDirectMessageActionEvidence.DISPATCH_PERMISSION_REJECTED
            )
        replay = _receipt_for_existing(expected, dispatched)
        if replay is not None:
            return replay

        try:
            message_send.click(timeout=_ACTION_TIMEOUT_MILLISECONDS)
        except PlaywrightTimeoutError:
            return self._uncertain(
                expected,
                fingerprint,
                DouyinDirectMessageActionEvidence.DISPATCH_TIMED_OUT,
                dispatched,
            )
        except Exception:
            return self._uncertain(
                expected,
                fingerprint,
                DouyinDirectMessageActionEvidence.DISPATCH_UNAVAILABLE,
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
                verification_fingerprint=_verification_fingerprint(fingerprint),
                verified_at=self._now(),
            )
        except Exception:
            return self._uncertain(
                expected,
                fingerprint,
                DouyinDirectMessageActionEvidence.VERIFICATION_UNAVAILABLE,
                dispatched,
            )
        return _effect_receipt(
            expected,
            DouyinDirectMessageActionState.VERIFIED,
            DouyinDirectMessageActionEvidence.MESSAGE_CONFIRMED,
            verified,
            replayed=False,
        )

    def _uncertain(
        self,
        expected: ActionAuthorizationExpectation,
        fingerprint: bytes,
        evidence: DouyinDirectMessageActionEvidence,
        dispatched: LocalSideEffect,
    ) -> DouyinDirectMessageActionReceipt:
        settled = dispatched
        with suppress(Exception):
            settled = self._ledger.mark_side_effect_uncertain(
                action_id=str(expected.action_id),
                effect_fingerprint=fingerprint,
                uncertain_at=self._now(),
            )
        return _effect_receipt(
            expected,
            DouyinDirectMessageActionState.OUTCOME_UNCERTAIN,
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
            raise DouyinDirectMessageActionRejected from None


def _effect_fingerprint(intent: DouyinDirectMessageActionIntent) -> bytes:
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


def _verification_fingerprint(effect_fingerprint: bytes) -> bytes:
    return hashlib.sha256(
        _VERIFICATION_FINGERPRINT_DOMAIN
        + effect_fingerprint
        + b"\0"
        + DOUYIN_DIRECT_MESSAGE_PAGE_SELECTOR_VERSION.encode("ascii")
        + b"\0final_confirmation_visible"
    ).digest()


def _limit_evidence(reason: LocalActionLimitReason) -> DouyinDirectMessageActionEvidence:
    return {
        LocalActionLimitReason.EMERGENCY_STOP: (
            DouyinDirectMessageActionEvidence.LOCAL_EMERGENCY_STOP
        ),
        LocalActionLimitReason.MINIMUM_INTERVAL: (
            DouyinDirectMessageActionEvidence.LOCAL_MINIMUM_INTERVAL
        ),
        LocalActionLimitReason.TASK_ACTION_LIMIT: (
            DouyinDirectMessageActionEvidence.LOCAL_TASK_ACTION_LIMIT
        ),
    }[reason]


def _ready_evidence(
    observation: DouyinDirectMessagePageObservation,
) -> DouyinDirectMessageActionEvidence | None:
    if observation.state in {
        DouyinDirectMessagePageState.PROFILE_READY,
        DouyinDirectMessagePageState.CONVERSATION_READY,
    }:
        return None
    if observation.state is DouyinDirectMessagePageState.CONFIRMED:
        return DouyinDirectMessageActionEvidence.STALE_CONFIRMATION
    if observation.state is DouyinDirectMessagePageState.LOGIN_REQUIRED:
        return DouyinDirectMessageActionEvidence.READY_LOGIN_REQUIRED
    if observation.state is DouyinDirectMessagePageState.DIALOG_BLOCKED:
        return DouyinDirectMessageActionEvidence.READY_DIALOG_BLOCKED
    if observation.state is DouyinDirectMessagePageState.PERMISSION_DENIED:
        return (
            DouyinDirectMessageActionEvidence.READY_FOLLOW_REQUIRED
            if observation.evidence is DouyinDirectMessagePageEvidence.FOLLOW_REQUIRED
            else DouyinDirectMessageActionEvidence.READY_MESSAGING_NOT_ALLOWED
        )
    return {
        DouyinDirectMessagePageEvidence.REQUIRED_ANCHOR_MISSING: (
            DouyinDirectMessageActionEvidence.READY_TIMED_OUT
        ),
        DouyinDirectMessagePageEvidence.PAGE_VERSION_UNKNOWN: (
            DouyinDirectMessageActionEvidence.READY_PAGE_VERSION_UNKNOWN
        ),
        DouyinDirectMessagePageEvidence.CONFLICTING_ANCHORS: (
            DouyinDirectMessageActionEvidence.READY_CONFLICTING_ANCHORS
        ),
        DouyinDirectMessagePageEvidence.PAGE_UNAVAILABLE: (
            DouyinDirectMessageActionEvidence.READY_PAGE_UNAVAILABLE
        ),
    }.get(observation.evidence, DouyinDirectMessageActionEvidence.READY_PAGE_UNAVAILABLE)


def _conversation_evidence(
    observation: DouyinDirectMessagePageObservation,
) -> DouyinDirectMessageActionEvidence | None:
    if observation.state is DouyinDirectMessagePageState.CONVERSATION_READY:
        return None
    if observation.state is DouyinDirectMessagePageState.PROFILE_READY:
        return DouyinDirectMessageActionEvidence.READY_TIMED_OUT
    return _ready_evidence(observation)


def _final_evidence(
    observation: DouyinDirectMessagePageObservation,
) -> DouyinDirectMessageActionEvidence | None:
    if observation.state is DouyinDirectMessagePageState.CONFIRMED:
        return None
    if observation.state is DouyinDirectMessagePageState.LOGIN_REQUIRED:
        return DouyinDirectMessageActionEvidence.FINAL_LOGIN_REQUIRED
    if observation.state is DouyinDirectMessagePageState.DIALOG_BLOCKED:
        return DouyinDirectMessageActionEvidence.FINAL_DIALOG_BLOCKED
    if observation.state is DouyinDirectMessagePageState.PERMISSION_DENIED:
        return (
            DouyinDirectMessageActionEvidence.FINAL_FOLLOW_REQUIRED
            if observation.evidence is DouyinDirectMessagePageEvidence.FOLLOW_REQUIRED
            else DouyinDirectMessageActionEvidence.FINAL_MESSAGING_NOT_ALLOWED
        )
    if observation.state in {
        DouyinDirectMessagePageState.PROFILE_READY,
        DouyinDirectMessagePageState.CONVERSATION_READY,
    }:
        return DouyinDirectMessageActionEvidence.FINAL_TIMED_OUT
    return {
        DouyinDirectMessagePageEvidence.REQUIRED_ANCHOR_MISSING: (
            DouyinDirectMessageActionEvidence.FINAL_TIMED_OUT
        ),
        DouyinDirectMessagePageEvidence.PAGE_VERSION_UNKNOWN: (
            DouyinDirectMessageActionEvidence.FINAL_PAGE_VERSION_UNKNOWN
        ),
        DouyinDirectMessagePageEvidence.CONFLICTING_ANCHORS: (
            DouyinDirectMessageActionEvidence.FINAL_CONFLICTING_ANCHORS
        ),
        DouyinDirectMessagePageEvidence.PAGE_UNAVAILABLE: (
            DouyinDirectMessageActionEvidence.FINAL_PAGE_UNAVAILABLE
        ),
    }.get(observation.evidence, DouyinDirectMessageActionEvidence.FINAL_PAGE_UNAVAILABLE)


def _receipt_for_existing(
    expected: ActionAuthorizationExpectation,
    effect: LocalSideEffect,
) -> DouyinDirectMessageActionReceipt | None:
    if not effect.replayed or effect.state is SideEffectState.PREPARED:
        return None
    if effect.state is SideEffectState.VERIFIED:
        return _effect_receipt(
            expected,
            DouyinDirectMessageActionState.VERIFIED,
            DouyinDirectMessageActionEvidence.REPLAY_VERIFIED,
            effect,
            replayed=True,
        )
    return _effect_receipt(
        expected,
        DouyinDirectMessageActionState.OUTCOME_UNCERTAIN,
        DouyinDirectMessageActionEvidence.REPLAY_UNCERTAIN,
        effect,
        replayed=True,
    )


def _empty_receipt(
    expected: ActionAuthorizationExpectation,
    evidence: DouyinDirectMessageActionEvidence,
) -> DouyinDirectMessageActionReceipt:
    return DouyinDirectMessageActionReceipt(
        action_id=expected.action_id,
        target_id=expected.target_id,
        state=DouyinDirectMessageActionState.NOT_DISPATCHED,
        evidence=evidence,
        side_effect_state=None,
        side_effect_revision=None,
        replayed=False,
    )


def _prepared_receipt(
    expected: ActionAuthorizationExpectation,
    evidence: DouyinDirectMessageActionEvidence,
) -> DouyinDirectMessageActionReceipt:
    return DouyinDirectMessageActionReceipt(
        action_id=expected.action_id,
        target_id=expected.target_id,
        state=DouyinDirectMessageActionState.NOT_DISPATCHED,
        evidence=evidence,
        side_effect_state=SideEffectState.PREPARED,
        side_effect_revision=1,
        replayed=False,
    )


def _effect_receipt(
    expected: ActionAuthorizationExpectation,
    state: DouyinDirectMessageActionState,
    evidence: DouyinDirectMessageActionEvidence,
    effect: LocalSideEffect,
    *,
    replayed: bool,
) -> DouyinDirectMessageActionReceipt:
    return DouyinDirectMessageActionReceipt(
        action_id=expected.action_id,
        target_id=expected.target_id,
        state=state,
        evidence=evidence,
        side_effect_state=effect.state,
        side_effect_revision=effect.revision,
        replayed=replayed,
    )


__all__ = [
    "DOUYIN_DIRECT_MESSAGE_ACTION_EXECUTION_VERSION",
    "DouyinDirectMessageActionClock",
    "DouyinDirectMessageActionEvidence",
    "DouyinDirectMessageActionExecution",
    "DouyinDirectMessageActionIntent",
    "DouyinDirectMessageActionReceipt",
    "DouyinDirectMessageActionRejected",
    "DouyinDirectMessageActionState",
]
