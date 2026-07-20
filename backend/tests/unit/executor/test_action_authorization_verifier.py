from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone, tzinfo
from typing import cast

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from automation_tool.executor.action_authorization import (
    ActionAuthorizationExpectation,
    ActionAuthorizationVerificationClock,
    ActionAuthorizationVerificationRejected,
    Ed25519ActionAuthorizationVerifier,
)
from automation_tool.protocol import (
    ACTION_AUTHORIZATION_VERSION,
    ActionAuthorizationClaims,
    DouyinSearchExposureAction,
    ProtocolActionId,
    ProtocolExecutionAttemptId,
    ProtocolExecutorId,
    ProtocolInstallationId,
    ProtocolTargetId,
    ProtocolTaskId,
    action_authorization_idempotency_key,
    action_authorization_signing_input,
    encode_action_authorization_token,
)

NOW = datetime(2026, 7, 20, 2, 0, tzinfo=UTC)
PRIVATE_KEY = bytes(range(32))
ACTION_ID = ProtocolActionId("123e4567-e89b-42d3-a456-426614174001")
TARGET_ID = ProtocolTargetId("123e4567-e89b-42d3-a456-426614174002")
ATTEMPT_ID = ProtocolExecutionAttemptId("123e4567-e89b-42d3-a456-426614174003")
TASK_ID = ProtocolTaskId("123e4567-e89b-42d3-a456-426614174004")
INSTALLATION_ID = ProtocolInstallationId("123e4567-e89b-42d3-a456-426614174005")
EXECUTOR_ID = ProtocolExecutorId("123e4567-e89b-42d3-a456-426614174006")


class FixedClock:
    def __init__(self, now: object = NOW + timedelta(seconds=1)) -> None:
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


def claims() -> ActionAuthorizationClaims:
    return ActionAuthorizationClaims(
        version=ACTION_AUTHORIZATION_VERSION,
        action_id=ACTION_ID,
        target_id=TARGET_ID,
        execution_attempt_id=ATTEMPT_ID,
        task_id=TASK_ID,
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
        platform="douyin",
        action=DouyinSearchExposureAction.COMMENT,
        idempotency_key=action_authorization_idempotency_key(ACTION_ID),
        authorized_at=NOW,
        deadline_at=NOW + timedelta(seconds=60),
    )


def token(private_key: bytes = PRIVATE_KEY) -> str:
    value = claims()
    signature = Ed25519PrivateKey.from_private_bytes(private_key).sign(
        action_authorization_signing_input(value)
    )
    return encode_action_authorization_token(value, signature)


def expectation(**changes: object) -> ActionAuthorizationExpectation:
    values: dict[str, object] = {
        "action_id": ACTION_ID,
        "target_id": TARGET_ID,
        "execution_attempt_id": ATTEMPT_ID,
        "task_id": TASK_ID,
        "installation_id": INSTALLATION_ID,
        "executor_id": EXECUTOR_ID,
        "platform": "douyin",
        "action": DouyinSearchExposureAction.COMMENT,
        "idempotency_key": action_authorization_idempotency_key(ACTION_ID),
    }
    values.update(changes)
    return ActionAuthorizationExpectation(**values)  # type: ignore[arg-type]


def verifier(
    clock: ActionAuthorizationVerificationClock | None = None,
) -> Ed25519ActionAuthorizationVerifier:
    public_key = Ed25519PrivateKey.from_private_bytes(PRIVATE_KEY).public_key().public_bytes_raw()
    return Ed25519ActionAuthorizationVerifier(
        public_key=public_key,
        clock=clock or FixedClock(),
    )


def test_verifier_accepts_only_a_valid_unexpired_token_for_the_exact_intent() -> None:
    candidate = verifier()
    verified = candidate.verify(token=token(), expected=expectation())

    assert verified == claims()
    assert "123e4567" not in repr(verified)
    assert "123e4567" not in repr(expectation())
    assert "private" not in repr(candidate).lower()


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("action_id", ProtocolActionId("123e4567-e89b-42d3-a456-426614174011")),
        ("target_id", ProtocolTargetId("123e4567-e89b-42d3-a456-426614174012")),
        (
            "execution_attempt_id",
            ProtocolExecutionAttemptId("123e4567-e89b-42d3-a456-426614174013"),
        ),
        ("task_id", ProtocolTaskId("123e4567-e89b-42d3-a456-426614174014")),
        (
            "installation_id",
            ProtocolInstallationId("123e4567-e89b-42d3-a456-426614174015"),
        ),
        ("executor_id", ProtocolExecutorId("123e4567-e89b-42d3-a456-426614174016")),
        ("action", DouyinSearchExposureAction.BROWSE),
        ("idempotency_key", "action:another"),
    ),
)
def test_verifier_rejects_every_cross_scope_or_changed_intent(
    field: str,
    replacement: object,
) -> None:
    with pytest.raises(ActionAuthorizationVerificationRejected):
        verifier().verify(token=token(), expected=expectation(**{field: replacement}))


def test_verifier_rejects_tampering_wrong_signer_expiry_and_excess_clock_skew() -> None:
    valid = token()
    prefix, payload, signature = valid.split(".")
    cases = (
        (f"{prefix}.{payload[:-1]}A.{signature}", verifier()),
        (
            token(bytes(reversed(PRIVATE_KEY))),
            verifier(),
        ),
        (
            valid,
            verifier(FixedClock(NOW + timedelta(seconds=60))),
        ),
        (
            valid,
            verifier(FixedClock(NOW - timedelta(seconds=31))),
        ),
        (
            valid,
            verifier(FixedClock(datetime(2026, 7, 20, 10, 0, tzinfo=timezone(timedelta(hours=8))))),
        ),
    )
    for presented, candidate in cases:
        with pytest.raises(ActionAuthorizationVerificationRejected) as captured:
            candidate.verify(token=presented, expected=expectation())
        assert captured.value.__cause__ is None
        assert presented not in repr(captured.value)


def test_verifier_rejects_invalid_public_key_clock_token_and_expectation() -> None:
    for public_key in (b"", bytes(31), bytes(33), cast(bytes, "private")):
        with pytest.raises(ActionAuthorizationVerificationRejected):
            Ed25519ActionAuthorizationVerifier(public_key=public_key, clock=FixedClock())

    public_key = Ed25519PrivateKey.from_private_bytes(PRIVATE_KEY).public_key().public_bytes_raw()
    with pytest.raises(ActionAuthorizationVerificationRejected):
        Ed25519ActionAuthorizationVerifier(
            public_key=public_key,
            clock=object(),  # type: ignore[arg-type]
        )
    with pytest.raises(ActionAuthorizationVerificationRejected):
        verifier(FixedClock("private")).verify(token=token(), expected=expectation())
    with pytest.raises(ActionAuthorizationVerificationRejected):
        verifier(FixedClock(datetime(2026, 7, 20, 2, 0, tzinfo=BrokenTimezone()))).verify(
            token=token(), expected=expectation()
        )
    with pytest.raises(ActionAuthorizationVerificationRejected):
        verifier(ExplodingClock()).verify(token=token(), expected=expectation())
    with pytest.raises(ActionAuthorizationVerificationRejected):
        verifier().verify(token=object(), expected=expectation())  # type: ignore[arg-type]
    with pytest.raises(ActionAuthorizationVerificationRejected):
        verifier().verify(token=token(), expected=object())  # type: ignore[arg-type]
