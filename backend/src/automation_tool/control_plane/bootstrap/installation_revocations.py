"""Runtime wiring for operator-initiated Installation revocation."""

from datetime import UTC, datetime

from automation_tool.control_plane.application.installation_revocations import (
    InstallationRevocationService,
)
from automation_tool.control_plane.infrastructure.database import (
    Database,
    SqlAlchemyInstallationRevocationRepository,
)


class _SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


def installation_revocation_service(database: Database) -> InstallationRevocationService:
    return InstallationRevocationService(
        repository=SqlAlchemyInstallationRevocationRepository(database),
        clock=_SystemClock(),
    )


__all__ = ["installation_revocation_service"]
