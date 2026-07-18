"""Runtime wiring for the device credential lifecycle service."""

import secrets
from datetime import UTC, datetime
from uuid import uuid4

from automation_tool.control_plane.application.device_credentials import (
    DeviceCredentialFactory,
    DeviceCredentialService,
)
from automation_tool.control_plane.infrastructure.database import Database
from automation_tool.control_plane.infrastructure.database.device_credential_repository import (
    SqlAlchemyDeviceCredentialRepository,
)


class _SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


def device_credential_service(database: Database) -> DeviceCredentialService:
    """Build lifecycle management from process-safe runtime dependencies."""
    return DeviceCredentialService(
        repository=SqlAlchemyDeviceCredentialRepository(database),
        clock=_SystemClock(),
        credential_factory=DeviceCredentialFactory(
            secret_source=secrets.token_bytes,
            id_source=uuid4,
        ),
    )


__all__ = ["device_credential_service"]
