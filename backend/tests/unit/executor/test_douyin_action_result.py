from __future__ import annotations

from collections.abc import Callable
from typing import cast

import pytest

from automation_tool.executor.rpa.douyin.action_result import (
    DouyinActionResultRejected,
    browse_action_result,
    comment_action_result,
    direct_message_action_result,
    recovered_action_result,
)
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
    DouyinSideEffectRecoveryEvidence,
    DouyinSideEffectRecoveryReceipt,
    DouyinSideEffectRecoveryState,
)
from automation_tool.executor.side_effect_ledger import SideEffectState
from automation_tool.protocol import (
    ACTION_RESULT_EVIDENCE_VERSION,
    DouyinSearchExposureAction,
    ProtocolActionId,
    ProtocolTargetId,
)

ACTION_ID = ProtocolActionId("123e4567-e89b-42d3-a456-426614174008")
TARGET_ID = ProtocolTargetId("123e4567-e89b-42d3-a456-426614174006")


def test_browse_observations_map_to_closed_success_and_failure_facts() -> None:
    completed = browse_action_result(
        action_id=ACTION_ID,
        target_id=TARGET_ID,
        observation=DouyinBrowseExecutionObservation(
            state=DouyinBrowseExecutionState.COMPLETED,
            evidence=DouyinBrowseExecutionEvidence.PROFILE_VISIBLE,
        ),
    )
    failed = browse_action_result(
        action_id=ACTION_ID,
        target_id=TARGET_ID,
        observation=DouyinBrowseExecutionObservation(
            state=DouyinBrowseExecutionState.LOGIN_REQUIRED,
            evidence=DouyinBrowseExecutionEvidence.LOGIN_REQUIRED,
        ),
    )

    assert (completed.message_type, completed.evidence.value) == (
        "step.completed",
        "profile_visible",
    )
    assert (failed.message_type, failed.evidence.value) == (
        "step.failed",
        "login_required",
    )


def comment_receipt(
    *,
    state: DouyinCommentActionState,
    evidence: DouyinCommentActionEvidence,
) -> DouyinCommentActionReceipt:
    if state is DouyinCommentActionState.VERIFIED:
        side_effect_state = SideEffectState.VERIFIED
        side_effect_revision = 3
        replayed = evidence is DouyinCommentActionEvidence.REPLAY_VERIFIED
    elif state is DouyinCommentActionState.OUTCOME_UNCERTAIN:
        side_effect_state = SideEffectState.DISPATCHED
        side_effect_revision = 2
        replayed = evidence is DouyinCommentActionEvidence.REPLAY_UNCERTAIN
        if replayed:
            side_effect_state = SideEffectState.UNCERTAIN
            side_effect_revision = 3
    elif evidence in {
        DouyinCommentActionEvidence.ADMISSION_REJECTED,
        DouyinCommentActionEvidence.LOCAL_EMERGENCY_STOP,
        DouyinCommentActionEvidence.LOCAL_MINIMUM_INTERVAL,
        DouyinCommentActionEvidence.LOCAL_TASK_ACTION_LIMIT,
        DouyinCommentActionEvidence.LEDGER_UNAVAILABLE,
    }:
        side_effect_state = None
        side_effect_revision = None
        replayed = False
    else:
        side_effect_state = SideEffectState.PREPARED
        side_effect_revision = 1
        replayed = False
    return DouyinCommentActionReceipt(
        action_id=ACTION_ID,
        target_id=TARGET_ID,
        state=state,
        evidence=evidence,
        side_effect_state=side_effect_state,
        side_effect_revision=side_effect_revision,
        replayed=replayed,
    )


