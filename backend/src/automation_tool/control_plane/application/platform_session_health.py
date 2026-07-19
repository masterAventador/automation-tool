"""Validate and converge non-sensitive platform Session health reports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol, runtime_checkable

from automation_tool.control_plane.domain import InstallationId
from automation_tool.protocol import PlatformSessionHealthEnvelope, PlatformSessionState


class PlatformSessionHealthRejected(ValueError):
    def __init__(self) -> None:
        super().__init__("Platform Session health is rejected")


class PlatformSessionHealthUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Platform Session health is unavailable")


@dataclass(frozen=True, slots=True)
class PendingPlatformSessionHealth:
    installation_id: InstallationId
    platform: str
    state: PlatformSessionState
    session_revision: int
    observed_at: datetime
    received_at: datetime

    def __post_init__(self) -> None:
        _validate_projection_fields(
            installation_id=self.installation_id,
            platform=self.platform,
            state=self.state,
            session_revision=self.session_revision,
            observed_at=self.observed_at,
            updated_at=self.received_at,
        )

    @property
    def circuit_open(self) -> bool:
        return self.state is not PlatformSessionState.HEALTHY


@dataclass(frozen=True, slots=True)
class PlatformSessionHealthProjection:
    installation_id: InstallationId
    platform: str
    state: PlatformSessionState
    session_revision: int
    observed_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        _validate_projection_fields(
            installation_id=self.installation_id,
            platform=self.platform,
            state=self.state,
            session_revision=self.session_revision,
            observed_at=self.observed_at,
            updated_at=self.updated_at,
        )

    @property
    def circuit_open(self) -> bool:
        return self.state is not PlatformSessionState.HEALTHY


@dataclass(frozen=True, slots=True)
class PlatformSessionHealthConvergenceResult:
    projection: PlatformSessionHealthProjection
    duplicate: bool

    def __post_init__(self) -> None:
        if (
            not isinstance(self.projection, PlatformSessionHealthProjection)
            or type(self.duplicate) is not bool
        ):
            raise PlatformSessionHealthRejected


@dataclass(frozen=True, slots=True)
class PlatformSessionLogoutGate:
    installation_id: InstallationId
    platform: str
    state: Literal["blocked"]
    session_revision: int
    updated_at: datetime

    def __post_init__(self) -> None:
        if (
            not isinstance(self.installation_id, InstallationId)
            or self.platform != "douyin"
            or self.state != "blocked"
            or type(self.session_revision) is not int
            or self.session_revision <= 0
            or not isinstance(self.updated_at, datetime)
            or self.updated_at.utcoffset() != UTC.utcoffset(self.updated_at)
        ):
            raise PlatformSessionHealthRejected


@runtime_checkable
class PlatformSessionHealthRepository(Protocol):
    async def converge(
        self,
        pending: PendingPlatformSessionHealth,
    ) -> PlatformSessionHealthConvergenceResult: ...

    async def get(
        self,
        installation_id: InstallationId,
        platform: str,
    ) -> PlatformSessionHealthProjection | None: ...

    async def begin_logout(
        self,
        installation_id: InstallationId,
        platform: str,
        blocked_at: datetime,
    ) -> PlatformSessionLogoutGate: ...


class PlatformSessionHealthClock(Protocol):
    def now(self) -> datetime: ...


class SystemPlatformSessionHealthClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class PlatformSessionHealthService:
    """Accept only timely typed facts and delegate one atomic convergence."""

    def __init__(
        self,
        *,
        repository: PlatformSessionHealthRepository,
        clock: PlatformSessionHealthClock | None = None,
    ) -> None:
        if not isinstance(repository, PlatformSessionHealthRepository):
            raise PlatformSessionHealthRejected
        self._repository = repository
        self._clock = clock or SystemPlatformSessionHealthClock()

    def _now(self) -> datetime:
        try:
            value = self._clock.now()
            if not isinstance(value, datetime) or value.utcoffset() is None:
                raise ValueError
            return value.astimezone(UTC)
        except Exception:
            raise PlatformSessionHealthUnavailable from None

    async def receive(
        self,
        message: PlatformSessionHealthEnvelope,
    ) -> PlatformSessionHealthConvergenceResult:
        if not isinstance(message, PlatformSessionHealthEnvelope):
            raise PlatformSessionHealthRejected
        received_at = self._now()
        if (
            received_at < message.sent_at
            or received_at >= message.deadline_at
            or message.payload.observed_at > message.sent_at
        ):
            raise PlatformSessionHealthRejected
        pending = PendingPlatformSessionHealth(
            installation_id=InstallationId.parse(str(message.installation_id)),
            platform=message.payload.platform,
            state=message.payload.state,
            session_revision=message.payload.session_revision,
            observed_at=message.payload.observed_at,
            received_at=received_at,
        )
        try:
            return await self._repository.converge(pending)
        except PlatformSessionHealthRejected:
            raise
        except PlatformSessionHealthUnavailable:
            raise
        except Exception:
            raise PlatformSessionHealthUnavailable from None

    async def get(
        self,
        installation_id: InstallationId,
        *,
        platform: str,
    ) -> PlatformSessionHealthProjection | None:
        if not isinstance(installation_id, InstallationId) or platform != "douyin":
            raise PlatformSessionHealthRejected
        try:
            projection = await self._repository.get(installation_id, platform)
        except PlatformSessionHealthRejected:
            raise
        except PlatformSessionHealthUnavailable:
            raise
        except Exception:
            raise PlatformSessionHealthUnavailable from None
        if projection is not None and (
            not isinstance(projection, PlatformSessionHealthProjection)
            or projection.installation_id != installation_id
            or projection.platform != platform
        ):
            raise PlatformSessionHealthUnavailable
        return projection

    async def begin_logout(
        self,
        installation_id: InstallationId,
        *,
        platform: str,
    ) -> PlatformSessionLogoutGate:
        if not isinstance(installation_id, InstallationId) or platform != "douyin":
            raise PlatformSessionHealthRejected
        blocked_at = self._now()
        try:
            gate = await self._repository.begin_logout(
                installation_id,
                platform,
                blocked_at,
            )
        except PlatformSessionHealthRejected:
            raise
        except PlatformSessionHealthUnavailable:
            raise
        except Exception:
            raise PlatformSessionHealthUnavailable from None
        if (
            not isinstance(gate, PlatformSessionLogoutGate)
            or gate.installation_id != installation_id
            or gate.platform != platform
        ):
            raise PlatformSessionHealthUnavailable
        return gate


def _validate_projection_fields(
    *,
    installation_id: InstallationId,
    platform: str,
    state: PlatformSessionState,
    session_revision: int,
    observed_at: datetime,
    updated_at: datetime,
) -> None:
    if (
        not isinstance(installation_id, InstallationId)
        or platform != "douyin"
        or type(platform) is not str
        or not isinstance(state, PlatformSessionState)
        or type(session_revision) is not int
        or session_revision <= 0
        or not isinstance(observed_at, datetime)
        or observed_at.utcoffset() != UTC.utcoffset(observed_at)
        or not isinstance(updated_at, datetime)
        or updated_at.utcoffset() != UTC.utcoffset(updated_at)
        or updated_at < observed_at
    ):
        raise PlatformSessionHealthRejected


__all__ = [
    "PendingPlatformSessionHealth",
    "PlatformSessionHealthClock",
    "PlatformSessionHealthConvergenceResult",
    "PlatformSessionHealthProjection",
    "PlatformSessionHealthRejected",
    "PlatformSessionHealthRepository",
    "PlatformSessionHealthService",
    "PlatformSessionHealthUnavailable",
    "PlatformSessionLogoutGate",
    "SystemPlatformSessionHealthClock",
]
