from __future__ import annotations

import json
from dataclasses import dataclass, fields, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from automation_tool.control_plane.application import task_command_delivery as delivery_module
from automation_tool.control_plane.application.action_risk_authorizations import (
    ActionRiskAuthorization,
)
from automation_tool.control_plane.application.executor_connection_registry import (
    ExecutorConnectionRegistry,
)
from automation_tool.control_plane.application.task_command_delivery import (
    ActionCommandContext,
    CommandDeliveryResult,
    PendingTaskCommand,
    TaskCommandDeliveryRejected,
    TaskCommandDeliveryService,
    TaskCommandDeliveryUnavailable,
    TaskCommandRecord,
)
from automation_tool.control_plane.domain import (
    ACTION_RISK_POLICY_VERSION,
    ActionId,
    ActionRiskPlatform,
    DouyinSearchExposureAction,
    ExecutionAttemptId,
    ExecutorConnectionId,
    ExecutorId,
    InstallationId,
    TargetId,
    TaskCommandResponseType,
    TaskCommandStatus,
    TaskCommandType,
    TaskId,
)
from automation_tool.protocol import (
    DouyinCandidate,
    DouyinCandidateSource,
    DouyinCandidateSummary,
    DouyinDiscoveryCommandPayload,
    TaskActionCommandEnvelope,
    TaskCommandResultEnvelope,
    parse_executor_message,
)

NOW = datetime(2026, 7, 18, 8, 0, tzinfo=UTC)
INSTALLATION_ID = InstallationId.parse("123e4567-e89b-42d3-a456-426614174003")
TASK_ID = TaskId.parse("123e4567-e89b-42d3-a456-426614174005")
ATTEMPT_ID = ExecutionAttemptId.parse("123e4567-e89b-42d3-a456-426614174006")
EXECUTOR_ID = ExecutorId.parse("123e4567-e89b-42d3-a456-426614174004")
CONNECTION_ID = ExecutorConnectionId.parse("123e4567-e89b-42d3-a456-426614174007")
MESSAGE_ID = UUID("323e4567-e89b-42d3-a456-426614174001")
CORRELATION_ID = UUID("323e4567-e89b-42d3-a456-426614174002")


@dataclass
class MutableClock:
    value: datetime = NOW

    def now(self) -> datetime:
        return self.value


class FakeRepository:
    def __init__(self, command: TaskCommandRecord | None = None) -> None:
        self.command = command
        self.expire_calls = 0
        self.claim_recovery: list[bool] = []
        self.fail_delivery = False

    async def enqueue(self, command: PendingTaskCommand) -> TaskCommandRecord:
        if self.command is None:
            self.command = TaskCommandRecord.from_pending(command)
        return self.command

    async def expire_due(self, *, installation_id: InstallationId, now: datetime) -> int:
        assert installation_id == INSTALLATION_ID
        self.expire_calls += 1
        return 0

    async def claim_next(
        self,
        *,
        installation_id: InstallationId,
        now: datetime,
        lease_expires_at: datetime,
        retry_delivered_before: datetime,
        recover_delivered: bool,
    ) -> TaskCommandRecord | None:
        assert installation_id == INSTALLATION_ID
        self.claim_recovery.append(recover_delivered)
        if self.command is None or self.command.status is not TaskCommandStatus.PENDING:
            return None
        self.command = replace(
            self.command,
            status=TaskCommandStatus.IN_FLIGHT,
            revision=self.command.revision + 1,
            delivery_attempts=self.command.delivery_attempts + 1,
            next_delivery_at=None,
            lease_expires_at=lease_expires_at,
            updated_at=now,
        )
        return self.command

    async def mark_delivered(
        self,
        *,
        message_id: UUID,
        expected_revision: int,
        delivered_at: datetime,
    ) -> TaskCommandRecord:
        assert self.command is not None
        if self.fail_delivery:
            raise TaskCommandDeliveryRejected
        assert message_id == self.command.message_id
        assert expected_revision == self.command.revision
        self.command = replace(
            self.command,
            status=TaskCommandStatus.DELIVERED,
            revision=self.command.revision + 1,
            lease_expires_at=None,
            delivered_at=delivered_at,
            updated_at=delivered_at,
        )
        return self.command

    async def release_for_retry(
        self,
        *,
        message_id: UUID,
        expected_revision: int,
        now: datetime,
        retry_at: datetime,
    ) -> TaskCommandRecord:
        assert self.command is not None
        self.command = replace(
            self.command,
            status=TaskCommandStatus.PENDING,
            revision=self.command.revision + 1,
            next_delivery_at=retry_at,
            lease_expires_at=None,
            delivered_at=None,
            updated_at=now,
        )
        return self.command

    async def acknowledge(
        self,
        *,
        response: TaskCommandResultEnvelope,
        received_at: datetime,
    ) -> TaskCommandRecord:
        assert self.command is not None
        response_type = TaskCommandResponseType(response.message_type)
        self.command = replace(
            self.command,
            status=(
                TaskCommandStatus.REJECTED
                if response_type is TaskCommandResponseType.TASK_REJECT
                else TaskCommandStatus.ACKNOWLEDGED
            ),
            revision=self.command.revision + 1,
            acknowledged_at=received_at,
            response_message_id=UUID(str(response.message_id)),
            response_type=response_type,
            updated_at=received_at,
        )
        return self.command


