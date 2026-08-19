"""抖音评论动作：闸门+台账外层，页面层由自愈式技能编排器驱动。

写死选择器的 Page Object 已删除。页面交互完全由已签名的技能回放完成
（路由 SA-06 → 回放 SA-04 → 失败按 SA-05 接管决策），台账在外部步之前
经回放器的 dispatch 钩子登记，回放的结果证据成立即 verify。

诚实状态，不装成功：

* 没有可路由的技能 → ``SKILL_AWAITING_RECORDING``（待技能录制）；
* 外部步之前回放失败 → ``SKILL_RECOVERY_PENDING``（待技能修复/录制，
  真正的 Browser Use 续跑需要视觉模型凭据与真实登录态）；
* 外部步之后失败 → ``SKILL_RECONCILE_REQUIRED``，只对账、绝不重发。
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
    DOUYIN_COMMENT_MESSAGE_PARAMETER,
    DOUYIN_COMMENT_SKILL_ID,
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

DOUYIN_COMMENT_ACTION_EXECUTION_VERSION = "douyin.comment-action-execution.v2"
DOUYIN_COMMENT_VERIFICATION_VERSION = "douyin.comment-skill.v1"
_EFFECT_FINGERPRINT_DOMAIN = "automation-tool.douyin.comment-effect.v1"
_VERIFICATION_FINGERPRINT_DOMAIN = b"automation-tool.douyin.comment-verification.v1\0"
_REPLAY_PAGE_TIMEOUT_SECONDS = 15


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
    SKILL_AWAITING_RECORDING = "skill_awaiting_recording"
    SKILL_RECOVERY_PENDING = "skill_recovery_pending"
    PREPARE_UNAVAILABLE = "prepare_unavailable"
    DISPATCH_PERMISSION_REJECTED = "dispatch_permission_rejected"
    SKILL_RECONCILE_REQUIRED = "skill_reconcile_required"
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
        DouyinCommentActionEvidence.SKILL_AWAITING_RECORDING,
        DouyinCommentActionEvidence.SKILL_RECOVERY_PENDING,
        DouyinCommentActionEvidence.PREPARE_UNAVAILABLE,
        DouyinCommentActionEvidence.DISPATCH_PERMISSION_REJECTED,
    }
)
_POST_DISPATCH_EVIDENCE = frozenset(
    {
        DouyinCommentActionEvidence.SKILL_RECONCILE_REQUIRED,
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


class _DispatchRefused(Exception):
    """台账拒绝进入 dispatch——外部动作尚未尝试。"""


class _EffectSettled(Exception):
    """dispatch 登记时发现效果已被此前的尝试定论。"""

    def __init__(self, receipt: DouyinCommentActionReceipt) -> None:
        super().__init__("the side effect is already settled")
        self.receipt = receipt


def _default_replay_page(window: BrowserWindow) -> ReplayPage:
    return PlaywrightReplayPage(
        window.playwright_page, action_timeout_seconds=_REPLAY_PAGE_TIMEOUT_SECONDS
    )


class DouyinCommentActionExecution:
    """Execute one confirmed comment without ever redispatching an admitted action."""

    def __init__(
        self,
        *,
        window: BrowserWindow,
        action_gate: ExecutorActionGate,
        ledger: ExecutorLedger,
        clock: DouyinCommentActionClock,
        orchestrator: SkillOrchestrator | None = None,
        replay_page_factory: Callable[[BrowserWindow], ReplayPage] | None = None,
    ) -> None:
        if (
            not isinstance(window, BrowserWindow)
            or not isinstance(action_gate, ExecutorActionGate)
            or not isinstance(ledger, ExecutorLedger)
            or not isinstance(clock, DouyinCommentActionClock)
            or not (orchestrator is None or isinstance(orchestrator, SkillOrchestrator))
            or not (replay_page_factory is None or callable(replay_page_factory))
        ):
            raise DouyinCommentActionRejected
        self._window = window
        self._action_gate = action_gate
        self._ledger = ledger
        self._clock = clock
        self._orchestrator = orchestrator
        self._replay_page_factory = replay_page_factory or _default_replay_page
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

        try:
            orchestrator = (
                self._orchestrator if self._orchestrator is not None else default_orchestrator()
            )
            page = self._replay_page_factory(self._window)
        except Exception:
            return _prepared_receipt(expected, DouyinCommentActionEvidence.PREPARE_UNAVAILABLE)

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
                DOUYIN_COMMENT_SKILL_ID,
                page,
                parameters={DOUYIN_COMMENT_MESSAGE_PARAMETER: intent._rendered_message},
                on_external_dispatch=begin_dispatch,
            )
        except _DispatchRefused:
            return _prepared_receipt(
                expected, DouyinCommentActionEvidence.DISPATCH_PERMISSION_REJECTED
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
                    DouyinCommentActionEvidence.SKILL_RECONCILE_REQUIRED,
                    dispatched_effects[0],
                )
            return _prepared_receipt(expected, DouyinCommentActionEvidence.PREPARE_UNAVAILABLE)

        if report.kind is SkillExecutionKind.NO_ROUTE:
            return _prepared_receipt(
                expected, DouyinCommentActionEvidence.SKILL_AWAITING_RECORDING
            )
        if report.kind is SkillExecutionKind.RECOVERY_PENDING:
            return _prepared_receipt(
                expected, DouyinCommentActionEvidence.SKILL_RECOVERY_PENDING
            )
        if report.kind is SkillExecutionKind.RECONCILE_REQUIRED:
            if not dispatched_effects:
                # 回放器断言 dispatched 必经钩子；到不了这里才算防御。
                return _prepared_receipt(
                    expected, DouyinCommentActionEvidence.PREPARE_UNAVAILABLE
                )
            return self._uncertain(
                expected,
                fingerprint,
                DouyinCommentActionEvidence.SKILL_RECONCILE_REQUIRED,
                dispatched_effects[0],
            )

        # REPLAYED：回放完成且结果证据成立。
        if not dispatched_effects:
            # 路由到的技能没有外部步——什么都没发出去，不能谎称已评论。
            return _prepared_receipt(expected, DouyinCommentActionEvidence.PREPARE_UNAVAILABLE)
        try:
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
                dispatched_effects[0],
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
        + DOUYIN_COMMENT_VERIFICATION_VERSION.encode("ascii")
        + b"\0success_evidence_visible"
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
    "DOUYIN_COMMENT_VERIFICATION_VERSION",
    "DouyinCommentActionClock",
    "DouyinCommentActionEvidence",
    "DouyinCommentActionExecution",
    "DouyinCommentActionIntent",
    "DouyinCommentActionReceipt",
    "DouyinCommentActionRejected",
    "DouyinCommentActionState",
    "comment_action_verification_fingerprint",
]
