from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import count
from typing import Any, cast
from uuid import UUID

import pytest

from automation_tool.control_plane.application.task_command_delivery import TaskCommandRecord
from automation_tool.control_plane.application.task_discovery import (
    PendingTaskDiscovery,
    SystemTaskDiscoveryClock,
    TaskDiscoveryBatchAccumulator,
    TaskDiscoveryConvergenceResult,
    TaskDiscoveryConvergenceService,
    TaskDiscoveryRejected,
    TaskDiscoveryStartResult,
    TaskDiscoveryStartService,
    TaskDiscoveryUnavailable,
)
from automation_tool.control_plane.application.tasks import TaskRecord
from automation_tool.control_plane.domain import (
    ExecutionAttemptId,
    InstallationId,
    TaskCommandStatus,
    TaskCommandType,
    TaskId,
    TaskStatus,
)
from automation_tool.protocol import (
    DouyinCandidate,
    TaskDiscoveryBatchEnvelope,
    TaskDiscoveryCompletedEnvelope,
)

NOW = datetime(2026, 7, 19, 16, 30, tzinfo=UTC)
INSTALLATION_ID = InstallationId.parse("123e4567-e89b-42d3-a456-426614174003")
TASK_ID = TaskId.parse("123e4567-e89b-42d3-a456-426614174005")
ATTEMPT_ID = ExecutionAttemptId.parse("123e4567-e89b-42d3-a456-426614174006")


class Clock:
    @staticmethod
    def now() -> datetime:
        return NOW


class Ids:
    def __init__(self) -> None:
        self._values = count(10)

    def __call__(self) -> UUID:
        return UUID(f"923e4567-e89b-42d3-a456-{next(self._values):012d}")


def task(status: TaskStatus = TaskStatus.DISCOVERING_TARGETS) -> TaskRecord:
    return TaskRecord(
        task_id=TASK_ID,
        installation_id=INSTALLATION_ID,
        status=status,
        revision=2,
        last_event_sequence=2,
        created_at=NOW - timedelta(minutes=1),
        updated_at=NOW,
    )


def command_record() -> TaskCommandRecord:
    return TaskCommandRecord(
        message_id=UUID("923e4567-e89b-42d3-a456-426614174001"),
        correlation_id=UUID("923e4567-e89b-42d3-a456-426614174002"),
        installation_id=INSTALLATION_ID,
        task_id=TASK_ID,
        execution_attempt_id=ATTEMPT_ID,
        sequence=1,
        command_type=TaskCommandType.TASK_DISCOVER,
        status=TaskCommandStatus.PENDING,
        idempotency_key="task:discover:request:1",
        revision=1,
        delivery_attempts=0,
        next_delivery_at=NOW,
        lease_expires_at=None,
        delivered_at=None,
        acknowledged_at=None,
        response_message_id=None,
        response_type=None,
        deadline_at=NOW + timedelta(minutes=3),
        created_at=NOW,
        updated_at=NOW,
    )


class StartRepository:
    def __init__(self) -> None:
        self.pending: list[PendingTaskDiscovery] = []

    async def start(self, pending: PendingTaskDiscovery) -> TaskDiscoveryStartResult:
        self.pending.append(pending)
        return TaskDiscoveryStartResult(task=task(), command=command_record(), created=True)


@pytest.mark.asyncio
async def test_start_service_allocates_one_bounded_idempotent_discover_intent() -> None:
    repository = StartRepository()
    service = TaskDiscoveryStartService(
        repository=repository,
        clock=Clock(),
        id_source=Ids(),
    )

    result = await service.start(
        installation_id=INSTALLATION_ID,
        task_id=TASK_ID,
        idempotency_key="task:discover:request:1",
    )

    assert result.created is True
    pending = repository.pending[0]
    assert pending.installation_id == INSTALLATION_ID
    assert pending.task_id == TASK_ID
    assert pending.command_type is TaskCommandType.TASK_DISCOVER
    assert pending.command_sequence == 1
    assert pending.created_at == NOW
    assert pending.deadline_at == NOW + timedelta(minutes=3)

    for invalid in ("", "contains space", "x" * 129):
        with pytest.raises(TaskDiscoveryRejected):
            await service.start(
                installation_id=INSTALLATION_ID,
                task_id=TASK_ID,
                idempotency_key=invalid,
            )


