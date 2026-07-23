"""Aliyun IMS polling reconciliation, cooperative cancel and crash recovery.

VE-06 consumes the officially documented `GetMediaProducingJob` query
(status vocabulary Init/Queuing/Processing/Success/Failed) and advances the
provider-neutral `EditingJob` exclusively through the legal transitions of
`EditingJobStateMachine`:

- duplicate or out-of-order vendor reports are idempotent and never regress a
  local status (a terminal intent short-circuits before any network call);
- an unknown vendor status or an exhausted poll/transient budget settles the
  job as `outcome_uncertain` instead of guessing a terminal result;
- ICE 2020-11-09 exposes no cancel action for media producing jobs (verified
  against the official RAM action list), so cancellation is a local
  cooperative annotation: the job turns `cancelling`, reconciliation keeps
  running and may still converge to the real `succeeded`/`failed` terminal;
  `cancelled` is therefore unreachable for this adapter;
- recovery scans the durable intent store after a restart: DISPATCHED intents
  resume polling, PREPARED intents await an idempotent resubmission by their
  caller, UNCERTAIN intents are never replayed automatically.

All Aliyun DTOs stay inside this adapter and never enter the provider-neutral
editing domain. Error messages are fixed and never carry job payloads,
credentials or user content.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum, unique
from types import MappingProxyType
from typing import Final, Never, Protocol, final

from automation_tool.control_plane.domain.aliyun_ims_editing_callback import (
    AliyunProduceMediaCompleteEvent,
)
from automation_tool.control_plane.domain.aliyun_ims_editing_provider import (
    AliyunEditingIntent,
    AliyunEditingIntentState,
    AliyunEditingIntentStore,
    AliyunImsEditingProvider,
    AliyunImsTransportFailure,
)
from automation_tool.control_plane.domain.aliyun_ims_editing_staging import (
    AliyunImsEditingStagingContract,
    AliyunImsRegion,
)
from automation_tool.control_plane.domain.resource_ids import ArtifactId
from automation_tool.control_plane.domain.video_editing import (
    EditingFailureCode,
    EditingJobId,
    EditingJobStateMachine,
    EditingJobStatus,
)
from automation_tool.control_plane.domain.video_editing_provider import (
    EditingProviderErrorCode,
    EditingProviderFailure,
    EditingProviderJobSnapshot,
)

QUERY_ACTION: Final = "GetMediaProducingJob"

_VENDOR_JOB_ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9-]{8,128}$")
_STATUS_TOKEN_PATTERN: Final = re.compile(r"^[A-Za-z][A-Za-z0-9]{0,63}$")

_MAX_POLLS_LIMIT: Final = 10_000
_MAX_TRANSIENT_FAILURE_LIMIT: Final = 10_000
_MAX_POLL_INTERVAL_SECONDS: Final = 3_600.0


class InvalidAliyunImsEditingReconciliationModel(ValueError):
    """An Aliyun IMS editing reconciliation value is invalid."""

    def __init__(self) -> None:
        super().__init__("Aliyun IMS editing reconciliation value is invalid")


@final
class AliyunImsJobNotFound(Exception):
    """The gateway definitively reported the media producing job as unknown."""

    def __init__(self) -> None:
        super().__init__("Aliyun IMS media producing job was not found")


def _reject() -> Never:
    raise InvalidAliyunImsEditingReconciliationModel


def _fail(code: EditingProviderErrorCode) -> Never:
    raise EditingProviderFailure(code)


@unique
class AliyunMediaProducingStatus(StrEnum):
    """The officially documented GetMediaProducingJob status vocabulary."""

    INIT = "Init"
    QUEUING = "Queuing"
    PROCESSING = "Processing"
    SUCCESS = "Success"
    FAILED = "Failed"


_KNOWN_STATUSES: Final[Mapping[str, AliyunMediaProducingStatus]] = MappingProxyType(
    {status.value: status for status in AliyunMediaProducingStatus}
)

# The polling API documents `Failed` while the ProduceMediaComplete callback
# documents `Fail`; both are definitive vendor failure reports.
_FAILURE_TOKENS: Final = frozenset({"Failed", "Fail"})
_SUCCESS_TOKENS: Final = frozenset({"Success"})
_RUNNING_TOKENS: Final = frozenset({"Processing"})
_PENDING_TOKENS: Final = frozenset({"Init", "Queuing"})


@final
@dataclass(frozen=True, slots=True)
class AliyunGetMediaProducingRequest:
    """One deterministic, fully assembled GetMediaProducingJob query."""

    endpoint: str
    api_version: str
    action: str
    region: AliyunImsRegion
    vendor_job_id: str

    def __post_init__(self) -> None:
        if (
            type(self.endpoint) is not str
            or not isinstance(self.region, AliyunImsRegion)
            or self.endpoint != f"ice.{self.region.value}.aliyuncs.com"
            or self.api_version != "2020-11-09"
            or self.action != QUERY_ACTION
            or type(self.vendor_job_id) is not str
            or _VENDOR_JOB_ID_PATTERN.fullmatch(self.vendor_job_id) is None
        ):
            _reject()

    def query_parameters(self) -> Mapping[str, str]:
        """Return the documented RPC query parameters for this request."""
        return MappingProxyType({"JobId": self.vendor_job_id})


def build_get_media_producing_request(
    *,
    contract: AliyunImsEditingStagingContract,
    region: AliyunImsRegion,
    vendor_job_id: str,
) -> AliyunGetMediaProducingRequest:
    """Assemble one GetMediaProducingJob query against the contract endpoint."""
    if not isinstance(contract, AliyunImsEditingStagingContract) or not isinstance(
        region, AliyunImsRegion
    ):
        _reject()
    return AliyunGetMediaProducingRequest(
        endpoint=contract.endpoints[region],
        api_version=contract.api_version,
        action=QUERY_ACTION,
        region=region,
        vendor_job_id=vendor_job_id,
    )


@final
@dataclass(frozen=True, slots=True)
class AliyunMediaProducingJobReport:
    """One vendor status report; unknown tokens are carried, never guessed."""

    vendor_job_id: str
    status_token: str

    def __post_init__(self) -> None:
        if (
            type(self.vendor_job_id) is not str
            or _VENDOR_JOB_ID_PATTERN.fullmatch(self.vendor_job_id) is None
            or type(self.status_token) is not str
            or _STATUS_TOKEN_PATTERN.fullmatch(self.status_token) is None
        ):
            _reject()

    def known_status(self) -> AliyunMediaProducingStatus | None:
        """Return the documented status, or None for an undocumented token."""
        return _KNOWN_STATUSES.get(self.status_token)


class AliyunImsQueryTransport(Protocol):
    """Network port that performs exactly one GetMediaProducingJob call."""

    async def get_media_producing_job(
        self, request: AliyunGetMediaProducingRequest
    ) -> AliyunMediaProducingJobReport:
        """Query once; raise `AliyunImsTransportFailure` or `AliyunImsJobNotFound`."""
        ...


class AliyunEditingOutputRegistrar(Protocol):
    """Port registering the confirmed outputs of one succeeded editing job.

    VE-07 replaces the minimal implementation with real finished-video
    download and artifact registration; VE-06 only needs stable identifiers.
    """

    async def register_confirmed_output(
        self, editing_job_id: EditingJobId
    ) -> tuple[ArtifactId, ...]:
        """Return at least one artifact identifier for the confirmed output."""
        ...


@final
class NewArtifactAliyunEditingOutputRegistrar:
    """Minimal registrar allocating one fresh artifact per confirmed success."""

    __slots__ = ()

    async def register_confirmed_output(
        self, editing_job_id: EditingJobId
    ) -> tuple[ArtifactId, ...]:
        if not isinstance(editing_job_id, EditingJobId):
            _reject()
        return (ArtifactId.new(),)


@final
@dataclass(frozen=True, slots=True)
class AliyunEditingReconciliationPolicy:
    """Bounded polling budget; exhaustion settles as `outcome_uncertain`."""

    max_polls: int
    transient_failure_limit: int
    poll_interval_seconds: float

    def __post_init__(self) -> None:
        if (
            type(self.max_polls) is not int
            or not 1 <= self.max_polls <= _MAX_POLLS_LIMIT
            or type(self.transient_failure_limit) is not int
            or not 1 <= self.transient_failure_limit <= _MAX_TRANSIENT_FAILURE_LIMIT
            or type(self.poll_interval_seconds) not in {int, float}
            or not 0 <= self.poll_interval_seconds <= _MAX_POLL_INTERVAL_SECONDS
        ):
            _reject()


@final
@dataclass(frozen=True, slots=True)
class AliyunEditingRecoveryReport:
    """Restart classification of every persisted submission intent."""

    resumable: tuple[EditingJobId, ...]
    awaiting_resubmission: tuple[EditingJobId, ...]
    uncertain: tuple[EditingJobId, ...]
    settled: tuple[EditingJobId, ...]

    def __post_init__(self) -> None:
        for bucket in (self.resumable, self.awaiting_resubmission, self.uncertain, self.settled):
            if not isinstance(bucket, tuple) or any(
                not isinstance(job_id, EditingJobId) for job_id in bucket
            ):
                _reject()


async def _default_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


@final
class AliyunImsEditingReconciler:
    """Drive persisted editing intents to their real terminal states.

    The reconciler is the only VE-06 writer: it consumes vendor reports from
    polling or parsed callbacks and forwards them into the provider's
    `record_*` entry points, which enforce the legal state machine.
    """

    __slots__ = (
        "_contract",
        "_intent_store",
        "_policy",
        "_provider",
        "_region",
        "_registrar",
        "_sleep",
        "_transport",
    )

    def __init__(
        self,
        *,
        provider: AliyunImsEditingProvider,
        intent_store: AliyunEditingIntentStore,
        transport: AliyunImsQueryTransport,
        contract: AliyunImsEditingStagingContract,
        region: AliyunImsRegion,
        registrar: AliyunEditingOutputRegistrar,
        policy: AliyunEditingReconciliationPolicy,
        sleep: Callable[[float], Awaitable[None]] = _default_sleep,
    ) -> None:
        if (
            not isinstance(provider, AliyunImsEditingProvider)
            or not isinstance(contract, AliyunImsEditingStagingContract)
            or not isinstance(region, AliyunImsRegion)
            or not isinstance(policy, AliyunEditingReconciliationPolicy)
        ):
            _reject()
        self._provider = provider
        self._intent_store = intent_store
        self._transport = transport
        self._contract = contract
        self._region = region
        self._registrar = registrar
        self._policy = policy
        self._sleep = sleep

    async def poll_once(self, editing_job_id: EditingJobId) -> EditingProviderJobSnapshot:
        """Perform at most one vendor query and apply its report idempotently."""
        intent = await self._settled_intent(editing_job_id)
        if intent.state is AliyunEditingIntentState.UNCERTAIN or EditingJobStateMachine.is_terminal(
            intent.status
        ):
            return await self._provider.get(editing_job_id)
        if intent.vendor_job_id is None:
            _fail(EditingProviderErrorCode.NOT_FOUND)
        request = build_get_media_producing_request(
            contract=self._contract, region=self._region, vendor_job_id=intent.vendor_job_id
        )
        report = await self._transport.get_media_producing_job(request)
        if report.vendor_job_id != intent.vendor_job_id:
            _fail(EditingProviderErrorCode.PROVIDER_ERROR)
        return await self.apply_vendor_status(editing_job_id, report.status_token)

    async def reconcile_until_terminal(
        self, editing_job_id: EditingJobId
    ) -> EditingProviderJobSnapshot:
        """Poll within the budget until a real terminal state is confirmed.

        Budget exhaustion — too many polls without a terminal report or too
        many failed queries — is classified as `outcome_uncertain`; the
        reconciler never invents a success or failure it did not observe.
        """
        intent = await self._settled_intent(editing_job_id)
        if intent.state is AliyunEditingIntentState.UNCERTAIN or EditingJobStateMachine.is_terminal(
            intent.status
        ):
            return await self._provider.get(editing_job_id)

        transient_failures = 0
        for attempt in range(self._policy.max_polls):
            if attempt:
                await self._sleep(self._policy.poll_interval_seconds)
            try:
                snapshot = await self.poll_once(editing_job_id)
            except (AliyunImsTransportFailure, AliyunImsJobNotFound):
                transient_failures += 1
                if transient_failures >= self._policy.transient_failure_limit:
                    break
                continue
            if EditingJobStateMachine.is_terminal(snapshot.status):
                return snapshot
        return await self._settle_uncertain(editing_job_id)

    async def request_cancel(self, editing_job_id: EditingJobId) -> EditingProviderJobSnapshot:
        """Request cooperative cancellation as a local annotation only.

        ICE 2020-11-09 has no cancel action for media producing jobs, so no
        cloud call is made; reconciliation continues and the job may still
        converge to its real `succeeded`/`failed` terminal state.
        """
        return await self._provider.cancel(editing_job_id)

    async def apply_callback_event(
        self, event: AliyunProduceMediaCompleteEvent
    ) -> EditingProviderJobSnapshot:
        """Apply one parsed callback event through the same idempotent path."""
        if not isinstance(event, AliyunProduceMediaCompleteEvent):
            _reject()
        intent = await self._intent_store.load_by_vendor_job_id(event.vendor_job_id)
        if intent is None or intent.state is AliyunEditingIntentState.PREPARED:
            _fail(EditingProviderErrorCode.NOT_FOUND)
        return await self.apply_vendor_status(intent.editing_job_id, event.status_token)

    async def apply_vendor_status(
        self, editing_job_id: EditingJobId, status_token: str
    ) -> EditingProviderJobSnapshot:
        """Advance the editing job from one vendor status report.

        Reports arriving after a terminal state — duplicates, replays or
        out-of-order stragglers — return the settled snapshot unchanged.
        """
        if type(status_token) is not str or _STATUS_TOKEN_PATTERN.fullmatch(status_token) is None:
            _reject()
        intent = await self._settled_intent(editing_job_id)
        if intent.state is AliyunEditingIntentState.UNCERTAIN or EditingJobStateMachine.is_terminal(
            intent.status
        ):
            return await self._provider.get(editing_job_id)

        if status_token in _PENDING_TOKENS:
            return await self._provider.get(editing_job_id)
        if status_token in _RUNNING_TOKENS:
            if intent.status in {EditingJobStatus.QUEUED, EditingJobStatus.PAUSED}:
                await self._provider.record_running(editing_job_id)
            return await self._provider.get(editing_job_id)
        if status_token in _SUCCESS_TOKENS:
            outputs = await self._registrar.register_confirmed_output(editing_job_id)
            if (
                not isinstance(outputs, tuple)
                or not outputs
                or any(not isinstance(artifact_id, ArtifactId) for artifact_id in outputs)
            ):
                _fail(EditingProviderErrorCode.PROVIDER_ERROR)
            if intent.status in {EditingJobStatus.QUEUED, EditingJobStatus.PAUSED}:
                await self._provider.record_running(editing_job_id)
            await self._provider.record_succeeded(editing_job_id, outputs)
            return await self._provider.get(editing_job_id)
        if status_token in _FAILURE_TOKENS:
            if intent.status is EditingJobStatus.PAUSED:
                await self._provider.record_running(editing_job_id)
            await self._provider.record_failed(editing_job_id, EditingFailureCode.EDITING_FAILED)
            return await self._provider.get(editing_job_id)
        return await self._settle_uncertain(editing_job_id)

    async def recover(self) -> AliyunEditingRecoveryReport:
        """Classify every persisted intent for the restart recovery paths."""
        resumable: list[EditingJobId] = []
        awaiting_resubmission: list[EditingJobId] = []
        uncertain: list[EditingJobId] = []
        settled: list[EditingJobId] = []
        for intent in await self._intent_store.load_all():
            if intent.state is AliyunEditingIntentState.PREPARED:
                awaiting_resubmission.append(intent.editing_job_id)
            elif (
                intent.state is AliyunEditingIntentState.UNCERTAIN
                or intent.status is EditingJobStatus.OUTCOME_UNCERTAIN
            ):
                uncertain.append(intent.editing_job_id)
            elif EditingJobStateMachine.is_terminal(intent.status):
                settled.append(intent.editing_job_id)
            else:
                resumable.append(intent.editing_job_id)
        return AliyunEditingRecoveryReport(
            resumable=tuple(resumable),
            awaiting_resubmission=tuple(awaiting_resubmission),
            uncertain=tuple(uncertain),
            settled=tuple(settled),
        )

    async def _settle_uncertain(self, editing_job_id: EditingJobId) -> EditingProviderJobSnapshot:
        """Settle a job whose outcome cannot be confirmed as `outcome_uncertain`.

        The legal path runs through the state machine: a job that never left
        the queue is first annotated `cancelling` (reconciliation gives up on
        it cooperatively) before the uncertain terminal is recorded.
        """
        intent = await self._settled_intent(editing_job_id)
        if intent.state is AliyunEditingIntentState.UNCERTAIN or EditingJobStateMachine.is_terminal(
            intent.status
        ):
            return await self._provider.get(editing_job_id)
        if intent.status in {EditingJobStatus.QUEUED, EditingJobStatus.PAUSED}:
            await self._provider.cancel(editing_job_id)
        await self._provider.record_outcome_uncertain(editing_job_id)
        return await self._provider.get(editing_job_id)

    async def _settled_intent(self, editing_job_id: EditingJobId) -> AliyunEditingIntent:
        if not isinstance(editing_job_id, EditingJobId):
            _fail(EditingProviderErrorCode.INVALID_INPUT)
        intent = await self._intent_store.load(editing_job_id)
        if intent is None or intent.state is AliyunEditingIntentState.PREPARED:
            _fail(EditingProviderErrorCode.NOT_FOUND)
        return intent


__all__ = [
    "QUERY_ACTION",
    "AliyunEditingOutputRegistrar",
    "AliyunEditingReconciliationPolicy",
    "AliyunEditingRecoveryReport",
    "AliyunGetMediaProducingRequest",
    "AliyunImsEditingReconciler",
    "AliyunImsJobNotFound",
    "AliyunImsQueryTransport",
    "AliyunMediaProducingJobReport",
    "AliyunMediaProducingStatus",
    "InvalidAliyunImsEditingReconciliationModel",
    "NewArtifactAliyunEditingOutputRegistrar",
    "build_get_media_producing_request",
]
