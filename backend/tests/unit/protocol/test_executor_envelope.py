from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from pydantic import ValidationError

from automation_tool.protocol.executor_envelope import (
    EXECUTOR_PROTOCOL_VERSION,
    CorrelationId,
    ExecutorLifecycleEnvelope,
    ExecutorMessage,
    ExecutorProtocolError,
    IdempotencyKey,
    MessageId,
    PlatformSessionHealthEnvelope,
    TaskCommandEnvelope,
    TaskEventEnvelope,
    parse_executor_message,
)

MESSAGE_ID = "123e4567-e89b-42d3-a456-426614174001"
CORRELATION_ID = "123e4567-e89b-42d3-a456-426614174002"
INSTALLATION_ID = "123e4567-e89b-42d3-a456-426614174003"
EXECUTOR_ID = "123e4567-e89b-42d3-a456-426614174004"
TASK_ID = "123e4567-e89b-42d3-a456-426614174005"
ATTEMPT_ID = "123e4567-e89b-42d3-a456-426614174006"
SENT_AT = datetime(2026, 7, 18, 3, 0, tzinfo=UTC)
DEADLINE_AT = SENT_AT + timedelta(minutes=5)


def lifecycle_message(**changes: object) -> dict[str, object]:
    message: dict[str, object] = {
        "protocol_version": "1.0",
        "message_id": MESSAGE_ID,
        "message_type": "executor.heartbeat",
        "sent_at": SENT_AT.isoformat().replace("+00:00", "Z"),
        "deadline_at": DEADLINE_AT.isoformat().replace("+00:00", "Z"),
        "installation_id": INSTALLATION_ID,
        "executor_id": EXECUTOR_ID,
        "correlation_id": CORRELATION_ID,
        "idempotency_key": "executor:heartbeat:00000001",
        "sequence": 1,
        "payload": {"status": "healthy"},
    }
    message.update(changes)
    return message


def task_message(**changes: object) -> dict[str, object]:
    message = lifecycle_message(
        message_type="task.offer",
        task_id=TASK_ID,
        execution_attempt_id=ATTEMPT_ID,
        idempotency_key="task:offer:attempt:1",
        payload={"task_kind": "douyin.search_leads"},
    )
    message.update(changes)
    return message


def platform_session_health_message(**changes: object) -> dict[str, object]:
    message = lifecycle_message(
        message_type="platform.session_health",
        idempotency_key="platform:douyin:session:7:healthy",
        payload={
            "platform": "douyin",
            "state": "healthy",
            "session_revision": 7,
            "observed_at": "2026-07-18T03:00:00Z",
        },
    )
    message.update(changes)
    return message


def validate(message: dict[str, object]) -> ExecutorMessage:
    return ExecutorMessage.model_validate(message)


def test_discriminated_union_selects_lifecycle_platform_command_and_event_variants() -> None:
    lifecycle = validate(lifecycle_message()).root
    platform_health = validate(platform_session_health_message()).root
    command = validate(task_message()).root
    event = validate(
        task_message(
            message_type="step.progress",
            idempotency_key="task:event:sequence:2",
            sequence=2,
            payload={"step_code": "discover_targets", "progress_percent": 25},
        )
    ).root

    assert isinstance(lifecycle, ExecutorLifecycleEnvelope)
    assert isinstance(platform_health, PlatformSessionHealthEnvelope)
    assert isinstance(command, TaskCommandEnvelope)
    assert isinstance(event, TaskEventEnvelope)
    assert lifecycle.protocol_version == EXECUTOR_PROTOCOL_VERSION
    assert type(lifecycle.message_id) is MessageId
    assert type(lifecycle.correlation_id) is CorrelationId
    assert not hasattr(platform_health, "task_id")
    assert (
        ExecutorMessage.model_validate_json(ExecutorMessage(root=event).model_dump_json()).root
        == event
    )


def test_platform_session_health_is_executor_scoped_and_rejects_sensitive_payloads() -> None:
    parsed = validate(platform_session_health_message()).root

    assert isinstance(parsed, PlatformSessionHealthEnvelope)
    assert parsed.message_type == "platform.session_health"
    assert parsed.payload.model_dump(mode="json") == {
        "platform": "douyin",
        "state": "healthy",
        "session_revision": 7,
        "observed_at": "2026-07-18T03:00:00Z",
    }
    for changes in (
        {"task_id": TASK_ID, "execution_attempt_id": ATTEMPT_ID},
        {"payload": {"platform": "douyin", "cookie": "private-cookie-value"}},
        {"payload": {"platform": "douyin", "profile_path": "/private/profile"}},
        {"payload": {"platform": "douyin", "qr_image": "fixture"}},
        {"payload": {"platform": "douyin", "captcha_code": "fixture"}},
    ):
        with pytest.raises(ValidationError):
            validate(platform_session_health_message(**changes))


@pytest.mark.parametrize("invalid_version", (None, "", "1", "1.1", 1.0))
def test_protocol_version_is_explicit_and_exact(invalid_version: object) -> None:
    message = lifecycle_message()
    if invalid_version is None:
        message.pop("protocol_version")
    else:
        message["protocol_version"] = invalid_version

    with pytest.raises(ValidationError):
        validate(message)


