"""Installation-scoped target discovery API."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from automation_tool.control_plane.api.errors import AppError
from automation_tool.control_plane.api.installation_access import (
    require_current_installation_access,
)
from automation_tool.control_plane.application.task_discovery import (
    TaskDiscoveryInstallationBusy,
    TaskDiscoveryRejected,
    TaskDiscoveryStartResult,
    TaskDiscoveryStartService,
    TaskDiscoveryUnavailable,
)
from automation_tool.control_plane.domain import (
    MAX_TASK_EVENT_SEQUENCE,
    InstallationId,
    InvalidResourceId,
    TaskCommandStatus,
    TaskId,
    TaskStatus,
)
from automation_tool.protocol import IdempotencyKey

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


class TaskDiscoveryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    task_id: str = Field(alias="taskId")
    task_status: TaskStatus = Field(alias="taskStatus")
    task_revision: int = Field(alias="taskRevision", ge=1, le=MAX_TASK_EVENT_SEQUENCE)
    last_event_sequence: int = Field(
        alias="lastEventSequence",
        ge=1,
        le=MAX_TASK_EVENT_SEQUENCE,
    )
    command_id: str = Field(alias="commandId")
    execution_attempt_id: str = Field(alias="executionAttemptId")
    command_status: TaskCommandStatus = Field(alias="commandStatus")
    created_at: datetime = Field(alias="createdAt")
    deadline_at: datetime = Field(alias="deadlineAt")


def _service(request: Request) -> TaskDiscoveryStartService:
    service = request.app.state.task_discovery_start_service
    if not isinstance(service, TaskDiscoveryStartService):
        raise AppError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="task_discovery_unavailable",
            message="Task discovery is unavailable",
            retryable=True,
        )
    return service


def _response(result: TaskDiscoveryStartResult) -> TaskDiscoveryResponse:
    task = result.task
    command = result.command
    return TaskDiscoveryResponse(
        taskId=str(task.task_id),
        taskStatus=task.status,
        taskRevision=task.revision,
        lastEventSequence=task.last_event_sequence,
        commandId=str(command.message_id),
        executionAttemptId=str(command.execution_attempt_id),
        commandStatus=command.status,
        createdAt=command.created_at,
        deadlineAt=command.deadline_at,
    )


@router.post(
    "/{task_id}/discoveries",
    response_model=TaskDiscoveryResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="startTaskDiscovery",
)
async def start_task_discovery(
    task_id: str,
    response: Response,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    installation_id: Annotated[InstallationId, Depends(require_current_installation_access)],
    service: Annotated[TaskDiscoveryStartService, Depends(_service)],
) -> TaskDiscoveryResponse:
    response.headers["cache-control"] = "no-store"
    try:
        parsed_task_id = TaskId.parse(task_id)
        normalized_idempotency_key = str(IdempotencyKey(idempotency_key))
    except (InvalidResourceId, TypeError, ValueError):
        raise AppError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="validation",
            message="Request validation failed",
        ) from None
    try:
        result = await service.start(
            installation_id=installation_id,
            task_id=parsed_task_id,
            idempotency_key=normalized_idempotency_key,
        )
    except TaskDiscoveryInstallationBusy:
        raise AppError(
            status_code=status.HTTP_423_LOCKED,
            code="installation_task_active",
            message="Another task is already active on this device",
        ) from None
    except TaskDiscoveryRejected:
        raise AppError(
            status_code=status.HTTP_409_CONFLICT,
            code="task_discovery_rejected",
            message="Task discovery was rejected",
        ) from None
    except TaskDiscoveryUnavailable:
        raise AppError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="task_discovery_unavailable",
            message="Task discovery is unavailable",
            retryable=True,
        ) from None
    if not result.created:
        response.status_code = status.HTTP_200_OK
    return _response(result)


__all__ = ["router"]