def envelope(
    message_type: str,
    payload: dict[str, object],
    *,
    message_id: str,
    idempotency_key: str,
    sequence: int,
) -> TaskDiscoveryBatchEnvelope | TaskDiscoveryCompletedEnvelope:
    model = (
        TaskDiscoveryBatchEnvelope
        if message_type == "task.discovery_batch"
        else TaskDiscoveryCompletedEnvelope
    )
    return model.model_validate(
        {
            "protocol_version": "1.0",
            "message_id": message_id,
            "message_type": message_type,
            "sent_at": NOW,
            "deadline_at": NOW + timedelta(minutes=1),
            "installation_id": str(INSTALLATION_ID),
            "executor_id": "123e4567-e89b-42d3-a456-426614174004",
            "correlation_id": "323e4567-e89b-42d3-a456-426614174002",
            "idempotency_key": idempotency_key,
            "sequence": sequence,
            "payload": payload,
            "task_id": str(TASK_ID),
            "execution_attempt_id": str(ATTEMPT_ID),
        }
    )


def candidate(index: int) -> dict[str, object]:
    return {
        "candidate_version": "douyin.candidate.v1",
        "platform_target_id": f"author-{index}",
        "display_name": f"目标 {index}",
        "public_handle": f"target_{index}",
        "source": "general_search_author",
        "page_revision": 7,
    }


def batch(index: int, candidates: list[dict[str, object]]) -> TaskDiscoveryBatchEnvelope:
    return cast(
        TaskDiscoveryBatchEnvelope,
        envelope(
            "task.discovery_batch",
            {
                "discovery_version": "douyin.discovery.v1",
                "page_revision": 7,
                "batch_index": index,
                "batch_count": 2,
                "candidates": candidates,
            },
            message_id=f"423e4567-e89b-42d3-a456-{index:012d}",
            idempotency_key=f"task:discovery:batch:{index}",
            sequence=index,
        ),
    )


def completed() -> TaskDiscoveryCompletedEnvelope:
    return cast(
        TaskDiscoveryCompletedEnvelope,
        envelope(
            "task.discovery_completed",
            {
                "discovery_version": "douyin.discovery.v1",
                "outcome": "completed",
                "evidence": "candidates_extracted",
                "page_revision": 7,
                "batch_count": 2,
                "candidate_count": 12,
            },
            message_id="523e4567-e89b-42d3-a456-426614174001",
            idempotency_key="task:discovery:completed",
            sequence=3,
        ),
    )


class ConvergenceRepository:
    def __init__(self) -> None:
        self.authorized: list[TaskDiscoveryBatchEnvelope] = []
        self.completed: list[
            tuple[TaskDiscoveryCompletedEnvelope, tuple[DouyinCandidate, ...] | None]
        ] = []

    async def authorize_batch(self, message: TaskDiscoveryBatchEnvelope) -> None:
        self.authorized.append(message)

    async def converge(
        self,
        message: TaskDiscoveryCompletedEnvelope,
        *,
        candidates: tuple[DouyinCandidate, ...] | None,
        source_fingerprint: bytes,
        received_at: datetime,
    ) -> TaskDiscoveryConvergenceResult:
        assert len(source_fingerprint) == 32
        assert received_at == NOW
        self.completed.append((message, candidates))
        return TaskDiscoveryConvergenceResult(
            task=task(TaskStatus.AWAITING_CONFIRMATION), duplicate=False
        )


@pytest.mark.asyncio
async def test_convergence_accumulates_chunks_and_passes_one_ordered_candidate_snapshot() -> None:
    repository = ConvergenceRepository()
    service = TaskDiscoveryConvergenceService(
        repository=repository,
        accumulator=TaskDiscoveryBatchAccumulator(maximum_attempts=2),
        clock=Clock(),
    )
    first = batch(1, [candidate(index) for index in range(1, 11)])
    second = batch(2, [candidate(11), candidate(12)])

    await service.receive_batch(first)
    await service.receive_batch(first)
    await service.receive_batch(second)
    result = await service.receive_completed(completed())

    assert result.task.status is TaskStatus.AWAITING_CONFIRMATION
    assert len(repository.authorized) == 3
    persisted = repository.completed[0][1]
    assert persisted is not None
    assert [item.platform_target_id for item in persisted] == [
        *(f"author-{index}" for index in range(1, 13))
    ]


@pytest.mark.asyncio
async def test_convergence_rejects_conflicting_replay_missing_batch_and_expired_result() -> None:
    repository = ConvergenceRepository()
    service = TaskDiscoveryConvergenceService(
        repository=repository,
        accumulator=TaskDiscoveryBatchAccumulator(maximum_attempts=1),
        clock=Clock(),
    )
    await service.receive_batch(batch(1, [candidate(index) for index in range(1, 11)]))
    with pytest.raises(TaskDiscoveryRejected):
        await service.receive_batch(batch(1, [candidate(index) for index in range(2, 12)]))
    with pytest.raises(TaskDiscoveryRejected):
        await service.receive_completed(completed())

    expired = completed().model_copy(
        update={"deadline_at": NOW, "sent_at": NOW - timedelta(seconds=1)}
    )
    with pytest.raises(TaskDiscoveryRejected):
        await service.receive_completed(expired)


