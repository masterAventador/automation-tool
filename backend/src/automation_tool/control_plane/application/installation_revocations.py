"""Atomic operator-initiated revocation of one App installation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from automation_tool.control_plane.domain import InstallationId, InvalidResourceId


class InstallationRevocationRejected(PermissionError):
    """The target is unknown, inactive, stale, or otherwise not revocable."""

    def __init__(self) -> None:
        super().__init__("Installation revocation is rejected")


@dataclass(frozen=True, slots=True)
class RevokedInstallation:
    installation_id: InstallationId
    revision: int
    revoked_at: datetime


class InstallationRevocationRepository(Protocol):
    async def revoke(
        self,
        *,
        installation_id: InstallationId,
        expected_revision: int,
        revoked_at: datetime,
    ) -> RevokedInstallation: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


class InstallationRevocationService:
    """Validate an operator target and delegate one fail-closed transaction."""

    def __init__(
        self,
        *,
        repository: InstallationRevocationRepository,
        clock: Clock,
    ) -> None:
        self._repository = repository
        self._clock = clock

    async def revoke(
        self,
        *,
        installation_id: object,
        expected_revision: object,
    ) -> RevokedInstallation:
        try:
            target = InstallationId.parse(installation_id)
        except InvalidResourceId:
            target = None
        if target is None:
            raise InstallationRevocationRejected
        if type(expected_revision) is not int or expected_revision <= 0:
            raise InstallationRevocationRejected
        revoked_at = self._clock.now()
        if not isinstance(revoked_at, datetime) or revoked_at.utcoffset() is None:
            raise RuntimeError("Installation revocation clock is invalid")
        return await self._repository.revoke(
            installation_id=target,
            expected_revision=expected_revision,
            revoked_at=revoked_at.astimezone(UTC),
        )


__all__ = [
    "InstallationRevocationRejected",
    "InstallationRevocationRepository",
    "InstallationRevocationService",
    "RevokedInstallation",
]
