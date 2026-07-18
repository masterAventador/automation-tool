"""Installation-scoped Task pause and resume API."""

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from automation_tool.control_plane.api.errors import AppError
from automation_tool.control_plane.api.installation_access import (
    require_current_installation_access,
)
from automation_tool.control_plane.application.task_controls import (
    InvalidTaskControl,
    TaskControlConflict,
    TaskControlEnqueueResult,
    TaskControlNotFound,
    TaskControlService,
    TaskControlUnavailable,
)
from automation_tool.control_plane.domain import InstallationId, TaskCommandStatus, TaskCommandType

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


class TaskControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskControlResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    command_id: str = Field(alias="commandId")
    task_id: str = Field(alias="taskId")
    execution_attempt_id: str = Field(alias="executionAttemptId")
    sequence: int
    command_type: TaskCommandType = Field(alias="commandType")
    status: TaskCommandStatus
    revision: int
    created_at: datetime = Field(alias="createdAt")
    deadline_at: datetime = Field(alias="deadlineAt")


def _service(request: Request) -> TaskControlService:
    service = request.app.state.task_control_service
    if not isinstance(service, TaskControlService):
        raise AppError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="task_control_unavailable",
            message="Task control is unavailable",
            retryable=True,
        )
    return service


def _response(result: TaskControlEnqueueResult) -> TaskControlResponse:
    command = result.command
    return TaskControlResponse(
        commandId=str(command.message_id),
        taskId=str(command.task_id),
        executionAttemptId=str(command.execution_attempt_id),
        sequence=command.sequence,
        commandType=command.command_type,
        status=command.status,
        revision=command.revision,
        createdAt=command.created_at,
        deadlineAt=command.deadline_at,
    )


async def _control(
    operation: Callable[..., Awaitable[TaskControlEnqueueResult]],
    *,
    task_id: str,
    idempotency_key: str,
    installation_id: InstallationId,
) -> TaskControlEnqueueResult:
    try:
        return await operation(
            installation_id=installation_id,
            task_id=task_id,
            idempotency_key=idempotency_key,
        )
    except InvalidTaskControl:
        raise AppError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="validation",
            message="Request validation failed",
        ) from None
    except TaskControlNotFound:
        raise AppError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="task_not_found",
            message="Task is unavailable",
        ) from None
    except TaskControlConflict:
        raise AppError(
            status_code=status.HTTP_409_CONFLICT,
            code="task_control_rejected",
            message="Task control was rejected",
        ) from None
    except TaskControlUnavailable:
        raise AppError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="task_control_unavailable",
            message="Task control is unavailable",
            retryable=True,
        ) from None


@router.post(
    "/{task_id}/pause",
    response_model=TaskControlResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="pauseTask",
)
async def pause_task(
    task_id: str,
    _payload: TaskControlRequest,
    response: Response,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    installation_id: Annotated[InstallationId, Depends(require_current_installation_access)],
    service: Annotated[TaskControlService, Depends(_service)],
) -> TaskControlResponse:
    response.headers["cache-control"] = "no-store"
    result = await _control(
        service.pause,
        task_id=task_id,
        idempotency_key=idempotency_key,
        installation_id=installation_id,
    )
    if not result.created:
        response.status_code = status.HTTP_200_OK
    return _response(result)


@router.post(
    "/{task_id}/resume",
    response_model=TaskControlResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="resumeTask",
)
async def resume_task(
    task_id: str,
    _payload: TaskControlRequest,
    response: Response,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    installation_id: Annotated[InstallationId, Depends(require_current_installation_access)],
    service: Annotated[TaskControlService, Depends(_service)],
) -> TaskControlResponse:
    response.headers["cache-control"] = "no-store"
    result = await _control(
        service.resume,
        task_id=task_id,
        idempotency_key=idempotency_key,
        installation_id=installation_id,
    )
    if not result.created:
        response.status_code = status.HTTP_200_OK
    return _response(result)


__all__ = ["router"]
