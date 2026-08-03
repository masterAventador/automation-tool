from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from automation_tool.control_plane.application.platform_session_health import (
    PendingPlatformSessionHealth,
    PlatformSessionHealthConvergenceResult,
    PlatformSessionHealthProjection,
    PlatformSessionHealthRejected,
    PlatformSessionHealthRepository,
    PlatformSessionHealthService,
    PlatformSessionHealthUnavailable,
    PlatformSessionLogoutGate,
    SystemPlatformSessionHealthClock,
)
from automation_tool.control_plane.domain import InstallationId
from automation_tool.protocol import (
    PlatformSessionHealthEnvelope,
    PlatformSessionState,
)

NOW = datetime(2026, 7, 19, 11, 0, tzinfo=UTC)
OBSERVED_AT = NOW - timedelta(seconds=1)
INSTALLATION_ID = UUID("123e4567-e89b-42d3-a456-426614174003")
EXECUTOR_ID = UUID("123e4567-e89b-42d3-a456-426614174004")


@dataclass
class FixedClock:
    value: datetime = NOW

    def now(self) -> datetime:
        return self.value


class Repository:
    def __init__(self) -> None:
        self.pending: PendingPlatformSessionHealth | None = None
        self.failure: Exception | None = None
        self.blocked_at: datetime | None = None

    async def converge(
        self,
        pending: PendingPlatformSessionHealth,
    ) -> PlatformSessionHealthConvergenceResult:
        self.pending = pending
        if self.failure is not None:
            raise self.failure
        return PlatformSessionHealthConvergenceResult(
            projection=PlatformSessionHealthProjection(
                installation_id=pending.installation_id,
                platform=pending.platform,
                state=pending.state,
                session_revision=pending.session_revision,
                observed_at=pending.observed_at,
                updated_at=pending.received_at,
            ),
            duplicate=False,
        )

    async def get(
        self,
        installation_id: InstallationId,
        platform: str,
    ) -> PlatformSessionHealthProjection | None:
        assert installation_id == InstallationId.parse(INSTALLATION_ID)
        assert platform == "douyin"
        if self.failure is not None:
            raise self.failure
        return PlatformSessionHealthProjection(
            installation_id=installation_id,
            platform=platform,
            state=PlatformSessionState.HEALTHY,
            session_revision=7,
            observed_at=OBSERVED_AT,
            updated_at=NOW,
        )

    async def begin_logout(
        self,
        installation_id: InstallationId,
        platform: str,
        blocked_at: datetime,
    ) -> PlatformSessionLogoutGate:
        if self.failure is not None:
            raise self.failure
        self.blocked_at = blocked_at
        return PlatformSessionLogoutGate(
            installation_id=installation_id,
            platform=platform,
            state="blocked",
            session_revision=8,
            updated_at=blocked_at,
        )


def message(
    *,
    state: str = "healthy",
    revision: int = 7,
    observed_at: datetime = OBSERVED_AT,
    sent_at: datetime = NOW,
    deadline_at: datetime = NOW + timedelta(seconds=30),
) -> PlatformSessionHealthEnvelope:
    return PlatformSessionHealthEnvelope.model_validate(
        {
            "protocol_version": "1.0",
            "message_id": "723e4567-e89b-42d3-a456-426614174001",
            "message_type": "platform.session_health",
            "sent_at": sent_at,
            "deadline_at": deadline_at,
            "installation_id": str(INSTALLATION_ID),
            "executor_id": str(EXECUTOR_ID),
            "correlation_id": "723e4567-e89b-42d3-a456-426614174002",
            "idempotency_key": f"platform:douyin:session:{revision}:{state}",
            "sequence": revision,
            "payload": {
                "platform": "douyin",
                "state": state,
                "session_revision": revision,
                "observed_at": observed_at,
            },
        }
    )


