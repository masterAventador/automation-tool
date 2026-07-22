"""Health and compatibility endpoints with stable response contracts."""

from typing import Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from automation_tool import __version__
from automation_tool.control_plane.api.errors import AppError
from automation_tool.control_plane.domain import DatabaseLifecycle, DependencyUnavailable
from automation_tool.protocol.version import (
    API_VERSION,
    CURRENT_DESKTOP_APP_VERSION,
    CURRENT_EXECUTOR_PROTOCOL,
    CURRENT_EXECUTOR_RUNTIME_VERSION,
    MAXIMUM_COMPATIBLE_DESKTOP_APP_VERSION,
    MAXIMUM_COMPATIBLE_EXECUTOR_PROTOCOL,
    MAXIMUM_COMPATIBLE_EXECUTOR_RUNTIME_VERSION,
    MINIMUM_COMPATIBLE_DESKTOP_APP_VERSION,
    MINIMUM_COMPATIBLE_EXECUTOR_PROTOCOL,
    MINIMUM_COMPATIBLE_EXECUTOR_RUNTIME_VERSION,
)

router = APIRouter(prefix="/api/v1", tags=["system"])


class ApiResponse(BaseModel):
    """Strict response base to prevent accidental contract expansion."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class HealthResponse(ApiResponse):
    status: Literal["ok"] = "ok"
    service: Literal["control-plane"] = "control-plane"
    version: str


class VersionCompatibility(ApiResponse):
    current: str
    minimum_compatible: str = Field(alias="minimumCompatible")
    maximum_compatible: str = Field(alias="maximumCompatible")


class VersionResponse(ApiResponse):
    service: Literal["control-plane"] = "control-plane"
    version: str
    api_version: str = Field(alias="apiVersion")
    desktop_app: VersionCompatibility = Field(alias="desktopApp")
    executor_runtime: VersionCompatibility = Field(alias="executorRuntime")
    executor_protocol: VersionCompatibility = Field(alias="executorProtocol")


def _disable_caching(response: Response) -> None:
    response.headers["cache-control"] = "no-store"


@router.get("/health", response_model=HealthResponse, operation_id="getSystemHealth")
async def health(request: Request, response: Response) -> HealthResponse:
    _disable_caching(response)
    database: DatabaseLifecycle | None = request.app.state.database
    if database is not None:
        try:
            await database.check_connection()
        except DependencyUnavailable:
            raise AppError(
                status_code=503,
                code="dependency_unavailable",
                message="Database is unavailable",
                retryable=True,
            ) from None
    return HealthResponse(version=__version__)


@router.get("/version", response_model=VersionResponse, operation_id="getSystemVersion")
async def version(response: Response) -> VersionResponse:
    _disable_caching(response)
    return VersionResponse(
        version=__version__,
        apiVersion=API_VERSION,
        desktopApp=VersionCompatibility(
            current=CURRENT_DESKTOP_APP_VERSION,
            minimumCompatible=MINIMUM_COMPATIBLE_DESKTOP_APP_VERSION,
            maximumCompatible=MAXIMUM_COMPATIBLE_DESKTOP_APP_VERSION,
        ),
        executorRuntime=VersionCompatibility(
            current=CURRENT_EXECUTOR_RUNTIME_VERSION,
            minimumCompatible=MINIMUM_COMPATIBLE_EXECUTOR_RUNTIME_VERSION,
            maximumCompatible=MAXIMUM_COMPATIBLE_EXECUTOR_RUNTIME_VERSION,
        ),
        executorProtocol=VersionCompatibility(
            current=CURRENT_EXECUTOR_PROTOCOL,
            minimumCompatible=MINIMUM_COMPATIBLE_EXECUTOR_PROTOCOL,
            maximumCompatible=MAXIMUM_COMPATIBLE_EXECUTOR_PROTOCOL,
        ),
    )
