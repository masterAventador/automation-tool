"""Installation-scoped Task creation and snapshot query API."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from automation_tool.control_plane.api.errors import AppError
from automation_tool.control_plane.api.installation_access import (
    require_current_installation_access,
)
from automation_tool.control_plane.application.task_queries import (
    InvalidTaskQuery,
    TaskNotFound,
    TaskQueryService,
)
from automation_tool.control_plane.application.tasks import (
    InvalidTaskCreation,
    TaskCreationService,
    TaskPersistenceRejected,
    TaskRecord,
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


class TaskListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    items: list[TaskResponse]
    next_cursor: str | None = Field(alias="nextCursor")


def _creation_service(request: Request) -> TaskCreationService:
    service = request.app.state.task_creation_service
    if not isinstance(service, TaskCreationService):
        raise AppError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="tasks_unavailable",
            message="Task service is unavailable",
            retryable=True,
        )
    return service


def _query_service(request: Request) -> TaskQueryService:
    service = request.app.state.task_query_service
    if not isinstance(service, TaskQueryService):
        raise AppError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="tasks_unavailable",
            message="Task service is unavailable",
            retryable=True,
        )
    return service


def _task_response(task: TaskRecord) -> TaskResponse:
    return TaskResponse(
        taskId=str(task.task_id),
        status=task.status,
        revision=task.revision,
        createdAt=task.created_at,
        updatedAt=task.updated_at,
    )


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
    service: Annotated[TaskCreationService, Depends(_creation_service)],
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
    return _task_response(task)


@router.get(
    "",
    response_model=TaskListResponse,
    operation_id="listTasks",
)
async def list_tasks(
    response: Response,
    installation_id: Annotated[InstallationId, Depends(require_current_installation_access)],
    service: Annotated[TaskQueryService, Depends(_query_service)],
    cursor: Annotated[str | None, Query(max_length=256)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> TaskListResponse:
    response.headers["cache-control"] = "no-store"
    try:
        page = await service.list(
            installation_id=installation_id,
            cursor=cursor,
            limit=limit,
        )
    except InvalidTaskQuery:
        raise AppError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="validation",
            message="Request validation failed",
        ) from None
    return TaskListResponse(
        items=[_task_response(task) for task in page.items],
        nextCursor=page.next_cursor,
    )


@router.get(
    "/{task_id}",
    response_model=TaskResponse,
    operation_id="getTask",
)
async def get_task(
    task_id: str,
    response: Response,
    installation_id: Annotated[InstallationId, Depends(require_current_installation_access)],
    service: Annotated[TaskQueryService, Depends(_query_service)],
) -> TaskResponse:
    response.headers["cache-control"] = "no-store"
    try:
        task = await service.get(installation_id=installation_id, task_id=task_id)
    except TaskNotFound:
        raise AppError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="task_not_found",
            message="Task is unavailable",
        ) from None
    except InvalidTaskQuery:
        raise AppError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="validation",
            message="Request validation failed",
        ) from None
    return _task_response(task)


__all__ = ["router"]