def direct_message_receipt(
    *,
    state: DouyinDirectMessageActionState,
    evidence: DouyinDirectMessageActionEvidence,
) -> DouyinDirectMessageActionReceipt:
    if state is DouyinDirectMessageActionState.VERIFIED:
        side_effect_state = SideEffectState.VERIFIED
        side_effect_revision = 3
        replayed = evidence is DouyinDirectMessageActionEvidence.REPLAY_VERIFIED
    elif state is DouyinDirectMessageActionState.OUTCOME_UNCERTAIN:
        side_effect_state = SideEffectState.DISPATCHED
        side_effect_revision = 2
        replayed = evidence is DouyinDirectMessageActionEvidence.REPLAY_UNCERTAIN
        if replayed:
            side_effect_state = SideEffectState.UNCERTAIN
            side_effect_revision = 3
    elif evidence in {
        DouyinDirectMessageActionEvidence.ADMISSION_REJECTED,
        DouyinDirectMessageActionEvidence.LOCAL_EMERGENCY_STOP,
        DouyinDirectMessageActionEvidence.LOCAL_MINIMUM_INTERVAL,
        DouyinDirectMessageActionEvidence.LOCAL_TASK_ACTION_LIMIT,
        DouyinDirectMessageActionEvidence.LEDGER_UNAVAILABLE,
    }:
        side_effect_state = None
        side_effect_revision = None
        replayed = False
    else:
        side_effect_state = SideEffectState.PREPARED
        side_effect_revision = 1
        replayed = False
    return DouyinDirectMessageActionReceipt(
        action_id=ACTION_ID,
        target_id=TARGET_ID,
        state=state,
        evidence=evidence,
        side_effect_state=side_effect_state,
        side_effect_revision=side_effect_revision,
        replayed=replayed,
    )


def test_comment_receipt_maps_to_closed_success_payload() -> None:
    fact = comment_action_result(
        DouyinCommentActionReceipt(
            action_id=ACTION_ID,
            target_id=TARGET_ID,
            state=DouyinCommentActionState.VERIFIED,
            evidence=DouyinCommentActionEvidence.COMMENT_CONFIRMED,
            side_effect_state=SideEffectState.VERIFIED,
            side_effect_revision=3,
            replayed=False,
        )
    )

    assert fact.message_type == "step.completed"
    assert fact.payload == {
        "action_id": str(ACTION_ID),
        "evidence": "comment_confirmed",
        "evidence_version": ACTION_RESULT_EVIDENCE_VERSION,
    }
    assert str(ACTION_ID) not in repr(fact)


def test_direct_message_failure_and_uncertain_are_not_reported_as_success() -> None:
    failed = direct_message_action_result(
        DouyinDirectMessageActionReceipt(
            action_id=ACTION_ID,
            target_id=TARGET_ID,
            state=DouyinDirectMessageActionState.NOT_DISPATCHED,
            evidence=DouyinDirectMessageActionEvidence.READY_FOLLOW_REQUIRED,
            side_effect_state=SideEffectState.PREPARED,
            side_effect_revision=1,
            replayed=False,
        )
    )
    uncertain = direct_message_action_result(
        DouyinDirectMessageActionReceipt(
            action_id=ACTION_ID,
            target_id=TARGET_ID,
            state=DouyinDirectMessageActionState.OUTCOME_UNCERTAIN,
            evidence=DouyinDirectMessageActionEvidence.DISPATCH_TIMED_OUT,
            side_effect_state=SideEffectState.DISPATCHED,
            side_effect_revision=2,
            replayed=False,
        )
    )

    assert (failed.message_type, failed.evidence.value) == ("step.failed", "follow_required")
    assert (uncertain.message_type, uncertain.evidence.value) == (
        "task.outcome_uncertain",
        "dispatch_timed_out",
    )


def test_recovery_only_reports_dispatched_terminal_facts() -> None:
    verified = recovered_action_result(
        DouyinSideEffectRecoveryReceipt(
            action_id=ACTION_ID,
            target_id=TARGET_ID,
            action=DouyinSearchExposureAction.COMMENT,
            state=DouyinSideEffectRecoveryState.VERIFIED,
            evidence=DouyinSideEffectRecoveryEvidence.ALREADY_VERIFIED,
            side_effect_state=SideEffectState.VERIFIED,
            side_effect_revision=3,
            replayed=True,
        )
    )
    assert (verified.message_type, verified.evidence.value) == (
        "step.completed",
        "comment_confirmed",
    )

    with pytest.raises(DouyinActionResultRejected):
        recovered_action_result(
            DouyinSideEffectRecoveryReceipt(
                action_id=ACTION_ID,
                target_id=TARGET_ID,
                action=DouyinSearchExposureAction.COMMENT,
                state=DouyinSideEffectRecoveryState.NOT_DISPATCHED,
                evidence=DouyinSideEffectRecoveryEvidence.PREPARED_NOT_DISPATCHED,
                side_effect_state=SideEffectState.PREPARED,
                side_effect_revision=1,
                replayed=False,
            )
        )