class FakeRegistry(ExecutorConnectionRegistry):
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.fail = False

    async def send_current(self, **values: object) -> None:
        assert values["installation_id"] == INSTALLATION_ID
        assert values["connection_id"] == CONNECTION_ID
        if self.fail:
            from automation_tool.control_plane.application.executor_connection_registry import (
                ExecutorConnectionUnavailable,
            )

            raise ExecutorConnectionUnavailable
        self.sent.append(str(values["source"]))


@dataclass(frozen=True)
class FakeIssuedAuthority:
    token: str = "ataa1.Y2Fub25pY2Fs.c2lnbmF0dXJl"


class FakeActionAuthorityIssuer:
    @staticmethod
    def issue(
        *,
        authorization: ActionRiskAuthorization,
        executor_id: ExecutorId,
    ) -> FakeIssuedAuthority:
        assert authorization.action_id == ACTION_ID
        assert executor_id == EXECUTOR_ID
        return FakeIssuedAuthority()


def pending_command() -> PendingTaskCommand:
    return PendingTaskCommand(
        message_id=MESSAGE_ID,
        correlation_id=CORRELATION_ID,
        installation_id=INSTALLATION_ID,
        task_id=TASK_ID,
        execution_attempt_id=ATTEMPT_ID,
        sequence=1,
        command_type=TaskCommandType.TASK_OFFER,
        idempotency_key="task:offer:attempt:1",
        deadline_at=NOW + timedelta(minutes=5),
        created_at=NOW,
    )


ACTION_ID = ActionId.parse("923e4567-e89b-42d3-a456-426614174001")
TARGET_ID = TargetId.parse("a23e4567-e89b-42d3-a456-426614174001")


def action_command() -> TaskCommandRecord:
    authorization = ActionRiskAuthorization(
        action_id=ACTION_ID,
        target_id=TARGET_ID,
        execution_attempt_id=ATTEMPT_ID,
        task_id=TASK_ID,
        installation_id=INSTALLATION_ID,
        ordinal=1,
        platform=ActionRiskPlatform.DOUYIN,
        action=DouyinSearchExposureAction.COMMENT,
        policy_version=ACTION_RISK_POLICY_VERSION,
        effective_minimum_interval_seconds=5,
        task_action_limit=10,
        daily_action_limit=100,
        consecutive_failure_threshold=3,
        task_count_after=1,
        daily_count_after=1,
        authorized_day=NOW.date(),
        authorized_at=NOW,
        created_at=NOW,
    )
    base = PendingTaskCommand(
        message_id=MESSAGE_ID,
        correlation_id=CORRELATION_ID,
        installation_id=INSTALLATION_ID,
        task_id=TASK_ID,
        execution_attempt_id=ATTEMPT_ID,
        sequence=2,
        command_type=TaskCommandType.ACTION_EXECUTE,
        idempotency_key=f"action:{ACTION_ID}",
        deadline_at=NOW + timedelta(minutes=5),
        created_at=NOW,
    )
    return replace(
        TaskCommandRecord.from_pending(base),
        action_id=ACTION_ID,
        action_context=ActionCommandContext(
            authorization=authorization,
            candidate=candidate(),
            message_template="您好 {{target_display_name}}",
        ),
    )


