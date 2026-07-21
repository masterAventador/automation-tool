"""Application records and stable failures for server-side action authorization."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum

from automation_tool.control_plane.domain import (
    ACTION_RISK_POLICY_VERSION,
    MAX_ACTION_RISK_LIMIT,
    MAX_TASK_INTERVAL_SECONDS,
    MAX_TASK_TARGET_LIMIT,
    ActionId,
    ActionRiskPlatform,
    DouyinSearchExposureAction,
    ExecutionAttemptId,
    InstallationId,
    TargetId,
    TaskId,
)


class ActionRiskAuthorizationRejected(PermissionError):
    def __init__(self) -> None:
        super().__init__("Action risk authorization is rejected")


class ActionRiskAuthorizationUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Action risk authorization is unavailable")


class ActionRiskLimitReason(StrEnum):
    MINIMUM_INTERVAL = "minimum_interval"
    TASK_ACTION_LIMIT = "task_action_limit"
    DAILY_ACTION_LIMIT = "daily_action_limit"
    CONSECUTIVE_FAILURE_CIRCUIT = "consecutive_failure_circuit"


class ActionRiskAuthorizationLimited(PermissionError):
    def __init__(self, reason: ActionRiskLimitReason) -> None:
        if not isinstance(reason, ActionRiskLimitReason):
            raise ActionRiskAuthorizationRejected
        self.reason = reason
        super().__init__("Action risk authorization is rate limited")


@dataclass(frozen=True, slots=True, repr=False)
class ActionRiskAuthorization:
    """One immutable PostgreSQL-backed authorization and counter snapshot."""

    action_id: ActionId
    target_id: TargetId
    execution_attempt_id: ExecutionAttemptId
    task_id: TaskId
    installation_id: InstallationId
    ordinal: int
    platform: ActionRiskPlatform
    action: DouyinSearchExposureAction
    policy_version: str
    effective_minimum_interval_seconds: int
    task_action_limit: int
    daily_action_limit: int
    consecutive_failure_threshold: int
    task_count_after: int
    daily_count_after: int
    authorized_day: date
    authorized_at: datetime
    created_at: datetime

    def __post_init__(self) -> None:
        authorized_at = _canonical_utc(self.authorized_at)
        created_at = _canonical_utc(self.created_at)
        if (
            not isinstance(self.action_id, ActionId)
            or not isinstance(self.target_id, TargetId)
            or not isinstance(self.execution_attempt_id, ExecutionAttemptId)
            or not isinstance(self.task_id, TaskId)
            or not isinstance(self.installation_id, InstallationId)
            or not _bounded_integer(self.ordinal, MAX_TASK_TARGET_LIMIT)
            or not isinstance(self.platform, ActionRiskPlatform)
            or not isinstance(self.action, DouyinSearchExposureAction)
            or self.policy_version != ACTION_RISK_POLICY_VERSION
            or not _bounded_integer(
                self.effective_minimum_interval_seconds,
                MAX_TASK_INTERVAL_SECONDS,
            )
            or not _bounded_integer(self.task_action_limit, MAX_TASK_TARGET_LIMIT)
            or not _bounded_integer(self.daily_action_limit, MAX_ACTION_RISK_LIMIT)
            or not _bounded_integer(
                self.consecutive_failure_threshold,
                MAX_ACTION_RISK_LIMIT,
            )
            or not _bounded_integer(self.task_count_after, self.task_action_limit)
            or not _bounded_integer(self.daily_count_after, self.daily_action_limit)
            or type(self.authorized_day) is not date
            or authorized_at is None
            or created_at is None
            or self.authorized_day != authorized_at.date()
            or created_at < authorized_at
        ):
            raise ActionRiskAuthorizationRejected
        object.__setattr__(self, "authorized_at", authorized_at)
        object.__setattr__(self, "created_at", created_at)

    @property
    def remaining_task_actions(self) -> int:
        return self.task_action_limit - self.task_count_after

    @property
    def remaining_daily_actions(self) -> int:
        return self.daily_action_limit - self.daily_count_after

    def __repr__(self) -> str:
        return (
            "ActionRiskAuthorization("
            f"platform={self.platform.value!r}, action={self.action.value!r}, "
            f"ordinal={self.ordinal!r}, task_count_after={self.task_count_after!r}, "
            f"daily_count_after={self.daily_count_after!r}, <redacted>)"
        )


def _bounded_integer(value: object, maximum: int) -> bool:
    return type(value) is int and 1 <= value <= maximum


def _canonical_utc(value: object) -> datetime | None:
    if type(value) is not datetime or value.tzinfo is None:
        return None
    try:
        if value.utcoffset() != timedelta(0):
            return None
        return value.astimezone(UTC)
    except Exception:
        return None


__all__ = [
    "ActionRiskAuthorization",
    "ActionRiskAuthorizationLimited",
    "ActionRiskAuthorizationRejected",
    "ActionRiskAuthorizationUnavailable",
    "ActionRiskLimitReason",
]