@pytest.mark.parametrize(
    ("evidence", "expected"),
    (
        (DouyinCommentActionEvidence.ADMISSION_REJECTED, "admission_rejected"),
        (DouyinCommentActionEvidence.LOCAL_EMERGENCY_STOP, "local_safety_limit"),
        (DouyinCommentActionEvidence.LOCAL_MINIMUM_INTERVAL, "local_safety_limit"),
        (DouyinCommentActionEvidence.LOCAL_TASK_ACTION_LIMIT, "local_safety_limit"),
        # 待录制/待修复：自动化尚不能安全驱动这个页面——wire 层的既有语义
        # 就是 page_version_unknown（前端显示「页面版本无法安全识别」）。
        (DouyinCommentActionEvidence.SKILL_AWAITING_RECORDING, "page_version_unknown"),
        (DouyinCommentActionEvidence.SKILL_RECOVERY_PENDING, "page_version_unknown"),
        (DouyinCommentActionEvidence.PREPARE_UNAVAILABLE, "page_unavailable"),
        (
            DouyinCommentActionEvidence.DISPATCH_PERMISSION_REJECTED,
            "executor_reported_failure",
        ),
        (DouyinCommentActionEvidence.LEDGER_UNAVAILABLE, "executor_reported_failure"),
    ),
)
def test_comment_failure_receipts_map_to_closed_safe_evidence(
    evidence: DouyinCommentActionEvidence,
    expected: str,
) -> None:
    fact = comment_action_result(
        comment_receipt(state=DouyinCommentActionState.NOT_DISPATCHED, evidence=evidence)
    )
    assert (fact.message_type, fact.evidence.value) == ("step.failed", expected)


@pytest.mark.parametrize(
    ("evidence", "expected"),
    (
        (DouyinCommentActionEvidence.SKILL_RECONCILE_REQUIRED, "final_state_unconfirmed"),
        (DouyinCommentActionEvidence.VERIFICATION_UNAVAILABLE, "final_state_unconfirmed"),
        (DouyinCommentActionEvidence.REPLAY_UNCERTAIN, "recovery_unconfirmed"),
    ),
)
def test_comment_uncertain_receipts_never_collapse_to_failure_or_success(
    evidence: DouyinCommentActionEvidence,
    expected: str,
) -> None:
    fact = comment_action_result(
        comment_receipt(state=DouyinCommentActionState.OUTCOME_UNCERTAIN, evidence=evidence)
    )
    assert (fact.message_type, fact.evidence.value) == ("task.outcome_uncertain", expected)


@pytest.mark.parametrize(
    ("evidence", "expected"),
    (
        (DouyinDirectMessageActionEvidence.ADMISSION_REJECTED, "admission_rejected"),
        (DouyinDirectMessageActionEvidence.LOCAL_EMERGENCY_STOP, "local_safety_limit"),
        (DouyinDirectMessageActionEvidence.LOCAL_MINIMUM_INTERVAL, "local_safety_limit"),
        (DouyinDirectMessageActionEvidence.LOCAL_TASK_ACTION_LIMIT, "local_safety_limit"),
        (DouyinDirectMessageActionEvidence.READY_LOGIN_REQUIRED, "login_required"),
        (DouyinDirectMessageActionEvidence.READY_DIALOG_BLOCKED, "dialog_blocked"),
        (
            DouyinDirectMessageActionEvidence.READY_MESSAGING_NOT_ALLOWED,
            "messaging_not_allowed",
        ),
        (DouyinDirectMessageActionEvidence.READY_FOLLOW_REQUIRED, "follow_required"),
        (DouyinDirectMessageActionEvidence.READY_TIMED_OUT, "timed_out"),
        (
            DouyinDirectMessageActionEvidence.READY_PAGE_VERSION_UNKNOWN,
            "page_version_unknown",
        ),
        (
            DouyinDirectMessageActionEvidence.READY_CONFLICTING_ANCHORS,
            "conflicting_anchors",
        ),
        (DouyinDirectMessageActionEvidence.READY_PAGE_UNAVAILABLE, "page_unavailable"),
        (DouyinDirectMessageActionEvidence.LEDGER_UNAVAILABLE, "executor_reported_failure"),
    ),
)
def test_direct_message_failure_receipts_map_to_closed_safe_evidence(
    evidence: DouyinDirectMessageActionEvidence,
    expected: str,
) -> None:
    fact = direct_message_action_result(
        direct_message_receipt(
            state=DouyinDirectMessageActionState.NOT_DISPATCHED,
            evidence=evidence,
        )
    )
    assert (fact.message_type, fact.evidence.value) == ("step.failed", expected)