def candidate() -> DouyinCandidate:
    return DouyinCandidate(
        platform_target_id="douyin-user-1",
        summary=DouyinCandidateSummary(
            display_name="目标一",
            public_handle="target-one",
        ),
        source=DouyinCandidateSource.GENERAL_SEARCH_AUTHOR,
        page_revision=1,
    )


def response(message_type: str = "task.accept") -> TaskCommandResultEnvelope:
    return TaskCommandResultEnvelope.model_validate(
        {
            "protocol_version": "1.0",
            "message_id": "423e4567-e89b-42d3-a456-426614174001",
            "message_type": message_type,
            "sent_at": "2026-07-18T08:00:01Z",
            "deadline_at": "2026-07-18T08:00:31Z",
            "installation_id": str(INSTALLATION_ID),
            "executor_id": str(EXECUTOR_ID),
            "correlation_id": str(CORRELATION_ID),
            "idempotency_key": "task:accept:attempt:1",
            "sequence": 1,
            "payload": {"accepted": True},
            "task_id": str(TASK_ID),
            "execution_attempt_id": str(ATTEMPT_ID),
        }
    )


@pytest.mark.asyncio
async def test_pending_offer_is_claimed_sent_and_only_marked_delivered() -> None:
    repository = FakeRepository(TaskCommandRecord.from_pending(pending_command()))
    registry = FakeRegistry()
    service = TaskCommandDeliveryService(
        repository=repository,
        registry=registry,
        clock=MutableClock(),
    )

    result = await service.dispatch_current(
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
        connection_id=CONNECTION_ID,
    )

    assert result == CommandDeliveryResult(delivered=1, expired=0)
    assert repository.command is not None
    assert repository.command.status is TaskCommandStatus.DELIVERED
    assert repository.command.delivery_attempts == 1
    wire = json.loads(registry.sent[0])
    assert wire["message_type"] == "task.offer"
    assert wire["message_id"] == str(MESSAGE_ID)
    assert wire["correlation_id"] == str(CORRELATION_ID)
    assert wire["executor_id"] == str(EXECUTOR_ID)
    assert wire["payload"] == {"task_event_sequence_baseline": 0}


@pytest.mark.asyncio
async def test_action_command_is_signed_and_typed_at_the_real_delivery_boundary() -> None:
    repository = FakeRepository(action_command())
    registry = FakeRegistry()
    service = TaskCommandDeliveryService(
        repository=repository,
        registry=registry,
        clock=MutableClock(),
        action_authority_issuer=FakeActionAuthorityIssuer(),
    )

    result = await service.dispatch_current(
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
        connection_id=CONNECTION_ID,
    )

    assert result == CommandDeliveryResult(delivered=1, expired=0)
    parsed = parse_executor_message(registry.sent[0])
    assert isinstance(parsed, TaskActionCommandEnvelope)
    assert str(parsed.payload.action_id) == str(ACTION_ID)
    assert str(parsed.payload.target_id) == str(TARGET_ID)
    assert parsed.payload.message_template == "您好 {{target_display_name}}"
    assert parsed.payload.signed_authority == FakeIssuedAuthority().token


