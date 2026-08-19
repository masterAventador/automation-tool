"""Map local side-effect receipts to the closed target-result wire fact."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from automation_tool.executor.rpa.douyin.browse import (
    DouyinBrowseExecutionEvidence,
    DouyinBrowseExecutionObservation,
    DouyinBrowseExecutionState,
)
from automation_tool.executor.rpa.douyin.comment_action import (
    DouyinCommentActionEvidence,
    DouyinCommentActionReceipt,
    DouyinCommentActionState,
)
from automation_tool.executor.rpa.douyin.direct_message_action import (
    DouyinDirectMessageActionEvidence,
    DouyinDirectMessageActionReceipt,
    DouyinDirectMessageActionState,
)
from automation_tool.executor.rpa.douyin.side_effect_recovery import (
    DouyinSideEffectRecoveryReceipt,
    DouyinSideEffectRecoveryState,
)
from automation_tool.protocol import (
    ACTION_RESULT_EVIDENCE_VERSION,
    ActionResultEvidence,
    DouyinSearchExposureAction,
    ProtocolActionId,
    ProtocolTargetId,
)


class DouyinActionResultRejected(ValueError):
    def __init__(self) -> None:
        super().__init__("Douyin action result is invalid")


@dataclass(frozen=True, slots=True, repr=False)
class DouyinActionResultFact:
    action_id: ProtocolActionId
    target_id: ProtocolTargetId
    message_type: Literal["step.completed", "step.failed", "task.outcome_uncertain"]
    evidence: ActionResultEvidence

    @property
    def payload(self) -> dict[str, str]:
        return {
            "action_id": str(self.action_id),
            "evidence": self.evidence.value,
            "evidence_version": ACTION_RESULT_EVIDENCE_VERSION,
        }

    def __repr__(self) -> str:
        return (
            "DouyinActionResultFact("
            f"message_type={self.message_type!r}, evidence={self.evidence.value!r}, "
            "<redacted>)"
        )


_COMMENT_FAILURES = {
    DouyinCommentActionEvidence.ADMISSION_REJECTED: ActionResultEvidence.ADMISSION_REJECTED,
    DouyinCommentActionEvidence.LOCAL_EMERGENCY_STOP: ActionResultEvidence.LOCAL_SAFETY_LIMIT,
    DouyinCommentActionEvidence.LOCAL_MINIMUM_INTERVAL: ActionResultEvidence.LOCAL_SAFETY_LIMIT,
    DouyinCommentActionEvidence.LOCAL_TASK_ACTION_LIMIT: ActionResultEvidence.LOCAL_SAFETY_LIMIT,
    # 待录制/待修复：自动化尚不能安全驱动这个页面。wire 层既有语义即
    # page_version_unknown（前端显示「页面版本无法安全识别」）。
    DouyinCommentActionEvidence.SKILL_AWAITING_RECORDING: (
        ActionResultEvidence.PAGE_VERSION_UNKNOWN
    ),
    DouyinCommentActionEvidence.SKILL_RECOVERY_PENDING: (
        ActionResultEvidence.PAGE_VERSION_UNKNOWN
    ),
    DouyinCommentActionEvidence.PREPARE_UNAVAILABLE: ActionResultEvidence.PAGE_UNAVAILABLE,
}
_DIRECT_FAILURES = {
    DouyinDirectMessageActionEvidence.ADMISSION_REJECTED: ActionResultEvidence.ADMISSION_REJECTED,
    DouyinDirectMessageActionEvidence.LOCAL_EMERGENCY_STOP: ActionResultEvidence.LOCAL_SAFETY_LIMIT,
    DouyinDirectMessageActionEvidence.LOCAL_MINIMUM_INTERVAL: (
        ActionResultEvidence.LOCAL_SAFETY_LIMIT
    ),
    DouyinDirectMessageActionEvidence.LOCAL_TASK_ACTION_LIMIT: (
        ActionResultEvidence.LOCAL_SAFETY_LIMIT
    ),
    DouyinDirectMessageActionEvidence.SKILL_AWAITING_RECORDING: (
        ActionResultEvidence.PAGE_VERSION_UNKNOWN
    ),
    DouyinDirectMessageActionEvidence.SKILL_RECOVERY_PENDING: (
        ActionResultEvidence.PAGE_VERSION_UNKNOWN
    ),
    DouyinDirectMessageActionEvidence.PREPARE_UNAVAILABLE: (
        ActionResultEvidence.PAGE_UNAVAILABLE
    ),
}
_BROWSE_FAILURES = {
    DouyinBrowseExecutionEvidence.NAVIGATION_TIMED_OUT: ActionResultEvidence.TIMED_OUT,
    DouyinBrowseExecutionEvidence.PAGE_VERSION_UNKNOWN: ActionResultEvidence.PAGE_VERSION_UNKNOWN,
    DouyinBrowseExecutionEvidence.PAGE_UNAVAILABLE: ActionResultEvidence.PAGE_UNAVAILABLE,
    DouyinBrowseExecutionEvidence.CANCELLATION_UNAVAILABLE: (
        ActionResultEvidence.EXECUTOR_REPORTED_FAILURE
    ),
    DouyinBrowseExecutionEvidence.CANCELLATION_REQUESTED: (
        ActionResultEvidence.EXECUTOR_REPORTED_FAILURE
    ),
}


def _uncertain_evidence(value: object) -> ActionResultEvidence:
    if value in {
        DouyinCommentActionEvidence.REPLAY_UNCERTAIN,
        DouyinDirectMessageActionEvidence.REPLAY_UNCERTAIN,
    }:
        return ActionResultEvidence.RECOVERY_UNCONFIRMED
    return ActionResultEvidence.FINAL_STATE_UNCONFIRMED


def browse_action_result(
    *,
    action_id: ProtocolActionId,
    target_id: ProtocolTargetId,
    observation: DouyinBrowseExecutionObservation,
) -> DouyinActionResultFact:
    if (
        type(action_id) is not ProtocolActionId
        or type(target_id) is not ProtocolTargetId
        or not isinstance(observation, DouyinBrowseExecutionObservation)
    ):
        raise DouyinActionResultRejected
    if observation.state is DouyinBrowseExecutionState.COMPLETED:
        return DouyinActionResultFact(
            action_id,
            target_id,
            "step.completed",
            ActionResultEvidence.PROFILE_VISIBLE,
        )
    return DouyinActionResultFact(
        action_id,
        target_id,
        "step.failed",
        _BROWSE_FAILURES.get(
            observation.evidence,
            ActionResultEvidence.EXECUTOR_REPORTED_FAILURE,
        ),
    )


def comment_action_result(receipt: DouyinCommentActionReceipt) -> DouyinActionResultFact:
    if not isinstance(receipt, DouyinCommentActionReceipt):
        raise DouyinActionResultRejected
    message_type: Literal["step.completed", "step.failed", "task.outcome_uncertain"]
    if receipt.state is DouyinCommentActionState.VERIFIED:
        message_type = "step.completed"
        evidence = ActionResultEvidence.COMMENT_CONFIRMED
    elif receipt.state is DouyinCommentActionState.OUTCOME_UNCERTAIN:
        message_type = "task.outcome_uncertain"
        evidence = _uncertain_evidence(receipt.evidence)
    else:
        message_type = "step.failed"
        evidence = _COMMENT_FAILURES.get(
            receipt.evidence,
            ActionResultEvidence.EXECUTOR_REPORTED_FAILURE,
        )
    return DouyinActionResultFact(receipt.action_id, receipt.target_id, message_type, evidence)


def direct_message_action_result(
    receipt: DouyinDirectMessageActionReceipt,
) -> DouyinActionResultFact:
    if not isinstance(receipt, DouyinDirectMessageActionReceipt):
        raise DouyinActionResultRejected
    message_type: Literal["step.completed", "step.failed", "task.outcome_uncertain"]
    if receipt.state is DouyinDirectMessageActionState.VERIFIED:
        message_type = "step.completed"
        evidence = ActionResultEvidence.MESSAGE_CONFIRMED
    elif receipt.state is DouyinDirectMessageActionState.OUTCOME_UNCERTAIN:
        message_type = "task.outcome_uncertain"
        evidence = _uncertain_evidence(receipt.evidence)
    else:
        message_type = "step.failed"
        evidence = _DIRECT_FAILURES.get(
            receipt.evidence,
            ActionResultEvidence.EXECUTOR_REPORTED_FAILURE,
        )
    return DouyinActionResultFact(receipt.action_id, receipt.target_id, message_type, evidence)


def recovered_action_result(receipt: DouyinSideEffectRecoveryReceipt) -> DouyinActionResultFact:
    if (
        not isinstance(receipt, DouyinSideEffectRecoveryReceipt)
        or receipt.state is DouyinSideEffectRecoveryState.NOT_DISPATCHED
    ):
        raise DouyinActionResultRejected
    message_type: Literal["step.completed", "task.outcome_uncertain"]
    if receipt.state is DouyinSideEffectRecoveryState.VERIFIED:
        message_type = "step.completed"
        evidence = (
            ActionResultEvidence.COMMENT_CONFIRMED
            if receipt.action is DouyinSearchExposureAction.COMMENT
            else ActionResultEvidence.MESSAGE_CONFIRMED
        )
    else:
        message_type = "task.outcome_uncertain"
        evidence = ActionResultEvidence.RECOVERY_UNCONFIRMED
    return DouyinActionResultFact(receipt.action_id, receipt.target_id, message_type, evidence)


__all__ = [
    "DouyinActionResultFact",
    "DouyinActionResultRejected",
    "browse_action_result",
    "comment_action_result",
    "direct_message_action_result",
    "recovered_action_result",
]