@pytest.mark.asyncio
async def test_receive_projects_only_the_closed_non_sensitive_session_fact() -> None:
    repository = Repository()
    service = PlatformSessionHealthService(repository=repository, clock=FixedClock())

    result = await service.receive(message())

    assert result.projection == PlatformSessionHealthProjection(
        installation_id=InstallationId.parse(INSTALLATION_ID),
        platform="douyin",
        state=PlatformSessionState.HEALTHY,
        session_revision=7,
        observed_at=OBSERVED_AT,
        updated_at=NOW,
    )
    assert result.duplicate is False
    assert repository.pending == PendingPlatformSessionHealth(
        installation_id=InstallationId.parse(INSTALLATION_ID),
        platform="douyin",
        state=PlatformSessionState.HEALTHY,
        session_revision=7,
        observed_at=OBSERVED_AT,
        received_at=NOW,
    )
    assert "cookie" not in repr(repository.pending).lower()
    assert "profile" not in repr(repository.pending).lower()


@pytest.mark.asyncio
async def test_get_returns_only_the_current_installation_projection() -> None:
    repository = Repository()
    service = PlatformSessionHealthService(repository=repository, clock=FixedClock())

    projection = await service.get(
        InstallationId.parse(INSTALLATION_ID),
        platform="douyin",
    )

    assert projection == PlatformSessionHealthProjection(
        installation_id=InstallationId.parse(INSTALLATION_ID),
        platform="douyin",
        state=PlatformSessionState.HEALTHY,
        session_revision=7,
        observed_at=OBSERVED_AT,
        updated_at=NOW,
    )


@pytest.mark.asyncio
async def test_begin_logout_persists_one_typed_gate_at_the_service_clock() -> None:
    repository = Repository()
    service = PlatformSessionHealthService(repository=repository, clock=FixedClock())

    gate = await service.begin_logout(
        InstallationId.parse(INSTALLATION_ID),
        platform="douyin",
    )

    assert gate.state == "blocked"
    assert gate.session_revision == 8
    assert gate.updated_at == NOW
    assert repository.blocked_at == NOW


@pytest.mark.asyncio
async def test_get_rejects_invalid_scope_and_maps_repository_failure() -> None:
    repository = Repository()
    service = PlatformSessionHealthService(repository=repository, clock=FixedClock())

    with pytest.raises(PlatformSessionHealthRejected):
        await service.get(InstallationId.parse(INSTALLATION_ID), platform="private")

    repository.failure = RuntimeError("private database detail")
    with pytest.raises(PlatformSessionHealthUnavailable) as captured:
        await service.get(InstallationId.parse(INSTALLATION_ID), platform="douyin")
    assert "private database detail" not in str(captured.value)


@pytest.mark.asyncio
async def test_receive_rejects_future_observation_expired_wire_and_wrong_type() -> None:
    repository = Repository()
    service = PlatformSessionHealthService(repository=repository, clock=FixedClock())

    invalid = (
        message(observed_at=NOW + timedelta(microseconds=1)),
        message(sent_at=NOW - timedelta(minutes=1), deadline_at=NOW),
        object(),
    )
    for candidate in invalid:
        with pytest.raises(PlatformSessionHealthRejected):
            await service.receive(candidate)
    assert repository.pending is None


@pytest.mark.asyncio
async def test_repository_rejection_is_preserved_and_unknown_failure_is_safe() -> None:
    repository = Repository()
    service = PlatformSessionHealthService(repository=repository, clock=FixedClock())
    repository.failure = PlatformSessionHealthRejected()
    with pytest.raises(PlatformSessionHealthRejected):
        await service.receive(message())

    repository.failure = PlatformSessionHealthUnavailable()
    with pytest.raises(PlatformSessionHealthUnavailable):
        await service.receive(message())

    repository.failure = RuntimeError("private database detail")
    with pytest.raises(PlatformSessionHealthUnavailable) as captured:
        await service.receive(message())
    assert "private database detail" not in str(captured.value)


@pytest.mark.asyncio
async def test_service_requires_a_structural_repository_and_aware_clock() -> None:
    with pytest.raises(PlatformSessionHealthRejected):
        PlatformSessionHealthService(repository=object())  # type: ignore[arg-type]
    repository = Repository()
    service = PlatformSessionHealthService(
        repository=repository,
        clock=FixedClock(datetime(2026, 7, 19, 11, 0)),
    )
    with pytest.raises(PlatformSessionHealthUnavailable):
        await service.receive(message())
    assert isinstance(repository, PlatformSessionHealthRepository)


