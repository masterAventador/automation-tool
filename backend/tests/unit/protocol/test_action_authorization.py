from __future__ import annotations

import base64
import json
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from typing import Any

import pytest

from automation_tool.protocol import (
    ACTION_AUTHORIZATION_MAX_LIFETIME,
    ACTION_AUTHORIZATION_VERSION,
    ActionAuthorizationClaims,
    ActionAuthorizationRejected,
    DouyinSearchExposureAction,
    IdempotencyKey,
    ProtocolActionId,
    ProtocolExecutionAttemptId,
    ProtocolExecutorId,
    ProtocolInstallationId,
    ProtocolTargetId,
    ProtocolTaskId,
    action_authorization_idempotency_key,
    action_authorization_signing_input,
    encode_action_authorization_token,
    parse_action_authorization_token,
)
from automation_tool.protocol import action_authorization as authorization_module

NOW = datetime(2026, 7, 20, 2, 0, tzinfo=UTC)
ACTION_ID = ProtocolActionId("123e4567-e89b-42d3-a456-426614174001")
TARGET_ID = ProtocolTargetId("123e4567-e89b-42d3-a456-426614174002")
ATTEMPT_ID = ProtocolExecutionAttemptId("123e4567-e89b-42d3-a456-426614174003")
TASK_ID = ProtocolTaskId("123e4567-e89b-42d3-a456-426614174004")
INSTALLATION_ID = ProtocolInstallationId("123e4567-e89b-42d3-a456-426614174005")
EXECUTOR_ID = ProtocolExecutorId("123e4567-e89b-42d3-a456-426614174006")


class BrokenTimezone(tzinfo):
    def utcoffset(self, value: datetime | None) -> timedelta | None:
        raise RuntimeError("private timezone failure")

    def dst(self, value: datetime | None) -> timedelta | None:
        return None

    def tzname(self, value: datetime | None) -> str | None:
        return None


def claims(**changes: Any) -> ActionAuthorizationClaims:
    values: dict[str, object] = {
        "version": ACTION_AUTHORIZATION_VERSION,
        "action_id": ACTION_ID,
        "target_id": TARGET_ID,
        "execution_attempt_id": ATTEMPT_ID,
        "task_id": TASK_ID,
        "installation_id": INSTALLATION_ID,
        "executor_id": EXECUTOR_ID,
        "platform": "douyin",
        "action": DouyinSearchExposureAction.COMMENT,
        "idempotency_key": action_authorization_idempotency_key(ACTION_ID),
        "authorized_at": NOW,
        "deadline_at": NOW + timedelta(seconds=60),
    }
    values.update(changes)
    return ActionAuthorizationClaims(**values)  # type: ignore[arg-type]


def test_claims_are_exact_immutable_redacted_and_have_one_stable_idempotency_key() -> None:
    value = claims()

    assert value.idempotency_key == IdempotencyKey(f"action:{ACTION_ID}")
    assert value.authorized_at is NOW
    assert "123e4567" not in repr(value)
    assert "comment" in repr(value)
    with pytest.raises(FrozenInstanceError):
        value.action = DouyinSearchExposureAction.BROWSE  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "invalid"),
    (
        ("version", "latest"),
        ("action_id", TARGET_ID),
        ("target_id", ACTION_ID),
        ("execution_attempt_id", TASK_ID),
        ("task_id", ATTEMPT_ID),
        ("installation_id", EXECUTOR_ID),
        ("executor_id", INSTALLATION_ID),
        ("platform", "private"),
        ("action", "comment"),
        ("idempotency_key", IdempotencyKey("action:another")),
        ("authorized_at", datetime(2026, 7, 20, 2, 0)),
        (
            "authorized_at",
            datetime(2026, 7, 20, 10, 0, tzinfo=timezone(timedelta(hours=8))),
        ),
        ("deadline_at", NOW),
        ("deadline_at", NOW + ACTION_AUTHORIZATION_MAX_LIFETIME + timedelta(seconds=1)),
    ),
)
def test_claims_reject_type_confusion_scope_changes_and_unsafe_time(
    field: str,
    invalid: Any,
) -> None:
    with pytest.raises(ActionAuthorizationRejected) as captured:
        replace(claims(), **{field: invalid})
    assert captured.value.__cause__ is None
    assert "123e4567" not in str(captured.value)