@pytest.mark.parametrize(
    ("evidence", "expected"),
    (
        (DouyinDirectMessageActionEvidence.DISPATCH_UNAVAILABLE, "dispatch_unavailable"),
        (DouyinDirectMessageActionEvidence.REPLAY_UNCERTAIN, "recovery_unconfirmed"),
        (DouyinDirectMessageActionEvidence.FINAL_LOGIN_REQUIRED, "final_state_unconfirmed"),
    ),
)
def test_direct_message_uncertain_receipts_remain_uncertain(
    evidence: DouyinDirectMessageActionEvidence,
    expected: str,
) -> None:
    fact = direct_message_action_result(
        direct_message_receipt(
            state=DouyinDirectMessageActionState.OUTCOME_UNCERTAIN,
            evidence=evidence,
        )
    )
    assert (fact.message_type, fact.evidence.value) == ("task.outcome_uncertain", expected)


def test_direct_message_success_and_recovery_message_or_uncertain_are_exact() -> None:
    success = direct_message_action_result(
        direct_message_receipt(
            state=DouyinDirectMessageActionState.VERIFIED,
            evidence=DouyinDirectMessageActionEvidence.MESSAGE_CONFIRMED,
        )
    )
    recovered_message = recovered_action_result(
        DouyinSideEffectRecoveryReceipt(
            action_id=ACTION_ID,
            target_id=TARGET_ID,
            action=DouyinSearchExposureAction.DIRECT_MESSAGE,
            state=DouyinSideEffectRecoveryState.VERIFIED,
            evidence=DouyinSideEffectRecoveryEvidence.MESSAGE_CONFIRMED,
            side_effect_state=SideEffectState.VERIFIED,
            side_effect_revision=3,
            replayed=False,
        )
    )
    recovered_uncertain = recovered_action_result(
        DouyinSideEffectRecoveryReceipt(
            action_id=ACTION_ID,
            target_id=TARGET_ID,
            action=DouyinSearchExposureAction.COMMENT,
            state=DouyinSideEffectRecoveryState.OUTCOME_UNCERTAIN,
            evidence=DouyinSideEffectRecoveryEvidence.ALREADY_UNCERTAIN,
            side_effect_state=SideEffectState.UNCERTAIN,
            side_effect_revision=3,
            replayed=True,
        )
    )
    assert (success.message_type, success.evidence.value) == ("step.completed", "message_confirmed")
    assert recovered_message.evidence.value == "message_confirmed"
    assert (recovered_uncertain.message_type, recovered_uncertain.evidence.value) == (
        "task.outcome_uncertain",
        "recovery_unconfirmed",
    )


def test_action_result_mappers_reject_wrong_receipt_types_without_disclosure() -> None:
    invalid: tuple[Callable[[], object], ...] = (
        lambda: browse_action_result(
            action_id=cast(ProtocolActionId, object()),
            target_id=TARGET_ID,
            observation=cast(DouyinBrowseExecutionObservation, object()),
        ),
        lambda: comment_action_result(cast(DouyinCommentActionReceipt, object())),
        lambda: direct_message_action_result(cast(DouyinDirectMessageActionReceipt, object())),
        lambda: recovered_action_result(cast(DouyinSideEffectRecoveryReceipt, object())),
    )
    for invoke in invalid:
        with pytest.raises(DouyinActionResultRejected, match="invalid") as captured:
            invoke()
        assert "private" not in str(captured.value)
