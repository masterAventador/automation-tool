from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime, timedelta, timezone
from typing import cast

import pytest

from automation_tool.control_plane.domain import (
    MAX_DEMO_BOOTSTRAP_LIFETIME,
    BootstrapAuthorizationDenied,
    BootstrapDenialReason,
    BootstrapPurpose,
    DemoBootstrapGrant,
    DemoEnvironmentId,
    InvalidDemoBootstrap,
    InvalidDemoEnvironmentId,
)

ENVIRONMENT = DemoEnvironmentId.parse("customer-a-demo-202607")
OTHER_ENVIRONMENT = DemoEnvironmentId.parse("customer-b-demo-202607")
NOT_BEFORE = datetime(2026, 7, 18, 1, 0, tzinfo=UTC)
EXPIRES_AT = NOT_BEFORE + timedelta(hours=2)


def make_grant() -> DemoBootstrapGrant:
    return DemoBootstrapGrant(
        environment_id=ENVIRONMENT,
        not_before=NOT_BEFORE,
        expires_at=EXPIRES_AT,
    )


@pytest.mark.parametrize(
    "valid_value",
    (
        "demo",
        "customer-a",
        "customer-a-demo-202607",
        "a" * 64,
    ),
)
def test_demo_environment_ids_accept_only_canonical_bounded_slugs(valid_value: str) -> None:
    environment_id = DemoEnvironmentId.parse(valid_value)

    assert str(environment_id) == valid_value
    assert DemoEnvironmentId.parse(environment_id) is environment_id


@pytest.mark.parametrize(
    "invalid_value",
    (
        "",
        "Customer-A",
        "customer_a",
        "customer/a",
        "-customer-a",
        "customer-a-",
        "a" * 65,
        # A trailing newline. Under `fullmatch` a trailing `$` rejects this
        # too, so this case does not pin the anchor -- it pins the behaviour
        # against `$` and `match` regressing together. The anchor itself is
        # pinned by `test_the_guard_does_not_depend_on_the_calling_verb`.
        "customer-a\n",
        None,
        1,
    ),
)
def test_demo_environment_ids_reject_noncanonical_or_unbounded_values(
    invalid_value: object,
) -> None:
    with pytest.raises(InvalidDemoEnvironmentId) as captured:
        DemoEnvironmentId.parse(invalid_value)

    assert str(captured.value) == "Invalid demo environment ID"


def test_demo_environment_direct_construction_cannot_bypass_type_validation() -> None:
    with pytest.raises(InvalidDemoEnvironmentId):
        DemoEnvironmentId(cast(str, 1))


def test_bootstrap_grant_has_one_fixed_registration_purpose() -> None:
    grant = make_grant()

    assert grant.purpose is BootstrapPurpose.REGISTER_INSTALLATION
    assert {field.name for field in fields(grant)} == {
        "environment_id",
        "not_before",
        "expires_at",
        "purpose",
    }


@pytest.mark.parametrize(
    "authorized_at",
    (
        NOT_BEFORE,
        NOT_BEFORE + timedelta(minutes=1),
        EXPIRES_AT - timedelta(microseconds=1),
    ),
)
def test_registration_is_authorized_inside_the_exact_time_and_environment_scope(
    authorized_at: datetime,
) -> None:
    grant = make_grant()

    grant.authorize(
        purpose=BootstrapPurpose.REGISTER_INSTALLATION,
        environment_id=ENVIRONMENT,
        at=authorized_at,
    )

    assert grant.purpose is BootstrapPurpose.REGISTER_INSTALLATION


@pytest.mark.parametrize(
    ("purpose", "reason"),
    (
        ("task.create", BootstrapDenialReason.PURPOSE_MISMATCH),
        ("installation.register", BootstrapDenialReason.PURPOSE_MISMATCH),
        (None, BootstrapDenialReason.PURPOSE_MISMATCH),
    ),
)
def test_bootstrap_cannot_authorize_business_or_untyped_api_operations(
    purpose: object,
    reason: BootstrapDenialReason,
) -> None:
    grant = make_grant()

    with pytest.raises(BootstrapAuthorizationDenied) as captured:
        grant.authorize(purpose=purpose, environment_id=ENVIRONMENT, at=NOT_BEFORE)

    assert captured.value.reason is reason
    assert str(captured.value) == "Demo bootstrap authorization denied"


def test_bootstrap_rejects_a_different_demo_environment() -> None:
    grant = make_grant()

    with pytest.raises(BootstrapAuthorizationDenied) as captured:
        grant.authorize(
            purpose=BootstrapPurpose.REGISTER_INSTALLATION,
            environment_id=OTHER_ENVIRONMENT,
            at=NOT_BEFORE,
        )

    assert captured.value.reason is BootstrapDenialReason.ENVIRONMENT_MISMATCH


