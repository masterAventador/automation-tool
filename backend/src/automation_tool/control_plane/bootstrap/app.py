"""FastAPI application factory and process lifespan."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from automation_tool import __version__
from automation_tool.control_plane.api.device_credentials import (
    router as device_credential_router,
)
from automation_tool.control_plane.api.device_sessions import (
    router as device_session_router,
)
from automation_tool.control_plane.api.errors import (
    install_request_context,
    register_error_handlers,
)
from automation_tool.control_plane.api.registrations import router as registration_router
from automation_tool.control_plane.api.system import router as system_router
from automation_tool.control_plane.application.device_credentials import DeviceCredentialService
from automation_tool.control_plane.application.device_sessions import DeviceSessionService
from automation_tool.control_plane.application.registration import InstallationRegistrationService
from automation_tool.control_plane.bootstrap.database import database_from_environment
from automation_tool.control_plane.bootstrap.device_credentials import (
    device_credential_service as build_device_credential_service,
)
from automation_tool.control_plane.bootstrap.device_sessions import (
    device_session_service as build_device_session_service,
)
from automation_tool.control_plane.bootstrap.registration import (
    registration_service_from_environment,
)
from automation_tool.control_plane.domain import DatabaseLifecycle
from automation_tool.control_plane.infrastructure.database import Database


class _FromEnvironment:
    """Sentinel that distinguishes production defaults from an explicit no-database app."""


_FROM_ENVIRONMENT = _FromEnvironment()


@asynccontextmanager
async def control_plane_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Own resources that must exist for exactly one application lifespan."""

    app.state.lifecycle_state = "running"
    try:
        yield
    finally:
        database: DatabaseLifecycle | None = app.state.database
        if database is not None:
            await database.close()
        app.state.lifecycle_state = "stopped"


def create_app(
    *,
    database: DatabaseLifecycle | None | _FromEnvironment = _FROM_ENVIRONMENT,
    registration_service: InstallationRegistrationService | None = None,
    device_credential_service: DeviceCredentialService | None = None,
    device_session_service: DeviceSessionService | None = None,
) -> FastAPI:
    """Create an isolated Control Plane application instance."""

    resolved_database = (
        database_from_environment() if isinstance(database, _FromEnvironment) else database
    )
    resolved_registration_service = registration_service
    resolved_device_credential_service = device_credential_service
    resolved_device_session_service = device_session_service
    if (
        resolved_registration_service is None
        and isinstance(database, _FromEnvironment)
        and isinstance(resolved_database, Database)
    ):
        resolved_registration_service = registration_service_from_environment(resolved_database)
    if resolved_device_credential_service is None and isinstance(resolved_database, Database):
        resolved_device_credential_service = build_device_credential_service(resolved_database)
    if resolved_device_session_service is None and isinstance(resolved_database, Database):
        resolved_device_session_service = build_device_session_service(resolved_database)

    app = FastAPI(
        title="automation-tool Control Plane",
        version=__version__,
        lifespan=control_plane_lifespan,
    )
    app.state.lifecycle_state = "created"
    app.state.database = resolved_database
    app.state.registration_service = resolved_registration_service
    app.state.device_credential_service = resolved_device_credential_service
    app.state.device_session_service = resolved_device_session_service
    install_request_context(app)
    register_error_handlers(app)
    app.include_router(system_router)
    app.include_router(registration_router)
    app.include_router(device_credential_router)
    app.include_router(device_session_router)
    return app
