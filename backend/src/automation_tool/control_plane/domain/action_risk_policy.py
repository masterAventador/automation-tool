"""Pure, explicit hard-limit policy for externally observable platform actions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

from automation_tool.control_plane.domain.resource_ids import InstallationId
from automation_tool.control_plane.domain.task_definitions import (
    MAX_TASK_INTERVAL_SECONDS,
    MAX_TASK_TARGET_LIMIT,
    DouyinSearchExposureAction,
)
from automation_tool.protocol.limits import MAX_CROSS_RUNTIME_SEQUENCE

ACTION_RISK_POLICY_VERSION = "action-risk-policy.v1"
MAX_ACTION_RISK_LIMIT = MAX_CROSS_RUNTIME_SEQUENCE


class InvalidActionRiskPolicy(ValueError):
    """A hard-limit policy is unbounded, untyped, or internally invalid."""

    def __init__(self) -> None:
        super().__init__("Action risk policy is invalid")


class ActionRiskPlatform(StrEnum):
    """Platforms with an implemented action-risk contract."""

    DOUYIN = "douyin"


@dataclass(frozen=True, slots=True, repr=False)
class ActionRiskScope:
    """One installation/platform/action counter and authorization scope."""

    installation_id: InstallationId
    platform: ActionRiskPlatform
    action: DouyinSearchExposureAction

    def __post_init__(self) -> None:
        if (
            not isinstance(self.installation_id, InstallationId)
            or not isinstance(self.platform, ActionRiskPlatform)
            or not isinstance(self.action, DouyinSearchExposureAction)
        ):
            raise InvalidActionRiskPolicy

    def __repr__(self) -> str:
        return (
            "ActionRiskScope("
            f"platform={self.platform.value!r}, action={self.action.value!r}, "
            "installation_id=<redacted>)"
        )


@dataclass(frozen=True, slots=True, repr=False)
class ActionRiskPolicy:
    """Server-owned limits; the daily limit applies to one UTC calendar day."""

    scope: ActionRiskScope
    minimum_interval: timedelta
    task_action_limit: int
    daily_action_limit: int
    consecutive_failure_threshold: int
    version: str = ACTION_RISK_POLICY_VERSION

    def __post_init__(self) -> None:
        if (
            not isinstance(self.scope, ActionRiskScope)
            or type(self.minimum_interval) is not timedelta
            or self.minimum_interval.microseconds != 0
            or not timedelta(seconds=1)
            <= self.minimum_interval
            <= timedelta(seconds=MAX_TASK_INTERVAL_SECONDS)
            or not _bounded_integer(self.task_action_limit, maximum=MAX_TASK_TARGET_LIMIT)
            or not _bounded_integer(self.daily_action_limit, maximum=MAX_ACTION_RISK_LIMIT)
            or not _bounded_integer(
                self.consecutive_failure_threshold,
                maximum=MAX_ACTION_RISK_LIMIT,
            )
            or self.version != ACTION_RISK_POLICY_VERSION
        ):
            raise InvalidActionRiskPolicy

    @property
    def minimum_interval_seconds(self) -> int:
        return int(self.minimum_interval.total_seconds())

    def __repr__(self) -> str:
        return (
            "ActionRiskPolicy("
            f"scope={self.scope!r}, minimum_interval_seconds="
            f"{self.minimum_interval_seconds!r}, task_action_limit="
            f"{self.task_action_limit!r}, daily_action_limit={self.daily_action_limit!r}, "
            "consecutive_failure_threshold="
            f"{self.consecutive_failure_threshold!r}, version={self.version!r})"
        )


def _bounded_integer(value: object, *, maximum: int) -> bool:
    return type(value) is int and 1 <= value <= maximum


__all__ = [
    "ACTION_RISK_POLICY_VERSION",
    "MAX_ACTION_RISK_LIMIT",
    "ActionRiskPlatform",
    "ActionRiskPolicy",
    "ActionRiskScope",
    "InvalidActionRiskPolicy",
]
