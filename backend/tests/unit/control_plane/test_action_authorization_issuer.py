from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from typing import Any, cast

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from automation_tool.control_plane.application.action_risk_authorizations import (
    ActionRiskAuthorization,
)
from automation_tool.control_plane.domain import (
    ACTION_RISK_POLICY_VERSION,
    ActionId,
    ActionRiskPlatform,
    DouyinSearchExposureAction,
    ExecutionAttemptId,
    ExecutorId,
    InstallationId,
    TargetId,
    TaskId,
)
from automation_tool.control_plane.infrastructure.security import (
    action_authorizations as issuer_module,
)
from automation_tool.control_plane.infrastructure.security.action_authorizations import (
    ActionAuthorizationIssuanceRejected,
    Ed25519ActionAuthorizationIssuer,
)
from automation_tool.protocol import parse_action_authorization_token

NOW = datetime(2026, 7, 20, 2, 0, tzinfo=UTC)
PRIVATE_KEY = bytes(range(32))
EXECUTOR_ID = ExecutorId.parse("123e4567-e89b-42d3-a456-426614174006")
GOLDEN_TOKEN = (
    "ataa1.eyJhY3Rpb24iOiJjb21tZW50IiwiYWN0aW9uX2lkIjoiMTIzZTQ1NjctZTg5Yi00MmQzLWE0"
    "NTYtNDI2NjE0MTc0MDAxIiwiYXV0aG9yaXplZF9hdCI6IjIwMjYtMDctMjBUMDI6MDA6MDAuMDAwMDAw"
    "WiIsImRlYWRsaW5lX2F0IjoiMjAyNi0wNy0yMFQwMjowMTowMC4wMDAwMDBaIiwiZXhlY3V0aW9uX2F0"
    "dGVtcHRfaWQiOiIxMjNlNDU2Ny1lODliLTQyZDMtYTQ1Ni00MjY2MTQxNzQwMDMiLCJleGVjdXRvcl9p"
    "ZCI6IjEyM2U0NTY3LWU4OWItNDJkMy1hNDU2LTQyNjYxNDE3NDAwNiIsImlkZW1wb3RlbmN5X2tleSI6"
    "ImFjdGlvbjoxMjNlNDU2Ny1lODliLTQyZDMtYTQ1Ni00MjY2MTQxNzQwMDEiLCJpbnN0YWxsYXRpb25f"
    "aWQiOiIxMjNlNDU2Ny1lODliLTQyZDMtYTQ1Ni00MjY2MTQxNzQwMDUiLCJwbGF0Zm9ybSI6ImRvdXlp"
    "biIsInRhcmdldF9pZCI6IjEyM2U0NTY3LWU4OWItNDJkMy1hNDU2LTQyNjYxNDE3NDAwMiIsInRhc2tf"
    "aWQiOiIxMjNlNDU2Ny1lODliLTQyZDMtYTQ1Ni00MjY2MTQxNzQwMDQiLCJ2ZXJzaW9uIjoiYWN0aW9u"
    "LWF1dGhvcml6YXRpb24udjEifQ.WSlPEM3gduNL32k34bu-5AWSkevRKThwBqHV-nIgAp77QhSEnore"
    "RprXnI7oDzUBdjkN5w9XR5u5iCudGmnMAg"
)


class FixedClock:
    def __init__(self, now: object = NOW) -> None:
        self.value = now

    def now(self) -> datetime:
        return cast(datetime, self.value)


class ExplodingClock:
    def now(self) -> datetime:
        raise RuntimeError("private clock failure")


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
        "authorized_day": NOW.date(),
        "authorized_at": NOW,
        "created_at": NOW,
    }
    values.update(changes)
    return ActionRiskAuthorization(**values)  # type: ignore[arg-type]


