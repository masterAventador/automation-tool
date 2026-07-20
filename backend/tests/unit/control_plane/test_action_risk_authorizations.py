from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime, timedelta, timezone, tzinfo
from typing import Any

import pytest

from automation_tool.control_plane.application.action_risk_authorizations import (
    ActionRiskAuthorization,
    ActionRiskAuthorizationLimited,
    ActionRiskAuthorizationRejected,
    ActionRiskAuthorizationUnavailable,
    ActionRiskLimitReason,
)
from automation_tool.control_plane.domain import (
    ACTION_RISK_POLICY_VERSION,
    ActionId,
    ActionRiskPlatform,
    DouyinSearchExposureAction,
    ExecutionAttemptId,
    InstallationId,
    TargetId,
    TaskId,
)
from automation_tool.control_plane.infrastructure.database import (
    SqlAlchemyActionRiskAuthorizationRepository,
)
from automation_tool.control_plane.infrastructure.database import (
    action_risk_authorization_repository as repository_module,
)

NOW = datetime(2026, 7, 20, 1, 0, tzinfo=UTC)


class BrokenTimezone(tzinfo):
    def utcoffset(self, value: datetime | None) -> timedelta | None:
        raise RuntimeError("private timezone failure")

    def dst(self, value: datetime | None) -> timedelta | None:
        return None

    def tzname(self, value: datetime | None) -> str | None:
        return None


def authorization(**changes: Any) -> ActionRiskAuthorization:
    values: dict[str, object] = {
        "action_id": ActionId.parse("123e4567-e89b-42d3-a456-426614174001"),
        "target_id": TargetId.parse("123e4567-e89b-42d3-a456-426614174002"),
        "execution_attempt_id": ExecutionAttemptId.parse("123e4567-e89b-42d3-a456-426614174003"),
        "task_id": TaskId.parse("123e4567-e89b-42d3-a456-426614174004"),
        "installation_id": InstallationId.parse("123e4567-e89b-42d3-a456-426614174005"),
        "ordinal": 1,
        "platform": ActionRiskPlatform.DOUYIN,
        "action": DouyinSearchExposureAction.COMMENT,
        "policy_version": ACTION_RISK_POLICY_VERSION,
        "effective_minimum_interval_seconds": 30,
        "task_action_limit": 20,
        "daily_action_limit": 50,
        "consecutive_failure_threshold": 3,
        "task_count_after": 1,
        "daily_count_after": 1,
        "authorized_day": date(2026, 7, 20),
        "authorized_at": NOW,
        "created_at": NOW,
    }
    values.update(changes)
    return ActionRiskAuthorization(**values)  # type: ignore[arg-type]


def test_authorization_is_immutable_canonical_redacted_and_reports_remaining_counts() -> None:
    value = authorization()

    assert value.authorized_at is NOW
    assert value.created_at is NOW
    assert value.remaining_task_actions == 19
    assert value.remaining_daily_actions == 49
    assert "123e4567" not in repr(value)
    assert "comment" in repr(value)
    with pytest.raises(FrozenInstanceError):
        value.task_count_after = 2  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "invalid"),
    (
        ("action_id", TargetId.new()),
        ("target_id", TaskId.new()),
        ("execution_attempt_id", TaskId.new()),
        ("task_id", TargetId.new()),
        ("installation_id", TaskId.new()),
        ("ordinal", True),
        ("ordinal", 0),
        ("ordinal", 101),
        ("platform", "douyin"),
        ("action", "comment"),
        ("policy_version", "latest"),
        ("effective_minimum_interval_seconds", 0),
        ("effective_minimum_interval_seconds", 3601),
        ("task_action_limit", 0),
        ("task_action_limit", 101),
        ("daily_action_limit", 0),
        ("consecutive_failure_threshold", 0),
        ("task_count_after", 0),
        ("task_count_after", 21),
        ("daily_count_after", 0),
        ("daily_count_after", 51),
        ("authorized_day", date(2026, 7, 19)),
        ("authorized_day", "2026-07-20"),
        ("authorized_at", datetime(2026, 7, 20, 1, 0)),
        ("authorized_at", datetime(2026, 7, 20, 9, 0, tzinfo=timezone(timedelta(hours=8)))),
        ("authorized_at", datetime(2026, 7, 20, 1, 0, tzinfo=BrokenTimezone())),
        ("created_at", NOW - timedelta(microseconds=1)),
    ),
)
def test_authorization_rejects_forged_incoherent_or_non_utc_values(
    field: str,
    invalid: Any,
) -> None:
    with pytest.raises(
        ActionRiskAuthorizationRejected,
        match="Action risk authorization is rejected",
    ) as captured:
        replace(authorization(), **{field: invalid})

    assert captured.value.__cause__ is None
    assert "123e4567" not in str(captured.value)


def test_limit_reason_is_a_closed_public_contract() -> None:
    assert tuple(ActionRiskLimitReason) == (
        ActionRiskLimitReason.MINIMUM_INTERVAL,
        ActionRiskLimitReason.TASK_ACTION_LIMIT,
        ActionRiskLimitReason.DAILY_ACTION_LIMIT,
    )


def test_failures_are_stable_redacted_and_reject_invalid_limit_reasons() -> None:
    assert str(ActionRiskAuthorizationUnavailable()) == ("Action risk authorization is unavailable")
    with pytest.raises(ActionRiskAuthorizationRejected) as captured:
        ActionRiskAuthorizationLimited("private")  # type: ignore[arg-type]
    assert captured.value.__cause__ is None
    assert "private" not in str(captured.value)


def test_repository_helpers_fail_closed_for_non_utc_and_malformed_database_rows() -> None:
    assert repository_module._canonical_utc(datetime(2026, 7, 20, 1, 0)) is None
    assert (
        repository_module._canonical_utc(
            datetime(2026, 7, 20, 9, 0, tzinfo=timezone(timedelta(hours=8)))
        )
        is None
    )
    assert (
        repository_module._canonical_utc(datetime(2026, 7, 20, 1, 0, tzinfo=BrokenTimezone()))
        is None
    )
    with pytest.raises(ActionRiskAuthorizationRejected) as captured:
        repository_module._record({})  # type: ignore[arg-type]
    assert captured.value.__cause__ is None


def test_exact_replay_intent_compares_every_strong_identity() -> None:
    existing = authorization()

    def matches(
        *,
        action_id: ActionId = existing.action_id,
        target_id: TargetId = existing.target_id,
        execution_attempt_id: ExecutionAttemptId = existing.execution_attempt_id,
        task_id: TaskId = existing.task_id,
        installation_id: InstallationId = existing.installation_id,
        action: DouyinSearchExposureAction = existing.action,
    ) -> bool:
        return repository_module._same_intent(
            existing,
            action_id=action_id,
            target_id=target_id,
            execution_attempt_id=execution_attempt_id,
            task_id=task_id,
            installation_id=installation_id,
            action=action,
        )

    assert matches()
    assert not matches(action_id=ActionId.new())
    assert not matches(target_id=TargetId.new())
    assert not matches(execution_attempt_id=ExecutionAttemptId.new())
    assert not matches(task_id=TaskId.new())
    assert not matches(installation_id=InstallationId.new())
    assert not matches(action=DouyinSearchExposureAction.BROWSE)


def test_repository_rejects_an_invalid_database_without_leaking_details() -> None:
    with pytest.raises(ActionRiskAuthorizationUnavailable) as captured:
        SqlAlchemyActionRiskAuthorizationRepository(object())  # type: ignore[arg-type]
    assert captured.value.__cause__ is None
    assert "object" not in str(captured.value)
