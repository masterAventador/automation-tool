"""Installation-scoped public workbench runtime projection."""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from automation_tool.control_plane.api.errors import AppError
from automation_tool.control_plane.api.installation_access import (
    require_current_installation_access,
)
from automation_tool.control_plane.application.executor_connection_registry import (
    ExecutorConnectionRegistry,
    ExecutorConnectionRegistryRejected,
)
from automation_tool.control_plane.domain import InstallationId

router = APIRouter(prefix="/api/v1/workbench", tags=["workbench"])


class WorkbenchStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    control_plane_status: Literal["ready"] = Field(alias="controlPlaneStatus")
    executor_status: Literal["online", "offline"] = Field(alias="executorStatus")
    executor_last_heartbeat_at: datetime | None = Field(alias="executorLastHeartbeatAt")


def _registry(request: Request) -> ExecutorConnectionRegistry:
    registry = request.app.state.executor_connection_registry
    if not isinstance(registry, ExecutorConnectionRegistry):
        raise _unavailable()
    return registry


def _unavailable() -> AppError:
    return AppError(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        code="workbench_status_unavailable",
        message="Workbench status is unavailable",
        retryable=True,
    )


@router.get(
    "/status",
    response_model=WorkbenchStatusResponse,
    operation_id="getWorkbenchStatus",
)
async def get_workbench_status(
    response: Response,
    installation_id: Annotated[InstallationId, Depends(require_current_installation_access)],
    registry: Annotated[ExecutorConnectionRegistry, Depends(_registry)],
) -> WorkbenchStatusResponse:
    response.headers["cache-control"] = "no-store"
    try:
        online = await registry.snapshot(installation_id)
    except ExecutorConnectionRegistryRejected:
        raise _unavailable() from None
    return WorkbenchStatusResponse(
        controlPlaneStatus="ready",
        executorStatus="offline" if online is None else "online",
        executorLastHeartbeatAt=None if online is None else online.last_heartbeat_at,
    )


__all__ = ["router"]
