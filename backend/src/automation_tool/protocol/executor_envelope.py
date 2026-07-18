"""Strict v1 wire envelope shared by the Control Plane and Local Executor."""

from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime, timedelta
from typing import Annotated, ClassVar, Literal
from uuid import RFC_4122, UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    RootModel,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_core import CoreSchema, core_schema

from automation_tool.protocol.version import CURRENT_EXECUTOR_PROTOCOL

EXECUTOR_PROTOCOL_VERSION = CURRENT_EXECUTOR_PROTOCOL
MAX_EXECUTOR_MESSAGE_BYTES = 32 * 1024
MAX_EXECUTOR_PAYLOAD_BYTES = 16 * 1024
MAX_EXECUTOR_PAYLOAD_DEPTH = 8
MAX_EXECUTOR_COLLECTION_ITEMS = 64
MAX_EXECUTOR_STRING_LENGTH = 4096
MAX_EXECUTOR_SEQUENCE = 2**53 - 1

_UUID_V4_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
_IDEMPOTENCY_KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$"
_RFC3339_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$"
)
_CONTROL_OR_BIDI_PATTERN = re.compile(r"[\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]")
_PRIVATE_POSIX_PATH_PATTERN = re.compile(
    r"(?:^|[\s\"'=])/(?:users|home|root|tmp|var/folders)(?:/|$)",
    re.IGNORECASE,
)
_WINDOWS_ABSOLUTE_PATH_PATTERN = re.compile(r"(?:^|[\s\"'=])[a-z]:[\\/]", re.IGNORECASE)
_INLINE_DATA_URI_PATTERN = re.compile(r"\bdata:[a-z0-9.+-]+/[a-z0-9.+-]+[^,]*,", re.IGNORECASE)
_SENSITIVE_ASSIGNMENT_PATTERN = re.compile(
    r"(?:^|[^a-z0-9_])(?:access[_-]?token|api[_-]?key|authorization|cookie|credential|"
    r"password|private[_-]?key|refresh[_-]?token|secret|session[_-]?cookie|token)\s*[:=]",
    re.IGNORECASE,
)
_SENSITIVE_PAYLOAD_SEGMENTS = frozenset(
    {
        "cookie",
        "cookies",
        "credential",
        "credentials",
        "password",
        "secret",
        "secrets",
        "token",
        "tokens",
    }
)
_SENSITIVE_PAYLOAD_NAMES = frozenset(
    {
        "access_token",
        "api_key",
        "authorization",
        "captcha_code",
        "cookie",
        "cookies",
        "credential",
        "credentials",
        "file_path",
        "image",
        "image_data",
        "inline_image",
        "inline_screenshot",
        "local_path",
        "otp",
        "password",
        "private_key",
        "refresh_token",
        "screenshot",
        "secret",
        "secrets",
        "session_cookie",
        "token",
        "tokens",
        "verification_code",
    }
)


class _CanonicalUuidV4(str):
    """A runtime-distinct canonical UUIDv4 string for one protocol purpose."""

    _purpose: ClassVar[str] = "protocol"

    def __new__(cls, value: str) -> _CanonicalUuidV4:
        if type(value) is not str:
            raise ValueError(f"Invalid {cls._purpose} ID")
        try:
            parsed = UUID(value)
        except ValueError as error:
            raise ValueError(f"Invalid {cls._purpose} ID") from error
        if parsed.version != 4 or parsed.variant != RFC_4122 or value != str(parsed):
            raise ValueError(f"Invalid {cls._purpose} ID")
        return str.__new__(cls, value)

    @classmethod
    def __get_pydantic_core_schema__(
        cls,
        _source_type: object,
        _handler: object,
    ) -> CoreSchema:
        return core_schema.no_info_after_validator_function(
            cls,
            core_schema.str_schema(
                strict=True,
                min_length=36,
                max_length=36,
                pattern=_UUID_V4_PATTERN,
            ),
            serialization=core_schema.to_string_ser_schema(),
        )


class MessageId(_CanonicalUuidV4):
    """Unique identity of one wire message."""

    _purpose = "message"


class CorrelationId(_CanonicalUuidV4):
    """Identity shared by causally related messages."""

    _purpose = "correlation"


class ProtocolInstallationId(_CanonicalUuidV4):
    """Installation identity at the process protocol boundary."""

    _purpose = "installation"