def test_issuer_maps_one_database_fact_to_one_stable_signed_authorization() -> None:
    issuer = Ed25519ActionAuthorizationIssuer(
        private_key=PRIVATE_KEY,
        clock=FixedClock(),
        authorization_lifetime=timedelta(seconds=60),
    )

    first = issuer.issue(authorization=authorization(), executor_id=EXECUTOR_ID)
    replay = issuer.issue(authorization=authorization(), executor_id=EXECUTOR_ID)
    parsed = parse_action_authorization_token(first.token)

    assert replay == first
    assert first.token == GOLDEN_TOKEN
    assert parsed.claims == first.claims
    assert parsed.claims.deadline_at == NOW + timedelta(seconds=60)
    assert parsed.claims.idempotency_key == ("action:123e4567-e89b-42d3-a456-426614174001")
    Ed25519PrivateKey.from_private_bytes(PRIVATE_KEY).public_key().verify(
        parsed.signature,
        parsed.signing_input,
    )
    assert "123e4567" not in repr(first)
    assert "private" not in repr(issuer).lower()


@pytest.mark.parametrize(
    "changes",
    (
        {"private_key": b""},
        {"private_key": bytes(31)},
        {"private_key": bytes(33)},
        {"private_key": "private"},
        {"clock": object()},
        {"authorization_lifetime": timedelta(0)},
        {"authorization_lifetime": timedelta(microseconds=1)},
        {"authorization_lifetime": timedelta(minutes=6)},
    ),
)
def test_issuer_rejects_invalid_key_clock_and_lifetime(changes: dict[str, object]) -> None:
    values: dict[str, object] = {
        "private_key": PRIVATE_KEY,
        "clock": FixedClock(),
        "authorization_lifetime": timedelta(seconds=60),
    }
    values.update(changes)
    with pytest.raises(ActionAuthorizationIssuanceRejected):
        Ed25519ActionAuthorizationIssuer(**values)  # type: ignore[arg-type]


def test_issuer_rejects_invalid_fact_executor_clock_and_late_issuance() -> None:
    for value, executor_id, clock in (
        (object(), EXECUTOR_ID, FixedClock()),
        (authorization(), InstallationId.new(), FixedClock()),
        (authorization(), EXECUTOR_ID, FixedClock("private")),
        (
            authorization(),
            EXECUTOR_ID,
            FixedClock(datetime(2026, 7, 20, 10, 0, tzinfo=timezone(timedelta(hours=8)))),
        ),
        (
            authorization(),
            EXECUTOR_ID,
            FixedClock(datetime(2026, 7, 20, 2, 0, tzinfo=BrokenTimezone())),
        ),
        (authorization(), EXECUTOR_ID, ExplodingClock()),
        (authorization(), EXECUTOR_ID, FixedClock(NOW - timedelta(microseconds=1))),
        (authorization(), EXECUTOR_ID, FixedClock(NOW + timedelta(seconds=60))),
    ):
        issuer = Ed25519ActionAuthorizationIssuer(
            private_key=PRIVATE_KEY,
            clock=clock,
            authorization_lifetime=timedelta(seconds=60),
        )
        with pytest.raises(ActionAuthorizationIssuanceRejected) as captured:
            issuer.issue(authorization=value, executor_id=executor_id)  # type: ignore[arg-type]
        assert captured.value.__cause__ is None


def test_issuer_collapses_unexpected_signing_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(_claims: object) -> bytes:
        raise RuntimeError("private signing failure")

    monkeypatch.setattr(issuer_module, "action_authorization_signing_input", fail)
    with pytest.raises(ActionAuthorizationIssuanceRejected) as captured:
        Ed25519ActionAuthorizationIssuer(
            private_key=PRIVATE_KEY,
            clock=FixedClock(),
            authorization_lifetime=timedelta(seconds=60),
        ).issue(authorization=authorization(), executor_id=EXECUTOR_ID)
    assert captured.value.__cause__ is None
    assert "private" not in str(captured.value)


def test_issuer_rejects_a_fact_whose_creation_time_is_not_the_authorization_time() -> None:
    with pytest.raises(ActionAuthorizationIssuanceRejected):
        Ed25519ActionAuthorizationIssuer(
            private_key=PRIVATE_KEY,
            clock=FixedClock(),
            authorization_lifetime=timedelta(seconds=60),
        ).issue(
            authorization=replace(
                authorization(),
                created_at=NOW + timedelta(microseconds=1),
            ),
            executor_id=EXECUTOR_ID,
        )
