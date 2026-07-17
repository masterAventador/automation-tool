"""Health and compatibility endpoints with stable response contracts."""

from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, Field

from automation_tool import __version__
from automation_tool.protocol.version import (
    API_VERSION,
    CURRENT_EXECUTOR_PROTOCOL,
    MAXIMUM_COMPATIBLE_EXECUTOR_PROTOCOL,
    MINIMUM_COMPATIBLE_EXECUTOR_PROTOCOL,
)

router = APIRouter(prefix="/api/v1", tags=["system"])


class ApiResponse(BaseModel):
    """Strict response base to prevent accidental contract expansion."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class HealthResponse(ApiResponse):
    status: Literal["ok"] = "ok"
    service: Literal["control-plane"] = "control-plane"
    version: str


class ExecutorProtocolCompatibility(ApiResponse):
    current: str
    minimum_compatible: str = Field(alias="minimumCompatible")
    maximum_compatible: str = Field(alias="maximumCompatible")


class VersionResponse(ApiResponse):
    service: Literal["control-plane"] = "control-plane"
    version: str
    api_version: str = Field(alias="apiVersion")
    executor_protocol: ExecutorProtocolCompatibility = Field(alias="executorProtocol")


def _disable_caching(response: Response) -> None:
    response.headers["cache-control"] = "no-store"


@router.get("/health", response_model=HealthResponse)
async def health(response: Response) -> HealthResponse:
    _disable_caching(response)
    return HealthResponse(version=__version__)


@router.get("/version", response_model=VersionResponse)
async def version(response: Response) -> VersionResponse:
    _disable_caching(response)
    return VersionResponse(
        version=__version__,
        apiVersion=API_VERSION,
        executorProtocol=ExecutorProtocolCompatibility(
            current=CURRENT_EXECUTOR_PROTOCOL,
            minimumCompatible=MINIMUM_COMPATIBLE_EXECUTOR_PROTOCOL,
            maximumCompatible=MAXIMUM_COMPATIBLE_EXECUTOR_PROTOCOL,
        ),
    )