def test_token_encoding_is_canonical_bounded_and_round_trips_exact_claims() -> None:
    value = claims()
    signature = bytes(range(64))

    token = encode_action_authorization_token(value, signature)
    parsed = parse_action_authorization_token(token)

    assert parsed.claims == value
    assert parsed.signature == signature
    assert parsed.signing_input == action_authorization_signing_input(value)
    assert parsed.fingerprint == parse_action_authorization_token(token).fingerprint
    assert len(parsed.fingerprint) == 32
    assert token.startswith("ataa1.")
    assert "123e4567" not in repr(parsed)


@pytest.mark.parametrize(
    "token",
    (
        "",
        "ataa1.only-two-parts",
        "ataa2.e30.invalid",
        "ataa1.***.***",
        "ataa1.A.A",
        "ataa1.AB.AA",
        "ataa1.e30.eA",
        "ataa1." + ("A" * 4096) + ".AA",
        "ataa1.载荷.签名",
    ),
)
def test_malformed_tokens_fail_closed_without_reflecting_input(token: str) -> None:
    with pytest.raises(ActionAuthorizationRejected) as captured:
        parse_action_authorization_token(token)
    assert captured.value.__cause__ is None
    if token:
        assert token not in repr(captured.value)


def test_encoder_rejects_non_claims_and_non_ed25519_signatures() -> None:
    for value, signature in (
        (object(), bytes(64)),
        (claims(), b""),
        (claims(), bytes(63)),
        (claims(), bytes(65)),
        (claims(), "private"),
    ):
        with pytest.raises(ActionAuthorizationRejected):
            encode_action_authorization_token(value, signature)  # type: ignore[arg-type]


def test_protocol_helpers_reject_broken_time_wrong_id_and_noncanonical_payloads() -> None:
    assert (
        authorization_module._canonical_utc(datetime(2026, 7, 20, 2, 0, tzinfo=BrokenTimezone()))
        is None
    )
    for timestamp in (1, "private", "2026-07-20T02:00:00.1Z"):
        with pytest.raises(ActionAuthorizationRejected):
            authorization_module._parse_timestamp(timestamp)
    with pytest.raises(ActionAuthorizationRejected):
        action_authorization_idempotency_key(TARGET_ID)  # type: ignore[arg-type]
    with pytest.raises(ActionAuthorizationRejected):
        authorization_module._document(object())  # type: ignore[arg-type]
    with pytest.raises(ActionAuthorizationRejected):
        action_authorization_signing_input(object())  # type: ignore[arg-type]
    with pytest.raises(ActionAuthorizationRejected):
        authorization_module._unique_object((("version", 1), ("version", 1)))

    valid = encode_action_authorization_token(claims(), bytes(64))
    _prefix, payload_segment, _signature = valid.split(".")
    payload = base64.urlsafe_b64decode(payload_segment + ("=" * (-len(payload_segment) % 4)))
    document = json.loads(payload)
    malformed_payloads = (
        b"[]",
        b"{}",
        b'{"version":1,"version":1}',
        b"\xff",
        b" " + payload,
        json.dumps(
            {**document, "authorized_at": 1},
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii"),
    )
    signature_segment = base64.urlsafe_b64encode(bytes(64)).rstrip(b"=").decode("ascii")
    for malformed in malformed_payloads:
        malformed_segment = base64.urlsafe_b64encode(malformed).rstrip(b"=").decode("ascii")
        with pytest.raises(ActionAuthorizationRejected):
            parse_action_authorization_token(f"ataa1.{malformed_segment}.{signature_segment}")
