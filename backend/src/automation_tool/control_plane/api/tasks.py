"""Installation-scoped Task creation API."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from automation_tool.control_plane.api.errors import AppError
from automation_tool.control_plane.api.installation_access import (
    require_current_installation_access,
)
from automation_tool.control_plane.application.tasks import (
    InvalidTaskCreation,
    TaskCreationService,
    TaskPersistenceRejected,
)
from automation_tool.control_plane.domain import InstallationId, TaskStatus

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


class TaskCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    task_id: str = Field(alias="taskId")
    status: TaskStatus
    revision: int
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


def _service(request: Request) -> TaskCreationService:
    service = request.app.state.task_creation_service
    if not isinstance(service, TaskCreationService):
        raise AppError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="tasks_unavailable",
            message="Task service is unavailable",
            retryable=True,
        )
    return service


@router.post(
    "",
    response_model=TaskResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="createTask",
)
async def create_task(
    _payload: TaskCreateRequest,
    response: Response,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    installation_id: Annotated[InstallationId, Depends(require_current_installation_access)],
    service: Annotated[TaskCreationService, Depends(_service)],
) -> TaskResponse:
    response.headers["cache-control"] = "no-store"
    try:
        result = await service.create(
            installation_id=installation_id,
            idempotency_key=idempotency_key,
        )
    except InvalidTaskCreation:
        raise AppError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="validation",
            message="Request validation failed",
        ) from None
    except TaskPersistenceRejected:
        raise AppError(
            status_code=status.HTTP_409_CONFLICT,
            code="task_create_rejected",
            message="Task creation was rejected",
        ) from None
    if not result.created:
        response.status_code = status.HTTP_200_OK
    task = result.task
    return TaskResponse(
        taskId=str(task.task_id),
        status=task.status,
        revision=task.revision,
        createdAt=task.created_at,
        updatedAt=task.updated_at,
    )


__all__ = ["router"]
