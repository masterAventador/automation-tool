from __future__ import annotations

from dataclasses import MISSING, FrozenInstanceError, fields, replace
from datetime import timedelta
from typing import Any, cast

import pytest

from automation_tool.control_plane.domain import (
    ACTION_RISK_POLICY_VERSION,
    MAX_ACTION_RISK_LIMIT,
    MAX_TASK_INTERVAL_SECONDS,
    MAX_TASK_TARGET_LIMIT,
    ActionRiskPlatform,
    ActionRiskPolicy,
    ActionRiskScope,
    DouyinSearchExposureAction,
    InstallationId,
    InvalidActionRiskPolicy,
)

INSTALLATION_ID = InstallationId.parse("123e4567-e89b-42d3-a456-426614174003")


def scope(
    *,
    installation_id: InstallationId = INSTALLATION_ID,
    action: DouyinSearchExposureAction = DouyinSearchExposureAction.COMMENT,
) -> ActionRiskScope:
    return ActionRiskScope(
        installation_id=installation_id,
        platform=ActionRiskPlatform.DOUYIN,
        action=action,
    )


def policy(**changes: Any) -> ActionRiskPolicy:
    values: dict[str, object] = {
        "scope": scope(),
        "minimum_interval": timedelta(seconds=30),
        "task_action_limit": 20,
        "daily_action_limit": 50,
        "consecutive_failure_threshold": 3,
    }
    values.update(changes)
    return ActionRiskPolicy(**values)  # type: ignore[arg-type]


def test_policy_is_explicit_immutable_and_scoped_by_installation_platform_action() -> None:
    value = policy()

    assert value.scope.installation_id == INSTALLATION_ID
    assert value.scope.platform is ActionRiskPlatform.DOUYIN
    assert value.scope.action is DouyinSearchExposureAction.COMMENT
    assert value.minimum_interval == timedelta(seconds=30)
    assert value.minimum_interval_seconds == 30
    assert value.task_action_limit == 20
    assert value.daily_action_limit == 50
    assert value.consecutive_failure_threshold == 3
    assert value.version == ACTION_RISK_POLICY_VERSION
    assert {field.name for field in fields(ActionRiskPolicy)} == {
        "scope",
        "minimum_interval",
        "task_action_limit",
        "daily_action_limit",
        "consecutive_failure_threshold",
        "version",
    }
    with pytest.raises(FrozenInstanceError):
        value.daily_action_limit = 51  # type: ignore[misc]


def test_scope_distinguishes_installations_and_actions_without_free_form_values() -> None:
    another_installation = InstallationId.parse("223e4567-e89b-42d3-a456-426614174003")

    assert scope() != scope(installation_id=another_installation)
    assert scope() != scope(action=DouyinSearchExposureAction.DIRECT_MESSAGE)
    assert (
        len(
            {
                scope(action=DouyinSearchExposureAction.BROWSE),
                scope(action=DouyinSearchExposureAction.COMMENT),
                scope(action=DouyinSearchExposureAction.DIRECT_MESSAGE),
            }
        )
        == 3
    )


def test_policy_has_no_uncalibrated_operational_defaults() -> None:
    required = {
        "scope",
        "minimum_interval",
        "task_action_limit",
        "daily_action_limit",
        "consecutive_failure_threshold",
    }

    assert {
        field.name
        for field in fields(ActionRiskPolicy)
        if field.default is MISSING and field.default_factory is MISSING
    } >= required
    with pytest.raises(TypeError):
        ActionRiskPolicy(scope=scope())  # type: ignore[call-arg]


@pytest.mark.parametrize("seconds", (1, MAX_TASK_INTERVAL_SECONDS))
def test_minimum_interval_accepts_positive_whole_seconds_within_task_boundary(
    seconds: int,
) -> None:
    value = policy(minimum_interval=timedelta(seconds=seconds))

    assert value.minimum_interval_seconds == seconds


