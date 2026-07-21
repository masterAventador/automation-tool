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
from automation_tool.control_plane.application.workbench_metrics import (
    MAX_WORKBENCH_METRIC_COUNT,
    WORKBENCH_METRICS_VERSION,
    InvalidWorkbenchMetrics,
    WorkbenchMetricsService,
    WorkbenchMetricsUnavailable,
)
from automation_tool.control_plane.domain import InstallationId

router = APIRouter(prefix="/api/v1/workbench", tags=["workbench"])


class WorkbenchStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    control_plane_status: Literal["ready"] = Field(alias="controlPlaneStatus")
    executor_status: Literal["online", "offline"] = Field(alias="executorStatus")
    executor_last_heartbeat_at: datetime | None = Field(alias="executorLastHeartbeatAt")


class WorkbenchTaskMetricsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    total: int = Field(ge=0, le=MAX_WORKBENCH_METRIC_COUNT)
    succeeded: int = Field(ge=0, le=MAX_WORKBENCH_METRIC_COUNT)
    failed: int = Field(ge=0, le=MAX_WORKBENCH_METRIC_COUNT)
    handoff_required: int = Field(ge=0, le=MAX_WORKBENCH_METRIC_COUNT, alias="handoffRequired")
    outcome_uncertain: int = Field(ge=0, le=MAX_WORKBENCH_METRIC_COUNT, alias="outcomeUncertain")


class WorkbenchActionMetricsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    total: int = Field(ge=0, le=MAX_WORKBENCH_METRIC_COUNT)
    succeeded: int = Field(ge=0, le=MAX_WORKBENCH_METRIC_COUNT)
    failed: int = Field(ge=0, le=MAX_WORKBENCH_METRIC_COUNT)
    outcome_uncertain: int = Field(ge=0, le=MAX_WORKBENCH_METRIC_COUNT, alias="outcomeUncertain")


class WorkbenchMetricsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    version: Literal["workbench.metrics.v1"]
    tasks: WorkbenchTaskMetricsResponse
    actions: WorkbenchActionMetricsResponse


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


def _metrics_service(request: Request) -> WorkbenchMetricsService:
    service = request.app.state.workbench_metrics_service
    if not isinstance(service, WorkbenchMetricsService):
        raise _metrics_unavailable()
    return service


def _metrics_unavailable() -> AppError:
    return AppError(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        code="workbench_metrics_unavailable",
        message="Workbench metrics are unavailable",
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


@router.get(
    "/metrics",
    response_model=WorkbenchMetricsResponse,
    operation_id="getWorkbenchMetrics",
)
async def get_workbench_metrics(
    response: Response,
    installation_id: Annotated[InstallationId, Depends(require_current_installation_access)],
    service: Annotated[WorkbenchMetricsService, Depends(_metrics_service)],
) -> WorkbenchMetricsResponse:
    response.headers["cache-control"] = "no-store"
    try:
        metrics = await service.get(installation_id=installation_id)
    except (InvalidWorkbenchMetrics, WorkbenchMetricsUnavailable):
        raise _metrics_unavailable() from None
    return WorkbenchMetricsResponse(
        version=WORKBENCH_METRICS_VERSION,
        tasks=WorkbenchTaskMetricsResponse(
            total=metrics.task_total,
            succeeded=metrics.task_succeeded,
            failed=metrics.task_failed,
            handoffRequired=metrics.task_handoff_required,
            outcomeUncertain=metrics.task_outcome_uncertain,
        ),
        actions=WorkbenchActionMetricsResponse(
            total=metrics.action_total,
            succeeded=metrics.action_succeeded,
            failed=metrics.action_failed,
            outcomeUncertain=metrics.action_outcome_uncertain,
        ),
    )


__all__ = ["router"]
