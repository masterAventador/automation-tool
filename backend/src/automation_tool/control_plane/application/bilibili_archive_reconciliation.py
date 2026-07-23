"""Bilibili archive status reconciliation for PB-04.

The service turns the durable PB-03 attempt facts into a settled publishing
outcome by polling the official archive-status query surface:

- ``submitted`` attempts poll the single-archive detail until the platform
  reports the archive as open (published) or returned (rejected);
- ``outcome_uncertain`` attempts — the creation response was lost — enumerate
  the account archive list to decide whether the archive actually exists.  An
  unambiguous match converges onto the real archive; a complete enumeration
  without any candidate is the only path that may settle the loss as failed;
- stale ``dispatched`` admissions left behind by a crash are fenced to
  ``outcome_uncertain`` first and reconciled the same way.

Queries never mutate platform state and this service has no access to the
archive-submission gateway, so no reconciliation path can resubmit.  Query
timeouts, rate limits, and malformed responses only influence the polling
cadence; the durable outcome is monotonic and never regresses once settled.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, Never, Protocol, runtime_checkable

from automation_tool.control_plane.application.bilibili_archive_publishing import (
    BilibiliAccessTokenProvider,
    BilibiliArchivePublishRejected,
    BilibiliArchivePublishUnavailable,
    BilibiliGatewayUnreachable,
    BilibiliPublishAttemptRecord,
    BilibiliPublishClock,
    BilibiliPublishPhase,
    SystemBilibiliPublishClock,
)
from automation_tool.control_plane.domain.bilibili_open_api import (
    ArchiveListPage,
    ArchiveStatusNotification,
    ArchiveStatusSnapshot,
    BilibiliErrorCategory,
    BilibiliOpenApiContract,
    BilibiliPlatformRejection,
    InvalidBilibiliOpenApiMessage,
    parse_archive_view,
    parse_archive_viewlist,
)
from automation_tool.control_plane.domain.video_publishing import (
    PublishFailureCode,
    PublishJobId,
    PublishJobStateMachine,
    PublishJobStatus,
)

_RESOURCE_ID_PATTERN: Final = re.compile(r"^BV[0-9A-Za-z]{10}$")
_LIST_STATUS_FILTER: Final = "all"

_RECONCILABLE_PHASES: Final = frozenset(
    {
        BilibiliPublishPhase.DISPATCHED,
        BilibiliPublishPhase.SUBMITTED,
        BilibiliPublishPhase.OUTCOME_UNCERTAIN,
    }
)


def _reject() -> Never:
    raise BilibiliArchivePublishRejected


class BilibiliReconciliationOutcome(StrEnum):
    """Closed durable reconciliation outcomes for one publish attempt."""

    PENDING = "pending"
    PUBLISHED = "published"
    REJECTED = "rejected"
    FAILED = "failed"


_SETTLED_OUTCOMES: Final = frozenset(
    {
        BilibiliReconciliationOutcome.PUBLISHED,
        BilibiliReconciliationOutcome.REJECTED,
        BilibiliReconciliationOutcome.FAILED,
    }
)

_OUTCOME_TO_JOB_STATUS: Final = {
    BilibiliReconciliationOutcome.PUBLISHED: PublishJobStatus.PUBLISHED,
    BilibiliReconciliationOutcome.REJECTED: PublishJobStatus.REJECTED,
    BilibiliReconciliationOutcome.FAILED: PublishJobStatus.FAILED,
}


def publish_job_target_for(outcome: object) -> PublishJobStatus | None:
    """Map a settled reconciliation outcome onto the legal PublishJob target."""
    if not isinstance(outcome, BilibiliReconciliationOutcome):
        _reject()
    target = _OUTCOME_TO_JOB_STATUS.get(outcome)
    if target is None:
        return None
    # Both ambiguous origins must accept the target; a domain regression here
    # must fail loudly instead of silently emitting an illegal transition.
    for origin in (PublishJobStatus.DISPATCHING, PublishJobStatus.OUTCOME_UNCERTAIN):
        if PublishJobStateMachine.transition(origin, target) is not target:
            raise BilibiliArchivePublishUnavailable  # pragma: no cover - defensive
    return target


class BilibiliReconciliationDecision(StrEnum):
    """Closed decision vocabulary for one reconciliation tick."""

    IN_REVIEW = "in_review"
    PUBLISHED = "published"
    REJECTED = "rejected"
    FAILED_ABSENT = "failed_absent"
    AMBIGUOUS_MATCH = "ambiguous_match"
    STILL_UNCERTAIN = "still_uncertain"
    ARCHIVE_MISSING = "archive_missing"
    RATE_LIMITED = "rate_limited"
    QUERY_UNREACHABLE = "query_unreachable"
    QUERY_REJECTED = "query_rejected"
    ALREADY_SETTLED = "already_settled"


def _validate_utc(value: object) -> None:
    if not isinstance(value, datetime) or value.utcoffset() != UTC.utcoffset(value):
        _reject()


@dataclass(frozen=True, slots=True)
class BilibiliReconciliationPolicy:
    """Polling cadence; every interval is in seconds and strictly positive."""

    poll_interval_seconds: int = 60
    unreachable_retry_seconds: int = 60
    rate_limit_backoff_seconds: int = 300
    attention_interval_seconds: int = 900
    match_ctime_skew_seconds: int = 600
    max_list_pages: int = 100

    def __post_init__(self) -> None:
        for value in (
            self.poll_interval_seconds,
            self.unreachable_retry_seconds,
            self.rate_limit_backoff_seconds,
            self.attention_interval_seconds,
            self.match_ctime_skew_seconds,
            self.max_list_pages,
        ):
            if type(value) is not int or value < 1:
                _reject()


_DECISION_DELAY_FIELD: Final = {
    BilibiliReconciliationDecision.IN_REVIEW: "poll_interval_seconds",
    BilibiliReconciliationDecision.STILL_UNCERTAIN: "poll_interval_seconds",
    BilibiliReconciliationDecision.QUERY_UNREACHABLE: "unreachable_retry_seconds",
    BilibiliReconciliationDecision.QUERY_REJECTED: "unreachable_retry_seconds",
    BilibiliReconciliationDecision.RATE_LIMITED: "rate_limit_backoff_seconds",
    BilibiliReconciliationDecision.AMBIGUOUS_MATCH: "attention_interval_seconds",
    BilibiliReconciliationDecision.ARCHIVE_MISSING: "attention_interval_seconds",
}


@dataclass(frozen=True, slots=True)
class BilibiliReconciliationRecord:
    """Durable reconciliation state for one publish attempt."""

    publish_job_id: PublishJobId
    outcome: BilibiliReconciliationOutcome
    resource_id: str | None
    archive_state: int | None
    failure_code: PublishFailureCode | None
    last_checked_at: datetime | None
    settled_at: datetime | None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if (
            not isinstance(self.publish_job_id, PublishJobId)
            or not isinstance(self.outcome, BilibiliReconciliationOutcome)
            or (
                self.resource_id is not None
                and (
                    not isinstance(self.resource_id, str)
                    or _RESOURCE_ID_PATTERN.fullmatch(self.resource_id) is None
                )
            )
            or (self.archive_state is not None and (type(self.archive_state) is not int))
            or (
                self.failure_code is not None
                and not isinstance(self.failure_code, PublishFailureCode)
            )
        ):
            _reject()
        settled = self.outcome in _SETTLED_OUTCOMES
        if (self.settled_at is not None) is not settled:
            _reject()
        if (self.failure_code is not None) is not (
            self.outcome is BilibiliReconciliationOutcome.FAILED
        ):
            _reject()
        if self.outcome in {
            BilibiliReconciliationOutcome.PUBLISHED,
            BilibiliReconciliationOutcome.REJECTED,
        } and (self.resource_id is None or self.archive_state is None):
            _reject()
        _validate_utc(self.created_at)
        _validate_utc(self.updated_at)
        if self.updated_at < self.created_at:
            _reject()
        for timestamp in (self.last_checked_at, self.settled_at):
            if timestamp is not None:
                _validate_utc(timestamp)


@dataclass(frozen=True, slots=True)
class BilibiliReconciliationResult:
    """Outcome of one reconciliation tick plus the pacing hint."""

    publish_job_id: PublishJobId
    decision: BilibiliReconciliationDecision
    outcome: BilibiliReconciliationOutcome
    publish_job_target: PublishJobStatus | None
    resource_id: str | None
    archive_state: int | None
    next_check_delay_seconds: int


@runtime_checkable
class BilibiliReconciliationAttemptSource(Protocol):
    """Read access to the durable PB-03 attempt facts plus the fencing hook."""

    async def list_reconcilable(self) -> tuple[BilibiliPublishAttemptRecord, ...]: ...

    async def load(self, publish_job_id: PublishJobId) -> BilibiliPublishAttemptRecord | None: ...

    async def record_outcome_uncertain(
        self, publish_job_id: PublishJobId, at: datetime
    ) -> None: ...


@runtime_checkable
class BilibiliReconciliationStore(Protocol):
    """Durable, monotonic reconciliation state per publish attempt."""

    async def ensure_pending(
        self, publish_job_id: PublishJobId, resource_id: str | None, at: datetime
    ) -> BilibiliReconciliationRecord: ...

    async def load(self, publish_job_id: PublishJobId) -> BilibiliReconciliationRecord | None: ...

    async def find_by_resource_id(
        self, resource_id: str
    ) -> BilibiliReconciliationRecord | None: ...

    async def list_unsettled(self) -> tuple[BilibiliReconciliationRecord, ...]: ...

    async def record_checked(
        self, publish_job_id: PublishJobId, archive_state: int | None, at: datetime
    ) -> None: ...

    async def adopt_resource_id(
        self, publish_job_id: PublishJobId, resource_id: str, at: datetime
    ) -> None: ...

    async def settle(
        self,
        publish_job_id: PublishJobId,
        outcome: BilibiliReconciliationOutcome,
        archive_state: int | None,
        failure_code: PublishFailureCode | None,
        at: datetime,
    ) -> bool: ...


@runtime_checkable
class BilibiliArchiveQueryGateway(Protocol):
    """Read-only transport to the archive status query surface.

    The protocol deliberately has no submission method: reconciliation cannot
    resubmit an archive through any code path.
    """

    async def archive_view(self, *, access_token: str, resource_id: str) -> object: ...

    async def archive_viewlist(
        self, *, access_token: str, page_number: int, page_size: int, status_filter: str
    ) -> object: ...


class _QuerySetback(Exception):
    """Internal control flow: the query failed without changing business state."""

    def __init__(self, decision: BilibiliReconciliationDecision) -> None:
        super().__init__(decision.value)
        self.decision = decision


class BilibiliArchiveReconciliationService:
    """Reconcile durable publish attempts against the platform query surface."""

    def __init__(
        self,
        *,
        contract: BilibiliOpenApiContract,
        attempt_source: BilibiliReconciliationAttemptSource,
        store: BilibiliReconciliationStore,
        gateway: BilibiliArchiveQueryGateway,
        token_provider: BilibiliAccessTokenProvider,
        policy: BilibiliReconciliationPolicy | None = None,
        clock: BilibiliPublishClock | None = None,
    ) -> None:
        if (
            not isinstance(contract, BilibiliOpenApiContract)
            or not isinstance(attempt_source, BilibiliReconciliationAttemptSource)
            or not isinstance(store, BilibiliReconciliationStore)
            or not isinstance(gateway, BilibiliArchiveQueryGateway)
            or not isinstance(token_provider, BilibiliAccessTokenProvider)
            or (policy is not None and not isinstance(policy, BilibiliReconciliationPolicy))
        ):
            _reject()
        self._contract = contract
        self._attempts = attempt_source
        self._store = store
        self._gateway = gateway
        self._tokens = token_provider
        self._policy = policy or BilibiliReconciliationPolicy()
        self._clock = clock or SystemBilibiliPublishClock()

    def _now(self) -> datetime:
        try:
            value = self._clock.now()
        except Exception:
            raise BilibiliArchivePublishUnavailable from None
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise BilibiliArchivePublishUnavailable
        return value.astimezone(UTC)

    async def _current_token(self) -> str:
        try:
            token = await self._tokens.current_access_token()
        except Exception:
            raise BilibiliArchivePublishUnavailable from None
        if not isinstance(token, str) or not token:
            raise BilibiliArchivePublishUnavailable
        return token

    async def _refreshed_token(self) -> str:
        try:
            token = await self._tokens.refresh_access_token()
        except Exception:
            raise BilibiliArchivePublishUnavailable from None
        if not isinstance(token, str) or not token:
            raise BilibiliArchivePublishUnavailable
        return token

    async def _store_call(self, awaitable: object) -> object:
        try:
            return await awaitable  # type: ignore[misc]
        except BilibiliArchivePublishRejected:
            raise
        except Exception:
            raise BilibiliArchivePublishUnavailable from None

    def _delay_for(self, decision: BilibiliReconciliationDecision) -> int:
        field = _DECISION_DELAY_FIELD.get(decision)
        if field is None:
            return 0
        return int(getattr(self._policy, field))

    def _result(
        self,
        publish_job_id: PublishJobId,
        decision: BilibiliReconciliationDecision,
        record: BilibiliReconciliationRecord,
    ) -> BilibiliReconciliationResult:
        return BilibiliReconciliationResult(
            publish_job_id=publish_job_id,
            decision=decision,
            outcome=record.outcome,
            publish_job_target=publish_job_target_for(record.outcome),
            resource_id=record.resource_id,
            archive_state=record.archive_state,
            next_check_delay_seconds=self._delay_for(decision),
        )

    async def _load_record(self, publish_job_id: PublishJobId) -> BilibiliReconciliationRecord:
        record = await self._store_call(self._store.load(publish_job_id))
        if record is None:
            _reject()
        if not isinstance(record, BilibiliReconciliationRecord) or (
            record.publish_job_id != publish_job_id
        ):
            raise BilibiliArchivePublishUnavailable
        return record

    async def recover(self) -> tuple[PublishJobId, ...]:
        """Rebuild the reconciliation queue from durable facts after a restart."""
        attempts = await self._store_call(self._attempts.list_reconcilable())
        if not isinstance(attempts, tuple):
            raise BilibiliArchivePublishUnavailable
        queue: list[PublishJobId] = []
        for attempt in attempts:
            if (
                not isinstance(attempt, BilibiliPublishAttemptRecord)
                or attempt.phase not in _RECONCILABLE_PHASES
            ):
                raise BilibiliArchivePublishUnavailable
            if attempt.phase is BilibiliPublishPhase.DISPATCHED:
                # A dispatched admission that survived a restart means the
                # creation response was lost; fence it before reconciling.
                await self._store_call(
                    self._attempts.record_outcome_uncertain(attempt.publish_job_id, self._now())
                )
            record = await self._store_call(
                self._store.ensure_pending(attempt.publish_job_id, attempt.resource_id, self._now())
            )
            if not isinstance(record, BilibiliReconciliationRecord):
                raise BilibiliArchivePublishUnavailable
            if record.outcome is BilibiliReconciliationOutcome.PENDING:
                queue.append(attempt.publish_job_id)
        return tuple(queue)

    async def reconcile_once(self, publish_job_id: PublishJobId) -> BilibiliReconciliationResult:
        """Run one reconciliation tick; queries never change platform state."""
        if not isinstance(publish_job_id, PublishJobId):
            _reject()
        record = await self._load_record(publish_job_id)
        if record.outcome is not BilibiliReconciliationOutcome.PENDING:
            return self._result(
                publish_job_id, BilibiliReconciliationDecision.ALREADY_SETTLED, record
            )
        attempt = await self._store_call(self._attempts.load(publish_job_id))
        if attempt is None:
            _reject()
        if not isinstance(attempt, BilibiliPublishAttemptRecord):
            raise BilibiliArchivePublishUnavailable
        if attempt.phase is BilibiliPublishPhase.DISPATCHED:
            await self._store_call(
                self._attempts.record_outcome_uncertain(publish_job_id, self._now())
            )
        elif attempt.phase not in _RECONCILABLE_PHASES:
            _reject()
        resource_id = record.resource_id or attempt.resource_id
        try:
            if resource_id is None:
                resource_id = await self._locate_uncertain_archive(publish_job_id, attempt)
                if resource_id is None:
                    # Complete enumeration found no candidate: the creation
                    # verifiably never happened, settle the loss as failed.
                    return await self._settle(
                        publish_job_id,
                        BilibiliReconciliationDecision.FAILED_ABSENT,
                        BilibiliReconciliationOutcome.FAILED,
                        archive_state=None,
                        failure_code=PublishFailureCode.PLATFORM_ERROR,
                    )
            snapshot = await self._query_view(resource_id)
        except _QuerySetback as setback:
            current = await self._load_record(publish_job_id)
            if current.outcome is not BilibiliReconciliationOutcome.PENDING:
                return self._result(
                    publish_job_id, BilibiliReconciliationDecision.ALREADY_SETTLED, current
                )
            return self._result(publish_job_id, setback.decision, current)
        if snapshot.state == self._contract.archive_state_open:
            return await self._settle(
                publish_job_id,
                BilibiliReconciliationDecision.PUBLISHED,
                BilibiliReconciliationOutcome.PUBLISHED,
                archive_state=snapshot.state,
                failure_code=None,
            )
        if snapshot.state == self._contract.archive_state_rejected:
            return await self._settle(
                publish_job_id,
                BilibiliReconciliationDecision.REJECTED,
                BilibiliReconciliationOutcome.REJECTED,
                archive_state=snapshot.state,
                failure_code=None,
            )
        await self._store_call(
            self._store.record_checked(publish_job_id, snapshot.state, self._now())
        )
        refreshed = await self._load_record(publish_job_id)
        return self._result(publish_job_id, BilibiliReconciliationDecision.IN_REVIEW, refreshed)

    async def apply_notification(
        self, notification: ArchiveStatusNotification
    ) -> BilibiliReconciliationResult | None:
        """Apply one verified webhook push; unknown archives are ignored."""
        if not isinstance(notification, ArchiveStatusNotification):
            _reject()
        record = await self._store_call(self._store.find_by_resource_id(notification.resource_id))
        if record is None:
            return None
        if not isinstance(record, BilibiliReconciliationRecord):
            raise BilibiliArchivePublishUnavailable
        if record.outcome is not BilibiliReconciliationOutcome.PENDING:
            return self._result(
                record.publish_job_id, BilibiliReconciliationDecision.ALREADY_SETTLED, record
            )
        if notification.state == self._contract.archive_state_open:
            decision = BilibiliReconciliationDecision.PUBLISHED
            outcome = BilibiliReconciliationOutcome.PUBLISHED
        elif notification.state == self._contract.archive_state_rejected:
            decision = BilibiliReconciliationDecision.REJECTED
            outcome = BilibiliReconciliationOutcome.REJECTED
        else:  # pragma: no cover - notification parser locks documented states
            _reject()
        return await self._settle(
            record.publish_job_id,
            decision,
            outcome,
            archive_state=notification.state,
            failure_code=None,
        )

    async def _settle(
        self,
        publish_job_id: PublishJobId,
        decision: BilibiliReconciliationDecision,
        outcome: BilibiliReconciliationOutcome,
        *,
        archive_state: int | None,
        failure_code: PublishFailureCode | None,
    ) -> BilibiliReconciliationResult:
        settled = await self._store_call(
            self._store.settle(publish_job_id, outcome, archive_state, failure_code, self._now())
        )
        record = await self._load_record(publish_job_id)
        if settled is not True:
            return self._result(
                publish_job_id, BilibiliReconciliationDecision.ALREADY_SETTLED, record
            )
        return self._result(publish_job_id, decision, record)

    async def _gateway_payload(self, awaitable: object) -> object:
        try:
            return await awaitable  # type: ignore[misc]
        except BilibiliGatewayUnreachable:
            raise _QuerySetback(BilibiliReconciliationDecision.QUERY_UNREACHABLE) from None
        except Exception:
            raise BilibiliArchivePublishUnavailable from None

    def _setback_for_rejection(
        self, rejection: BilibiliPlatformRejection, *, archive_bound: bool
    ) -> _QuerySetback:
        if rejection.category is BilibiliErrorCategory.RATE_LIMITED:
            return _QuerySetback(BilibiliReconciliationDecision.RATE_LIMITED)
        if archive_bound and rejection.category is BilibiliErrorCategory.ARCHIVE_CONFLICT:
            # The platform denies knowing an archive we hold a receipt for;
            # never auto-fail on this contradiction, ask for attention instead.
            return _QuerySetback(BilibiliReconciliationDecision.ARCHIVE_MISSING)
        return _QuerySetback(BilibiliReconciliationDecision.QUERY_REJECTED)

    async def _query_view(self, resource_id: str) -> ArchiveStatusSnapshot:
        token = await self._current_token()
        payload = await self._gateway_payload(
            self._gateway.archive_view(access_token=token, resource_id=resource_id)
        )
        parsed = self._parse_view(payload)
        if (
            isinstance(parsed, BilibiliPlatformRejection)
            and parsed.category is BilibiliErrorCategory.AUTH_REJECTED
        ):
            token = await self._refreshed_token()
            payload = await self._gateway_payload(
                self._gateway.archive_view(access_token=token, resource_id=resource_id)
            )
            parsed = self._parse_view(payload)
        if isinstance(parsed, BilibiliPlatformRejection):
            raise self._setback_for_rejection(parsed, archive_bound=True)
        if parsed.resource_id != resource_id:
            raise _QuerySetback(BilibiliReconciliationDecision.QUERY_UNREACHABLE)
        return parsed

    def _parse_view(self, payload: object) -> ArchiveStatusSnapshot | BilibiliPlatformRejection:
        try:
            return parse_archive_view(self._contract, payload)
        except InvalidBilibiliOpenApiMessage:
            raise _QuerySetback(BilibiliReconciliationDecision.QUERY_UNREACHABLE) from None

    def _parse_viewlist(self, payload: object) -> ArchiveListPage | BilibiliPlatformRejection:
        try:
            return parse_archive_viewlist(self._contract, payload)
        except InvalidBilibiliOpenApiMessage:
            raise _QuerySetback(BilibiliReconciliationDecision.QUERY_UNREACHABLE) from None

    async def _list_page(self, token: str, page_number: int) -> ArchiveListPage:
        payload = await self._gateway_payload(
            self._gateway.archive_viewlist(
                access_token=token,
                page_number=page_number,
                page_size=self._contract.page_size_max,
                status_filter=_LIST_STATUS_FILTER,
            )
        )
        parsed = self._parse_viewlist(payload)
        if (
            isinstance(parsed, BilibiliPlatformRejection)
            and parsed.category is BilibiliErrorCategory.AUTH_REJECTED
        ):
            token = await self._refreshed_token()
            payload = await self._gateway_payload(
                self._gateway.archive_viewlist(
                    access_token=token,
                    page_number=page_number,
                    page_size=self._contract.page_size_max,
                    status_filter=_LIST_STATUS_FILTER,
                )
            )
            parsed = self._parse_viewlist(payload)
        if isinstance(parsed, BilibiliPlatformRejection):
            raise self._setback_for_rejection(parsed, archive_bound=False)
        return parsed

    def _is_candidate(
        self, attempt: BilibiliPublishAttemptRecord, item: ArchiveStatusSnapshot
    ) -> bool:
        if item.title != attempt.fields.title:
            return False
        dispatched_at = attempt.dispatched_at
        if dispatched_at is None:  # pragma: no cover - reconcilable phases carry it
            return False
        window_start = int(dispatched_at.timestamp()) - self._policy.match_ctime_skew_seconds
        return item.created_at_epoch_seconds >= window_start

    async def _locate_uncertain_archive(
        self, publish_job_id: PublishJobId, attempt: BilibiliPublishAttemptRecord
    ) -> str | None:
        """Enumerate the account archive list to decide whether the lost
        creation actually happened.  Returns the adopted resource id, ``None``
        after a complete enumeration without candidates, and raises a
        :class:`_QuerySetback` whenever absence cannot be proven safely."""
        token = await self._current_token()
        candidates: list[ArchiveStatusSnapshot] = []
        page_number = 1
        expected_total: int | None = None
        seen = 0
        while True:
            if page_number > self._policy.max_list_pages:
                raise _QuerySetback(BilibiliReconciliationDecision.STILL_UNCERTAIN)
            page = await self._list_page(token, page_number)
            if expected_total is None:
                expected_total = page.total
            elif page.total != expected_total:
                # The listing shifted while we were walking it; absence can no
                # longer be proven from this pass.
                raise _QuerySetback(BilibiliReconciliationDecision.STILL_UNCERTAIN)
            if len(page.items) > page.page_size:
                raise _QuerySetback(BilibiliReconciliationDecision.STILL_UNCERTAIN)
            seen += len(page.items)
            candidates.extend(item for item in page.items if self._is_candidate(attempt, item))
            if seen >= expected_total or not page.items:
                break
            page_number += 1
        if seen < (expected_total or 0):
            raise _QuerySetback(BilibiliReconciliationDecision.STILL_UNCERTAIN)
        if not candidates:
            return None
        if len(candidates) > 1:
            raise _QuerySetback(BilibiliReconciliationDecision.AMBIGUOUS_MATCH)
        resource_id = candidates[0].resource_id
        await self._store_call(
            self._store.adopt_resource_id(publish_job_id, resource_id, self._now())
        )
        return resource_id


__all__ = [
    "BilibiliArchiveQueryGateway",
    "BilibiliArchiveReconciliationService",
    "BilibiliReconciliationAttemptSource",
    "BilibiliReconciliationDecision",
    "BilibiliReconciliationOutcome",
    "BilibiliReconciliationPolicy",
    "BilibiliReconciliationRecord",
    "BilibiliReconciliationResult",
    "BilibiliReconciliationStore",
    "publish_job_target_for",
]
