from datetime import UTC
from typing import cast

from automation_tool.control_plane.application.account_devices import AccountDeviceService
from automation_tool.control_plane.application.account_installation_bindings import (
    AccountInstallationBindingService,
)
from automation_tool.control_plane.application.account_sessions import AccountSessionService
from automation_tool.control_plane.bootstrap.account_devices import (
    _Clock,
    account_device_service,
)
from automation_tool.control_plane.bootstrap.account_installation_bindings import (
    SystemClock,
    account_installation_binding_service,
)
from automation_tool.control_plane.infrastructure.database import Database


def database_without_connection() -> Database:
    return Database.from_url(
        "postgresql+asyncpg://unused:unused@127.0.0.1:1/unused",
        connect_timeout_seconds=0.01,
    )


def test_account_management_clocks_and_composition_are_utc() -> None:
    database = database_without_connection()
    sessions = cast(AccountSessionService, object())

    assert _Clock().now().tzinfo == UTC
    assert SystemClock().now().tzinfo == UTC
    assert isinstance(account_device_service(database, sessions), AccountDeviceService)
    assert isinstance(
        account_installation_binding_service(database, sessions),
        AccountInstallationBindingService,
    )