@pytest.mark.parametrize(
    "invalid_id",
    (
        "",
        "123e4567-e89b-12d3-a456-426614174001",
        "123E4567-E89B-42D3-A456-426614174001",
        "123e4567e89b42d3a456426614174001",
        "00000000-0000-0000-0000-000000000000",
        1,
    ),
)
@pytest.mark.parametrize(
    "field",
    ("message_id", "correlation_id", "installation_id", "executor_id"),
)
def test_envelope_ids_require_canonical_lowercase_uuid_v4_strings(
    field: str, invalid_id: object
) -> None:
    with pytest.raises(ValidationError):
        validate(lifecycle_message(**{field: invalid_id}))


def test_protocol_identifier_value_types_validate_direct_construction() -> None:
    assert MessageId(MESSAGE_ID) == MESSAGE_ID
    with pytest.raises(ValueError, match="Invalid message ID"):
        MessageId(cast(str, 1))
    with pytest.raises(ValueError, match="Invalid message ID"):
        MessageId("not-a-uuid")
    with pytest.raises(ValueError, match="Invalid message ID"):
        MessageId("123e4567-e89b-12d3-a456-426614174001")
    with pytest.raises(ValueError, match="Invalid message ID"):
        MessageId(MESSAGE_ID.upper())


@pytest.mark.parametrize(
    "invalid_time",
    ("", "2026-07-18T03:00:00", "2026-07-18T03:00:00-00:00", 0, None),
)
@pytest.mark.parametrize("field", ("sent_at", "deadline_at"))
def test_timestamps_require_aware_rfc3339_values(field: str, invalid_time: object) -> None:
    with pytest.raises(ValidationError):
        validate(lifecycle_message(**{field: invalid_time}))


@pytest.mark.parametrize(
    "deadline",
    (
        SENT_AT,
        SENT_AT - timedelta(microseconds=1),
    ),
)
def test_deadline_must_be_strictly_later_than_sent_at(deadline: datetime) -> None:
    with pytest.raises(ValidationError, match="deadline_at must be later than sent_at"):
        validate(lifecycle_message(deadline_at=deadline.isoformat()))


def test_non_utc_offsets_are_rejected_instead_of_silently_normalized() -> None:
    with pytest.raises(ValidationError, match="timestamps must use UTC"):
        validate(lifecycle_message(sent_at="2026-07-18T11:00:00+08:00"))


def test_python_datetime_inputs_are_preserved_as_canonical_utc_values() -> None:
    message = validate(lifecycle_message(sent_at=SENT_AT, deadline_at=DEADLINE_AT)).root

    assert message.sent_at == SENT_AT
    assert message.deadline_at == DEADLINE_AT


@pytest.mark.parametrize(
    "invalid_key",
    (
        "",
        " leading",
        "has space",
        "contains?query",
        "x" * 129,
        1,
    ),
)
def test_idempotency_key_is_bounded_strict_and_canonical(invalid_key: object) -> None:
    with pytest.raises(ValidationError):
        validate(lifecycle_message(idempotency_key=invalid_key))


def test_idempotency_key_value_type_validates_direct_construction() -> None:
    assert IdempotencyKey("task:offer:1") == "task:offer:1"
    with pytest.raises(ValueError, match="Invalid Executor protocol idempotency key"):
        IdempotencyKey("")
    with pytest.raises(ValueError, match="Invalid Executor protocol idempotency key"):
        IdempotencyKey(cast(str, 1))


@pytest.mark.parametrize("invalid_sequence", (0, -1, 2**53, 2**63, 1.0, True, "1"))
def test_sequence_is_a_positive_strict_cross_language_safe_integer(
    invalid_sequence: object,
) -> None:
    with pytest.raises(ValidationError):
        validate(lifecycle_message(sequence=invalid_sequence))


def test_lifecycle_and_task_scopes_cannot_be_confused() -> None:
    with pytest.raises(ValidationError):
        validate(lifecycle_message(task_id=TASK_ID, execution_attempt_id=ATTEMPT_ID))

    task_without_attempt = task_message()
    task_without_attempt.pop("execution_attempt_id")
    with pytest.raises(ValidationError):
        validate(task_without_attempt)


def test_unknown_message_types_and_envelope_fields_fail_closed() -> None:
    with pytest.raises(ValidationError):
        validate(lifecycle_message(message_type="executor.future"))
    with pytest.raises(ValidationError):
        validate(lifecycle_message(private_extension="not allowed"))