class ProtocolExecutorId(_CanonicalUuidV4):
    """Executor identity at the process protocol boundary."""

    _purpose = "executor"


class ProtocolTaskId(_CanonicalUuidV4):
    """Task identity at the process protocol boundary."""

    _purpose = "task"


class ProtocolExecutionAttemptId(_CanonicalUuidV4):
    """Execution-attempt identity at the process protocol boundary."""

    _purpose = "execution attempt"


class IdempotencyKey(str):
    """Bounded canonical key used to deduplicate one message intent."""

    def __new__(cls, value: str) -> IdempotencyKey:
        if type(value) is not str or re.fullmatch(_IDEMPOTENCY_KEY_PATTERN, value) is None:
            raise ValueError("Invalid Executor protocol idempotency key")
        return str.__new__(cls, value)

    @classmethod
    def __get_pydantic_core_schema__(
        cls,
        _source_type: object,
        _handler: object,
    ) -> CoreSchema:
        return core_schema.no_info_after_validator_function(
            cls,
            core_schema.str_schema(
                strict=True,
                min_length=1,
                max_length=128,
                pattern=_IDEMPOTENCY_KEY_PATTERN,
            ),
            serialization=core_schema.to_string_ser_schema(),
        )


class ExecutorProtocolError(ValueError):
    """A fixed parser failure that never reflects the rejected wire value."""

    def __init__(self) -> None:
        super().__init__("Invalid Executor protocol message")


