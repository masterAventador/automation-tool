"""Current Installation's non-sensitive social-platform Session projection."""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from automation_tool.control_plane.api.errors import AppError
from automation_tool.control_plane.api.installation_access import (
    require_current_installation_access,
)
from automation_tool.control_plane.application.platform_session_health import (
    PlatformSessionHealthRejected,
    PlatformSessionHealthService,
    PlatformSessionHealthUnavailable,
)
from automation_tool.control_plane.domain import InstallationId
from automation_tool.protocol import PlatformSessionState

router = APIRouter(prefix="/api/v1/platform-sessions", tags=["platform-sessions"])


class PlatformSessionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    platform: Literal["douyin"]
    state: PlatformSessionState
    observed_at: datetime | None = Field(alias="observedAt")


def _service(request: Request) -> PlatformSessionHealthService:
    service = request.app.state.platform_session_health_service
    if not isinstance(service, PlatformSessionHealthService):
        raise _unavailable()
    return service


def _unavailable() -> AppError:
    return AppError(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        code="platform_session_unavailable",
        message="Platform Session is unavailable",
        retryable=True,
    )


@router.get(
    "/douyin",
    response_model=PlatformSessionResponse,
    operation_id="getDouyinPlatformSession",
)
async def get_douyin_platform_session(
    response: Response,
    installation_id: Annotated[InstallationId, Depends(require_current_installation_access)],
    service: Annotated[PlatformSessionHealthService, Depends(_service)],
) -> PlatformSessionResponse:
    response.headers["cache-control"] = "no-store"
    try:
        projection = await service.get(installation_id, platform="douyin")
    except (PlatformSessionHealthRejected, PlatformSessionHealthUnavailable):
        raise _unavailable() from None
    return PlatformSessionResponse(
        platform="douyin",
        state=(PlatformSessionState.UNKNOWN if projection is None else projection.state),
        observedAt=None if projection is None else projection.observed_at,
    )


__all__ = ["router"]
