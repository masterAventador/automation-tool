"""Closed, privacy-safe evidence codes for target-level action results."""

from enum import StrEnum
from typing import Final

ACTION_RESULT_EVIDENCE_VERSION = "action-result-evidence.v1"


class ActionResultEvidence(StrEnum):
    AWAITING_EXECUTION = "awaiting_execution"
    ACTION_PENDING = "action_pending"
    ACTION_IN_PROGRESS = "action_in_progress"
    PROFILE_VISIBLE = "profile_visible"
    COMMENT_CONFIRMED = "comment_confirmed"
    MESSAGE_CONFIRMED = "message_confirmed"
    EXECUTOR_REPORTED_SUCCESS = "executor_reported_success"
    USER_EXCLUDED = "user_excluded"
    DUPLICATE_IN_TASK = "duplicate_in_task"
    DUPLICATE_IN_HISTORY = "duplicate_in_history"
    BLACKLISTED = "blacklisted"
    ACTION_CANCELLED = "action_cancelled"
    ADMISSION_REJECTED = "admission_rejected"
    LOCAL_SAFETY_LIMIT = "local_safety_limit"
    LOGIN_REQUIRED = "login_required"
    DIALOG_BLOCKED = "dialog_blocked"
    MESSAGING_NOT_ALLOWED = "messaging_not_allowed"
    FOLLOW_REQUIRED = "follow_required"
    TIMED_OUT = "timed_out"
    PAGE_VERSION_UNKNOWN = "page_version_unknown"
    CONFLICTING_ANCHORS = "conflicting_anchors"
    PAGE_UNAVAILABLE = "page_unavailable"
    VERIFICATION_UNAVAILABLE = "verification_unavailable"
    EXECUTOR_REPORTED_FAILURE = "executor_reported_failure"
    DISPATCH_TIMED_OUT = "dispatch_timed_out"
    DISPATCH_UNAVAILABLE = "dispatch_unavailable"
    FINAL_STATE_UNCONFIRMED = "final_state_unconfirmed"
    RECOVERY_UNCONFIRMED = "recovery_unconfirmed"


SUCCESS_ACTION_RESULT_EVIDENCE: Final[frozenset[ActionResultEvidence]] = frozenset(
    {
        ActionResultEvidence.PROFILE_VISIBLE,
        ActionResultEvidence.COMMENT_CONFIRMED,
        ActionResultEvidence.MESSAGE_CONFIRMED,
        ActionResultEvidence.EXECUTOR_REPORTED_SUCCESS,
    }
)
SKIPPED_ACTION_RESULT_EVIDENCE: Final[frozenset[ActionResultEvidence]] = frozenset(
    {
        ActionResultEvidence.USER_EXCLUDED,
        ActionResultEvidence.DUPLICATE_IN_TASK,
        ActionResultEvidence.DUPLICATE_IN_HISTORY,
        ActionResultEvidence.BLACKLISTED,
        ActionResultEvidence.ACTION_CANCELLED,
    }
)
FAILED_ACTION_RESULT_EVIDENCE: Final[frozenset[ActionResultEvidence]] = frozenset(
    {
        ActionResultEvidence.ADMISSION_REJECTED,
        ActionResultEvidence.LOCAL_SAFETY_LIMIT,
        ActionResultEvidence.LOGIN_REQUIRED,
        ActionResultEvidence.DIALOG_BLOCKED,
        ActionResultEvidence.MESSAGING_NOT_ALLOWED,
        ActionResultEvidence.FOLLOW_REQUIRED,
        ActionResultEvidence.TIMED_OUT,
        ActionResultEvidence.PAGE_VERSION_UNKNOWN,
        ActionResultEvidence.CONFLICTING_ANCHORS,
        ActionResultEvidence.PAGE_UNAVAILABLE,
        ActionResultEvidence.VERIFICATION_UNAVAILABLE,
        ActionResultEvidence.EXECUTOR_REPORTED_FAILURE,
    }
)
UNCERTAIN_ACTION_RESULT_EVIDENCE: Final[frozenset[ActionResultEvidence]] = frozenset(
    {
        ActionResultEvidence.DISPATCH_TIMED_OUT,
        ActionResultEvidence.DISPATCH_UNAVAILABLE,
        ActionResultEvidence.FINAL_STATE_UNCONFIRMED,
        ActionResultEvidence.RECOVERY_UNCONFIRMED,
    }
)
EXECUTOR_ACTION_RESULT_EVIDENCE: Final[frozenset[ActionResultEvidence]] = frozenset(
    SUCCESS_ACTION_RESULT_EVIDENCE
    | FAILED_ACTION_RESULT_EVIDENCE
    | UNCERTAIN_ACTION_RESULT_EVIDENCE
)
PERSISTED_ACTION_RESULT_EVIDENCE: Final[frozenset[ActionResultEvidence]] = frozenset(
    EXECUTOR_ACTION_RESULT_EVIDENCE | {ActionResultEvidence.ACTION_CANCELLED}
)


__all__ = [
    "ACTION_RESULT_EVIDENCE_VERSION",
    "EXECUTOR_ACTION_RESULT_EVIDENCE",
    "FAILED_ACTION_RESULT_EVIDENCE",
    "PERSISTED_ACTION_RESULT_EVIDENCE",
    "SKIPPED_ACTION_RESULT_EVIDENCE",
    "SUCCESS_ACTION_RESULT_EVIDENCE",
    "UNCERTAIN_ACTION_RESULT_EVIDENCE",
    "ActionResultEvidence",
]
