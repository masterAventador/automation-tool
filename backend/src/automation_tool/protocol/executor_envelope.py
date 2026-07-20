"""Strict v1 wire envelope shared by the Control Plane and Local Executor."""

from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime, timedelta
from enum import StrEnum
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

from automation_tool.protocol.douyin_candidate import (
    MAX_CANDIDATE_DISPLAY_NAME_CHARACTERS,
    MAX_CANDIDATE_PUBLIC_HANDLE_CHARACTERS,
    MAX_DOUYIN_TARGET_ID_CHARACTERS,
    DouyinCandidate,
    DouyinCandidateSource,
    DouyinCandidateSummary,
)
from automation_tool.protocol.douyin_search import (
    MAX_SEARCH_KEYWORD_CHARACTERS,
    MAX_TASK_TARGET_LIMIT,
    DouyinSearchInput,
)
from automation_tool.protocol.json_object import decode_bounded_json_object
from automation_tool.protocol.limits import MAX_CROSS_RUNTIME_SEQUENCE
from automation_tool.protocol.safe_text import contains_control_or_bidi, is_unsafe_text
from automation_tool.protocol.version import CURRENT_EXECUTOR_PROTOCOL

EXECUTOR_PROTOCOL_VERSION = CURRENT_EXECUTOR_PROTOCOL
MAX_EXECUTOR_MESSAGE_BYTES = 32 * 1024
MAX_EXECUTOR_PAYLOAD_BYTES = 16 * 1024
MAX_EXECUTOR_PAYLOAD_DEPTH = 8
MAX_EXECUTOR_COLLECTION_ITEMS = 64
MAX_EXECUTOR_STRING_LENGTH = 4096
MAX_EXECUTOR_SEQUENCE = MAX_CROSS_RUNTIME_SEQUENCE
DOUYIN_DISCOVERY_PROTOCOL_VERSION = "douyin.discovery.v1"
MAX_DISCOVERY_BATCH_CANDIDATES = 10
MAX_DISCOVERY_BATCH_COUNT = MAX_TASK_TARGET_LIMIT // MAX_DISCOVERY_BATCH_CANDIDATES