def test_action_command_context_and_wire_fail_closed_on_inconsistent_authority() -> None:
    command = action_command()
    assert command.action_context is not None
    authorization = command.action_context.authorization
    browse_authorization = replace(
        authorization,
        action=DouyinSearchExposureAction.BROWSE,
    )
    browse_context = ActionCommandContext(
        authorization=browse_authorization,
        candidate=candidate(),
        message_template=None,
    )
    assert repr(browse_context) == "ActionCommandContext(<redacted>)"

    invalid_contexts = (
        {
            "authorization": browse_authorization,
            "candidate": candidate(),
            "message_template": "browse must not have a template",
        },
        {
            "authorization": authorization,
            "candidate": candidate(),
            "message_template": " ",
        },
        {
            "authorization": authorization,
            "candidate": candidate(),
            "message_template": None,
        },
        {
            "authorization": object(),
            "candidate": candidate(),
            "message_template": "您好 {{target_display_name}}",
        },
        {
            "authorization": authorization,
            "candidate": object(),
            "message_template": "您好 {{target_display_name}}",
        },
    )
    for values in invalid_contexts:
        with pytest.raises(TaskCommandDeliveryRejected):
            ActionCommandContext(**values)  # type: ignore[arg-type]

    different_authorization = replace(
        authorization,
        action_id=ActionId.parse("b23e4567-e89b-42d3-a456-426614174001"),
    )
    inconsistent_commands = (
        replace(command, action_id=None),
        replace(command, action_context=None),
        replace(
            command,
            action_context=replace(
                command.action_context,
                authorization=different_authorization,
            ),
        ),
    )
    for inconsistent in inconsistent_commands:
        with pytest.raises(TaskCommandDeliveryRejected):
            delivery_module._command_wire(
                inconsistent,
                executor_id=EXECUTOR_ID,
                sent_at=NOW,
                action_authority_issuer=FakeActionAuthorityIssuer(),
            )

    with pytest.raises(TaskCommandDeliveryRejected):
        delivery_module._command_wire(command, executor_id=EXECUTOR_ID, sent_at=NOW)

    class InvalidIssuer:
        @staticmethod
        def issue(**values: object) -> object:
            return object()

    with pytest.raises(TaskCommandDeliveryRejected):
        delivery_module._command_wire(
            command,
            executor_id=EXECUTOR_ID,
            sent_at=NOW,
            action_authority_issuer=InvalidIssuer(),
        )

    discovery_payload = DouyinDiscoveryCommandPayload.model_validate(
        {
            "discovery_version": "douyin.discovery.v1",
            "keyword": "自动化运营",
            "target_limit": 10,
            "page_revision": 1,
        }
    )
    with pytest.raises(TaskCommandDeliveryRejected):
        delivery_module._command_wire(
            replace(command, discovery_payload=discovery_payload),
            executor_id=EXECUTOR_ID,
            sent_at=NOW,
            action_authority_issuer=FakeActionAuthorityIssuer(),
        )


@pytest.mark.asyncio
async def test_socket_failure_releases_claim_without_fabricating_ack() -> None:
    repository = FakeRepository(TaskCommandRecord.from_pending(pending_command()))
    registry = FakeRegistry()
    registry.fail = True
    service = TaskCommandDeliveryService(
        repository=repository,
        registry=registry,
        clock=MutableClock(),
    )

    result = await service.dispatch_current(
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
        connection_id=CONNECTION_ID,
    )

    assert result.delivered == 0
    assert repository.command is not None
    assert repository.command.status is TaskCommandStatus.PENDING
    assert repository.command.response_message_id is None


@pytest.mark.asyncio
async def test_executor_response_is_the_only_acknowledgement_boundary() -> None:
    delivered = replace(
        TaskCommandRecord.from_pending(pending_command()),
        status=TaskCommandStatus.DELIVERED,
        revision=3,
        delivery_attempts=1,
        next_delivery_at=None,
        delivered_at=NOW,
    )
    repository = FakeRepository(delivered)
    service = TaskCommandDeliveryService(
        repository=repository,
        registry=FakeRegistry(),
        clock=MutableClock(NOW + timedelta(seconds=1)),
    )

    recorded = await service.acknowledge(response())

    assert recorded.status is TaskCommandStatus.ACKNOWLEDGED
    assert recorded.response_type is TaskCommandResponseType.TASK_ACCEPT


@pytest.mark.asyncio
async def test_recovery_dispatch_is_explicit_and_inputs_fail_closed() -> None:
    repository = FakeRepository()
    service = TaskCommandDeliveryService(
        repository=repository,
        registry=FakeRegistry(),
        clock=MutableClock(),
    )

    await service.dispatch_current(
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
        connection_id=CONNECTION_ID,
        recover_delivered=True,
    )
    assert repository.claim_recovery == [True]

    for invalid in (None, str(INSTALLATION_ID), 1, True):
        with pytest.raises(
            TaskCommandDeliveryRejected, match=r"^Task command delivery is rejected$"
        ):
            await service.dispatch_current(
                installation_id=invalid,
                executor_id=EXECUTOR_ID,
                connection_id=CONNECTION_ID,
            )


