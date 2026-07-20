"""Start and converge one chunked, read-only target discovery attempt."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable
from uuid import RFC_4122, UUID, uuid4

from automation_tool.control_plane.application.task_command_delivery import TaskCommandRecord
from automation_tool.control_plane.application.tasks import TaskRecord
from automation_tool.control_plane.domain import (
    ExecutionAttemptId,
    InstallationId,
    TaskCommandType,
    TaskId,
)
from automation_tool.protocol import (
    IdempotencyKey,
    TaskDiscoveryBatchEnvelope,
    TaskDiscoveryCompletedEnvelope,
)
from automation_tool.protocol.douyin_candidate import DouyinCandidate

_DISCOVERY_DEADLINE = timedelta(minutes=3)
_MAX_ACCUMULATED_ATTEMPTS = 32


class TaskDiscoveryRejected(ValueError):
    def __init__(self) -> None:
        super().__init__("Task discovery is rejected")


class TaskDiscoveryUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Task discovery is unavailable")


class TaskDiscoveryClock(Protocol):
    def now(self) -> datetime: ...


class SystemTaskDiscoveryClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


def _aware_utc(value: object) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise TaskDiscoveryRejected
    return value.astimezone(UTC)


def _uuid_v4(value: object) -> UUID:
    if not isinstance(value, UUID) or value.version != 4 or value.variant != RFC_4122:
        raise TaskDiscoveryRejected
    return value


@dataclass(frozen=True, slots=True)
class PendingTaskDiscovery:
    message_id: UUID
    correlation_id: UUID
    execution_attempt_id: ExecutionAttemptId
    installation_id: InstallationId
    task_id: TaskId
    idempotency_key: str
    created_at: datetime
    deadline_at: datetime

    def __post_init__(self) -> None:
        _uuid_v4(self.message_id)
        _uuid_v4(self.correlation_id)
        created_at = _aware_utc(self.created_at)
        deadline_at = _aware_utc(self.deadline_at)
        if (
            not isinstance(self.execution_attempt_id, ExecutionAttemptId)
            or not isinstance(self.installation_id, InstallationId)
            or not isinstance(self.task_id, TaskId)
            or deadline_at <= created_at
        ):
            raise TaskDiscoveryRejected
        try:
            normalized_key = str(IdempotencyKey(self.idempotency_key))
        except Exception:
            raise TaskDiscoveryRejected from None
        object.__setattr__(self, "idempotency_key", normalized_key)
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "deadline_at", deadline_at)

    @property
    def command_type(self) -> TaskCommandType:
        return TaskCommandType.TASK_DISCOVER

    @property
    def command_sequence(self) -> int:
        return 1


@dataclass(frozen=True, slots=True)
class TaskDiscoveryStartResult:
    task: TaskRecord
    command: TaskCommandRecord
    created: bool

    def __post_init__(self) -> None:
        if (
            not isinstance(self.task, TaskRecord)
            or not isinstance(self.command, TaskCommandRecord)
            or self.command.command_type is not TaskCommandType.TASK_DISCOVER
            or self.command.task_id != self.task.task_id
            or self.command.installation_id != self.task.installation_id
            or type(self.created) is not bool
        ):
            raise TaskDiscoveryRejected


@runtime_checkable
class TaskDiscoveryStartRepository(Protocol):
    async def start(self, pending: PendingTaskDiscovery) -> TaskDiscoveryStartResult: ...


class TaskDiscoveryStartService:
    def __init__(
        self,
        *,
        repository: TaskDiscoveryStartRepository,
        clock: TaskDiscoveryClock | None = None,
        id_source: object = uuid4,
    ) -> None:
        if not isinstance(repository, TaskDiscoveryStartRepository) or not callable(id_source):
            raise TaskDiscoveryRejected
        self._repository = repository
        self._clock = clock or SystemTaskDiscoveryClock()
        self._id_source = id_source

    async def start(
        self,
        *,
        installation_id: InstallationId,
        task_id: TaskId,
        idempotency_key: str,
    ) -> TaskDiscoveryStartResult:
        if not isinstance(installation_id, InstallationId) or not isinstance(task_id, TaskId):
            raise TaskDiscoveryRejected
        try:
            normalized_key = str(IdempotencyKey(idempotency_key))
            now = _aware_utc(self._clock.now())
            pending = PendingTaskDiscovery(
                message_id=_uuid_v4(self._id_source()),
                correlation_id=_uuid_v4(self._id_source()),
                execution_attempt_id=ExecutionAttemptId.parse(_uuid_v4(self._id_source())),
                installation_id=installation_id,
                task_id=task_id,
                idempotency_key=normalized_key,
                created_at=now,
                deadline_at=now + _DISCOVERY_DEADLINE,
            )
            return await self._repository.start(pending)
        except TaskDiscoveryRejected:
            raise
        except (TypeError, ValueError):
            raise TaskDiscoveryRejected from None
        except Exception:
            raise TaskDiscoveryUnavailable from None


@dataclass(frozen=True, slots=True)
class TaskDiscoveryConvergenceResult:
    task: TaskRecord
    duplicate: bool

    def __post_init__(self) -> None:
        if not isinstance(self.task, TaskRecord) or type(self.duplicate) is not bool:
            raise TaskDiscoveryRejected


@runtime_checkable
class TaskDiscoveryConvergenceRepository(Protocol):
    async def authorize_batch(self, message: TaskDiscoveryBatchEnvelope) -> None: ...

    async def converge(
        self,
        message: TaskDiscoveryCompletedEnvelope,
        *,
        candidates: tuple[DouyinCandidate, ...] | None,
        source_fingerprint: bytes,
        received_at: datetime,
    ) -> TaskDiscoveryConvergenceResult: ...


@dataclass(slots=True)
class _AccumulatedBatches:
    batch_count: int
    batches: dict[int, tuple[DouyinCandidate, ...]]
    fingerprints: dict[int, bytes]


def _attempt_key(
    message: TaskDiscoveryBatchEnvelope | TaskDiscoveryCompletedEnvelope,
) -> tuple[str, str, str, int]:
    return (
        str(message.installation_id),
        str(message.task_id),
        str(message.execution_attempt_id),
        message.payload.page_revision,
    )


def _source_fingerprint(
    message: TaskDiscoveryBatchEnvelope | TaskDiscoveryCompletedEnvelope,
) -> bytes:
    stable = message.model_dump(mode="json")
    del stable["message_id"]
    del stable["sent_at"]
    encoded = json.dumps(
        stable,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).digest()


class TaskDiscoveryBatchAccumulator:
    """Bounded memory rebuilt from the Executor's durable ordered Outbox on reconnect."""

    def __init__(self, *, maximum_attempts: int = _MAX_ACCUMULATED_ATTEMPTS) -> None:
        if type(maximum_attempts) is not int or not 1 <= maximum_attempts <= 256:
            raise TaskDiscoveryRejected
        self._maximum_attempts = maximum_attempts
        self._states: dict[tuple[str, str, str, int], _AccumulatedBatches] = {}

    def add(self, message: TaskDiscoveryBatchEnvelope) -> None:
        if not isinstance(message, TaskDiscoveryBatchEnvelope):
            raise TaskDiscoveryRejected
        key = _attempt_key(message)
        fingerprint = _source_fingerprint(message)
        state = self._states.get(key)
        if state is None:
            if len(self._states) >= self._maximum_attempts or message.payload.batch_index != 1:
                raise TaskDiscoveryRejected
            state = _AccumulatedBatches(
                batch_count=message.payload.batch_count,
                batches={},
                fingerprints={},
            )
            self._states[key] = state
        if state.batch_count != message.payload.batch_count:
            raise TaskDiscoveryRejected
        existing = state.fingerprints.get(message.payload.batch_index)
        if existing is not None:
            if existing != fingerprint:
                raise TaskDiscoveryRejected
            return
        if message.payload.batch_index != len(state.batches) + 1:
            raise TaskDiscoveryRejected
        state.fingerprints[message.payload.batch_index] = fingerprint
        state.batches[message.payload.batch_index] = tuple(
            item.to_candidate() for item in message.payload.candidates
        )

    def complete(
        self,
        message: TaskDiscoveryCompletedEnvelope,
    ) -> tuple[DouyinCandidate, ...] | None:
        if not isinstance(message, TaskDiscoveryCompletedEnvelope):
            raise TaskDiscoveryRejected
        key = _attempt_key(message)
        state = self._states.get(key)
        if message.payload.outcome != "completed":
            if state is not None:
                raise TaskDiscoveryRejected
            return None
        if (
            state is None
            or state.batch_count != message.payload.batch_count
            or len(state.batches) != state.batch_count
        ):
            raise TaskDiscoveryRejected
        candidates = tuple(
            candidate
            for index in range(1, state.batch_count + 1)
            for candidate in state.batches[index]
        )
        if len(candidates) != message.payload.candidate_count:
            raise TaskDiscoveryRejected
        return candidates

    def clear(self, message: TaskDiscoveryCompletedEnvelope) -> None:
        self._states.pop(_attempt_key(message), None)