@pytest.mark.parametrize(
    ("attempted_at", "reason"),
    (
        (NOT_BEFORE - timedelta(microseconds=1), BootstrapDenialReason.NOT_YET_VALID),
        (EXPIRES_AT, BootstrapDenialReason.EXPIRED),
        (EXPIRES_AT + timedelta(days=1), BootstrapDenialReason.EXPIRED),
    ),
)
def test_bootstrap_rejects_calls_outside_its_half_open_time_window(
    attempted_at: datetime,
    reason: BootstrapDenialReason,
) -> None:
    grant = make_grant()

    with pytest.raises(BootstrapAuthorizationDenied) as captured:
        grant.authorize(
            purpose=BootstrapPurpose.REGISTER_INSTALLATION,
            environment_id=ENVIRONMENT,
            at=attempted_at,
        )

    assert captured.value.reason is reason


@pytest.mark.parametrize(
    ("not_before", "expires_at"),
    (
        (NOT_BEFORE.replace(tzinfo=None), EXPIRES_AT),
        (NOT_BEFORE, EXPIRES_AT.replace(tzinfo=None)),
        (NOT_BEFORE, NOT_BEFORE),
        (NOT_BEFORE, NOT_BEFORE - timedelta(seconds=1)),
        (NOT_BEFORE, NOT_BEFORE + MAX_DEMO_BOOTSTRAP_LIFETIME + timedelta(microseconds=1)),
    ),
)
def test_bootstrap_rejects_naive_reversed_or_excessive_validity_windows(
    not_before: datetime,
    expires_at: datetime,
) -> None:
    with pytest.raises(InvalidDemoBootstrap):
        DemoBootstrapGrant(
            environment_id=ENVIRONMENT,
            not_before=not_before,
            expires_at=expires_at,
        )


def test_bootstrap_rejects_an_untyped_environment_during_direct_construction() -> None:
    with pytest.raises(InvalidDemoBootstrap):
        DemoBootstrapGrant(
            environment_id=cast(DemoEnvironmentId, "customer-a-demo-202607"),
            not_before=NOT_BEFORE,
            expires_at=EXPIRES_AT,
        )


def test_bootstrap_accepts_and_normalizes_the_maximum_window() -> None:
    offset = timezone(timedelta(hours=8))
    grant = DemoBootstrapGrant(
        environment_id=ENVIRONMENT,
        not_before=NOT_BEFORE.astimezone(offset),
        expires_at=(NOT_BEFORE + MAX_DEMO_BOOTSTRAP_LIFETIME).astimezone(offset),
    )

    assert grant.not_before.tzinfo is UTC
    assert grant.not_before == NOT_BEFORE
    assert grant.expires_at - grant.not_before == MAX_DEMO_BOOTSTRAP_LIFETIME


def test_bootstrap_rejects_untyped_environment_and_naive_evaluation_time() -> None:
    grant = make_grant()

    with pytest.raises(BootstrapAuthorizationDenied) as wrong_environment:
        grant.authorize(
            purpose=BootstrapPurpose.REGISTER_INSTALLATION,
            environment_id=str(ENVIRONMENT),
            at=NOT_BEFORE,
        )
    with pytest.raises(InvalidDemoBootstrap):
        grant.authorize(
            purpose=BootstrapPurpose.REGISTER_INSTALLATION,
            environment_id=ENVIRONMENT,
            at=NOT_BEFORE.replace(tzinfo=None),
        )

    assert wrong_environment.value.reason is BootstrapDenialReason.ENVIRONMENT_MISMATCH


def test_bootstrap_grants_are_immutable() -> None:
    grant = make_grant()
    purpose_attribute = "purpose"

    with pytest.raises(FrozenInstanceError):
        setattr(grant, purpose_attribute, "task.create")

    assert grant.purpose is BootstrapPurpose.REGISTER_INSTALLATION


def test_the_guard_does_not_depend_on_the_calling_verb() -> None:
    """`\\Z` is why, and this is the only shape that pins it.

    Under `fullmatch` a trailing `$` behaves identically, so every
    behavioural case in this file passes either way -- reverting the
    anchor is invisible to them. Calling `match` instead states the
    property the anchor actually buys: the guard holds even if the
    verb is ever weakened.
    """
    from automation_tool.control_plane.domain.demo_bootstrap import (
        _ENVIRONMENT_PATTERN,
    )

    assert _ENVIRONMENT_PATTERN.match("customer-a\n") is None
