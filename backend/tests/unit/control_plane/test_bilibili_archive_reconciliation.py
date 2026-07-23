"""PB-04: Bilibili archive status reconciliation orchestration tests."""

from __future__ import annotations

import hashlib
import json
from collections import deque
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from automation_tool.control_plane.application.bilibili_archive_publishing import (
    BilibiliArchiveFields,
    BilibiliArchivePublishRejected,
    BilibiliArchivePublishUnavailable,
    BilibiliGatewayUnreachable,
    BilibiliPublishAttemptRecord,
    BilibiliPublishMaterial,
    BilibiliPublishPhase,
    BilibiliUploadType,
)
from automation_tool.control_plane.application.bilibili_archive_reconciliation import (
    BilibiliArchiveReconciliationService,
    BilibiliReconciliationDecision,
    BilibiliReconciliationOutcome,
    BilibiliReconciliationPolicy,
    BilibiliReconciliationRecord,
    publish_job_target_for,
)
from automation_tool.control_plane.domain.bilibili_open_api import (
    load_bilibili_open_api_contract,
    parse_archive_status_notification,
)
from automation_tool.control_plane.domain.video_publishing import (
    PublishFailureCode,
    PublishJobId,
    PublishJobStatus,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
CONTRACT_PATH = REPOSITORY_ROOT / "contracts/publishing/bilibili-open-api.v1.json"
FIXTURE_ROOT = REPOSITORY_ROOT / "contracts/publishing/fixtures/bilibili-open-api-v1"

CONTRACT = load_bilibili_open_api_contract(CONTRACT_PATH)
NOW = datetime(2026, 7, 23, 1, 0, tzinfo=UTC)
DISPATCHED_AT = datetime(2026, 7, 23, 0, 30, tzinfo=UTC)
DISPATCHED_EPOCH = int(DISPATCHED_AT.timestamp())
TITLE = "契约样例一分钟看懂分片上传"
RESOURCE_ID = "BV17B4y1s7R1"
OTHER_RESOURCE_ID = "BV1MW421X7gM"


def _fixture(name: str) -> Any:
    document = json.loads((FIXTURE_ROOT / f"{name}.json").read_text(encoding="utf-8"))
    return json.loads(json.dumps(document["payload"]))


def _error_payload(code: int) -> dict[str, object]:
    return {"code": code, "message": "fixture-error"}


def _archive_item(
    *,
    resource_id: str = RESOURCE_ID,
    title: str = TITLE,
    state: int = -30,
    state_desc: str = "审核中",
    reject_reason: str = "",
    ctime: int = DISPATCHED_EPOCH + 5,
    ptime: int = 0,
) -> dict[str, object]:
    return {
        "resource_id": resource_id,
        "title": title,
        "cover": "https://i1.hdslb.com/bfs/archive/fixture.jpg",
        "tid": 21,
        "no_reprint": 0,
        "desc": "样例描述",
        "tag": "科技,教程",
        "copyright": 1,
        "ctime": ctime,
        "ptime": ptime,
        "addit_info": {
            "state": state,
            "state_desc": state_desc,
            "reject_reason": reject_reason,
        },
        "video_info": {},
    }


def _view_payload(**overrides: Any) -> dict[str, object]:
    return {"code": 0, "message": "0", "ttl": 1, "data": _archive_item(**overrides)}


def _viewlist_payload(
    items: list[dict[str, object]], *, pn: int = 1, ps: int = 50, total: int | None = None
) -> dict[str, object]:
    return {
        "code": 0,
        "message": "0",
        "ttl": 1,
        "data": {
            "list": items,
            "page": {"pn": pn, "ps": ps, "total": len(items) if total is None else total},
        },
    }


def attempt_record(
    *,
    phase: BilibiliPublishPhase,
    publish_job_id: PublishJobId | None = None,
    resource_id: str | None = None,
    failure_code: PublishFailureCode | None = None,
    platform_error_code: int | None = None,
    title: str = TITLE,
) -> BilibiliPublishAttemptRecord:
    settled = phase in {
        BilibiliPublishPhase.SUBMITTED,
        BilibiliPublishPhase.FAILED,
        BilibiliPublishPhase.OUTCOME_UNCERTAIN,
    }
    dispatched = settled or phase is BilibiliPublishPhase.DISPATCHED
    uploaded = dispatched or phase is BilibiliPublishPhase.VIDEO_UPLOADED
    return BilibiliPublishAttemptRecord(
        publish_job_id=publish_job_id or PublishJobId.new(),
        phase=phase,
        request_digest=hashlib.sha256(b"digest").hexdigest(),
        material=BilibiliPublishMaterial(
            file_name="demo.mp4",
            size_bytes=1024,
            duration_seconds=90,
            sha256=hashlib.sha256(b"material").hexdigest(),
        ),
        fields=BilibiliArchiveFields(
            title=title,
            tid=21,
            tag="科技,教程",
            copyright=1,
            description="样例描述",
            source=None,
            no_reprint=0,
        ),
        upload_type=BilibiliUploadType.SMALL,
        part_size_bytes=0,
        part_count=0,
        has_cover=False,
        upload_token="fixture-upload-token-000000000000" if uploaded else None,
        cover_url=None,
        video_uploaded_at=DISPATCHED_AT - timedelta(minutes=5) if uploaded else None,
        dispatched_at=DISPATCHED_AT if dispatched else None,
        settled_at=DISPATCHED_AT + timedelta(minutes=1) if settled else None,
        resource_id=resource_id,
        failure_code=failure_code,
        platform_error_code=platform_error_code,
        created_at=DISPATCHED_AT - timedelta(minutes=10),
        updated_at=DISPATCHED_AT + timedelta(minutes=1) if settled else DISPATCHED_AT,
    )


class FixedClock:
    def __init__(self, start: datetime = NOW) -> None:
        self.current = start

    def now(self) -> datetime:
        self.current = self.current + timedelta(seconds=1)
        return self.current


class FakeAttemptSource:
    """In-memory mirror of the attempt store facts PB-04 consumes."""

    def __init__(self) -> None:
        self.records: dict[str, BilibiliPublishAttemptRecord] = {}

    def add(self, record: BilibiliPublishAttemptRecord) -> BilibiliPublishAttemptRecord:
        self.records[str(record.publish_job_id)] = record
        return record

    async def list_reconcilable(self) -> tuple[BilibiliPublishAttemptRecord, ...]:
        return tuple(
            record
            for record in sorted(self.records.values(), key=lambda item: item.created_at)
            if record.phase
            in {
                BilibiliPublishPhase.DISPATCHED,
                BilibiliPublishPhase.SUBMITTED,
                BilibiliPublishPhase.OUTCOME_UNCERTAIN,
            }
        )

    async def load(self, publish_job_id: PublishJobId) -> BilibiliPublishAttemptRecord | None:
        return self.records.get(str(publish_job_id))

    async def record_outcome_uncertain(self, publish_job_id: PublishJobId, at: datetime) -> None:
        record = self.records.get(str(publish_job_id))
        if record is None or record.phase is not BilibiliPublishPhase.DISPATCHED:
            raise BilibiliArchivePublishRejected
        self.records[str(publish_job_id)] = replace(
            record,
            phase=BilibiliPublishPhase.OUTCOME_UNCERTAIN,
            settled_at=at,
            updated_at=at,
        )


class FakeReconciliationStore:
    """In-memory mirror of the PostgreSQL reconciliation store semantics."""

    def __init__(self) -> None:
        self.records: dict[str, BilibiliReconciliationRecord] = {}

    async def ensure_pending(
        self, publish_job_id: PublishJobId, resource_id: str | None, at: datetime
    ) -> BilibiliReconciliationRecord:
        key = str(publish_job_id)
        existing = self.records.get(key)
        if existing is not None:
            return existing
        record = BilibiliReconciliationRecord(
            publish_job_id=publish_job_id,
            outcome=BilibiliReconciliationOutcome.PENDING,
            resource_id=resource_id,
            archive_state=None,
            failure_code=None,
            last_checked_at=None,
            settled_at=None,
            created_at=at,
            updated_at=at,
        )
        self.records[key] = record
        return record

    async def load(self, publish_job_id: PublishJobId) -> BilibiliReconciliationRecord | None:
        return self.records.get(str(publish_job_id))

    async def find_by_resource_id(self, resource_id: str) -> BilibiliReconciliationRecord | None:
        for record in self.records.values():
            if record.resource_id == resource_id:
                return record
        return None

    async def list_unsettled(self) -> tuple[BilibiliReconciliationRecord, ...]:
        return tuple(
            record
            for record in sorted(self.records.values(), key=lambda item: item.created_at)
            if record.outcome is BilibiliReconciliationOutcome.PENDING
        )

    async def record_checked(
        self, publish_job_id: PublishJobId, archive_state: int | None, at: datetime
    ) -> None:
        record = self.records.get(str(publish_job_id))
        if record is None or record.outcome is not BilibiliReconciliationOutcome.PENDING:
            raise BilibiliArchivePublishRejected
        self.records[str(publish_job_id)] = replace(
            record, archive_state=archive_state, last_checked_at=at, updated_at=at
        )

    async def adopt_resource_id(
        self, publish_job_id: PublishJobId, resource_id: str, at: datetime
    ) -> None:
        record = self.records.get(str(publish_job_id))
        if (
            record is None
            or record.outcome is not BilibiliReconciliationOutcome.PENDING
            or record.resource_id is not None
        ):
            raise BilibiliArchivePublishRejected
        self.records[str(publish_job_id)] = replace(record, resource_id=resource_id, updated_at=at)

    async def settle(
        self,
        publish_job_id: PublishJobId,
        outcome: BilibiliReconciliationOutcome,
        archive_state: int | None,
        failure_code: PublishFailureCode | None,
        at: datetime,
    ) -> bool:
        record = self.records.get(str(publish_job_id))
        if record is None:
            raise BilibiliArchivePublishRejected
        if record.outcome is not BilibiliReconciliationOutcome.PENDING:
            return False
        self.records[str(publish_job_id)] = replace(
            record,
            outcome=outcome,
            archive_state=archive_state,
            failure_code=failure_code,
            last_checked_at=at,
            settled_at=at,
            updated_at=at,
        )
        return True


class ScriptedQueryGateway:
    """Query-only gateway double; it cannot submit archives by construction."""

    def __init__(self) -> None:
        self.scripts: dict[str, deque[object]] = {}
        self.calls: list[tuple[str, dict[str, object]]] = []

    def script(self, method: str, *outcomes: object) -> None:
        self.scripts.setdefault(method, deque()).extend(outcomes)

    def call_count(self, method: str) -> int:
        return sum(1 for name, _ in self.calls if name == method)

    def _next(self, method: str, call: dict[str, object]) -> object:
        self.calls.append((method, call))
        queue = self.scripts.get(method)
        if not queue:
            raise AssertionError(f"gateway method {method} was not scripted")
        outcome = queue.popleft()
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    async def archive_view(self, *, access_token: str, resource_id: str) -> object:
        return self._next(
            "archive_view", {"access_token": access_token, "resource_id": resource_id}
        )

    async def archive_viewlist(
        self, *, access_token: str, page_number: int, page_size: int, status_filter: str
    ) -> object:
        return self._next(
            "archive_viewlist",
            {
                "access_token": access_token,
                "page_number": page_number,
                "page_size": page_size,
                "status_filter": status_filter,
            },
        )


class FakeTokenProvider:
    def __init__(self, tokens: list[str] | None = None) -> None:
        self.tokens = tokens or ["fixture-access-token-000000000001"]
        self.index = 0
        self.refresh_calls = 0

    async def current_access_token(self) -> str:
        return self.tokens[self.index]

    async def refresh_access_token(self) -> str:
        self.refresh_calls += 1
        if self.index + 1 < len(self.tokens):
            self.index += 1
        return self.tokens[self.index]


def make_service(
    *,
    policy: BilibiliReconciliationPolicy | None = None,
) -> tuple[
    BilibiliArchiveReconciliationService,
    FakeAttemptSource,
    FakeReconciliationStore,
    ScriptedQueryGateway,
    FakeTokenProvider,
]:
    attempts = FakeAttemptSource()
    store = FakeReconciliationStore()
    gateway = ScriptedQueryGateway()
    provider = FakeTokenProvider()
    service = BilibiliArchiveReconciliationService(
        contract=CONTRACT,
        attempt_source=attempts,
        store=store,
        gateway=gateway,
        token_provider=provider,
        policy=policy,
        clock=FixedClock(),
    )
    return service, attempts, store, gateway, provider


POLICY = BilibiliReconciliationPolicy()


async def _recovered_submitted(
    service: BilibiliArchiveReconciliationService,
    attempts: FakeAttemptSource,
) -> PublishJobId:
    record = attempts.add(
        attempt_record(phase=BilibiliPublishPhase.SUBMITTED, resource_id=RESOURCE_ID)
    )
    await service.recover()
    return record.publish_job_id


async def _recovered_uncertain(
    service: BilibiliArchiveReconciliationService,
    attempts: FakeAttemptSource,
) -> PublishJobId:
    record = attempts.add(attempt_record(phase=BilibiliPublishPhase.OUTCOME_UNCERTAIN))
    await service.recover()
    return record.publish_job_id


class TestRecovery:
    @pytest.mark.asyncio
    async def test_recover_enqueues_submitted_and_uncertain_and_fences_dispatched(self) -> None:
        service, attempts, store, _, _ = make_service()
        submitted = attempts.add(
            attempt_record(phase=BilibiliPublishPhase.SUBMITTED, resource_id=RESOURCE_ID)
        )
        dispatched = attempts.add(attempt_record(phase=BilibiliPublishPhase.DISPATCHED))
        uncertain = attempts.add(attempt_record(phase=BilibiliPublishPhase.OUTCOME_UNCERTAIN))
        attempts.add(attempt_record(phase=BilibiliPublishPhase.PREPARED))
        attempts.add(
            attempt_record(
                phase=BilibiliPublishPhase.FAILED,
                failure_code=PublishFailureCode.INVALID_INPUT,
                platform_error_code=123024,
            )
        )

        queue = await service.recover()

        assert set(queue) == {
            submitted.publish_job_id,
            dispatched.publish_job_id,
            uncertain.publish_job_id,
        }
        fenced = attempts.records[str(dispatched.publish_job_id)]
        assert fenced.phase is BilibiliPublishPhase.OUTCOME_UNCERTAIN
        submitted_row = await store.load(submitted.publish_job_id)
        assert submitted_row is not None
        assert submitted_row.resource_id == RESOURCE_ID
        assert submitted_row.outcome is BilibiliReconciliationOutcome.PENDING
        uncertain_row = await store.load(uncertain.publish_job_id)
        assert uncertain_row is not None
        assert uncertain_row.resource_id is None

    @pytest.mark.asyncio
    async def test_recover_is_idempotent_and_skips_settled_rows(self) -> None:
        service, attempts, store, gateway, _ = make_service()
        job_id = await _recovered_submitted(service, attempts)
        gateway.script("archive_view", _view_payload(state=0, ptime=DISPATCHED_EPOCH + 60))
        await service.reconcile_once(job_id)

        queue = await service.recover()

        assert queue == ()
        row = await store.load(job_id)
        assert row is not None
        assert row.outcome is BilibiliReconciliationOutcome.PUBLISHED


class TestSubmittedReconciliation:
    @pytest.mark.asyncio
    async def test_in_review_state_keeps_pending_and_polls_again(self) -> None:
        service, attempts, store, gateway, _ = make_service()
        job_id = await _recovered_submitted(service, attempts)
        gateway.script("archive_view", _view_payload(state=-30))

        result = await service.reconcile_once(job_id)

        assert result.decision is BilibiliReconciliationDecision.IN_REVIEW
        assert result.outcome is BilibiliReconciliationOutcome.PENDING
        assert result.publish_job_target is None
        assert result.archive_state == -30
        assert result.next_check_delay_seconds == POLICY.poll_interval_seconds
        row = await store.load(job_id)
        assert row is not None
        assert row.outcome is BilibiliReconciliationOutcome.PENDING
        assert row.archive_state == -30
        assert row.last_checked_at is not None

    @pytest.mark.asyncio
    async def test_open_state_settles_published_with_legal_job_transition(self) -> None:
        service, attempts, store, gateway, _ = make_service()
        job_id = await _recovered_submitted(service, attempts)
        gateway.script("archive_view", _view_payload(state=0, ptime=DISPATCHED_EPOCH + 60))

        result = await service.reconcile_once(job_id)

        assert result.decision is BilibiliReconciliationDecision.PUBLISHED
        assert result.outcome is BilibiliReconciliationOutcome.PUBLISHED
        assert result.publish_job_target is PublishJobStatus.PUBLISHED
        assert result.resource_id == RESOURCE_ID
        assert result.next_check_delay_seconds == 0
        row = await store.load(job_id)
        assert row is not None
        assert row.outcome is BilibiliReconciliationOutcome.PUBLISHED
        assert row.settled_at is not None

    @pytest.mark.asyncio
    async def test_rejected_state_settles_rejected(self) -> None:
        service, attempts, store, gateway, _ = make_service()
        job_id = await _recovered_submitted(service, attempts)
        gateway.script(
            "archive_view",
            _view_payload(state=-2, state_desc="已退回", reject_reason="内容不符合规范"),
        )

        result = await service.reconcile_once(job_id)

        assert result.decision is BilibiliReconciliationDecision.REJECTED
        assert result.outcome is BilibiliReconciliationOutcome.REJECTED
        assert result.publish_job_target is PublishJobStatus.REJECTED
        row = await store.load(job_id)
        assert row is not None
        assert row.outcome is BilibiliReconciliationOutcome.REJECTED
        assert row.archive_state == -2

    @pytest.mark.asyncio
    async def test_settled_row_replays_without_further_queries(self) -> None:
        service, attempts, _store, gateway, _ = make_service()
        job_id = await _recovered_submitted(service, attempts)
        gateway.script("archive_view", _view_payload(state=0, ptime=DISPATCHED_EPOCH + 60))
        await service.reconcile_once(job_id)

        replay = await service.reconcile_once(job_id)

        assert replay.decision is BilibiliReconciliationDecision.ALREADY_SETTLED
        assert replay.outcome is BilibiliReconciliationOutcome.PUBLISHED
        assert replay.publish_job_target is PublishJobStatus.PUBLISHED
        assert replay.next_check_delay_seconds == 0
        assert gateway.call_count("archive_view") == 1

    @pytest.mark.asyncio
    async def test_settled_outcome_never_regresses(self) -> None:
        service, attempts, store, gateway, _ = make_service()
        job_id = await _recovered_submitted(service, attempts)
        gateway.script("archive_view", _view_payload(state=0, ptime=DISPATCHED_EPOCH + 60))
        await service.reconcile_once(job_id)

        settled = await store.settle(
            job_id,
            BilibiliReconciliationOutcome.REJECTED,
            -2,
            None,
            NOW + timedelta(minutes=5),
        )

        assert settled is False
        row = await store.load(job_id)
        assert row is not None
        assert row.outcome is BilibiliReconciliationOutcome.PUBLISHED


class TestQueryFailuresOnlyAffectPacing:
    @pytest.mark.asyncio
    async def test_view_timeout_keeps_business_state(self) -> None:
        service, attempts, store, gateway, _ = make_service()
        job_id = await _recovered_submitted(service, attempts)
        gateway.script("archive_view", BilibiliGatewayUnreachable())

        result = await service.reconcile_once(job_id)

        assert result.decision is BilibiliReconciliationDecision.QUERY_UNREACHABLE
        assert result.outcome is BilibiliReconciliationOutcome.PENDING
        assert result.next_check_delay_seconds == POLICY.unreachable_retry_seconds
        row = await store.load(job_id)
        assert row is not None
        assert row.outcome is BilibiliReconciliationOutcome.PENDING
        assert row.last_checked_at is None

    @pytest.mark.asyncio
    async def test_malformed_view_response_is_pacing_only(self) -> None:
        service, attempts, store, gateway, _ = make_service()
        job_id = await _recovered_submitted(service, attempts)
        gateway.script("archive_view", {"code": 0, "message": "0", "data": {"surprise": 1}})

        result = await service.reconcile_once(job_id)

        assert result.decision is BilibiliReconciliationDecision.QUERY_UNREACHABLE
        row = await store.load(job_id)
        assert row is not None
        assert row.outcome is BilibiliReconciliationOutcome.PENDING

    @pytest.mark.asyncio
    async def test_rate_limited_view_backs_off(self) -> None:
        service, attempts, store, gateway, _ = make_service()
        job_id = await _recovered_submitted(service, attempts)
        gateway.script("archive_view", _error_payload(127306))

        result = await service.reconcile_once(job_id)

        assert result.decision is BilibiliReconciliationDecision.RATE_LIMITED
        assert result.next_check_delay_seconds == POLICY.rate_limit_backoff_seconds
        row = await store.load(job_id)
        assert row is not None
        assert row.outcome is BilibiliReconciliationOutcome.PENDING

    @pytest.mark.asyncio
    async def test_expired_token_refreshes_once_and_retries(self) -> None:
        service, attempts, _store, gateway, provider = make_service()
        provider.tokens = [
            "fixture-access-token-000000000001",
            "fixture-access-token-000000000002",
        ]
        job_id = await _recovered_submitted(service, attempts)
        gateway.script(
            "archive_view",
            _error_payload(127001),
            _view_payload(state=0, ptime=DISPATCHED_EPOCH + 60),
        )

        result = await service.reconcile_once(job_id)

        assert result.decision is BilibiliReconciliationDecision.PUBLISHED
        assert provider.refresh_calls == 1
        tokens_used = [call["access_token"] for name, call in gateway.calls]
        assert tokens_used == [
            "fixture-access-token-000000000001",
            "fixture-access-token-000000000002",
        ]

    @pytest.mark.asyncio
    async def test_auth_rejection_after_refresh_changes_nothing(self) -> None:
        service, attempts, store, gateway, provider = make_service()
        job_id = await _recovered_submitted(service, attempts)
        gateway.script("archive_view", _error_payload(127001), _error_payload(127001))

        result = await service.reconcile_once(job_id)

        assert result.decision is BilibiliReconciliationDecision.QUERY_REJECTED
        assert provider.refresh_calls == 1
        row = await store.load(job_id)
        assert row is not None
        assert row.outcome is BilibiliReconciliationOutcome.PENDING

    @pytest.mark.asyncio
    async def test_archive_not_found_for_submitted_receipt_flags_attention(self) -> None:
        service, attempts, store, gateway, _ = make_service()
        job_id = await _recovered_submitted(service, attempts)
        gateway.script("archive_view", _error_payload(123004))

        result = await service.reconcile_once(job_id)

        assert result.decision is BilibiliReconciliationDecision.ARCHIVE_MISSING
        assert result.outcome is BilibiliReconciliationOutcome.PENDING
        assert result.next_check_delay_seconds == POLICY.attention_interval_seconds
        row = await store.load(job_id)
        assert row is not None
        assert row.outcome is BilibiliReconciliationOutcome.PENDING


class TestUncertainReconciliation:
    @pytest.mark.asyncio
    async def test_matching_archive_is_adopted_and_converges_to_review(self) -> None:
        service, attempts, store, gateway, _ = make_service()
        job_id = await _recovered_uncertain(service, attempts)
        gateway.script("archive_viewlist", _viewlist_payload([_archive_item()]))
        gateway.script("archive_view", _view_payload(state=-30))

        result = await service.reconcile_once(job_id)

        assert result.decision is BilibiliReconciliationDecision.IN_REVIEW
        assert result.resource_id == RESOURCE_ID
        row = await store.load(job_id)
        assert row is not None
        assert row.resource_id == RESOURCE_ID
        assert row.outcome is BilibiliReconciliationOutcome.PENDING

    @pytest.mark.asyncio
    async def test_adopted_archive_can_settle_published(self) -> None:
        service, attempts, store, gateway, _ = make_service()
        job_id = await _recovered_uncertain(service, attempts)
        gateway.script("archive_viewlist", _viewlist_payload([_archive_item()]))
        gateway.script("archive_view", _view_payload(state=0, ptime=DISPATCHED_EPOCH + 60))

        result = await service.reconcile_once(job_id)

        assert result.decision is BilibiliReconciliationDecision.PUBLISHED
        assert result.publish_job_target is PublishJobStatus.PUBLISHED
        row = await store.load(job_id)
        assert row is not None
        assert row.outcome is BilibiliReconciliationOutcome.PUBLISHED
        assert row.resource_id == RESOURCE_ID

    @pytest.mark.asyncio
    async def test_adopted_resource_is_reused_on_the_next_tick(self) -> None:
        service, attempts, _store, gateway, _ = make_service()
        job_id = await _recovered_uncertain(service, attempts)
        gateway.script("archive_viewlist", _viewlist_payload([_archive_item()]))
        gateway.script("archive_view", _view_payload(state=-30))
        await service.reconcile_once(job_id)
        gateway.script("archive_view", _view_payload(state=0, ptime=DISPATCHED_EPOCH + 90))

        result = await service.reconcile_once(job_id)

        assert result.decision is BilibiliReconciliationDecision.PUBLISHED
        assert gateway.call_count("archive_viewlist") == 1

    @pytest.mark.asyncio
    async def test_complete_enumeration_without_match_settles_failed(self) -> None:
        service, attempts, store, gateway, _ = make_service()
        job_id = await _recovered_uncertain(service, attempts)
        first_page = [
            _archive_item(resource_id=OTHER_RESOURCE_ID, title="别的稿件") for _ in range(1)
        ]
        gateway.script(
            "archive_viewlist",
            _viewlist_payload(first_page, pn=1, ps=1, total=2),
            _viewlist_payload(
                [_archive_item(resource_id="BV1Other0002", title="另一稿件")],
                pn=2,
                ps=1,
                total=2,
            ),
        )

        result = await service.reconcile_once(job_id)

        assert result.decision is BilibiliReconciliationDecision.FAILED_ABSENT
        assert result.outcome is BilibiliReconciliationOutcome.FAILED
        assert result.publish_job_target is PublishJobStatus.FAILED
        row = await store.load(job_id)
        assert row is not None
        assert row.outcome is BilibiliReconciliationOutcome.FAILED
        assert row.failure_code is PublishFailureCode.PLATFORM_ERROR
        assert row.resource_id is None

    @pytest.mark.asyncio
    async def test_empty_account_listing_settles_failed(self) -> None:
        service, attempts, _store, gateway, _ = make_service()
        job_id = await _recovered_uncertain(service, attempts)
        gateway.script("archive_viewlist", _viewlist_payload([], total=0))

        result = await service.reconcile_once(job_id)

        assert result.decision is BilibiliReconciliationDecision.FAILED_ABSENT

    @pytest.mark.asyncio
    async def test_title_match_outside_dispatch_window_is_not_a_candidate(self) -> None:
        service, attempts, _store, gateway, _ = make_service()
        job_id = await _recovered_uncertain(service, attempts)
        stale = _archive_item(ctime=DISPATCHED_EPOCH - POLICY.match_ctime_skew_seconds - 10)
        gateway.script("archive_viewlist", _viewlist_payload([stale]))

        result = await service.reconcile_once(job_id)

        assert result.decision is BilibiliReconciliationDecision.FAILED_ABSENT

    @pytest.mark.asyncio
    async def test_multiple_candidates_require_human_attention(self) -> None:
        service, attempts, store, gateway, _ = make_service()
        job_id = await _recovered_uncertain(service, attempts)
        gateway.script(
            "archive_viewlist",
            _viewlist_payload([_archive_item(), _archive_item(resource_id=OTHER_RESOURCE_ID)]),
        )

        result = await service.reconcile_once(job_id)

        assert result.decision is BilibiliReconciliationDecision.AMBIGUOUS_MATCH
        assert result.outcome is BilibiliReconciliationOutcome.PENDING
        assert result.next_check_delay_seconds == POLICY.attention_interval_seconds
        row = await store.load(job_id)
        assert row is not None
        assert row.outcome is BilibiliReconciliationOutcome.PENDING
        assert row.resource_id is None

    @pytest.mark.asyncio
    async def test_listing_failure_mid_enumeration_stays_uncertain(self) -> None:
        service, attempts, store, gateway, _ = make_service()
        job_id = await _recovered_uncertain(service, attempts)
        gateway.script(
            "archive_viewlist",
            _viewlist_payload(
                [_archive_item(resource_id=OTHER_RESOURCE_ID, title="别的稿件")],
                pn=1,
                ps=1,
                total=2,
            ),
            BilibiliGatewayUnreachable(),
        )

        result = await service.reconcile_once(job_id)

        assert result.decision is BilibiliReconciliationDecision.QUERY_UNREACHABLE
        row = await store.load(job_id)
        assert row is not None
        assert row.outcome is BilibiliReconciliationOutcome.PENDING

    @pytest.mark.asyncio
    async def test_inconsistent_totals_stay_uncertain(self) -> None:
        service, attempts, store, gateway, _ = make_service()
        job_id = await _recovered_uncertain(service, attempts)
        gateway.script(
            "archive_viewlist",
            _viewlist_payload(
                [_archive_item(resource_id=OTHER_RESOURCE_ID, title="别的稿件")],
                pn=1,
                ps=1,
                total=2,
            ),
            _viewlist_payload(
                [_archive_item(resource_id="BV1Other0002", title="另一稿件")],
                pn=2,
                ps=1,
                total=3,
            ),
        )

        result = await service.reconcile_once(job_id)

        assert result.decision is BilibiliReconciliationDecision.STILL_UNCERTAIN
        row = await store.load(job_id)
        assert row is not None
        assert row.outcome is BilibiliReconciliationOutcome.PENDING

    @pytest.mark.asyncio
    async def test_rate_limited_listing_backs_off(self) -> None:
        service, attempts, _store, gateway, _ = make_service()
        job_id = await _recovered_uncertain(service, attempts)
        gateway.script("archive_viewlist", _error_payload(127306))

        result = await service.reconcile_once(job_id)

        assert result.decision is BilibiliReconciliationDecision.RATE_LIMITED
        assert result.next_check_delay_seconds == POLICY.rate_limit_backoff_seconds

    @pytest.mark.asyncio
    async def test_reconciliation_only_ever_queries(self) -> None:
        service, attempts, _store, gateway, _ = make_service()
        job_id = await _recovered_uncertain(service, attempts)
        gateway.script("archive_viewlist", _viewlist_payload([_archive_item()]))
        gateway.script("archive_view", _view_payload(state=0, ptime=DISPATCHED_EPOCH + 60))

        await service.reconcile_once(job_id)

        assert {name for name, _ in gateway.calls} <= {"archive_view", "archive_viewlist"}
        assert not hasattr(service, "create_archive")


class TestDispatchedFencing:
    @pytest.mark.asyncio
    async def test_reconcile_fences_a_stale_dispatched_attempt(self) -> None:
        service, attempts, _store, gateway, _ = make_service()
        record = attempts.add(attempt_record(phase=BilibiliPublishPhase.DISPATCHED))
        await service.recover()
        gateway.script("archive_viewlist", _viewlist_payload([], total=0))

        result = await service.reconcile_once(record.publish_job_id)

        assert result.decision is BilibiliReconciliationDecision.FAILED_ABSENT
        fenced = attempts.records[str(record.publish_job_id)]
        assert fenced.phase is BilibiliPublishPhase.OUTCOME_UNCERTAIN


class TestNotifications:
    def _notification(self, event: str, state: int) -> Any:
        payload = _fixture("notification-video-open-valid")
        payload["event"] = event
        payload["content"]["state"] = state
        payload["content"]["resource_id"] = RESOURCE_ID
        return parse_archive_status_notification(CONTRACT, payload)

    @pytest.mark.asyncio
    async def test_video_open_notification_settles_published(self) -> None:
        service, attempts, store, gateway, _ = make_service()
        job_id = await _recovered_submitted(service, attempts)

        result = await service.apply_notification(self._notification("video_open", 0))

        assert result is not None
        assert result.decision is BilibiliReconciliationDecision.PUBLISHED
        assert result.publish_job_target is PublishJobStatus.PUBLISHED
        row = await store.load(job_id)
        assert row is not None
        assert row.outcome is BilibiliReconciliationOutcome.PUBLISHED
        assert gateway.calls == []

    @pytest.mark.asyncio
    async def test_video_fail_notification_settles_rejected(self) -> None:
        service, attempts, store, _gateway, _ = make_service()
        job_id = await _recovered_submitted(service, attempts)

        result = await service.apply_notification(self._notification("video_fail", -2))

        assert result is not None
        assert result.decision is BilibiliReconciliationDecision.REJECTED
        row = await store.load(job_id)
        assert row is not None
        assert row.outcome is BilibiliReconciliationOutcome.REJECTED

    @pytest.mark.asyncio
    async def test_duplicate_delivery_is_idempotent(self) -> None:
        service, attempts, store, _, _ = make_service()
        job_id = await _recovered_submitted(service, attempts)
        await service.apply_notification(self._notification("video_open", 0))

        replay = await service.apply_notification(self._notification("video_open", 0))

        assert replay is not None
        assert replay.decision is BilibiliReconciliationDecision.ALREADY_SETTLED
        row = await store.load(job_id)
        assert row is not None
        assert row.outcome is BilibiliReconciliationOutcome.PUBLISHED

    @pytest.mark.asyncio
    async def test_conflicting_notification_after_settle_does_not_regress(self) -> None:
        service, attempts, store, _, _ = make_service()
        job_id = await _recovered_submitted(service, attempts)
        await service.apply_notification(self._notification("video_open", 0))

        replay = await service.apply_notification(self._notification("video_fail", -2))

        assert replay is not None
        assert replay.decision is BilibiliReconciliationDecision.ALREADY_SETTLED
        row = await store.load(job_id)
        assert row is not None
        assert row.outcome is BilibiliReconciliationOutcome.PUBLISHED

    @pytest.mark.asyncio
    async def test_unknown_resource_is_ignored(self) -> None:
        service, _, _, _, _ = make_service()

        assert await service.apply_notification(self._notification("video_open", 0)) is None

    @pytest.mark.asyncio
    async def test_non_notification_input_is_rejected(self) -> None:
        service, _, _, _, _ = make_service()
        with pytest.raises(BilibiliArchivePublishRejected):
            await service.apply_notification("video_open")  # type: ignore[arg-type]


class TestGuards:
    @pytest.mark.asyncio
    async def test_reconcile_requires_a_recovered_row(self) -> None:
        service, attempts, _, _, _ = make_service()
        record = attempts.add(
            attempt_record(phase=BilibiliPublishPhase.SUBMITTED, resource_id=RESOURCE_ID)
        )
        with pytest.raises(BilibiliArchivePublishRejected):
            await service.reconcile_once(record.publish_job_id)

    @pytest.mark.asyncio
    async def test_reconcile_rejects_unknown_job(self) -> None:
        service, _, _, _, _ = make_service()
        with pytest.raises(BilibiliArchivePublishRejected):
            await service.reconcile_once(PublishJobId.new())

    @pytest.mark.asyncio
    async def test_reconcile_rejects_non_job_id(self) -> None:
        service, _, _, _, _ = make_service()
        with pytest.raises(BilibiliArchivePublishRejected):
            await service.reconcile_once("job")  # type: ignore[arg-type]

    def test_service_rejects_invalid_collaborators(self) -> None:
        attempts = FakeAttemptSource()
        store = FakeReconciliationStore()
        gateway = ScriptedQueryGateway()
        provider = FakeTokenProvider()
        with pytest.raises(BilibiliArchivePublishRejected):
            BilibiliArchiveReconciliationService(
                contract="contract",  # type: ignore[arg-type]
                attempt_source=attempts,
                store=store,
                gateway=gateway,
                token_provider=provider,
            )
        with pytest.raises(BilibiliArchivePublishRejected):
            BilibiliArchiveReconciliationService(
                contract=CONTRACT,
                attempt_source=attempts,
                store=store,
                gateway="gateway",  # type: ignore[arg-type]
                token_provider=provider,
            )

    @pytest.mark.asyncio
    async def test_broken_store_maps_to_unavailable(self) -> None:
        service, attempts, store, gateway, _ = make_service()
        job_id = await _recovered_submitted(service, attempts)
        gateway.script("archive_view", _view_payload(state=-30))

        async def broken_checked(*arguments: object, **keywords: object) -> None:
            raise RuntimeError("storage exploded")

        store.record_checked = broken_checked  # type: ignore[method-assign]
        with pytest.raises(BilibiliArchivePublishUnavailable):
            await service.reconcile_once(job_id)

    def test_policy_rejects_non_positive_intervals(self) -> None:
        with pytest.raises(BilibiliArchivePublishRejected):
            BilibiliReconciliationPolicy(poll_interval_seconds=0)
        with pytest.raises(BilibiliArchivePublishRejected):
            BilibiliReconciliationPolicy(rate_limit_backoff_seconds=-1)
        with pytest.raises(BilibiliArchivePublishRejected):
            BilibiliReconciliationPolicy(max_list_pages=0)

    def test_publish_job_targets_are_state_machine_legal(self) -> None:
        assert (
            publish_job_target_for(BilibiliReconciliationOutcome.PUBLISHED)
            is PublishJobStatus.PUBLISHED
        )
        assert (
            publish_job_target_for(BilibiliReconciliationOutcome.REJECTED)
            is PublishJobStatus.REJECTED
        )
        assert (
            publish_job_target_for(BilibiliReconciliationOutcome.FAILED) is PublishJobStatus.FAILED
        )
        assert publish_job_target_for(BilibiliReconciliationOutcome.PENDING) is None
        with pytest.raises(BilibiliArchivePublishRejected):
            publish_job_target_for("published")

    def test_record_invariants_fail_closed(self) -> None:
        base = BilibiliReconciliationRecord(
            publish_job_id=PublishJobId.new(),
            outcome=BilibiliReconciliationOutcome.PENDING,
            resource_id=None,
            archive_state=None,
            failure_code=None,
            last_checked_at=None,
            settled_at=None,
            created_at=NOW,
            updated_at=NOW,
        )
        assert base.outcome is BilibiliReconciliationOutcome.PENDING
        with pytest.raises(BilibiliArchivePublishRejected):
            replace(base, settled_at=NOW)
        with pytest.raises(BilibiliArchivePublishRejected):
            replace(base, outcome=BilibiliReconciliationOutcome.PUBLISHED)
        with pytest.raises(BilibiliArchivePublishRejected):
            replace(
                base,
                outcome=BilibiliReconciliationOutcome.PUBLISHED,
                settled_at=NOW,
                resource_id=RESOURCE_ID,
            )
        with pytest.raises(BilibiliArchivePublishRejected):
            replace(base, failure_code=PublishFailureCode.PLATFORM_ERROR)
        with pytest.raises(BilibiliArchivePublishRejected):
            replace(base, resource_id="av1234")
        with pytest.raises(BilibiliArchivePublishRejected):
            replace(base, updated_at=NOW - timedelta(seconds=1))


class SettlingThenFailingGateway:
    """Settles the record out of band, then reports the query as unreachable."""

    def __init__(self, store: FakeReconciliationStore, publish_job_id: PublishJobId) -> None:
        self.store = store
        self.publish_job_id = publish_job_id

    async def archive_view(self, *, access_token: str, resource_id: str) -> object:
        del access_token, resource_id
        await self.store.settle(
            self.publish_job_id,
            BilibiliReconciliationOutcome.PUBLISHED,
            0,
            None,
            NOW + timedelta(minutes=1),
        )
        raise BilibiliGatewayUnreachable

    async def archive_viewlist(
        self, *, access_token: str, page_number: int, page_size: int, status_filter: str
    ) -> object:  # pragma: no cover - not used in this double
        del access_token, page_number, page_size, status_filter
        raise BilibiliGatewayUnreachable


class TestDependencyFailureGuards:
    @pytest.mark.asyncio
    async def test_broken_clock_maps_to_unavailable(self) -> None:
        class BrokenClock:
            def now(self) -> datetime:
                raise RuntimeError("clock exploded")

        class NaiveClock:
            def now(self) -> datetime:
                return datetime(2026, 7, 23, 1, 0)

        for clock in (BrokenClock(), NaiveClock()):
            attempts = FakeAttemptSource()
            attempts.add(
                attempt_record(phase=BilibiliPublishPhase.SUBMITTED, resource_id=RESOURCE_ID)
            )
            service = BilibiliArchiveReconciliationService(
                contract=CONTRACT,
                attempt_source=attempts,
                store=FakeReconciliationStore(),
                gateway=ScriptedQueryGateway(),
                token_provider=FakeTokenProvider(),
                clock=clock,
            )
            with pytest.raises(BilibiliArchivePublishUnavailable):
                await service.recover()

    @pytest.mark.asyncio
    async def test_token_provider_failures_map_to_unavailable(self) -> None:
        class BrokenProvider:
            async def current_access_token(self) -> str:
                raise RuntimeError("token store exploded")

            async def refresh_access_token(self) -> str:
                raise RuntimeError("token store exploded")

        class EmptyProvider:
            async def current_access_token(self) -> str:
                return ""

            async def refresh_access_token(self) -> str:
                return ""

        for provider in (BrokenProvider(), EmptyProvider()):
            attempts = FakeAttemptSource()
            store = FakeReconciliationStore()
            record = attempts.add(
                attempt_record(phase=BilibiliPublishPhase.SUBMITTED, resource_id=RESOURCE_ID)
            )
            service = BilibiliArchiveReconciliationService(
                contract=CONTRACT,
                attempt_source=attempts,
                store=store,
                gateway=ScriptedQueryGateway(),
                token_provider=provider,
                clock=FixedClock(),
            )
            await store.ensure_pending(record.publish_job_id, RESOURCE_ID, NOW)
            with pytest.raises(BilibiliArchivePublishUnavailable):
                await service.reconcile_once(record.publish_job_id)

    @pytest.mark.asyncio
    async def test_refresh_failure_during_auth_retry_maps_to_unavailable(self) -> None:
        class RefreshlessProvider:
            async def current_access_token(self) -> str:
                return "fixture-access-token-000000000001"

            async def refresh_access_token(self) -> str:
                raise RuntimeError("refresh exploded")

        attempts = FakeAttemptSource()
        store = FakeReconciliationStore()
        gateway = ScriptedQueryGateway()
        record = attempts.add(
            attempt_record(phase=BilibiliPublishPhase.SUBMITTED, resource_id=RESOURCE_ID)
        )
        service = BilibiliArchiveReconciliationService(
            contract=CONTRACT,
            attempt_source=attempts,
            store=store,
            gateway=gateway,
            token_provider=RefreshlessProvider(),
            clock=FixedClock(),
        )
        await store.ensure_pending(record.publish_job_id, RESOURCE_ID, NOW)
        gateway.script("archive_view", _error_payload(127001))
        with pytest.raises(BilibiliArchivePublishUnavailable):
            await service.reconcile_once(record.publish_job_id)

    @pytest.mark.asyncio
    async def test_store_rejection_passes_through(self) -> None:
        service, attempts, store, gateway, _ = make_service()
        job_id = await _recovered_submitted(service, attempts)
        gateway.script("archive_view", _view_payload(state=-30))

        async def rejecting_checked(*arguments: object, **keywords: object) -> None:
            raise BilibiliArchivePublishRejected

        store.record_checked = rejecting_checked  # type: ignore[method-assign]
        with pytest.raises(BilibiliArchivePublishRejected):
            await service.reconcile_once(job_id)

    @pytest.mark.asyncio
    async def test_mismatched_store_record_maps_to_unavailable(self) -> None:
        service, attempts, store, _, _ = make_service()
        job_id = await _recovered_submitted(service, attempts)
        other = store.records[str(job_id)]
        store.records[str(job_id)] = replace(other, publish_job_id=PublishJobId.new())
        with pytest.raises(BilibiliArchivePublishUnavailable):
            await service.reconcile_once(job_id)

    @pytest.mark.asyncio
    async def test_malformed_attempt_listing_maps_to_unavailable(self) -> None:
        service, attempts, _, _, _ = make_service()

        async def listing_list(*arguments: object) -> object:
            return [attempt_record(phase=BilibiliPublishPhase.SUBMITTED, resource_id=RESOURCE_ID)]

        attempts.list_reconcilable = listing_list  # type: ignore[assignment]
        with pytest.raises(BilibiliArchivePublishUnavailable):
            await service.recover()

        async def listing_junk(*arguments: object) -> object:
            return ("not-an-attempt",)

        attempts.list_reconcilable = listing_junk  # type: ignore[assignment]
        with pytest.raises(BilibiliArchivePublishUnavailable):
            await service.recover()

    @pytest.mark.asyncio
    async def test_malformed_ensure_pending_result_maps_to_unavailable(self) -> None:
        service, attempts, store, _, _ = make_service()
        attempts.add(attempt_record(phase=BilibiliPublishPhase.SUBMITTED, resource_id=RESOURCE_ID))

        async def junk_ensure(*arguments: object, **keywords: object) -> object:
            return "not-a-record"

        store.ensure_pending = junk_ensure  # type: ignore[assignment]
        with pytest.raises(BilibiliArchivePublishUnavailable):
            await service.recover()

    @pytest.mark.asyncio
    async def test_missing_attempt_for_a_recovered_row_is_rejected(self) -> None:
        service, attempts, _store, _, _ = make_service()
        job_id = await _recovered_submitted(service, attempts)
        del attempts.records[str(job_id)]
        with pytest.raises(BilibiliArchivePublishRejected):
            await service.reconcile_once(job_id)

    @pytest.mark.asyncio
    async def test_malformed_attempt_record_maps_to_unavailable(self) -> None:
        service, attempts, _store, _, _ = make_service()
        job_id = await _recovered_submitted(service, attempts)

        async def junk_load(*arguments: object) -> object:
            return "not-an-attempt"

        attempts.load = junk_load  # type: ignore[assignment]
        with pytest.raises(BilibiliArchivePublishUnavailable):
            await service.reconcile_once(job_id)

    @pytest.mark.asyncio
    async def test_non_reconcilable_attempt_phase_is_rejected(self) -> None:
        service, attempts, store, _, _ = make_service()
        record = attempts.add(attempt_record(phase=BilibiliPublishPhase.PREPARED))
        await store.ensure_pending(record.publish_job_id, None, NOW)
        with pytest.raises(BilibiliArchivePublishRejected):
            await service.reconcile_once(record.publish_job_id)

    @pytest.mark.asyncio
    async def test_stale_dispatched_attempt_is_fenced_inside_reconcile(self) -> None:
        service, attempts, store, gateway, _ = make_service()
        record = attempts.add(attempt_record(phase=BilibiliPublishPhase.DISPATCHED))
        await store.ensure_pending(record.publish_job_id, None, NOW)
        gateway.script("archive_viewlist", _viewlist_payload([], total=0))

        result = await service.reconcile_once(record.publish_job_id)

        assert result.decision is BilibiliReconciliationDecision.FAILED_ABSENT
        fenced = attempts.records[str(record.publish_job_id)]
        assert fenced.phase is BilibiliPublishPhase.OUTCOME_UNCERTAIN

    @pytest.mark.asyncio
    async def test_concurrent_settlement_during_a_query_setback_replays(self) -> None:
        attempts = FakeAttemptSource()
        store = FakeReconciliationStore()
        record = attempts.add(
            attempt_record(phase=BilibiliPublishPhase.SUBMITTED, resource_id=RESOURCE_ID)
        )
        service = BilibiliArchiveReconciliationService(
            contract=CONTRACT,
            attempt_source=attempts,
            store=store,
            gateway=SettlingThenFailingGateway(store, record.publish_job_id),
            token_provider=FakeTokenProvider(),
            clock=FixedClock(),
        )
        await store.ensure_pending(record.publish_job_id, RESOURCE_ID, NOW)

        result = await service.reconcile_once(record.publish_job_id)

        assert result.decision is BilibiliReconciliationDecision.ALREADY_SETTLED
        assert result.outcome is BilibiliReconciliationOutcome.PUBLISHED

    @pytest.mark.asyncio
    async def test_malformed_notification_lookup_maps_to_unavailable(self) -> None:
        service, attempts, store, _, _ = make_service()
        await _recovered_submitted(service, attempts)

        async def junk_find(*arguments: object) -> object:
            return "not-a-record"

        store.find_by_resource_id = junk_find  # type: ignore[assignment]
        payload = _fixture("notification-video-open-valid")
        payload["content"]["resource_id"] = RESOURCE_ID
        notification = parse_archive_status_notification(CONTRACT, payload)
        with pytest.raises(BilibiliArchivePublishUnavailable):
            await service.apply_notification(notification)

    @pytest.mark.asyncio
    async def test_settle_race_between_check_and_write_replays(self) -> None:
        service, attempts, store, gateway, _ = make_service()
        job_id = await _recovered_submitted(service, attempts)
        gateway.script("archive_view", _view_payload(state=0, ptime=DISPATCHED_EPOCH + 60))
        original_settle = store.settle

        async def racing_settle(*arguments: object, **keywords: object) -> bool:
            await original_settle(
                job_id,
                BilibiliReconciliationOutcome.REJECTED,
                -2,
                None,
                NOW + timedelta(minutes=1),
            )
            return False

        store.settle = racing_settle  # type: ignore[method-assign]
        result = await service.reconcile_once(job_id)
        assert result.decision is BilibiliReconciliationDecision.ALREADY_SETTLED
        assert result.outcome is BilibiliReconciliationOutcome.REJECTED

    @pytest.mark.asyncio
    async def test_unexpected_gateway_exception_maps_to_unavailable(self) -> None:
        service, attempts, _store, gateway, _ = make_service()
        job_id = await _recovered_submitted(service, attempts)
        gateway.script("archive_view", RuntimeError("socket exploded"))
        with pytest.raises(BilibiliArchivePublishUnavailable):
            await service.reconcile_once(job_id)

    @pytest.mark.asyncio
    async def test_view_answering_for_another_archive_is_pacing_only(self) -> None:
        service, attempts, store, gateway, _ = make_service()
        job_id = await _recovered_submitted(service, attempts)
        gateway.script(
            "archive_view",
            _view_payload(resource_id=OTHER_RESOURCE_ID, state=0, ptime=DISPATCHED_EPOCH + 60),
        )
        result = await service.reconcile_once(job_id)
        assert result.decision is BilibiliReconciliationDecision.QUERY_UNREACHABLE
        row = await store.load(job_id)
        assert row is not None
        assert row.outcome is BilibiliReconciliationOutcome.PENDING

    @pytest.mark.asyncio
    async def test_malformed_listing_page_is_pacing_only(self) -> None:
        service, attempts, _store, gateway, _ = make_service()
        job_id = await _recovered_uncertain(service, attempts)
        gateway.script("archive_viewlist", {"code": 0, "message": "0", "data": {"surprise": 1}})
        result = await service.reconcile_once(job_id)
        assert result.decision is BilibiliReconciliationDecision.QUERY_UNREACHABLE

    @pytest.mark.asyncio
    async def test_expired_token_during_listing_refreshes_once(self) -> None:
        service, attempts, _store, gateway, provider = make_service()
        provider.tokens = [
            "fixture-access-token-000000000001",
            "fixture-access-token-000000000002",
        ]
        job_id = await _recovered_uncertain(service, attempts)
        gateway.script(
            "archive_viewlist",
            _error_payload(127001),
            _viewlist_payload([], total=0),
        )
        result = await service.reconcile_once(job_id)
        assert result.decision is BilibiliReconciliationDecision.FAILED_ABSENT
        assert provider.refresh_calls == 1

    @pytest.mark.asyncio
    async def test_listing_beyond_the_page_budget_stays_uncertain(self) -> None:
        service, attempts, _store, gateway, _ = make_service(
            policy=BilibiliReconciliationPolicy(max_list_pages=1)
        )
        job_id = await _recovered_uncertain(service, attempts)
        gateway.script(
            "archive_viewlist",
            _viewlist_payload(
                [_archive_item(resource_id=OTHER_RESOURCE_ID, title="别的稿件")],
                pn=1,
                ps=1,
                total=2,
            ),
        )
        result = await service.reconcile_once(job_id)
        assert result.decision is BilibiliReconciliationDecision.STILL_UNCERTAIN

    @pytest.mark.asyncio
    async def test_overfull_listing_page_stays_uncertain(self) -> None:
        service, attempts, _store, gateway, _ = make_service()
        job_id = await _recovered_uncertain(service, attempts)
        gateway.script(
            "archive_viewlist",
            _viewlist_payload(
                [
                    _archive_item(resource_id=OTHER_RESOURCE_ID, title="别的稿件"),
                    _archive_item(resource_id="BV1Other0002", title="另一稿件"),
                ],
                pn=1,
                ps=1,
                total=2,
            ),
        )
        result = await service.reconcile_once(job_id)
        assert result.decision is BilibiliReconciliationDecision.STILL_UNCERTAIN

    @pytest.mark.asyncio
    async def test_truncated_listing_never_proves_absence(self) -> None:
        service, attempts, store, gateway, _ = make_service()
        job_id = await _recovered_uncertain(service, attempts)
        gateway.script("archive_viewlist", _viewlist_payload([], total=5))
        result = await service.reconcile_once(job_id)
        assert result.decision is BilibiliReconciliationDecision.STILL_UNCERTAIN
        row = await store.load(job_id)
        assert row is not None
        assert row.outcome is BilibiliReconciliationOutcome.PENDING

    def test_naive_timestamps_are_rejected_in_records(self) -> None:
        base = BilibiliReconciliationRecord(
            publish_job_id=PublishJobId.new(),
            outcome=BilibiliReconciliationOutcome.PENDING,
            resource_id=None,
            archive_state=None,
            failure_code=None,
            last_checked_at=None,
            settled_at=None,
            created_at=NOW,
            updated_at=NOW,
        )
        naive = datetime(2026, 7, 23, 1, 0)
        with pytest.raises(BilibiliArchivePublishRejected):
            replace(base, created_at=naive, updated_at=naive)

    @pytest.mark.asyncio
    async def test_empty_refreshed_token_maps_to_unavailable(self) -> None:
        class EmptyRefreshProvider:
            async def current_access_token(self) -> str:
                return "fixture-access-token-000000000001"

            async def refresh_access_token(self) -> str:
                return ""

        attempts = FakeAttemptSource()
        store = FakeReconciliationStore()
        gateway = ScriptedQueryGateway()
        record = attempts.add(
            attempt_record(phase=BilibiliPublishPhase.SUBMITTED, resource_id=RESOURCE_ID)
        )
        service = BilibiliArchiveReconciliationService(
            contract=CONTRACT,
            attempt_source=attempts,
            store=store,
            gateway=gateway,
            token_provider=EmptyRefreshProvider(),
            clock=FixedClock(),
        )
        await store.ensure_pending(record.publish_job_id, RESOURCE_ID, NOW)
        gateway.script("archive_view", _error_payload(127001))
        with pytest.raises(BilibiliArchivePublishUnavailable):
            await service.reconcile_once(record.publish_job_id)
