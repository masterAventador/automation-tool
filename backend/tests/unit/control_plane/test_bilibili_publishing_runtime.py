"""PB-07 production Bilibili publishing runtime lifecycle coverage."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from automation_tool.control_plane.application import bilibili_publishing_runtime as runtime_module
from automation_tool.control_plane.application.bilibili_archive_publishing import (
    BilibiliArchiveCreationReceipt,
    BilibiliArchiveFields,
    BilibiliArchiveOutcomeUncertain,
    BilibiliArchivePublishRejected,
    BilibiliArchivePublishUnavailable,
    BilibiliPublishAttemptRecord,
    BilibiliPublishMaterial,
    BilibiliPublishPhase,
    BilibiliPublishPreparation,
    BilibiliPublishStepFailed,
    BilibiliUploadType,
)
from automation_tool.control_plane.application.bilibili_publishing_runtime import (
    BilibiliCredentialRotation,
    BilibiliPublishingCredential,
    BilibiliPublishingRuntime,
    _Session,
)
from automation_tool.control_plane.domain.bilibili_open_api import (
    load_bilibili_open_api_contract,
)
from automation_tool.control_plane.domain.resource_ids import InstallationId
from automation_tool.control_plane.domain.video_publishing import PublishFailureCode, PublishJobId
from automation_tool.control_plane.infrastructure.database import Database

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
CONTRACT = load_bilibili_open_api_contract(
    REPOSITORY_ROOT / "contracts/publishing/bilibili-open-api.v1.json"
)
NOW = datetime(2026, 8, 3, 1, 0, tzinfo=UTC)
INSTALLATION = InstallationId.new()
OTHER_INSTALLATION = InstallationId.new()
JOB = PublishJobId.new()
OTHER_JOB = PublishJobId.new()
MATERIAL = BilibiliPublishMaterial(
    file_name="video.mp4",
    size_bytes=4,
    duration_seconds=2,
    sha256="a" * 64,
)
FIELDS = BilibiliArchiveFields(
    title="title",
    tid=21,
    tag="tag",
    copyright=1,
    description=None,
    source=None,
    no_reprint=1,
)


def credential() -> BilibiliPublishingCredential:
    return BilibiliPublishingCredential(
        client_id="client",
        app_secret="secret",
        access_token="access",
        refresh_token="refresh",
        expires_at_epoch_seconds=2_000_000_000,
    )


def attempt_record(
    phase: BilibiliPublishPhase = BilibiliPublishPhase.PREPARED,
) -> BilibiliPublishAttemptRecord:
    uploaded = phase is not BilibiliPublishPhase.PREPARED
    dispatched = phase in {
        BilibiliPublishPhase.DISPATCHED,
        BilibiliPublishPhase.SUBMITTED,
        BilibiliPublishPhase.FAILED,
        BilibiliPublishPhase.OUTCOME_UNCERTAIN,
    }
    settled = phase in {
        BilibiliPublishPhase.SUBMITTED,
        BilibiliPublishPhase.FAILED,
        BilibiliPublishPhase.OUTCOME_UNCERTAIN,
    }
    failed = phase is BilibiliPublishPhase.FAILED
    return BilibiliPublishAttemptRecord(
        publish_job_id=JOB,
        phase=phase,
        request_digest="b" * 64,
        material=MATERIAL,
        fields=FIELDS,
        upload_type=BilibiliUploadType.SMALL,
        part_size_bytes=0,
        part_count=0,
        has_cover=False,
        upload_token="upload" if uploaded else None,
        cover_url=None,
        video_uploaded_at=NOW if uploaded else None,
        dispatched_at=NOW if dispatched else None,
        settled_at=NOW if settled else None,
        resource_id="BV17B4y1s7R1" if phase is BilibiliPublishPhase.SUBMITTED else None,
        failure_code=PublishFailureCode.PLATFORM_ERROR if failed else None,
        platform_error_code=400 if failed else None,
        created_at=NOW,
        updated_at=NOW,
    )


class FakeCloseable:
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.closed = 0
        self.failure = failure

    async def aclose(self) -> None:
        self.closed += 1
        if self.failure is not None:
            raise self.failure


class FakeTokens(FakeCloseable):
    def __init__(self, *, revision: int = 0) -> None:
        super().__init__()
        self.revision = revision

    def snapshot(self) -> SimpleNamespace:
        return SimpleNamespace(
            revision=self.revision,
            access_token=f"access-{self.revision}",
            refresh_token=f"refresh-{self.revision}",
            expires_at_epoch_seconds=2_000_000_000 + self.revision,
        )


class FakeAttempts:
    def __init__(self, record: BilibiliPublishAttemptRecord | None = None) -> None:
        self.record = record

    async def load(self, publish_job_id: PublishJobId) -> BilibiliPublishAttemptRecord | None:
        assert publish_job_id == JOB
        return self.record


class FakeReconciliations:
    def __init__(self) -> None:
        self.calls: list[tuple[PublishJobId, str | None]] = []

    async def ensure_pending(
        self,
        publish_job_id: PublishJobId,
        resource_id: str | None,
        at: datetime,
    ) -> None:
        assert at.tzinfo is UTC
        self.calls.append((publish_job_id, resource_id))


class FakeService:
    def __init__(self) -> None:
        self.preparation: object = BilibiliPublishPreparation(
            record=attempt_record(), replayed=False
        )
        self.prepare_failure: Exception | None = None
        self.upload_record = attempt_record(BilibiliPublishPhase.VIDEO_UPLOADED)
        self.receipt: object = BilibiliArchiveCreationReceipt(
            publish_job_id=JOB,
            resource_id="BV17B4y1s7R1",
            request_digest="b" * 64,
            replayed=False,
        )
        self.create_failure: Exception | None = None
        self.reader: object | None = None

    async def prepare(self, *_args: object, **_kwargs: object) -> object:
        if self.prepare_failure is not None:
            raise self.prepare_failure
        return self.preparation

    async def upload_video(
        self, _job: PublishJobId, reader: object
    ) -> BilibiliPublishAttemptRecord:
        self.reader = reader
        return self.upload_record

    async def create_archive(self, _job: PublishJobId) -> object:
        if self.create_failure is not None:
            raise self.create_failure
        return self.receipt


def session(
    *,
    token: str = "session-token",
    installation_id: InstallationId = INSTALLATION,
    publish_job_id: PublishJobId = JOB,
    service: FakeService | None = None,
    gateway: FakeCloseable | None = None,
    tokens: FakeTokens | None = None,
    expires_at_monotonic: float = 10_000_000_000.0,
) -> _Session:
    return _Session(
        token=token,
        installation_id=installation_id,
        publish_job_id=publish_job_id,
        service=cast(Any, service or FakeService()),
        gateway=cast(Any, gateway or FakeCloseable()),
        tokens=cast(Any, tokens or FakeTokens()),
        expires_at_monotonic=expires_at_monotonic,
    )


def empty_runtime(
    *,
    record: BilibiliPublishAttemptRecord | None = None,
) -> tuple[BilibiliPublishingRuntime, FakeAttempts, FakeReconciliations]:
    value = object.__new__(BilibiliPublishingRuntime)
    attempts = FakeAttempts(record)
    reconciliations = FakeReconciliations()
    value._contract = CONTRACT
    value._attempts = cast(Any, attempts)
    value._reconciliations = cast(Any, reconciliations)
    value._sessions = {}
    value._lock = asyncio.Lock()
    return value, attempts, reconciliations


@pytest.mark.parametrize(
    "value",
    [None, 1, "", " value", "value ", "va lue", "x" * 4097],
)
def test_credentials_reject_non_compact_secrets(value: object) -> None:
    values: dict[str, object] = {
        "client_id": "client",
        "app_secret": "secret",
        "access_token": "access",
        "refresh_token": "refresh",
        "expires_at_epoch_seconds": 2_000_000_000,
    }
    values["client_id"] = value
    with pytest.raises(BilibiliArchivePublishRejected):
        BilibiliPublishingCredential(**cast(Any, values))


@pytest.mark.parametrize("expires", [True, 0, -1, 1.0, "1"])
def test_credentials_reject_invalid_expiration(expires: object) -> None:
    with pytest.raises(BilibiliArchivePublishRejected):
        BilibiliPublishingCredential(
            client_id="client",
            app_secret="secret",
            access_token="access",
            refresh_token="refresh",
            expires_at_epoch_seconds=cast(Any, expires),
        )


def test_credential_and_rotation_repr_are_secret_free() -> None:
    original = credential()
    rotated = BilibiliCredentialRotation(
        access_token="rotated-access",
        refresh_token="rotated-refresh",
        expires_at_epoch_seconds=2_000_000_001,
    )
    assert repr(original) == (
        "BilibiliPublishingCredential(expires_at_epoch_seconds=2000000000, <redacted>)"
    )
    assert repr(rotated) == (
        "BilibiliCredentialRotation(expires_at_epoch_seconds=2000000001, <redacted>)"
    )
    for invalid in ["", "with space", " trailing"]:
        with pytest.raises(BilibiliArchivePublishRejected):
            BilibiliCredentialRotation(
                access_token=invalid,
                refresh_token="refresh",
                expires_at_epoch_seconds=1,
            )
    with pytest.raises(BilibiliArchivePublishRejected):
        BilibiliCredentialRotation(
            access_token="access",
            refresh_token="refresh",
            expires_at_epoch_seconds=cast(Any, False),
        )


def test_runtime_constructor_and_projection_are_closed() -> None:
    database = object.__new__(Database)
    runtime = BilibiliPublishingRuntime(database=database, contract=CONTRACT)
    assert repr(runtime) == "BilibiliPublishingRuntime(<redacted>)"
    assert runtime.maximum_video_bytes == CONTRACT.video_max_bytes
    for bad_database, bad_contract in [(object(), CONTRACT), (database, object())]:
        with pytest.raises(BilibiliArchivePublishRejected):
            BilibiliPublishingRuntime(
                database=cast(Any, bad_database),
                contract=cast(Any, bad_contract),
            )


@pytest.mark.asyncio
async def test_session_rotation_repr_and_close_are_bounded() -> None:
    tokens = FakeTokens()
    gateway = FakeCloseable()
    current = session(tokens=tokens, gateway=gateway)
    assert "session-token" not in repr(current)
    assert current.take_rotation() is None
    tokens.revision = 1
    rotation = current.take_rotation()
    assert rotation == BilibiliCredentialRotation(
        access_token="access-1",
        refresh_token="refresh-1",
        expires_at_epoch_seconds=2_000_000_001,
    )
    assert current.take_rotation() is None
    await current.aclose()
    assert gateway.closed == tokens.closed == 1


@pytest.mark.asyncio
async def test_close_sessions_suppresses_one_close_failure_and_continues() -> None:
    runtime, _, _ = empty_runtime()
    failing = session(gateway=FakeCloseable(failure=RuntimeError("closed")))
    healthy_gateway = FakeCloseable()
    await runtime._close_sessions([failing, session(gateway=healthy_gateway)])
    assert healthy_gateway.closed == 1


@pytest.mark.asyncio
async def test_expiration_and_runtime_close_remove_every_owned_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _, _ = empty_runtime()
    expired = session(token="expired", expires_at_monotonic=4.0)
    live = session(token="live", expires_at_monotonic=6.0)
    runtime._sessions = {expired.token: expired, live.token: live}
    monkeypatch.setattr(cast(Any, runtime_module).time, "monotonic", lambda: 5.0)
    assert await runtime._remove_expired_locked() == [expired]
    assert list(runtime._sessions) == ["live"]
    await runtime.aclose()
    assert runtime._sessions == {}
    assert cast(FakeCloseable, live.gateway).closed == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field", ["installation_id", "publish_job_id", "credential", "material", "fields"]
)
async def test_prepare_rejects_every_wrong_boundary_type(field: str) -> None:
    runtime, _, _ = empty_runtime()
    values: dict[str, object] = {
        "installation_id": INSTALLATION,
        "publish_job_id": JOB,
        "credential": credential(),
        "material": MATERIAL,
        "fields": FIELDS,
    }
    values[field] = object()
    with pytest.raises(BilibiliArchivePublishRejected):
        await runtime.prepare(**cast(Any, values))


def install_prepare_fakes(
    monkeypatch: pytest.MonkeyPatch,
    service: FakeService,
    gateway: FakeCloseable,
    tokens: FakeTokens,
) -> None:
    monkeypatch.setattr(
        runtime_module, "HttpxBilibiliAccessTokenProvider", lambda **_values: tokens
    )
    monkeypatch.setattr(runtime_module, "HttpxBilibiliOpenApiGateway", lambda **_values: gateway)
    monkeypatch.setattr(
        runtime_module,
        "BilibiliArchivePublishService",
        lambda **_values: service,
    )


@pytest.mark.asyncio
async def test_prepare_closes_both_clients_when_service_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _, _ = empty_runtime()
    service = FakeService()
    service.prepare_failure = RuntimeError("service failed")
    gateway = FakeCloseable()
    tokens = FakeTokens()
    install_prepare_fakes(monkeypatch, service, gateway, tokens)
    with pytest.raises(RuntimeError, match="service failed"):
        await runtime.prepare(
            installation_id=INSTALLATION,
            publish_job_id=JOB,
            credential=credential(),
            material=MATERIAL,
            fields=FIELDS,
        )
    assert gateway.closed == tokens.closed == 1


@pytest.mark.asyncio
async def test_prepare_rejects_a_non_preparation_and_closes_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _, _ = empty_runtime()
    service = FakeService()
    service.preparation = object()
    gateway = FakeCloseable()
    tokens = FakeTokens()
    install_prepare_fakes(monkeypatch, service, gateway, tokens)
    with pytest.raises(BilibiliArchivePublishUnavailable):
        await runtime.prepare(
            installation_id=INSTALLATION,
            publish_job_id=JOB,
            credential=credential(),
            material=MATERIAL,
            fields=FIELDS,
        )
    assert gateway.closed == tokens.closed == 1


@pytest.mark.asyncio
async def test_prepare_admits_one_session_replaces_installation_and_reports_rotation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _, _ = empty_runtime()
    previous = session(token="previous")
    runtime._sessions[previous.token] = previous
    service = FakeService()
    service.preparation = BilibiliPublishPreparation(record=attempt_record(), replayed=True)
    gateway = FakeCloseable()
    tokens = FakeTokens(revision=2)
    install_prepare_fakes(monkeypatch, service, gateway, tokens)
    monkeypatch.setattr(
        cast(Any, runtime_module).secrets,
        "token_urlsafe",
        lambda _length: "new-session",
    )
    monkeypatch.setattr(cast(Any, runtime_module).time, "monotonic", lambda: 100.0)

    token, result = await runtime.prepare(
        installation_id=INSTALLATION,
        publish_job_id=JOB,
        credential=credential(),
        material=MATERIAL,
        fields=FIELDS,
    )

    assert token == "new-session"
    assert list(runtime._sessions) == [token]
    assert result.phase is BilibiliPublishPhase.PREPARED
    assert result.request_digest == "b" * 64
    assert result.resource_id is None
    assert result.replayed is True
    assert result.credential_rotation is not None
    assert cast(FakeCloseable, previous.gateway).closed == 1


@pytest.mark.asyncio
async def test_prepare_refuses_capacity_after_closing_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _, _ = empty_runtime()
    runtime._sessions = {
        f"token-{index}": session(
            token=f"token-{index}",
            installation_id=InstallationId.new(),
            publish_job_id=PublishJobId.new(),
        )
        for index in range(runtime_module._MAX_SESSIONS)
    }
    service = FakeService()
    gateway = FakeCloseable()
    tokens = FakeTokens()
    install_prepare_fakes(monkeypatch, service, gateway, tokens)
    with pytest.raises(BilibiliArchivePublishUnavailable):
        await runtime.prepare(
            installation_id=INSTALLATION,
            publish_job_id=JOB,
            credential=credential(),
            material=MATERIAL,
            fields=FIELDS,
        )
    assert gateway.closed == tokens.closed == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("installation_id", "publish_job_id", "token"),
    [
        (OTHER_INSTALLATION, JOB, "session-token"),
        (INSTALLATION, OTHER_JOB, "session-token"),
        (INSTALLATION, JOB, "missing-token"),
    ],
)
async def test_session_lookup_rejects_cross_binding_and_missing_tokens(
    installation_id: InstallationId,
    publish_job_id: PublishJobId,
    token: str,
) -> None:
    runtime, _, _ = empty_runtime()
    current = session()
    runtime._sessions[current.token] = current
    with pytest.raises(BilibiliArchivePublishRejected):
        await runtime._session(
            installation_id=installation_id,
            publish_job_id=publish_job_id,
            session_token=token,
        )


@pytest.mark.asyncio
async def test_session_lookup_closes_expired_and_refreshes_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _, _ = empty_runtime()
    expired = session(token="expired", expires_at_monotonic=0.0)
    selected = session(token="selected", expires_at_monotonic=9.0)
    runtime._sessions = {expired.token: expired, selected.token: selected}
    monkeypatch.setattr(cast(Any, runtime_module).time, "monotonic", lambda: 5.0)
    assert (
        await runtime._session(
            installation_id=INSTALLATION,
            publish_job_id=JOB,
            session_token="selected",
        )
        is selected
    )
    assert selected.expires_at_monotonic == 5.0 + runtime_module._SESSION_LIFETIME_SECONDS
    assert cast(FakeCloseable, expired.gateway).closed == 1


@pytest.mark.asyncio
async def test_session_lookup_rejects_non_compact_token() -> None:
    runtime, _, _ = empty_runtime()
    with pytest.raises(BilibiliArchivePublishRejected):
        await runtime._session(
            installation_id=INSTALLATION,
            publish_job_id=JOB,
            session_token="bad token",
        )


@pytest.mark.asyncio
async def test_retire_only_closes_the_current_identity() -> None:
    runtime, _, _ = empty_runtime()
    current = session()
    runtime._sessions[current.token] = current
    await runtime._retire(current.token, session())
    assert current.token in runtime._sessions
    await runtime._retire(current.token, current)
    assert current.token not in runtime._sessions
    assert cast(FakeCloseable, current.gateway).closed == 1


@pytest.mark.asyncio
async def test_upload_rejects_missing_attempt() -> None:
    runtime, _, _ = empty_runtime()
    current = session()
    runtime._sessions[current.token] = current
    with pytest.raises(BilibiliArchivePublishRejected):
        await runtime.upload_video(
            installation_id=INSTALLATION,
            publish_job_id=JOB,
            session_token=current.token,
            material_root=Path("/private/material"),
        )


@pytest.mark.asyncio
async def test_upload_uses_durable_material_facts_and_returns_record(tmp_path: Path) -> None:
    runtime, _, _ = empty_runtime(record=attempt_record())
    service = FakeService()
    current = session(service=service)
    runtime._sessions[current.token] = current
    (tmp_path / MATERIAL.file_name).write_bytes(b"data")
    result = await runtime.upload_video(
        installation_id=INSTALLATION,
        publish_job_id=JOB,
        session_token=current.token,
        material_root=tmp_path,
    )
    assert result.phase is BilibiliPublishPhase.VIDEO_UPLOADED
    reader = cast(Any, service.reader)
    assert reader._path == (tmp_path / "video.mp4").resolve()
    assert reader._file_name == "video.mp4"
    assert reader._duration_seconds == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        BilibiliArchiveOutcomeUncertain(),
        BilibiliPublishStepFailed(cast(Any, object())),
    ],
)
async def test_submit_preserves_durable_outcome_after_uncertain_creation(
    failure: Exception,
) -> None:
    durable = attempt_record(BilibiliPublishPhase.OUTCOME_UNCERTAIN)
    runtime, attempts, reconciliations = empty_runtime(record=durable)
    service = FakeService()
    service.create_failure = failure
    current = session(service=service)
    runtime._sessions[current.token] = current
    result = await runtime.submit(
        installation_id=INSTALLATION,
        publish_job_id=JOB,
        session_token=current.token,
    )
    assert result.phase is BilibiliPublishPhase.OUTCOME_UNCERTAIN
    assert result.replayed is False
    assert reconciliations.calls == [(JOB, None)]
    assert current.token not in runtime._sessions
    assert attempts.record is durable


@pytest.mark.asyncio
async def test_submit_rejects_non_receipt_and_still_retires_session() -> None:
    runtime, _, _ = empty_runtime(record=attempt_record())
    service = FakeService()
    service.receipt = object()
    current = session(service=service)
    runtime._sessions[current.token] = current
    with pytest.raises(BilibiliArchivePublishUnavailable):
        await runtime.submit(
            installation_id=INSTALLATION,
            publish_job_id=JOB,
            session_token=current.token,
        )
    assert current.token not in runtime._sessions


@pytest.mark.asyncio
async def test_submit_rejects_missing_durable_record_and_retires_session() -> None:
    runtime, _, _ = empty_runtime()
    current = session()
    runtime._sessions[current.token] = current
    with pytest.raises(BilibiliArchivePublishUnavailable):
        await runtime.submit(
            installation_id=INSTALLATION,
            publish_job_id=JOB,
            session_token=current.token,
        )
    assert current.token not in runtime._sessions


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("phase", "pending"),
    [
        (BilibiliPublishPhase.SUBMITTED, True),
        (BilibiliPublishPhase.DISPATCHED, True),
        (BilibiliPublishPhase.FAILED, False),
    ],
)
async def test_submit_reconciles_only_admitted_outcomes(
    phase: BilibiliPublishPhase,
    pending: bool,
) -> None:
    durable = attempt_record(phase)
    runtime, _, reconciliations = empty_runtime(record=durable)
    service = FakeService()
    service.receipt = BilibiliArchiveCreationReceipt(
        publish_job_id=JOB,
        resource_id="BV17B4y1s7R1",
        request_digest="b" * 64,
        replayed=True,
    )
    current = session(service=service)
    runtime._sessions[current.token] = current
    result = await runtime.submit(
        installation_id=INSTALLATION,
        publish_job_id=JOB,
        session_token=current.token,
    )
    assert result.phase is phase
    assert result.replayed is True
    assert bool(reconciliations.calls) is pending


@pytest.mark.asyncio
async def test_cancel_retires_selected_session() -> None:
    runtime, _, _ = empty_runtime()
    current = session()
    runtime._sessions[current.token] = current
    await runtime.cancel(
        installation_id=INSTALLATION,
        publish_job_id=JOB,
        session_token=current.token,
    )
    assert runtime._sessions == {}
    assert cast(FakeCloseable, current.gateway).closed == 1
