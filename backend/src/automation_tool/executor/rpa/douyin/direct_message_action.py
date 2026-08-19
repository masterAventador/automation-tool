"""抖音私信动作：闸门+台账外层，页面层由自愈式技能编排器驱动。

与评论动作同构（见 comment_action 模块说明）。私信特有的一点：权限受限
（暂时无法私信/关注后才能私信）的页面没有可达的私信入口——技能回放在
外部步之前失败，如实落为 ``SKILL_RECOVERY_PENDING``，没有任何东西被发出。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol, cast, runtime_checkable

from automation_tool.executor.action_authorization import ActionAuthorizationExpectation
from automation_tool.executor.action_gate import (
    ActionGateLimited,
    ActionGateRejected,
    ExecutorActionGate,
    LocalActionLimitReason,
)
from automation_tool.executor.browser_runtime import BrowserWindow
from automation_tool.executor.ledger import ExecutorLedger
from automation_tool.executor.rpa.douyin.skills import (
    DOUYIN_DIRECT_MESSAGE_PARAMETER,
    DOUYIN_DIRECT_MESSAGE_SKILL_ID,
    default_orchestrator,
)
from automation_tool.executor.side_effect_ledger import LocalSideEffect, SideEffectState
from automation_tool.executor.skill_orchestrator import (
    SkillExecutionKind,
    SkillOrchestrator,
)
from automation_tool.executor.skill_replay_page import PlaywrightReplayPage
from automation_tool.executor.skill_replayer import ReplayPage
from automation_tool.protocol import (
    ACTION_MESSAGE_TEMPLATE_VERSION,
    ActionMessageTemplate,
    DouyinCandidateSummary,
    DouyinSearchExposureAction,
    ProtocolActionId,
    ProtocolTargetId,
)

DOUYIN_DIRECT_MESSAGE_ACTION_EXECUTION_VERSION = "douyin.direct-message-action-execution.v2"
DOUYIN_DIRECT_MESSAGE_VERIFICATION_VERSION = "douyin.direct-message-skill.v1"
_EFFECT_FINGERPRINT_DOMAIN = "automation-tool.douyin.direct-message-effect.v1"
_VERIFICATION_FINGERPRINT_DOMAIN = b"automation-tool.douyin.direct-message-verification.v1\0"
_REPLAY_PAGE_TIMEOUT_SECONDS = 15


class DouyinDirectMessageActionRejected(RuntimeError):
    def __init__(self) -> None:
        super().__init__("douyin direct message action execution is unavailable")


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
    SKILL_AWAITING_RECORDING = "skill_awaiting_recording"
    SKILL_RECOVERY_PENDING = "skill_recovery_pending"
    PREPARE_UNAVAILABLE = "prepare_unavailable"
    DISPATCH_PERMISSION_REJECTED = "dispatch_permission_rejected"
    SKILL_RECONCILE_REQUIRED = "skill_reconcile_required"
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
        DouyinDirectMessageActionEvidence.SKILL_AWAITING_RECORDING,
        DouyinDirectMessageActionEvidence.SKILL_RECOVERY_PENDING,
        DouyinDirectMessageActionEvidence.PREPARE_UNAVAILABLE,
        DouyinDirectMessageActionEvidence.DISPATCH_PERMISSION_REJECTED,
    }
)
_POST_DISPATCH_EVIDENCE = frozenset(
    {
        DouyinDirectMessageActionEvidence.SKILL_RECONCILE_REQUIRED,
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


class _DispatchRefused(Exception):
    """台账拒绝进入 dispatch——外部动作尚未尝试。"""


class _EffectSettled(Exception):
    """dispatch 登记时发现效果已被此前的尝试定论。"""

    def __init__(self, receipt: DouyinDirectMessageActionReceipt) -> None:
        super().__init__("the side effect is already settled")
        self.receipt = receipt


def _default_replay_page(window: BrowserWindow) -> ReplayPage:
    return PlaywrightReplayPage(
        window.playwright_page, action_timeout_seconds=_REPLAY_PAGE_TIMEOUT_SECONDS
    )


class DouyinDirectMessageActionExecution:
    """Execute one confirmed direct message without ever redispatching it."""

    def __init__(
        self,
        *,
        window: BrowserWindow,
        action_gate: ExecutorActionGate,
        ledger: ExecutorLedger,
        clock: DouyinDirectMessageActionClock,
        orchestrator: SkillOrchestrator | None = None,
        replay_page_factory: Callable[[BrowserWindow], ReplayPage] | None = None,
    ) -> None:
        if (
            not isinstance(window, BrowserWindow)
            or not isinstance(action_gate, ExecutorActionGate)
            or not isinstance(ledger, ExecutorLedger)
            or not isinstance(clock, DouyinDirectMessageActionClock)
            or not (orchestrator is None or isinstance(orchestrator, SkillOrchestrator))
            or not (replay_page_factory is None or callable(replay_page_factory))
        ):
            raise DouyinDirectMessageActionRejected
        self._window = window
        self._action_gate = action_gate
        self._ledger = ledger
        self._clock = clock
        self._orchestrator = orchestrator
        self._replay_page_factory = replay_page_factory or _default_replay_page
        self._executed = False

    def __repr__(self) -> str:
        return "DouyinDirectMessageActionExecution(<redacted>)"

    def run(
        self,
        *,
        intent: DouyinDirectMessageActionIntent,
    ) -> DouyinDirectMessageActionReceipt:
        if self._executed or not isinstance(intent, DouyinDirectMessageActionIntent):
            raise DouyinDirectMessageActionRejected
        self._executed = True
        expected = intent.authorization
        try:
            self._action_gate.admit(expected=expected)
        except ActionGateLimited as error:
            return _empty_receipt(expected, _limit_evidence(error.reason))
        except ActionGateRejected:
            return _empty_receipt(
                expected, DouyinDirectMessageActionEvidence.ADMISSION_REJECTED
            )

        fingerprint = _effect_fingerprint(intent)
        try:
            prepared = self._ledger.prepare_side_effect(
                action_id=str(expected.action_id),
                effect_fingerprint=fingerprint,
                prepared_at=self._now(),
            )
        except Exception:
            return _empty_receipt(
                expected, DouyinDirectMessageActionEvidence.LEDGER_UNAVAILABLE
            )
        replay = _receipt_for_existing(expected, prepared)
        if replay is not None:
            return replay

        try:
            orchestrator = (
                self._orchestrator if self._orchestrator is not None else default_orchestrator()
            )
            page = self._replay_page_factory(self._window)
        except Exception:
            return _prepared_receipt(
                expected, DouyinDirectMessageActionEvidence.PREPARE_UNAVAILABLE
            )

        dispatched_effects: list[LocalSideEffect] = []

        def begin_dispatch() -> None:
            # 回放器在外部步之前调它：台账先记 DISPATCHED，平台才可能看到动作。
            try:
                effect = self._ledger.begin_side_effect_dispatch(
                    action_id=str(expected.action_id),
                    effect_fingerprint=fingerprint,
                    dispatched_at=self._now(),
                )
            except Exception as error:
                raise _DispatchRefused from error
            settled = _receipt_for_existing(expected, effect)
            if settled is not None:
                raise _EffectSettled(settled)
            dispatched_effects.append(effect)

        try:
            report = orchestrator.execute(
                DOUYIN_DIRECT_MESSAGE_SKILL_ID,
                page,
                parameters={DOUYIN_DIRECT_MESSAGE_PARAMETER: intent._rendered_message},
                on_external_dispatch=begin_dispatch,
            )
        except _DispatchRefused:
            return _prepared_receipt(
                expected, DouyinDirectMessageActionEvidence.DISPATCH_PERMISSION_REJECTED
            )
        except _EffectSettled as settled:
            return settled.receipt
        except Exception:
            # 编排器/页面适配层的基础设施异常（不是回放失败）。dispatch 已
            # 登记则结果不确定，只能对账；否则页面未被外部触碰。
            if dispatched_effects:
                return self._uncertain(
                    expected,
                    fingerprint,
                    DouyinDirectMessageActionEvidence.SKILL_RECONCILE_REQUIRED,
                    dispatched_effects[0],
                )
            return _prepared_receipt(
                expected, DouyinDirectMessageActionEvidence.PREPARE_UNAVAILABLE
            )

        if report.kind is SkillExecutionKind.NO_ROUTE:
            return _prepared_receipt(
                expected, DouyinDirectMessageActionEvidence.SKILL_AWAITING_RECORDING
            )
        if report.kind is SkillExecutionKind.RECOVERY_PENDING:
            return _prepared_receipt(
                expected, DouyinDirectMessageActionEvidence.SKILL_RECOVERY_PENDING
            )
        if report.kind is SkillExecutionKind.RECONCILE_REQUIRED:
            if not dispatched_effects:
                return _prepared_receipt(
                    expected, DouyinDirectMessageActionEvidence.PREPARE_UNAVAILABLE
                )
            return self._uncertain(
                expected,
                fingerprint,
                DouyinDirectMessageActionEvidence.SKILL_RECONCILE_REQUIRED,
                dispatched_effects[0],
            )

        # REPLAYED：回放完成且结果证据成立。
        if not dispatched_effects:
            # 路由到的技能没有外部步——什么都没发出去，不能谎称已发送。
            return _prepared_receipt(
                expected, DouyinDirectMessageActionEvidence.PREPARE_UNAVAILABLE
            )
        try:
            verified = self._ledger.verify_side_effect(
                action_id=str(expected.action_id),
                effect_fingerprint=fingerprint,
                verification_fingerprint=direct_message_action_verification_fingerprint(
                    fingerprint
                ),
                verified_at=self._now(),
            )
        except Exception:
            return self._uncertain(
                expected,
                fingerprint,
                DouyinDirectMessageActionEvidence.VERIFICATION_UNAVAILABLE,
                dispatched_effects[0],
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


def direct_message_action_verification_fingerprint(effect_fingerprint: bytes) -> bytes:
    return hashlib.sha256(
        _VERIFICATION_FINGERPRINT_DOMAIN
        + effect_fingerprint
        + b"\0"
        + DOUYIN_DIRECT_MESSAGE_VERIFICATION_VERSION.encode("ascii")
        + b"\0success_evidence_visible"
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
    "DOUYIN_DIRECT_MESSAGE_VERIFICATION_VERSION",
    "DouyinDirectMessageActionClock",
    "DouyinDirectMessageActionEvidence",
    "DouyinDirectMessageActionExecution",
    "DouyinDirectMessageActionIntent",
    "DouyinDirectMessageActionReceipt",
    "DouyinDirectMessageActionRejected",
    "DouyinDirectMessageActionState",
    "direct_message_action_verification_fingerprint",
]
