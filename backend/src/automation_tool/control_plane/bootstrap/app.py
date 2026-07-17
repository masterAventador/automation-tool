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


@asynccontextmanager
async def control_plane_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Own resources that must exist for exactly one application lifespan."""

    app.state.lifecycle_state = "running"
    try:
        yield
    finally:
        app.state.lifecycle_state = "stopped"


def create_app() -> FastAPI:
    """Create an isolated Control Plane application instance."""

    app = FastAPI(
        title="automation-tool Control Plane",
        version=__version__,
        lifespan=control_plane_lifespan,
    )
    app.state.lifecycle_state = "created"
    install_request_context(app)
    register_error_handlers(app)
    app.include_router(system_router)
    return app
