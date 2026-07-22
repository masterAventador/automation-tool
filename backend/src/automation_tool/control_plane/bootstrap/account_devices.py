"""Runtime wiring for current-account Installation management."""

from datetime import UTC, datetime

from automation_tool.control_plane.application.account_devices import AccountDeviceService
from automation_tool.control_plane.application.account_sessions import AccountSessionService
from automation_tool.control_plane.infrastructure.database import Database
from automation_tool.control_plane.infrastructure.database.account_device_repository import (
    SqlAlchemyAccountDeviceRepository,
)


class _Clock:
    def now(self) -> datetime:
        return datetime.now(UTC)


def account_device_service(
    database: Database, account_sessions: AccountSessionService
) -> AccountDeviceService:
    return AccountDeviceService(
        repository=SqlAlchemyAccountDeviceRepository(database),
        account_sessions=account_sessions,
        clock=_Clock(),
    )


__all__ = ["account_device_service"]