def test_discovery_value_objects_and_service_construction_fail_closed() -> None:
    assert SystemTaskDiscoveryClock().now().utcoffset() == timedelta(0)
    valid = PendingTaskDiscovery(
        message_id=UUID("923e4567-e89b-42d3-a456-426614174010"),
        correlation_id=UUID("923e4567-e89b-42d3-a456-426614174011"),
        execution_attempt_id=ATTEMPT_ID,
        installation_id=INSTALLATION_ID,
        task_id=TASK_ID,
        idempotency_key="task:discover:value-object",
        created_at=NOW,
        deadline_at=NOW + timedelta(seconds=1),
    )
    assert valid.created_at == NOW
    assert valid.deadline_at == NOW + timedelta(seconds=1)

    invalid_pending = (
        {"message_id": cast(Any, object())},
        {"created_at": datetime(2026, 7, 19, 16, 30)},
        {"execution_attempt_id": cast(Any, object())},
        {"deadline_at": NOW},
        {"idempotency_key": "contains space"},
    )
    for changes in invalid_pending:
        values: dict[str, object] = {
            "message_id": valid.message_id,
            "correlation_id": valid.correlation_id,
            "execution_attempt_id": valid.execution_attempt_id,
            "installation_id": valid.installation_id,
            "task_id": valid.task_id,
            "idempotency_key": valid.idempotency_key,
            "created_at": valid.created_at,
            "deadline_at": valid.deadline_at,
        }
        values.update(changes)
        with pytest.raises(TaskDiscoveryRejected):
            PendingTaskDiscovery(**cast(Any, values))

    for invalid in (
        {"task": cast(Any, object())},
        {"command": cast(Any, object())},
        {"created": cast(Any, 1)},
    ):
        values = {"task": task(), "command": command_record(), "created": True}
        values.update(invalid)
        with pytest.raises(TaskDiscoveryRejected):
            TaskDiscoveryStartResult(**cast(Any, values))
    with pytest.raises(TaskDiscoveryRejected):
        TaskDiscoveryConvergenceResult(task=cast(Any, object()), duplicate=False)
    with pytest.raises(TaskDiscoveryRejected):
        TaskDiscoveryBatchAccumulator(maximum_attempts=0)
    with pytest.raises(TaskDiscoveryRejected):
        TaskDiscoveryStartService(repository=cast(Any, object()))
    with pytest.raises(TaskDiscoveryRejected):
        TaskDiscoveryStartService(repository=StartRepository(), id_source=cast(Any, None))
    with pytest.raises(TaskDiscoveryRejected):
        TaskDiscoveryConvergenceService(repository=cast(Any, object()))


@pytest.mark.asyncio
async def test_start_service_maps_invalid_identity_and_repository_failures() -> None:
    class FailingRepository(StartRepository):
        def __init__(self, failure: Exception) -> None:
            super().__init__()
            self.failure = failure

        async def start(self, pending: PendingTaskDiscovery) -> TaskDiscoveryStartResult:
            raise self.failure

    with pytest.raises(TaskDiscoveryRejected):
        await TaskDiscoveryStartService(repository=StartRepository()).start(
            installation_id=cast(Any, object()),
            task_id=TASK_ID,
            idempotency_key="task:discover:invalid-installation",
        )
    with pytest.raises(TaskDiscoveryRejected):
        await TaskDiscoveryStartService(
            repository=StartRepository(),
            clock=Clock(),
            id_source=lambda: UUID(int=4),
        ).start(
            installation_id=INSTALLATION_ID,
            task_id=TASK_ID,
            idempotency_key="task:discover:invalid-id",
        )
    with pytest.raises(TaskDiscoveryRejected):
        await TaskDiscoveryStartService(
            repository=FailingRepository(ValueError("private")), clock=Clock()
        ).start(
            installation_id=INSTALLATION_ID,
            task_id=TASK_ID,
            idempotency_key="task:discover:value-error",
        )
    with pytest.raises(TaskDiscoveryUnavailable):
        await TaskDiscoveryStartService(
            repository=FailingRepository(RuntimeError("private")), clock=Clock()
        ).start(
            installation_id=INSTALLATION_ID,
            task_id=TASK_ID,
            idempotency_key="task:discover:unavailable",
        )


def non_success_completed() -> TaskDiscoveryCompletedEnvelope:
    return cast(
        TaskDiscoveryCompletedEnvelope,
        envelope(
            "task.discovery_completed",
            {
                "discovery_version": "douyin.discovery.v1",
                "outcome": "login_required",
                "evidence": "login_required",
                "page_revision": 7,
                "batch_count": 0,
                "candidate_count": 0,
            },
            message_id="523e4567-e89b-42d3-a456-426614174002",
            idempotency_key="task:discovery:login-required",
            sequence=1,
        ),
    )


