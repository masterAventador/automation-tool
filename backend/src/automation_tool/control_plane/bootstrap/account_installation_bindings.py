"""Production account Installation binding composition."""

import secrets
from datetime import UTC, datetime
from uuid import uuid4

from automation_tool.control_plane.application.account_installation_bindings import (
    AccountInstallationBindingService,
)
from automation_tool.control_plane.application.account_sessions import AccountSessionService
from automation_tool.control_plane.application.device_credentials import DeviceCredentialFactory
from automation_tool.control_plane.infrastructure.database import (
    Database,
    SqlAlchemyAccountInstallationBindingRepository,
)


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


def account_installation_binding_service(
    database: Database,
    account_sessions: AccountSessionService,
) -> AccountInstallationBindingService:
    return AccountInstallationBindingService(
        repository=SqlAlchemyAccountInstallationBindingRepository(database),
        account_sessions=account_sessions,
        clock=SystemClock(),
        nonce_source=secrets.token_bytes,
        id_source=uuid4,
        credential_factory=DeviceCredentialFactory(
            secret_source=secrets.token_bytes,
            id_source=uuid4,
        ),
    )


__all__ = ["account_installation_binding_service"]