def test_pending_command_and_service_configuration_reject_invalid_values() -> None:
    valid = pending_command()
    invalid_pending: tuple[dict[str, object], ...] = (
        {"message_id": UUID("123e4567-e89b-12d3-a456-426614174001")},
        {"correlation_id": UUID("123e4567-e89b-12d3-a456-426614174002")},
        {"installation_id": str(INSTALLATION_ID)},
        {"task_id": str(TASK_ID)},
        {"execution_attempt_id": str(ATTEMPT_ID)},
        {"sequence": True},
        {"sequence": 0},
        {"command_type": "task.offer"},
        {"idempotency_key": "private invalid key"},
        {"created_at": NOW.replace(tzinfo=None)},
        {"deadline_at": NOW},
    )
    values = {field.name: getattr(valid, field.name) for field in fields(valid)}
    for overrides in invalid_pending:
        with pytest.raises(TaskCommandDeliveryRejected):
            PendingTaskCommand(**{**values, **overrides})

    with pytest.raises(TaskCommandDeliveryRejected):
        TaskCommandRecord.from_pending(object())  # type: ignore[arg-type]

    repository = FakeRepository()
    registry = FakeRegistry()
    invalid_service_arguments: tuple[dict[str, object], ...] = (
        {"repository": object()},
        {"registry": object()},
        {"id_source": object()},
        {"maximum_batch_size": True},
        {"maximum_batch_size": 0},
        {"maximum_batch_size": 257},
        {"lease_duration": timedelta(0)},
        {"retry_delay": timedelta(seconds=-1)},
        {"acknowledgement_timeout": "5"},
    )
    for overrides in invalid_service_arguments:
        arguments: dict[str, object] = {"repository": repository, "registry": registry}
        arguments.update(overrides)
        with pytest.raises(TaskCommandDeliveryRejected):
            TaskCommandDeliveryService(**arguments)  # type: ignore[arg-type]

    assert delivery_module.SystemTaskCommandDeliveryClock().now().utcoffset() == timedelta(0)
    assert str(TaskCommandDeliveryUnavailable()) == "Task command delivery is unavailable"


@pytest.mark.asyncio
async def test_enqueue_generates_ids_and_maps_repository_or_clock_failures() -> None:
    ids = iter((MESSAGE_ID, CORRELATION_ID))
    repository = FakeRepository()
    service = TaskCommandDeliveryService(
        repository=repository,
        registry=FakeRegistry(),
        clock=MutableClock(),
        id_source=lambda: next(ids),
    )

    created = await service.enqueue(
        installation_id=INSTALLATION_ID,
        task_id=TASK_ID,
        execution_attempt_id=ATTEMPT_ID,
        sequence=1,
        command_type=TaskCommandType.TASK_OFFER,
        idempotency_key="task:offer:attempt:1",
        deadline_at=NOW + timedelta(minutes=5),
    )
    assert created.message_id == MESSAGE_ID

    class BadClock:
        def now(self) -> object:
            return None

    with pytest.raises(TaskCommandDeliveryRejected):
        await TaskCommandDeliveryService(
            repository=FakeRepository(),
            registry=FakeRegistry(),
            clock=BadClock(),  # type: ignore[arg-type]
        ).enqueue(
            installation_id=INSTALLATION_ID,
            task_id=TASK_ID,
            execution_attempt_id=ATTEMPT_ID,
            sequence=1,
            command_type=TaskCommandType.TASK_OFFER,
            idempotency_key="task:offer:attempt:1",
            deadline_at=NOW + timedelta(minutes=5),
        )

    bad_id_service = TaskCommandDeliveryService(
        repository=FakeRepository(),
        registry=FakeRegistry(),
        clock=MutableClock(),
        id_source=lambda: UUID("123e4567-e89b-12d3-a456-426614174001"),
    )
    with pytest.raises(TaskCommandDeliveryRejected):
        await bad_id_service.enqueue(
            installation_id=INSTALLATION_ID,
            task_id=TASK_ID,
            execution_attempt_id=ATTEMPT_ID,
            sequence=1,
            command_type=TaskCommandType.TASK_OFFER,
            idempotency_key="task:offer:attempt:1",
            deadline_at=NOW + timedelta(minutes=5),
        )

    class RejectingEnqueueRepository(FakeRepository):
        async def enqueue(self, command: PendingTaskCommand) -> TaskCommandRecord:
            raise TaskCommandDeliveryRejected

    class FailingEnqueueRepository(FakeRepository):
        async def enqueue(self, command: PendingTaskCommand) -> TaskCommandRecord:
            raise RuntimeError("private")

    for repository_type, error in (
        (RejectingEnqueueRepository, TaskCommandDeliveryRejected),
        (FailingEnqueueRepository, TaskCommandDeliveryUnavailable),
    ):
        ids = iter((MESSAGE_ID, CORRELATION_ID))
        failing = TaskCommandDeliveryService(
            repository=repository_type(),
            registry=FakeRegistry(),
            clock=MutableClock(),
            id_source=ids.__next__,
        )
        with pytest.raises(error):
            await failing.enqueue(
                installation_id=INSTALLATION_ID,
                task_id=TASK_ID,
                execution_attempt_id=ATTEMPT_ID,
                sequence=1,
                command_type=TaskCommandType.TASK_OFFER,
                idempotency_key="task:offer:attempt:1",
                deadline_at=NOW + timedelta(minutes=5),
            )


