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
            await service.receive(candidate)  # type: ignore[arg-type]
    assert repository.pending is None


@pytest.mark.asyncio
async def test_repository_rejection_is_preserved_and_unknown_failure_is_safe() -> None:
    repository = Repository()
    service = PlatformSessionHealthService(repository=repository, clock=FixedClock())
    repository.failure = PlatformSessionHealthRejected()
    with pytest.raises(PlatformSessionHealthRejected):
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