class _ProtocolModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _ExecutorEnvelopeBase(_ProtocolModel):
    protocol_version: Literal["1.0"]
    message_id: MessageId
    sent_at: AwareDatetime
    deadline_at: AwareDatetime
    installation_id: ProtocolInstallationId
    executor_id: ProtocolExecutorId
    correlation_id: CorrelationId
    idempotency_key: IdempotencyKey
    sequence: Annotated[int, Field(strict=True, ge=1, le=MAX_EXECUTOR_SEQUENCE)]
    payload: dict[str, JsonValue]

    @field_validator("sent_at", "deadline_at", mode="before")
    @classmethod
    def require_rfc3339_or_datetime(cls, value: object) -> object:
        if isinstance(value, datetime):
            return value
        if (
            type(value) is str
            and _RFC3339_PATTERN.fullmatch(value) is not None
            and not value.endswith("-00:00")
        ):
            return value
        raise ValueError("timestamps must use RFC3339")

    @field_validator("sent_at", "deadline_at")
    @classmethod
    def require_utc_timestamps(cls, value: datetime) -> datetime:
        if value.utcoffset() != timedelta(0):
            raise ValueError("timestamps must use UTC")
        return value.astimezone(UTC)

    @field_validator("payload")
    @classmethod
    def require_bounded_safe_payload(cls, payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
        _validate_payload_value(payload, depth=0)
        try:
            encoded = json.dumps(
                payload,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (TypeError, ValueError, UnicodeError) as error:
            raise ValueError("payload must be bounded safe JSON") from error
        if len(encoded) > MAX_EXECUTOR_PAYLOAD_BYTES:
            raise ValueError("payload exceeds the Executor protocol limit")
        return payload

    @model_validator(mode="after")
    def require_ordered_deadline(self) -> _ExecutorEnvelopeBase:
        if self.deadline_at <= self.sent_at:
            raise ValueError("deadline_at must be later than sent_at")
        return self


class ExecutorLifecycleEnvelope(_ExecutorEnvelopeBase):
    """Executor-scoped lifecycle traffic that has no task identity."""

    message_type: Literal["executor.hello", "executor.heartbeat"]


class _TaskEnvelopeBase(_ExecutorEnvelopeBase):
    task_id: ProtocolTaskId
    execution_attempt_id: ProtocolExecutionAttemptId


class TaskCommandEnvelope(_TaskEnvelopeBase):
    """Control Plane commands that target one execution attempt."""

    message_type: Literal[
        "task.offer",
        "task.pause",
        "task.resume",
        "task.cancel",
        "task.emergency_stop",
    ]


class TaskCommandResultEnvelope(_TaskEnvelopeBase):
    """Executor acknowledgements and decisions for a command."""

    message_type: Literal["task.accept", "task.reject", "task.control_ack"]


class TaskEventEnvelope(_TaskEnvelopeBase):
    """Executor facts emitted for one execution attempt."""

    message_type: Literal[
        "task.started",
        "step.started",
        "step.progress",
        "step.completed",
        "step.failed",
        "session.login_required",
        "handoff.requested",
        "task.paused",
        "task.resumed",
        "task.cancelled",
        "task.completed",
        "task.partially_completed",
        "task.failed",
        "task.outcome_uncertain",
    ]


type ExecutorEnvelope = Annotated[
    ExecutorLifecycleEnvelope | TaskCommandEnvelope | TaskCommandResultEnvelope | TaskEventEnvelope,
    Field(discriminator="message_type"),
]


class ExecutorMessage(RootModel[ExecutorEnvelope]):
    """Pydantic discriminated union for every supported v1 message type."""

    model_config = ConfigDict(frozen=True)


def _normalized_payload_name(value: str) -> str:
    camel_split = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", value)
    return re.sub(r"[.-]+", "_", camel_split).lower()


def _unsafe_payload_string(value: str) -> bool:
    folded = value.casefold()
    return (
        len(value) > MAX_EXECUTOR_STRING_LENGTH
        or _CONTROL_OR_BIDI_PATTERN.search(value) is not None
        or "bearer " in folded
        or "file://" in folded
        or _SENSITIVE_ASSIGNMENT_PATTERN.search(value) is not None
        or _INLINE_DATA_URI_PATTERN.search(value) is not None
        or _PRIVATE_POSIX_PATH_PATTERN.search(value) is not None
        or _WINDOWS_ABSOLUTE_PATH_PATTERN.search(value) is not None
    )


def _validate_payload_value(value: JsonValue, *, depth: int) -> None:
    if depth > MAX_EXECUTOR_PAYLOAD_DEPTH:
        raise ValueError("payload nesting exceeds the Executor protocol limit")
    if isinstance(value, dict):
        if len(value) > MAX_EXECUTOR_COLLECTION_ITEMS:
            raise ValueError("payload object exceeds the Executor protocol limit")
        for key, child in value.items():
            normalized_key = _normalized_payload_name(key)
            if (
                not key
                or len(key) > 128
                or _CONTROL_OR_BIDI_PATTERN.search(key) is not None
                or normalized_key in _SENSITIVE_PAYLOAD_NAMES
                or set(normalized_key.split("_")) & _SENSITIVE_PAYLOAD_SEGMENTS
            ):
                raise ValueError("payload contains a forbidden field")
            _validate_payload_value(child, depth=depth + 1)
        return
    if isinstance(value, list):
        if len(value) > MAX_EXECUTOR_COLLECTION_ITEMS:
            raise ValueError("payload array exceeds the Executor protocol limit")
        for child in value:
            _validate_payload_value(child, depth=depth + 1)
        return
    if isinstance(value, str) and _unsafe_payload_string(value):
        raise ValueError("payload contains unsafe text")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("payload numbers must be finite")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def parse_executor_message(value: str | bytes) -> ExecutorEnvelope:
    """Parse a bounded JSON object and collapse every failure to one safe error."""

    try:
        if type(value) is bytes:
            if len(value) > MAX_EXECUTOR_MESSAGE_BYTES:
                raise ValueError("message too large")
            source = value.decode("utf-8")
        elif type(value) is str:
            if len(value.encode("utf-8")) > MAX_EXECUTOR_MESSAGE_BYTES:
                raise ValueError("message too large")
            source = value
        else:
            raise TypeError("message must be text")
        decoded = json.loads(source, object_pairs_hook=_reject_duplicate_keys)
        if not isinstance(decoded, dict):
            raise ValueError("message must be an object")
        return ExecutorMessage.model_validate(decoded).root
    except (TypeError, ValueError, UnicodeError, ValidationError):
        pass
    raise ExecutorProtocolError()


__all__ = [
    "EXECUTOR_PROTOCOL_VERSION",
    "MAX_EXECUTOR_MESSAGE_BYTES",
    "CorrelationId",
    "ExecutorEnvelope",
    "ExecutorLifecycleEnvelope",
    "ExecutorMessage",
    "ExecutorProtocolError",
    "IdempotencyKey",
    "MessageId",
    "ProtocolExecutionAttemptId",
    "ProtocolExecutorId",
    "ProtocolInstallationId",
    "ProtocolTaskId",
    "TaskCommandEnvelope",
    "TaskCommandResultEnvelope",
    "TaskEventEnvelope",
    "parse_executor_message",
]