@pytest.mark.asyncio
async def test_dispatch_repository_failures_and_invalid_claims_fail_closed() -> None:
    class FailingRepository(FakeRepository):
        failure: str = ""

        async def expire_due(self, **values: object) -> int:
            if self.failure == "expire_rejected":
                raise TaskCommandDeliveryRejected
            if self.failure == "expire_unavailable":
                raise RuntimeError("private")
            if self.failure == "expire_invalid":
                return -1
            return await super().expire_due(**values)  # type: ignore[arg-type]

        async def claim_next(self, **values: object) -> TaskCommandRecord | None:
            if self.failure == "claim_rejected":
                raise TaskCommandDeliveryRejected
            if self.failure == "claim_unavailable":
                raise RuntimeError("private")
            claimed = await super().claim_next(**values)  # type: ignore[arg-type]
            if self.failure == "claim_invalid":
                return replace(
                    TaskCommandRecord.from_pending(pending_command()),
                    status=TaskCommandStatus.DELIVERED,
                )
            return claimed

        async def mark_delivered(self, **values: object) -> TaskCommandRecord:
            if self.failure == "mark_rejected":
                raise TaskCommandDeliveryRejected
            if self.failure == "mark_unavailable":
                raise RuntimeError("private")
            return await super().mark_delivered(**values)  # type: ignore[arg-type]

        async def release_for_retry(self, **values: object) -> TaskCommandRecord:
            if self.failure == "release_unavailable":
                raise RuntimeError("private")
            return await super().release_for_retry(**values)  # type: ignore[arg-type]

    expected: tuple[tuple[str, type[Exception]], ...] = (
        ("expire_rejected", TaskCommandDeliveryRejected),
        ("expire_unavailable", TaskCommandDeliveryUnavailable),
        ("expire_invalid", TaskCommandDeliveryRejected),
        ("claim_rejected", TaskCommandDeliveryRejected),
        ("claim_unavailable", TaskCommandDeliveryUnavailable),
        ("claim_invalid", TaskCommandDeliveryRejected),
        ("mark_rejected", TaskCommandDeliveryRejected),
        ("mark_unavailable", TaskCommandDeliveryUnavailable),
    )
    for failure, error in expected:
        repository = FailingRepository(TaskCommandRecord.from_pending(pending_command()))
        repository.failure = failure
        with pytest.raises(error):
            await TaskCommandDeliveryService(
                repository=repository,
                registry=FakeRegistry(),
                clock=MutableClock(),
            ).dispatch_current(
                installation_id=INSTALLATION_ID,
                executor_id=EXECUTOR_ID,
                connection_id=CONNECTION_ID,
            )

    repository = FailingRepository(TaskCommandRecord.from_pending(pending_command()))
    repository.failure = "release_unavailable"
    registry = FakeRegistry()
    registry.fail = True
    with pytest.raises(TaskCommandDeliveryUnavailable):
        await TaskCommandDeliveryService(
            repository=repository,
            registry=registry,
            clock=MutableClock(),
        ).dispatch_current(
            installation_id=INSTALLATION_ID,
            executor_id=EXECUTOR_ID,
            connection_id=CONNECTION_ID,
        )