_UUID_V4_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
_IDEMPOTENCY_KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$"
_RFC3339_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$"
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
    def require_bounded_safe_payload(
        cls,
        payload: dict[str, JsonValue] | BaseModel,
    ) -> dict[str, JsonValue] | BaseModel:
        bounded_payload = (
            payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
        )
        _validate_payload_value(bounded_payload, depth=0)
        try:
            encoded = json.dumps(
                bounded_payload,
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


class PlatformSessionState(StrEnum):
    """Closed platform-login health state; only healthy closes the circuit."""

    HEALTHY = "healthy"
    EXPIRED = "expired"
    MISSING = "missing"
    RISK = "risk"
    UNKNOWN = "unknown"


class PlatformSessionHealthPayload(_ProtocolModel):
    """The complete non-sensitive platform Session projection on the wire."""

    platform: Literal["douyin"]
    state: PlatformSessionState
    session_revision: Annotated[int, Field(strict=True, ge=1, le=MAX_EXECUTOR_SEQUENCE)]
    observed_at: AwareDatetime

    @field_validator("observed_at", mode="before")
    @classmethod
    def require_rfc3339_or_datetime(cls, value: object) -> object:
        return _ExecutorEnvelopeBase.require_rfc3339_or_datetime(value)

    @field_validator("observed_at")
    @classmethod
    def require_utc_timestamp(cls, value: datetime) -> datetime:
        return _ExecutorEnvelopeBase.require_utc_timestamps(value)


class PlatformSessionHealthEnvelope(_ExecutorEnvelopeBase):
    """Executor-scoped platform Session health fact."""

    message_type: Literal["platform.session_health"]
    payload: PlatformSessionHealthPayload  # type: ignore[assignment]


class _TaskEnvelopeBase(_ExecutorEnvelopeBase):
    task_id: ProtocolTaskId
    execution_attempt_id: ProtocolExecutionAttemptId


class DouyinDiscoveryCommandPayload(_ProtocolModel):
    """Only discovery business input crosses the cloud/local boundary."""

    discovery_version: Literal["douyin.discovery.v1"]
    keyword: Annotated[
        str,
        Field(strict=True, min_length=1, max_length=MAX_SEARCH_KEYWORD_CHARACTERS),
    ]
    target_limit: Annotated[int, Field(strict=True, ge=1, le=MAX_TASK_TARGET_LIMIT)]
    page_revision: Annotated[int, Field(strict=True, ge=1, le=MAX_EXECUTOR_SEQUENCE)]

    @model_validator(mode="after")
    def require_shared_search_policy(self) -> DouyinDiscoveryCommandPayload:
        DouyinSearchInput(keyword=self.keyword, target_limit=self.target_limit)
        return self

    def to_search_input(self) -> DouyinSearchInput:
        return DouyinSearchInput(keyword=self.keyword, target_limit=self.target_limit)


class DouyinDiscoveryCandidatePayload(_ProtocolModel):
    """The minimum Candidate fields allowed in one discovery batch."""

    candidate_version: Literal["douyin.candidate.v1"]
    platform_target_id: Annotated[
        str,
        Field(strict=True, min_length=1, max_length=MAX_DOUYIN_TARGET_ID_CHARACTERS),
    ]
    display_name: Annotated[
        str,
        Field(strict=True, min_length=1, max_length=MAX_CANDIDATE_DISPLAY_NAME_CHARACTERS),
    ]
    public_handle: (
        Annotated[
            str,
            Field(strict=True, min_length=1, max_length=MAX_CANDIDATE_PUBLIC_HANDLE_CHARACTERS),
        ]
        | None
    )
    source: Literal["general_search_author"]
    page_revision: Annotated[int, Field(strict=True, ge=1, le=MAX_EXECUTOR_SEQUENCE)]

    @model_validator(mode="after")
    def require_shared_candidate_policy(self) -> DouyinDiscoveryCandidatePayload:
        self.to_candidate()
        return self

    def to_candidate(self) -> DouyinCandidate:
        return DouyinCandidate(
            platform_target_id=self.platform_target_id,
            summary=DouyinCandidateSummary(
                display_name=self.display_name,
                public_handle=self.public_handle,
            ),
            source=DouyinCandidateSource(self.source),
            page_revision=self.page_revision,
        )


class DouyinDiscoveryBatchPayload(_ProtocolModel):
    """One bounded replayable Candidate chunk."""

    discovery_version: Literal["douyin.discovery.v1"]
    page_revision: Annotated[int, Field(strict=True, ge=1, le=MAX_EXECUTOR_SEQUENCE)]
    batch_index: Annotated[int, Field(strict=True, ge=1, le=MAX_DISCOVERY_BATCH_COUNT)]
    batch_count: Annotated[int, Field(strict=True, ge=1, le=MAX_DISCOVERY_BATCH_COUNT)]
    candidates: Annotated[
        list[DouyinDiscoveryCandidatePayload],
        Field(min_length=1, max_length=MAX_DISCOVERY_BATCH_CANDIDATES),
    ]

    @model_validator(mode="after")
    def require_consistent_page_and_batch(self) -> DouyinDiscoveryBatchPayload:
        if self.batch_index > self.batch_count or any(
            candidate.page_revision != self.page_revision for candidate in self.candidates
        ):
            raise ValueError("inconsistent discovery batch")
        return self


class DouyinDiscoveryCompletedPayload(_ProtocolModel):
    """Closed final discovery fact; only success can reference Candidate batches."""

    discovery_version: Literal["douyin.discovery.v1"]
    outcome: Literal["completed", "login_required", "handoff_required", "failed"]
    evidence: Literal[
        "candidates_extracted",
        "login_required",
        "blocking_dialog",
        "no_candidates",
        "navigation_timed_out",
        "home_ready_timed_out",
        "action_timed_out",
        "result_url_timed_out",
        "results_ready_timed_out",
        "page_version_unknown",
        "conflicting_anchors",
        "results_unavailable",
        "privacy_rejected",
        "result_count_decreased",
        "cancellation_unavailable",
        "cancellation_requested",
        "page_unavailable",
    ]
    page_revision: Annotated[int, Field(strict=True, ge=1, le=MAX_EXECUTOR_SEQUENCE)]
    batch_count: Annotated[int, Field(strict=True, ge=0, le=MAX_DISCOVERY_BATCH_COUNT)]
    candidate_count: Annotated[int, Field(strict=True, ge=0, le=MAX_TASK_TARGET_LIMIT)]

    @model_validator(mode="after")
    def require_consistent_outcome(self) -> DouyinDiscoveryCompletedPayload:
        expected_evidence = {
            "completed": frozenset({"candidates_extracted"}),
            "login_required": frozenset({"login_required"}),
            "handoff_required": frozenset({"blocking_dialog"}),
            "failed": frozenset(
                {
                    "no_candidates",
                    "navigation_timed_out",
                    "home_ready_timed_out",
                    "action_timed_out",
                    "result_url_timed_out",
                    "results_ready_timed_out",
                    "page_version_unknown",
                    "conflicting_anchors",
                    "results_unavailable",
                    "privacy_rejected",
                    "result_count_decreased",
                    "cancellation_unavailable",
                    "cancellation_requested",
                    "page_unavailable",
                }
            ),
        }
        if self.evidence not in expected_evidence[self.outcome]:
            raise ValueError("discovery evidence does not match outcome")
        if self.outcome == "completed":
            expected_batches = (
                self.candidate_count + MAX_DISCOVERY_BATCH_CANDIDATES - 1
            ) // MAX_DISCOVERY_BATCH_CANDIDATES
            if self.candidate_count == 0 or self.batch_count != expected_batches:
                raise ValueError("discovery completion counts are inconsistent")
        elif self.batch_count != 0 or self.candidate_count != 0:
            raise ValueError("non-success discovery cannot reference candidates")
        return self


class TaskCommandEnvelope(_TaskEnvelopeBase):
    """Control Plane commands that target one execution attempt."""

    message_type: Literal[
        "task.offer",
        "task.pause",
        "task.resume",
        "task.cancel",
        "task.emergency_stop",
    ]


class TaskDiscoveryCommandEnvelope(_TaskEnvelopeBase):
    """One typed, read-only Douyin target discovery command."""

    message_type: Literal["task.discover"]
    payload: DouyinDiscoveryCommandPayload  # type: ignore[assignment]


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


class TaskDiscoveryBatchEnvelope(_TaskEnvelopeBase):
    """One replayable chunk of privacy-trimmed Candidates."""

    message_type: Literal["task.discovery_batch"]
    payload: DouyinDiscoveryBatchPayload  # type: ignore[assignment]


class TaskDiscoveryCompletedEnvelope(_TaskEnvelopeBase):
    """The final closed discovery outcome for one execution attempt."""

    message_type: Literal["task.discovery_completed"]
    payload: DouyinDiscoveryCompletedPayload  # type: ignore[assignment]


type ExecutorEnvelope = Annotated[
    ExecutorLifecycleEnvelope
    | PlatformSessionHealthEnvelope
    | TaskCommandEnvelope
    | TaskDiscoveryCommandEnvelope
    | TaskCommandResultEnvelope
    | TaskEventEnvelope
    | TaskDiscoveryBatchEnvelope
    | TaskDiscoveryCompletedEnvelope,
    Field(discriminator="message_type"),
]


class ExecutorMessage(RootModel[ExecutorEnvelope]):
    """Pydantic discriminated union for every supported v1 message type."""

    model_config = ConfigDict(frozen=True)


def _normalized_payload_name(value: str) -> str:
    camel_split = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", value)
    return re.sub(r"[.-]+", "_", camel_split).lower()


def _unsafe_payload_string(value: str) -> bool:
    return is_unsafe_text(
        value,
        maximum_characters=MAX_EXECUTOR_STRING_LENGTH,
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
                or contains_control_or_bidi(key)
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


def parse_executor_message(value: str | bytes) -> ExecutorEnvelope:
    """Parse a bounded JSON object and collapse every failure to one safe error."""

    try:
        decoded = decode_bounded_json_object(value, maximum_bytes=MAX_EXECUTOR_MESSAGE_BYTES)
        return ExecutorMessage.model_validate(decoded).root
    except (TypeError, ValueError, UnicodeError, ValidationError):
        pass
    raise ExecutorProtocolError()


__all__ = [
    "DOUYIN_DISCOVERY_PROTOCOL_VERSION",
    "EXECUTOR_PROTOCOL_VERSION",
    "MAX_DISCOVERY_BATCH_CANDIDATES",
    "MAX_DISCOVERY_BATCH_COUNT",
    "MAX_EXECUTOR_MESSAGE_BYTES",
    "CorrelationId",
    "DouyinDiscoveryBatchPayload",
    "DouyinDiscoveryCandidatePayload",
    "DouyinDiscoveryCommandPayload",
    "DouyinDiscoveryCompletedPayload",
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
    "TaskDiscoveryBatchEnvelope",
    "TaskDiscoveryCommandEnvelope",
    "TaskDiscoveryCompletedEnvelope",
    "TaskEventEnvelope",
    "parse_executor_message",
]