class TaskDiscoveryConvergenceService:
    def __init__(
        self,
        *,
        repository: TaskDiscoveryConvergenceRepository,
        accumulator: TaskDiscoveryBatchAccumulator | None = None,
        clock: TaskDiscoveryClock | None = None,
    ) -> None:
        if not isinstance(repository, TaskDiscoveryConvergenceRepository):
            raise TaskDiscoveryRejected
        self._repository = repository
        self._accumulator = accumulator or TaskDiscoveryBatchAccumulator()
        self._clock = clock or SystemTaskDiscoveryClock()

    def _receive_time(
        self,
        message: TaskDiscoveryBatchEnvelope | TaskDiscoveryCompletedEnvelope,
    ) -> datetime:
        try:
            received_at = _aware_utc(self._clock.now())
            if received_at < message.sent_at or received_at >= message.deadline_at:
                raise TaskDiscoveryRejected
            return received_at
        except TaskDiscoveryRejected:
            raise
        except Exception:
            raise TaskDiscoveryUnavailable from None

    async def receive_batch(self, message: TaskDiscoveryBatchEnvelope) -> None:
        if not isinstance(message, TaskDiscoveryBatchEnvelope):
            raise TaskDiscoveryRejected
        self._receive_time(message)
        try:
            await self._repository.authorize_batch(message)
            self._accumulator.add(message)
        except TaskDiscoveryRejected:
            raise
        except Exception:
            raise TaskDiscoveryUnavailable from None

    async def receive_completed(
        self,
        message: TaskDiscoveryCompletedEnvelope,
    ) -> TaskDiscoveryConvergenceResult:
        if not isinstance(message, TaskDiscoveryCompletedEnvelope):
            raise TaskDiscoveryRejected
        received_at = self._receive_time(message)
        candidates = self._accumulator.complete(message)
        try:
            result = await self._repository.converge(
                message,
                candidates=candidates,
                source_fingerprint=_source_fingerprint(message),
                received_at=received_at,
            )
        except TaskDiscoveryRejected:
            raise
        except Exception:
            raise TaskDiscoveryUnavailable from None
        self._accumulator.clear(message)
        return result


__all__ = [
    "PendingTaskDiscovery",
    "SystemTaskDiscoveryClock",
    "TaskDiscoveryBatchAccumulator",
    "TaskDiscoveryClock",
    "TaskDiscoveryConvergenceRepository",
    "TaskDiscoveryConvergenceResult",
    "TaskDiscoveryConvergenceService",
    "TaskDiscoveryRejected",
    "TaskDiscoveryStartRepository",
    "TaskDiscoveryStartResult",
    "TaskDiscoveryStartService",
    "TaskDiscoveryUnavailable",
]