@pytest.mark.parametrize(
    "minimum_interval",
    (
        timedelta(0),
        timedelta(microseconds=1),
        timedelta(seconds=-1),
        timedelta(seconds=MAX_TASK_INTERVAL_SECONDS + 1),
        30,
        True,
        "30",
    ),
)
def test_minimum_interval_rejects_zero_fractional_out_of_range_or_wrong_types(
    minimum_interval: object,
) -> None:
    with pytest.raises(InvalidActionRiskPolicy, match="Action risk policy is invalid"):
        policy(minimum_interval=minimum_interval)


@pytest.mark.parametrize("task_action_limit", (1, MAX_TASK_TARGET_LIMIT))
def test_task_action_limit_reuses_the_task_target_hard_boundary(task_action_limit: int) -> None:
    assert policy(task_action_limit=task_action_limit).task_action_limit == task_action_limit


@pytest.mark.parametrize("task_action_limit", (0, -1, MAX_TASK_TARGET_LIMIT + 1, True, 1.0, "1"))
def test_task_action_limit_rejects_non_integer_or_out_of_range_values(
    task_action_limit: object,
) -> None:
    with pytest.raises(InvalidActionRiskPolicy):
        policy(task_action_limit=task_action_limit)


@pytest.mark.parametrize("field", ("daily_action_limit", "consecutive_failure_threshold"))
@pytest.mark.parametrize("value", (1, MAX_ACTION_RISK_LIMIT))
def test_daily_and_failure_limits_accept_positive_cross_runtime_integers(
    field: str,
    value: int,
) -> None:
    assert getattr(policy(**{field: value}), field) == value


@pytest.mark.parametrize("field", ("daily_action_limit", "consecutive_failure_threshold"))
@pytest.mark.parametrize("value", (0, -1, MAX_ACTION_RISK_LIMIT + 1, True, 1.0, "1"))
def test_daily_and_failure_limits_reject_non_integer_or_out_of_range_values(
    field: str,
    value: object,
) -> None:
    with pytest.raises(InvalidActionRiskPolicy):
        policy(**{field: value})


def test_task_daily_and_failure_thresholds_are_independent_hard_limits() -> None:
    value = policy(
        task_action_limit=100,
        daily_action_limit=1,
        consecutive_failure_threshold=101,
    )

    assert value.task_action_limit == 100
    assert value.daily_action_limit == 1
    assert value.consecutive_failure_threshold == 101


@pytest.mark.parametrize(
    ("field", "invalid"),
    (
        ("scope", object()),
        ("installation_id", "123e4567-e89b-42d3-a456-426614174003"),
        ("platform", "douyin"),
        ("action", "comment"),
    ),
)
def test_scope_and_policy_reject_forged_or_free_form_identifiers(
    field: str,
    invalid: object,
) -> None:
    with pytest.raises(InvalidActionRiskPolicy):
        if field == "scope":
            policy(scope=invalid)
        else:
            ActionRiskScope(
                installation_id=cast(InstallationId, invalid)
                if field == "installation_id"
                else INSTALLATION_ID,
                platform=cast(ActionRiskPlatform, invalid)
                if field == "platform"
                else ActionRiskPlatform.DOUYIN,
                action=cast(DouyinSearchExposureAction, invalid)
                if field == "action"
                else DouyinSearchExposureAction.COMMENT,
            )


def test_version_is_exact_and_forged_replacement_fails_closed() -> None:
    value = policy()

    with pytest.raises(InvalidActionRiskPolicy):
        policy(version="action-risk-policy.v2")
    with pytest.raises(InvalidActionRiskPolicy):
        replace(value, scope=cast(ActionRiskScope, object()))


def test_repr_and_errors_do_not_expose_installation_or_rejected_values() -> None:
    private_value = "private-policy-value"
    value = policy()

    assert str(INSTALLATION_ID) not in repr(value.scope)
    assert str(INSTALLATION_ID) not in repr(value)
    with pytest.raises(InvalidActionRiskPolicy) as captured:
        policy(version=private_value)
    assert str(captured.value) == "Action risk policy is invalid"
    assert private_value not in str(captured.value)
