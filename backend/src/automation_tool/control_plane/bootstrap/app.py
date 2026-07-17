"""FastAPI application factory and process lifespan."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from automation_tool import __version__
from automation_tool.control_plane.api.errors import (
    install_request_context,
    register_error_handlers,
)
from automation_tool.control_plane.api.system import router as system_router
from automation_tool.control_plane.bootstrap.database import database_from_environment
from automation_tool.control_plane.domain import DatabaseLifecycle


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
) -> FastAPI:
    """Create an isolated Control Plane application instance."""

    resolved_database = (
        database_from_environment() if isinstance(database, _FromEnvironment) else database
    )

    app = FastAPI(
        title="automation-tool Control Plane",
        version=__version__,
        lifespan=control_plane_lifespan,
    )
    app.state.lifecycle_state = "created"
    app.state.database = resolved_database
    install_request_context(app)
    register_error_handlers(app)
    app.include_router(system_router)
    return app