@pytest.mark.parametrize(
    "payload",
    (
        {"cookie": "session=private"},
        {"nested": {"access_token": "private-token"}},
        {"sessionCookieValue": "private-cookie"},
        {"message": "Authorization: Bearer private-token"},
        {"message": "cookie=private-cookie"},
        {"message": "file:///private/customer.csv"},
        {"path": "/Users/private/customer.csv"},
        {"path": r"C:\\Users\\private\\customer.csv"},
        {"image": "data:image/png;base64,AAAA"},
        {"message": "data:application/octet-stream;base64,AAAA"},
        {"message": "unsafe\u202evalue"},
        {"message": "x" * 4097},
        {"not_finite": float("nan")},
        {"": 1},
        {"x" * 129: 1},
        {"bad\nkey": 1},
    ),
)
def test_payload_rejects_credentials_private_paths_inline_data_and_resource_abuse(
    payload: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        validate(task_message(payload=payload))


def test_payload_size_and_collection_counts_are_bounded() -> None:
    with pytest.raises(ValidationError):
        validate(task_message(payload={f"field_{index}": "x" * 4000 for index in range(5)}))
    with pytest.raises(ValidationError):
        validate(task_message(payload={f"field_{index}": index for index in range(65)}))
    with pytest.raises(ValidationError):
        validate(task_message(payload={"items": list(range(65))}))
    too_deep: dict[str, object] = {"value": 1}
    for _ in range(9):
        too_deep = {"nested": too_deep}
    with pytest.raises(ValidationError):
        validate(task_message(payload=too_deep))


def test_payload_accepts_bounded_arrays_finite_floats_and_safe_nulls() -> None:
    parsed = validate(task_message(payload={"items": [1, 0.5, True, None, "safe diagnostic"]})).root

    assert parsed.payload == {"items": [1, 0.5, True, None, "safe diagnostic"]}


def test_payload_rejects_text_that_cannot_be_encoded_as_utf8() -> None:
    with pytest.raises(ValidationError, match="payload must be bounded safe JSON"):
        validate(task_message(payload={"message": "\ud800"}))


def test_parser_accepts_only_bounded_json_objects_and_returns_fixed_errors() -> None:
    parsed = parse_executor_message(json.dumps(task_message()))
    parsed_from_bytes = parse_executor_message(json.dumps(lifecycle_message()).encode())

    assert isinstance(parsed, TaskCommandEnvelope)
    assert parsed.task_id == TASK_ID
    assert isinstance(parsed_from_bytes, ExecutorLifecycleEnvelope)

    private_value = "private-cookie-value"
    valid_json = json.dumps(lifecycle_message())
    duplicate_key_json = f'{valid_json[:-1]}, "sequence": 2}}'
    invalid_inputs: tuple[str | bytes, ...] = (
        json.dumps(task_message(payload={"cookie": private_value})),
        duplicate_key_json,
        "[]",
        "not-json",
        b"{\xff}",
        b" " * 32_769,
        " " * 32_769,
        cast(str | bytes, 1),
    )
    for invalid in invalid_inputs:
        with pytest.raises(ExecutorProtocolError) as captured:
            parse_executor_message(invalid)
        assert str(captured.value) == "Invalid Executor protocol message"
        assert private_value not in repr(captured.value)
        assert captured.value.__cause__ is None
        assert captured.value.__context__ is None


def test_schema_publishes_one_exact_discriminator_and_explicit_required_core_fields() -> None:
    schema = ExecutorMessage.model_json_schema()
    envelope_schema = schema["$defs"]["ExecutorEnvelope"]
    expected_message_types = {
        "action.accept",
        "action.execute",
        "action.reject",
        "executor.heartbeat",
        "executor.hello",
        "platform.session_health",
        "handoff.requested",
        "session.login_required",
        "step.completed",
        "step.failed",
        "step.progress",
        "step.started",
        "task.accept",
        "task.cancel",
        "task.cancelled",
        "task.completed",
        "task.control_ack",
        "task.discover",
        "task.discovery_batch",
        "task.discovery_completed",
        "task.emergency_stop",
        "task.failed",
        "task.offer",
        "task.outcome_uncertain",
        "task.partially_completed",
        "task.pause",
        "task.paused",
        "task.reject",
        "task.resume",
        "task.resumed",
        "task.started",
    }

    assert envelope_schema["discriminator"]["propertyName"] == "message_type"
    assert set(envelope_schema["discriminator"]["mapping"]) == expected_message_types
    for model_name in (
        "ExecutorLifecycleEnvelope",
        "PlatformSessionHealthEnvelope",
        "TaskActionCommandEnvelope",
        "TaskCommandEnvelope",
        "TaskCommandResultEnvelope",
        "TaskDiscoveryBatchEnvelope",
        "TaskDiscoveryCommandEnvelope",
        "TaskDiscoveryCompletedEnvelope",
        "TaskEventEnvelope",
    ):
        model_schema = schema["$defs"][model_name]
        assert model_schema["additionalProperties"] is False
        assert model_schema["properties"]["protocol_version"] == {
            "const": EXECUTOR_PROTOCOL_VERSION,
            "title": "Protocol Version",
            "type": "string",
        }
        assert {
            "protocol_version",
            "message_id",
            "message_type",
            "sent_at",
            "deadline_at",
            "installation_id",
            "executor_id",
            "correlation_id",
            "idempotency_key",
            "sequence",
            "payload",
        } <= set(model_schema["required"])
