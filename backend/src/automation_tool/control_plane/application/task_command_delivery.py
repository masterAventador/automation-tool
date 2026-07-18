"""Persistent Executor command delivery without conflating writes and ACKs."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable
from uuid import RFC_4122, UUID, uuid4

from automation_tool.control_plane.application.executor_connection_registry import (
    ExecutorConnectionRegistry,
    ExecutorConnectionUnavailable,
    StaleExecutorConnection,
)
from automation_tool.control_plane.domain import (
    MAX_TASK_EVENT_SEQUENCE,
    ExecutionAttemptId,
    ExecutorConnectionId,
    ExecutorId,
    InstallationId,
    TaskCommandResponseType,
    TaskCommandStatus,
    TaskCommandType,
    TaskId,
)
from automation_tool.protocol import (
    EXECUTOR_PROTOCOL_VERSION,
    IdempotencyKey,
    TaskCommandEnvelope,
    TaskCommandResultEnvelope,
)


class TaskCommandDeliveryRejected(ValueError):
    """A command delivery operation failed without reflecting private input."""

    def __init__(self) -> None:
        super().__init__("Task command delivery is rejected")


class TaskCommandDeliveryUnavailable(RuntimeError):
    """Persistent delivery cannot currently make safe progress."""

    def __init__(self) -> None:
        super().__init__("Task command delivery is unavailable")


class TaskCommandDeliveryClock(Protocol):
    def now(self) -> datetime: ...


class SystemTaskCommandDeliveryClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


def _aware_utc(value: object) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise TaskCommandDeliveryRejected
    return value.astimezone(UTC)


def _uuid_v4(value: object) -> UUID:
    if not isinstance(value, UUID) or value.version != 4 or value.variant != RFC_4122:
        raise TaskCommandDeliveryRejected
    return value


def _positive_seconds(value: object) -> timedelta:
    if not isinstance(value, timedelta) or value <= timedelta(0):
        raise TaskCommandDeliveryRejected
    return value


@dataclass(frozen=True, slots=True)
class PendingTaskCommand:
    message_id: UUID
    correlation_id: UUID
    installation_id: InstallationId
    task_id: TaskId
    execution_attempt_id: ExecutionAttemptId
    sequence: int
    command_type: TaskCommandType
    idempotency_key: str
    deadline_at: datetime
    created_at: datetime

    def __post_init__(self) -> None:
        _uuid_v4(self.message_id)
        _uuid_v4(self.correlation_id)
        if (
            type(self.installation_id) is not InstallationId
            or type(self.task_id) is not TaskId
            or type(self.execution_attempt_id) is not ExecutionAttemptId
            or type(self.sequence) is not int
            or not 1 <= self.sequence <= MAX_TASK_EVENT_SEQUENCE
            or not isinstance(self.command_type, TaskCommandType)
        ):
            raise TaskCommandDeliveryRejected
        try:
            normalized_key = str(IdempotencyKey(self.idempotency_key))
        except (TypeError, ValueError):
            raise TaskCommandDeliveryRejected from None
        created_at = _aware_utc(self.created_at)
        deadline_at = _aware_utc(self.deadline_at)
        if deadline_at <= created_at:
            raise TaskCommandDeliveryRejected
        object.__setattr__(self, "idempotency_key", normalized_key)
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "deadline_at", deadline_at)


@dataclass(frozen=True, slots=True)
class TaskCommandRecord:
    message_id: UUID
    correlation_id: UUID
    installation_id: InstallationId
    task_id: TaskId
    execution_attempt_id: ExecutionAttemptId
    sequence: int
    command_type: TaskCommandType
    status: TaskCommandStatus
    idempotency_key: str
    revision: int
    delivery_attempts: int
    next_delivery_at: datetime | None
    lease_expires_at: datetime | None
    delivered_at: datetime | None
    acknowledged_at: datetime | None
    response_message_id: UUID | None
    response_type: TaskCommandResponseType | None
    deadline_at: datetime
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_pending(cls, command: PendingTaskCommand) -> TaskCommandRecord:
        if not isinstance(command, PendingTaskCommand):
            raise TaskCommandDeliveryRejected
        return cls(
            message_id=command.message_id,
            correlation_id=command.correlation_id,
            installation_id=command.installation_id,
            task_id=command.task_id,
            execution_attempt_id=command.execution_attempt_id,
            sequence=command.sequence,
            command_type=command.command_type,
            status=TaskCommandStatus.PENDING,
            idempotency_key=command.idempotency_key,
            revision=1,
            delivery_attempts=0,
            next_delivery_at=command.created_at,
            lease_expires_at=None,
            delivered_at=None,
            acknowledged_at=None,
            response_message_id=None,
            response_type=None,
            deadline_at=command.deadline_at,
            created_at=command.created_at,
            updated_at=command.created_at,
        )


@dataclass(frozen=True, slots=True)
class CommandDeliveryResult:
    delivered: int
    expired: int


@runtime_checkable
class TaskCommandRepository(Protocol):
    async def enqueue(self, command: PendingTaskCommand) -> TaskCommandRecord: ...

    async def expire_due(
        self,
        *,
        installation_id: InstallationId,
        now: datetime,
    ) -> int: ...

    async def claim_next(
        self,
        *,
        installation_id: InstallationId,
        now: datetime,
        lease_expires_at: datetime,
        retry_delivered_before: datetime,
        recover_delivered: bool,
    ) -> TaskCommandRecord | None: ...

    async def mark_delivered(
        self,
        *,
        message_id: UUID,
        expected_revision: int,
        delivered_at: datetime,
    ) -> TaskCommandRecord: ...

    async def release_for_retry(
        self,
        *,
        message_id: UUID,
        expected_revision: int,
        now: datetime,
        retry_at: datetime,
    ) -> TaskCommandRecord: ...

    async def acknowledge(
        self,
        *,
        response: TaskCommandResultEnvelope,
        received_at: datetime,
    ) -> TaskCommandRecord: ...


class TaskCommandDeliveryService:
    """Claim, send, retry, expire, and acknowledge persistent commands."""

    def __init__(
        self,
        *,
        repository: TaskCommandRepository,
        registry: ExecutorConnectionRegistry,
        clock: TaskCommandDeliveryClock | None = None,
        lease_duration: timedelta = timedelta(seconds=10),
        retry_delay: timedelta = timedelta(seconds=1),
        acknowledgement_timeout: timedelta = timedelta(seconds=5),
        maximum_batch_size: int = 32,
        id_source: Callable[[], object] = uuid4,
    ) -> None:
        if (
            not isinstance(repository, TaskCommandRepository)
            or not isinstance(registry, ExecutorConnectionRegistry)
            or not callable(id_source)
            or type(maximum_batch_size) is not int
            or not 1 <= maximum_batch_size <= 256
        ):
            raise TaskCommandDeliveryRejected
        self._repository = repository
        self._registry = registry
        self._clock = clock or SystemTaskCommandDeliveryClock()
        self._lease_duration = _positive_seconds(lease_duration)
        self._retry_delay = _positive_seconds(retry_delay)
        self._acknowledgement_timeout = _positive_seconds(acknowledgement_timeout)
        self._maximum_batch_size = maximum_batch_size
        self._id_source = id_source

    def _now(self) -> datetime:
        try:
            return _aware_utc(self._clock.now())
        except Exception:
            raise TaskCommandDeliveryRejected from None

    def _new_id(self) -> UUID:
        try:
            return _uuid_v4(self._id_source())
        except Exception:
            raise TaskCommandDeliveryRejected from None

    async def enqueue(
        self,
        *,
        installation_id: InstallationId,
        task_id: TaskId,
        execution_attempt_id: ExecutionAttemptId,
        sequence: int,
        command_type: TaskCommandType,
        idempotency_key: str,
        deadline_at: datetime,
    ) -> TaskCommandRecord:
        now = self._now()
        command = PendingTaskCommand(
            message_id=self._new_id(),
            correlation_id=self._new_id(),
            installation_id=installation_id,
            task_id=task_id,
            execution_attempt_id=execution_attempt_id,
            sequence=sequence,
            command_type=command_type,
            idempotency_key=idempotency_key,
            deadline_at=deadline_at,
            created_at=now,
        )
        try:
            return await self._repository.enqueue(command)
        except TaskCommandDeliveryRejected:
            raise
        except Exception:
            raise TaskCommandDeliveryUnavailable from None

    async def dispatch_current(
        self,
        *,
        installation_id: InstallationId,
        executor_id: ExecutorId,
        connection_id: ExecutorConnectionId,
        recover_delivered: bool = False,
    ) -> CommandDeliveryResult:
        if (
            type(installation_id) is not InstallationId
            or type(executor_id) is not ExecutorId
            or type(connection_id) is not ExecutorConnectionId
            or type(recover_delivered) is not bool
        ):
            raise TaskCommandDeliveryRejected
        now = self._now()
        try:
            expired = await self._repository.expire_due(
                installation_id=installation_id,
                now=now,
            )
        except TaskCommandDeliveryRejected:
            raise
        except Exception:
            raise TaskCommandDeliveryUnavailable from None
        if type(expired) is not int or expired < 0:
            raise TaskCommandDeliveryRejected

        delivered = 0
        recovery_cutoff = now
        for _ in range(self._maximum_batch_size):
            claimed_at = self._now()
            try:
                command = await self._repository.claim_next(
                    installation_id=installation_id,
                    now=claimed_at,
                    lease_expires_at=claimed_at + self._lease_duration,
                    retry_delivered_before=(
                        recovery_cutoff
                        if recover_delivered
                        else claimed_at - self._acknowledgement_timeout
                    ),
                    recover_delivered=recover_delivered,
                )
            except TaskCommandDeliveryRejected:
                raise
            except Exception:
                raise TaskCommandDeliveryUnavailable from None
            if command is None:
                break
            if (
                not isinstance(command, TaskCommandRecord)
                or command.installation_id != installation_id
                or command.status is not TaskCommandStatus.IN_FLIGHT
                or claimed_at >= command.deadline_at
            ):
                raise TaskCommandDeliveryRejected
            source = _command_wire(command, executor_id=executor_id, sent_at=claimed_at)
            try:
                await self._registry.send_current(
                    installation_id=installation_id,
                    connection_id=connection_id,
                    source=source,
                )
            except (ExecutorConnectionUnavailable, StaleExecutorConnection):
                retry_at = self._now() + self._retry_delay
                try:
                    await self._repository.release_for_retry(
                        message_id=command.message_id,
                        expected_revision=command.revision,
                        now=self._now(),
                        retry_at=retry_at,
                    )
                except Exception:
                    raise TaskCommandDeliveryUnavailable from None
                break
            try:
                await self._repository.mark_delivered(
                    message_id=command.message_id,
                    expected_revision=command.revision,
                    delivered_at=claimed_at,
                )
            except TaskCommandDeliveryRejected:
                raise
            except Exception:
                raise TaskCommandDeliveryUnavailable from None
            delivered += 1
        return CommandDeliveryResult(delivered=delivered, expired=expired)

    async def acknowledge(
        self,
        response: TaskCommandResultEnvelope,
    ) -> TaskCommandRecord:
        if not isinstance(response, TaskCommandResultEnvelope):
            raise TaskCommandDeliveryRejected
        received_at = self._now()
        try:
            recorded = await self._repository.acknowledge(
                response=response,
                received_at=received_at,
            )
        except TaskCommandDeliveryRejected:
            raise
        except Exception:
            raise TaskCommandDeliveryUnavailable from None
        if recorded.status is TaskCommandStatus.EXPIRED:
            raise TaskCommandDeliveryRejected
        return recorded


def _command_wire(
    command: TaskCommandRecord,
    *,
    executor_id: ExecutorId,
    sent_at: datetime,
) -> str:
    try:
        envelope = TaskCommandEnvelope.model_validate(
            {
                "protocol_version": EXECUTOR_PROTOCOL_VERSION,
                "message_id": str(command.message_id),
                "message_type": command.command_type.value,
                "sent_at": sent_at,
                "deadline_at": command.deadline_at,
                "installation_id": str(command.installation_id),
                "executor_id": str(executor_id),
                "correlation_id": str(command.correlation_id),
                "idempotency_key": command.idempotency_key,
                "sequence": command.sequence,
                "payload": {},
                "task_id": str(command.task_id),
                "execution_attempt_id": str(command.execution_attempt_id),
            }
        )
        return json.dumps(
            envelope.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except Exception:
        raise TaskCommandDeliveryRejected from None


__all__ = [
    "CommandDeliveryResult",
    "PendingTaskCommand",
    "SystemTaskCommandDeliveryClock",
    "TaskCommandDeliveryClock",
    "TaskCommandDeliveryRejected",
    "TaskCommandDeliveryService",
    "TaskCommandDeliveryUnavailable",
    "TaskCommandRecord",
    "TaskCommandRepository",
]
