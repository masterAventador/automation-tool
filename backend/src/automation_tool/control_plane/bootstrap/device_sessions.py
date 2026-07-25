"""Runtime wiring for short-lived device sessions."""

import secrets
from datetime import UTC, datetime
from uuid import uuid4

from automation_tool.control_plane.application.device_sessions import (
    DeviceSessionFactory,
    DeviceSessionService,
)
from automation_tool.control_plane.infrastructure.database import Database
from automation_tool.control_plane.infrastructure.database.device_session_repository import (
    SqlAlchemyDeviceSessionRepository,
)


class _SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


def device_session_service(
    database: Database,
    *,
    require_installation_owner: bool,
) -> DeviceSessionService:
    """Build the session exchange from process-safe runtime dependencies."""
    return DeviceSessionService(
        repository=SqlAlchemyDeviceSessionRepository(
            database,
            require_installation_owner=require_installation_owner,
        ),
        clock=_SystemClock(),
        session_factory=DeviceSessionFactory(
            secret_source=secrets.token_bytes,
            id_source=uuid4,
        ),
    )


__all__ = ["device_session_service"]