def test_typed_health_values_reject_malformed_fields_and_expose_circuit_state() -> None:
    installation_id = InstallationId.parse(INSTALLATION_ID)
    valid = {
        "installation_id": installation_id,
        "platform": "douyin",
        "state": PlatformSessionState.MISSING,
        "session_revision": 1,
        "observed_at": OBSERVED_AT,
        "updated_at": NOW,
    }
    invalid = (
        {"installation_id": object()},
        {"platform": "private"},
        {"state": "missing"},
        {"session_revision": True},
        {"session_revision": 0},
        {"observed_at": "private"},
        {"observed_at": datetime(2026, 7, 19, 10, 59)},
        {"updated_at": "private"},
        {"updated_at": datetime(2026, 7, 19, 11, 0)},
        {"updated_at": OBSERVED_AT - timedelta(microseconds=1)},
    )
    for overrides in invalid:
        with pytest.raises(PlatformSessionHealthRejected):
            PlatformSessionHealthProjection(**(valid | overrides))

    pending = PendingPlatformSessionHealth(
        installation_id=installation_id,
        platform="douyin",
        state=PlatformSessionState.MISSING,
        session_revision=1,
        observed_at=OBSERVED_AT,
        received_at=NOW,
    )
    projection = PlatformSessionHealthProjection(**valid)
    assert pending.circuit_open is True
    assert projection.circuit_open is True

    with pytest.raises(PlatformSessionHealthRejected):
        PlatformSessionHealthConvergenceResult(
            projection=object(),  # type: ignore[arg-type]
            duplicate=False,
        )
    with pytest.raises(PlatformSessionHealthRejected):
        PlatformSessionHealthConvergenceResult(projection=projection, duplicate=1)  # type: ignore[arg-type]


def test_logout_gate_rejects_every_malformed_field() -> None:
    valid = {
        "installation_id": InstallationId.parse(INSTALLATION_ID),
        "platform": "douyin",
        "state": "blocked",
        "session_revision": 1,
        "updated_at": NOW,
    }
    invalid = (
        {"installation_id": object()},
        {"platform": "private"},
        {"state": "open"},
        {"session_revision": True},
        {"session_revision": 0},
        {"updated_at": "private"},
        {"updated_at": datetime(2026, 7, 19, 11, 0)},
    )
    for overrides in invalid:
        with pytest.raises(PlatformSessionHealthRejected):
            PlatformSessionLogoutGate(**(valid | overrides))


@pytest.mark.asyncio
async def test_service_preserves_explicit_failures_and_rejects_malformed_results() -> None:
    installation_id = InstallationId.parse(INSTALLATION_ID)
    repository = Repository()
    service = PlatformSessionHealthService(repository=repository, clock=FixedClock())

    for failure in (PlatformSessionHealthRejected(), PlatformSessionHealthUnavailable()):
        repository.failure = failure
        with pytest.raises(type(failure)):
            await service.get(installation_id, platform="douyin")
        with pytest.raises(type(failure)):
            await service.begin_logout(installation_id, platform="douyin")

    repository.failure = RuntimeError("private database detail")
    with pytest.raises(PlatformSessionHealthUnavailable):
        await service.begin_logout(installation_id, platform="douyin")

    repository.failure = None

    async def malformed_get(
        installation_id: InstallationId,
        platform: str,
    ) -> object:
        return object()

    repository.get = malformed_get  # type: ignore[method-assign,assignment]
    with pytest.raises(PlatformSessionHealthUnavailable):
        await service.get(installation_id, platform="douyin")

    async def malformed_gate(
        installation_id: InstallationId,
        platform: str,
        blocked_at: datetime,
    ) -> object:
        return object()

    repository.begin_logout = malformed_gate  # type: ignore[method-assign,assignment]
    with pytest.raises(PlatformSessionHealthUnavailable):
        await service.begin_logout(installation_id, platform="douyin")


@pytest.mark.asyncio
async def test_begin_logout_rejects_invalid_scope_and_system_clock_is_utc() -> None:
    service = PlatformSessionHealthService(repository=Repository(), clock=FixedClock())
    with pytest.raises(PlatformSessionHealthRejected):
        await service.begin_logout(
            InstallationId.parse(INSTALLATION_ID),
            platform="private",
        )
    assert SystemPlatformSessionHealthClock().now().utcoffset() == UTC.utcoffset(NOW)