@pytest.mark.asyncio
async def test_acknowledgement_failure_expiry_and_wire_validation_are_closed() -> None:
    class AcknowledgementRepository(FakeRepository):
        failure: str = ""

        async def acknowledge(self, **values: object) -> TaskCommandRecord:
            if self.failure == "rejected":
                raise TaskCommandDeliveryRejected
            if self.failure == "unavailable":
                raise RuntimeError("private")
            if self.failure == "expired":
                assert self.command is not None
                return replace(self.command, status=TaskCommandStatus.EXPIRED)
            return await super().acknowledge(**values)  # type: ignore[arg-type]

    service = TaskCommandDeliveryService(
        repository=AcknowledgementRepository(),
        registry=FakeRegistry(),
        clock=MutableClock(),
    )
    with pytest.raises(TaskCommandDeliveryRejected):
        await service.acknowledge(object())

    for failure, error in (
        ("rejected", TaskCommandDeliveryRejected),
        ("unavailable", TaskCommandDeliveryUnavailable),
        ("expired", TaskCommandDeliveryRejected),
    ):
        repository = AcknowledgementRepository(
            replace(
                TaskCommandRecord.from_pending(pending_command()),
                status=TaskCommandStatus.DELIVERED,
                next_delivery_at=None,
                delivered_at=NOW,
            )
        )
        repository.failure = failure
        with pytest.raises(error):
            await TaskCommandDeliveryService(
                repository=repository,
                registry=FakeRegistry(),
                clock=MutableClock(),
            ).acknowledge(response())

    invalid_wire_command = replace(
        TaskCommandRecord.from_pending(pending_command()),
        idempotency_key="private invalid key",
    )
    with pytest.raises(TaskCommandDeliveryRejected):
        delivery_module._command_wire(
            invalid_wire_command,
            executor_id=EXECUTOR_ID,
            sent_at=NOW,
        )

    with pytest.raises(TaskCommandDeliveryRejected):
        delivery_module._command_wire(
            replace(
                TaskCommandRecord.from_pending(pending_command()),
                task_event_sequence_baseline=None,
            ),
            executor_id=EXECUTOR_ID,
            sent_at=NOW,
        )

    discovery_payload = DouyinDiscoveryCommandPayload.model_validate(
        {
            "discovery_version": "douyin.discovery.v1",
            "keyword": "自动化运营",
            "target_limit": 10,
            "page_revision": 1,
        }
    )
    for inconsistent in (
        replace(
            TaskCommandRecord.from_pending(pending_command()),
            command_type=TaskCommandType.TASK_DISCOVER,
        ),
        replace(
            TaskCommandRecord.from_pending(pending_command()),
            discovery_payload=discovery_payload,
        ),
    ):
        with pytest.raises(TaskCommandDeliveryRejected):
            delivery_module._command_wire(
                inconsistent,
                executor_id=EXECUTOR_ID,
                sent_at=NOW,
            )

    discovery_wire = delivery_module._command_wire(
        replace(
            TaskCommandRecord.from_pending(pending_command()),
            command_type=TaskCommandType.TASK_DISCOVER,
            discovery_payload=discovery_payload,
        ),
        executor_id=EXECUTOR_ID,
        sent_at=NOW,
    )
    assert json.loads(discovery_wire)["message_type"] == "task.discover"


@pytest.mark.asyncio
async def test_dispatch_honours_the_bounded_batch_limit() -> None:
    class EndlessRepository(FakeRepository):
        async def claim_next(self, **values: object) -> TaskCommandRecord:
            now = values["now"]
            lease = values["lease_expires_at"]
            assert isinstance(now, datetime)
            assert isinstance(lease, datetime)
            base = TaskCommandRecord.from_pending(pending_command())
            self.command = replace(
                base,
                status=TaskCommandStatus.IN_FLIGHT,
                revision=2,
                delivery_attempts=1,
                next_delivery_at=None,
                lease_expires_at=lease,
                updated_at=now,
            )
            return self.command

    result = await TaskCommandDeliveryService(
        repository=EndlessRepository(),
        registry=FakeRegistry(),
        clock=MutableClock(),
        maximum_batch_size=1,
    ).dispatch_current(
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
        connection_id=CONNECTION_ID,
    )
    assert result.delivered == 1