def test_accumulator_rejects_resource_conflicts_and_clears_terminal_state() -> None:
    accumulator = TaskDiscoveryBatchAccumulator(maximum_attempts=1)
    with pytest.raises(TaskDiscoveryRejected):
        accumulator.add(cast(Any, object()))
    with pytest.raises(TaskDiscoveryRejected):
        accumulator.complete(cast(Any, object()))
    with pytest.raises(TaskDiscoveryRejected):
        accumulator.add(batch(2, [candidate(11)]))

    first = batch(1, [candidate(index) for index in range(1, 11)])
    accumulator.add(first)
    wrong_count = first.model_copy(
        update={"payload": first.payload.model_copy(update={"batch_count": 3})}
    )
    with pytest.raises(TaskDiscoveryRejected):
        accumulator.add(wrong_count)
    out_of_order = first.model_copy(
        update={
            "message_id": UUID("623e4567-e89b-42d3-a456-426614174003"),
            "idempotency_key": "task:discovery:batch:3",
            "payload": first.payload.model_copy(update={"batch_count": 2, "batch_index": 3}),
        }
    )
    with pytest.raises(TaskDiscoveryRejected):
        accumulator.add(out_of_order)
    second_attempt = first.model_copy(
        update={
            "execution_attempt_id": UUID("623e4567-e89b-42d3-a456-426614174004"),
            "message_id": UUID("623e4567-e89b-42d3-a456-426614174005"),
            "idempotency_key": "task:discovery:second-attempt",
        }
    )
    with pytest.raises(TaskDiscoveryRejected):
        accumulator.add(second_attempt)
    with pytest.raises(TaskDiscoveryRejected):
        accumulator.complete(non_success_completed())

    accumulator.clear(non_success_completed())
    assert accumulator.complete(non_success_completed()) is None
    with pytest.raises(TaskDiscoveryRejected):
        accumulator.complete(completed())

    count_mismatch = TaskDiscoveryBatchAccumulator()
    count_mismatch.add(first)
    count_mismatch.add(batch(2, [candidate(11), candidate(12)]))
    invalid_completed = completed().model_copy(
        update={"payload": completed().payload.model_copy(update={"candidate_count": 11})}
    )
    with pytest.raises(TaskDiscoveryRejected):
        count_mismatch.complete(invalid_completed)


@pytest.mark.asyncio
async def test_convergence_service_maps_clock_repository_and_input_failures() -> None:
    class BrokenClock:
        @staticmethod
        def now() -> datetime:
            raise RuntimeError("private clock")

    class FailureRepository(ConvergenceRepository):
        def __init__(self, failure: Exception, *, fail_batch: bool) -> None:
            super().__init__()
            self.failure = failure
            self.fail_batch = fail_batch

        async def authorize_batch(self, message: TaskDiscoveryBatchEnvelope) -> None:
            if self.fail_batch:
                raise self.failure
            await super().authorize_batch(message)

        async def converge(
            self,
            message: TaskDiscoveryCompletedEnvelope,
            *,
            candidates: tuple[DouyinCandidate, ...] | None,
            source_fingerprint: bytes,
            received_at: datetime,
        ) -> TaskDiscoveryConvergenceResult:
            raise self.failure

    with pytest.raises(TaskDiscoveryUnavailable):
        await TaskDiscoveryConvergenceService(
            repository=ConvergenceRepository(), clock=BrokenClock()
        ).receive_batch(batch(1, [candidate(1)]))
    with pytest.raises(TaskDiscoveryRejected):
        await TaskDiscoveryConvergenceService(repository=ConvergenceRepository()).receive_batch(
            cast(Any, object())
        )
    with pytest.raises(TaskDiscoveryRejected):
        await TaskDiscoveryConvergenceService(
            repository=FailureRepository(TaskDiscoveryRejected(), fail_batch=True), clock=Clock()
        ).receive_batch(batch(1, [candidate(1)]))
    with pytest.raises(TaskDiscoveryUnavailable):
        await TaskDiscoveryConvergenceService(
            repository=FailureRepository(RuntimeError("private"), fail_batch=True), clock=Clock()
        ).receive_batch(batch(1, [candidate(1)]))
    with pytest.raises(TaskDiscoveryRejected):
        await TaskDiscoveryConvergenceService(repository=ConvergenceRepository()).receive_completed(
            cast(Any, object())
        )

    for failure, expected in (
        (TaskDiscoveryRejected(), TaskDiscoveryRejected),
        (RuntimeError("private"), TaskDiscoveryUnavailable),
    ):
        service = TaskDiscoveryConvergenceService(
            repository=FailureRepository(failure, fail_batch=False), clock=Clock()
        )
        with pytest.raises(expected):
            await service.receive_completed(non_success_completed())
